"""github_repo_activity: the script's rows on its scenarios, and leadline's side of each decided divergence.

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
from leadline.sources.github import github_repo_activity as source
from leadline.sources.github.github_repo_activity.tests import scenarios as gra
from leadline.sources.github.tests.builders import GITHUB_API, github_issue, github_release, github_repo

NAME = "github_repo_activity"
CONFIG = gra.LEADLINE_CONFIG
FILE = gra.FILE
REPO = gra.PROJECT
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it
COMMITS_URL = gra.commits([])[0]["request"]["url"]
# The weekly_snapshot row's values, in the script's order.
WEEK: dict[str, Any] = {
    "repo": REPO,
    "stars": 900,
    "forks": 40,
    "open_issues": 12,
    "codebase_bytes": 160900,
    "languages": {"Python": 120000, "JavaScript": 40000, "Dockerfile": 900},
    "velocity_7d": {"commits": 8, "authors": 2, "fixes": 4, "fix_ratio": 50.0, "redesigns": 2},
    "issues_7d": {"total": 3, "external": 2},
    "contributors": {"total": 10, "top_contributor": "contributor-01", "top_contributor_pct": 34.2},
    "latest_release": {"tag": "v3.9.0", "date": "2026-09-20"},
}


def row(**fields: Any) -> str:
    """A row for the run's date as leadline writes it: the label, then `fields`, then captured_at."""
    return json.dumps({"date": "2026-09-23", "label": gra.LABEL, **fields, "captured_at": NOW}) + "\n"


def partial_row(reason: str, **nulls: Any) -> str:
    """A partial row: WEEK with `nulls` set. The languages are one part with the code size, so null together."""
    if "codebase_bytes" in nulls and nulls["codebase_bytes"] is None:
        nulls = {**nulls, "languages": None}
    return row(**{**WEEK, **nulls}, status="partial", reason=reason)


def failure_row(status: str, reason: str, http_code: str | None = None) -> str:
    code = {"http_code": http_code} if http_code is not None else {}
    return row(repo=REPO, status=status, **code, reason=reason)


def week(**answers: Any) -> list[Any]:
    """weekly_snapshot's responses, with the request for each URL given in `answers` answered so instead."""
    responses = [gra.REPOSITORY, *gra.commits(gra.WEEK), *gra.rest(gra.ISSUES, gra.CONTRIBUTORS, gra.RELEASES)]
    return [answers.get(e["request"]["url"], e) for e in responses]


def run(tmp_path: Path, responses: list[Any], seed: str = gra.EARLIER, cfg: Any = CONFIG) -> port.PortRun:
    scenario = replace(port.scenario(NAME, "weekly_snapshot"), responses=responses, seed={FILE: seed})
    return port.run(source, cfg, scenario, tmp_path)


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert source.NAME == NAME
    assert source.ENV == ("GITHUB_TOKEN",)
    assert source.LAGGING is False


def test_a_valid_config_has_no_problems() -> None:
    cfg = [
        {"repo": "a/b"},
        {"repo": "a/c", "label": "c", "fix_pattern": r"^fix\b", "redesign_pattern": "rework"},
        {"repo": "a/d", "file": "x-{label}.jsonl"},
    ]

    assert source.validate(cfg) == []


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"repo": "a/b"}, [": must be a list of entries"]),
        (["a/b"], ["[0]: must be an object"]),
        ([{}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a"}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a/b", "label": ""}], ["[0].label: must be a non-empty string"]),
        ([{"repo": "a/b", "label": 5}], ["[0].label: must be a non-empty string"]),
        ([{"repo": "a/b", "fix_pattern": 5}], ["[0].fix_pattern: must be a regex"]),
        (
            [{"repo": "a/b", "redesign_pattern": "*polish"}],
            ["[0].redesign_pattern: must be a regex: nothing to repeat at position 0"],
        ),
        (
            [{"repo": "a/b", "label": "x", "fix_pattern": "[", "redesign_pattern": ""}],
            ["[0].fix_pattern: must be a regex: unterminated character set at position 0"],
        ),
        (
            [{"repo": "a/b", "file": "x-{site}.jsonl"}],
            ["[0].file: 'x-{site}.jsonl' uses {site}, which is not one of: label, repo, repo_name"],
        ),
        (
            [{"repo": "a/b", "label": "x/y"}],
            ["[0].file: 'github-repo-activity-{label}.jsonl' must fill to a plain *.jsonl name, "
             "not 'github-repo-activity-x/y.jsonl'"],
        ),
    ],
    ids=["not a list", "an entry not an object", "no repo", "a repo without its owner", "an empty label",
         "a label not a string", "a fix pattern not a string", "a redesign pattern that isn't a regex",
         "a fix pattern that isn't a regex", "a bad file", "a label the file can't hold"],
)  # fmt: skip
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert source.validate(cfg) == problems


