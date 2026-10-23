"""github_traffic_daily: the script's rows on its scenarios, and leadline's failure rows.

The rows are compared with the goldens through tests/port.py. leadline's rows match the script's on
every scenario, which seed yesterday's good row; the failure rows leadline writes where the script
writes nothing are asserted here, on those scenarios without that row.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import port
import pytest
import scenarios
from scenarios.builders.common import exchange, failed

from leadline.context import Correction, Critical, Finding
from leadline.http import SavedResponses
from leadline.sources.github import github_traffic_daily as source
from leadline.sources.github.github_traffic_daily.tests import scenarios as gd
from leadline.sources.github.tests.builders import GITHUB_API, github_traffic

NAME = "github_traffic_daily"
CONFIG = gd.LEADLINE_CONFIG
FILE = gd.FILE
REPO = gd.REPO
EARLIER = gd.row("2026-09-21", 5)  # the seed without yesterday's row, so a failure row can be written
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it
CRITICAL = {"failed_views_write_nothing": [Critical(NAME, FILE, f"{REPO}: HTTP 403 from GitHub's traffic API")]}


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert source.NAME == NAME
    assert source.ENV == ("GITHUB_TOKEN",)
    assert source.LAGGING is True  # today is still counting, so the newest row is yesterday's


def test_a_valid_config_has_no_problems() -> None:
    assert source.validate([{"repo": "a/b"}, {"repo": "a/c", "file": "x-{repo_name}.jsonl"}]) == []


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"repo": "a/b"}, [": must be a list of entries"]),
        (["a/b"], ["[0]: must be an object"]),
        ([{}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a/b/c"}], ["[0].repo: must be owner/name"]),
        (
            [{"repo": "a/b", "file": "x-{label}.jsonl"}],
            ["[0].file: 'x-{label}.jsonl' uses {label}, which is not one of: repo, repo_name"],
        ),
    ],
    ids=["not a list", "an entry not an object", "no repo", "a repo with two slashes", "a bad file"],
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert source.validate(cfg) == problems


def test_files_are_the_default_or_the_template_of_each_entry() -> None:
    cfg = [{"repo": "a/one"}, {"repo": "a/two", "file": "daily-{repo_name}.jsonl"}]

    assert source.files(cfg) == {"[0]": "github-traffic-daily-one.jsonl", "[1]": "daily-two.jsonl"}


def test_it_is_skipped_without_a_token(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "skips_without_a_token"), tmp_path)

    assert "SKIP: github_traffic_daily (GITHUB_TOKEN not set)" in done.log
    assert (done.status, done.rows) == (0, {})


def test_it_sends_the_token_and_asks_for_the_github_json(tmp_path: Path) -> None:
    sent: list[Any] = []
    transport = SavedResponses(gd.responses())

    def recording(request: Any, timeout: float) -> Any:
        sent.append(request)
        return transport(request, timeout)

    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    ctx.http._transport = recording  # to see the request headers
    with port.credentials({"GITHUB_TOKEN": "t0ken"}, source.ENV):
        source.collect(ctx, CONFIG)

    assert [request.full_url for request in sent] == [gd.VIEWS, gd.CLONES]
    assert {request.get_header("Authorization") for request in sent} == {"Bearer t0ken"}
    assert {request.get_header("Accept") for request in sent} == {"application/vnd.github+json"}


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    done = port.run(source, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert done.ctx.critical == CRITICAL.get(scenario.name, [])
    assert done.status == (1 if done.ctx.critical else 0)


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == ["ignores_clones_days_views_lack"]


def test_a_day_with_clones_and_no_views_is_a_row_with_zero_views(tmp_path: Path) -> None:
    """The script wrote rows for the views' days only, so a day GitHub counted clones for, and no views, was lost."""
    done = port.run(source, CONFIG, port.scenario(NAME, "ignores_clones_days_views_lack"), tmp_path)

    rows = [json.loads(line) for line in done.rows[FILE].splitlines()]
    by_day = {row["date"]: row for row in rows}
    clones_only = [day for day in gd.CLONES_BY_DAY if day in gd.WINDOW[7:9] and day not in gd.WINDOW[9:12]]
    assert len(clones_only) == 2
    for day in clones_only:
        assert by_day[day.isoformat()] == {
            "date": day.isoformat(),
            "views": 0,
            "views_unique": 0,
            "clones": gd.CLONES_BY_DAY[day][0],
            "clones_unique": gd.CLONES_BY_DAY[day][1],
            "captured_at": by_day[day.isoformat()]["captured_at"],
        }
    assert sorted(by_day) == [d.isoformat() for d in gd.WINDOW[7:12] if d.isoformat() < "2026-09-23"]


