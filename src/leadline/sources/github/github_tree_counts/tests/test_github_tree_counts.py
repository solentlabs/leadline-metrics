"""github_tree_counts: the script's rows on its scenarios, and leadline's side of each decided divergence.

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
from scenarios.builders.common import exchange, failed, retried, thrice

from leadline import config, trends
from leadline.context import Critical, Finding
from leadline.http import SavedResponses
from leadline.sources.github import github_tree_counts
from leadline.sources.github.github_tree_counts import source as tree_source
from leadline.sources.github.github_tree_counts.tests import scenarios as tc
from leadline.sources.github.tests.builders import GITHUB_API, github_commit, github_tree

NAME = "github_tree_counts"
CONFIG = tc.LEADLINE_CONFIG
FILE = tc.FILE
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it
REPO = tc.REPO
VALUES = {"pattern": "(a)", "key": "k"}
GENERIC = {"repo": REPO, "pattern": tc.PATTERN, "ref": "main"}  # the default keys and file


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert github_tree_counts.NAME == NAME
    assert github_tree_counts.ENV == ("GITHUB_TOKEN",)
    assert github_tree_counts.LAGGING is False


def test_a_valid_config_has_no_problems() -> None:
    cfg = [*CONFIG, {"repo": "a/b", "pattern": "x/(.+)/(.+)"}, {**GENERIC, "content": tc.CONTENT}]

    assert github_tree_counts.validate(cfg) == []


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"repo": "a/b"}, [": must be a list of entries"]),
        (["a/b"], ["[0]: must be an object"]),
        ([{"pattern": "(a)(b)"}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a", "pattern": "(a)(b)"}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a/b/c", "pattern": "(a)(b)"}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a/ b", "pattern": "(a)(b)"}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a/b"}], ["[0].pattern: must be a non-empty string"]),
        ([{"repo": "a/b", "pattern": ""}], ["[0].pattern: must be a non-empty string"]),
        ([{"repo": "a/b", "pattern": "a)"}], ["[0].pattern: not a valid regex: unbalanced parenthesis"]),
        (
            [{"repo": "a/b", "pattern": "a/(b)"}],
            ["[0].pattern: must have two groups (the group, then the item), not 1"],
        ),
        ([{"repo": "a/b", "pattern": "(a)(b)", "ref": ""}], ["[0].ref: must be a non-empty string"]),
        ([{"repo": "a/b", "pattern": "(a)(b)", "ref": 5}], ["[0].ref: must be a non-empty string"]),
        ([{"repo": "a/b", "pattern": "(a)(b)", "keys": ["files"]}], ["[0].keys: must be an object"]),
        (
            [{"repo": "a/b", "pattern": "(a)(b)", "keys": {"models": "m"}}],
            ["[0].keys.models: not one of: files, items, groups, items_per_group"],
        ),
        (
            [{"repo": "a/b", "pattern": "(a)(b)", "keys": {"files": ""}}],
            ["[0].keys.files: must be a non-empty string"],
        ),
        (
            [{"repo": "a/b", "pattern": "(a)(b)", "keys": {"files": "n", "items": "n"}}],
            ["[0].keys.items: 'n' is already the key for files"],
        ),
        (
            [{"repo": "a/b", "pattern": "(a)(b)", "keys": {"groups": "items"}}],
            ["[0].keys.groups: 'items' is already the key for items"],
        ),
        (
            [{"repo": "a/b", "pattern": "(a)(b)", "keys": {"files": "date"}}],
            ["[0].keys.files: 'date' is a key a row has"],
        ),
        (
            [{"repo": "a/b", "pattern": "(a)(b)", "file": "x-{label}.jsonl"}],
            ["[0].file: 'x-{label}.jsonl' uses {label}, which is not one of: repo, repo_name"],
        ),
        (
            [{**GENERIC, "content": "status"}],
            ["[0].content: must be an object with a pattern (one group) and a key"],
        ),
        ([{**GENERIC, "content": {"key": "k"}}], ["[0].content.pattern: must be a non-empty string"]),
        ([{**GENERIC, "content": {"pattern": "", "key": "k"}}], ["[0].content.pattern: must be a non-empty string"]),
        (
            [{**GENERIC, "content": {"pattern": "a)", "key": "k"}}],
            ["[0].content.pattern: not a valid regex: unbalanced parenthesis"],
        ),
        (
            [{**GENERIC, "content": {"pattern": "status", "key": "k"}}],
            ["[0].content.pattern: must have one group (the value), not 0"],
        ),
        (
            [{**GENERIC, "content": {"pattern": "(a)(b)", "key": "k"}}],
            ["[0].content.pattern: must have one group (the value), not 2"],
        ),
        ([{**GENERIC, "content": {"pattern": "(a)"}}], ["[0].content.key: must be a non-empty string"]),
        ([{**GENERIC, "content": {"pattern": "(a)", "key": 5}}], ["[0].content.key: must be a non-empty string"]),
        (
            [{**GENERIC, "content": {"pattern": "(a)", "key": "date"}}],
            ["[0].content.key: 'date' is a key a row has"],
        ),
        (
            [{**GENERIC, "keys": {"groups": "status"}}],
            ["[0].keys.groups: 'status' is a key a row has"],
        ),
        (
            [{**GENERIC, "content": {"pattern": "(a)", "key": "reason"}}],
            ["[0].content.key: 'reason' is a key a row has"],
        ),
        (
            [{**GENERIC, "content": {"pattern": "(a)", "key": "files"}}],
            ["[0].content.key: 'files' is already the key for files"],
        ),
        (
            [{**GENERIC, "keys": {"items": "k_blobs"}, "content": {"pattern": "(a)", "key": "k"}}],
            ["[0].content.key: 'k_blobs', where the blobs read are kept, is already the key for items"],
        ),
        (
            [{**GENERIC, "content": {"pattern": "(a)", "key": "k", "colour": "red"}}],
            ["[0].content.colour: not one of: pattern, key, map"],
        ),
        ([{**GENERIC, "content": {**VALUES, "map": {"a": "b"}}}], []),
        ([{**GENERIC, "content": {**VALUES, "map": []}}], ["[0].content.map: must be an object of values to values"]),
        (
            [{**GENERIC, "content": {**VALUES, "map": {"": "b", "a": ""}}}],
            ["[0].content.map: '' must not be empty", "[0].content.map.a: must be a non-empty string"],
        ),
        ([{**GENERIC, "content": {**VALUES, "map": {"a": 5}}}], ["[0].content.map.a: must be a non-empty string"]),
        (
            [{**GENERIC, "content": {**VALUES, "map": {"a": "b", "b": "c"}}}],
            ["[0].content.map.a: 'b' is itself mapped; a value maps to a final name"],
        ),
        (
            [{**GENERIC, "content": {**VALUES, "map": {"a": "a"}}}],
            ["[0].content.map.a: 'a' is itself mapped; a value maps to a final name"],
        ),
    ],
    ids=[
        "not a list",
        "an entry not an object",
        "no repo",
        "a repo without its owner",
        "a repo with two slashes",
        "a repo with a space",
        "no pattern",
        "empty pattern",
        "a pattern that doesn't compile",
        "a pattern with one group",
        "empty ref",
        "ref not a string",
        "keys not an object",
        "an unknown key",
        "an empty key",
        "two counts under one key",
        "a count under another count's default key",
        "a key every row has",
        "a bad file",
        "content not an object",
        "content without a pattern",
        "an empty content pattern",
        "a content pattern that doesn't compile",
        "a content pattern with no group",
        "a content pattern with two groups",
        "content without a key",
        "a content key not a string",
        "a count key a failure row has",
        "a content key a partial row has",
        "a content key every row has",
        "a content key a count has",
        "a content key whose blobs key a count has",
        "an unknown content setting",
        "a value map",
        "a value map not an object",
        "an empty value or name in the map",
        "a mapped name not a string",
        "a map that chains",
        "a map that maps a value to itself",
    ],
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert github_tree_counts.validate(cfg) == problems


def test_files_are_the_default_or_the_template_of_each_entry() -> None:
    cfg = [GENERIC, {**GENERIC, "repo": "a/other", "file": "x-{repo_name}.jsonl"}]

    assert github_tree_counts.files(cfg) == {"[0]": "github-tree-counts-example.jsonl", "[1]": "x-other.jsonl"}


def test_it_is_skipped_without_a_token(tmp_path: Path) -> None:
    done = port.run(github_tree_counts, CONFIG, port.scenario(NAME, "skips_without_a_token"), tmp_path)

    assert "SKIP: github_tree_counts (GITHUB_TOKEN not set)" in done.log
    assert (done.status, done.rows) == (0, {})


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    done = port.run(github_tree_counts, tc.leadline_config(scenario), scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == [
        "a_body_without_a_tree_or_truncated_counts_nothing",
        "failed_fetch_writes_nothing",
        "unparseable_tree_writes_nothing",
    ]


def test_it_sends_the_token_and_asks_for_the_github_json(tmp_path: Path) -> None:
    sent: list[Any] = []
    transport = SavedResponses(tc.tree(tc.MODEMS))

    def recording(request: Any, timeout: float) -> Any:
        sent.append(request)
        return transport(request, timeout)

    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    ctx.http._transport = recording  # to see the request headers
    with port.credentials({"GITHUB_TOKEN": "t0ken"}, github_tree_counts.ENV):
        github_tree_counts.collect(ctx, CONFIG)

    assert sent[0].get_header("Authorization") == "Bearer t0ken"
    assert sent[0].get_header("Accept") == "application/vnd.github+json"


def test_a_truncated_tree_is_logged_as_floors(tmp_path: Path) -> None:
    done = port.run(github_tree_counts, CONFIG, port.scenario(NAME, "a_truncated_tree_is_marked"), tmp_path)

    assert f"WARN: the tree of {REPO} was truncated; its counts are floors, not totals" in done.log


# --- the config decides the counts' keys, the ref and the pattern ---


def test_the_default_keys_keep_the_scripts_order(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "counts_configurations_models_and_vendors")

    done = port.run(github_tree_counts, [GENERIC], scenario, tmp_path)

    assert done.rows == {
        "github-tree-counts-example.jsonl": '{"date": "2026-09-23", "items": 4, "files": 5, "groups": 3, '
        '"items_per_group": {"acme": 2, "orbit": 1, "zenith": 1}, "tree_truncated": false, '
        f'"captured_at": "{NOW}"}}\n'
    }


def test_the_ref_defaults_to_head(tmp_path: Path) -> None:
    url = f"https://api.github.com/repos/{REPO}/git/trees/HEAD?recursive=1"
    scenario = replace(port.scenario(NAME, "a_tree_without_modems"), responses=[exchange(url, github_tree(REPO, []))])

    done = port.run(github_tree_counts, [{"repo": REPO, "pattern": tc.PATTERN}], scenario, tmp_path)

    assert '"items": 0, "files": 0, "groups": 0, "items_per_group": {}' in done.rows["github-tree-counts-example.jsonl"]


def test_an_anchored_pattern_leaves_out_what_the_scripts_unanchored_one_counts(tmp_path: Path) -> None:
    """The pattern is the user's: `re.search`, as the script, so `^` is how a path is held to the root."""
    scenario = port.scenario(NAME, "counts_configurations_models_and_vendors")

    done = port.run(github_tree_counts, [{**GENERIC, "pattern": "^" + tc.PATTERN}], scenario, tmp_path)

    assert '"items": 3, "files": 4, "groups": 2' in done.rows["github-tree-counts-example.jsonl"]


