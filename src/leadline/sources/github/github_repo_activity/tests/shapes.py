"""The kinds of response the oracle's shape test checks for github_repo_activity: commits, contributors, languages."""

from __future__ import annotations

from scenarios.builders import common
from shape_kind import Kind

from leadline.sources.github.tests import builders as github

REPO = "solentlabs/example"


KINDS = [
    Kind(
        "GitHub commits",
        lambda r: "/commits?" in r["url"],
        github.github_numbered_pages(
            REPO,
            "commits",
            "since=2026-09-16T00:00:00Z&per_page=2",
            [
                github.github_commit(REPO, "Fix a crash\n\nDetails.", when="2026-09-20T10:00:00Z"),
                github.github_commit(REPO, "Refactor the parser", author="another-user", when="2026-09-19T10:00:00Z"),
                github.github_commit(REPO, "Add a modem", when="2026-09-18T10:00:00Z"),
            ],
            per_page=2,
        ),
        unverified_link=True,
    ),
    Kind(
        "GitHub contributors",
        lambda r: "/contributors?" in r["url"],
        github.github_numbered_pages(
            REPO,
            "contributors",
            "per_page=2",
            [github.github_contributor(login, n) for login, n in (("example-user", 9), ("another-user", 3), ("x", 1))],
            per_page=2,
            numbered=False,
        ),
    ),
    Kind(
        "GitHub languages",
        lambda r: r["url"].endswith("/languages"),
        [common.exchange("languages", {"Python": 50000, "Shell": 3000})],
        maps=frozenset({"$"}),
    ),
]
