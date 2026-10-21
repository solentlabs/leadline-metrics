"""github_discussions: the script's rows on its scenarios, and leadline's side of each decided divergence.

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
from leadline.sources.github import github_discussions
from leadline.sources.github.github_discussions.tests import scenarios as gd
from leadline.sources.github.tests.builders import (
    GITHUB_API,
    GITHUB_GRAPHQL,
    github_discussion,
    github_repo,
    graphql_body,
)
from leadline.sources.github.tests.builders import github_discussions as discussions_body

NAME = "github_discussions"
CONFIG = gd.LEADLINE_CONFIG
FILE = gd.FILE
REPO = "solentlabs/example"
EARLIER = gd.EARLIER[FILE]
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert github_discussions.NAME == NAME
    assert github_discussions.ENV == ("GITHUB_TOKEN",)
    assert github_discussions.LAGGING is False


def test_a_valid_config_has_no_problems() -> None:
    cfg = [{"repo": "a/b", "maintainer": "m"}, {"repo": "a/c", "maintainer": "m", "file": "x-{repo_name}.jsonl"}]

    assert github_discussions.validate(cfg) == []


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"repo": "a/b", "maintainer": "m"}, [": must be a list of entries"]),
        (["a/b"], ["[0]: must be an object"]),
        ([{"maintainer": "m"}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a", "maintainer": "m"}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a/b"}], ["[0].maintainer: must be a login"]),
        ([{"repo": "a/b", "maintainer": ""}], ["[0].maintainer: must be a login"]),
        ([{"repo": "a/b", "maintainer": 5}], ["[0].maintainer: must be a login"]),
        ([{}], ["[0].repo: must be owner/name", "[0].maintainer: must be a login"]),
        (
            [{"repo": "a/b", "maintainer": "m", "file": "x-{label}.jsonl"}],
            ["[0].file: 'x-{label}.jsonl' uses {label}, which is not one of: repo, repo_name"],
        ),
    ],
    ids=[
        "not a list",
        "an entry not an object",
        "no repo",
        "a repo without its owner",
        "no maintainer",
        "an empty maintainer",
        "a maintainer not a string",
        "neither",
        "a bad file",
    ],
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert github_discussions.validate(cfg) == problems


def test_files_are_the_default_or_the_template_of_each_entry() -> None:
    cfg = [{"repo": "a/one", "maintainer": "m"}, {"repo": "a/two", "maintainer": "m", "file": "d-{repo_name}.jsonl"}]

    assert github_discussions.files(cfg) == {"[0]": "github-discussions-one.jsonl", "[1]": "d-two.jsonl"}


def test_it_is_skipped_without_a_token(tmp_path: Path) -> None:
    done = port.run(github_discussions, CONFIG, port.scenario(NAME, "skips_without_a_token"), tmp_path)

    assert "SKIP: github_discussions (GITHUB_TOKEN not set)" in done.log
    assert (done.status, done.rows) == (0, {})


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    done = port.run(github_discussions, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == [
        "a_null_repository_writes_nothing",
        "an_empty_maintainer_login_makes_everyone_community",
        "failed_fetch_writes_nothing",
        "graphql_errors_write_nothing",
        "unparseable_body_writes_nothing",
    ]


def row(tmp_path: Path) -> dict[str, Any]:
    written: dict[str, Any] = json.loads((tmp_path / FILE).read_text(encoding="utf-8").splitlines()[-1])
    return written


def test_the_ten_most_recent_are_listed(tmp_path: Path) -> None:
    port.run(github_discussions, CONFIG, port.scenario(NAME, "who_opened_what_and_what_was_answered"), tmp_path)

    written = row(tmp_path)
    assert written["fetched"] == 12
    assert [d["number"] for d in written["recent"]] == list(range(1, 11))


def test_a_null_category_name_is_counted_under_null(tmp_path: Path) -> None:
    """Pinned: the script counts it under the key None, which JSON writes as "null"."""
    port.run(github_discussions, CONFIG, port.scenario(NAME, "missing_authors_categories_and_counts"), tmp_path)

    assert row(tmp_path)["categories"] == {"Q&A": 2, "uncategorised": 1, "null": 1}


# --- rebuilt history (ADR: past values a source can rebuild are filled, never changed) ---

LATE_SCENARIO = "a_file_starting_late_gets_rows_back_to_the_first_discussion"


def test_the_days_before_the_file_are_rebuilt_once_and_said_so(tmp_path: Path) -> None:
    """The scenario's reviewed rows hold the values; a second run finds nothing missing, so asks for no comments."""
    scenario = port.scenario(NAME, LATE_SCENARIO)

    first = port.run(github_discussions, CONFIG, scenario, tmp_path / "first")
    once = replace(scenario, seed=first.rows, responses=scenario.responses[:1])
    again = port.run(github_discussions, CONFIG, once, tmp_path / "again")

    assert f"Backfilled {FILE}: 0 row(s) filled, 3 added, 2026-09-18 to 2026-09-20" in first.log
    assert first.ctx.corrections == []  # a backfill, not a correction
    assert again.rows == first.rows
    assert not [line for line in again.log if line.startswith("Backfilled")]


