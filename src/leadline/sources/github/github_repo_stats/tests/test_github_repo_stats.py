"""github_repo_stats: the script's rows on its scenarios, and leadline's side of each decided divergence.

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
from leadline.sources.github import github_repo_stats as source
from leadline.sources.github import spec as github_spec
from leadline.sources.github.github_repo_stats.tests import scenarios as grs
from leadline.sources.github.tests.builders import GITHUB_API, github_repo, github_traffic

NAME = "github_repo_stats"
CONFIG = grs.LEADLINE_CONFIG
FILE = grs.FILE
REPO = grs.REPO
EARLIER = grs.EARLIER[FILE]
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it
REPOSITORY, VIEWS, CLONES = grs.REPOSITORY, *grs.TRAFFIC
STATS = {"stars": 41, "forks": 6, "watchers": 5, "open_issues": 3}
TRAFFIC = {"views_14d": 217, "views_unique_14d": 42, "clones_14d": 28, "clones_unique_14d": 14}
NO_VIEWS = {"views_14d": None, "views_unique_14d": None}
NO_CLONES = {"clones_14d": None, "clones_unique_14d": None}


def row(**fields: Any) -> str:
    """A row for the run's date as leadline writes it: the script's fields, then `fields`, then captured_at."""
    return json.dumps({"date": "2026-09-23", "repo": REPO, **fields, "captured_at": NOW}) + "\n"


def run(tmp_path: Path, *responses: Any, seed: str = EARLIER, cfg: Any = CONFIG) -> port.PortRun:
    scenario = replace(port.scenario(NAME, "repository_and_its_traffic"), responses=list(responses), seed={FILE: seed})
    return port.run(source, cfg, scenario, tmp_path)


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert source.NAME == NAME
    assert source.ENV == ("GITHUB_TOKEN",)
    assert source.LAGGING is False


def test_a_valid_config_has_no_problems() -> None:
    assert source.validate([{"repo": "a/b"}, {"repo": "a/c", "file": "x-{repo_name}.jsonl"}]) == []


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"repo": "a/b"}, [": must be a list of entries"]),
        (["a/b"], ["[0]: must be an object"]),
        ([{}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a"}], ["[0].repo: must be owner/name"]),
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
    ids=["not a list", "an entry not an object", "no repo", "a repo without its owner", "a repo not a string",
         "a bad file", "a file the repo can't name"],
)  # fmt: skip
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert source.validate(cfg) == problems


def test_files_are_the_default_or_the_template_of_each_entry() -> None:
    cfg = [{"repo": "a/one"}, {"repo": "a/two", "file": "stats-{repo_name}.jsonl"}]

    assert source.files(cfg) == {"[0]": "github-repo-stats-one.jsonl", "[1]": "stats-two.jsonl"}


def test_it_is_skipped_without_a_token(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "skips_without_a_token"), tmp_path)

    assert "SKIP: github_repo_stats (GITHUB_TOKEN not set)" in done.log
    assert (done.status, done.rows) == (0, {})


def test_it_sends_the_token_and_asks_for_the_github_json(tmp_path: Path) -> None:
    sent: list[Any] = []
    transport = SavedResponses([REPOSITORY, VIEWS, CLONES])

    def recording(request: Any, timeout: float) -> Any:
        sent.append(request)
        return transport(request, timeout)

    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    ctx.http._transport = recording  # to see the request headers
    with port.credentials({"GITHUB_TOKEN": "t0ken"}, source.ENV):
        source.collect(ctx, CONFIG)

    assert [r.get_header("Authorization") for r in sent] == ["Bearer t0ken"] * 3
    assert {r.get_header("Accept") for r in sent} == {"application/vnd.github+json"}


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    done = port.run(source, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == [
        "an_escaped_quote_in_the_repository_writes_nothing",
        "failed_repository_writes_nothing",
        "failed_traffic_counts_as_zero",
        "triple_quotes_in_the_repository_write_nothing",
        "unparseable_repository_writes_nothing",
        "unparseable_traffic_writes_nothing",
    ]


def test_missing_counts_are_a_parse_failed_row_naming_them_not_zeros(tmp_path: Path) -> None:
    """The script reads a missing count as 0; leadline records the failure (ADR: a failure is recorded as a failure)."""
    bare = exchange(grs.REPO_URL, {"full_name": REPO})

    done = run(tmp_path, bare, exchange(grs.VIEWS, {"views": []}), CLONES)

    reason = "repository response is missing stargazers_count, forks_count, subscribers_count, open_issues_count"
    failure = f'"status": "parse_failed", "reason": "{reason}", "captured_at": "{NOW}"'
    assert done.rows[FILE] == EARLIER + f'{{"date": "2026-09-23", "repo": "{REPO}", {failure}}}\n'


# --- decided divergences: the port reads the JSON ---


@pytest.mark.parametrize(
    "name", ["an_escaped_quote_in_the_repository_writes_nothing", "triple_quotes_in_the_repository_write_nothing"]
)
def test_a_repository_the_script_cant_paste_writes_the_real_row(tmp_path: Path, name: str) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, name), tmp_path)

    assert done.rows == {FILE: EARLIER + row(stars=41, forks=0, watchers=0, open_issues=0, **TRAFFIC)}
    assert (done.status, done.ctx.critical) == (0, [])


# --- decided divergences: the repository is required (ADR: a failure is recorded as a failure) ---


def failure_row(status: str, reason: str, http_code: str | None = None) -> str:
    code = {"http_code": http_code} if http_code is not None else {}
    return row(status=status, **code, reason=reason)


def test_a_failed_repository_is_a_fetch_failed_row(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "failed_repository_writes_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "repository request failed", "502")}
    assert (done.status, done.ctx.critical) == (0, [])  # recoverable: the next run writes the day


def test_an_unparseable_repository_is_a_parse_failed_row(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "unparseable_repository_writes_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "repository response was not JSON")}
    assert (done.status, done.ctx.critical) == (0, [])


@pytest.mark.parametrize(
    "body",
    [[], None, 5, {**github_repo(REPO), "stargazers_count": None}, {**github_repo(REPO), "forks_count": "6"}],
    ids=["a list", "null", "a number", "a null count", "a count not a number"],
)
def test_a_repository_it_cant_use_is_a_parse_failed_row(tmp_path: Path, body: Any) -> None:
    done = run(tmp_path, exchange(grs.REPO_URL, body), VIEWS, CLONES)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "repository response had an unexpected shape")}


@pytest.mark.parametrize("code", [401, 403, 404])
def test_a_repository_error_waiting_cant_fix_is_critical_at_once(tmp_path: Path, code: int) -> None:
    done = run(tmp_path, exchange(grs.REPO_URL, "", status=code))

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "repository request failed", str(code))}
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP {code} from GitHub's repository API")]
    assert done.status == 1


def test_a_failure_keeps_a_good_row(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data."""
    good = '{"date": "2026-09-23", "repo": "solentlabs/example-tool", "stars": 41}\n'

    done = run(tmp_path, exchange(grs.REPO_URL, "", status=502), seed=EARLIER + good)

    assert done.rows == {FILE: EARLIER + good}
    assert f"WARN: kept the good row for 2026-09-23 in {FILE}; fetch_failed not recorded" in done.log