def test_a_pattern_with_two_groups_counts_without_a_third(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "counts_configurations_models_and_vendors")

    done = port.run(
        github_tree_counts,
        [{**GENERIC, "pattern": r"catalog/modems/([^/]+)/([^/]+)/modem[^/]*\.yaml$"}],
        scenario,
        tmp_path,
    )

    assert '"items": 4, "files": 5, "groups": 3' in done.rows["github-tree-counts-example.jsonl"]


# --- decided divergences: ADR "A failure is recorded as a failure" ---


def failure_row(status: str, reason: str, http_code: str | None = None) -> str:
    code = f', "http_code": "{http_code}"' if http_code is not None else ""
    return f'{{"date": "2026-09-23", "status": "{status}"{code}, "reason": "{reason}", "captured_at": "{NOW}"}}\n'


def test_a_repo_or_ref_not_found_is_a_fetch_failed_row_and_critical(tmp_path: Path) -> None:
    done = port.run(github_tree_counts, CONFIG, port.scenario(NAME, "failed_fetch_writes_nothing"), tmp_path)

    assert done.rows == {
        FILE: tc.EARLIER + failure_row("fetch_failed", "tree request failed (HTTP 404, JSON body)", "404")
    }
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP 404 from GitHub's git trees API; JSON body")]
    assert done.status == 1