def test_a_new_file_gets_every_day_back_to_the_first_discussion_the_night_after_its_first_row(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, LATE_SCENARIO)
    first = port.run(github_discussions, CONFIG, replace(scenario, seed={}, responses=scenario.responses[:1]), tmp_path)
    assert first.rows[FILE].count("\n") == 1  # no history on the first night

    seed = {FILE: first.rows[FILE].replace("2026-09-23", "2026-09-22")}
    done = port.run(github_discussions, CONFIG, replace(scenario, seed=seed), tmp_path / "next")

    rows = [json.loads(line) for line in done.rows[FILE].splitlines()]
    dates = [f"2026-09-{day}" for day in range(18, 24)]
    assert [(r["date"], r["total"], r["total_comments"], r.get("backfilled")) for r in rows] == [
        *[(day, total, comments, True) for day, total, comments in zip(dates, (1, 2, 2, 3), (0, 1, 2, 2))],
        ("2026-09-22", 4, 2, None),
        ("2026-09-23", 4, 2, None),
    ]


def test_with_more_comments_than_the_query_reads_the_rebuilt_rows_leave_them_null(tmp_path: Path) -> None:
    """The comments query reads a discussion's first 100: with more, a past day's count can't be told."""
    scenario = port.scenario(NAME, LATE_SCENARIO)
    capped = [scenario.responses[0], gd.comments_answer(gd.FOUR_COMMENTS, read={2: 150})]

    done = port.run(github_discussions, CONFIG, replace(scenario, responses=capped), tmp_path)

    assert done.rows[FILE] == gd.WITHOUT_COMMENTS + gd.LATE + gd.four_today(4)
    assert f"Not rebuilding past comments for {REPO}: the query read 2 of discussion #2's 150" in done.log


def test_a_failed_comments_query_leaves_them_null_and_fails_nothing(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, LATE_SCENARIO)
    sent = graphql_body(gd.COMMENTS_QUERY, {"o": "solentlabs", "n": "example"})
    broken = [scenario.responses[0], exchange(GITHUB_GRAPHQL, "", method="POST", sent=sent, status=502)]

    done = port.run(github_discussions, CONFIG, replace(scenario, responses=broken), tmp_path)

    assert done.rows[FILE] == gd.WITHOUT_COMMENTS + gd.LATE + gd.four_today(4)
    assert f"Not rebuilding past comments for {REPO}: 502" in done.log
    assert (done.status, done.ctx.critical) == (0, [])


def test_with_more_discussions_than_the_query_reads_the_log_says_why_nothing_is_rebuilt(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "with_more_discussions_than_the_query_reads_nothing_is_rebuilt")

    done = port.run(github_discussions, CONFIG, scenario, tmp_path)

    assert f"Not rebuilding past discussions for {REPO}: the query read 4 of 150" in done.log


def test_a_discussion_without_its_creation_time_rebuilds_nothing(tmp_path: Path) -> None:
    """The scenario's first two discussions have no createdAt, so no day can be counted (its golden: no rows added)."""
    done = port.run(github_discussions, CONFIG, port.scenario(NAME, "missing_authors_categories_and_counts"), tmp_path)

    assert f"Not rebuilding past discussions for {REPO}: discussion #1 has no creation time" in done.log


# --- decided divergences ---


def failure_row(status: str, reason: str, http_code: str | None = None) -> str:
    code = f', "http_code": "{http_code}"' if http_code is not None else ""
    return f'{{"date": "2026-09-23", "status": "{status}"{code}, "reason": "{reason}", "captured_at": "{NOW}"}}\n'


def test_an_empty_maintainer_is_a_config_error() -> None:
    """CONFIGURATION: an entry's maintainer is required, so the script's empty login can't be configured."""
    scenario = port.scenario(NAME, "an_empty_maintainer_login_makes_everyone_community")
    assert scenario.config == {"MAINTAINER_LOGIN": ""}

    assert github_discussions.validate([{"repo": REPO, "maintainer": ""}]) == ["[0].maintainer: must be a login"]


