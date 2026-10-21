"""The kind of response the oracle's shape test checks for github_contributor_locations: a user's profile."""

from __future__ import annotations

from scenarios.builders import common
from shape_kind import Kind

from leadline.sources.github.tests import builders as github

KINDS = [
    Kind(
        "GitHub user profile",
        lambda r: r["url"].startswith(f"{github.GITHUB_API}/users/"),
        [
            common.exchange("user", github.github_profile("example-user", "Yondershire, England")),
            common.exchange("user", github.github_profile("example-maintainer", own=True)),
        ],
    ),
]
