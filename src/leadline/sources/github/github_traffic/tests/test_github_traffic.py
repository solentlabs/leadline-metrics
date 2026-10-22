"""github_traffic: the script's rows on its scenarios, and leadline's side of each decided divergence.

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

from leadline.context import Critical, Finding
from leadline.http import SavedResponses
from leadline.sources.github import github_traffic as source
from leadline.sources.github.github_traffic.tests import scenarios as gt
from leadline.sources.github.tests.builders import GITHUB_API, github_referrer, github_traffic

NAME = "github_traffic"
CONFIG = gt.LEADLINE_CONFIG
FILE = gt.FILE
REPO = gt.REPO
EARLIER = gt.SEEDED[FILE]
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert source.NAME == NAME
    assert source.ENV == ("GITHUB_TOKEN",)
    assert source.LAGGING is False  # a snapshot for the run date


def test_a_valid_config_has_no_problems() -> None:
    assert source.validate([{"repo": "a/b"}, {"repo": "a/c", "file": "x-{repo_name}.jsonl"}]) == []


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"repo": "a/b"}, [": must be a list of entries"]),
        (["a/b"], ["[0]: must be an object"]),
        ([{}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a"}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a/b/c"}], ["[0].repo: must be owner/name"]),
        ([{"repo": 5}], ["[0].repo: must be owner/name"]),
        (
            [{"repo": "a/b", "file": "x-{label}.jsonl"}],
            ["[0].file: 'x-{label}.jsonl' uses {label}, which is not one of: repo, repo_name"],
        ),
        (
            [{"repo": "a/b", "file": "x-{repo}.jsonl"}],
            ["[0].file: 'x-{repo}.jsonl' must fill to a plain *.jsonl name, not 'x-a/b.jsonl'"],
        ),
    ],
    ids=[
        "not a list",
        "an entry not an object",
        "no repo",
        "a repo without its owner",
        "a repo with two slashes",
        "a repo not a string",
        "a bad file",
        "a file the repo can't name",
    ],
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert source.validate(cfg) == problems


def test_files_are_the_default_or_the_template_of_each_entry() -> None:
    cfg = [{"repo": "a/one"}, {"repo": "a/two", "file": "traffic-{repo_name}.jsonl"}]

    assert source.files(cfg) == {"[0]": "github-traffic-one.jsonl", "[1]": "traffic-two.jsonl"}


def test_it_is_skipped_without_a_token(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "skips_without_a_token"), tmp_path)

    assert "SKIP: github_traffic (GITHUB_TOKEN not set)" in done.log
    assert (done.status, done.rows) == (0, {})


def test_it_sends_the_token_and_asks_for_the_github_json(tmp_path: Path) -> None:
    sent: list[Any] = []
    transport = SavedResponses(gt.responses())

    def recording(request: Any, timeout: float) -> Any:
        sent.append(request)
        return transport(request, timeout)

    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    ctx.http._transport = recording  # to see the request headers
    with port.credentials({"GITHUB_TOKEN": "t0ken"}, source.ENV):
        source.collect(ctx, CONFIG)

    assert [request.full_url for request in sent] == [gt.VIEWS, gt.CLONES, gt.REFERRERS]
    assert {request.get_header("Authorization") for request in sent} == {"Bearer t0ken"}
    assert {request.get_header("Accept") for request in sent} == {"application/vnd.github+json"}


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    done = port.run(source, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == [
        "an_escaped_quote_in_the_referrers_writes_nothing",
        "failed_clones_write_nothing",
        "failed_referrers_leave_the_list_empty",
        "failed_views_write_nothing",
        "referrers_not_a_list_write_nothing",
        "triple_quotes_in_the_referrers_write_nothing",
        "unparseable_views_write_nothing",
    ]


def partial_row(reason: str) -> str:
    """The snapshot scenario's row with its referrers null: a partial row (ADR: a partly failed row keeps its good
    parts)."""
    golden = scenarios.load_golden(NAME)["snapshot_with_the_top_five_referrers"]["rows"][FILE]
    row = json.loads(golden)
    captured_at = row.pop("captured_at")
    row["top_referrers"] = None
    return json.dumps({**row, "status": "partial", "reason": reason, "captured_at": captured_at}) + "\n"


def test_a_failed_referrers_fetch_is_a_partial_row_with_the_views_and_clones(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "failed_referrers_leave_the_list_empty"), tmp_path)

    assert done.rows == {FILE: partial_row("referrers request failed (HTTP 500)")}
    assert f"WARN: Failed to fetch GitHub referrers for {REPO}: HTTP 500" in done.log
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_failed_referrers_fetch_keeps_the_referrers_an_earlier_run_got(tmp_path: Path) -> None:
    """ADR: a run never erases what it didn't get."""
    scenario = port.scenario(NAME, "snapshot_with_the_top_five_referrers")
    port.run(source, CONFIG, scenario, tmp_path / "first")
    kept = port.scenario(NAME, "failed_referrers_leave_the_list_empty")
    seeded = replace(kept, seed={FILE: (tmp_path / "first" / FILE).read_text(encoding="utf-8")})

    done = port.run(source, CONFIG, seeded, tmp_path / "second")

    assert port.golden_differences(NAME, "snapshot_with_the_top_five_referrers", tmp_path / "second") == []
    assert done.rows[FILE].count("\n") == 1


