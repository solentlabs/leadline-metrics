"""goatcounter: the script's rows on its scenarios, and leadline's side of each decided divergence.

The rows are compared with the goldens through tests/port.py; the scenarios where leadline differs by
a decision are listed in scenarios.py (DIVERGENCES) and asserted here instead.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import port
import pytest
import scenarios
from scenarios.builders.common import exchange, failed

from leadline.context import Correction, Critical, Finding
from leadline.http import SavedResponses
from leadline.sources import goatcounter as source
from leadline.sources.goatcounter.tests import scenarios as gc
from leadline.sources.goatcounter.tests.builders import goatcounter_refs

NAME = "goatcounter"
CONFIG = gc.LEADLINE_CONFIG
FILE = gc.FILE
SITE = gc.CONFIG["GOATCOUNTER_SITE"]
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it
EARLIER = gc.row("2026-09-21", 4)  # the failure scenarios' seed: no row for yesterday
BEFORE = "".join(gc.ALL_BUT_YESTERDAY.values())  # a row for each day of the week but yesterday
TOP = [
    {"ref": "github.com", "count": 17},
    {"ref": "example.com", "count": 4},
    {"ref": "forum.example", "count": 3},
    {"ref": "search.example", "count": 2},
    {"ref": "blog.example", "count": 1},
]  # the three pages' referrers, as the script tops them


def golden_lines(name: str) -> list[str]:
    lines: list[str] = scenarios.load_golden(NAME)[name]["rows"][FILE].splitlines(keepends=True)
    return lines


def filled(day: str, pageviews: int) -> str:
    """A backfilled day's row: its page views, and no referrers."""
    return json.dumps({"date": day, "pageviews": pageviews, "top_referrers": [], "captured_at": NOW}) + "\n"


def yesterday(**fields: Any) -> str:
    """Yesterday's row for the three pages, with `fields` in place (a partial row's nulls, status, reason)."""
    row: dict[str, Any] = {
        "date": "2026-09-22",
        "pageviews": 24,
        "top_referrers": TOP,
        "window_sum": 105,
        "window_total": 132,
        "paths_returned": 3,
    }
    return json.dumps({**row, **fields, "captured_at": NOW}) + "\n"


def failure_row(status: str, reason: str, http_code: str | None = None) -> str:
    code = f', "http_code": "{http_code}"' if http_code is not None else ""
    return f'{{"date": "2026-09-22", "status": "{status}"{code}, "reason": "{reason}", "captured_at": "{NOW}"}}\n'


def partial(reason: str, **nulls: Any) -> str:
    return yesterday(**nulls, status="partial", reason=reason)


def run(tmp_path: Path, *responses: Any, seed: str = BEFORE) -> port.PortRun:
    """A run on `responses`, from a file with a row for each day but yesterday (unless `seed` says otherwise)."""
    scenario = replace(port.scenario(NAME, "retries_the_pages_request"), responses=list(responses), seed={FILE: seed})
    return port.run(source, CONFIG, scenario, tmp_path)


def three_pages(*, total: Any = None, refs: Any = None) -> list[Any]:
    """The three pages' requests, in order, with the total's and the referrers' answers replaced where given."""
    return [gc.hits(gc.THREE), total or gc.total(gc.THREE), *(refs or gc.REFERRERS)]


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert source.NAME == NAME
    assert source.ENV == ("GOATCOUNTER_TOKEN",)
    assert source.LAGGING is True  # today is still counting, so the newest row is yesterday's


def test_a_valid_config_has_no_problems() -> None:
    assert source.validate([{"site": "example"}, {"site": "other-site", "file": "gc-{site}.jsonl"}]) == []


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"site": "example"}, [": must be a list of entries"]),
        (["example"], ["[0]: must be an object"]),
        ([{}], ["[0].site: must be a GoatCounter site code (letters, digits and dashes)"]),
        ([{"site": ""}], ["[0].site: must be a GoatCounter site code (letters, digits and dashes)"]),
        ([{"site": "example.com"}], ["[0].site: must be a GoatCounter site code (letters, digits and dashes)"]),
        ([{"site": 5}], ["[0].site: must be a GoatCounter site code (letters, digits and dashes)"]),
        (
            [{"site": "example", "file": "x-{label}.jsonl"}],
            ["[0].file: 'x-{label}.jsonl' uses {label}, which is not one of: site"],
        ),
    ],
    ids=["not a list", "an entry not an object", "no site", "an empty site", "a domain", "a number", "a bad file"],
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert source.validate(cfg) == problems


