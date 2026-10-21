"""github_issues: the script's rows on its scenarios, and leadline's side of each decided divergence.

The rows are compared with the goldens through tests/port.py, each scenario run with the config its
calls and constants give (scenarios.py, leadline_config); the scenarios where leadline
differs by a decision are listed there (DIVERGENCES) and asserted here instead.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, Sequence

import port
import pytest
import scenarios
from scenarios.builders.common import exchange, failed, failing_at

from leadline import config, http, trends
from leadline.context import Critical, Finding
from leadline.http import Http, SavedResponses
from leadline.sources.github import github_issues as source
from leadline.sources.github import spec as github_spec
from leadline.sources.github.github_issues import regions
from leadline.sources.github.github_issues.tests import scenarios as gi
from leadline.sources.github.tests.builders import GITHUB_API, GITHUB_GRAPHQL, github_repo, graphql_body

NAME = "github_issues"
FILE = gi.FILE
REPO = gi.REPO
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it
OPEN_URL = f"{gi.REPO_URL}/issues?state=open&per_page=100&page=1"
ALL_URL = f"{gi.REPO_URL}/issues?state=all&per_page=100&page=1"
LABEL_URL = f"{gi.REPO_URL}/labels/new%20modem"
ENTRY = {"repo": REPO, "maintainer": gi.MAINTAINER}  # no queue
# A queue's issues: one waiting on a region's keyword and a matching body, one resolved in two days.
QUEUED = [
    gi.issue(
        1,
        title="On Example Cable",
        author="example-user",
        created_at="2026-09-18T10:00:00Z",
        comments=1,
        labels=["queue-x"],
        body="See example.LOG",
    ),
    gi.issue(
        2,
        author=gi.MAINTAINER,
        created_at="2026-09-19T10:00:00Z",
        closed_at="2026-09-21T10:00:00Z",
        labels=["queue-x"],
    ),
]
TABLE = {"text": [{"region": "XA", "keywords": ["example cable"]}]}
ONLY_QUEUE_OR_STARS = "regions: only a queue or stars_and_forks uses a region table; add one or remove it"
MATCH = {"pattern": r"\.log\b", "flag_key": "has_log", "count_key": "with_log"}


def failure_row(status: str, reason: str, http_code: str | None = None) -> str:
    """A failure row for the run's date as leadline writes it: no counts, and nothing but its key."""
    code = {"http_code": http_code} if http_code is not None else {}
    return json.dumps({"date": "2026-09-23", "status": status, **code, "reason": reason, "captured_at": NOW}) + "\n"


def run(
    tmp_path: Path, responses: list[Any], cfg: Any = (ENTRY,), seed: str = gi.YESTERDAY, name: str = "empty_repository"
) -> port.PortRun:
    scenario = replace(port.scenario(NAME, name), responses=responses, seed={FILE: seed} if seed else {})
    return port.run(source, list(cfg), scenario, tmp_path)


def without_counts(text: str, before: str) -> list[dict[str, Any]]:
    """The rows of `text`, without the `repo` of those before `before`: what a test of the issues' totals reads."""
    rows = [json.loads(line) for line in text.splitlines()]
    return [{k: v for k, v in row.items() if not (k == "repo" and row["date"] < before)} for row in rows]


def last_row(done: port.PortRun) -> dict[str, Any]:
    row: dict[str, Any] = json.loads(done.rows[FILE].splitlines()[-1])
    return row


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert source.NAME == NAME
    assert source.ENV == ("GITHUB_TOKEN",)
    assert source.LAGGING is False


def test_a_valid_config_has_no_problems() -> None:
    cfg = [
        {"repo": "a/b"},
        {"repo": "a/c", "maintainer": ""},
        {"repo": "a/d", "maintainer": "someone", "queue": {"label": "x"}, "file": "x-{repo_name}.jsonl"},
        {"repo": "a/e", "regions": TABLE, "queue": {"label": "x", "key": "k", "items_key": "i", "body_match": MATCH}},
        {"repo": "a/f", "kinds": gi.KINDS},
        {"repo": "a/g", "stars_and_forks": True},
        {"repo": "a/h", "stars_and_forks": False},
        {"repo": "a/i", "stars_and_forks": True, "regions": TABLE},
    ]

    assert source.validate(cfg) == []


@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        ({"repo": "a"}, "repo: must be owner/name"),
        ({"maintainer": 5}, "maintainer: must be a login, or empty for none"),
        ({"stars_and_forks": "yes"}, "stars_and_forks: must be true or false"),
        ({"stars_and_forks": 1}, "stars_and_forks: must be true or false"),
        ({"queue": "x"}, "queue: must be an object with a label"),
        ({"queue": {}}, "queue.label: must be a non-empty string"),
        ({"queue": {"label": ""}}, "queue.label: must be a non-empty string"),
        ({"queue": {"label": "x", "key": ""}}, "queue.key: must be a non-empty string"),
        ({"queue": {"label": "x", "items_key": 5}}, "queue.items_key: must be a non-empty string"),
        ({"queue": {"label": "x", "key": "issues"}}, "queue.key: must not be one of the row's own keys"),
        ({"queue": {"label": "x", "body_match": []}}, "queue.body_match: must be an object"),
        ({"queue": {"label": "x", "bogus": 1}}, "queue.bogus: not one of: label, key, items_key, body_match"),
        (
            {"queue": {"label": "x", "body_match": {**MATCH, "bogus": 1}}},
            "queue.body_match.bogus: not one of: pattern, flag_key, count_key",
        ),
        ({"queue": {"label": "x", "body_match": {**MATCH, "pattern": 5}}}, "queue.body_match.pattern: must be a regex"),
        (
            {"queue": {"label": "x", "body_match": {**MATCH, "pattern": "*log"}}},
            "queue.body_match.pattern: must be a regex: nothing to repeat at position 0",
        ),
        (
            {"queue": {"label": "x", "body_match": {"pattern": "log", "count_key": "n"}}},
            "queue.body_match.flag_key: must be a non-empty string",
        ),
        (
            {"queue": {"label": "x", "body_match": {**MATCH, "count_key": "total"}}},
            "queue.body_match.count_key: must not be one of the queue's own keys",
        ),
        (
            {"queue": {"label": "x", "items_key": "with_log", "body_match": MATCH}},
            "queue.body_match.count_key: must not be one of the queue's own keys",
        ),
        (
            {"queue": {"label": "x", "items_key": "regions"}},
            "queue.items_key: must not be one of the queue's own keys",
        ),
        (
            {"queue": {"label": "x", "body_match": {**MATCH, "flag_key": "labels"}}},
            "queue.body_match.flag_key: must not be one of an item's own keys",
        ),
        ({"regions": TABLE}, ONLY_QUEUE_OR_STARS),
        ({"regions": TABLE, "stars_and_forks": False}, ONLY_QUEUE_OR_STARS),
        ({"kinds": "bug"}, "kinds: must be a non-empty list of kinds, each a name and a label"),
        ({"kinds": []}, "kinds: must be a non-empty list of kinds, each a name and a label"),
        ({"kinds": ["bug"]}, "kinds[0]: must be an object with a name and a label"),
        ({"kinds": [{"name": "bug"}]}, "kinds[0].label: must be a non-empty string"),
        ({"kinds": [{"label": "bug"}]}, "kinds[0].name: must be a non-empty string"),
        ({"kinds": [{"name": "", "label": "bug"}]}, "kinds[0].name: must be a non-empty string"),
        ({"kinds": [{"name": "bug", "label": 5}]}, "kinds[0].label: must be a non-empty string"),
        (
            {"kinds": [{"name": "bug", "label": "bug", "colour": "red"}]},
            "kinds[0].colour: not one of: name, label",
        ),
        (
            {"kinds": [{"name": "other", "label": "bug"}]},
            "kinds[0].name: 'other' is the kind of an issue with none of the labels",
        ),
        (
            {"kinds": [{"name": "a", "label": "x"}, {"name": "a", "label": "y"}]},
            "kinds[1].name: 'a' is already the name of kinds[0]",
        ),
        (
            {"kinds": [{"name": "a", "label": "x"}, {"name": "b", "label": "x"}]},
            "kinds[1].label: 'x' is already the label of kinds[0], so no issue could be of kind 'b'",
        ),
    ],
    ids=["a repo without its owner", "a maintainer not a string",
         "stars_and_forks a string", "stars_and_forks a number", "a queue not an object", "a queue without a label",
         "an empty label", "an empty key", "an items key not a string", "a key the row has",
         "a body match not an object", "an unknown queue key", "an unknown body match key",
         "a pattern not a string", "a pattern that isn't a regex", "no flag key",
         "a count key the queue has", "a count key the items key has", "an items key the queue has",
         "a flag key an item has", "regions without a queue or stars and forks",
         "regions with stars and forks off", "kinds not a list", "no kinds", "a kind not an object",
         "a kind without a name", "a kind without a label", "an empty name", "a label not a string",
         "an unknown kind key", "a kind named other", "two kinds with one name", "two kinds with one label"],
)  # fmt: skip
def test_validate_names_each_problem_by_its_path(entry: dict[str, Any], problem: str) -> None:
    assert source.validate([{"repo": "a/b", **entry}]) == [f"[0].{problem}"]


def test_validate_names_a_list_and_its_entries_as_every_source_does() -> None:
    assert source.validate({"repo": "a/b"}) == [": must be a list of entries"]
    assert source.validate(["a/b", {"repo": "a/b", "file": "x"}]) == [
        "[0]: must be an object",
        "[1].file: 'x' must fill to a plain *.jsonl name, not 'x'",
    ]


