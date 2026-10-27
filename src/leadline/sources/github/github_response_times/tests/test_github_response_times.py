"""github_response_times: the script's rows on its scenarios, and leadline's side of each decided divergence.

The rows are compared with the goldens through tests/port.py; the scenarios where leadline differs by
a decision are listed in scenarios.py (DIVERGENCES) and asserted here instead.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import port
import pytest
import scenarios
from row_files import files_in
from scenarios.builders.common import exchange, failed

from leadline import trends
from leadline.context import Correction, Critical, Finding
from leadline.http import SavedResponses
from leadline.sources.github import github_response_times
from leadline.sources.github.github_response_times.tests import scenarios as grt
from leadline.sources.github.tests.builders import GITHUB_API, GITHUB_GRAPHQL, github_repo, graphql_body

NAME = "github_response_times"
CONFIG = grt.LEADLINE_CONFIG
FILE = grt.FILE
REPO = "solentlabs/example"
EARLIER = grt.EARLIER
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it
FIRST_SENT = grt.FIRST["request"]["body"]


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert github_response_times.NAME == NAME
    assert github_response_times.ENV == ("GITHUB_TOKEN",)
    assert github_response_times.LAGGING is False


def test_a_valid_config_has_no_problems() -> None:
    cfg = [{"repo": "a/b", "maintainer": "m"}, {"repo": "a/c", "maintainer": "m", "file": "x-{repo_name}.jsonl"}]

    assert github_response_times.validate(cfg) == []


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"repo": "a/b", "maintainer": "m"}, [": must be a list of entries"]),
        (["a/b"], ["[0]: must be an object"]),
        ([{"maintainer": "m"}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a/b"}], ["[0].maintainer: must be a login"]),
        ([{"repo": "a/b", "maintainer": ""}], ["[0].maintainer: must be a login"]),
        ([{"repo": "a/b", "maintainer": ["m"]}], ["[0].maintainer: must be a login"]),
        (
            [{"repo": "a/b", "maintainer": "m", "file": "x-{label}.jsonl"}],
            ["[0].file: 'x-{label}.jsonl' uses {label}, which is not one of: repo, repo_name"],
        ),
    ],
    ids=["not a list", "an entry not an object", "no repo", "no maintainer", "an empty maintainer", "a list", "a file"],
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert github_response_times.validate(cfg) == problems


def test_files_are_the_default_or_the_template_of_each_entry() -> None:
    cfg = [{"repo": "a/one", "maintainer": "m"}, {"repo": "a/two", "maintainer": "m", "file": "r-{repo_name}.jsonl"}]

    assert github_response_times.files(cfg) == {"[0]": "github-response-times-one.jsonl", "[1]": "r-two.jsonl"}


def test_it_is_skipped_without_a_token(tmp_path: Path) -> None:
    done = port.run(github_response_times, CONFIG, port.scenario(NAME, "skips_without_a_token"), tmp_path)

    assert "SKIP: github_response_times (GITHUB_TOKEN not set)" in done.log
    assert (done.status, done.rows) == (0, {})


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    done = port.run(github_response_times, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == [
        "a_closed_issue_is_answered_at_its_closing",
        "a_failed_later_page_writes_nothing",
        "graphql_errors_write_nothing",
    ]


def row(tmp_path: Path) -> dict[str, Any]:
    written: dict[str, Any] = json.loads((tmp_path / FILE).read_text(encoding="utf-8").splitlines()[-1])
    return written


def test_replies_exactly_on_a_bound_count_as_within_it(tmp_path: Path) -> None:
    """One reply each at 1, 24 and 72 hours."""
    port.run(github_response_times, CONFIG, port.scenario(NAME, "replies_on_the_bounds_count_as_within"), tmp_path)

    written = row(tmp_path)
    assert (written["within_1h_pct"], written["within_24h_pct"], written["within_72h_pct"]) == (33.3, 66.7, 100.0)


def test_it_asks_for_each_page_after_the_last_ones_cursor(tmp_path: Path) -> None:
    """The saved responses are exactly the script's requests: the second sends the first's endCursor."""
    done = port.run(github_response_times, CONFIG, port.scenario(NAME, "pages_through_cursors"), tmp_path)

    assert f"Fetched 2 page(s) of issues with comments for {REPO}" in done.log


def test_a_next_page_without_a_cursor_is_the_last_as_in_the_script(tmp_path: Path) -> None:
    body = json.loads(grt.FIRST["response"]["body"])
    body["data"]["repository"]["issues"]["pageInfo"]["endCursor"] = ""
    scenario = replace(
        port.scenario(NAME, "pages_through_cursors"),
        responses=[{**grt.FIRST, "response": {"status": 200, "body": json.dumps(body)}}],
    )

    done = port.run(github_response_times, CONFIG, scenario, tmp_path)

    assert row(tmp_path)["issues_scanned"] == 5
    assert f"Fetched 1 page(s) of issues with comments for {REPO}" in done.log


def test_the_pages_text_order_changes_no_value(tmp_path: Path) -> None:
    """The script reads page_10 and page_11 before page_2; its golden row is leadline's, read in page order."""
    scenario = port.scenario(NAME, "eleven_pages_read_in_text_order_give_the_same_row")
    assert len(scenario.responses) == 11

    port.run(github_response_times, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []


# --- decided divergences ---


def failure_row(status: str, reason: str, http_code: str | None = None) -> str:
    code = f', "http_code": "{http_code}"' if http_code is not None else ""
    return f'{{"date": "2026-09-23", "status": "{status}"{code}, "reason": "{reason}", "captured_at": "{NOW}"}}\n'


def test_graphql_errors_are_a_graphql_error_row(tmp_path: Path) -> None:
    """The scenario's error has no type, such as a timeout: waiting can fix it."""
    done = port.run(github_response_times, CONFIG, port.scenario(NAME, "graphql_errors_write_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("graphql_error", "Something went wrong")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_failed_later_page_is_a_fetch_failed_row(tmp_path: Path) -> None:
    done = port.run(github_response_times, CONFIG, port.scenario(NAME, "a_failed_later_page_writes_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "issues request failed", "502")}
    assert (done.status, done.ctx.critical, done.sleeps) == (0, [], [])
    assert f"ERROR: Failed to fetch issue page 2 for {REPO}: HTTP 502" in done.log


def test_a_closed_community_issue_is_answered_at_its_closing_or_its_first_reply_whichever_comes_first(
    tmp_path: Path,
) -> None:
    """ADR: a closed community issue is answered. Closed at 2 hours with no reply, replied to at 5 and closed at 10,
    closed at 3 and replied to at 30, closed at 72 with only the community's comments: 2, 5, 3 and 72 hours. The open
    one is never answered, and the maintainer's own isn't counted. The script's golden counts only the replies."""
    scenario = port.scenario(NAME, "a_closed_issue_is_answered_at_its_closing")

    done = port.run(github_response_times, CONFIG, scenario, tmp_path)

    assert done.rows == {
        FILE: '{"date": "2026-09-23", "issues_scanned": 6, "community_issues": 5, "answered": 4, '
        '"never_answered": 1, "median_hours": 4.0, "mean_hours": 20.5, "p90_hours": 72.0, "fastest_hours": 2.0, '
        '"within_1h_pct": 0.0, "within_24h_pct": 75.0, "within_72h_pct": 100.0, '
        f'"captured_at": "{NOW}"}}\n'
    }
    assert (done.status, done.ctx.critical) == (0, [])


def first_page(body: Any) -> Any:
    return exchange(GITHUB_GRAPHQL, body, method="POST", sent=FIRST_SENT)


def unreadable(tmp_path: Path, *responses: Any) -> port.PortRun:
    scenario = replace(port.scenario(NAME, "graphql_errors_write_nothing"), responses=list(responses))
    done = port.run(github_response_times, CONFIG, scenario, tmp_path)
    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "issues response had an unexpected shape")}
    assert (done.status, done.ctx.critical) == (0, [])
    return done


def issues(page_info: Any = None, nodes: Any = ()) -> Any:
    info = {"hasNextPage": False, "endCursor": None} if page_info is None else page_info
    return {"data": {"repository": {"issues": {"pageInfo": info, "nodes": list(nodes) if nodes is not None else None}}}}


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"data": None},
        {"data": {"repository": None}},
        {"data": {"repository": {"issues": None}}},
        issues(page_info={}),
        issues(page_info={"hasNextPage": "no", "endCursor": None}),
        issues(page_info={"hasNextPage": True, "endCursor": None}),
    ],
    ids=[
        "null",
        "a list",
        "null data without errors",
        "a null repository",
        "null issues",
        "no page info",
        "hasNextPage not a boolean",
        "a next page without a cursor",
    ],
)
def test_a_page_it_cant_page_through_is_a_parse_failed_row(tmp_path: Path, body: Any) -> None:
    """The script stops at such a page: nothing after it is asked for."""
    unreadable(tmp_path, first_page(body))