def test_an_unparseable_tree_is_a_parse_failed_row(tmp_path: Path) -> None:
    done = port.run(github_tree_counts, CONFIG, port.scenario(NAME, "unparseable_tree_writes_nothing"), tmp_path)

    assert done.rows == {FILE: tc.EARLIER + failure_row("parse_failed", "response was not JSON")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_body_without_a_tree_is_a_parse_failed_row_not_zeros(tmp_path: Path) -> None:
    """Zero means zero: an answer with no tree says nothing about the repo's files."""
    scenario = port.scenario(NAME, "a_body_without_a_tree_or_truncated_counts_nothing")

    done = port.run(github_tree_counts, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: failure_row("parse_failed", "response had an unexpected shape")}
    assert (done.status, done.ctx.critical) == (0, [])


@pytest.mark.parametrize(
    "body",
    [
        [1],
        None,
        {"tree": None},
        {"tree": {"path": "a"}},
        {"tree": ["catalog/modems/acme/x100/modem.yaml"]},
        {"tree": [{"path": 5}]},
    ],
    ids=["a list", "null", "a null tree", "a tree not a list", "an entry not an object", "a path not a string"],
)
def test_any_answer_the_scripts_code_stops_on_is_a_parse_failed_row(tmp_path: Path, body: Any) -> None:
    scenario = replace(port.scenario(NAME, "unparseable_tree_writes_nothing"), responses=[exchange(tc.TREE_URL, body)])

    done = port.run(github_tree_counts, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: tc.EARLIER + failure_row("parse_failed", "response had an unexpected shape")}


def test_an_entry_without_a_path_is_not_counted(tmp_path: Path) -> None:
    """As the script reads it: `entry.get('path', '')`."""
    body = {"tree": [{"type": "blob"}, {"path": "catalog/modems/acme/x100/modem.yaml"}], "truncated": False}
    scenario = replace(port.scenario(NAME, "a_tree_without_modems"), responses=[exchange(tc.TREE_URL, body)])

    done = port.run(github_tree_counts, CONFIG, scenario, tmp_path)

    assert '"models": 1, "configs": 1, "vendors": 1' in done.rows[FILE]


def test_a_failure_keeps_a_good_row(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data."""
    good = '{"date": "2026-09-23", "models": 4, "configs": 5, "vendors": 3}\n'
    scenario = replace(port.scenario(NAME, "unparseable_tree_writes_nothing"), seed={FILE: tc.EARLIER + good})

    done = port.run(github_tree_counts, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: tc.EARLIER + good}
    assert f"WARN: kept the good row for 2026-09-23 in {FILE}; parse_failed not recorded" in done.log


def test_a_good_row_replaces_a_failure_row(tmp_path: Path) -> None:
    reason = "tree request failed (HTTP 503, empty body)"
    failure = f'{{"date": "2026-09-23", "status": "fetch_failed", "http_code": "503", "reason": "{reason}"}}\n'
    scenario = replace(port.scenario(NAME, "counts_configurations_models_and_vendors"), seed={FILE: failure})

    port.run(github_tree_counts, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, "counts_configurations_models_and_vendors", tmp_path) == []


# --- failures: recoverable or not ---


@pytest.mark.parametrize("code", [401, 403, 404])
def test_an_error_waiting_cant_fix_is_critical_at_once(tmp_path: Path, code: int) -> None:
    scenario = replace(
        port.scenario(NAME, "failed_fetch_writes_nothing"), responses=[exchange(tc.TREE_URL, "", status=code)]
    )

    done = port.run(github_tree_counts, CONFIG, scenario, tmp_path)

    assert done.rows == {
        FILE: tc.EARLIER + failure_row("fetch_failed", f"tree request failed (HTTP {code}, empty body)", str(code))
    }
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP {code} from GitHub's git trees API; empty body")]
    assert done.status == 1


@pytest.mark.parametrize(
    ("code", "headers"),
    [(403, {"X-RateLimit-Remaining": "0"}), (403, {"Retry-After": "60"}), (429, {})],
    ids=["403 none remaining", "403 retry-after", "429"],
)
def test_a_rate_limit_is_recoverable_and_tried_three_times(tmp_path: Path, code: int, headers: dict[str, str]) -> None:
    limited = exchange(tc.TREE_URL, '{"message":"API rate limit exceeded"}', status=code, headers=headers)
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=thrice(limited))

    done = port.run(github_tree_counts, CONFIG, scenario, tmp_path)

    assert done.rows == {
        FILE: tc.EARLIER + failure_row("fetch_failed", f"tree request failed (HTTP {code}, rate limited)", str(code))
    }
    assert (done.status, done.ctx.critical, done.sleeps) == (0, [], [10, 20])


def test_no_connection_is_recorded_as_000_and_is_recoverable(tmp_path: Path) -> None:
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=thrice(failed(tc.TREE_URL)))

    done = port.run(github_tree_counts, CONFIG, scenario, tmp_path)

    assert done.rows == {
        FILE: tc.EARLIER + failure_row("fetch_failed", "tree request failed (HTTP 000, connection refused)", "000")
    }
    assert (done.status, done.sleeps) == (0, [10, 20])  # three attempts


def test_an_error_page_names_its_kind_in_the_reason(tmp_path: Path) -> None:
    page = exchange(tc.TREE_URL, "<html>Bad Gateway</html>", status=502)
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=thrice(page))

    done = port.run(github_tree_counts, CONFIG, scenario, tmp_path)

    assert done.rows == {
        FILE: tc.EARLIER + failure_row("fetch_failed", "tree request failed (HTTP 502, HTML page)", "502")
    }


def test_a_failing_entry_does_not_stop_the_next(tmp_path: Path) -> None:
    other_url = "https://api.github.com/repos/a/other/git/trees/main?recursive=1"
    responses = [*thrice(exchange(tc.TREE_URL, "", status=502)), exchange(other_url, github_tree("a/other", tc.MODEMS))]
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=responses)

    done = port.run(github_tree_counts, [*CONFIG, {**GENERIC, "repo": "a/other"}], scenario, tmp_path)

    assert done.rows[FILE] == tc.EARLIER + failure_row(
        "fetch_failed", "tree request failed (HTTP 502, empty body)", "502"
    )
    assert '"items": 4, "files": 5' in done.rows["github-tree-counts-other.jsonl"]


# --- check() ---


def check(tmp_path: Path, cfg: Any, *responses: Any) -> list[Finding]:
    transport = SavedResponses(retried(responses))
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, github_tree_counts.ENV):
        findings = github_tree_counts.check(ctx, cfg)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


WHERE = f"{REPO}@main"


def test_check_finds_the_paths_the_pattern_matches(tmp_path: Path) -> None:
    assert check(tmp_path, CONFIG, *tc.tree(tc.MODEMS + tc.NOT_MODEMS)) == [
        Finding("ok", f"{WHERE}: the pattern matches 5 paths in the tree")
    ]


def test_check_warns_when_the_pattern_matches_nothing(tmp_path: Path) -> None:
    assert check(tmp_path, CONFIG, *tc.tree(tc.NOT_MODEMS)) == [
        Finding("warn", f"{WHERE}: the pattern matches no path in the tree, so every night counts 0")
    ]


def test_check_warns_of_a_truncated_tree(tmp_path: Path) -> None:
    assert check(tmp_path, CONFIG, *tc.tree(tc.MODEMS, truncated=True)) == [
        Finding("warn", f"{WHERE}: the tree is truncated, so the counts are floors; the pattern matches 5 paths in it")
    ]


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (
            exchange(tc.TREE_URL, '{"message":"Not Found"}', status=404),
            Finding("error", f"{WHERE}: HTTP 404: the repo or its ref doesn't exist, or the token can't read it"),
        ),
        (
            exchange(tc.TREE_URL, "", status=401),
            Finding("error", f"{WHERE}: HTTP 401: the repo or its ref doesn't exist, or the token can't read it"),
        ),
        (
            thrice(exchange(tc.TREE_URL, "", status=403, headers={"X-RateLimit-Remaining": "0"})),
            Finding("warn", f"{WHERE}: could not fetch the tree: HTTP 403, rate limited"),
        ),
        (exchange(tc.TREE_URL, "", status=502), Finding("warn", f"{WHERE}: could not fetch the tree: HTTP 502")),
        (failed(tc.TREE_URL), Finding("warn", f"{WHERE}: could not fetch the tree: HTTP 0")),
        (exchange(tc.TREE_URL, "<html>"), Finding("warn", f"{WHERE}: could not read the tree: not JSON")),
        (exchange(tc.TREE_URL, {"sha": "0"}), Finding("warn", f"{WHERE}: could not read the tree: unexpected answer")),
    ],
    ids=["not found", "rejected", "rate limited", "unavailable", "no connection", "not JSON", "no tree"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(tmp_path: Path, response: Any, finding: Finding) -> None:
    responses = response if isinstance(response, list) else [response]

    assert check(tmp_path, CONFIG, *responses) == [finding]


def test_check_checks_every_entry(tmp_path: Path) -> None:
    other_url = "https://api.github.com/repos/a/other/git/trees/HEAD?recursive=1"
    findings = check(
        tmp_path,
        [*CONFIG, {"repo": "a/other", "pattern": tc.PATTERN}],
        *tc.tree(tc.MODEMS),
        exchange(other_url, "", status=404),
    )

    assert [f.level for f in findings] == ["ok", "error"]
    assert findings[1].message.startswith("a/other@HEAD: HTTP 404")


# --- the summary ---


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(github_tree_counts, CONFIG, "counts_configurations_models_and_vendors", tmp_path) == [
        "",
        "### github_tree_counts: solentlabs/example",
        "",
        "- models: 4",
        "- configs: 5",
        "- vendors: 3",
    ]


# --- rebuilt history (ADR: past values a source can rebuild are filled, never changed) ---

LATE_SCENARIO = "a_file_starting_late_gets_rows_back_to_the_first_commit_that_matches"


def test_the_days_before_the_file_are_rebuilt_once_and_said_so(tmp_path: Path) -> None:
    """The scenario's reviewed rows hold the values; a second run finds nothing missing, so asks for no history."""
    scenario = port.scenario(NAME, LATE_SCENARIO)

    first = port.run(github_tree_counts, CONFIG, scenario, tmp_path / "first")
    once = replace(scenario, seed=first.rows, responses=scenario.responses[:1])
    again = port.run(github_tree_counts, CONFIG, once, tmp_path / "again")

    assert f"Backfilled {FILE}: 0 row(s) filled, 4 added, 2026-09-17 to 2026-09-20" in first.log
    assert first.ctx.corrections == []
    assert again.rows == first.rows
    assert not [line for line in again.log if line.startswith("Backfilled")]


def test_a_rate_limit_stops_the_rebuild_for_the_night_and_the_next_night_goes_on(tmp_path: Path) -> None:
    """The row of the night is good; the next night asks only for the trees of the days still missing."""
    scenario = port.scenario(NAME, LATE_SCENARIO)
    limited_tree = tc.commit_tree(*tc.HISTORY[3])["request"]["url"]
    limited = exchange(limited_tree, '{"message": "API rate limit exceeded"}', status=403)
    limited["response"]["headers"] = {"x-ratelimit-remaining": "0"}
    night = [*scenario.responses[:-1], *thrice(limited)]

    first = port.run(github_tree_counts, CONFIG, replace(scenario, responses=night), tmp_path / "first")
    resumed = [*scenario.responses[:1], *tc.commits(tc.HISTORY), tc.commit_tree(*tc.HISTORY[3])]
    again = port.run(
        github_tree_counts, CONFIG, replace(scenario, seed=first.rows, responses=resumed), tmp_path / "next"
    )

    rebuilt = tc.REBUILT.splitlines(keepends=True)
    assert first.rows[FILE] == "".join(rebuilt[:2]) + tc.LATE + tc.TODAY
    assert (first.status, first.ctx.critical) == (0, [])
    assert "Stopped rebuilding past tree counts for solentlabs/example: HTTP 403; the next night goes on" in first.log
    assert again.rows[FILE] == tc.REBUILT + tc.LATE + tc.TODAY


def test_the_rebuild_asks_for_no_more_trees_than_a_budget_a_night_and_goes_on_the_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long history must not spend the day's rate limit that the other sources share."""
    monkeypatch.setattr(tree_source, "TREE_BUDGET", 2)
    scenario = port.scenario(NAME, LATE_SCENARIO)
    night = scenario.responses[:-1]  # today's tree, the commits, and the trees of the first two days with one

    first = port.run(github_tree_counts, CONFIG, replace(scenario, responses=night), tmp_path / "first")
    resumed = [*scenario.responses[:1], *tc.commits(tc.HISTORY), tc.commit_tree(*tc.HISTORY[3])]
    again = port.run(
        github_tree_counts, CONFIG, replace(scenario, seed=first.rows, responses=resumed), tmp_path / "next"
    )

    rebuilt = tc.REBUILT.splitlines(keepends=True)
    assert first.rows[FILE] == "".join(rebuilt[:2]) + tc.LATE + tc.TODAY
    assert "Stopped rebuilding past tree counts for solentlabs/example after 2 tree(s); the next night goes on" in (
        first.log
    )
    assert (first.status, again.status) == (0, 0)
    assert again.rows[FILE] == tc.REBUILT + tc.LATE + tc.TODAY


def test_a_new_file_asks_for_no_history_on_its_first_night(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, LATE_SCENARIO)

    done = port.run(github_tree_counts, CONFIG, replace(scenario, seed={}, responses=scenario.responses[:1]), tmp_path)

    assert done.rows[FILE] == tc.TODAY


# --- content: the files counted by a value read from each one's text (ADR: a tree's files are counted by what
# they say) ---

CONTENT_FIRST = tc.CONTENT_NAMES[0]
CONTENT_LATE = tc.CONTENT_NAMES[1]
WITH_CONTENT = [{**tc.LEADLINE_CONFIG[0], "content": tc.CONTENT}]
ALL_TODAY = tc.REBUILT_WITH_STATUS + tc.NIGHTS_WITH_STATUS + tc.TODAY_WITH_STATUS  # the rows after a whole history
NEXT_NIGHT = "2026-09-24T23:31:05"
BLOB_URL = f"{GITHUB_API}/repos/{REPO}/git/blobs/"


def content_run(tmp_path: Path, responses: list[Any], seed: str = "", name: str = CONTENT_FIRST, **changes: Any) -> Any:
    scenario = replace(port.scenario(NAME, name), responses=responses, seed={FILE: seed} if seed else {}, **changes)
    return port.run(github_tree_counts, WITH_CONTENT, scenario, tmp_path)


def last_row(done: Any) -> dict[str, Any]:
    row: dict[str, Any] = json.loads(done.rows[FILE].splitlines()[-1])
    return row


def today_blobs() -> list[Any]:
    return [tc.blob(text) for text in sorted(set(tc.TEXTS_TODAY.values()))]


def test_the_files_are_counted_by_the_value_the_pattern_reads_and_the_counts_add_up_to_the_files(
    tmp_path: Path,
) -> None:
    done = content_run(tmp_path, [*tc.tree(tc.MODEMS + tc.NOT_MODEMS, contents=tc.TEXTS_TODAY), *today_blobs()])

    row = last_row(done)
    assert row["by_status"] == {"awaiting": 1, "confirmed": 3, "unknown": 1}
    assert sum(row["by_status"].values()) == row["configs"] == 5
    assert list(row)[-3:] == ["by_status", "by_status_blobs", "captured_at"]  # after the script's keys
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_blob_shared_by_two_files_is_read_once(tmp_path: Path) -> None:
    """The five files hold four texts: asking twice would fail the run, a blob having one answer."""
    done = content_run(tmp_path, [*tc.tree(tc.MODEMS, contents=tc.TEXTS_TODAY), *today_blobs()])

    assert len(last_row(done)["by_status_blobs"]["confirmed"]) == 2
    assert done.status == 0


def test_an_ordinary_night_reads_no_blob(tmp_path: Path) -> None:
    """Every blob in the tree was read by an earlier row: the night asks for the tree and nothing else."""
    done = content_run(
        tmp_path, tc.tree(tc.MODEMS + tc.NOT_MODEMS, contents=tc.TEXTS_TODAY), ALL_TODAY, CONTENT_LATE, now=NEXT_NIGHT
    )

    row = last_row(done)
    assert (row["date"], row["by_status"]) == ("2026-09-24", {"awaiting": 1, "confirmed": 3, "unknown": 1})
    assert "by_status_blobs" not in row
    assert done.rows[FILE].startswith(ALL_TODAY)  # nothing before it changes
    assert not [line for line in done.log if line.startswith("Backfilled")]


def test_a_night_reads_only_the_blobs_no_row_has_read_and_its_row_records_them(tmp_path: Path) -> None:
    changed = {**tc.TEXTS_TODAY, tc.MODEMS[2]: "status: retired\n"}

    done = content_run(
        tmp_path,
        [*tc.tree(tc.MODEMS + tc.NOT_MODEMS, contents=changed), tc.blob("status: retired\n")],
        ALL_TODAY,
        CONTENT_LATE,
        now=NEXT_NIGHT,
    )

    row = last_row(done)
    assert row["by_status"] == {"awaiting": 1, "confirmed": 3, "retired": 1}
    assert row["by_status_blobs"] == json.loads(tc.read_by_status(retired=["status: retired\n"]))


def test_a_rerun_of_the_day_keeps_the_blobs_the_earlier_run_recorded(tmp_path: Path) -> None:
    tree = tc.tree(tc.MODEMS + tc.NOT_MODEMS, contents=tc.TEXTS_TODAY)
    first = content_run(tmp_path / "first", [*tree, *today_blobs()])

    again = content_run(tmp_path / "again", tree, first.rows[FILE])

    assert again.rows == first.rows  # the row as the first run wrote it, with its blobs


def test_a_file_with_no_match_or_an_empty_one_counts_as_unknown(tmp_path: Path) -> None:
    pattern = {"pattern": r"^status:\s*(\w*)", "key": "by_status"}
    texts = {tc.MODEMS[0]: "status:\n", tc.MODEMS[1]: "no status here\n", tc.MODEMS[3]: "Status: Shouting\n"}
    responses = [*tc.tree(tc.MODEMS[:2] + tc.MODEMS[3:4], contents=texts), *(tc.blob(t) for t in set(texts.values()))]
    scenario = replace(port.scenario(NAME, CONTENT_FIRST), responses=responses)

    done = port.run(github_tree_counts, [{**tc.LEADLINE_CONFIG[0], "content": pattern}], scenario, tmp_path)

    assert last_row(done)["by_status"] == {"unknown": 3}  # the pattern is case sensitive


def test_the_pattern_runs_over_every_line_of_the_text(tmp_path: Path) -> None:
    text = "name: X100\nnotes: none\nstatus: confirmed\n"
    done = content_run(tmp_path, [*tc.tree(tc.MODEMS[:1], contents={tc.MODEMS[0]: text}), tc.blob(text)])

    assert last_row(done)["by_status"] == {"confirmed": 1}


def test_an_entry_that_is_not_a_file_is_unknown_and_asks_for_nothing(tmp_path: Path) -> None:
    """A folder or submodule whose name matches has no text to read."""
    entries = [
        {"path": "catalog/modems/a/x/modem.yaml", "type": "tree", "sha": "1" * 40},
        {"path": "catalog/modems/a/y/modem.yaml", "type": "commit", "sha": "2" * 40},
        {"path": "catalog/modems/a/z/modem.yaml", "type": "blob"},
    ]
    responses = [exchange(tc.TREE_URL, {"sha": "0" * 40, "tree": entries, "truncated": False})]

    done = content_run(tmp_path, responses)

    assert last_row(done)["by_status"] == {"unknown": 3}


def test_a_blob_that_cant_be_read_is_a_failed_part_not_a_count(tmp_path: Path) -> None:
    """ADR: a partly failed row keeps its good parts: the counts are null, the blobs that were read are kept."""
    responses = [
        *tc.tree(tc.MODEMS + tc.NOT_MODEMS, contents=tc.TEXTS_TODAY),
        tc.blob(tc.AWAITING),
        tc.blob(tc.NEW_X100),
        *thrice(exchange(BLOB_URL + tc.BLOB_SHA(tc.NO_STATUS), "", status=503)),
    ]

    done = content_run(tmp_path, responses)

    row = last_row(done)
    assert (row["configs"], row["by_status"], row["status"]) == (5, None, "partial")
    assert row["reason"] == "blob request failed (HTTP 503, empty body)"
    assert list(row)[-4:] == ["by_status_blobs", "status", "reason", "captured_at"]
    assert row["by_status_blobs"] == json.loads(tc.read_by_status(awaiting=[tc.AWAITING], confirmed=[tc.NEW_X100]))
    assert (done.status, done.ctx.critical) == (0, [])  # recoverable


def test_an_unreadable_blob_waiting_cant_fix_is_critical_at_once(tmp_path: Path) -> None:
    responses = [
        *tc.tree(tc.MODEMS[:1], contents=tc.TEXTS_TODAY),
        exchange(BLOB_URL + tc.BLOB_SHA(tc.NEW_X100), '{"message": "Not Found"}', status=404),
    ]

    done = content_run(tmp_path, responses)

    assert last_row(done)["status"] == "partial"
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP 404 from GitHub's git blobs API; JSON body")]
    assert done.status == 1


@pytest.mark.parametrize(
    "body",
    [
        "<html>",
        [1],
        {"encoding": "base64"},
        {"content": "aGk=", "encoding": "utf-8"},
        {"content": 5, "encoding": "base64"},
    ],
    ids=["not JSON", "a list", "no content", "another encoding", "content not a string"],
)
def test_a_blob_answer_it_cant_use_is_a_failed_part(tmp_path: Path, body: Any) -> None:
    responses = [*tc.tree(tc.MODEMS[:1], contents=tc.TEXTS_TODAY), exchange(BLOB_URL + tc.BLOB_SHA(tc.NEW_X100), body)]

    done = content_run(tmp_path, responses)

    row = last_row(done)
    assert (row["by_status"], row["status"], row["reason"]) == (None, "partial", "blob response was not usable")
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_failed_blob_never_replaces_the_counts_the_day_has(tmp_path: Path) -> None:
    """ADR: a run never erases what it didn't get: the stored counts stay and the row is good again."""
    stored = (
        '{"date": "2026-09-23", "models": 1, "configs": 1, "vendors": 1, "models_per_vendor": {"acme": 1}, '
        '"tree_truncated": false, "by_status": {"confirmed": 1}, "captured_at": "2026-09-23T10:00:00+00:00"}\n'
    )
    responses = [
        *tc.tree(tc.MODEMS[:1], contents=tc.TEXTS_TODAY),
        *thrice(exchange(BLOB_URL + tc.BLOB_SHA(tc.NEW_X100), "", status=503)),
    ]

    done = content_run(tmp_path, responses, stored)

    row = last_row(done)
    assert (row["by_status"], "status" in row) == ({"confirmed": 1}, False)
    assert f"Kept the stored by_status for 2026-09-23 in {FILE}, which this run failed to get" in done.log


def test_the_content_counts_of_a_file_whose_tree_cant_be_read_are_a_failure_row_as_before(tmp_path: Path) -> None:
    done = content_run(tmp_path, [exchange(tc.TREE_URL, "<html>")])

    assert done.rows == {FILE: failure_row("parse_failed", "response was not JSON")}


def test_the_history_gets_the_counts_too_and_each_blob_is_read_once(tmp_path: Path) -> None:
    """The scenario's reviewed rows hold the values; a second run finds nothing missing, so asks for no history."""
    scenario = port.scenario(NAME, CONTENT_LATE)

    first = port.run(github_tree_counts, WITH_CONTENT, scenario, tmp_path / "first")
    once = replace(scenario, seed=first.rows, responses=scenario.responses[:1])
    again = port.run(github_tree_counts, WITH_CONTENT, once, tmp_path / "again")

    assert f"Backfilled {FILE}: 2 row(s) filled, 4 added, 2026-09-17 to 2026-09-22" in first.log
    assert first.ctx.corrections == []  # a fill, not a correction
    assert again.rows == first.rows
    assert not [line for line in again.log if line.startswith("Backfilled")]


def test_a_count_a_past_row_has_stays_and_the_others_are_filled(tmp_path: Path) -> None:
    kept = tc.NIGHT.format(day=21, more='"by_status": {"confirmed": 4}, ')
    scenario = port.scenario(NAME, CONTENT_LATE)

    done = port.run(
        github_tree_counts,
        WITH_CONTENT,
        replace(scenario, seed={FILE: kept + tc.NIGHT.format(day=22, more="")}),
        tmp_path,
    )

    assert tc.NIGHT.format(day=22, more=tc.BY_STATUS_19TH) in done.rows[FILE]
    assert kept in done.rows[FILE]


def test_a_failure_row_in_the_history_is_left_as_it_is_and_asks_for_nothing(tmp_path: Path) -> None:
    reason = "tree request failed (HTTP 502, empty body)"
    failure = f'{{"date": "2026-09-22", "status": "fetch_failed", "http_code": "502", "reason": "{reason}"}}\n'
    seed = ALL_TODAY.replace(tc.NIGHT.format(day=22, more=tc.BY_STATUS_19TH), failure)

    done = content_run(
        tmp_path, tc.tree(tc.MODEMS + tc.NOT_MODEMS, contents=tc.TEXTS_TODAY), seed, CONTENT_LATE, now=NEXT_NIGHT
    )

    assert done.rows[FILE].startswith(seed)  # the tree was the only request: no commits were listed for it


def test_the_rebuild_reads_no_more_than_a_budget_of_blobs_a_night_and_goes_on_the_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rebuild stops before a tree once a night's reads reach the budget; the night's own row isn't counted."""
    monkeypatch.setattr(tree_source, "BLOB_BUDGET", 1)
    scenario = port.scenario(NAME, CONTENT_LATE)
    today = scenario.responses[: 1 + len(set(tc.TEXTS_TODAY.values()))]
    night = [*today, *tc.commits(tc.HISTORY), *(tc.commit_tree(*tc.HISTORY[n]) for n in (0,))]
    night = [*night, tc.commit_tree(*tc.HISTORY[2], tc.TEXTS_17TH), tc.blob(tc.OLD_X100)]

    first = port.run(github_tree_counts, WITH_CONTENT, replace(scenario, responses=night), tmp_path / "first")
    rest = [*today[:1], *tc.commits(tc.HISTORY), tc.commit_tree(*tc.HISTORY[3], tc.TEXTS_19TH)]
    again = port.run(
        github_tree_counts, WITH_CONTENT, replace(scenario, seed=first.rows, responses=rest), tmp_path / "next"
    )

    rebuilt = tc.REBUILT_WITH_STATUS.splitlines(keepends=True)
    assert first.rows[FILE] == "".join(rebuilt[:2]) + tc.NIGHTS + tc.TODAY_WITH_STATUS
    assert (
        "Stopped rebuilding past tree counts for solentlabs/example after 1 blob read(s); the next night goes on"
        in first.log
    )
    assert again.rows[FILE] == ALL_TODAY
    assert (first.status, again.status) == (0, 0)


# --- content's map: a value written differently over time counts as one ---

PENDING = {**tc.CONTENT, "map": {"awaiting": "pending"}}
MAPPED = [{**tc.LEADLINE_CONFIG[0], "content": PENDING}]
TODAY_READ = tc.read_by_status(pending=[tc.AWAITING], confirmed=[tc.NEW_X100, tc.CONFIRMED], unknown=[tc.NO_STATUS])


def mapped_run(tmp_path: Path, responses: list[Any], seed: str = "", name: str = CONTENT_FIRST, **changes: Any) -> Any:
    scenario = replace(port.scenario(NAME, name), responses=responses, seed={FILE: seed} if seed else {}, **changes)
    return port.run(github_tree_counts, MAPPED, scenario, tmp_path)


def parsed(done: Any) -> list[dict[str, Any]]:
    return [json.loads(line) for line in done.rows[FILE].splitlines()]


def test_the_files_are_counted_by_the_mapped_value_and_the_blobs_listed_under_it(tmp_path: Path) -> None:
    done = mapped_run(tmp_path, [*tc.tree(tc.MODEMS + tc.NOT_MODEMS, contents=tc.TEXTS_TODAY), *today_blobs()])

    row = parsed(done)[-1]
    assert row["by_status"] == {"confirmed": 3, "pending": 1, "unknown": 1}  # unmapped values stay as written
    assert row["by_status_blobs"] == json.loads(TODAY_READ)
    assert (done.status, done.ctx.critical) == (0, [])


def test_blobs_stored_under_an_old_name_are_read_under_the_mapped_one_without_a_request(tmp_path: Path) -> None:
    """Every blob in the tree is listed in the rows under `awaiting`: the night asks for the tree and nothing else, and
    the rows before it are left as they were written."""
    done = mapped_run(
        tmp_path, tc.tree(tc.MODEMS + tc.NOT_MODEMS, contents=tc.TEXTS_TODAY), ALL_TODAY, CONTENT_LATE, now=NEXT_NIGHT
    )

    row = parsed(done)[-1]
    assert (row["date"], row["by_status"]) == ("2026-09-24", {"confirmed": 3, "pending": 1, "unknown": 1})
    assert "by_status_blobs" not in row
    assert done.rows[FILE].startswith(ALL_TODAY)
    assert (done.status, done.ctx.critical, done.ctx.corrections) == (0, [], [])


def test_a_rerun_of_the_day_under_a_new_map_rewrites_its_row_with_the_mapped_names(tmp_path: Path) -> None:
    tree = tc.tree(tc.MODEMS + tc.NOT_MODEMS, contents=tc.TEXTS_TODAY)
    first = content_run(tmp_path / "first", [*tree, *today_blobs()])

    again = mapped_run(tmp_path / "again", tree, first.rows[FILE])

    (row,) = parsed(again)
    assert row["by_status"] == {"confirmed": 3, "pending": 1, "unknown": 1}
    assert row["by_status_blobs"] == json.loads(TODAY_READ)
    assert again.status == 0


def test_the_rebuild_counts_every_era_under_the_mapped_name(tmp_path: Path) -> None:
    """The 17th and 19th trees hold the old name; the rebuilt rows and the fill of the nights say the mapped one."""
    scenario = port.scenario(NAME, CONTENT_LATE)

    done = mapped_run(tmp_path, list(scenario.responses), tc.NIGHTS, CONTENT_LATE)

    by_status = {row["date"]: row["by_status"] for row in parsed(done)}
    assert by_status["2026-09-17"] == {"pending": 2}
    assert by_status["2026-09-19"] == {"confirmed": 2, "pending": 1, "unknown": 1}
    assert by_status["2026-09-21"] == by_status["2026-09-22"] == by_status["2026-09-19"]
    assert by_status["2026-09-23"] == {"confirmed": 3, "pending": 1, "unknown": 1}
    assert all("awaiting" not in row["by_status"] for row in parsed(done))
    assert (done.status, done.ctx.critical, done.ctx.corrections) == (0, [], [])


# --- several patterns, in priority order (newest location first) ---

NEW = r"^lib/items/([^/]+)/([^/]+)/item\.yaml$"
OLD = r"^items/([^/]+)/([^/]+)/item\.yaml$"
SRC = r"^src/([^/]+)/([a-z][^/]*)\.py$"
LOCATIONS = [NEW, OLD, {"pattern": SRC, "content": False}]
MOVED = {**tc.LEADLINE_CONFIG[0], "pattern": LOCATIONS}
STATUS_ITEM = "name: item\nstatus: confirmed\n"
SOURCE_TEXT = "status: not a status line\n"  # source files may hold the line's text; none is read


def moved_run(
    tmp_path: Path, files: dict[str, str], cfg: Any = MOVED, source_read: bool = False, **changes: Any
) -> Any:
    """A night's run over a tree of `files` (path to text); a file's blob is asked for as the responses give it, the
    source files' too when `source_read`."""
    texts = (
        {text for path, text in files.items() if source_read or not path.startswith("src/")}
        if "content" in cfg
        else set()
    )
    responses = [*tc.tree(list(files), contents=files), *(tc.blob(text) for text in sorted(texts))]
    scenario = replace(port.scenario(NAME, "a_tree_without_modems"), responses=responses, **changes)
    return port.run(github_tree_counts, [cfg], scenario, tmp_path)


def test_a_list_of_patterns_is_valid_as_strings_or_objects() -> None:
    cfg = [{**GENERIC, "pattern": [NEW, {"pattern": OLD}, {"pattern": SRC, "content": False}]}]

    assert github_tree_counts.validate(cfg) == []


@pytest.mark.parametrize(
    ("pattern", "problems"),
    [
        ([], ["[0].pattern: must not be an empty list"]),
        ([""], ["[0].pattern[0]: must be a non-empty string"]),
        ([NEW, 5], ["[0].pattern[1]: must be a regex or an object with a pattern"]),
        ([NEW, "a)"], ["[0].pattern[1]: not a valid regex: unbalanced parenthesis"]),
        ([NEW, "a/(b)"], ["[0].pattern[1]: must have two groups (the group, then the item), not 1"]),
        ([{}], ["[0].pattern[0].pattern: must be a non-empty string"]),
        ([{"pattern": "a)"}], ["[0].pattern[0].pattern: not a valid regex: unbalanced parenthesis"]),
        (
            [{"pattern": NEW, "content": "no"}],
            ["[0].pattern[0].content: must be true, false or an object of a pattern and a map"],
        ),
        ([{"pattern": NEW, "colour": "red"}], ["[0].pattern[0].colour: not one of: pattern, content"]),
        (
            ["a)", {"pattern": "b)"}],
            [
                "[0].pattern[0]: not a valid regex: unbalanced parenthesis",
                "[0].pattern[1].pattern: not a valid regex: unbalanced parenthesis",
            ],
        ),
        ({"pattern": NEW}, ["[0].pattern: must be a non-empty string"]),
    ],
    ids=[
        "an empty list",
        "an empty pattern",
        "an item not a regex or object",
        "a pattern that doesn't compile",
        "a pattern with one group",
        "an object without a pattern",
        "an object whose pattern doesn't compile",
        "content not a boolean",
        "an unknown setting",
        "every pattern's problem",
        "an object in place of the list",
    ],
)
def test_validate_checks_each_listed_pattern(pattern: Any, problems: list[str]) -> None:
    assert github_tree_counts.validate([{**GENERIC, "pattern": pattern}]) == problems


def test_a_pair_is_counted_from_the_first_pattern_that_matches_it(tmp_path: Path) -> None:
    """a/b is in all three places, a/c in the second and third, a/d in the third: files are the winners' paths."""
    files = {
        "lib/items/a/b/item.yaml": STATUS_ITEM,
        "items/a/b/item.yaml": STATUS_ITEM,
        "src/a/b.py": SOURCE_TEXT,
        "items/a/c/item.yaml": STATUS_ITEM,
        "src/a/c.py": SOURCE_TEXT,
        "src/z/d.py": SOURCE_TEXT,
        "src/a/__init__.py": SOURCE_TEXT,
    }

    row = last_row(moved_run(tmp_path, files))

    assert (row["models"], row["configs"], row["vendors"]) == (3, 3, 2)
    assert row["models_per_vendor"] == {"a": 2, "z": 1}


def test_a_list_of_one_pattern_counts_as_the_string(tmp_path: Path) -> None:
    files = {"lib/items/a/b/item.yaml": STATUS_ITEM, "items/a/b/item.yaml": STATUS_ITEM, "items/a/c/item.yaml": ""}
    as_string = {**MOVED, "pattern": r"items/([^/]+)/([^/]+)/item\.yaml$"}

    listed = moved_run(tmp_path / "list", files, {**MOVED, "pattern": [as_string["pattern"]]})
    single = moved_run(tmp_path / "string", files, as_string)

    assert listed.rows == single.rows


def test_content_is_read_only_for_the_patterns_it_is_meant_for(tmp_path: Path) -> None:
    """The source files count under `unknown`, none of them asked for: a request without a response would fail."""
    files = {"lib/items/a/b/item.yaml": STATUS_ITEM, "src/a/c.py": SOURCE_TEXT, "src/a/d.py": SOURCE_TEXT}

    done = moved_run(tmp_path, files, {**MOVED, "content": tc.CONTENT})

    row = last_row(done)
    assert row["by_status"] == {"confirmed": 1, "unknown": 2}
    assert row["by_status_blobs"] == json.loads(tc.read_by_status(confirmed=[STATUS_ITEM]))
    assert (done.status, done.ctx.critical) == (0, [])


PARSER = {"pattern": r"^[ \t]*status\s*=\s*Status\.(\w+)", "map": {"VERIFIED": "confirmed"}}
PARSER_TEXT = "class A:\n    status = Status.VERIFIED  # by someone\n"
READ_BY_PARSER = {**tc.LEADLINE_CONFIG[0], "pattern": [NEW, {"pattern": SRC, "content": PARSER}], "content": tc.CONTENT}


def test_a_pattern_may_read_its_files_by_its_own_content_pattern_and_map(tmp_path: Path) -> None:
    """The source files say `Status.VERIFIED`, the items `status: confirmed`; both count under the entry's key, and the
    blobs listed use the mapped name."""
    files = {"lib/items/a/b/item.yaml": STATUS_ITEM, "src/a/c.py": PARSER_TEXT, "src/a/d.py": "x = 1\n"}

    done = moved_run(tmp_path, files, READ_BY_PARSER, source_read=True)

    row = last_row(done)
    assert row["by_status"] == {"confirmed": 2, "unknown": 1}
    assert row["by_status_blobs"] == json.loads(
        tc.read_by_status(confirmed=[STATUS_ITEM, PARSER_TEXT], unknown=["x = 1\n"])
    )
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_patterns_content_object_may_leave_out_its_pattern_and_map() -> None:
    cfg = {**READ_BY_PARSER, "pattern": [{"pattern": SRC, "content": {}}]}

    assert github_tree_counts.validate([cfg]) == []


@pytest.mark.parametrize(
    ("setting", "problems"),
    [
        ({"pattern": "a"}, ["[0].pattern[1].content.pattern: must have one group (the value), not 0"]),
        (
            {"pattern": "a)"},
            ["[0].pattern[1].content.pattern: not a valid regex: unbalanced parenthesis"],
        ),
        (
            {"map": {"a": "b", "b": "c"}},
            ["[0].pattern[1].content.map.a: 'b' is itself mapped; a value maps to a final name"],
        ),
        ({"key": "k"}, ["[0].pattern[1].content.key: not one of: pattern, map"]),
        ([], ["[0].pattern[1].content: must be true, false or an object of a pattern and a map"]),
    ],
    ids=["no group", "no compile", "a chain", "an entry-only setting", "a list"],
)
def test_validate_checks_a_patterns_content_object(setting: Any, problems: list[str]) -> None:
    cfg = {**READ_BY_PARSER, "pattern": [NEW, {"pattern": SRC, "content": setting}]}

    assert github_tree_counts.validate([cfg]) == problems


def test_a_patterns_content_object_needs_content_on_the_entry() -> None:
    cfg = {**GENERIC, "pattern": [{"pattern": SRC, "content": PARSER}]}

    assert github_tree_counts.validate([cfg]) == ["[0].pattern[0].content: needs the entry's content"]


def test_the_check_counts_the_paths_the_patterns_count(tmp_path: Path) -> None:
    files = ["lib/items/a/b/item.yaml", "items/a/b/item.yaml", "src/a/c.py"]

    assert check(tmp_path, [MOVED], *tc.tree(files)) == [
        Finding("ok", f"{WHERE}: the patterns match 2 paths in the tree")
    ]


def day_tree(commit: Any, files: dict[str, str]) -> Any:
    return tc.commit_tree(commit, list(files), files)


def test_the_rebuild_goes_back_to_the_first_tree_any_pattern_matches_and_counts_stay_continuous(
    tmp_path: Path,
) -> None:
    """A source era, then the old location (the source files still there), then both locations for a day, then the
    new: the items never drop, and no day is written before the first tree that matches."""
    src = {"src/a/b.py": SOURCE_TEXT, "src/a/c.py": SOURCE_TEXT}
    old = {"items/a/b/item.yaml": STATUS_ITEM, "items/a/c/item.yaml": STATUS_ITEM}
    new = {"lib/items/a/b/item.yaml": STATUS_ITEM, "lib/items/a/c/item.yaml": STATUS_ITEM}
    history = [
        (github_commit(REPO, "Nothing yet", when="2026-09-14T10:00:00Z"), {"README.md": ""}),
        (github_commit(REPO, "Source", when="2026-09-15T10:00:00Z"), src),
        (github_commit(REPO, "Old location", when="2026-09-17T10:00:00Z"), {**src, **old}),
        (github_commit(REPO, "Move", when="2026-09-19T10:00:00Z"), {**old, **new}),
    ]
    night = {**new, "lib/items/a/d/item.yaml": STATUS_ITEM}
    seed = tc.NIGHT.format(day=21, more="")
    responses = [
        *tc.tree(list(night), contents=night),
        *tc.commits([(commit, list(files)) for commit, files in history]),
        *(day_tree(commit, files) for commit, files in history),
    ]
    scenario = replace(port.scenario(NAME, "a_tree_without_modems"), responses=responses, seed={FILE: seed})

    done = port.run(github_tree_counts, [MOVED], scenario, tmp_path)

    rows = [json.loads(line) for line in done.rows[FILE].splitlines()]
    assert [(r["date"], r["models"], r["configs"]) for r in rows] == [
        ("2026-09-15", 2, 2),
        ("2026-09-16", 2, 2),
        ("2026-09-17", 2, 2),
        ("2026-09-18", 2, 2),
        ("2026-09-19", 2, 2),
        ("2026-09-20", 2, 2),
        ("2026-09-21", 3, 4),
        ("2026-09-23", 3, 3),
    ]


# --- the summary ---


def test_its_summary_shows_the_counts_by_value_as_a_breakdown_only_with_content() -> None:
    (plain,) = github_tree_counts.summary(CONFIG)
    (counted,) = github_tree_counts.summary(WITH_CONTENT)

    assert [shown.field for shown in counted.shown[:-1]] == [shown.field for shown in plain.shown]
    assert counted.shown[-1] == config.Shown("by_status", "by_status", "breakdown")


def test_its_file_count_is_charted_only_as_the_top_of_the_content_stack() -> None:
    (plain,) = github_tree_counts.summary(CONFIG)
    (counted,) = github_tree_counts.summary(WITH_CONTENT)

    assert {shown.field: shown.into for shown in plain.shown}["configs"] == ""
    assert {shown.field: shown.into for shown in counted.shown} == {
        **{shown.field: "" for shown in plain.shown},
        "configs": "by_status",
        "by_status": "",
    }


def test_the_default_summary_shows_the_counts_by_value_largest_first(tmp_path: Path) -> None:
    lines = port.summary(github_tree_counts, WITH_CONTENT, CONTENT_FIRST, tmp_path)

    assert lines[-1] == "- by_status: confirmed 3 · awaiting 1 · unknown 1"


def test_trends_chart_the_counts_by_value_stacked_to_the_files(tmp_path: Path) -> None:
    (tmp_path / FILE).write_text(ALL_TODAY, encoding="utf-8")
    (tmp_path / "health.jsonl").write_text("", encoding="utf-8")

    data = trends.build(files_in(tmp_path), date(2026, 9, 24), github_tree_counts.summary(WITH_CONTENT), "health.jsonl")

    (section,) = data["sections"]
    field = next(f for f in section["fields"] if f["field"] == "by_status")
    assert (field["kind"], field["versions"], field["keys"]) == (
        "breakdown",
        False,
        ["confirmed", "awaiting", "unknown"],
    )
    stacked = [sum(day) for day in zip(*field["values"], field["other"])]
    files = next(f for f in section["fields"] if f["field"] == "configs")["values"]
    assert stacked == files  # each day's bands add up to the day's configs
