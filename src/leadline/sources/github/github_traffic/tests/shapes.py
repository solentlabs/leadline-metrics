"""The kind of response the oracle's shape test checks for github_traffic: a repository's referrers."""

from __future__ import annotations

from scenarios.builders import common
from shape_kind import Kind

from leadline.sources.github.tests import builders as github

KINDS = [
    Kind(
        "GitHub traffic referrers",
        lambda r: r["url"].endswith("/traffic/popular/referrers"),
        [
            common.exchange(
                "referrers", [github.github_referrer("github.com", 10, 3), github.github_referrer("example.com", 2, 1)]
            )
        ],
    ),
]