def test_files_are_named_for_the_repo_by_default() -> None:
    cfg = [{"repo": "a/one"}, {"repo": "a/two", "file": "x-{repo_name}.jsonl"}]

    assert source.files(cfg) == {"[0]": "github-issues-one.jsonl", "[1]": "x-two.jsonl"}


def test_it_is_skipped_without_a_token(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "skips_without_a_token")

    done = port.run(source, gi.leadline_config(scenario), scenario, tmp_path)

    assert "SKIP: github_issues (GITHUB_TOKEN not set)" in done.log
    assert (done.status, done.rows) == (0, {})


def test_it_sends_the_token_and_asks_for_the_github_json(tmp_path: Path) -> None:
    sent: list[Any] = []
    transport = http.SavedResponses(gi.responses(gi.SMALL))

    def recording(request: Any, timeout: float) -> Any:
        sent.append(request)
        return transport(request, timeout)

    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    ctx.http._transport = recording  # to see the request headers
    with port.credentials({"GITHUB_TOKEN": "t0ken"}, source.ENV):
        source.collect(ctx, [ENTRY])

    assert {r.get_header("Authorization") for r in sent} == {"Bearer t0ken"}
    assert {r.get_header("Accept") for r in sent} == {"application/vnd.github+json"}


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    done = port.run(source, gi.leadline_config(scenario), scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == [
        "a_page_not_a_list_writes_nothing",
        "an_earlier_row_without_totals_gives_no_flow",
        "failed_later_page_writes_nothing",
        "failed_open_page_writes_nothing",
        "flow_since_the_previous_snapshot",
        "repo_not_found_writes_nothing",
        "unparseable_repository_writes_nothing",
    ]


def test_an_absent_maintainer_is_none_as_an_empty_one_is(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "without_a_maintainer_everyone_is_community")

    port.run(source, [{"repo": REPO}], scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []


# --- the queue: config names its label, keys and body pattern; its keys come in the script's order ---


def queue(tmp_path: Path, **settings: Any) -> list[tuple[str, Any]]:
    """The queue's keys and values, in the row's order, for QUEUED under `settings`."""
    regions = settings.pop("regions", None)
    entry = {**ENTRY, "queue": {"label": "queue-x", **settings}, **({"regions": regions} if regions else {})}

    done = run(tmp_path, gi.responses(QUEUED), cfg=[entry], seed="")

    row = last_row(done)
    key = settings.get("key", "label_queue")
    assert list(row)[-2:] == [key, "captured_at"]  # after every key the script writes before its pipeline
    return list(row[key].items())


REQUESTERS = {"maintainer": 1, "community": 1, "bot": 0, "community_authors": 1}
ITEM = [("number", 1), ("title", "On Example Cable"), ("age_days", 5), ("comments", 1)]


def test_a_queue_without_a_body_match_or_regions_leaves_their_keys_out(tmp_path: Path) -> None:
    assert queue(tmp_path) == [
        ("total", 1),
        ("avg_age_days", 5),
        ("with_comments", 1),
        ("requested_total", 2),
        ("resolved_total", 1),
        ("median_days_to_resolve", 2),
        ("requesters", REQUESTERS),
        ("issues", [dict([*ITEM, ("labels", ["queue-x"])])]),
    ]


def test_a_queue_with_a_body_match_and_regions_has_the_scripts_keys_in_its_order(tmp_path: Path) -> None:
    items = [dict([*ITEM, ("has_log", True), ("labels", ["queue-x"]), ("region", "XA")])]

    got = queue(tmp_path, key="waiting", items_key="requests", body_match=MATCH, regions=TABLE)

    assert got == [
        ("total", 1),
        ("avg_age_days", 5),
        ("with_log", 1),  # the pattern is matched ignoring case
        ("with_comments", 1),
        ("regions", {"XA": 1}),
        ("requested_total", 2),
        ("resolved_total", 1),
        ("median_days_to_resolve", 2),
        ("requesters", REQUESTERS),
        ("requests", items),
    ]
    assert [list(item) for item in dict(got)["requests"]] == [[k for k, _ in items[0].items()]]


def test_a_queue_with_regions_only_has_the_regions(tmp_path: Path) -> None:
    got = dict(queue(tmp_path, regions={"text": [{"region": "XB", "keywords": ["nowhere"]}]}))

    assert (got["regions"], [item["region"] for item in got["issues"]]) == ({"unknown": 1}, ["unknown"])
    assert "with_log" not in got


def test_the_regions_come_from_the_shared_table_the_entry_names(tmp_path: Path) -> None:
    """config.load puts the named table in the entry; the queue reads it as the script reads its rules."""
    path = tmp_path / "leadline.json"
    entry = {**ENTRY, "regions": "example-regions", "queue": {"label": "queue-x"}}
    data = {"out_dir": "m", "shared": {"regions": {"example-regions": TABLE}}, "sources": {NAME: [entry]}}
    path.write_text(json.dumps(data), encoding="utf-8")

    cfg = config.load(path, dict.fromkeys(config.SETTINGS)).sources[NAME]
    done = run(tmp_path / "out", gi.responses(QUEUED), cfg=cfg, seed="")

    assert last_row(done)["label_queue"]["regions"] == {"XA": 1}


# --- the previous snapshot, for the flow ---


def flow(tmp_path: Path, seed: str) -> dict[str, int]:
    done = run(tmp_path, gi.responses(gi.SMALL), seed=seed)
    since_prev: dict[str, int] = last_row(done)["issues"]["since_prev"]
    return since_prev


def test_a_failure_row_is_not_a_previous_snapshot(tmp_path: Path) -> None:
    """Every reader of the rows skips failure rows (ADR: a failure is recorded as a failure)."""
    failure = '{"date": "2026-09-22", "status": "fetch_failed", "http_code": "503", "reason": "x"}\n'

    assert flow(tmp_path, gi.flow_row("2026-09-21", 1, 0) + failure) == {"days": 2, "opened": 2, "closed": 2}


def test_a_line_not_a_row_is_not_a_previous_snapshot_and_is_kept(tmp_path: Path) -> None:
    """Where the script can't read the file (ADR: store never fails on a line it can't use)."""
    unusable = 'null\n[1]\n{"date": 5, "issues": {"opened_total": 0}}\n{"date": "Sept 22", "issues": {}}\n'
    seed = gi.flow_row("2026-09-21", 1, 0) + unusable

    assert flow(tmp_path, seed) == {"days": 2, "opened": 2, "closed": 2}
    kept = (tmp_path / FILE).read_text(encoding="utf-8").splitlines(keepends=True)
    assert "".join(line for line in kept if '"backfilled"' not in line).startswith(seed)


def test_totals_not_counts_give_no_flow(tmp_path: Path) -> None:
    seed = '{"date": "2026-09-22", "issues": {"opened_total": "1", "closed_total": 0}}\n'

    assert flow(tmp_path, seed) == {"days": 0, "opened": 0, "closed": 0}


# --- rebuilt totals (ADR: past values a source can rebuild are filled, never changed) ---


def test_past_rows_get_the_totals_they_lack_once_and_say_so(tmp_path: Path) -> None:
    """The scenario's reviewed rows hold the values; a second run finds nothing missing."""
    scenario = port.scenario(NAME, "past_rows_without_totals_are_filled_from_each_issues_dates")

    first = port.run(source, gi.leadline_config(scenario), scenario, tmp_path / "first")
    again = port.run(source, gi.leadline_config(scenario), replace(scenario, seed=first.rows), tmp_path / "again")

    assert f"Backfilled {FILE}: 3 row(s) filled, 0 added, 2026-09-17 to 2026-09-22" in first.log
    assert first.ctx.corrections == []  # a backfill, not a correction
    assert again.rows == first.rows
    assert not [line for line in again.log if line.startswith("Backfilled")]


def test_the_days_before_the_file_are_rebuilt_once_and_said_so(tmp_path: Path) -> None:
    """The scenario's reviewed rows hold the values; a second run finds nothing missing."""
    scenario = port.scenario(NAME, "a_file_starting_late_gets_rows_back_to_the_first_issue")

    first = port.run(source, gi.leadline_config(scenario), scenario, tmp_path / "first")
    again = port.run(source, gi.leadline_config(scenario), replace(scenario, seed=first.rows), tmp_path / "again")

    assert f"Backfilled {FILE}: 0 row(s) filled, 3 added, 2026-09-18 to 2026-09-20" in first.log
    assert first.ctx.corrections == []
    assert again.rows == first.rows
    assert not [line for line in again.log if line.startswith("Backfilled")]


def test_a_rebuilt_total_counts_what_the_runs_total_counts_pull_requests_aside(tmp_path: Path) -> None:
    """The day before the run, with nothing opened or closed since, rebuilds to the run's own totals."""
    done = run(tmp_path, gi.responses(gi.PIPELINE))  # nothing in it opened or closed on the run date
    eve = '{"date": "2026-09-22", "issues": {}}\n'
    filled = run(tmp_path / "filled", gi.responses(gi.PIPELINE), seed=eve)

    totals = {key: last_row(done)["issues"][key] for key in ("opened_total", "closed_total")}
    assert totals == {"opened_total": 9, "closed_total": 2}  # three of PIPELINE's twelve are pull requests
    assert json.loads(filled.rows[FILE].splitlines()[-2])["issues"] == totals  # the eve's row, the run date's after


def test_a_snapshot_without_totals_is_filled_before_the_flow_reads_it(tmp_path: Path) -> None:
    """The script gives no flow from it; leadline fills its totals first, so the flow is a day's."""
    scenario = port.scenario(NAME, "an_earlier_row_without_totals_gives_no_flow")
    lists = gi.stargazers(["2026-09-18T12:00:00Z"] * 12)  # its row's 12 stars; it has no forks to date

    done = port.run(source, [ENTRY], replace(scenario, responses=[*scenario.responses, *lists]), tmp_path)

    filled = '{"date": "2026-09-22", "repo": {"stars": 12}, "issues": {"opened_total": 3, "closed_total": 2}}\n'
    expected = gi.rebuilt((0, 0, 0)) + gi.flow_row("2026-09-21", 1, 0) + filled + gi.SMALL_TODAY
    assert without_counts(done.rows[FILE], "2026-09-22") == without_counts(expected, "2026-09-22")


def test_a_snapshot_lacking_its_closed_total_gets_it_and_the_flow_counts_from_it(tmp_path: Path) -> None:
    """The script reads the missing closed total as 0; leadline fills it with the day's, 1."""
    done = port.run(source, [ENTRY], port.scenario(NAME, "flow_since_the_previous_snapshot"), tmp_path)

    filled = '{"date": "2026-09-21", "issues": {"opened_total": 1, "closed_total": 1}}\n'
    flow = '"since_prev": {"days": 2, "opened": 2, "closed": 1}'
    today = gi.SMALL_TODAY.replace('"since_prev": {"days": 1, "opened": 0, "closed": 0}', flow)
    assert done.rows == {FILE: "not json\n" + gi.rebuilt((0, 0, 0)) + filled + today}


# --- stars and forks history (ADR: past values a source can rebuild are filled, never changed) ---

# Two past rows without the repo's counts, then the first with them, 09-22's (3 stars, 1 fork). SMALL's first issue
# is 09-18, GitHub's first star 09-17.
COUNTLESS = (
    '{"date": "2026-09-19", "issues": {"open": 1, "opened_total": 2, "closed_total": 0}}\n'
    '{"date": "2026-09-21", "issues": {"open": 1, "opened_total": 3, "closed_total": 2}}\n'
)
COUNTED = (
    '{"date": "2026-09-22", "repo": {"stars": 3, "forks": 1, "watchers": 0}, '
    '"issues": {"opened_total": 3, "closed_total": 2}}\n'
)
STARRED = ["2026-09-17T12:00:00Z", "2026-09-19T12:00:00Z", "2026-09-21T12:00:00Z"]
FORKED = ["2026-09-20T12:00:00Z"]


def by_day(done: port.PortRun) -> dict[str, dict[str, Any]]:
    return {row["date"]: row for row in map(json.loads, done.rows[FILE].splitlines())}


def counts(done: port.PortRun, field: str) -> dict[str, Any]:
    """Each day's `repo` count of `field`, where its row has one."""
    return {day: row["repo"][field] for day, row in by_day(done).items() if field in row.get("repo", {})}


def counting(stars: Sequence[str] = STARRED, forks: Sequence[str] = FORKED) -> list[Any]:
    """The run's answers when GitHub has the repo's stars and forks (today: as many as the lists)."""
    asked = [*(gi.stargazers(stars) if stars else []), *(gi.forks(forks) if forks else [])]  # none for a count of 0
    return [*gi.responses(gi.SMALL, stars=len(stars), forks=len(forks)), *asked]


def test_past_rows_without_stars_and_forks_get_them_from_the_dates_github_keeps(tmp_path: Path) -> None:
    done = run(tmp_path, counting(), seed=COUNTLESS + COUNTED)

    assert counts(done, "stars") == {
        "2026-09-17": 1, "2026-09-18": 1, "2026-09-19": 2, "2026-09-21": 3, "2026-09-22": 3, "2026-09-23": 3,
    }  # fmt: skip
    assert counts(done, "forks") == {
        "2026-09-17": 0, "2026-09-18": 0, "2026-09-19": 0, "2026-09-21": 1, "2026-09-22": 1, "2026-09-23": 1,
    }  # fmt: skip
    assert f"Backfilled {FILE}: 2 row(s) filled, 2 added, 2026-09-17 to 2026-09-21" in done.log
    assert done.ctx.corrections == []  # a backfill, not a correction


def test_a_filled_row_has_no_watchers_and_only_the_rows_written_for_a_missing_day_are_marked(tmp_path: Path) -> None:
    done = run(tmp_path, counting(), seed=COUNTLESS + COUNTED)

    rows = by_day(done)
    assert rows["2026-09-19"]["repo"] == {"stars": 2, "forks": 0}  # a row a night wrote: its added keys carry no mark
    assert "backfilled" not in rows["2026-09-19"]
    assert rows["2026-09-17"]["repo"] == {"stars": 1, "forks": 0}
    assert rows["2026-09-17"]["backfilled"] is True  # a row written for a day that had none
    assert rows["2026-09-19"]["issues"] == {"open": 1, "opened_total": 2, "closed_total": 0}  # the rest as it was


def test_the_first_row_with_counts_and_every_row_after_it_are_never_changed(tmp_path: Path) -> None:
    done = run(tmp_path, counting([*STARRED, "2026-09-22T09:00:00Z"]), seed=COUNTLESS + COUNTED)

    assert by_day(done)["2026-09-22"]["repo"] == {"stars": 3, "forks": 1, "watchers": 0}  # not the list's 4 that day


def test_a_second_run_asks_for_no_list_and_writes_nothing(tmp_path: Path) -> None:
    first = run(tmp_path / "first", counting(), seed=COUNTLESS + COUNTED)
    scenario = replace(port.scenario(NAME, "empty_repository"), responses=gi.responses(gi.SMALL, stars=3, forks=1))

    again = port.run(source, [ENTRY], replace(scenario, seed=first.rows), tmp_path / "again")  # no list to serve

    assert not [line for line in again.log if line.startswith("Backfilled")]
    assert again.rows[FILE].splitlines()[:-1] == first.rows[FILE].splitlines()[:-1]  # all but the run date's row


def test_a_file_with_no_row_that_has_the_counts_gets_none_and_asks_for_no_list(tmp_path: Path) -> None:
    done = run(tmp_path, gi.responses(gi.SMALL), seed=COUNTLESS)  # the run date's row is the first to have them

    assert counts(done, "stars") == {"2026-09-23": 12}


def test_every_page_of_the_stars_is_read(tmp_path: Path) -> None:
    many = [f"2026-09-{day:02d}T12:00:00Z" for day in [17] * 101]  # two pages: 100, then 1
    seed = COUNTLESS + '{"date": "2026-09-22", "repo": {"stars": 101, "forks": 1}, "issues": {"opened_total": 3}}\n'

    done = run(tmp_path, counting(many), seed=seed)

    assert counts(done, "stars")["2026-09-21"] == 101


def test_a_star_or_fork_counts_on_its_day_in_the_runs_offset(tmp_path: Path) -> None:
    late = "2026-09-20T03:00:00Z"  # the 19th, 20:00 in Los Angeles
    scenario = replace(port.scenario(NAME, "empty_repository"), tz="America/Los_Angeles", now="2026-09-23T23:31:05")

    done = port.run(
        source,
        [ENTRY],
        replace(scenario, responses=counting([late], []), seed={FILE: COUNTLESS + COUNTED.replace("3,", "1,")}),
        tmp_path,
    )

    assert {day: stars for day, stars in counts(done, "stars").items() if day < "2026-09-22"} == {
        "2026-09-18": 0, "2026-09-19": 1, "2026-09-21": 1,
    }  # fmt: skip


def test_the_stars_are_filled_when_the_forks_cannot_be_read_and_the_forks_the_next_night(tmp_path: Path) -> None:
    bad = [exchange(f"{gi.REPO_URL}/forks?sort=oldest&per_page=100&page=1", {"message": "no"})]
    first = run(tmp_path / "first", [*gi.responses(gi.SMALL, stars=3, forks=1), *gi.stargazers(STARRED), *bad],
                seed=COUNTLESS + COUNTED)  # fmt: skip

    assert counts(first, "stars")["2026-09-19"] == 2
    assert "forks" not in by_day(first)["2026-09-19"]["repo"]
    assert f"Left the forks of {FILE} unfilled: forks response had an unexpected shape" in first.log
    assert last_row(first)["repo"] == {"stars": 3, "forks": 1, "watchers": 4}  # the run's own row is written
    scenario = replace(port.scenario(NAME, "empty_repository"), seed=first.rows)
    next_night = [*gi.responses(gi.SMALL, stars=3, forks=1), *gi.forks(FORKED)]  # only the forks are asked for

    second = port.run(source, [ENTRY], replace(scenario, responses=next_night), tmp_path / "second")

    assert counts(second, "forks") == {"2026-09-17": 0, "2026-09-18": 0, "2026-09-19": 0, "2026-09-21": 1,
                                       "2026-09-22": 1, "2026-09-23": 1}  # fmt: skip


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        ({"message": "no"}, "stars response had an unexpected shape"),
        ([{"user": {}}], "stars response had an unexpected shape"),  # no starred_at
        ([{"starred_at": "yesterday"}], "stars response had an unexpected shape"),
        (["2026-09-17T12:00:00Z"], "stars response had an unexpected shape"),
    ],
    ids=["not a list", "no time", "not a time", "not an object"],
)
def test_a_list_it_cannot_read_leaves_the_rows_unfilled_and_costs_the_run_no_row(
    tmp_path: Path, answer: Any, reason: str
) -> None:
    stars = [exchange(f"{gi.REPO_URL}/stargazers?per_page=100&page=1", answer)]
    responses = [*gi.responses(gi.SMALL, stars=3, forks=1), *stars, *gi.forks(FORKED)]

    done = run(tmp_path, responses, seed=COUNTLESS + COUNTED)

    assert f"Left the stars of {FILE} unfilled: {reason}" in done.log
    assert "stars" not in by_day(done)["2026-09-19"].get("repo", {})
    assert last_row(done)["repo"]["stars"] == 3  # the run's own row is written
    assert "status" not in last_row(done)
    assert done.ctx.critical == []