def test_each_backfilled_day_is_logged(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "ignores_clones_days_views_lack"), tmp_path)

    backfilled = [line for line in done.log if line.startswith("GitHub daily traffic backfill: ")]
    assert backfilled == [f"GitHub daily traffic backfill: 2026-09-{day}" for day in (17, 18, 19, 20, 21)]


def test_a_current_file_is_logged_as_current(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "writes_nothing_when_current"), tmp_path)

    assert "GitHub daily traffic: already current" in done.log


def test_refreshing_yesterdays_good_row_with_new_counts_is_a_correction(tmp_path: Path) -> None:
    """ADR: every correction is recorded. The seed's yesterday (views 3) is re-read from the window."""
    done = port.run(source, CONFIG, port.scenario(NAME, "backfills_missing_days_and_refreshes_yesterday"), tmp_path)

    yesterday = gd.TODAY - timedelta(days=1)
    (views, views_unique), (clones, clones_unique) = gd.VIEWS_BY_DAY[yesterday], gd.CLONES_BY_DAY[yesterday]
    old = {"views": 3, "views_unique": 1, "clones": 0, "clones_unique": 0}
    new = {"views": views, "views_unique": views_unique, "clones": clones, "clones_unique": clones_unique}
    assert done.ctx.corrections == [Correction(NAME, FILE, "2026-09-22", old, new)]
    assert f"Corrected 2026-09-22 in {FILE}: views 3 -> {views}" in " ".join(done.log)


def test_backfilled_days_are_no_correction(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "ignores_clones_days_views_lack"), tmp_path)

    assert done.ctx.corrections == []


def test_an_empty_window_writes_nothing_and_is_no_failure(tmp_path: Path) -> None:
    """No days in the window is an answer: nothing to write, as in the script (no zeros, no failure row)."""
    scenario = replace(port.scenario(NAME, "an_empty_window"), seed={FILE: EARLIER})

    done = port.run(source, CONFIG, scenario, tmp_path)

    assert (done.status, done.rows) == (0, {FILE: EARLIER})
    assert f"GitHub daily traffic: no days in the window for {REPO}" in done.log


# --- ADR "A failure is recorded as a failure": a failure row for yesterday ---


def failure_row(status: str, reason: str, http_code: str | None = None) -> str:
    code = f', "http_code": "{http_code}"' if http_code is not None else ""
    return f'{{"date": "2026-09-22", "status": "{status}"{code}, "reason": "{reason}", "captured_at": "{NOW}"}}\n'


def without_yesterday(name: str, **changes: Any) -> Any:
    return replace(port.scenario(NAME, name), seed={FILE: EARLIER}, **changes)