def test_files_are_named_for_the_label_the_repo_name_by_default() -> None:
    cfg = [{"repo": "a/one"}, {"repo": "a/two", "label": "second"}, {"repo": "a/three", "file": "x-{repo_name}.jsonl"}]

    assert source.files(cfg) == {
        "[0]": "github-repo-activity-one.jsonl",
        "[1]": "github-repo-activity-second.jsonl",
        "[2]": "x-three.jsonl",
    }


def test_it_is_skipped_without_a_token(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "skips_without_a_token"), tmp_path)

    assert "SKIP: github_repo_activity (GITHUB_TOKEN not set)" in done.log
    assert (done.status, done.rows) == (0, {})


def test_it_sends_the_token_and_asks_for_the_github_json(tmp_path: Path) -> None:
    sent: list[Any] = []
    transport = SavedResponses(week())

    def recording(request: Any, timeout: float) -> Any:
        sent.append(request)
        return transport(request, timeout)

    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    ctx.http._transport = recording  # to see the request headers
    with port.credentials({"GITHUB_TOKEN": "t0ken"}, source.ENV):
        source.collect(ctx, CONFIG)

    assert {r.get_header("Authorization") for r in sent} == {"Bearer t0ken"}
    assert {r.get_header("Accept") for r in sent} == {"application/vnd.github+json"}


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    done = port.run(source, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


@pytest.mark.parametrize(
    "scenario",
    [s for s in scenarios.scenarios(NAME) if port.divergences(NAME).get(s.name) == gra.MORE_DATA],
    ids=lambda s: s.name,
)
def test_the_rows_are_the_scripts_with_the_languages_added(scenario: Any, tmp_path: Path) -> None:
    """More data is captured than the script did: `languages` beside the code size, which is their sum."""
    done = port.run(source, CONFIG, scenario, tmp_path)

    assert [d for d in port.golden_differences(NAME, scenario.name, tmp_path) if "languages: only in B" not in d] == []
    written = json.loads(done.rows[FILE].splitlines()[-1])
    assert written["languages"] == {"Python": 120000, "JavaScript": 40000, "Dockerfile": 900}
    assert written["codebase_bytes"] == sum(written["languages"].values())
    assert list(written)[list(written).index("codebase_bytes") + 1] == "languages"
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == [
        "a_failed_commits_page_writes_nothing",
        "an_empty_list_body_writes_nothing",
        "an_issue_with_a_null_user_writes_nothing",
        "an_unparseable_list_body_writes_nothing",
        "an_unpublished_latest_release_has_no_date",
        "failed_optional_lists_count_as_none",
        "failed_repository_writes_nothing",
        "pages_through_the_commits",
        "weekly_snapshot",
    ]


def test_the_label_is_the_repo_name_by_default(tmp_path: Path) -> None:
    scenario = replace(port.scenario(NAME, "weekly_snapshot"), seed={})

    done = port.run(source, [{"repo": REPO}], scenario, tmp_path)

    assert done.rows["github-repo-activity-example-project.jsonl"].startswith(
        '{"date": "2026-09-23", "label": "example-project", "repo": "example-org/example-project", '
    )


def test_configured_patterns_count_fixes_and_redesigns_ignoring_case(tmp_path: Path) -> None:
    cfg = [{**CONFIG[0], "fix_pattern": r"^fix\b", "redesign_pattern": "^ADD"}]

    done = run(tmp_path, week(), seed="")

    velocity = json.loads(done.rows[FILE])["velocity_7d"]
    assert (velocity["fixes"], velocity["redesigns"]) == (4, 2)  # the script's: Debug and Revert are fixes
    done = run(tmp_path / "configured", week(), seed="", cfg=cfg)

    velocity = json.loads(done.rows[FILE])["velocity_7d"]
    assert (velocity["fixes"], velocity["fix_ratio"], velocity["redesigns"]) == (2, 25.0, 1)


# --- decided divergences: the repository and the commits are required (ADR: a failure is recorded as a failure) ---


def test_a_failed_repository_is_a_fetch_failed_row(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "failed_repository_writes_nothing"), tmp_path)

    assert done.rows == {FILE: gra.EARLIER + failure_row("fetch_failed", "repository request failed", "000")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_failed_commits_page_is_a_fetch_failed_row(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "a_failed_commits_page_writes_nothing"), tmp_path)

    assert done.rows == {FILE: gra.EARLIER + failure_row("fetch_failed", "commits request failed", "502")}
    assert (done.status, done.ctx.critical) == (0, [])


