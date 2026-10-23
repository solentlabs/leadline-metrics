"""github_traffic: a repository's views, clones and referrers over GitHub's 14-day traffic window.

The script's collect_github_traffic reads the 14-day views and clones and the popular referrers, and
writes one row for the run's date into github-traffic.jsonl: the totals, their ratio of total to
unique, the last day the window holds, and the top five referrers (an empty list when that request fails). A
failed views or clones fetch, or a body that doesn't parse, writes nothing. The script pastes the
clones and referrers into Python string literals, so a backslash escape or ''' in them writes no
row, and nor do referrers that aren't a list. The same window day by day is github_traffic_daily.

Where leadline differs by a decision, the scenario is in DIVERGENCES, and
tests/test_github_traffic.py asserts leadline's side: leadline reads the JSON itself, so a quote or
''' in the referrers writes the real row, and a failed views or clones fetch or an answer it
can't use writes a failure row (`fetch_failed` or `parse_failed`) for the run's date, where the script
writes nothing (ADR: a failure is recorded as a failure). Referrers that fail or can't be used leave
`top_referrers` null in a `partial` row, where the script writes an empty list (ADR: a partly failed row
keeps its good parts). The script never reads the popular paths, so leadline's rows have `top_paths` besides,
and the scenarios that write a row are in DIVERGENCES for it (more data is captured than the script did);
`with_paths` gives leadline's one more response.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Mapping, Sequence

from scenarios import Scenario
from scenarios.builders.common import Exchange, exchange, failed

from leadline.sources.github.tests.builders import GITHUB_API, github_path, github_referrer, github_traffic

REPO = "solentlabs/example"
CONFIG = {"GITHUB_REPO": REPO}
CALLS = ["collect_github_traffic"]
FILE = "github-traffic.jsonl"
VIEWS, CLONES = f"{GITHUB_API}/repos/{REPO}/traffic/views", f"{GITHUB_API}/repos/{REPO}/traffic/clones"
REFERRERS = f"{GITHUB_API}/repos/{REPO}/traffic/popular/referrers"
PATHS = f"{GITHUB_API}/repos/{REPO}/traffic/popular/paths"
TODAY = date(2026, 9, 23)
WINDOW = [TODAY - timedelta(days=n) for n in range(13, -1, -1)]  # the API's 14 days, today's still counting

VIEWS_BY_DAY = {day: (20 + 3 * i, 4 + i % 5) for i, day in enumerate(WINDOW)}
CLONES_BY_DAY = {day: (6 + i % 4, 2 + i % 3) for i, day in enumerate(WINDOW) if day != date(2026, 9, 15)}
HOSTS = ["github.com", "example.com", "forum.example", "search.example", "news.example", "blog.example", "t.example"]
TOP_REFERRERS = [github_referrer(host, 40 - 5 * i, 12 - i) for i, host in enumerate(HOSTS)]
# The popular paths, which the script doesn't read: twelve, the API's own order (most views first), of which
# leadline keeps ten.
TOP_PATHS = [
    github_path(f"/{REPO}" + ("" if i == 0 else f"/page-{i}"), f"{REPO}: page {i}", 60 - 4 * i, 20 - i)
    for i in range(12)
]

# leadline's config for these scenarios: the script's repo, writing the script's file.
LEADLINE_CONFIG = [{"repo": REPO, "file": FILE}]

MORE_DATA = "more data is captured than the script did"

# The scenarios where leadline's rows differ from the script's by a decision, and the decision.
DIVERGENCES = {
    # leadline also reads the popular paths, so each of these has `top_paths`, which the script's rows lack.
    "snapshot_with_the_top_five_referrers": MORE_DATA,
    "a_window_ending_before_the_run_date": MORE_DATA,
    "items_without_a_timestamp": MORE_DATA,
    "an_empty_window": MORE_DATA,
    "failed_referrers_leave_the_list_empty": "ADR: a partly failed row keeps its good parts",
    "an_escaped_quote_in_the_referrers_writes_nothing": "the port reads the JSON, not code pasted from it",
    "triple_quotes_in_the_referrers_write_nothing": "the port reads the JSON, not code pasted from it",
    "referrers_not_a_list_write_nothing": "ADR: a partly failed row keeps its good parts",
    "unparseable_views_write_nothing": "ADR: a failure is recorded as a failure",
    "failed_views_write_nothing": "ADR: a failure is recorded as a failure",
    "failed_clones_write_nothing": "ADR: a failure is recorded as a failure",
}


def responses(
    views: Mapping[date, tuple[int, int]] = VIEWS_BY_DAY,
    clones: Mapping[date, tuple[int, int]] = CLONES_BY_DAY,
    referrers: Exchange | None = None,
) -> list[Exchange]:
    """Views, clones and referrers, in the order the script asks for them."""
    return [
        exchange(VIEWS, github_traffic("views", views)),
        exchange(CLONES, github_traffic("clones", clones)),
        referrers or exchange(REFERRERS, TOP_REFERRERS),
    ]


def with_paths(answers: Sequence[Exchange], paths: Exchange | None = None) -> list[Exchange]:
    """The script's responses and leadline's one more, the popular paths, which the script never asks for.
    A run that stops before the referrers is given none."""
    reaches = any(a["request"]["url"] == REFERRERS for a in answers)
    return [*answers, *([paths or exchange(PATHS, TOP_PATHS)] if reaches else [])]


def within(by_day: Mapping[date, tuple[int, int]], days: list[date]) -> dict[date, tuple[int, int]]:
    """The counts for those of `days` that `by_day` has."""
    return {day: n for day, n in by_day.items() if day in days}


# Today's views with no timestamp: window_end leaves it out.
UNSTAMPED = github_traffic("views", VIEWS_BY_DAY)
del UNSTAMPED["views"][-1]["timestamp"]

SEEDED = {FILE: '{"date": "2026-09-22", "views_total": 5}\n'}

SCENARIOS = [
    Scenario("snapshot_with_the_top_five_referrers", CALLS, responses(), config=CONFIG),
    Scenario(
        "failed_referrers_leave_the_list_empty",
        CALLS,
        responses(referrers=exchange(REFERRERS, "", status=500)),
        config=CONFIG,
    ),
    Scenario(
        # GitHub's traffic lags: the window can end days before the run, and window_end says where.
        "a_window_ending_before_the_run_date",
        CALLS,
        responses(within(VIEWS_BY_DAY, WINDOW[:-2]), within(CLONES_BY_DAY, WINDOW[:-2])),
        config=CONFIG,
    ),
    Scenario(
        "items_without_a_timestamp",
        CALLS,
        [exchange(VIEWS, UNSTAMPED), *responses()[1:]],
        config=CONFIG,
        seed=SEEDED,
    ),
    Scenario(
        # The referrers are pasted into a Python string literal, which reads \" as ", so the JSON breaks.
        "an_escaped_quote_in_the_referrers_writes_nothing",
        CALLS,
        responses(referrers=exchange(REFERRERS, [github_referrer('say "hi".example', 3, 1)])),
        config=CONFIG,
        seed=SEEDED,
    ),
    Scenario(
        # ''' in a referrer ends the Python literal it is pasted into.
        "triple_quotes_in_the_referrers_write_nothing",
        CALLS,
        responses(referrers=exchange(REFERRERS, [github_referrer("it'''s.example", 3, 1)])),
        config=CONFIG,
        seed=SEEDED,
    ),
    Scenario(
        "referrers_not_a_list_write_nothing",
        CALLS,
        responses(referrers=exchange(REFERRERS, {"message": "Not a list"})),
        config=CONFIG,
        seed=SEEDED,
    ),
    Scenario(
        "unparseable_views_write_nothing",
        CALLS,
        [exchange(VIEWS, "<html>maintenance</html>"), *responses()[1:]],
        config=CONFIG,
        seed=SEEDED,
    ),
    Scenario("an_empty_window", CALLS, responses({}, {}, exchange(REFERRERS, [])), config=CONFIG, seed=SEEDED),
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