# --- decided divergences: views and clones are optional (ADR: a partly failed row keeps its good parts) ---


def partial_row(reason: str, **traffic: Any) -> str:
    return row(**STATS, **{**TRAFFIC, **traffic}, status="partial", reason=reason)


def test_failed_traffic_is_null_in_a_partial_row_never_zero(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "failed_traffic_counts_as_zero"), tmp_path)

    reason = "views request failed (HTTP 403); clones request failed (HTTP 000)"
    assert done.rows == {FILE: partial_row(reason, **NO_VIEWS, **NO_CLONES)}
    # A 403 without a rate limit is no push access, which waiting can't fix.
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP 403 from GitHub's traffic API")]
    assert done.status == 1


def test_unparseable_views_are_null_in_a_partial_row(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "unparseable_traffic_writes_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + partial_row("views response was not JSON", **NO_VIEWS)}
    assert (done.status, done.ctx.critical) == (0, [])


def test_clones_without_their_counts_are_null_in_a_partial_row_naming_them_not_zero(tmp_path: Path) -> None:
    done = run(tmp_path, REPOSITORY, VIEWS, exchange(grs.CLONES, {"clones": []}))

    assert done.rows == {FILE: EARLIER + partial_row("clones response is missing count, uniques", **NO_CLONES)}
    assert (done.status, done.ctx.critical) == (0, [])


def test_failed_clones_alone_are_null_in_a_partial_row(tmp_path: Path) -> None:
    done = run(tmp_path, REPOSITORY, VIEWS, exchange(grs.CLONES, "", status=500))

    assert done.rows == {FILE: EARLIER + partial_row("clones request failed (HTTP 500)", **NO_CLONES)}
    assert (done.status, done.ctx.critical) == (0, [])


@pytest.mark.parametrize(
    "body",
    ["", [], {"count": None, "uniques": 1}, {"count": 3, "uniques": "1"}, {"count": True, "uniques": 1}],
    ids=["an empty body", "a list", "a null count", "uniques not a number", "a count that is a boolean"],
)
def test_views_it_cant_use_are_null_in_a_partial_row(tmp_path: Path, body: Any) -> None:
    done = run(tmp_path, REPOSITORY, exchange(grs.VIEWS, body), CLONES)

    reason = "views response was not JSON" if body == "" else "views response had an unexpected shape"
    assert done.rows == {FILE: EARLIER + partial_row(reason, **NO_VIEWS)}


@pytest.mark.parametrize(
    ("code", "headers"),
    [(403, {"X-RateLimit-Remaining": "0"}), (403, {"Retry-After": "60"}), (429, {})],
    ids=["403 none remaining", "403 retry-after", "429"],
)
def test_rate_limited_traffic_is_partial_and_recoverable(tmp_path: Path, code: int, headers: dict[str, str]) -> None:
    limited = exchange(grs.VIEWS, '{"message":"API rate limit exceeded"}', status=code, headers=headers)

    done = run(tmp_path, REPOSITORY, limited, CLONES)

    assert done.rows == {FILE: EARLIER + partial_row(f"views request failed (HTTP {code})", **NO_VIEWS)}
    assert (done.status, done.ctx.critical, done.sleeps) == (0, [], [])


# --- several runs on one day (a run writes what it got, and never erases what it didn't) ---

STORED_VIEWS = {"views_14d": 100, "views_unique_14d": 10}
KEPT_VIEWS = f"Kept the stored views_14d, views_unique_14d for 2026-09-23 in {FILE}, which this run failed to get"


def test_failed_views_keep_a_good_rows_views_and_the_row_stays_good(tmp_path: Path) -> None:
    good = row(**{**STATS, "stars": 40}, **{**TRAFFIC, **STORED_VIEWS})

    done = run(tmp_path, REPOSITORY, exchange(grs.VIEWS, "", status=500), CLONES, seed=EARLIER + good)

    assert done.rows == {FILE: EARLIER + row(**STATS, **{**TRAFFIC, **STORED_VIEWS})}
    assert KEPT_VIEWS in done.log
    assert done.ctx.corrections == []  # the run's own date


def test_a_partial_row_is_filled_by_a_later_run_and_becomes_good(tmp_path: Path) -> None:
    partial = partial_row("clones request failed (HTTP 500)", **STORED_VIEWS, **NO_CLONES)

    done = run(tmp_path, REPOSITORY, exchange(grs.VIEWS, "", status=500), CLONES, seed=EARLIER + partial)

    assert done.rows == {FILE: EARLIER + row(**STATS, **{**TRAFFIC, **STORED_VIEWS})}
    assert KEPT_VIEWS in done.log


def test_what_no_run_got_stays_null_named_by_this_runs_failure(tmp_path: Path) -> None:
    partial = partial_row("views request failed (HTTP 502); clones request failed (HTTP 502)", **NO_VIEWS, **NO_CLONES)

    done = run(tmp_path, REPOSITORY, exchange(grs.VIEWS, "", status=500), CLONES, seed=EARLIER + partial)

    assert done.rows == {FILE: EARLIER + partial_row("views request failed (HTTP 500)", **NO_VIEWS)}


def test_a_failure_keeps_a_partial_row(tmp_path: Path) -> None:
    partial = partial_row("views request failed (HTTP 500)", **NO_VIEWS)

    done = run(tmp_path, exchange(grs.REPO_URL, "", status=502), seed=EARLIER + partial)

    assert done.rows == {FILE: EARLIER + partial}
    assert f"WARN: kept the partial row for 2026-09-23 in {FILE}; fetch_failed not recorded" in done.log


def test_a_good_row_replaces_a_partial_row(tmp_path: Path) -> None:
    partial = partial_row("views request failed (HTTP 500)", **NO_VIEWS)
    scenario = replace(port.scenario(NAME, "repository_and_its_traffic"), seed={FILE: partial})

    done = port.run(source, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, "repository_and_its_traffic", tmp_path) == []
    assert done.ctx.corrections == []


def test_a_failing_entry_does_not_stop_the_next(tmp_path: Path) -> None:
    other = f"{GITHUB_API}/repos/a/other"
    done = run(
        tmp_path,
        exchange(grs.REPO_URL, "", status=502),
        exchange(other, github_repo("a/other", stars=2)),
        exchange(f"{other}/traffic/views", github_traffic("views", {})),
        exchange(f"{other}/traffic/clones", github_traffic("clones", {})),
        cfg=[*CONFIG, {"repo": "a/other"}],
    )

    assert done.rows[FILE] == EARLIER + failure_row("fetch_failed", "repository request failed", "502")
    assert '"repo": "a/other", "stars": 2' in done.rows["github-repo-stats-other.jsonl"]


# --- check() ---


def check(tmp_path: Path, cfg: Any, *responses: Any) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, source.ENV):
        findings = source.check(ctx, cfg)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