def test_files_are_the_default_or_the_template_of_each_entry() -> None:
    cfg = [{"site": "one"}, {"site": "two", "file": "gc-{site}.jsonl"}]

    assert source.files(cfg) == {"[0]": "goatcounter-one.jsonl", "[1]": "gc-two.jsonl"}


def test_it_is_skipped_without_a_token(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "skips_without_a_token"), tmp_path)

    assert "SKIP: goatcounter (GOATCOUNTER_TOKEN not set)" in done.log
    assert (done.status, done.rows) == (0, {})


def test_it_sends_the_token_on_every_request(tmp_path: Path) -> None:
    sent: list[Any] = []
    transport = SavedResponses(three_pages())

    def recording(request: Any, timeout: float) -> Any:
        sent.append(request)
        return transport(request, timeout)

    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    ctx.http._transport = recording  # to see the request headers
    with port.credentials({"GOATCOUNTER_TOKEN": "t0ken"}, source.ENV):
        source.collect(ctx, CONFIG)

    assert [request.full_url for request in sent] == [gc.HITS, gc.TOTAL, *(gc.refs_url(n) for n in (1, 2, 3))]
    assert {request.get_header("Authorization") for request in sent} == {"Bearer t0ken"}


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    done = port.run(source, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == [
        "a_seed_line_not_an_object_writes_nothing",
        "referrers_of_ten_pages_and_no_window_total",
        "three_empty_answers_write_nothing",
        "three_failed_attempts_write_nothing",
        "unparseable_hits_write_nothing",
        "unparseable_total_and_referrers_count_as_none",
    ]


def test_the_pages_request_is_retried_after_10_then_20_seconds(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "retries_the_pages_request"), tmp_path)

    assert done.sleeps == [10, 20]


def test_each_backfilled_day_is_logged(tmp_path: Path) -> None:
    done = port.run(
        source, CONFIG, port.scenario(NAME, "week_backfills_missing_days_and_refreshes_yesterday"), tmp_path
    )

    backfilled = [line for line in done.log if line.startswith("GoatCounter backfill: ")]
    assert backfilled == [f"GoatCounter backfill: 2026-09-{day}" for day in (16, 17, 19, 21)]


def test_refreshing_yesterdays_good_row_is_the_one_correction(tmp_path: Path) -> None:
    """ADR: every correction is recorded. The seed's yesterday (3 page views) is re-read; a backfill is none."""
    done = port.run(
        source, CONFIG, port.scenario(NAME, "week_backfills_missing_days_and_refreshes_yesterday"), tmp_path
    )

    new = {"pageviews": 24, "top_referrers": TOP, "window_sum": 105, "window_total": 132, "paths_returned": 3}
    assert done.ctx.corrections == [Correction(NAME, FILE, "2026-09-22", {"pageviews": 3, "top_referrers": []}, new)]


# --- the order of referrers tied on count ---


@pytest.mark.parametrize("order", [(1, 2), (2, 1)], ids=["page 1 first", "page 2 first"])
def test_referrers_tied_on_count_come_in_the_order_their_pages_came_back(
    tmp_path: Path, order: tuple[int, int]
) -> None:
    """The script's order depends on the file system; leadline's is the order of the pages in the answer."""
    pages = [gc.page(n) for n in order]
    refs = {1: goatcounter_refs([("one.example", 5)]), 2: goatcounter_refs([("two.example", 5)])}
    answers = [gc.hits(pages), gc.total(pages), *(exchange(gc.refs_url(n), refs[n]) for n in order)]

    done = run(tmp_path, *answers)

    top = json.loads(done.rows[FILE].splitlines()[-1])["top_referrers"]
    assert [ref["ref"] for ref in top] == [{1: "one.example", 2: "two.example"}[n] for n in order]