ISSUE = grt.issue(1, "example-user", [("2026-09-01T00:30:00Z", grt.MAINTAINER)])
COMMENT = ISSUE["comments"]["nodes"][0]


@pytest.mark.parametrize(
    "nodes",
    [
        None,
        [None],
        [{**ISSUE, "author": "example-user"}],
        [{**ISSUE, "author": {"login": 5}}],
        [{**ISSUE, "createdAt": None}],
        [{**ISSUE, "createdAt": "2026-09-01T00:00:00"}],
        [{**ISSUE, "closedAt": "yesterday"}],
        [{**ISSUE, "closedAt": 5}],
        [{**ISSUE, "comments": "none"}],
        [{**ISSUE, "comments": {"nodes": None}}],
        [{**ISSUE, "comments": {"nodes": [None]}}],
        [{**ISSUE, "comments": {"nodes": [{**COMMENT, "author": ["m"]}]}}],
        [{**ISSUE, "comments": {"nodes": [{**COMMENT, "createdAt": "soon"}]}}],
    ],
    ids=[
        "null nodes",
        "a null issue",
        "an author not an object",
        "a login not a string",
        "no created time",
        "a time without its offset",
        "a closed time not a time",
        "a closed time not a string",
        "comments not an object",
        "null comment nodes",
        "a null comment",
        "a comment author not an object",
        "a comment time not a time",
    ],
)
def test_issues_it_cant_read_are_a_parse_failed_row(tmp_path: Path, nodes: Any) -> None:
    """ADR: a failure is recorded as a failure; the issues are read whole."""
    unreadable(tmp_path, first_page(issues(nodes=nodes)))