def test_graphql_errors_are_a_graphql_error_row(tmp_path: Path) -> None:
    """The scenario's error is NOT_FOUND, the repo not there: waiting can't fix it."""
    done = port.run(github_discussions, CONFIG, port.scenario(NAME, "graphql_errors_write_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("graphql_error", "Could not resolve to a Repository")}
    reason = f"{REPO}: GitHub's GraphQL API answered: Could not resolve to a Repository"
    assert done.ctx.critical == [Critical(NAME, FILE, reason)]
    assert done.status == 1


def test_a_null_repository_is_a_parse_failed_row(tmp_path: Path) -> None:
    done = port.run(github_discussions, CONFIG, port.scenario(NAME, "a_null_repository_writes_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "discussions response had an unexpected shape")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_an_unparseable_body_is_a_parse_failed_row(tmp_path: Path) -> None:
    done = port.run(github_discussions, CONFIG, port.scenario(NAME, "unparseable_body_writes_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "discussions response was not JSON")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_failed_fetch_is_a_fetch_failed_row(tmp_path: Path) -> None:
    done = port.run(github_discussions, CONFIG, port.scenario(NAME, "failed_fetch_writes_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "discussions request failed", "502")}
    assert (done.status, done.ctx.critical, done.sleeps) == (0, [], [])  # recoverable, one attempt


GOOD = [github_discussion(1)]


@pytest.mark.parametrize(
    "discussions",
    [
        {"totalCount": 1, "nodes": {}},
        {"totalCount": "1", "nodes": GOOD},
        {"totalCount": 1, "nodes": [None]},
        {"totalCount": 1, "nodes": [{**GOOD[0], "author": "example-user"}]},
        {"totalCount": 1, "nodes": [{**GOOD[0], "author": {"login": 5}}]},
        {"totalCount": 1, "nodes": [{**GOOD[0], "category": "Q&A"}]},
        {"totalCount": 1, "nodes": [{**GOOD[0], "category": {"name": 5}}]},
        {"totalCount": 1, "nodes": [{**GOOD[0], "comments": {"totalCount": None}}]},
        {"totalCount": 1, "nodes": [{**GOOD[0], "isAnswered": "yes"}]},
        {"totalCount": 1, "nodes": [{k: v for k, v in GOOD[0].items() if k != "number"}]},
        {"totalCount": 1, "nodes": [{**GOOD[0], "title": None}]},
        {"totalCount": 1, "nodes": [{**GOOD[0], "upvoteCount": True}]},
        {"totalCount": 1, "nodes": [{**GOOD[0], "updatedAt": None}]},
    ],
    ids=[
        "nodes an object",
        "a total not a count",
        "a null node",
        "an author not an object",
        "a login not a string",
        "a category not an object",
        "a category name not a string",
        "a null comment count",
        "answered not a boolean",
        "no number",
        "no title",
        "upvotes not a count",
        "a null update time",
    ],
)
def test_discussions_it_cant_read_are_a_parse_failed_row(tmp_path: Path, discussions: Any) -> None:
    """ADR: a failure is recorded as a failure; an answer is read whole."""
    body = {"data": {"repository": {"discussions": discussions}}}
    scenario = replace(port.scenario(NAME, "a_null_repository_writes_nothing"), responses=[gd.answer(body)])

    done = port.run(github_discussions, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "discussions response had an unexpected shape")}


@pytest.mark.parametrize(
    "body",
    [None, [], {"data": None}, {"data": {"repository": {"discussions": None}}}],
    ids=["null", "a list", "null data without errors", "null discussions"],
)
def test_answers_without_the_discussions_are_a_parse_failed_row(tmp_path: Path, body: Any) -> None:
    scenario = replace(port.scenario(NAME, "a_null_repository_writes_nothing"), responses=[gd.answer(body)])

    done = port.run(github_discussions, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "discussions response had an unexpected shape")}


def test_a_failure_keeps_a_good_row(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data."""
    good = '{"date": "2026-09-23", "total": 3}\n'
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), seed={FILE: EARLIER + good})

    done = port.run(github_discussions, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + good}
    assert f"WARN: kept the good row for 2026-09-23 in {FILE}; fetch_failed not recorded" in done.log


def test_a_good_row_replaces_a_failure_row(tmp_path: Path) -> None:
    failure = '{"date": "2026-09-23", "status": "fetch_failed", "http_code": "502"}\n'
    scenario = replace(port.scenario(NAME, "no_discussions"), seed={FILE: failure})

    port.run(github_discussions, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, "no_discussions", tmp_path) == []


# --- failures: recoverable or not ---


@pytest.mark.parametrize("code", [401, 403, 404])
def test_an_error_waiting_cant_fix_is_critical_at_once(tmp_path: Path, code: int) -> None:
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=[gd.answer("", status=code)])

    done = port.run(github_discussions, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "discussions request failed", str(code))}
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP {code} from GitHub's GraphQL API")]
    assert done.status == 1


@pytest.mark.parametrize(
    ("code", "headers"),
    [(403, {"X-RateLimit-Remaining": "0"}), (403, {"Retry-After": "60"}), (429, {})],
    ids=["403 none remaining", "403 retry-after", "429"],
)
def test_an_http_rate_limit_is_recoverable(tmp_path: Path, code: int, headers: dict[str, str]) -> None:
    limited = exchange(GITHUB_GRAPHQL, "", method="POST", sent=gd.SENT, status=code, headers=headers)
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=[limited])

    done = port.run(github_discussions, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "discussions request failed", str(code))}
    assert (done.status, done.ctx.critical) == (0, [])


@pytest.mark.parametrize(
    "error",
    [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}, {"message": "Something went wrong"}],
    ids=["a graphql rate limit", "an error without a type"],
)
def test_a_graphql_error_waiting_can_fix_is_recoverable(tmp_path: Path, error: dict[str, str]) -> None:
    body = {"data": None, "errors": [error]}
    scenario = replace(port.scenario(NAME, "graphql_errors_write_nothing"), responses=[gd.answer(body)])

    done = port.run(github_discussions, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("graphql_error", error["message"])}
    assert (done.status, done.ctx.critical) == (0, [])


def test_no_connection_is_recorded_as_000_and_is_recoverable(tmp_path: Path) -> None:
    scenario = replace(
        port.scenario(NAME, "failed_fetch_writes_nothing"),
        responses=[failed(GITHUB_GRAPHQL, method="POST", sent=gd.SENT)],
    )

    done = port.run(github_discussions, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "discussions request failed", "000")}
    assert done.status == 0


def test_a_failing_entry_does_not_stop_the_next(tmp_path: Path) -> None:
    other = {"repo": "a/other", "maintainer": gd.MAINTAINER}
    sent = graphql_body(gd.QUERY, {"o": "a", "n": "other"})
    answered = exchange(GITHUB_GRAPHQL, discussions_body([]), method="POST", sent=sent)
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=[gd.answer("", 502), answered])

    done = port.run(github_discussions, [*CONFIG, other], scenario, tmp_path)

    assert done.rows[FILE] == EARLIER + failure_row("fetch_failed", "discussions request failed", "502")
    assert '"total": 0' in done.rows["github-discussions-other.jsonl"]


# --- check() ---

REPO_URL = f"{GITHUB_API}/repos/{REPO}"
CHECK_SENT = graphql_body(
    "query($o:String!,$n:String!,$m:String!){repository(owner:$o,name:$n){discussions(first:1){totalCount}} "
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
    with port.credentials(scenarios.CREDENTIALS, github_discussions.ENV):
        findings = github_discussions.check(ctx, CONFIG)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


def test_check_finds_the_repo_and_queries_its_discussions(tmp_path: Path) -> None:
    found = check(
        tmp_path,
        exchange(REPO_URL, github_repo(REPO)),
        query_answer(
            {"data": {"repository": {"discussions": {"totalCount": 3}}, "user": {"login": "example-maintainer"}}}
        ),
    )

    assert found == [
        Finding("ok", f"{REPO}: the repo exists"),
        Finding("ok", f"{REPO}: the token can query its discussions"),
        MAINTAINER_EXISTS,
    ]


def test_check_finds_a_missing_maintainer_login_in_the_same_query(tmp_path: Path) -> None:
    """A mistyped login would misread every night, with no failure to show it."""
    answer = {"data": {"repository": {"discussions": {"totalCount": 3}}, "user": None}, "errors": [NO_SUCH_USER]}

    found = check(tmp_path, exchange(REPO_URL, github_repo(REPO)), query_answer(answer))

    missing = f'{REPO}: the maintainer login "example-maintainer" doesn\'t exist, so '
    missing += "every discussion would count as the community's"
    assert found[-1] == Finding("error", missing)


def test_check_stops_at_a_missing_repo(tmp_path: Path) -> None:
    found = check(tmp_path, exchange(REPO_URL, "", status=404))

    assert found == [Finding("error", f"{REPO}: HTTP 404: the repo doesn't exist, or the token can't read it")]


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (
            query_answer({"data": None, "errors": [{"type": "FORBIDDEN", "message": "Resource not accessible"}]}),
            Finding("error", f"{REPO}: could not query its discussions: Resource not accessible"),
        ),
        (
            query_answer({"data": None, "errors": [{"type": "RATE_LIMITED", "message": "rate limit exceeded"}]}),
            Finding("warn", f"{REPO}: could not query its discussions: rate limit exceeded"),
        ),
        (query_answer("", status=502), Finding("warn", f"{REPO}: could not fetch its discussions: HTTP 502")),
    ],
    ids=["refused", "rate limited", "unavailable"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(tmp_path: Path, response: Any, finding: Finding) -> None:
    assert check(tmp_path, exchange(REPO_URL, github_repo(REPO)), response)[-1] == finding


# --- the summary ---


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(github_discussions, CONFIG, "who_opened_what_and_what_was_answered", tmp_path) == [
        "",
        "### github_discussions: solentlabs/example",
        "",
        "- Discussion threads: 30",
        "- Comments on discussions: 23",
    ]