def test_referrers_come_from_the_first_ten_pages_with_an_id(tmp_path: Path) -> None:
    answers = [exchange(gc.refs_url(n), goatcounter_refs([(f"site-{n}.example", n)])) for n in range(1, 11)]

    done = run(tmp_path, gc.hits(gc.TWELVE), gc.total(gc.TWELVE), *answers)

    top = json.loads(done.rows[FILE].splitlines()[-1])["top_referrers"]
    assert top == [{"ref": f"site-{n}.example", "count": n} for n in (10, 9, 8, 7, 6)]


def test_an_unreadable_referrers_answer_counts_toward_the_ten_pages(tmp_path: Path) -> None:
    """As in the script: page 2's answer can't be read, so pages 1 to 10 are asked for, and no more."""
    answers = [exchange(gc.refs_url(n), goatcounter_refs([(f"site-{n}.example", n)])) for n in range(1, 11)]
    answers[1] = exchange(gc.refs_url(2), "<html>maintenance</html>")

    done = run(tmp_path, gc.hits(gc.TWELVE), gc.total(gc.TWELVE), *answers)  # a request past them fails the run

    assert json.loads(done.rows[FILE].splitlines()[-1])["top_referrers"] is None
    assert "GoatCounter: fetched referrers for 10 paths" in done.log


# --- ADR: a failure is recorded as a failure (the pages are the row's required part) ---


def test_three_failed_attempts_are_a_fetch_failed_row_for_yesterday(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "three_failed_attempts_write_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "hits request failed", "429")}
    assert (done.status, done.ctx.critical, done.sleeps) == (0, [], [10, 20])


def test_three_empty_answers_are_a_fetch_failed_row_for_yesterday(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "three_empty_answers_write_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "hits request failed", "200")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_unparseable_pages_are_a_parse_failed_row_for_yesterday(tmp_path: Path) -> None:
    """The total is still asked for, as the script asks, before the pages are read."""
    done = port.run(source, CONFIG, port.scenario(NAME, "unparseable_hits_write_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "hits response was not JSON")}
    assert (done.status, done.ctx.critical) == (0, [])


STAT = {"day": "2026-09-22", "hourly": [0] * 24, "daily": 3}


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"hits": {}},
        {"hits": [5]},
        {"hits": [{"path_id": 1, "stats": {}}]},
        {"hits": [{"path_id": 1, "stats": [5]}]},
        {"hits": [{"path_id": 1, "stats": [{**STAT, "day": None}]}]},
        {"hits": [{"path_id": 1, "stats": [{**STAT, "day": "yesterday"}]}]},
        {"hits": [{"path_id": 1, "stats": [{**STAT, "daily": None}]}]},
        {"hits": [{"path_id": 1, "stats": [{**STAT, "daily": True}]}]},
    ],
    ids=[
        "a list",
        "the pages an object",
        "a page not an object",
        "the stats an object",
        "a stat not an object",
        "a null day",
        "a day not a date",
        "a null count",
        "a count a boolean",
    ],
)
def test_pages_it_cant_use_are_a_parse_failed_row(tmp_path: Path, body: Any) -> None:
    done = run(tmp_path, exchange(gc.HITS, body), gc.total(gc.THREE), seed=EARLIER)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "hits response had an unexpected shape")}


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({}, "hits response is missing hits"),
        ({"hits": [{"path_id": 1}]}, "hits response is missing stats in a page"),
        ({"hits": [{"path_id": 1, "stats": [{"day": "2026-09-22"}]}]}, "hits response is missing daily in a day"),
    ],
    ids=["no pages", "a page without stats", "a stat without its count"],
)
def test_pages_without_their_lists_or_counts_are_a_parse_failed_row_naming_them(
    tmp_path: Path, body: Any, reason: str
) -> None:
    """ADR: a failure is recorded as a failure. The script read what was absent as no pages and 0 views."""
    done = run(tmp_path, exchange(gc.HITS, body), gc.total(gc.THREE), seed=EARLIER)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", reason)}