# --- decided divergence: the port reads the JSON ---


def good_row(referrers: str) -> str:
    """The snapshot scenario's row with `referrers` as its top_referrers."""
    golden = scenarios.load_golden(NAME)["snapshot_with_the_top_five_referrers"]["rows"][FILE]
    row = json.loads(golden)
    row["top_referrers"] = json.loads(referrers)
    return json.dumps(row) + "\n"


@pytest.mark.parametrize(
    ("name", "referrer"),
    [
        ("an_escaped_quote_in_the_referrers_writes_nothing", 'say "hi".example'),
        ("triple_quotes_in_the_referrers_write_nothing", "it'''s.example"),
    ],
)
def test_a_referrer_the_script_cant_paste_is_written_as_it_is(tmp_path: Path, name: str, referrer: str) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, name), tmp_path)

    expected = json.dumps([{"ref": referrer, "count": 3, "unique": 1}])
    assert done.rows == {FILE: EARLIER + good_row(expected)}
    assert (done.status, done.ctx.critical) == (0, [])


# --- decided divergences: ADR "A failure is recorded as a failure" ---


def failure_row(status: str, reason: str, http_code: str | None = None) -> str:
    code = f', "http_code": "{http_code}"' if http_code is not None else ""
    return f'{{"date": "2026-09-23", "status": "{status}"{code}, "reason": "{reason}", "captured_at": "{NOW}"}}\n'


