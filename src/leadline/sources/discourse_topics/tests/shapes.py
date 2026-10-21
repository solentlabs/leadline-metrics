"""The kind of response the oracle's shape test checks for discourse_topics: a topic."""

from __future__ import annotations

import re

from scenarios.builders import common
from shape_kind import Kind

from leadline.sources.discourse_topics.tests import builders as discourse

KINDS = [
    Kind(
        "Discourse topic",
        lambda r: re.fullmatch(r"https://community\.home-assistant\.io/t/\d+\.json", r["url"]) is not None,
        [
            common.exchange("topic", discourse.discourse_topic(1001, views=500, posts_count=5, like_count=9)),
            common.exchange(
                "topic", discourse.discourse_topic(1002, views=50, posts_count=5, like_count=1, shared_issues=True)
            ),
        ],
    ),
]
