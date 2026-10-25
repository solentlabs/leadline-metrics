"""discourse_forum: a Discourse forum's site statistics from its about.json.

The script has no collector for it, so each scenario carries the rows the source must leave (`expected`), written by
hand and reviewed; they are the source's golden. Every answer is built by `discourse_about` from made-up values (no
capture holds this answer; the builder follows Discourse's about.json).
"""

from __future__ import annotations

from typing import Any

from scenarios import Scenario
from scenarios.builders.common import Exchange, exchange

from leadline.sources.discourse_forum.tests.builders import discourse_about

BASE_URL = "https://forum.example.com"
URL = f"{BASE_URL}/about.json"
FILE = "forum-site.jsonl"
STAMP = '"captured_at": "2026-09-23T23:31:05+00:00"}\n'
LEADLINE_CONFIG: list[dict[str, Any]] = [{"base_url": BASE_URL, "file": FILE}]
DIVERGENCES: dict[str, str] = {}  # the script has no collector for this source

WITH_CHAT = (
    '{"date": "2026-09-23", "base_url": "https://forum.example.com", '
    '"topics_last_day": 20, "topics_7_days": 111, "topics_30_days": 500, "topics_count": 100000, '
    '"posts_last_day": 100, "posts_7_days": 666, "posts_30_days": 3000, "posts_count": 600000, '
    '"users_last_day": 5, "users_7_days": 30, "users_30_days": 150, "users_count": 45000, '
    '"active_users_last_day": 60, "active_users_7_days": 300, "active_users_30_days": 900, '
    '"likes_last_day": 66, "likes_7_days": 500, "likes_30_days": 2400, "likes_count": 200000, '
    '"participating_users_last_day": 33, "participating_users_7_days": 150, "participating_users_30_days": 300, '
    '"chat_messages_last_day": 40, "chat_messages_count": 9000, "chat_messages_7_days": 300, ' + STAMP
)

SMALL = (
    '{"date": "2026-09-23", "base_url": "https://forum.example.com", '
    '"topics_last_day": 0, "topics_7_days": 2, "topics_30_days": 10, "topics_count": 2000, '
    '"posts_last_day": 1, "posts_7_days": 10, "posts_30_days": 45, "posts_count": 9000, '
    '"users_last_day": 0, "users_7_days": 0, "users_30_days": 3, "users_count": 1000, '
    '"active_users_last_day": 8, "active_users_7_days": 40, "active_users_30_days": 120, '
    '"likes_last_day": 1, "likes_7_days": 7, "likes_30_days": 36, "likes_count": 3000, '
    '"participating_users_last_day": 4, "participating_users_7_days": 20, "participating_users_30_days": 40, ' + STAMP
)
YESTERDAY = '{"date": "2026-09-22", "base_url": "https://forum.example.com", "users_count": 44990}\n'
EARLIER = '{"date": "2026-09-23", "base_url": "https://forum.example.com", "users_count": 44000}\n'


def about(**changes: Any) -> Exchange:
    """The forum's about.json answer: the made-up forum with chat, with `changes` to its arguments."""
    arguments = {"users": 45000, "topics": 100000, "posts": 600000, "active_7_days": 300, **changes}
    return exchange(URL, discourse_about(**arguments))


SCENARIOS = [
    Scenario("writes_the_days_site_statistics", [], [about()], expected={FILE: WITH_CHAT}),
    Scenario(
        "a_forum_without_chat_has_fewer_fields",
        [],
        [about(users=1000, topics=2000, posts=9000, active_7_days=40, chat=False)],
        expected={FILE: SMALL},
    ),
    Scenario(
        "a_rerun_replaces_only_the_same_day",
        [],
        [about()],
        seed={FILE: YESTERDAY + EARLIER},
        expected={FILE: YESTERDAY + WITH_CHAT},
    ),
]