def test_a_failed_request_for_the_stars_leaves_the_rows_unfilled_and_costs_the_run_no_row(tmp_path: Path) -> None:
    url = f"{gi.REPO_URL}/stargazers?per_page=100&page=1"
    responses = [*gi.responses(gi.SMALL, stars=3, forks=1), failed(url), *gi.forks(FORKED)]

    done = run(tmp_path, responses, seed=COUNTLESS + COUNTED)

    assert any(line.startswith(f"Left the stars of {FILE} unfilled: HTTP 0") for line in done.log)
    assert "stars" not in by_day(done)["2026-09-19"].get("repo", {})
    assert counts(done, "forks")["2026-09-19"] == 0
    assert last_row(done)["repo"]["stars"] == 3


def test_a_repo_with_no_stars_or_forks_has_zeros_filled_without_asking(tmp_path: Path) -> None:
    seed = COUNTLESS + '{"date": "2026-09-22", "repo": {"stars": 0, "forks": 0}, "issues": {"opened_total": 3}}\n'

    done = run(tmp_path, gi.responses(gi.SMALL, stars=0, forks=0), seed=seed)

    assert counts(done, "stars")["2026-09-19"] == counts(done, "forks")["2026-09-21"] == 0


def test_a_list_too_long_to_read_is_left_unfilled_and_not_asked_for(tmp_path: Path) -> None:
    limit = source.HISTORY_LIMIT
    seed = COUNTLESS + '{"date": "2026-09-22", "repo": {"stars": 3, "forks": 1}, "issues": {"opened_total": 3}}\n'

    done = run(tmp_path, [*gi.responses(gi.SMALL, stars=limit + 1, forks=1), *gi.forks(FORKED)], seed=seed)

    assert f"Left the stars of {FILE} unfilled: {limit + 1} stars are more than the {limit} it reads" in done.log
    assert "stars" not in by_day(done)["2026-09-19"].get("repo", {})
    assert counts(done, "forks")["2026-09-19"] == 0