def test_a_pages_404_is_retried_and_recoverable(tmp_path: Path) -> None:
    """GoatCounter answers 404 for a readable site at times (ADR: one failed night recovers; consecutive
    failures are critical): the pages' request is retried, as the script retries it, and is not critical."""
    done = run(tmp_path, *[exchange(gc.HITS, "", status=404)] * 3, seed=EARLIER)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "hits request failed", "404")}
    assert (done.status, done.ctx.critical, done.sleeps) == (0, [], [10, 20])


def test_a_failed_request_logs_its_cause(tmp_path: Path) -> None:
    """The night an outage and a network problem looked alike: each failure's cause stays in the log."""
    headers = {"Server": "cloudflare", "CF-Ray": "abc-AMS"}
    done = run(
        tmp_path,
        failed(gc.HITS, "timed out"),
        exchange(gc.HITS, "site not found\n", status=404, headers=headers),
        failed(gc.HITS, "connection refused"),
        seed=EARLIER,
    )

    assert [line for line in done.log if "attempt" in line or "failed" in line] == [
        f"WARN: attempt 1/3 failed (HTTP 000: <urlopen error timed out>), retrying in 10s: {gc.HITS}",
        "WARN: attempt 2/3 failed (HTTP 404: site not found [server=cloudflare, cf-ray=abc-AMS]), "
        f"retrying in 20s: {gc.HITS}",
        f"WARN: attempt 3/3 failed (HTTP 000: <urlopen error connection refused>), giving up: {gc.HITS}",
        f"ERROR: GoatCounter hits for {SITE} failed (HTTP 000)",
    ]
    assert "Bearer" not in "\n".join(done.log)


@pytest.mark.parametrize("code", [401, 403])
def test_an_error_waiting_cant_fix_is_not_retried_and_critical_at_once(tmp_path: Path, code: int) -> None:
    done = run(tmp_path, exchange(gc.HITS, "", status=code), seed=EARLIER)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "hits request failed", str(code))}
    assert done.ctx.critical == [Critical(NAME, FILE, f"{SITE}: HTTP {code} from GoatCounter's API")]
    assert (done.status, done.sleeps) == (1, [])