def test_failed_views_are_a_fetch_failed_row_for_yesterday_and_critical_without_push_access(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, without_yesterday("failed_views_write_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "views request failed", "403")}
    assert done.ctx.critical == CRITICAL["failed_views_write_nothing"]
    assert done.status == 1


def test_failed_clones_are_a_fetch_failed_row_for_yesterday(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, without_yesterday("failed_clones_write_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "clones request failed", "000")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_unparseable_clones_are_a_parse_failed_row_for_yesterday(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, without_yesterday("unparseable_clones_write_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "clones response was not JSON")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_day_without_a_timestamp_is_a_parse_failed_row_for_yesterday(tmp_path: Path) -> None:
    """The rule for an item it can't place on a day: the whole answer is one it can't use.

    Writing the other days would be the only port that writes part of an answer, and a clones item
    it can't place would leave its day's clones read as zero. The window keeps 14 days, so the next
    good run backfills every day this one misses.
    """
    done = port.run(source, CONFIG, without_yesterday("items_without_a_timestamp"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "views response had an unexpected shape")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_failure_row_is_kept_out_where_yesterday_is_good(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data. The scenarios seed yesterday's good row, so the rows match."""
    done = port.run(source, CONFIG, port.scenario(NAME, "failed_clones_write_nothing"), tmp_path)

    assert f"WARN: kept the good row for 2026-09-22 in {FILE}; fetch_failed not recorded" in done.log


def test_a_failure_row_is_backfilled_by_the_next_good_run(tmp_path: Path) -> None:
    """A failure row doesn't count as a day the file has: the next good run replaces it."""
    failure = '{"date": "2026-09-19", "status": "fetch_failed", "http_code": "500"}\n'
    scenario = replace(
        port.scenario(NAME, "writes_nothing_when_current"), seed={FILE: gd.row("2026-09-20", 2) + failure}
    )

    done = port.run(source, CONFIG, scenario, tmp_path)

    day_rows = scenarios.load_golden(NAME)["ignores_clones_days_views_lack"]["rows"][FILE].splitlines()
    assert day_rows[0].startswith('{"date": "2026-09-19", ')
    assert done.rows[FILE].splitlines() == [gd.row("2026-09-20", 2).rstrip("\n"), day_rows[0], day_rows[2]]


VIEWS = github_traffic("views", gd.VIEWS_BY_DAY)
CLONES = github_traffic("clones", gd.CLONES_BY_DAY)
DAY = {"timestamp": "2026-09-22T00:00:00Z", "count": 3, "uniques": 1}


@pytest.mark.parametrize(
    ("views", "clones", "kind"),
    [
        ([], CLONES, "views"),
        ({**VIEWS, "views": {}}, CLONES, "views"),
        ({**VIEWS, "views": [5]}, CLONES, "views"),
        ({**VIEWS, "views": [{**DAY, "timestamp": None}]}, CLONES, "views"),
        ({**VIEWS, "views": [{**DAY, "timestamp": "yesterday"}]}, CLONES, "views"),
        ({**VIEWS, "views": [{**DAY, "count": None}]}, CLONES, "views"),
        ({**VIEWS, "views": [{**DAY, "uniques": "1"}]}, CLONES, "views"),
        (VIEWS, "null", "clones"),
        (VIEWS, {**CLONES, "clones": None}, "clones"),
        (VIEWS, {**CLONES, "clones": [{"count": 3, "uniques": 1}]}, "clones"),
        (VIEWS, {**CLONES, "clones": [{**DAY, "count": True}]}, "clones"),
    ],
    ids=[
        "views a list",
        "the days an object",
        "a day not an object",
        "a null timestamp",
        "a timestamp not a date",
        "a null count",
        "a unique count not a number",
        "clones null",
        "the clones days null",
        "a clones day without a timestamp",
        "a clones count a boolean",
    ],
)
def test_any_answer_the_source_cant_use_is_a_parse_failed_row(
    tmp_path: Path, views: Any, clones: Any, kind: str
) -> None:
    scenario = without_yesterday(
        "unparseable_clones_write_nothing", responses=[exchange(gd.VIEWS, views), exchange(gd.CLONES, clones)]
    )

    done = port.run(source, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", f"{kind} response had an unexpected shape")}


@pytest.mark.parametrize(
    ("views", "reason"),
    [
        ({}, "views response is missing views"),
        ({"count": 0, "uniques": 0}, "views response is missing views"),
        (
            {**VIEWS, "views": [{"timestamp": DAY["timestamp"], "uniques": 1}]},
            "views response is missing count in a day",
        ),
        (
            {**VIEWS, "views": [{"timestamp": DAY["timestamp"], "count": 3}]},
            "views response is missing uniques in a day",
        ),
    ],
    ids=["an empty object", "no list of days", "a day without a count", "a day without uniques"],
)
def test_an_answer_without_its_counts_is_a_parse_failed_row_naming_them_not_zeros(
    tmp_path: Path, views: Any, reason: str
) -> None:
    """ADR: a failure is recorded as a failure. The script read what was absent as 0."""
    scenario = without_yesterday(
        "unparseable_clones_write_nothing", responses=[exchange(gd.VIEWS, views), exchange(gd.CLONES, CLONES)]
    )

    done = port.run(source, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", reason)}


# --- failures: recoverable or not ---


@pytest.mark.parametrize("code", [401, 403, 404])
def test_an_error_waiting_cant_fix_is_critical_at_once(tmp_path: Path, code: int) -> None:
    scenario = without_yesterday("failed_views_write_nothing", responses=[exchange(gd.VIEWS, "", status=code)])

    done = port.run(source, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "views request failed", str(code))}
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP {code} from GitHub's traffic API")]
    assert done.status == 1


@pytest.mark.parametrize(
    ("code", "headers"),
    [(403, {"X-RateLimit-Remaining": "0"}), (403, {"Retry-After": "60"}), (429, {})],
    ids=["403 none remaining", "403 retry-after", "429"],
)
def test_a_rate_limit_is_recoverable_and_not_retried(tmp_path: Path, code: int, headers: dict[str, str]) -> None:
    limited = exchange(gd.VIEWS, '{"message":"API rate limit exceeded"}', status=code, headers=headers)
    scenario = without_yesterday("failed_views_write_nothing", responses=[limited])

    done = port.run(source, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "views request failed", str(code))}
    assert (done.status, done.ctx.critical, done.sleeps) == (0, [], [])


def test_a_failing_entry_does_not_stop_the_next(tmp_path: Path) -> None:
    other = f"{GITHUB_API}/repos/a/other/traffic"
    day = {gd.WINDOW[-2]: (4, 2)}
    answers = [
        exchange(f"{other}/views", github_traffic("views", day)),
        exchange(f"{other}/clones", github_traffic("clones", day)),
    ]
    scenario = without_yesterday("failed_views_write_nothing", responses=[failed(gd.VIEWS), *answers])

    done = port.run(source, [*CONFIG, {"repo": "a/other"}], scenario, tmp_path)

    assert done.rows[FILE] == EARLIER + failure_row("fetch_failed", "views request failed", "000")
    assert done.rows["github-traffic-daily-other.jsonl"].startswith('{"date": "2026-09-22", "views": 4, ')


# --- check() ---


def check(tmp_path: Path, cfg: Any, *responses: Any) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, source.ENV):
        findings = source.check(ctx, cfg)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


def test_check_finds_the_token_can_read_the_traffic(tmp_path: Path) -> None:
    assert check(tmp_path, CONFIG, gd.responses()[0]) == [Finding("ok", f"{REPO}: the token can read its traffic")]


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (
            exchange(gd.VIEWS, '{"message":"Must have push access to repository"}', status=403),
            Finding("error", f"{REPO}: HTTP 403: the token can't read its traffic, which needs push access"),
        ),
        (
            exchange(gd.VIEWS, '{"message":"Not Found"}', status=404),
            Finding("error", f"{REPO}: HTTP 404: the repo doesn't exist, or the token can't read it"),
        ),
        (
            exchange(gd.VIEWS, "", status=429),
            Finding("warn", f"{REPO}: could not fetch its traffic: HTTP 429, rate limited"),
        ),
        (failed(gd.VIEWS), Finding("warn", f"{REPO}: could not fetch its traffic: HTTP 0")),
    ],
    ids=["no push access", "not found", "rate limited", "no connection"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(tmp_path: Path, response: Any, finding: Finding) -> None:
    assert check(tmp_path, CONFIG, response) == [finding]


# --- the summary ---


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(source, CONFIG, "backfills_missing_days_and_refreshes_yesterday", tmp_path) == [
        "",
        "### github_traffic_daily: solentlabs/example",
        "",
        "- Views: 281 in 7d",
        "- Clones: 45 in 7d",
    ]