def test_a_failure_row_is_left_as_it_is(tmp_path: Path) -> None:
    failure = '{"date": "2026-09-20", "status": "fetch_failed", "http_code": "502", "reason": "x"}\n'

    done = run(tmp_path, counting(), seed=COUNTLESS + failure + COUNTED)

    assert by_day(done)["2026-09-20"] == json.loads(failure)


HTTP_LINES = ("WARN: attempt", "Reused the answer")  # the client names each request it logs, path and all

# --- stars and forks by region (ADR: stars and forks are counted by region, never by person) ---

REGIONAL = {
    "text": [
        {"region": "DE", "keywords": ["germany"]},
        {"region": "US", "keywords": ["illinois"]},
        {"region": "CA", "keywords": ["canada"]},
    ],
    "known": {"user-4": "CA", "user-5": "CA"},
}
BY_REGION = {**ENTRY, "stars_and_forks": True, "regions": REGIONAL}
# The stargazers and forker of STARRED and FORKED, where each lives; user-2 sets none.
HOMES = {"user-0": "Zedland, Germany", "user-1": "Springfield, Illinois", "user-2": None, "forker-0": "Berlin, Germany"}
# The days the file's first row and the one before the run have, for a run that asks for no history: a night wrote
# the first, with no stars, and the other has the counts by region as of the end of its day.
FIRST = '{"date": "2026-09-18", "repo": {"stars": 0, "forks": 0, "stars_by_region": {}, "forks_by_region": {}}}\n'


def regional(stars: dict[str, int], forks: dict[str, int] | None = None, stats: tuple[int, int] = (0, 0)) -> str:
    """The row of 2026-09-22 with the repo's counts by region, as a night wrote it."""
    repo = {"stars": stats[0], "forks": stats[1], "stars_by_region": stars, "forks_by_region": forks or {}}
    return json.dumps({"date": "2026-09-22", "repo": repo, "issues": {"opened_total": 3, "closed_total": 2}}) + "\n"


def regions_of(done: port.PortRun, field: str) -> dict[str, Any]:
    """Each day's `repo` counts by region of `field` (`stars`), where its row has them."""
    return {
        day: row["repo"][f"{field}_by_region"]
        for day, row in by_day(done).items()
        if f"{field}_by_region" in row["repo"]
    }


def with_people(stars: Sequence[str] = STARRED, forks: Sequence[str] = FORKED, homes: Any = HOMES) -> list[Any]:
    """The run's answers for the repo's stars and forks, and the profiles of the people in `homes`."""
    return [*counting(stars, forks), *gi.profiles(homes)]


def test_each_days_stars_and_forks_are_counted_by_the_region_of_their_people_and_unplaced_ones_are_unknown(
    tmp_path: Path,
) -> None:
    done = run(tmp_path, with_people(), [BY_REGION], seed=COUNTLESS + COUNTED)

    one, two, three = {"DE": 1}, {"DE": 1, "US": 1}, {"DE": 1, "US": 1, "unknown": 1}
    assert regions_of(done, "stars") == {
        "2026-09-17": one, "2026-09-18": one, "2026-09-19": two, "2026-09-21": three, "2026-09-22": three,
        "2026-09-23": three,
    }  # fmt: skip
    assert regions_of(done, "forks") == {
        "2026-09-17": {}, "2026-09-18": {}, "2026-09-19": {}, "2026-09-21": one, "2026-09-22": one, "2026-09-23": one,
    }  # fmt: skip
    assert last_row(done)["repo"] == {
        "stars": 3, "forks": 1, "watchers": 4, "stars_by_region": three, "forks_by_region": one,
    }  # fmt: skip
    assert done.ctx.corrections == []  # a fill, not a correction


def test_a_filled_row_gets_the_counts_with_no_backfilled_mark_unless_the_day_had_no_row(tmp_path: Path) -> None:
    done = run(tmp_path, with_people(), [BY_REGION], seed=COUNTLESS + COUNTED)

    rows = by_day(done)
    assert "backfilled" not in rows["2026-09-19"]  # a row a night wrote, with a field added
    assert rows["2026-09-19"]["repo"] == {
        "stars": 2, "forks": 0, "stars_by_region": {"DE": 1, "US": 1}, "forks_by_region": {},
    }  # fmt: skip
    assert rows["2026-09-17"]["backfilled"] is True  # a row written for a day that had none
    assert rows["2026-09-22"]["repo"]["watchers"] == 0  # the first row with counts keeps what it had


def test_no_login_is_written_to_a_row_or_the_log(tmp_path: Path) -> None:
    done = run(tmp_path, with_people(), [BY_REGION], seed=COUNTLESS + COUNTED)

    for login in HOMES:
        assert login not in done.rows[FILE]
        assert not [line for line in done.log if login in line]
    assert not [line for line in done.log if "Germany" in line or "Illinois" in line]  # nor a location


def test_a_person_who_starred_and_forked_costs_one_profile_request(tmp_path: Path) -> None:
    both = [*counting(STARRED, FORKED)[:-1], *gi.forks(FORKED, owners=["user-0"])]  # the fork is user-0's
    homes = {"user-0": "Zedland, Germany", "user-1": "Springfield, Illinois", "user-2": None}

    done = run(tmp_path, [*both, *gi.profiles(homes)], [BY_REGION], seed=COUNTLESS + COUNTED)

    assert regions_of(done, "forks")["2026-09-23"] == {"DE": 1}


def test_a_profile_no_rule_reads_is_unknown_and_a_known_region_places_it_but_never_over_a_profile(
    tmp_path: Path,
) -> None:
    stars = ["2026-09-21T12:00:00Z"] * 6
    homes = {
        "user-0": "Zedland, Germany",
        "user-1": None,
        "user-2": "Atlantis",  # a location no rule reads
        "user-3": None,
        "user-4": "Atlantis",  # known: CA
        "user-5": "Springfield, Illinois",  # known: CA, but its profile says US
    }
    gone = exchange(f"{GITHUB_API}/users/user-3", '{"message":"Not Found"}', status=404)  # no profile, no location
    answers = [*counting(stars, []), *gi.profiles({k: v for k, v in homes.items() if k != "user-3"}), gone]
    seed = '{"date": "2026-09-22", "repo": {"stars": 6, "forks": 0}, "issues": {"opened_total": 3}}\n'

    done = run(tmp_path, answers, [BY_REGION], seed=seed)

    assert last_row(done)["repo"]["stars_by_region"] == {"unknown": 3, "DE": 1, "CA": 1, "US": 1}
    assert "WARN: a known region says CA where a profile says US (1)" in done.log
    assert not [line for line in done.log if "user-" in line and not line.startswith(HTTP_LINES)]


def test_a_second_night_with_nobody_new_reads_the_lists_and_no_profile(tmp_path: Path) -> None:
    first = run(tmp_path / "first", with_people(), [BY_REGION], seed=COUNTLESS + COUNTED)
    scenario = replace(port.scenario(NAME, "empty_repository"), responses=counting())

    again = port.run(source, [BY_REGION], replace(scenario, seed=first.rows), tmp_path / "again")  # no profile served

    assert again.rows[FILE].splitlines()[:-1] == first.rows[FILE].splitlines()[:-1]
    assert last_row(again)["repo"] == last_row(first)["repo"]