def test_a_failure_keeps_yesterdays_good_row(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data."""
    good = gc.row("2026-09-22", 7)

    done = run(tmp_path, *[failed(gc.HITS)] * 3, seed=EARLIER + good)

    assert done.rows == {FILE: EARLIER + good}
    assert f"WARN: kept the good row for 2026-09-22 in {FILE}; fetch_failed not recorded" in done.log


def test_a_day_with_only_a_failure_row_is_backfilled(tmp_path: Path) -> None:
    """A failure row isn't a day the file has: the next good run fills it."""
    seed = BEFORE.replace(gc.row("2026-09-20", 1), failure_row("fetch_failed", "hits request failed", "503"))

    done = run(tmp_path, *three_pages(), seed=seed)

    assert done.rows[FILE].splitlines(keepends=True)[-2:] == [filled("2026-09-20", 18), yesterday()]


def test_a_seed_line_that_is_not_an_object_is_kept_and_the_rows_written(tmp_path: Path) -> None:
    """ADR: store never fails on a line it can't use (the script stops reading its rows on it and writes none)."""
    scenario = port.scenario(NAME, "a_seed_line_not_an_object_writes_nothing")

    done = port.run(source, CONFIG, scenario, tmp_path)

    backfilled = [filled(f"2026-09-{day}", 6 + 3 * (day - 16)) for day in range(16, 21)]
    assert done.rows == {FILE: scenario.seed[FILE] + "".join(backfilled) + yesterday()}
    assert done.status == 0


# --- ADR: a partly failed row keeps its good parts (the total and the referrers are optional) ---


def test_a_failed_total_and_referrers_are_null_in_a_partial_row(tmp_path: Path) -> None:
    """The referrers are one part, read whole: one page's failed request leaves them null, never a guess.

    The requests are still the script's: a failed request doesn't count toward the ten pages.
    """
    scenario = port.scenario(NAME, "referrers_of_ten_pages_and_no_window_total")

    done = port.run(source, CONFIG, scenario, tmp_path)

    script = golden_lines(scenario.name)
    reason = "total request failed (HTTP 500); path 2 referrers request failed (HTTP 500)"
    row = {**json.loads(script[-1]), "top_referrers": None}
    del row["captured_at"]
    written = json.dumps({**row, "status": "partial", "reason": reason, "captured_at": NOW}) + "\n"
    assert done.rows == {FILE: "".join(script[:-1]) + written}
    assert (done.status, done.ctx.critical) == (0, [])


def test_an_unparseable_total_and_referrers_are_null_in_a_partial_row(tmp_path: Path) -> None:
    """The script counts the unparseable referrers toward the ten pages, and so does leadline."""
    done = port.run(source, CONFIG, port.scenario(NAME, "unparseable_total_and_referrers_count_as_none"), tmp_path)

    reason = "total response was not JSON; path 2 referrers response was not JSON"
    lines = done.rows[FILE].splitlines(keepends=True)
    assert lines[-1] == partial(reason, window_total=None, top_referrers=None)
    assert lines[:-1] == golden_lines("unparseable_total_and_referrers_count_as_none")[:-1]


@pytest.mark.parametrize(
    "body",
    [{"total_events": 0}, {"total": None}, {"total": "132"}, {"total": True}, [132]],
    ids=["no total", "a null total", "a string", "a boolean", "a list"],
)
def test_a_total_it_cant_use_is_null_in_a_partial_row(tmp_path: Path, body: Any) -> None:
    done = run(tmp_path, *three_pages(total=exchange(gc.TOTAL, body)))

    reason = "total response had an unexpected shape"
    assert done.rows[FILE].splitlines(keepends=True)[-1] == partial(reason, window_total=None)


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"refs": {}},
        {"refs": ["github.com"]},
        {"refs": [{"name": None, "count": 1}]},
        {"refs": [{"name": "github.com", "count": "1"}]},
    ],
    ids=["a list", "the refs an object", "a ref not an object", "a null name", "a count not a number"],
)
def test_referrers_it_cant_use_are_null_in_a_partial_row(tmp_path: Path, body: Any) -> None:
    done = run(tmp_path, *three_pages(refs=[exchange(gc.refs_url(1), body), *gc.REFERRERS[1:]]))

    reason = "path 1 referrers response had an unexpected shape"
    assert done.rows[FILE].splitlines(keepends=True)[-1] == partial(reason, top_referrers=None)


@pytest.mark.parametrize("code", [401, 403])
def test_a_rejected_optional_request_is_critical_with_its_partial_row(tmp_path: Path, code: int) -> None:
    done = run(tmp_path, *three_pages(total=exchange(gc.TOTAL, "", status=code)))

    reason = f"total request failed (HTTP {code})"
    assert done.rows[FILE].splitlines(keepends=True)[-1] == partial(reason, window_total=None)
    assert done.ctx.critical == [Critical(NAME, FILE, f"{SITE}: HTTP {code} from GoatCounter's API")]
    assert done.status == 1


@pytest.mark.parametrize(
    ("responses", "row"),
    [
        (
            three_pages(total=exchange(gc.TOTAL, "", status=404)),
            partial("total request failed (HTTP 404)", window_total=None),
        ),
        (
            three_pages(refs=[exchange(gc.refs_url(1), "", status=404), *gc.REFERRERS[1:]]),
            partial("path 1 referrers request failed (HTTP 404)", top_referrers=None),
        ),
    ],
    ids=["total", "referrers"],
)
def test_an_optional_404_is_a_partial_row_and_recoverable(tmp_path: Path, responses: list[Any], row: str) -> None:
    """GoatCounter answers 404 for a readable site at times (ADR: one failed night recovers; consecutive
    failures are critical)."""
    done = run(tmp_path, *responses)

    assert done.rows[FILE].splitlines(keepends=True)[-1] == row
    assert (done.status, done.ctx.critical) == (0, [])


