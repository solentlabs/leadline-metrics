"""The kind of response the oracle's shape test checks for github_response_times: the GraphQL issues query."""

from __future__ import annotations

from scenarios.builders import common
from shape_kind import Kind

from leadline.sources.github.tests import builders as github

KINDS = [
    Kind(
        "GitHub GraphQL issues with comments",
        lambda r: r["url"] == github.GITHUB_GRAPHQL and "issues(" in r["body"],
        [
            common.exchange(
                github.GITHUB_GRAPHQL,
                github.github_issue_nodes(
                    [
                        github.github_issue_node(
                            1,
                            "2026-09-01T10:00:00Z",
                            author="example-user",
                            closed_at="2026-09-03T10:00:00Z",
                            comments=[("2026-09-01T12:00:00Z", "example-maintainer")],
                        ),
                        github.github_issue_node(2, "2026-09-02T10:00:00Z", author="another-user"),
                    ],
                    next_page=True,
                ),
                method="POST",
            )
        ],
    ),
]