def test_a_new_star_is_read_alone_and_added_to_the_counts_of_the_night_before(tmp_path: Path) -> None:
    seed = FIRST + regional({"DE": 2}, stats=(2, 0))
    stars = ["2026-09-19T12:00:00Z", "2026-09-20T12:00:00Z", "2026-09-23T08:00:00Z"]  # the third is today's
    answers = [*counting(stars, []), *gi.profiles({"user-2": "Springfield, Illinois"})]  # only the newcomer's

    done = run(tmp_path, answers, [BY_REGION], seed=seed)

    assert last_row(done)["repo"]["stars_by_region"] == {"DE": 2, "US": 1}
    assert regions_of(done, "stars")["2026-09-22"] == {"DE": 2}  # the night before keeps what it had


def test_a_star_removed_since_stays_counted_and_no_profile_is_read(tmp_path: Path) -> None:
    seed = FIRST + regional({"DE": 2}, stats=(2, 0))

    done = run(tmp_path, counting(["2026-09-19T12:00:00Z"], []), [BY_REGION], seed=seed)

    assert last_row(done)["repo"]["stars_by_region"] == {"DE": 2}


def test_a_new_star_is_counted_though_another_was_removed_and_the_total_is_the_same(tmp_path: Path) -> None:
    seed = FIRST + regional({"DE": 2}, stats=(2, 0))
    stars = ["2026-09-19T12:00:00Z", "2026-09-23T08:00:00Z"]  # one of the two counted left, one is new
    answers = [*counting(stars, []), *gi.profiles({"user-1": "Springfield, Illinois"})]

    done = run(tmp_path, answers, [BY_REGION], seed=seed)

    assert last_row(done)["repo"]["stars_by_region"] == {"DE": 2, "US": 1}


def test_a_run_reads_whole_days_of_profiles_up_to_its_limit_and_the_next_night_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(regions, "PROFILE_LIMIT", 2)
    first = run(
        tmp_path / "first",
        [*counting(), *gi.profiles({k: v for k, v in HOMES.items() if k in ("user-0", "user-1")})],  # not the rest
        [BY_REGION],
        seed=COUNTLESS + COUNTED,
    )

    assert regions_of(first, "stars") == {
        "2026-09-17": {"DE": 1}, "2026-09-18": {"DE": 1}, "2026-09-19": {"DE": 1, "US": 1},
    }  # fmt: skip
    assert regions_of(first, "forks") == {"2026-09-17": {}, "2026-09-18": {}, "2026-09-19": {}}  # none before 09-20
    assert "stars_by_region" not in last_row(first)["repo"]  # the run's row is written without them
    assert any("waits for the next run" in line for line in first.log)
    scenario = replace(port.scenario(NAME, "empty_repository"), seed=first.rows)
    left = {k: v for k, v in HOMES.items() if k in ("user-2", "forker-0")}

    second = port.run(source, [BY_REGION], replace(scenario, responses=with_people(homes=left)), tmp_path / "second")

    assert regions_of(second, "stars")["2026-09-23"] == {"DE": 1, "US": 1, "unknown": 1}
    assert regions_of(second, "forks")["2026-09-21"] == {"DE": 1}
    assert regions_of(second, "stars")["2026-09-22"] == {"DE": 1, "US": 1, "unknown": 1}


def test_a_day_with_more_people_than_the_limit_is_still_read_whole_so_a_run_always_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(regions, "PROFILE_LIMIT", 1)
    stars = ["2026-09-17T12:00:00Z", "2026-09-17T13:00:00Z", "2026-09-19T12:00:00Z"]
    homes = {"user-0": "Zedland, Germany", "user-1": "Springfield, Illinois"}  # the third waits

    done = run(tmp_path, [*counting(stars, []), *gi.profiles(homes)], [BY_REGION], seed=COUNTLESS + COUNTED)

    assert regions_of(done, "stars") == {"2026-09-17": {"DE": 1, "US": 1}, "2026-09-18": {"DE": 1, "US": 1}}


def test_a_profile_that_fails_stops_the_counts_at_its_day_and_costs_the_run_no_row(tmp_path: Path) -> None:
    broken = failed(f"{GITHUB_API}/users/user-1")
    answers = [*counting(), *gi.profiles({"user-0": "Zedland, Germany", "forker-0": "Berlin, Germany"}), broken]

    done = run(tmp_path, answers, [BY_REGION], seed=COUNTLESS + COUNTED)

    assert regions_of(done, "stars") == {"2026-09-17": {"DE": 1}, "2026-09-18": {"DE": 1}}
    assert any(
        line.startswith("Stopped the stars by region of") and "at 2026-09-19: HTTP 0" in line for line in done.log
    )
    assert not [line for line in done.log if "user-" in line and not line.startswith(HTTP_LINES)]
    assert last_row(done)["repo"]["stars"] == 3
    assert done.ctx.critical == []


def test_a_list_it_cannot_read_leaves_the_counts_by_region_for_the_next_night(tmp_path: Path) -> None:
    seed = FIRST + '{"date": "2026-09-22", "repo": {"stars": 3, "forks": 0}, "issues": {"opened_total": 3}}\n'
    url = f"{gi.REPO_URL}/stargazers?per_page=100&page=1"

    done = run(tmp_path, [*gi.responses(gi.SMALL, stars=3, forks=0), failed(url)], [BY_REGION], seed=seed)

    assert any(line.startswith(f"Left the stars by region of {FILE} unfilled: HTTP 0") for line in done.log)
    assert "stars_by_region" not in last_row(done)["repo"]
    assert last_row(done)["repo"]["forks_by_region"] == {}  # nothing to read for no forks


def test_a_list_too_long_to_read_is_left_without_counts_by_region_and_not_asked_for(tmp_path: Path) -> None:
    limit = source.HISTORY_LIMIT
    seed = FIRST + '{"date": "2026-09-22", "repo": {"stars": 3, "forks": 0}, "issues": {"opened_total": 3}}\n'

    done = run(tmp_path, gi.responses(gi.SMALL, stars=limit + 1, forks=0), [BY_REGION], seed=seed)

    assert (
        f"Left the stars by region of {FILE} unfilled: {limit + 1} stars are more than the {limit} it reads" in done.log
    )
    assert "stars_by_region" not in last_row(done)["repo"]


def test_a_repo_with_no_stars_or_forks_has_empty_counts_by_region_without_asking(tmp_path: Path) -> None:
    seed = FIRST + '{"date": "2026-09-22", "repo": {"stars": 0, "forks": 0}, "issues": {"opened_total": 3}}\n'

    done = run(tmp_path, gi.responses(gi.SMALL, stars=0, forks=0), [BY_REGION], seed=seed)

    assert regions_of(done, "stars")["2026-09-22"] == regions_of(done, "forks")["2026-09-23"] == {}


@pytest.mark.parametrize(
    "entry", [ENTRY, {**ENTRY, "stars_and_forks": True}, {**ENTRY, "regions": REGIONAL, "queue": {"label": "x"}}]
)
def test_without_both_stars_and_forks_and_a_region_table_the_rows_have_no_counts_by_region(
    tmp_path: Path, entry: dict[str, Any]
) -> None:
    done = run(tmp_path, counting(), [entry], seed=COUNTLESS + COUNTED)  # no profile is served, so none is asked for

    assert "_by_region" not in done.rows[FILE]


def test_an_entry_with_a_region_table_charts_the_same_counts_as_one_without() -> None:
    plain, second = source.summary([BY_REGION])

    assert plain.shown == source.summary([ENTRY])[0].shown
    assert [(s.field, s.kind, s.into) for s in second.shown] == [
        ("repo.stars", "snapshot", ""),
        ("repo.forks", "snapshot", ""),
    ]
    assert second.shown == source.summary([{**ENTRY, "stars_and_forks": True}])[1].shown
    assert second.card == github_spec.STARS_AND_FORKS


# --- opened by kind (ADR: the issues rows count what was opened by kind) ---

WITH_KINDS = {**ENTRY, "kinds": gi.KINDS}
KIND_SCENARIO = gi.BY_KIND[0]


def test_each_issue_opened_is_counted_once_under_the_first_kind_whose_label_it_carries(tmp_path: Path) -> None:
    done = run(tmp_path, gi.responses(gi.KIND_ITEMS), [WITH_KINDS], seed="")

    issues = last_row(done)["issues"]
    assert issues["opened_by_kind"] == {"new_modem": 1, "defect": 1, "other": 1}  # issue 2 has both labels
    assert sum(issues["opened_by_kind"].values()) == issues["opened_total"] == 3  # the pull request isn't counted
    assert list(issues)[-3:] == ["since_prev", "opened_by_kind", "open_by_kind"]


def test_the_kinds_are_the_configs_in_its_order_and_other_is_always_last(tmp_path: Path) -> None:
    kinds = [{"name": "defect", "label": "bug"}, {"name": "docs", "label": "docs"}]

    done = run(tmp_path, gi.responses(gi.KIND_ITEMS), [{**ENTRY, "kinds": kinds}], seed="")

    assert last_row(done)["issues"]["opened_by_kind"] == {"defect": 2, "docs": 1, "other": 0}


def test_the_open_issues_are_counted_by_kind_as_the_opened_are_and_add_up_to_open(tmp_path: Path) -> None:
    done = run(tmp_path, gi.responses(gi.KIND_ITEMS), [WITH_KINDS], seed="")

    issues = last_row(done)["issues"]
    assert issues["open"] == 1  # issue 1; the pull request isn't one
    assert issues["open_by_kind"] == {"new_modem": 0, "defect": 1, "other": 0}
    assert list(issues)[-2:] == ["opened_by_kind", "open_by_kind"]
    assert sum(issues["open_by_kind"].values()) == issues["open"]


def test_the_open_kinds_are_the_configs_in_its_order_and_other_is_always_last(tmp_path: Path) -> None:
    kinds = [{"name": "docs", "label": "docs"}, {"name": "defect", "label": "bug"}]

    done = run(tmp_path, gi.responses(gi.KIND_ITEMS), [{**ENTRY, "kinds": kinds}], seed="")

    assert last_row(done)["issues"]["open_by_kind"] == {
        "docs": 1,
        "defect": 0,
        "other": 0,
    }  # bug and docs: the first kind listed


