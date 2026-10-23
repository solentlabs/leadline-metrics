"""github_repo_stats: a second repository's stars, forks, watchers and open issues, with its traffic.

The script's collect_har_capture_github reads HAR_CAPTURE_REPO, then its 14-day views and clones,
and writes one row for the run's date into github-har-capture.jsonl. A failed views or clones fetch
counts as zero; a failed repository fetch writes nothing. The script pastes each body into a Python
string literal, so a body that doesn't parse, or one holding a backslash escape or ''', writes
nothing.

Where leadline differs by a decision, the scenario is in DIVERGENCES, and
tests/test_github_repo_stats.py asserts leadline's side: leadline reads the JSON itself, so a quote
or ''' in the repository writes the real row; a failed or unusable repository writes a failure
row (`fetch_failed` or `parse_failed`) for the run's date (ADR: a failure is recorded as a failure);
and failed or unusable views or clones leave those fields null in a partial row, never 0 (ADR: a
partly failed row keeps its good parts). The rows also hold the repository's `network_count`, `size_kb` and
`pushed_at`, which the script drops: the scenario that writes a row is in DIVERGENCES for them (more data is
captured than the script did).
"""

from __future__ import annotations

from datetime import date, timedelta

from scenarios import Scenario
from scenarios.builders.common import Exchange, exchange, failed

from leadline.sources.github.tests.builders import GITHUB_API, github_repo, github_traffic

REPO = "solentlabs/example-tool"
CONFIG = {"HAR_CAPTURE_REPO": REPO}
CALLS = ["collect_har_capture_github"]
FILE = "github-har-capture.jsonl"
REPO_URL = f"{GITHUB_API}/repos/{REPO}"
VIEWS, CLONES = f"{REPO_URL}/traffic/views", f"{REPO_URL}/traffic/clones"
WINDOW = [date(2026, 9, 23) - timedelta(days=n) for n in range(13, -1, -1)]
REPOSITORY = exchange(REPO_URL, github_repo(REPO, stars=41, forks=6, watchers=5, open_issues=3))
TRAFFIC = [
    exchange(VIEWS, github_traffic("views", {d: (9 + i, 3) for i, d in enumerate(WINDOW)})),
    exchange(CLONES, github_traffic("clones", dict.fromkeys(WINDOW, (2, 1)))),
]
# leadline's config for these scenarios: the script's repo, writing the script's file.
LEADLINE_CONFIG = [{"repo": REPO, "file": FILE}]

MORE_DATA = "more data is captured than the script did"

# The scenarios where leadline's rows differ from the script's by a decision, and the decision.
DIVERGENCES = {
    # leadline also keeps the repository's network_count, size_kb and pushed_at, which the script's rows lack.
    "repository_and_its_traffic": MORE_DATA,
    "failed_traffic_counts_as_zero": "ADR: a partly failed row keeps its good parts",
    "unparseable_traffic_writes_nothing": "ADR: a partly failed row keeps its good parts",
    "failed_repository_writes_nothing": "ADR: a failure is recorded as a failure",
    "unparseable_repository_writes_nothing": "ADR: a failure is recorded as a failure",
    "an_escaped_quote_in_the_repository_writes_nothing": "the port reads the JSON, not code pasted from it",
    "triple_quotes_in_the_repository_write_nothing": "the port reads the JSON, not code pasted from it",
}
EARLIER = {FILE: '{"date": "2026-09-22", "repo": "solentlabs/example-tool", "stars": 40}\n'}


def described(description: str) -> Exchange:
    """The repository, with `description` in its body."""
    return exchange(REPO_URL, {**github_repo(REPO, stars=41), "description": description})


SCENARIOS = [
    Scenario("repository_and_its_traffic", CALLS, [REPOSITORY, *TRAFFIC], config=CONFIG),
    Scenario(
        "failed_traffic_counts_as_zero",
        CALLS,
        [REPOSITORY, exchange(VIEWS, '{"message":"Must have push access"}', status=403), failed(CLONES)],
        config=CONFIG,
    ),
    Scenario(
        "failed_repository_writes_nothing",
        CALLS,
        [exchange(REPO_URL, "", status=502)],
        config=CONFIG,
        seed=EARLIER,
    ),
    Scenario(
        "unparseable_repository_writes_nothing",
        CALLS,
        [exchange(REPO_URL, "<html>maintenance</html>"), *TRAFFIC],
        config=CONFIG,
        seed=EARLIER,
    ),
    Scenario(
        # Unlike a failed fetch, which counts as zero.
        "unparseable_traffic_writes_nothing",
        CALLS,
        [REPOSITORY, exchange(VIEWS, "<html>maintenance</html>"), TRAFFIC[1]],
        config=CONFIG,
        seed=EARLIER,
    ),
    Scenario(
        # The literal reads \" as ", so the JSON breaks.
        "an_escaped_quote_in_the_repository_writes_nothing",
        CALLS,
        [described('Turns a "HAR" file into a report.'), *TRAFFIC],
        config=CONFIG,
        seed=EARLIER,
    ),
    Scenario(
        # ''' ends the literal.
        "triple_quotes_in_the_repository_write_nothing",
        CALLS,
        [described("Reads '''quoted''' notes."), *TRAFFIC],
        config=CONFIG,
        seed=EARLIER,
    ),
    Scenario("skips_without_a_token", CALLS, env={}, config=CONFIG),
]
