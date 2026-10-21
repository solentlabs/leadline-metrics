"""The kinds of response the oracle's shape test checks for the GitHub API that several GitHub sources read."""

from __future__ import annotations

import random
import re
from datetime import date, datetime, timedelta
from typing import Any

from scenarios.builders import common
from shape_kind import Kind

from leadline.sources.github.tests import builders as github

NOW = datetime(2026, 9, 23, 23, 31, 5)  # noqa: DTZ001 (the scenarios' clock is naive local time)
DAY = date(2026, 9, 22)
WEEK = [DAY - timedelta(days=n) for n in range(6, -1, -1)]
REPO = "solentlabs/example"


def _issues() -> list[dict[str, Any]]:
    """Every variant of an issue list's items: open and closed issues, merged and unmerged pull requests."""
    rng = random.Random(7)
    return [
        github.github_issue(
            REPO, 1, labels=["new modem"], assignees=["example-maintainer"], milestone=github.github_milestone(REPO, 1)
        ),
        github.github_issue(REPO, 2, closed_at="2026-09-10T10:00:00Z"),
        github.github_issue(REPO, 3, pull_request=True, closed_at="2026-09-12T10:00:00Z"),
        github.github_issue(REPO, 4, pull_request=True, merged=False, closed_at="2026-09-12T10:00:00Z"),
        github.github_issue(REPO, 5, pull_request=True, author="dependabot[bot]"),
        *github.made_up_issues(rng, REPO, 250, now=NOW, first=6),
    ]


KINDS = [
    Kind(
        "GitHub repository",
        lambda r: re.fullmatch(r"https://api\.github\.com/repos/[^/]+/[^/?]+", r["url"]) is not None,
        [common.exchange(f"{github.GITHUB_API}/repos/{REPO}", github.github_repo(REPO, stars=12, forks=3, watchers=4))],
    ),
    Kind(
        "GitHub issues, one page of a list",
        lambda r: "/issues?state=" in r["url"],
        github.github_issue_pages(REPO, "all", _issues()),
        unverified=frozenset({"$[].milestone"}),
    ),
    Kind(
        "GitHub traffic views",
        lambda r: r["url"].endswith("/traffic/views"),
        [common.exchange("views", github.github_traffic("views", dict.fromkeys(WEEK, (5, 2))))],
    ),
    Kind(
        "GitHub traffic clones",
        lambda r: r["url"].endswith("/traffic/clones"),
        [common.exchange("clones", github.github_traffic("clones", dict.fromkeys(WEEK, (3, 1))))],
    ),
    Kind(
        "GitHub releases",
        lambda r: "/releases?" in r["url"],
        github.github_numbered_pages(
            REPO,
            "releases",
            "per_page=2",
            [
                github.github_release(
                    REPO,
                    "v1.1.0",
                    assets=[github.github_release_asset(REPO, "v1.1.0", "example.zip", 7)],
                    mentions=1,
                    reactions=True,
                ),
                github.github_release(REPO, "v1.0.0", mentions=0),
                github.github_release(REPO, "v1.0.0-beta.1", prerelease=True),
            ],
            per_page=2,
            numbered=False,
        ),
    ),
]