def past_row(day: str, open_: int | None, by_kind: str = "") -> str:
    """A written row of `day` with the opened counts for KIND_ITEMS, and `open` and the open kinds as given."""
    opened = {"2026-09-18": 1, "2026-09-19": 2, "2026-09-20": 3}.get(day, 3)
    closed = {"2026-09-21": 1, "2026-09-22": 2}.get(day, 0)
    issues = {
        "opened_total": opened,
        "closed_total": closed,
        "opened_by_kind": {"new_modem": 0, "defect": 0, "other": 0},
    }
    if open_ is not None:
        issues = {"open": open_, **issues}
    row = json.dumps({"date": day, "issues": issues, "captured_at": f"{day}T23:30:00+00:00"})
    return row.replace("}}", "}" + by_kind + "}") + "\n" if by_kind else row + "\n"


def test_a_past_row_gets_the_open_issues_by_kind_of_its_day_as_a_correction(tmp_path: Path) -> None:
    """Open on a day is created by its end and not closed by it, each issue under its kind as the opened are counted;
    the kinds add up to the row's `open`, and the field is the last of `issues`."""
    seed = past_row("2026-09-19", 2) + past_row("2026-09-21", 2)

    done = run(tmp_path, gi.responses(gi.KIND_ITEMS), [WITH_KINDS], seed=seed)

    days = kind_rows(done)
    assert days["2026-09-19"]["open_by_kind"] == {"new_modem": 1, "defect": 1, "other": 0}
    assert days["2026-09-21"]["open_by_kind"] == {"new_modem": 1, "defect": 1, "other": 0}  # issue 3 closed that day
    assert all(list(issues)[-1] == "open_by_kind" for issues in (days["2026-09-19"], days["2026-09-21"]))
    assert all(
        sum(issues["open_by_kind"].values()) == issues["open"] for issues in (days["2026-09-19"], days["2026-09-21"])
    )
    corrected = {c.date: c for c in done.ctx.corrections}
    assert "open_by_kind" not in corrected["2026-09-19"].old["issues"]
    assert corrected["2026-09-19"].new["issues"]["open_by_kind"] == days["2026-09-19"]["open_by_kind"]


def test_a_past_row_whose_open_the_dates_cant_match_is_left_unfilled_and_reported(tmp_path: Path) -> None:
    """Issue 3 was open on the 20th, so 2 is not that day's `open`; a row without `open` can't be matched either."""
    seed = past_row("2026-09-20", 2) + past_row("2026-09-21", None) + past_row("2026-09-22", 1)

    done = run(tmp_path, gi.responses(gi.KIND_ITEMS), [WITH_KINDS], seed=seed)

    days = kind_rows(done)
    assert "open_by_kind" not in days["2026-09-20"]
    assert "open_by_kind" not in days["2026-09-21"]
    assert days["2026-09-22"]["open_by_kind"] == {"new_modem": 0, "defect": 1, "other": 0}
    assert any("2 past row(s)" in line and "2026-09-20" in line for line in done.log)


def test_a_past_rows_open_by_kind_is_counted_again_under_the_kinds_the_config_has_now(tmp_path: Path) -> None:
    kinds = [{"name": "docs", "label": "docs"}, {"name": "defect", "label": "bug"}]
    old = ', "open_by_kind": {"new_modem": 1, "defect": 1, "other": 0}'
    seed = past_row("2026-09-19", 2, old)

    done = run(tmp_path, gi.responses(gi.KIND_ITEMS), [{**ENTRY, "kinds": kinds}], seed=seed)

    assert kind_rows(done)["2026-09-19"]["open_by_kind"] == {"docs": 1, "defect": 1, "other": 0}
    assert {c.date for c in done.ctx.corrections} == {"2026-09-19"}  # its opened and its open counts, each a pass


def test_the_rows_rebuilt_before_the_first_have_their_open_by_kind(tmp_path: Path) -> None:
    done = run(tmp_path, gi.responses(gi.KIND_ITEMS), [WITH_KINDS], seed=past_row("2026-09-21", 2))

    rebuilt = [i for day, i in kind_rows(done).items() if day < "2026-09-21"]
    assert [i["open_by_kind"] for i in rebuilt] == [
        {"new_modem": 0, "defect": 1, "other": 0},
        {"new_modem": 1, "defect": 1, "other": 0},
        {"new_modem": 1, "defect": 1, "other": 1},
    ]
    assert all(sum(i["open_by_kind"].values()) == i["open"] for i in rebuilt)


def test_an_entry_without_kinds_writes_no_field(tmp_path: Path) -> None:
    done = run(tmp_path, gi.responses(gi.KIND_ITEMS), seed="2026-09-22")

    assert "opened_by_kind" not in done.rows[FILE]
    assert "open_by_kind" not in done.rows[FILE]


def test_past_rows_get_the_counts_by_kind_they_lack_once_and_say_so(tmp_path: Path) -> None:
    """The scenario's reviewed rows hold the values: a value a row has, null included, and a failure row stay."""
    scenario = port.scenario(NAME, KIND_SCENARIO)

    first = port.run(source, gi.leadline_config(scenario), scenario, tmp_path / "first")
    again = port.run(source, gi.leadline_config(scenario), replace(scenario, seed=first.rows), tmp_path / "again")

    assert f"Backfilled {FILE}: 1 row(s) filled, 2 added, 2026-09-18 to 2026-09-20" in first.log
    assert first.ctx.corrections == []  # a backfill, not a correction
    assert again.rows == first.rows
    assert not [line for line in again.log if line.startswith("Backfilled")]


THREE_KINDS = [*gi.KINDS, {"name": "enhancement", "label": "enhancement"}]
ENHANCED = [*gi.KIND_ITEMS, gi.issue(5, author="u", created_at="2026-09-19T11:00:00Z", labels=["enhancement"])]


def kind_rows(done: port.PortRun) -> dict[str, dict[str, Any]]:
    return {row["date"]: row["issues"] for row in map(json.loads, done.rows[FILE].splitlines()) if "issues" in row}


def test_past_rows_are_counted_again_under_the_kinds_the_config_has_now(tmp_path: Path) -> None:
    """A row written under two kinds, with the third's issues in other, is recomputed: each issue once."""
    seed = (
        '{"date": "2026-09-19", "issues": {"opened_total": 3, "closed_total": 0, "extra": 7, "opened_by_kind": '
        '{"new_modem": 1, "defect": 1, "other": 1}}, "captured_at": "2026-09-19T23:30:00+00:00"}\n'
    )

    done = run(tmp_path, gi.responses(ENHANCED), [{**ENTRY, "kinds": THREE_KINDS}], seed=seed)

    issues = kind_rows(done)["2026-09-19"]
    assert issues["opened_by_kind"] == {"new_modem": 1, "defect": 1, "enhancement": 1, "other": 0}
    assert list(issues["opened_by_kind"]) == ["new_modem", "defect", "enhancement", "other"]
    assert sum(issues["opened_by_kind"].values()) == issues["opened_total"]
    assert issues["extra"] == 7  # the rest of the row is kept
    (correction,) = [c for c in done.ctx.corrections if c.date == "2026-09-19"]
    assert correction.source == NAME
    assert correction.old["issues"]["opened_by_kind"] == {"new_modem": 1, "defect": 1, "other": 1}
    assert correction.new["issues"]["opened_by_kind"] == issues["opened_by_kind"]


STALE = (
    '{"date": "2026-09-20", "issues": {"opened_total": 4, "closed_total": 0, "opened_by_kind": '
    '{"new_modem": 1, "defect": 1, "other": 2}}}\n'
    '{"date": "2026-09-21", "issues": {"opened_total": 4, "closed_total": 1, "opened_by_kind": '
    '{"new_modem": 1, "defect": 1, "other": 2}}}\n'
)


def test_every_past_days_kinds_add_up_to_its_opened_total(tmp_path: Path) -> None:
    done = run(tmp_path, gi.responses(ENHANCED), [{**ENTRY, "kinds": THREE_KINDS}], seed=STALE)

    days = kind_rows(done)
    assert days
    for issues in days.values():
        assert sum(issues["opened_by_kind"].values()) == issues["opened_total"]


def test_a_null_count_by_kind_stays_for_the_fill_and_is_no_correction(tmp_path: Path) -> None:
    seed = '{"date": "2026-09-20", "issues": {"opened_total": 4, "closed_total": 0, "opened_by_kind": null}}\n'

    done = run(tmp_path, gi.responses(ENHANCED), [{**ENTRY, "kinds": THREE_KINDS}], seed=seed)

    assert kind_rows(done)["2026-09-20"]["opened_by_kind"] is None
    assert [c for c in done.ctx.corrections if c.date != "2026-09-23"] == []


def test_a_row_already_counted_under_the_kinds_is_no_correction_and_is_left_as_it_is(tmp_path: Path) -> None:
    kinds = [{**ENTRY, "kinds": THREE_KINDS}]
    first = run(tmp_path / "first", gi.responses(ENHANCED), kinds, seed=STALE)
    scenario = replace(port.scenario(NAME, "empty_repository"), responses=gi.responses(ENHANCED), seed=first.rows)

    again = port.run(source, kinds, scenario, tmp_path / "again")

    assert again.rows[FILE].splitlines()[:-1] == first.rows[FILE].splitlines()[:-1]  # all but the run date's row
    assert not [line for line in again.log if line.startswith("Recomputed")]
    assert [c for c in again.ctx.corrections if c.date != "2026-09-23"] == []


