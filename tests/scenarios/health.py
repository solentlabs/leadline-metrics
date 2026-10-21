"""health: the row each run adds to health.jsonl, from the row files in the metrics folder.

Health is not a source: its scenarios run the script's check_freshness on seeded row files, with no
requests. It reads every `*.jsonl` in the folder but health.jsonl, each date's last line winning,
and writes one row for the run date (a rerun replaces it): each file whose newest row is too old
(`stale`; a file in LAGGING_METRICS gets a day more, and a file with no dated row is stale with no
date), each file whose newest row is a failure row with its streak (`failed`), and each file's holes
in the last 14 days (`gaps`). The script's version and hash are set here, as leadline writes its own
(docs/ARCHITECTURE.md, "Health"); tests/test_health.py compares the rest of the row, and asserts the
fields leadline adds.

leadline differs by decision on the scenarios in DIVERGENCES, asserted in tests/test_health.py: a
line that isn't a row, or whose date isn't a date, is skipped, where the script's check stops and
writes no row (ADR: store never fails on a line it can't use).
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from scenarios import Scenario

CALLS = ["check_freshness"]
FILE = "health.jsonl"
LAGGING = ["example-lagging.jsonl", "example-lagging-behind.jsonl"]
CONFIG = {"SCRIPT_VERSION": "0.0.0-example", "SCRIPT_HASH": "0123456789ab", "LAGGING_METRICS": LAGGING}


def rows(*lines: Mapping[str, Any] | str) -> str:
    """A row file's text: each row as JSON, or a string as it is, one per line."""
    return "".join(f"{line if isinstance(line, str) else json.dumps(line)}\n" for line in lines)


def good(*days: str, **fields: Any) -> list[dict[str, Any]]:
    return [{"date": day, "n": 1, **fields} for day in days]


def failure(day: str, status: str = "fetch_failed") -> dict[str, Any]:
    return {"date": day, "status": status, "reason": "request failed"}


CURRENT = {
    "example-daily.jsonl": rows(*good("2026-09-21", "2026-09-22", "2026-09-23")),
    "example-lagging.jsonl": rows(*good("2026-09-20", "2026-09-21", "2026-09-22")),
    "notes.txt": "not a row file\n",
}


def scenario(name: str, seed: Mapping[str, str]) -> Scenario:
    return Scenario(name, CALLS, config=CONFIG, seed=seed)


SCENARIOS = [
    scenario("every_file_is_current", CURRENT),
    scenario("an_empty_folder", {}),
    scenario(
        "stale_files",
        {
            "example-behind.jsonl": rows(*good("2026-09-20", "2026-09-21")),
            "example-lagging.jsonl": rows(*good("2026-09-21")),
            "example-lagging-behind.jsonl": rows(*good("2026-09-20")),
            "example-undated.jsonl": rows({"n": 1}, "not json"),
            "example-empty.jsonl": "",
            "example-ahead.jsonl": rows(*good("2026-09-24")),
        },
    ),
    scenario(
        "failed_files_with_their_streak",
        {
            "example-failing.jsonl": rows(
                *good("2026-09-18", "2026-09-19"),
                failure("2026-09-21"),
                failure("2026-09-22", "partial"),
                failure("2026-09-23"),
            ),
            "example-status-ok.jsonl": rows(*good("2026-09-22", "2026-09-23", status="ok")),
            "example-once.jsonl": rows(*good("2026-09-22"), failure("2026-09-23", "rate_limited")),
            "example-never-good.jsonl": rows(failure("2026-09-22"), failure("2026-09-23")),
        },
    ),
    scenario(
        "gaps_in_the_last_14_days",
        {
            "example-holes.jsonl": rows(*good("2026-09-01", "2026-09-05", "2026-09-10", "2026-09-23")),
            "example-new.jsonl": rows(*good("2026-09-20"), failure("2026-09-22", "parse_failed"), *good("2026-09-23")),
            "example-lagging.jsonl": rows(*good("2026-09-18", "2026-09-20")),
        },
    ),
    scenario(
        "the_last_line_for_a_date_wins",
        {
            "example-healed.jsonl": rows(*good("2026-09-22"), failure("2026-09-23"), "", *good("2026-09-23")),
            "example-broke.jsonl": rows(*good("2026-09-22", "2026-09-23"), failure("2026-09-23")),
            "example-unordered.jsonl": rows(
                *good("2026-09-23", "2026-09-21"), "not json", {"date": ""}, {"date": None}, *good("2026-09-22")
            ),
        },
    ),
    scenario(
        "a_rerun_replaces_the_days_health_row",
        {
            FILE: rows({"date": "2026-09-22", "checked": 7}, {"date": "2026-09-23", "checked": 9}),
            **CURRENT,
        },
    ),
    scenario(
        "a_line_that_is_not_a_row_is_skipped",
        {
            **CURRENT,
            "example-daily.jsonl": CURRENT["example-daily.jsonl"]
            + rows("null", "[1]", {"date": 5}, {"date": "not-a-date"}, {"date": "20260901"}),
        },
    ),
]

# Where leadline differs by decision (tests/test_health.py asserts leadline's side).
DIVERGENCES = {
    "a_line_that_is_not_a_row_is_skipped": "ADR: store never fails on a line it can't use",
}