@pytest.mark.parametrize(
    ("url", "kind"), [(gra.REPO_URL, "repository"), (COMMITS_URL, "commits")], ids=["repository", "commits"]
)
@pytest.mark.parametrize("code", [401, 403, 404])
def test_a_required_error_waiting_cant_fix_is_critical_at_once(tmp_path: Path, code: int, url: str, kind: str) -> None:
    responses = [exchange(url, "", status=code) if e["request"]["url"] == url else e for e in week()]
    responses = responses[: [e["request"]["url"] for e in responses].index(url) + 1]  # nothing asked after it

    done = run(tmp_path, responses)

    assert done.rows == {FILE: gra.EARLIER + failure_row("fetch_failed", f"{kind} request failed", str(code))}
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP {code} from GitHub's {kind} API")]
    assert done.status == 1


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("<html>maintenance</html>", "repository response was not JSON"),
        ([], "repository response had an unexpected shape"),
        ({**github_repo(REPO), "owner": None}, "repository response had an unexpected shape"),
        ({**github_repo(REPO), "stargazers_count": None}, "repository response had an unexpected shape"),
        ({**github_repo(REPO), "full_name": 5}, "repository response had an unexpected shape"),
    ],
    ids=["not JSON", "a list", "a null owner", "a null count", "a name not a string"],
)
def test_a_repository_it_cant_use_is_a_parse_failed_row(tmp_path: Path, body: Any, reason: str) -> None:
    done = run(tmp_path, week(**{gra.REPO_URL: exchange(gra.REPO_URL, body)}))  # every list still asked for

    assert done.rows == {FILE: gra.EARLIER + failure_row("parse_failed", reason)}
    assert (done.status, done.ctx.critical) == (0, [])


def commit(message: Any, author: Any) -> dict[str, Any]:
    return {"commit": {"message": message}, "author": author}


@pytest.mark.parametrize(
    ("page", "reason"),
    [
        ("", "commits response was not JSON"),
        ({}, "commits response had an unexpected shape"),
        ([5], "commits response had an unexpected shape"),
        ([{"author": None}], "commits response had an unexpected shape"),
        ([commit(None, None)], "commits response had an unexpected shape"),
        ([commit("Fix", "a-login")], "commits response had an unexpected shape"),
        ([commit("Fix", {"login": 5})], "commits response had an unexpected shape"),
    ],
    ids=["an empty body", "an object", "a commit not an object", "no commit", "no message", "an author not an object",
         "a login not a string"],
)  # fmt: skip
def test_commits_it_cant_use_are_a_parse_failed_row(tmp_path: Path, page: Any, reason: str) -> None:
    done = run(tmp_path, week(**{COMMITS_URL: exchange(COMMITS_URL, page)}))

    assert done.rows == {FILE: gra.EARLIER + failure_row("parse_failed", reason)}