def test_check_finds_the_repo_and_that_the_token_can_read_its_traffic(tmp_path: Path) -> None:
    assert check(tmp_path, CONFIG, REPOSITORY, VIEWS) == [
        Finding("ok", f"{REPO}: the repo exists"),
        Finding("ok", f"{REPO}: the token can read its traffic"),
    ]


@pytest.mark.parametrize(
    ("responses", "findings"),
    [
        (
            [exchange(grs.REPO_URL, '{"message":"Not Found"}', status=404)],
            [Finding("error", f"{REPO}: HTTP 404: the repo doesn't exist, or the token can't read it")],
        ),
        (
            [exchange(grs.REPO_URL, "", status=403, headers={"Retry-After": "60"})],
            [Finding("warn", f"{REPO}: could not fetch the repo: HTTP 403, rate limited")],
        ),
        (
            [REPOSITORY, exchange(grs.VIEWS, '{"message":"Must have push access to repository"}', status=403)],
            [
                Finding("ok", f"{REPO}: the repo exists"),
                Finding("error", f"{REPO}: HTTP 403: the token can't read its traffic, which needs push access"),
            ],
        ),
        (
            [REPOSITORY, failed(grs.VIEWS)],
            [
                Finding("ok", f"{REPO}: the repo exists"),
                Finding("warn", f"{REPO}: could not fetch its traffic: HTTP 0"),
            ],
        ),
    ],
    ids=["no repo, and no traffic asked for", "the repo rate limited", "no push access", "no connection for traffic"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(
    tmp_path: Path, responses: list[Any], findings: list[Finding]
) -> None:
    assert check(tmp_path, CONFIG, *responses) == findings


def test_check_checks_every_entry(tmp_path: Path) -> None:
    other = f"{GITHUB_API}/repos/a/other"
    findings = check(tmp_path, [*CONFIG, {"repo": "a/other"}], REPOSITORY, VIEWS, exchange(other, "", status=404))

    assert [f.level for f in findings] == ["ok", "ok", "error"]


# --- the summary ---


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(source, CONFIG, "repository_and_its_traffic", tmp_path) == [
        "",
        "### github_repo_stats: solentlabs/example-tool",
        "",
        "- Stars: 41",
        "- Forks: 6",
        "- Views, 14 days: 217",
    ]


def test_its_summary_is_a_row_of_the_traffic_card_whose_views_are_not_charted() -> None:
    (section,) = source.summary(CONFIG)

    assert section.card == github_spec.STARS_AND_FORKS
    assert [(shown.field, shown.charted) for shown in section.shown] == [
        ("stars", True),
        ("forks", True),
        ("views_14d", False),
    ]
