"""The kind of response the oracle's shape test checks for github_tree_counts: a repository's tree."""

from __future__ import annotations

from scenarios.builders import common
from shape_kind import Kind

from leadline.sources.github.tests import builders as github

REPO = "solentlabs/example"


KINDS = [
    Kind(
        "GitHub repository tree",
        lambda r: "/git/trees/" in r["url"],
        [
            common.exchange(
                "tree", github.github_tree(REPO, ["README.md", "catalog/modems/example/x1/modem.yaml", "run.sh"])
            )
        ],
    ),
]