def test_a_filled_day_counts_the_issues_opened_by_the_end_of_it_in_the_runs_offset(tmp_path: Path) -> None:
    late = gi.issue(5, author="example-user", created_at="2026-09-21T03:00:00Z", labels=["bug"])  # the 20th, 20:00
    scenario = replace(port.scenario(NAME, KIND_SCENARIO), tz="America/Los_Angeles", now="2026-09-23T23:31:05")

    done = port.run(
        source,
        gi.leadline_config(port.scenario(NAME, KIND_SCENARIO)),
        replace(scenario, responses=gi.responses([*gi.KIND_ITEMS, late]), seed={FILE: gi.KIND_SEED}),
        tmp_path,
    )

    rows = [json.loads(line) for line in done.rows[FILE].splitlines()]
    by_day = {row["date"]: row["issues"].get("opened_by_kind") for row in rows if "issues" in row}
    assert by_day["2026-09-20"] == {"new_modem": 1, "defect": 2, "other": 1}


def test_its_summary_shows_the_counts_by_kind_as_breakdowns_only_with_kinds() -> None:
    (plain,) = source.summary([ENTRY])
    (with_kinds,) = source.summary([WITH_KINDS])

    assert [shown.field for shown in plain.shown] == ["issues.open", "issues.open_prs", "issues.opened_total"]
    assert [shown.field for shown in with_kinds.shown[:3]] == [shown.field for shown in plain.shown]
    assert with_kinds.shown[3:] == (
        config.Shown("issues.opened_by_kind", "Opened by kind", "breakdown", half=True),
        config.Shown("issues.open_by_kind", "Open by kind", "breakdown", half=True),
    )


def test_its_issues_opened_and_open_lines_are_charted_only_as_the_tops_of_the_kinds_stacks() -> None:
    (plain,) = source.summary([ENTRY])
    (with_kinds,) = source.summary([WITH_KINDS])

    assert (plain.shown[0].into, plain.shown[2].into) == ("", "")
    assert with_kinds.shown[0].into == "issues.open_by_kind"
    assert with_kinds.shown[2].into == "issues.opened_by_kind"


def test_an_entry_with_stars_and_forks_has_a_second_section_of_the_two_counts_in_the_traffic_card() -> None:
    plain, second = source.summary([{**ENTRY, "stars_and_forks": True}])

    assert plain.title == f"github_issues: {REPO}"
    assert second.title == f"github_issues: {REPO} stars"
    assert (second.name, second.file, second.label) == (REPO, plain.file, "Issues")
    assert [(shown.field, shown.label) for shown in second.shown] == [("repo.stars", "Stars"), ("repo.forks", "Forks")]
    assert second.card == github_spec.STARS_AND_FORKS


def test_an_entry_without_stars_and_forks_has_one_section_whether_the_setting_is_off_or_absent() -> None:
    for entry in (ENTRY, {**ENTRY, "stars_and_forks": False}):
        (section,) = source.summary([entry])
        assert (section.card, section.part) == (None, "")


def test_the_open_pull_requests_are_not_charted_with_or_without_kinds() -> None:
    for entry in (ENTRY, WITH_KINDS):
        (section,) = source.summary([entry])
        assert not section.shown[1].charted


def test_the_default_summary_shows_the_counts_by_kind(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, KIND_SCENARIO)

    lines = port.summary(source, gi.leadline_config(scenario), KIND_SCENARIO, tmp_path)

    assert lines[-2:] == [
        "- Opened by kind: new_modem 1 · defect 1 · other 1",
        "- Open by kind: defect 1 · new_modem 0 · other 0",
    ]


def test_trends_carry_the_counts_by_kind_as_a_breakdown_of_counts(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, KIND_SCENARIO)
    done = port.run(source, gi.leadline_config(scenario), scenario, tmp_path)
    (tmp_path / "health.jsonl").write_text("", encoding="utf-8")

    data = trends.build(tmp_path, date(2026, 9, 23), source.summary(gi.leadline_config(scenario)), "health.jsonl")

    (section,) = data["sections"]
    field = next(f for f in section["fields"] if f["field"] == "issues.opened_by_kind")
    assert done.status == 0
    assert (field["kind"], field["versions"], field["keys"]) == ("breakdown", False, ["new_modem", "defect", "other"])
    assert section["dates"] == ["2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21", "2026-09-23"]
    assert field["values"] == [[0, 1, 1, None, 1], [1, 1, 1, None, 1], [0, 0, 1, None, 1]]  # a null row has no counts
    assert field["other"] == [0, 0, 0, None, 0]


# --- decided divergences: every request is required (ADR: a failure is recorded as a failure) ---


def test_a_repo_not_found_is_a_fetch_failed_row_and_critical(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "repo_not_found_writes_nothing")

    done = port.run(source, gi.leadline_config(scenario), scenario, tmp_path)

    assert done.rows == {FILE: gi.YESTERDAY + failure_row("fetch_failed", "repository request failed", "404")}
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP 404 from GitHub's repository API")]
    assert done.status == 1


def test_a_failed_open_page_is_a_fetch_failed_row(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "failed_open_page_writes_nothing")

    done = port.run(source, gi.leadline_config(scenario), scenario, tmp_path)

    assert done.rows == {FILE: gi.YESTERDAY + failure_row("fetch_failed", "open issues request failed", "500")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_failed_later_page_is_a_fetch_failed_row(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "failed_later_page_writes_nothing")

    done = port.run(source, gi.leadline_config(scenario), scenario, tmp_path)

    assert done.rows == {FILE: gi.YESTERDAY + failure_row("fetch_failed", "all issues request failed", "000")}
    assert "ERROR: Failed to fetch GitHub all issues for solentlabs/example: HTTP 000" in done.log


def test_an_unparseable_repository_is_a_parse_failed_row(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "unparseable_repository_writes_nothing")

    done = port.run(source, gi.leadline_config(scenario), scenario, tmp_path)

    assert done.rows == {FILE: gi.YESTERDAY + failure_row("parse_failed", "repository response was not JSON")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_page_not_a_list_is_a_parse_failed_row(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "a_page_not_a_list_writes_nothing")

    done = port.run(source, gi.leadline_config(scenario), scenario, tmp_path)

    reason = "open issues response had an unexpected shape"
    assert done.rows == {FILE: gi.YESTERDAY + failure_row("parse_failed", reason)}


@pytest.mark.parametrize(
    ("url_end", "kind"),
    [(gi.REPO_URL, "repository"), (OPEN_URL, "open issues"), (ALL_URL, "all issues")],
    ids=["repository", "open issues", "all issues"],
)  # fmt: skip
@pytest.mark.parametrize("code", [401, 403, 404])
def test_an_error_waiting_cant_fix_is_critical_at_once(tmp_path: Path, code: int, url_end: str, kind: str) -> None:
    responses = failing_at(gi.responses(gi.SMALL), url_end, lambda url: exchange(url, "", status=code))

    done = run(tmp_path, responses)

    assert done.rows == {FILE: gi.YESTERDAY + failure_row("fetch_failed", f"{kind} request failed", str(code))}
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP {code} from GitHub's {kind} API")]
    assert done.status == 1


@pytest.mark.parametrize(
    ("code", "headers"),
    [(403, {"X-RateLimit-Remaining": "0"}), (403, {"Retry-After": "60"}), (429, {})],
    ids=["403 none remaining", "403 retry-after", "429"],
)
def test_a_rate_limit_is_a_recoverable_fetch_failed_row(tmp_path: Path, code: int, headers: dict[str, str]) -> None:
    limited = lambda url: exchange(url, '{"message":"API rate limit exceeded"}', status=code, headers=headers)  # noqa: E731
    responses = failing_at(gi.responses(gi.SMALL), "state=all&per_page=100&page=1", limited)

    done = run(tmp_path, responses)

    assert done.rows == {FILE: gi.YESTERDAY + failure_row("fetch_failed", "all issues request failed", str(code))}
    assert (done.status, done.ctx.critical, done.sleeps) == (0, [], [])


def answering(url: str, body: Any) -> list[Any]:
    """SMALL's responses, the request for `url` answered 200 with `body`; every request is still made."""
    return [exchange(url, body) if e["request"]["url"] == url else e for e in gi.responses(gi.SMALL)]


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ([], "repository response had an unexpected shape"),
        ({**github_repo(REPO), "stargazers_count": None}, "repository response had an unexpected shape"),
    ],
    ids=["a list", "a count not a number"],
)
def test_a_repository_it_cant_use_is_a_parse_failed_row(tmp_path: Path, body: Any, reason: str) -> None:
    done = run(tmp_path, answering(gi.REPO_URL, body))

    assert done.rows == {FILE: gi.YESTERDAY + failure_row("parse_failed", reason)}


def test_an_empty_object_is_not_an_empty_page(tmp_path: Path) -> None:
    """The script reads `{}` as a page with no issues; leadline can't use it (ADR: a failure is recorded as one)."""
    done = run(tmp_path, answering(ALL_URL, {}))

    assert done.rows == {
        FILE: gi.YESTERDAY + failure_row("parse_failed", "all issues response had an unexpected shape")
    }


def test_every_page_is_asked_for_before_any_is_read(tmp_path: Path) -> None:
    """As the script does: a page it can't read still has the requests after it made."""
    done = run(tmp_path, answering(OPEN_URL, "<html>Oops</html>"))

    assert done.rows == {FILE: gi.YESTERDAY + failure_row("parse_failed", "open issues response was not JSON")}


BASE = gi.issue(1)