def test_failed_views_are_a_fetch_failed_row_and_critical_without_push_access(tmp_path: Path) -> None:
    """GitHub answers 403 to a token without push access, which waiting can't fix."""
    done = port.run(source, CONFIG, port.scenario(NAME, "failed_views_write_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "views request failed", "403")}
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP 403 from GitHub's traffic API")]
    assert done.status == 1


def test_failed_clones_are_a_fetch_failed_row(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "failed_clones_write_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "clones request failed", "000")}
    assert (done.status, done.ctx.critical) == (0, [])  # recoverable: the next run writes the day


def test_unparseable_views_are_a_parse_failed_row(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "unparseable_views_write_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "views response was not JSON")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_referrers_not_a_list_are_a_partial_row(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "referrers_not_a_list_write_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + partial_row("referrers response had an unexpected shape")}
    assert (done.status, done.ctx.critical) == (0, [])


def replies(views: Any = None, clones: Any = None, referrers: Any = None) -> list[Any]:
    """The three answers, each the snapshot scenario's unless given."""
    default = gt.responses()
    given = [views, clones, referrers]
    urls = [gt.VIEWS, gt.CLONES, gt.REFERRERS]
    return [e if body is None else exchange(url, body) for e, body, url in zip(default, given, urls)]


VIEWS = github_traffic("views", gt.VIEWS_BY_DAY)


@pytest.mark.parametrize(
    ("answers", "kind"),
    [
        ({"views": []}, "views"),
        ({"views": "null"}, "views"),
        ({"views": {**VIEWS, "count": None}}, "views"),
        ({"views": {**VIEWS, "uniques": "4"}}, "views"),
        ({"views": {**VIEWS, "views": {}}}, "views"),
        ({"views": {**VIEWS, "views": [5]}}, "views"),
        ({"views": {**VIEWS, "views": [{"timestamp": 20260923}]}}, "views"),
        ({"views": {**VIEWS, "views": [{"timestamp": "yesterday"}]}}, "views"),
        ({"clones": [1]}, "clones"),
        ({"clones": {"count": True, "uniques": 1}}, "clones"),
    ],
    ids=[
        "views a list",
        "views null",
        "a null views count",
        "a views uniques not a number",
        "the days an object",
        "a day not an object",
        "a timestamp not a string",
        "a timestamp not a date",
        "clones a list",
        "a clones count a boolean",
    ],
)
def test_any_answer_the_source_cant_use_is_a_parse_failed_row(tmp_path: Path, answers: Any, kind: str) -> None:
    scenario = replace(port.scenario(NAME, "unparseable_views_write_nothing"), responses=replies(**answers))

    done = port.run(source, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", f"{kind} response had an unexpected shape")}


@pytest.mark.parametrize(
    "referrers",
    [
        "null",
        ["github.com"],
        [{"referrer": "github.com", "count": 3}],
        [{"referrer": None, "count": 3, "uniques": 1}],
        [{"referrer": "github.com", "count": "3", "uniques": 1}],
    ],
    ids=[
        "referrers null",
        "a referrer not an object",
        "a referrer without uniques",
        "a referrer without a name",
        "a referrer count not a number",
    ],
)
def test_referrers_the_source_cant_use_are_a_partial_row_not_an_empty_list(tmp_path: Path, referrers: Any) -> None:
    scenario = replace(port.scenario(NAME, "unparseable_views_write_nothing"), responses=replies(referrers=referrers))

    done = port.run(source, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + partial_row("referrers response had an unexpected shape")}


def test_only_the_top_five_referrers_are_read(tmp_path: Path) -> None:
    """The script reads the first five, so a sixth it can't use costs nothing."""
    referrers = [*gt.TOP_REFERRERS[:5], {"unexpected": True}]
    scenario = replace(
        port.scenario(NAME, "snapshot_with_the_top_five_referrers"), responses=replies(referrers=referrers)
    )

    port.run(source, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, "snapshot_with_the_top_five_referrers", tmp_path) == []


def test_a_failure_keeps_a_good_row(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data."""
    good = '{"date": "2026-09-23", "views_total": 7}\n'
    scenario = replace(port.scenario(NAME, "failed_clones_write_nothing"), seed={FILE: EARLIER + good})

    done = port.run(source, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + good}
    assert f"WARN: kept the good row for 2026-09-23 in {FILE}; fetch_failed not recorded" in done.log


def test_a_good_row_replaces_a_failure_row(tmp_path: Path) -> None:
    failure = '{"date": "2026-09-23", "status": "fetch_failed", "http_code": "500"}\n'
    scenario = replace(port.scenario(NAME, "snapshot_with_the_top_five_referrers"), seed={FILE: failure})

    port.run(source, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, "snapshot_with_the_top_five_referrers", tmp_path) == []


# --- failures: recoverable or not ---


@pytest.mark.parametrize("code", [401, 403, 404])
def test_an_error_waiting_cant_fix_is_critical_at_once(tmp_path: Path, code: int) -> None:
    scenario = replace(
        port.scenario(NAME, "failed_views_write_nothing"), responses=[exchange(gt.VIEWS, "", status=code)]
    )

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
    limited = exchange(gt.CLONES, '{"message":"API rate limit exceeded"}', status=code, headers=headers)
    scenario = replace(port.scenario(NAME, "failed_clones_write_nothing"), responses=[gt.responses()[0], limited])

    done = port.run(source, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "clones request failed", str(code))}
    assert (done.status, done.ctx.critical, done.sleeps) == (0, [], [])


def test_a_failing_entry_does_not_stop_the_next(tmp_path: Path) -> None:
    other = f"{GITHUB_API}/repos/a/other/traffic"
    empty = [
        exchange(f"{other}/views", github_traffic("views", {})),
        exchange(f"{other}/clones", github_traffic("clones", {})),
        exchange(f"{other}/popular/referrers", [github_referrer("github.com", 2, 1)]),
    ]
    scenario = replace(port.scenario(NAME, "failed_views_write_nothing"), responses=[failed(gt.VIEWS), *empty])

    done = port.run(source, [*CONFIG, {"repo": "a/other"}], scenario, tmp_path)

    assert done.rows[FILE] == EARLIER + failure_row("fetch_failed", "views request failed", "000")
    assert (
        '"top_referrers": [{"ref": "github.com", "count": 2, "unique": 1}]' in done.rows["github-traffic-other.jsonl"]
    )


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
    assert check(tmp_path, CONFIG, gt.responses()[0]) == [Finding("ok", f"{REPO}: the token can read its traffic")]


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (
            exchange(gt.VIEWS, '{"message":"Must have push access to repository"}', status=403),
            Finding("error", f"{REPO}: HTTP 403: the token can't read its traffic, which needs push access"),
        ),
        (
            exchange(gt.VIEWS, '{"message":"Not Found"}', status=404),
            Finding("error", f"{REPO}: HTTP 404: the repo doesn't exist, or the token can't read it"),
        ),
        (
            exchange(gt.VIEWS, "", status=401),
            Finding("error", f"{REPO}: HTTP 401: the repo doesn't exist, or the token can't read it"),
        ),
        (
            exchange(gt.VIEWS, "", status=403, headers={"X-RateLimit-Remaining": "0"}),
            Finding("warn", f"{REPO}: could not fetch its traffic: HTTP 403, rate limited"),
        ),
        (exchange(gt.VIEWS, "", status=503), Finding("warn", f"{REPO}: could not fetch its traffic: HTTP 503")),
        (failed(gt.VIEWS), Finding("warn", f"{REPO}: could not fetch its traffic: HTTP 0")),
    ],
    ids=["no push access", "not found", "rejected", "rate limited", "unavailable", "no connection"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(tmp_path: Path, response: Any, finding: Finding) -> None:
    assert check(tmp_path, CONFIG, response) == [finding]


def test_check_checks_every_entry(tmp_path: Path) -> None:
    other = f"{GITHUB_API}/repos/a/other/traffic/views"
    findings = check(tmp_path, [*CONFIG, {"repo": "a/other"}], gt.responses()[0], exchange(other, "", status=404))

    assert [f.level for f in findings] == ["ok", "error"]


# --- the summary ---


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(source, CONFIG, "snapshot_with_the_top_five_referrers", tmp_path) == [
        "",
        "### github_traffic: solentlabs/example",
        "",
        "- Views, 14 days: 553",
        "- Unique visitors, 14 days: 82",
        "- Clones, 14 days: 96",
    ]