def test_every_commits_page_is_asked_for_before_any_is_read(tmp_path: Path) -> None:
    """As the script does: a page it can't read still has its next page asked for."""
    pages = gra.commits(gra.MANY)
    pages[0] = {**pages[0], "response": {**pages[0]["response"], "body": "<html>Oops</html>"}}

    done = run(tmp_path, [gra.REPOSITORY, *pages, *gra.rest(gra.ISSUES, gra.CONTRIBUTORS, gra.RELEASES)])

    assert done.rows == {FILE: gra.EARLIER + failure_row("parse_failed", "commits response was not JSON")}


def test_commit_authors_without_a_login_are_not_counted(tmp_path: Path) -> None:
    page = [
        commit("Fix a", None),
        commit("Add b", {}),
        commit("Add c", {"login": None}),
        commit("Add d", {"login": "x"}),
    ]

    done = run(tmp_path, week(**{COMMITS_URL: exchange(COMMITS_URL, page)}))

    velocity = json.loads(done.rows[FILE].splitlines()[-1])["velocity_7d"]
    assert velocity == {"commits": 4, "authors": 1, "fixes": 1, "fix_ratio": 25.0, "redesigns": 0}


def test_a_failure_keeps_a_good_row(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data."""
    good = row(**WEEK)

    done = run(tmp_path, [failed(gra.REPO_URL)], seed=gra.EARLIER + good)

    assert done.rows == {FILE: gra.EARLIER + good}
    assert f"WARN: kept the good row for 2026-09-23 in {FILE}; fetch_failed not recorded" in done.log


# --- decided divergences: the lists are optional (ADR: a partly failed row keeps its good parts) ---


def test_an_issue_with_a_null_user_leaves_the_issues_null_in_a_partial_row(tmp_path: Path) -> None:
    """The issues are read whole or not at all: counting the issue either way would guess its author."""
    done = port.run(source, CONFIG, port.scenario(NAME, "an_issue_with_a_null_user_writes_nothing"), tmp_path)

    reason = "issues response had an unexpected shape"
    assert done.rows == {FILE: gra.EARLIER + partial_row(reason, issues_7d=None)}
    assert (done.status, done.ctx.critical) == (0, [])


def test_an_empty_list_body_is_null_in_a_partial_row(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "an_empty_list_body_writes_nothing"), tmp_path)

    assert done.rows == {FILE: gra.EARLIER + partial_row("issues response was not JSON", issues_7d=None)}
    assert (done.status, done.ctx.critical) == (0, [])


def test_an_unparseable_list_body_is_null_in_a_partial_row(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "an_unparseable_list_body_writes_nothing"), tmp_path)

    assert done.rows == {FILE: gra.EARLIER + partial_row("languages response was not JSON", codebase_bytes=None)}
    assert (done.status, done.ctx.critical) == (0, [])


def test_failed_lists_are_null_in_a_partial_row_never_none(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "failed_optional_lists_count_as_none"), tmp_path)

    reason = (
        "issues request failed (HTTP 500); contributors request failed (HTTP 500); "
        "releases request failed (HTTP 404); languages request failed (HTTP 000)"
    )
    velocity = {"commits": 0, "authors": 0, "fixes": 0, "fix_ratio": 0.0, "redesigns": 0}
    nulls = {"codebase_bytes": None, "issues_7d": None, "contributors": None, "latest_release": None}
    assert done.rows == {FILE: partial_row(reason, velocity_7d=velocity, **nulls)}
    # A 404 is an error waiting can't fix, on an optional list too.
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP 404 from GitHub's releases API")]
    assert done.status == 1


@pytest.mark.parametrize(
    ("url", "body", "field", "reason"),
    [
        (gra.ISSUES_URL, {}, "issues_7d", "issues"),
        (gra.ISSUES_URL, [5], "issues_7d", "issues"),
        (gra.ISSUES_URL, [{**github_issue(REPO, 9, author="x"), "user": "x"}], "issues_7d", "issues"),
        (gra.CONTRIBUTORS_URL, {"message": "x"}, "contributors", "contributors"),
        (gra.CONTRIBUTORS_URL, [{"login": "x", "contributions": "9"}], "contributors", "contributors"),
        (gra.CONTRIBUTORS_URL, [{"login": 5, "contributions": 9}], "contributors", "contributors"),
        (gra.RELEASES_URL, [None], "latest_release", "releases"),
        (gra.RELEASES_URL, [{"tag_name": "v1", "published_at": 5}], "latest_release", "releases"),
        (gra.RELEASES_URL, [{"tag_name": None}], "latest_release", "releases"),
        (gra.LANGUAGES_URL, [], "codebase_bytes", "languages"),
        (gra.LANGUAGES_URL, {"Python": "9"}, "codebase_bytes", "languages"),
    ],
    ids=["issues an object", "an issue not an object", "an issue's user not an object", "contributors an object",
         "contributions not a number", "a login not a string", "a release not an object",
         "a release date not a string", "a tag not a string", "languages a list", "bytes not a number"],
)  # fmt: skip
def test_a_list_it_cant_use_is_null_in_a_partial_row(
    tmp_path: Path, url: str, body: Any, field: str, reason: str
) -> None:
    done = run(tmp_path, week(**{url: exchange(url, body)}))

    assert done.rows == {FILE: gra.EARLIER + partial_row(f"{reason} response had an unexpected shape", **{field: None})}


def test_a_pull_request_with_a_null_user_is_not_read(tmp_path: Path) -> None:
    """As in the script, pull requests are left out before any user is read."""
    pull = {**github_issue(REPO, 9, author="x", pull_request=True), "user": None}

    done = run(tmp_path, week(**{gra.ISSUES_URL: exchange(gra.ISSUES_URL, [*gra.ISSUES, pull])}))

    assert done.rows == {FILE: gra.EARLIER + row(**WEEK)}


def test_empty_lists_read_as_the_script_reads_them(tmp_path: Path) -> None:
    empty = {
        gra.ISSUES_URL: exchange(gra.ISSUES_URL, []),
        gra.CONTRIBUTORS_URL: exchange(gra.CONTRIBUTORS_URL, []),
        gra.RELEASES_URL: exchange(gra.RELEASES_URL, []),
        gra.LANGUAGES_URL: exchange(gra.LANGUAGES_URL, {}),
    }

    done = run(tmp_path, week(**empty))

    none = {
        "codebase_bytes": 0,
        "languages": {},
        "issues_7d": {"total": 0, "external": 0},
        "contributors": {"total": 0, "top_contributor": "", "top_contributor_pct": 0.0},
        "latest_release": {"tag": "", "date": ""},
    }
    assert done.rows == {FILE: gra.EARLIER + row(**{**WEEK, **none})}


@pytest.mark.parametrize(
    ("code", "headers"),
    [(403, {"X-RateLimit-Remaining": "0"}), (403, {"Retry-After": "60"}), (429, {})],
    ids=["403 none remaining", "403 retry-after", "429"],
)
def test_a_rate_limited_list_is_partial_and_recoverable(tmp_path: Path, code: int, headers: dict[str, str]) -> None:
    limited = exchange(gra.RELEASES_URL, '{"message":"API rate limit exceeded"}', status=code, headers=headers)

    done = run(tmp_path, week(**{gra.RELEASES_URL: limited}))

    reason = f"releases request failed (HTTP {code})"
    assert done.rows == {FILE: gra.EARLIER + partial_row(reason, latest_release=None)}
    assert (done.status, done.ctx.critical, done.sleeps) == (0, [], [])


# --- several runs on one day (a run writes what it got, and never erases what it didn't) ---


def test_a_failed_list_keeps_a_good_rows_value_and_the_row_stays_good(tmp_path: Path) -> None:
    good = row(**{**WEEK, "stars": 899, "codebase_bytes": 1000, "languages": {"Go": 1000}})

    done = run(tmp_path, week(**{gra.LANGUAGES_URL: failed(gra.LANGUAGES_URL)}), seed=gra.EARLIER + good)

    assert done.rows == {FILE: gra.EARLIER + row(**{**WEEK, "codebase_bytes": 1000, "languages": {"Go": 1000}})}
    kept = f"Kept the stored codebase_bytes, languages for 2026-09-23 in {FILE}, which this run failed to get"
    assert kept in done.log


def test_a_partial_row_is_filled_by_a_later_run_and_becomes_good(tmp_path: Path) -> None:
    earlier_release = {"tag": "v3.8.0", "date": "2026-09-01"}
    partial = partial_row("languages request failed (HTTP 000)", codebase_bytes=None, latest_release=earlier_release)

    done = run(tmp_path, week(**{gra.RELEASES_URL: failed(gra.RELEASES_URL)}), seed=gra.EARLIER + partial)

    assert done.rows == {FILE: gra.EARLIER + row(**{**WEEK, "latest_release": earlier_release})}


def test_a_failure_keeps_a_partial_row(tmp_path: Path) -> None:
    partial = partial_row("languages request failed (HTTP 000)", codebase_bytes=None)

    done = run(tmp_path, [failed(gra.REPO_URL)], seed=gra.EARLIER + partial)

    assert done.rows == {FILE: gra.EARLIER + partial}
    assert f"WARN: kept the partial row for 2026-09-23 in {FILE}; fetch_failed not recorded" in done.log


def test_a_good_row_replaces_a_partial_row(tmp_path: Path) -> None:
    partial = partial_row("languages request failed (HTTP 000)", codebase_bytes=None)

    done = run(tmp_path, week(), seed=gra.EARLIER + partial)

    assert done.rows == {FILE: gra.EARLIER + row(**WEEK)}
    assert done.ctx.corrections == []


def test_a_failing_entry_does_not_stop_the_next(tmp_path: Path) -> None:
    other = f"{GITHUB_API}/repos/a/other"
    since = f"since={gra.SINCE}"
    responses = [
        failed(gra.REPO_URL),
        exchange(other, github_repo("a/other", stars=2)),
        exchange(f"{other}/commits?{since}&per_page=100&page=1", []),
        exchange(f"{other}/issues?state=all&{since}&per_page=100", []),
        exchange(f"{other}/contributors?per_page=10", []),
        exchange(f"{other}/releases?per_page=5", [github_release("a/other", "v1.0.0", published_at=None)]),
        exchange(f"{other}/languages", {}),
    ]

    done = run(tmp_path, responses, cfg=[*CONFIG, {"repo": "a/other"}])

    assert done.rows[FILE] == gra.EARLIER + failure_row("fetch_failed", "repository request failed", "000")
    assert '"label": "other", "repo": "a/other", "stars": 2' in done.rows["github-repo-activity-other.jsonl"]


# --- check() ---


def check(tmp_path: Path, cfg: Any, *responses: Any) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, source.ENV):
        findings = source.check(ctx, cfg)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


def test_check_finds_the_repo(tmp_path: Path) -> None:
    assert check(tmp_path, CONFIG, gra.REPOSITORY) == [Finding("ok", f"{REPO}: the repo exists")]


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (
            exchange(gra.REPO_URL, '{"message":"Not Found"}', status=404),
            Finding("error", f"{REPO}: HTTP 404: the repo doesn't exist, or the token can't read it"),
        ),
        (
            exchange(gra.REPO_URL, "", status=401),
            Finding("error", f"{REPO}: HTTP 401: the repo doesn't exist, or the token can't read it"),
        ),
        (
            exchange(gra.REPO_URL, "", status=403, headers={"X-RateLimit-Remaining": "0"}),
            Finding("warn", f"{REPO}: could not fetch the repo: HTTP 403, rate limited"),
        ),
        (failed(gra.REPO_URL), Finding("warn", f"{REPO}: could not fetch the repo: HTTP 0")),
    ],
    ids=["not found", "rejected", "rate limited", "no connection"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(tmp_path: Path, response: Any, finding: Finding) -> None:
    assert check(tmp_path, CONFIG, response) == [finding]


def test_check_checks_every_entry(tmp_path: Path) -> None:
    other = f"{GITHUB_API}/repos/a/other"
    findings = check(tmp_path, [*CONFIG, {"repo": "a/other"}], gra.REPOSITORY, exchange(other, "", status=404))

    assert [f.level for f in findings] == ["ok", "error"]


# --- the summary ---


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(source, CONFIG, "pages_through_the_commits", tmp_path) == [
        "",
        "### github_repo_activity: example-org/example-project",
        "",
        "- Commits, last 7 days: 130",
        "- Stars: 900",
        "- Latest release: v3.9.0",
        "- Released: 2026-09-20",
    ]