def test_issues_it_cant_read_still_have_the_later_pages_asked_for_as_in_the_script(tmp_path: Path) -> None:
    """The script reads each page's cursor as it goes, and the issues only once every page is in."""
    body = json.loads(grt.FIRST["response"]["body"])
    body["data"]["repository"]["issues"]["nodes"][0] = None
    bad_first = {**grt.FIRST, "response": {"status": 200, "body": json.dumps(body)}}

    unreadable(tmp_path, bad_first, grt.SECOND)


def test_an_unparseable_page_is_a_parse_failed_row(tmp_path: Path) -> None:
    scenario = replace(port.scenario(NAME, "graphql_errors_write_nothing"), responses=[first_page("<html>")])

    done = port.run(github_response_times, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "issues response was not JSON")}


def test_a_failure_keeps_a_good_row(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data."""
    good = '{"date": "2026-09-23", "issues_scanned": 3}\n'
    scenario = replace(port.scenario(NAME, "a_failed_later_page_writes_nothing"), seed={FILE: EARLIER + good})

    done = port.run(github_response_times, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + good}
    assert f"WARN: kept the good row for 2026-09-23 in {FILE}; fetch_failed not recorded" in done.log


# --- failures: recoverable or not ---


@pytest.mark.parametrize("code", [401, 403, 404])
def test_an_error_waiting_cant_fix_is_critical_at_once(tmp_path: Path, code: int) -> None:
    rejected = exchange(GITHUB_GRAPHQL, "", method="POST", sent=FIRST_SENT, status=code)
    scenario = replace(port.scenario(NAME, "graphql_errors_write_nothing"), responses=[rejected])

    done = port.run(github_response_times, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "issues request failed", str(code))}
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP {code} from GitHub's GraphQL API")]
    assert done.status == 1


def test_a_graphql_error_waiting_cant_fix_is_critical_at_once(tmp_path: Path) -> None:
    body = {"data": {"repository": None}, "errors": [{"type": "NOT_FOUND", "message": "Could not resolve"}]}

    done = port.run(
        github_response_times,
        CONFIG,
        replace(port.scenario(NAME, "graphql_errors_write_nothing"), responses=[first_page(body)]),
        tmp_path,
    )

    assert done.rows == {FILE: EARLIER + failure_row("graphql_error", "Could not resolve")}
    reason = f"{REPO}: GitHub's GraphQL API answered: Could not resolve"
    assert (done.status, done.ctx.critical) == (1, [Critical(NAME, FILE, reason)])


def test_a_graphql_rate_limit_is_recoverable(tmp_path: Path) -> None:
    body = {"data": None, "errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]}

    done = port.run(
        github_response_times,
        CONFIG,
        replace(port.scenario(NAME, "graphql_errors_write_nothing"), responses=[first_page(body)]),
        tmp_path,
    )

    assert done.rows == {FILE: EARLIER + failure_row("graphql_error", "API rate limit exceeded")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_no_connection_is_recorded_as_000_and_is_recoverable(tmp_path: Path) -> None:
    scenario = replace(
        port.scenario(NAME, "graphql_errors_write_nothing"),
        responses=[failed(GITHUB_GRAPHQL, method="POST", sent=FIRST_SENT)],
    )

    done = port.run(github_response_times, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "issues request failed", "000")}
    assert done.status == 0


def test_a_failing_entry_does_not_stop_the_next(tmp_path: Path) -> None:
    other = {"repo": "a/other", "maintainer": grt.MAINTAINER}
    sent = graphql_body(grt.QUERY, {"o": "a", "n": "other", "c": None})
    answered = exchange(GITHUB_GRAPHQL, issues(), method="POST", sent=sent)
    failing = exchange(GITHUB_GRAPHQL, "", method="POST", sent=FIRST_SENT, status=502)
    scenario = replace(port.scenario(NAME, "graphql_errors_write_nothing"), responses=[failing, answered])

    done = port.run(github_response_times, [*CONFIG, other], scenario, tmp_path)

    assert done.rows[FILE] == EARLIER + failure_row("fetch_failed", "issues request failed", "502")
    assert '"issues_scanned": 0' in done.rows["github-response-times-other.jsonl"]


# --- check() ---

REPO_URL = f"{GITHUB_API}/repos/{REPO}"
CHECK_SENT = graphql_body(
    "query($o:String!,$n:String!,$m:String!){repository(owner:$o,name:$n){issues(first:1){totalCount}} "
    "user(login:$m){login}}",
    {"o": "solentlabs", "n": "example", "m": "example-maintainer"},
)
MAINTAINER_EXISTS = Finding("ok", f'{REPO}: the maintainer login "example-maintainer" exists')
NO_SUCH_USER = {"type": "NOT_FOUND", "path": ["user"], "message": "Could not resolve to a User"}


def query_answer(body: Any, status: int = 200) -> Any:
    return exchange(GITHUB_GRAPHQL, body, method="POST", sent=CHECK_SENT, status=status)


def check(tmp_path: Path, *responses: Any) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, github_response_times.ENV):
        findings = github_response_times.check(ctx, CONFIG)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


def test_check_finds_the_repo_and_queries_its_issues(tmp_path: Path) -> None:
    found = check(
        tmp_path,
        exchange(REPO_URL, github_repo(REPO)),
        query_answer({"data": {"repository": {"issues": {"totalCount": 3}}, "user": {"login": "example-maintainer"}}}),
    )

    assert found == [
        Finding("ok", f"{REPO}: the repo exists"),
        Finding("ok", f"{REPO}: the token can query its issues"),
        MAINTAINER_EXISTS,
    ]


def test_check_finds_a_missing_maintainer_login_in_the_same_query(tmp_path: Path) -> None:
    """A mistyped login would misread every night, with no failure to show it."""
    answer = {"data": {"repository": {"issues": {"totalCount": 3}}, "user": None}, "errors": [NO_SUCH_USER]}

    found = check(tmp_path, exchange(REPO_URL, github_repo(REPO)), query_answer(answer))

    missing = f'{REPO}: the maintainer login "example-maintainer" doesn\'t exist, so '
    missing += "every issue would count as the community's and never answered"
    assert found[-1] == Finding("error", missing)


def test_check_stops_at_a_missing_repo(tmp_path: Path) -> None:
    found = check(tmp_path, exchange(REPO_URL, "", status=404))

    assert found == [Finding("error", f"{REPO}: HTTP 404: the repo doesn't exist, or the token can't read it")]


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (
            query_answer({"data": None, "errors": [{"type": "INSUFFICIENT_SCOPES", "message": "needs repo scope"}]}),
            Finding("error", f"{REPO}: could not query its issues: needs repo scope"),
        ),
        (
            query_answer({"data": None, "errors": [{"type": "RATE_LIMITED", "message": "rate limit exceeded"}]}),
            Finding("warn", f"{REPO}: could not query its issues: rate limit exceeded"),
        ),
        (query_answer("", status=502), Finding("warn", f"{REPO}: could not fetch its issues: HTTP 502")),
        (
            query_answer("<html>"),
            Finding("warn", f"{REPO}: its issues response was not JSON"),
        ),
    ],
    ids=["missing scopes", "rate limited", "unavailable", "not JSON"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(tmp_path: Path, response: Any, finding: Finding) -> None:
    assert check(tmp_path, exchange(REPO_URL, github_repo(REPO)), response)[-1] == finding


# --- the summary ---


def test_not_answered_is_not_charted_and_the_hours_are() -> None:
    (section,) = github_response_times.summary(CONFIG)

    assert {shown.field: shown.charted for shown in section.shown} == {"median_hours": True, "never_answered": False}


def test_the_median_mean_90th_percentile_and_fastest_are_lines_of_one_chart_and_only_the_median_is_in_the_summary() -> (
    None
):
    (section,) = github_response_times.summary(CONFIG)

    assert [shown.field for shown in section.shown] == ["median_hours", "never_answered"]
    assert [(c.field, c.label, c.kind, c.chart) for c in [section.shown[0], *section.charts]] == [
        ("median_hours", "Median hours to an answer", "snapshot", "Hours to an answer"),
        ("mean_hours", "Mean hours to an answer", "snapshot", "Hours to an answer"),
        ("p90_hours", "90th percentile hours to an answer", "snapshot", "Hours to an answer"),
        ("fastest_hours", "Fastest hours to an answer", "snapshot", "Hours to an answer"),
    ]


def test_trends_hold_the_four_hours_and_the_count_from_the_golden_rows(tmp_path: Path) -> None:
    (section,) = github_response_times.summary(CONFIG)
    golden = scenarios.load_golden(NAME)["hours_to_the_maintainers_first_reply"]["rows"]
    for file, text in golden.items():
        (tmp_path / file).write_text(text, encoding="utf-8")

    (built,) = trends.build(files_in(tmp_path), date(2026, 9, 24), [section], "health.jsonl")["sections"]

    assert [(f["field"], f["values"], f.get("chart")) for f in built["fields"]] == [
        ("median_hours", [17.5], "Hours to an answer"),
        ("never_answered", [3], None),
        ("mean_hours", [33.9], "Hours to an answer"),
        ("p90_hours", [100.0], "Hours to an answer"),
        ("fastest_hours", [0.5], "Hours to an answer"),
    ]


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(github_response_times, CONFIG, "hours_to_the_maintainers_first_reply", tmp_path) == [
        "",
        "### github_response_times: solentlabs/example",
        "",
        "- Median hours to an answer: 17.5",
        "- Not answered: 3",
    ]


# --- recomputed history (ADR: derived history follows today's rules) ---


def test_every_past_day_is_recomputed_each_change_a_correction_and_a_second_run_changes_nothing(tmp_path: Path) -> None:
    """The scenario's reviewed rows hold the values: the row written under the earlier rule is corrected, recorded for
    health, and a run after it finds nothing to change."""
    scenario = port.scenario(NAME, "past_days_are_recomputed_back_to_the_first_community_issue")

    first = port.run(github_response_times, CONFIG, scenario, tmp_path / "first")
    again = port.run(github_response_times, CONFIG, replace(scenario, seed=first.rows), tmp_path / "again")

    assert f"Recomputed {FILE}: 1 corrected, 2 added, 1 failure row(s) replaced" in first.log
    old = {"answered": 1, "never_answered": 1, "median_hours": 24.0, "mean_hours": 24.0, "fastest_hours": 24.0}
    new = {"answered": 2, "never_answered": 0, "median_hours": 18.0, "mean_hours": 18.0, "fastest_hours": 12.0}
    assert first.ctx.corrections == [Correction(NAME, FILE, "2026-09-20", old, new)]
    assert again.rows == first.rows
    assert again.ctx.corrections == []
    assert not [line for line in again.log if line.startswith("Recomputed")]


def test_a_new_file_gets_todays_row_and_its_history_on_the_first_run_of_a_later_day(tmp_path: Path) -> None:
    """The first-use story (docs/CONFIGURATION.md, "History on first use"): the first run on empty data writes only
    that day's row, a second run the same day replaces it and adds nothing, and the next day's run backfills."""
    base = replace(port.scenario(NAME, "past_days_are_recomputed_back_to_the_first_community_issue"), seed={})
    one = replace(base, responses=[grt.page(grt.LATE_ISSUES)])
    same_day = replace(base, responses=[grt.page(grt.LATE_ISSUES)])
    next_day = replace(base, responses=[grt.page(grt.LATE_ISSUES)], now="2026-09-24T23:31:05")

    first = port.run(github_response_times, CONFIG, one, tmp_path / "a")
    again = port.run(github_response_times, CONFIG, replace(same_day, seed=first.rows), tmp_path / "b")
    later = port.run(github_response_times, CONFIG, replace(next_day, seed=again.rows), tmp_path / "c")

    def days(run: Any) -> list[str]:
        return [json.loads(line)["date"] for line in run.rows[FILE].splitlines()]

    assert days(first) == days(again) == ["2026-09-23"]
    assert days(later) == [f"2026-09-{d}" for d in range(18, 24)] + ["2026-09-24"]
    assert '"backfilled": true' not in again.rows[FILE]
    assert later.rows[FILE].count('"backfilled": true') == 5
