"""The kind of response the oracle's shape test checks for github_discussions: the GraphQL discussions query."""

from __future__ import annotations

from scenarios.builders import common
from shape_kind import Kind

from leadline.sources.github.tests import builders as github

KINDS = [
    Kind(
        "GitHub GraphQL discussions",
        lambda r: r["url"] == github.GITHUB_GRAPHQL and "discussions(" in r["body"],
        [
            common.exchange(
                github.GITHUB_GRAPHQL,
                github.github_discussions(
                    [github.github_discussion(1, answered=True), github.github_discussion(2, category="Ideas")]
                ),
                method="POST",
            )
        ],
    ),
]
