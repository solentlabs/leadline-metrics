"""health: the row each run adds to health.jsonl, matched with the script's, and the fields leadline adds.

The script's side is its check_freshness on the scenarios in tests/scenarios/health.py; the golden is
health.jsonl as it writes it. leadline writes the same row for the same files, its own version and
config hash in place of the script's, and adds checks[], critical[] and corrections (ADRs: check()
runs on every run, after collection; one failed night recovers, consecutive failures are critical;
every correction is recorded).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import port
import pytest
from scenarios import Scenario
from scenarios.health import CONFIG, CURRENT, FILE, LAGGING, failure, good, rows

from leadline import __version__, health, http
from leadline.context import Context, Critical

HASH = "fedcba987654"
NEW_FIELDS = ["checks", "critical", "corrections"]
TODAY = "2026-09-23"


def context(folder: Path, now: str = f"{TODAY}T23:31:05", tz: str = "UTC") -> Context:
    folder.mkdir(parents=True, exist_ok=True)
    return port.context(folder, http.SavedResponses([]), port.local_time(now, tz), [])


def seeded(folder: Path, seed: Mapping[str, str], now: str = f"{TODAY}T23:31:05", tz: str = "UTC") -> Context:
    ctx = context(folder, now, tz)
    for name, text in seed.items():
        (folder / name).write_text(text, encoding="utf-8")
    return ctx


def write(
    ctx: Context,
    written: Mapping[str, health.Written] | None = None,
    checks: Sequence[Mapping[str, str]] = (),
) -> dict[str, Any]:
    """Write the run's health row; returns it, as the last line of health.jsonl."""
    health.write(ctx, written or {}, checks, HASH)
    row: dict[str, Any] = json.loads((ctx.out_dir / FILE).read_text(encoding="utf-8").splitlines()[-1])
    return row


def lagging_files() -> dict[str, health.Written]:
    """The scenarios' LAGGING_METRICS, as the runner gives them: files that lagging sources write."""
    return {name: health.Written("example_lagging", True) for name in LAGGING}


def as_the_script_writes_it(folder: Path) -> None:
    """Rewrite leadline's row as the script's: its added fields out, the scenario's version and hash in."""
    path = folder / FILE
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    row = json.loads(lines[-1])
    for name in NEW_FIELDS:
        del row[name]
    row["script_version"], row["script_hash"] = CONFIG["SCRIPT_VERSION"], CONFIG["SCRIPT_HASH"]
    lines[-1] = json.dumps(row) + "\n"
    path.write_text("".join(lines), encoding="utf-8")


@pytest.mark.parametrize("scenario", port.golden_scenarios("health"), ids=lambda s: s.name)
def test_the_row_matches_the_scripts_for_the_same_files(tmp_path: Path, scenario: Scenario) -> None:
    ctx = seeded(tmp_path, scenario.seed, scenario.now, scenario.tz)

    row = write(ctx, lagging_files())
    as_the_script_writes_it(tmp_path)

    assert list(row)[-5:] == ["gaps", *NEW_FIELDS, "captured_at"]
    assert port.golden_differences("health", scenario.name, tmp_path) == []


def test_a_line_that_is_not_a_row_is_skipped(tmp_path: Path) -> None:
    """Where the script's check stops and writes no row (ADR: store never fails on a line it can't use)."""
    scenario = port.scenario("health", "a_line_that_is_not_a_row_is_skipped")
    ctx = seeded(tmp_path, scenario.seed)

    write(ctx, lagging_files())
    as_the_script_writes_it(tmp_path)
    (tmp_path / "example-daily.jsonl").write_text(CURRENT["example-daily.jsonl"], encoding="utf-8")

    assert port.golden_differences("health", "every_file_is_current", tmp_path) == []


def test_the_row_carries_the_package_version_and_the_config_hash(tmp_path: Path) -> None:
    row = write(context(tmp_path))

    assert (row["script_version"], row["script_hash"]) == (__version__, HASH)


def test_checks_are_the_findings_the_run_gives(tmp_path: Path) -> None:
    checks = [
        {"source": "a", "level": "ok", "message": "the repo exists"},
        {"source": "b", "level": "error", "message": "no such site"},
    ]

    assert write(context(tmp_path), checks=checks)["checks"] == checks