# --- ADR: a run never erases what it didn't get ---


def test_a_failed_part_keeps_the_value_an_earlier_run_got(tmp_path: Path) -> None:
    """An earlier run of the day got the total; this one fails to, keeps it, and the row is good."""
    earlier = yesterday().replace(NOW, "2026-09-23T08:00:00+00:00")

    done = run(tmp_path, *three_pages(total=exchange(gc.TOTAL, "", status=500)), seed=BEFORE + earlier)

    assert done.rows == {FILE: BEFORE + yesterday()}
    assert f"Kept the stored window_total for 2026-09-22 in {FILE}, which this run failed to get" in done.log
    assert done.ctx.corrections == []


def test_a_partial_row_is_filled_by_a_later_run_and_becomes_good(tmp_path: Path) -> None:
    earlier = partial("total request failed (HTTP 500)", window_total=None)

    done = run(tmp_path, *three_pages(), seed=BEFORE + earlier)

    assert done.rows == {FILE: BEFORE + yesterday()}


def test_a_failure_keeps_a_partial_row(tmp_path: Path) -> None:
    earlier = partial("total request failed (HTTP 500)", window_total=None)

    done = run(tmp_path, *[failed(gc.HITS)] * 3, seed=EARLIER + earlier)

    assert done.rows == {FILE: EARLIER + earlier}
    assert f"WARN: kept the partial row for 2026-09-22 in {FILE}; fetch_failed not recorded" in done.log


def test_a_past_days_partial_row_is_not_backfilled_over(tmp_path: Path) -> None:
    """A partial row for a past day holds the values its night got; a backfill row would erase them."""
    past = partial("total request failed (HTTP 500)", window_total=None).replace("2026-09-22", "2026-09-21")
    seed = BEFORE.replace(gc.row("2026-09-21", 1), past)

    done = run(tmp_path, *three_pages(), seed=seed)

    assert done.rows == {FILE: seed + yesterday()}


# --- check() ---


def check(tmp_path: Path, *responses: Any) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, source.ENV):
        findings = source.check(ctx, CONFIG)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


def test_check_finds_the_site_and_that_the_token_can_read_it(tmp_path: Path) -> None:
    assert check(tmp_path, gc.total(gc.THREE)) == [Finding("ok", f"{SITE}: the site exists and the token can read it")]


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (
            exchange(gc.TOTAL, '{"error":"unauthorized"}', status=401),
            Finding("error", f"{SITE}: HTTP 401: the site doesn't exist, or the token can't read it"),
        ),
        (
            exchange(gc.TOTAL, '{"error":"forbidden"}', status=403),
            Finding("error", f"{SITE}: HTTP 403: the site doesn't exist, or the token can't read it"),
        ),
        (
            exchange(gc.TOTAL, "", status=404),
            Finding("error", f"{SITE}: HTTP 404: the site doesn't exist, or the token can't read it"),
        ),
        (exchange(gc.TOTAL, "", status=429), Finding("warn", f"{SITE}: could not read the site: HTTP 429")),
        (failed(gc.TOTAL), Finding("warn", f"{SITE}: could not read the site: HTTP 0")),
        (
            exchange(gc.TOTAL, "<html>maintenance</html>"),
            Finding("warn", f"{SITE}: could not read the site: total response was not JSON"),
        ),
    ],
    ids=["unauthorized", "forbidden", "not found", "rate limited", "no connection", "not JSON"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(tmp_path: Path, response: Any, finding: Finding) -> None:
    assert check(tmp_path, response) == [finding]


# --- the summary ---


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(source, CONFIG, "week_backfills_missing_days_and_refreshes_yesterday", tmp_path) == [
        "",
        "### goatcounter: example",
        "",
        "- Page views: 78 in 7d",
    ]
