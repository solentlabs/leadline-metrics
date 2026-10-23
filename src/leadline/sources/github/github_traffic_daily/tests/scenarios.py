"""github_traffic_daily: a repository's views and clones day by day, from GitHub's 14-day traffic window.

The script's collect_github_traffic_daily reads the 14-day views and clones and writes a row per day
into github-traffic-daily.jsonl: every day of the window the file is missing, and yesterday again,
never today, since today's counts are still growing. The script's rows follow the views: a clones day
the views lack writes no row (leadline writes one, with no views). A failed views or clones fetch, or
a body that doesn't parse, writes nothing, and so does a window with no days or a day without a
timestamp. The window as one snapshot is github_traffic.

leadline's rows match the script's on every scenario. Where the script writes nothing for a failed
fetch or an answer it can't use, leadline writes a failure row (`fetch_failed` or `parse_failed`) for
yesterday, the day its newest row should have, unless a good row holds it (ADR: a failure is recorded
as a failure; a failure never replaces good data). These scenarios seed yesterday's good row, so no
failure row is written; tests/test_github_traffic_daily.py asserts the failure rows without one.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Mapping

from scenarios import Scenario
from scenarios.builders.common import Exchange, exchange, failed

from leadline.sources.github.tests.builders import GITHUB_API, github_traffic

REPO = "solentlabs/example"
CONFIG = {"GITHUB_REPO": REPO}
CALLS = ["collect_github_traffic_daily"]
FILE = "github-traffic-daily.jsonl"
VIEWS, CLONES = f"{GITHUB_API}/repos/{REPO}/traffic/views", f"{GITHUB_API}/repos/{REPO}/traffic/clones"
TODAY = date(2026, 9, 23)
WINDOW = [TODAY - timedelta(days=n) for n in range(13, -1, -1)]  # the API's 14 days, today's still counting

VIEWS_BY_DAY = {day: (20 + 3 * i, 4 + i % 5) for i, day in enumerate(WINDOW)}
CLONES_BY_DAY = {day: (6 + i % 4, 2 + i % 3) for i, day in enumerate(WINDOW) if day != date(2026, 9, 15)}

# leadline's config for these scenarios: the script's repo, writing the script's file.
LEADLINE_CONFIG = [{"repo": REPO, "file": FILE}]


def row(day: str, views: int) -> str:
    return f'{{"date": "{day}", "views": {views}, "views_unique": 1, "clones": 0, "clones_unique": 0}}\n'


def responses(
    views: Mapping[date, tuple[int, int]] = VIEWS_BY_DAY, clones: Mapping[date, tuple[int, int]] = CLONES_BY_DAY
) -> list[Exchange]:
    """Views and clones, in the order the script asks for them."""
    return [exchange(VIEWS, github_traffic("views", views)), exchange(CLONES, github_traffic("clones", clones))]


def within(by_day: Mapping[date, tuple[int, int]], days: list[date]) -> dict[date, tuple[int, int]]:
    """The counts for those of `days` that `by_day` has."""
    return {day: n for day, n in by_day.items() if day in days}


# Today's views with no timestamp: no day can be read from it.
UNSTAMPED = github_traffic("views", VIEWS_BY_DAY)
del UNSTAMPED["views"][-1]["timestamp"]

SEEDED = {FILE: row("2026-09-21", 5) + row("2026-09-22", 6)}

# The scenarios where leadline's rows differ from the script's by a decision, and the decision.
DIVERGENCES = {
    "ignores_clones_days_views_lack": "a day with clones is a day: its views are zero, not a row never written",
}

SCENARIOS = [
    Scenario(
        "backfills_missing_days_and_refreshes_yesterday",
        CALLS,
        responses(),
        config=CONFIG,
        seed={
            FILE: "not json\n"
            + '{"views": 9}\n'  # no date: kept, and marks no day as present
            + row("2026-09-12", 1)
            + row("2026-09-20", 2)
            + row("2026-09-22", 3)
        },
    ),
    Scenario(
        # A clones day the views don't have writes no row: the rows follow the views.
        "ignores_clones_days_views_lack",
        CALLS,
        responses(within(VIEWS_BY_DAY, WINDOW[9:12]), within(CLONES_BY_DAY, WINDOW[7:12])),
        config=CONFIG,
    ),
    Scenario(
        "writes_nothing_when_current",
        CALLS,
        responses(within(VIEWS_BY_DAY, WINDOW[9:12]), within(CLONES_BY_DAY, WINDOW[9:12])),
        config=CONFIG,
        seed={FILE: row("2026-09-19", 1) + row("2026-09-20", 2) + row("2026-09-21", 3)},
    ),
    Scenario(
        "items_without_a_timestamp",
        CALLS,
        [exchange(VIEWS, UNSTAMPED), responses()[1]],
        config=CONFIG,
        seed=SEEDED,
    ),
    Scenario(
        "unparseable_clones_write_nothing",
        CALLS,
        [responses()[0], exchange(CLONES, "<html>maintenance</html>")],
        config=CONFIG,
        seed=SEEDED,
    ),
    Scenario("an_empty_window", CALLS, responses({}, {}), config=CONFIG, seed=SEEDED),
    Scenario(
        "failed_views_write_nothing",
        CALLS,
        [exchange(VIEWS, '{"message":"Must have push access"}', status=403)],
        config=CONFIG,
        seed=SEEDED,
    ),
    Scenario("failed_clones_write_nothing", CALLS, [responses()[0], failed(CLONES)], config=CONFIG, seed=SEEDED),
    Scenario("skips_without_a_token", CALLS, env={}, config=CONFIG),
]