def test_corrections_are_the_runs(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    ctx.record_correction("a", "a.jsonl", "2026-09-20", {"views": 3}, {"views": 4})
    ctx.record_correction("b", "b.jsonl", "2026-09-21", {"likes": 1}, {"likes": 2}, {"topic_id": 7})

    assert write(ctx)["corrections"] == [
        {
            "source": "a",
            "file": "a.jsonl",
            "date": "2026-09-20",
            "old": {"views": 3},
            "new": {"views": 4},
            "second_key": {},
        },
        {
            "source": "b",
            "file": "b.jsonl",
            "date": "2026-09-21",
            "old": {"likes": 1},
            "new": {"likes": 2},
            "second_key": {"topic_id": 7},
        },
    ]


def test_critical_holds_the_runs_errors_waiting_cant_fix(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    ctx.record_critical("a", "a.jsonl", "HTTP 401")
    ctx.record_critical("b", None, "crashed: RuntimeError: boom")

    assert write(ctx)["critical"] == [
        {"source": "a", "file": "a.jsonl", "reason": "HTTP 401"},
        {"source": "b", "file": None, "reason": "crashed: RuntimeError: boom"},
    ]


def test_nothing_new_on_a_quiet_night(tmp_path: Path) -> None:
    row = write(seeded(tmp_path, CURRENT), lagging_files())

    assert [row[name] for name in NEW_FIELDS] == [[], [], []]


# A source with no good row for two or more consecutive expected dates is critical (docs/ARCHITECTURE.md,
# "Terms"): the run date, or the day before it for a lagging source. Days before a file's first row don't
# count, as its series hadn't begun.
@pytest.mark.parametrize(
    ("lagging", "lines", "reason"),
    [
        (False, [*good("2026-09-21"), failure("2026-09-22"), failure("2026-09-23")], "2 nights, since 2026-09-22"),
        (False, [*good("2026-09-21")], "2 nights, since 2026-09-22"),
        (False, [*good("2026-09-19"), failure("2026-09-22", "partial")], "4 nights, since 2026-09-20"),
        (False, [failure("2026-09-22"), failure("2026-09-23")], "2 nights, since 2026-09-22"),
        (True, [*good("2026-09-20"), failure("2026-09-21"), failure("2026-09-22")], "2 nights, since 2026-09-21"),
        (True, [*good("2026-09-20"), failure("2026-09-23")], "2 nights, since 2026-09-21"),
    ],
    ids=[
        "two failure rows",
        "two nights without a row",
        "a partial row and missing days",
        "no good row since the file began",
        "a lagging source's two days",
        "a lagging source's row for today isn't its expected date's",
    ],
)
def test_consecutive_failed_nights_are_critical(
    tmp_path: Path, lagging: bool, lines: list[dict[str, Any]], reason: str
) -> None:
    ctx = seeded(tmp_path, {"a.jsonl": rows(*lines)})

    row = write(ctx, {"a.jsonl": health.Written("source_a", lagging)})

    expected = Critical("source_a", "a.jsonl", f"no good row for {reason}")
    assert ctx.critical == [expected]  # so the run exits 1
    assert row["critical"] == [{"source": "source_a", "file": "a.jsonl", "reason": expected.reason}]


@pytest.mark.parametrize(
    ("lagging", "lines"),
    [
        (False, [*good("2026-09-22"), failure("2026-09-23")]),
        (False, [*good("2026-09-22")]),
        (False, [failure("2026-09-23")]),
        (False, [*good("2026-09-21"), failure("2026-09-22"), *good("2026-09-23")]),
        (False, [failure("2026-09-22"), *good("2026-09-24")]),
        (True, [*good("2026-09-21"), failure("2026-09-22"), failure("2026-09-23")]),
        (True, [*good("2026-09-22")]),
        (False, [{"n": 1}]),
        (False, []),
    ],
    ids=[
        "one failed night",
        "one night without a row",
        "a file that began tonight with a failure",
        "a failure between good nights",
        "a good row after the expected date",
        "a lagging source's one failed day",
        "a lagging source that is current",
        "a file with no dated row",
        "an empty file",
    ],
)
def test_one_failed_night_is_not_critical(tmp_path: Path, lagging: bool, lines: list[dict[str, Any]]) -> None:
    ctx = seeded(tmp_path, {"a.jsonl": rows(*lines)})

    assert write(ctx, {"a.jsonl": health.Written("source_a", lagging)})["critical"] == []
    assert ctx.critical == []


def test_a_file_one_of_whose_series_fails_is_judged_by_the_dates_it_has_a_good_row_for(tmp_path: Path) -> None:
    """A forum file holds a row per topic a day: a deleted topic's failure row, last on each date, doesn't make the
    file's other topics' good rows count for nothing."""
    days = ["2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23"]
    lines = [
        json.dumps(row) + "\n"
        for day in days
        for row in ({"date": day, "topic_id": 1, "views": 5}, {"date": day, "topic_id": 2, "status": "fetch_failed"})
    ]
    ctx = seeded(tmp_path, {"forum.jsonl": "".join(lines)})

    row = write(ctx, {"forum.jsonl": health.Written("discourse_topics", False)})

    assert row["critical"] == []
    assert ctx.critical == []


def test_a_file_with_no_good_row_on_any_series_is_still_critical(tmp_path: Path) -> None:
    days = ["2026-09-21", "2026-09-22", "2026-09-23"]
    lines = [json.dumps({"date": day, "topic_id": n, "status": "fetch_failed"}) + "\n" for day in days for n in (1, 2)]
    ctx = seeded(tmp_path, {"forum.jsonl": "".join(lines)})

    write(ctx, {"forum.jsonl": health.Written("discourse_topics", False)})

    assert [c.reason for c in ctx.critical] == ["no good row for 3 nights, since 2026-09-21"]


def test_only_files_a_configured_source_writes_are_judged_critical(tmp_path: Path) -> None:
    """A file no source writes (an old one left in the folder) is only ever stale, as in the script."""
    ctx = seeded(tmp_path, {"old.jsonl": rows(*good("2026-09-01"))})

    row = write(ctx, {"absent.jsonl": health.Written("source_a", False)})

    assert row["critical"] == []
    assert row["stale"] == [{"file": "old.jsonl", "last": "2026-09-01", "age_days": 22}]


def test_the_run_date_is_local(tmp_path: Path) -> None:
    """Just after midnight in a zone east of UTC, the run date is the local one, as the script's `date`."""
    ctx = seeded(tmp_path, {"a.jsonl": rows(*good("2026-09-24"))}, "2026-09-24T00:30:00", "Asia/Kolkata")

    row = write(ctx, {"a.jsonl": health.Written("source_a", False)})

    assert (row["date"], row["stale"], row["critical"]) == ("2026-09-24", [], [])


def test_the_log_says_what_is_stale_failed_or_missing(tmp_path: Path) -> None:
    """As the script's check_freshness logs it."""
    ctx = seeded(
        tmp_path,
        {
            "behind.jsonl": rows(*good("2026-09-20")),
            "undated.jsonl": rows({"n": 1}),
            "failing.jsonl": rows(*good("2026-09-20"), failure("2026-09-22"), failure("2026-09-23")),
        },
    )
    lines: list[str] = []
    with port.captured_log(lines):
        write(ctx)

    assert lines == [
        "Checking metrics freshness...",
        "Added 2026-09-23 to health.jsonl",
        "STALE: behind.jsonl — 3d behind (last 2026-09-20)",
        "STALE: undated.jsonl — no dated records",
        "FAILED: failing.jsonl — record for 2026-09-23 has status=fetch_failed (2 nights running, since 2026-09-22)",
        "GAP: failing.jsonl — 1 missing (2026-09-21); 2 empty (2026-09-22, 2026-09-23) in 2026-09-20..2026-09-23",
        "WARN: 2 metrics source(s) stale — see health.jsonl",
        "WARN: 1 metrics source(s) reported but collected nothing — see health.jsonl",
    ]


def test_the_log_says_when_every_file_is_current(tmp_path: Path) -> None:
    lines: list[str] = []
    with port.captured_log(lines):
        write(seeded(tmp_path, CURRENT), lagging_files())

    assert lines == ["Checking metrics freshness...", "Added 2026-09-23 to health.jsonl", "All metrics sources current"]


def test_the_row_of_a_rerun_replaces_the_days(tmp_path: Path) -> None:
    ctx = seeded(tmp_path, {FILE: rows({"date": "2026-09-22"}, {"date": TODAY, "checked": 9})})

    write(ctx)

    written = [json.loads(line) for line in (tmp_path / FILE).read_text(encoding="utf-8").splitlines()]
    assert [(row["date"], row["checked"]) for row in written[1:]] == [(TODAY, 0)]
    assert written[0] == {"date": "2026-09-22"}