@pytest.mark.parametrize(
    "item",
    [
        5,
        {**BASE, "user": "x"},
        {**BASE, "user": {"login": None}},
        {**BASE, "labels": ["bug"]},
        {**BASE, "labels": [{"name": 5}]},
        {**BASE, "labels": None},
        {**BASE, "comments": None},
        {**BASE, "comments": True},
        {k: v for k, v in BASE.items() if k != "number"},
        {**BASE, "title": None},
        {**BASE, "body": 5},
        {**BASE, "created_at": "yesterday"},
        {**BASE, "created_at": "2026-09-01T10:00:00"},
        {k: v for k, v in BASE.items() if k != "created_at"},
        {**BASE, "closed_at": "soon"},
    ],
    ids=["not an object", "a user not an object", "a login not a string", "a label not an object",
         "a label name not a string", "labels not a list", "comments null", "comments a boolean", "no number",
         "a title not a string", "a body not a string", "a date that isn't a time", "a time without its offset",
         "no creation time", "a closing time that isn't a time"],
)  # fmt: skip
def test_an_issue_it_cant_read_makes_its_list_unusable(tmp_path: Path, item: Any) -> None:
    """A list is read whole or not at all: the script loses the row over any of these, or reads it wrong."""
    done = run(tmp_path, answering(OPEN_URL, [item]))

    reason = "open issues response had an unexpected shape"
    assert done.rows == {FILE: gi.YESTERDAY + failure_row("parse_failed", reason)}


def test_a_null_user_is_a_community_author_with_no_login(tmp_path: Path) -> None:
    """As the script reads it (null_user_and_body pins the rest)."""
    item = {**gi.issue(1), "user": None}

    done = run(tmp_path, answering(OPEN_URL, [item]))

    row = last_row(done)
    assert "status" not in row
    assert row["age"]["oldest_days"] == 22


def test_a_failure_keeps_a_good_row(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data."""
    good = run(tmp_path / "good", gi.responses(gi.SMALL)).rows[FILE]

    done = run(tmp_path, [failed(gi.REPO_URL)], seed=good)

    assert done.rows == {FILE: good}
    assert f"WARN: kept the good row for 2026-09-23 in {FILE}; fetch_failed not recorded" in done.log


def test_a_failing_entry_does_not_stop_the_next(tmp_path: Path) -> None:
    other = f"{GITHUB_API}/repos/a/other"
    responses = [
        failed(gi.REPO_URL),
        exchange(other, github_repo("a/other", stars=2)),
        exchange(f"{other}/issues?state=open&per_page=100&page=1", []),
        exchange(f"{other}/issues?state=all&per_page=100&page=1", []),
    ]

    done = run(tmp_path, responses, cfg=[ENTRY, {"repo": "a/other"}])

    assert done.rows[FILE] == gi.YESTERDAY + failure_row("fetch_failed", "repository request failed", "000")
    assert done.rows["github-issues-other.jsonl"].startswith(
        '{"date": "2026-09-23", "repo": {"stars": 2, "forks": 0, "watchers": 0}, "issues": {"open": 0, '
    )


# --- check() ---


def check(tmp_path: Path, cfg: Any, *responses: Any, http: Http | None = None) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    if http is not None:
        ctx = replace(ctx, http=http)
    with port.credentials(scenarios.CREDENTIALS, source.ENV):
        findings = source.check(ctx, cfg)
    assert transport.unmatched == transport.never_served() == []
    assert [p for p in tmp_path.iterdir() if p.suffix == ".jsonl"] == []  # a check writes no rows
    return findings


REPOSITORY = exchange(gi.REPO_URL, github_repo(REPO))
OPEN_PAGE = exchange(OPEN_URL, [])
WITH_QUEUE = [{**ENTRY, "queue": gi.QUEUE}]
EXISTS = Finding("ok", f"{REPO}: the repo exists")
READABLE = Finding("ok", f"{REPO}: the token can read its issues")
USER_SENT = graphql_body("query($m:String!){user(login:$m){login}}", {"m": gi.MAINTAINER})
NO_SUCH_USER = {"type": "NOT_FOUND", "path": ["user"], "message": "Could not resolve to a User"}


def user_answer(body: Any, status: int = 200) -> Any:
    return exchange(GITHUB_GRAPHQL, body, method="POST", sent=USER_SENT, status=status)


USER = user_answer({"data": {"user": {"login": gi.MAINTAINER}}})
MAINTAINER_EXISTS = Finding("ok", f'{REPO}: the maintainer login "{gi.MAINTAINER}" exists')


def test_check_finds_the_repo_its_issues_and_the_maintainers_login(tmp_path: Path) -> None:
    assert check(tmp_path, [ENTRY], REPOSITORY, OPEN_PAGE, USER) == [EXISTS, READABLE, MAINTAINER_EXISTS]


def test_check_asks_for_no_login_without_a_maintainer(tmp_path: Path) -> None:
    assert check(tmp_path, [{"repo": REPO}], REPOSITORY, OPEN_PAGE) == [EXISTS, READABLE]


def test_check_finds_a_missing_maintainer_login(tmp_path: Path) -> None:
    """A mistyped login would count every issue and pull request as the community's, with no failure to show it."""
    missing = user_answer({"data": {"user": None}, "errors": [NO_SUCH_USER]})

    findings = check(tmp_path, [ENTRY], REPOSITORY, OPEN_PAGE, missing)

    misread = "every issue and pull request would count as the community's"
    message = f'{REPO}: the maintainer login "{gi.MAINTAINER}" doesn\'t exist, so {misread}'
    assert findings == [EXISTS, READABLE, Finding("error", message)]


def test_check_still_finds_the_label_when_the_login_cant_be_asked_for(tmp_path: Path) -> None:
    label = exchange(LABEL_URL, {"name": gi.LABEL})

    findings = check(tmp_path, WITH_QUEUE, REPOSITORY, OPEN_PAGE, user_answer("", status=502), label)

    unavailable = Finding("warn", f"{REPO}: could not fetch the maintainer's login: HTTP 502")
    assert findings == [EXISTS, READABLE, unavailable, Finding("ok", f'{REPO}: the queue\'s label "{gi.LABEL}" exists')]


def test_check_finds_the_queues_label(tmp_path: Path) -> None:
    label = exchange(LABEL_URL, {"name": gi.LABEL})

    findings = check(tmp_path, WITH_QUEUE, REPOSITORY, OPEN_PAGE, USER, label)

    assert findings == [
        EXISTS,
        READABLE,
        MAINTAINER_EXISTS,
        Finding("ok", f'{REPO}: the queue\'s label "{gi.LABEL}" exists'),
    ]


def test_check_warns_when_the_repo_has_no_such_label(tmp_path: Path) -> None:
    missing = exchange(LABEL_URL, '{"message":"Not Found"}', status=404)

    findings = check(tmp_path, WITH_QUEUE, REPOSITORY, OPEN_PAGE, USER, missing)

    message = f'{REPO}: the repo has no label "{gi.LABEL}", so its queue is always empty'
    assert findings == [EXISTS, READABLE, MAINTAINER_EXISTS, Finding("warn", message)]


@pytest.mark.parametrize(
    ("responses", "finding"),
    [
        (
            [exchange(gi.REPO_URL, '{"message":"Not Found"}', status=404)],
            Finding("error", f"{REPO}: HTTP 404: the repo doesn't exist, or the token can't read it"),
        ),
        (
            [exchange(gi.REPO_URL, "", status=403, headers={"X-RateLimit-Remaining": "0"})],
            Finding("warn", f"{REPO}: could not fetch the repo: HTTP 403, rate limited"),
        ),
        (
            [REPOSITORY, exchange(OPEN_URL, "", status=403)],
            Finding("error", f"{REPO}: HTTP 403: the repo doesn't exist, or the token can't read it"),
        ),
        ([REPOSITORY, failed(OPEN_URL)], Finding("warn", f"{REPO}: could not fetch its issues: HTTP 0")),
        (
            [REPOSITORY, OPEN_PAGE, USER, exchange(LABEL_URL, "", status=503)],
            Finding("warn", f'{REPO}: could not fetch its label "{gi.LABEL}": HTTP 503'),
        ),
    ],
    ids=["no repo", "rate limited", "issues refused", "issues unreachable", "label unreachable"],
)
def test_check_reports_api_trouble_as_a_finding_and_stops_there(
    tmp_path: Path, responses: list[Any], finding: Finding
) -> None:
    assert check(tmp_path, WITH_QUEUE, *responses)[-1] == finding


def test_check_asks_only_for_the_login_and_the_label_after_a_collection(tmp_path: Path) -> None:
    """The repo and the first open page are what collection asked for, so the run's answers serve them."""
    transport = http.SavedResponses([*gi.responses(gi.SMALL), USER, exchange(LABEL_URL, {"name": gi.LABEL})])
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, source.ENV):
        source.collect(ctx, WITH_QUEUE)
        findings = source.check(ctx, WITH_QUEUE)

    assert [f.level for f in findings] == ["ok", "ok", "ok", "ok"]
    assert transport.unmatched == transport.never_served() == []


def test_check_checks_every_entry(tmp_path: Path) -> None:
    other = f"{GITHUB_API}/repos/a/other"

    responses = [REPOSITORY, OPEN_PAGE, USER, exchange(other, "", status=404)]

    findings = check(tmp_path, [ENTRY, {"repo": "a/other"}], *responses)

    assert [f.level for f in findings] == ["ok", "ok", "ok", "error"]


# --- the summary ---


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(
        source,
        gi.leadline_config(port.scenario(NAME, "snapshot_with_modem_pipeline")),
        "snapshot_with_modem_pipeline",
        tmp_path,
    ) == [
        "",
        "### github_issues: solentlabs/example",
        "",
        "- Open issues: 7",
        "- Open pull requests: 2",
    ]
