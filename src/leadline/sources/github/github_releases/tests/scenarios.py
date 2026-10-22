"""github_releases: every release of a repository, with each asset's downloads.

The script's collect_github_releases REPO reads the first 100 releases and writes one row for the
run's date into github-releases-<repo name>.jsonl: each release's tag, whether it is a prerelease or
a draft, when it was published, and each asset's name, downloads and size. A failed fetch, a body
that doesn't parse, or an error object in place of the list writes nothing.

Where leadline differs by a decision, the scenario is in DIVERGENCES, and
tests/test_github_releases.py asserts leadline's side: each of those answers writes a failure row
(`fetch_failed` or `parse_failed`) for the run's date, where the script writes nothing, and a release or
asset without a field the script defaults (a flag, a count) is a `parse_failed` row naming it (ADR: a failure
is recorded as a failure).
"""

from __future__ import annotations

from typing import Sequence

from scenarios import Scenario
from scenarios.builders.common import Exchange, Json, exchange

from leadline.sources.github.tests.builders import github_numbered_pages, github_release, github_release_asset

REPO = "solentlabs/example"
CALLS = [f'collect_github_releases "{REPO}"']
FILE = "github-releases-example.jsonl"

# leadline's config for these scenarios: the script's repo, whose default file is the script's.
LEADLINE_CONFIG = [{"repo": REPO}]

# The scenarios where leadline's rows differ from the script's by a decision, and the decision.
DIVERGENCES = {
    "failed_fetch_writes_nothing": "ADR: a failure is recorded as a failure",
    "unparseable_body_writes_nothing": "ADR: a failure is recorded as a failure",
    "a_body_not_a_list_writes_nothing": "ADR: a failure is recorded as a failure",
    "missing_fields_take_the_defaults": "ADR: a failure is recorded as a failure",
}


def releases(items: Sequence[Json]) -> list[Exchange]:
    return github_numbered_pages(REPO, "releases", "per_page=100", items, per_page=100, numbered=False)


def asset(tag: str, name: str, downloads: int, size: int = 2048) -> Json:
    return github_release_asset(REPO, tag, name, downloads, size)


EVERY_KIND = [
    github_release(REPO, "v2.0.0-beta.1", published_at=None, draft=True),
    github_release(
        REPO,
        "v1.1.0-beta.2",
        prerelease=True,
        published_at="2026-09-10T08:00:00Z",
        assets=[asset("v1.1.0-beta.2", "example.zip", 12)],
        mentions=1,
    ),
    github_release(
        REPO,
        "v1.0.0",
        published_at="2026-08-01T08:00:00Z",
        assets=[asset("v1.0.0", "example.zip", 140, 4096), asset("v1.0.0", "example-docs.zip", 9, 1024)],
        reactions=True,
    ),
    github_release(REPO, "v0.9.0", published_at="2026-06-01T08:00:00Z"),
]

URL = releases([])[0]["request"]["url"]
EARLIER = {FILE: '{"date": "2026-09-22", "repo": "solentlabs/example", "releases": []}\n'}
# A release, and an asset, with only the fields the script can't do without: the rest take defaults.
BARE = [{"tag_name": "v0.1.0", "assets": [{"name": "example.zip"}]}, {}]

SCENARIOS = [
    Scenario("every_release_with_its_asset_downloads", CALLS, releases(EVERY_KIND)),
    Scenario("a_repository_without_releases", CALLS, releases([])),
    Scenario("missing_fields_take_the_defaults", CALLS, releases(BARE)),
    Scenario("failed_fetch_writes_nothing", CALLS, [exchange(URL, "", status=500)], seed=EARLIER),
    Scenario("unparseable_body_writes_nothing", CALLS, [exchange(URL, "<html>maintenance</html>")], seed=EARLIER),
    Scenario(
        "a_body_not_a_list_writes_nothing",
        CALLS,
        [exchange(URL, {"message": "Moved Permanently"})],
        seed=EARLIER,
    ),
    Scenario("skips_without_a_token", CALLS, env={}),
]
