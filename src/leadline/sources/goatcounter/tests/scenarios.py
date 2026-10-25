"""goatcounter: a site's page views by day and its top referrers, from GoatCounter.

The script's collect_goatcounter asks for the week before the run, per page (up to three times while
the request fails or comes back empty), then the week's total, then the referrers of each of the
first ten pages with an id whose referrers it can fetch. It writes a row per day the pages have a
stat for into goatcounter.jsonl: every day the file is missing, and yesterday again, never today.
Yesterday's row carries the top five referrers of the week and the window's sum of page views
(undated stats included), GoatCounter's own total (null when that request fails or doesn't parse)
and how many pages came back; a referrers body that doesn't parse counts toward the ten but adds
nothing. A seed line that doesn't parse, or has no date, leaves its day missing and is kept. Hits
that fail three times or don't parse, no hits, or a seed line that parses but isn't an object write
nothing.

Referrers tied on count across pages come out in the order the file system lists the script's
temporary files, which differs between file systems, so no scenario ties them; leadline orders them
by the order the pages came back in (test_goatcounter.py).

leadline differs by decision on the scenarios in DIVERGENCES, asserted in test_goatcounter.py:
a failed or unusable pages answer is a failure row for yesterday, a failed or unusable total or
referrers answer is null in a partial row, and a seed line that isn't an object is kept. The rows also
hold `top_pages`, `top_browsers`, `top_systems` and `top_locations`, which the script drops: the scenarios
that write a row are in DIVERGENCES for it (more data is captured than the script did).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Mapping, Sequence

from scenarios import Scenario
from scenarios.builders.common import Exchange, Json, exchange, failed

from leadline.sources.goatcounter.tests.builders import (
    goatcounter_hits,
    goatcounter_path,
    goatcounter_refs,
    goatcounter_stats,
    goatcounter_total,
)

CONFIG = {"GOATCOUNTER_SITE": "example"}
CALLS = ["collect_goatcounter"]
FILE = "goatcounter.jsonl"
API = "https://example.goatcounter.com/api/v0"
WINDOW = "start=2026-09-16&end=2026-09-23"
HITS, TOTAL = f"{API}/stats/hits?{WINDOW}&limit=200", f"{API}/stats/total?{WINDOW}"
DAYS = [date(2026, 9, 16) + timedelta(days=n) for n in range(8)]  # the week, and today, still counting
# leadline's config for the scenarios: the script's site, and the file that keeps the script's name.
LEADLINE_CONFIG = [{"site": CONFIG["GOATCOUNTER_SITE"], "file": FILE}]


def refs_url(path_id: int) -> str:
    return f"{API}/stats/hits/{path_id}?{WINDOW}"


def page(path_id: int, views: Mapping[date, int] | None = None) -> Json:
    return goatcounter_path(path_id, f"/page-{path_id}", views or {day: path_id + i for i, day in enumerate(DAYS)})


def without_id(path: Json) -> Json:
    return {key: value for key, value in path.items() if key != "path_id"}


def hits(pages: Sequence[Json], status: int = 200) -> Exchange:
    return exchange(HITS, goatcounter_hits(pages), status=status)


def total(pages: Sequence[Json]) -> Exchange:
    """The week's total: every page's views, day by day."""
    daily = {day: sum(s["daily"] for p in pages for s in p["stats"] if s["day"] == day.isoformat()) for day in DAYS}
    return exchange(TOTAL, goatcounter_total(daily))


def row(day: str, pageviews: int) -> str:
    return f'{{"date": "{day}", "pageviews": {pageviews}, "top_referrers": []}}\n'


ALL_BUT_YESTERDAY = {FILE: "".join(row(day.isoformat(), 1) for day in DAYS[:-2])}  # a row each day before it
THREE = [page(1), page(2), page(3)]
REFERRERS = [
    exchange(refs_url(1), goatcounter_refs([("", 30), ("github.com", 12), ("example.com", 4), ("   ", 9)])),
    exchange(refs_url(2), goatcounter_refs([("github.com", 5), ("forum.example", 3), ("news.example", 0)])),
    exchange(refs_url(3), goatcounter_refs([("search.example", 2), ("blog.example", 1), ("t.example", 1)])),
]
KINDS = ("browsers", "systems", "locations")  # the stats leadline asks for besides, after the referrers
STAT_ITEMS = {
    "browsers": [("Chrome", 60), ("Firefox", 20)],
    "systems": [("Windows", 50), ("Linux", 30)],
    "locations": [("United States", 55), ("Germany", 25)],
}


def stats_url(kind: str) -> str:
    return f"{API}/stats/{kind}?{WINDOW}"


def stats(kind: str, body: object = None, status: int = 200) -> Exchange:
    """The answer to the request for `kind` (its items from STAT_ITEMS unless `body` is given)."""
    return exchange(stats_url(kind), goatcounter_stats(STAT_ITEMS[kind]) if body is None else body, status=status)


STATS = [stats(kind) for kind in KINDS]
BEFORE_YESTERDAY = [page(1, dict.fromkeys(DAYS[:5], 2))]  # stats up to three days before the run, none since
UNDATED = {"hourly": [0] * 24, "daily": 50}  # a stat with no day
TWELVE = [page(n, {DAYS[-2]: n}) for n in range(1, 13)]
TWELVE_REFERRERS = [
    exchange(refs_url(n), goatcounter_refs([(f"site-{n}.example", n)]), status=500 if n == 2 else 200)
    for n in range(1, 12)
]

SCENARIOS = [
    Scenario(
        "week_backfills_missing_days_and_refreshes_yesterday",
        CALLS,
        [hits(THREE), total(THREE), *REFERRERS],
        config=CONFIG,
        seed={FILE: row("2026-09-18", 1) + row("2026-09-20", 2) + row("2026-09-22", 3)},
    ),
    Scenario(
        "referrers_of_ten_pages_and_no_window_total",
        CALLS,
        [hits(TWELVE), exchange(TOTAL, "", status=500), *TWELVE_REFERRERS],
        config=CONFIG,
        seed=ALL_BUT_YESTERDAY,
    ),
    Scenario(
        "retries_the_pages_request",
        CALLS,
        [hits([], status=500), failed(HITS), hits(THREE), total(THREE), *REFERRERS],
        config=CONFIG,
        seed=ALL_BUT_YESTERDAY,
    ),
    Scenario(
        "three_failed_attempts_write_nothing",
        CALLS,
        [hits([], status=503), failed(HITS), exchange(HITS, "", status=429)],
        config=CONFIG,
        seed={FILE: row("2026-09-21", 4)},
    ),
    Scenario(
        "three_empty_answers_write_nothing",
        CALLS,
        [exchange(HITS, "")] * 3,  # each of the three attempts
        config=CONFIG,
        seed={FILE: row("2026-09-21", 4)},
    ),
    Scenario(
        "unparseable_hits_write_nothing",
        CALLS,
        [exchange(HITS, "<html>maintenance</html>"), total(THREE)],
        config=CONFIG,
        seed={FILE: row("2026-09-21", 4)},
    ),
    Scenario(
        "no_hits_write_nothing",
        CALLS,
        [hits([]), total([])],
        config=CONFIG,
        seed={FILE: row("2026-09-21", 4)},
    ),
    Scenario(
        "no_stat_for_yesterday_backfills_only_earlier_days",
        CALLS,
        [hits(BEFORE_YESTERDAY), total(BEFORE_YESTERDAY), REFERRERS[0]],
        config=CONFIG,
        seed={FILE: row("2026-09-16", 1)},
    ),
    Scenario(
        "unparseable_total_and_referrers_count_as_none",
        CALLS,
        [
            hits(THREE),
            exchange(TOTAL, "<html>maintenance</html>"),
            REFERRERS[0],
            exchange(refs_url(2), "<html>maintenance</html>"),
            REFERRERS[2],
        ],
        config=CONFIG,
        seed=ALL_BUT_YESTERDAY,
    ),
    Scenario(
        "pages_without_an_id_have_no_referrers",
        CALLS,
        [hits([page(1), {**page(2), "path_id": 0}, without_id(page(3))]), total(THREE), REFERRERS[0]],
        config=CONFIG,
        seed=ALL_BUT_YESTERDAY,
    ),
    Scenario(
        "an_undated_stat_counts_only_in_the_window_sum",
        CALLS,
        [hits([{**page(1), "stats": [*page(1)["stats"], UNDATED]}]), total([page(1)]), REFERRERS[0]],
        config=CONFIG,
        seed=ALL_BUT_YESTERDAY,
    ),
    Scenario(
        "bad_seed_lines_are_kept_and_their_days_backfilled",
        CALLS,
        [hits(THREE), total(THREE), *REFERRERS],
        config=CONFIG,
        seed={FILE: row("2026-09-16", 1) + "not json\n\n" + '{"pageviews": 5}\n' + row("2026-09-18", 1)},
    ),
    Scenario(
        "a_seed_line_not_an_object_writes_nothing",
        CALLS,
        [hits(THREE), total(THREE), *REFERRERS],
        config=CONFIG,
        seed={FILE: row("2026-09-21", 4) + '["2026-09-20"]\n'},
    ),
    Scenario("skips_without_a_token", CALLS, env={}, config=CONFIG),
]

MORE_DATA = "more data is captured than the script did"
FAILURE = "ADR: a failure is recorded as a failure"
PARTIAL = "ADR: a partly failed row keeps its good parts"
DIVERGENCES = {
    # leadline also keeps the top pages, browsers, systems and locations of the week on yesterday's row (an empty
    # list on the other days'), which the script's rows lack.
    "week_backfills_missing_days_and_refreshes_yesterday": MORE_DATA,
    "retries_the_pages_request": MORE_DATA,
    "no_stat_for_yesterday_backfills_only_earlier_days": MORE_DATA,
    "pages_without_an_id_have_no_referrers": MORE_DATA,
    "an_undated_stat_counts_only_in_the_window_sum": MORE_DATA,
    "bad_seed_lines_are_kept_and_their_days_backfilled": MORE_DATA,
    "three_failed_attempts_write_nothing": FAILURE,
    "three_empty_answers_write_nothing": FAILURE,
    "unparseable_hits_write_nothing": FAILURE,
    "referrers_of_ten_pages_and_no_window_total": PARTIAL,
    "unparseable_total_and_referrers_count_as_none": PARTIAL,
    "a_seed_line_not_an_object_writes_nothing": "ADR: store never fails on a line it can't use",
}
# Scenarios whose run reaches yesterday's row, so leadline also asks for the stats the script never does.
READS_STATS = (
    *(name for name, why in DIVERGENCES.items() if why in (MORE_DATA, PARTIAL)),
    "a_seed_line_not_an_object_writes_nothing",
)
