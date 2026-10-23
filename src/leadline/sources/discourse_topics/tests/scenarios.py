"""discourse_topics: views, replies and likes of topics on a Discourse forum.

The entry names the forum (`base_url`) and its `topics` (id and label). The script's collect_forum
reads each of FORUM_THREADS (topic_id:label) from the one forum it knows, and writes one row per
thread for the run's date into forum.jsonl: views, replies (posts but the first), likes and the day
of the last post. A row replaces only the same thread's row for the
same day, the thread matched as a number, so a row whose topic_id is a string stays beside it; blank
lines are dropped. A thread that fails to fetch (a connection or an error status) or parse (a null
posts_count too) is skipped; the others are still written.

leadline differs by decision on the scenarios in DIVERGENCES, asserted in test_discourse_topics.py:
a thread that fails to fetch or parse writes its failure row instead of being skipped, and a topic without
its views or likes is a `parse_failed` row naming them, not zeros. The rows also hold the topic's
`participants`, `closed`, `solved` and `tags`, which the script drops: the scenarios that write a row from the
forum's answer are in DIVERGENCES for them (more data is captured than the script did).
"""

from __future__ import annotations

from typing import Any

from scenarios import Scenario
from scenarios.builders.common import Exchange, Json, exchange, failed

from leadline.sources.discourse_topics.tests.builders import discourse_post, discourse_topic

CALLS = ["collect_forum"]
FILE = "forum.jsonl"
TWO = {"FORUM_THREADS": ["1001:main", "1002:other-thread"]}
BASE_URL = "https://community.home-assistant.io"  # the one forum the script knows


def leadline_config(scenario: Scenario) -> list[dict[str, Any]]:
    """leadline's config for `scenario`: the script's forum, its FORUM_THREADS as topics, the script's file."""
    threads = [thread.split(":", 1) for thread in scenario.config["FORUM_THREADS"]]
    return [{"base_url": BASE_URL, "topics": [{"id": int(i), "label": label} for i, label in threads], "file": FILE}]


def topic_url(topic_id: int) -> str:
    return f"{BASE_URL}/t/{topic_id}.json"


def topic(topic_id: int, views: int, posts: int, likes: int) -> Exchange:
    return exchange(topic_url(topic_id), discourse_topic(topic_id, views=views, posts_count=posts, like_count=likes))


# A topic opened on 2026-09-18, its five posts' creation; the topic's answer holds the first three, and the forum
# gives the rest by id.
POSTED = ["2026-09-18T10:00:00.000Z", "2026-09-18T20:00:00.000Z", "2026-09-20T09:00:00.000Z"]
POSTED += ["2026-09-20T11:00:00.000Z", "2026-09-22T08:00:00.000Z"]
POSTS = [{**discourse_post(1001, n, "example-author"), "created_at": when} for n, when in enumerate(POSTED, 1)]


def history_topic() -> Json:
    """The topic's answer: five posts, the first three in it, the stream naming all five."""
    answer = discourse_topic(1001, views=500, posts_count=5, like_count=7, last_posted_at=POSTED[-1])
    stream = {"posts": POSTS[:3], "stream": [post["id"] for post in POSTS]}
    return {**answer, "created_at": POSTED[0], "post_stream": stream}


def posts_url(topic_id: int, ids: list[int]) -> str:
    return f"{BASE_URL}/t/{topic_id}/posts.json?" + "&".join(f"post_ids[]={i}" for i in ids)


HISTORY = [
    exchange(topic_url(1001), history_topic()),
    exchange(posts_url(1001, [p["id"] for p in POSTS[3:]]), {"post_stream": {"posts": POSTS[3:]}, "id": 1001}),
]
# Another topic's row, earlier, which isn't this topic's first; and this topic's first, on 2026-09-21.
LATE = (
    '{"date": "2026-09-10", "topic_id": 1002, "label": "other-thread", "views": 70}\n'
    '{"date": "2026-09-21", "topic_id": 1001, "label": "main", "views": 450, "replies": 3, "likes": 6, '
    '"last_post": "2026-09-20"}\n'
)
LATE_LINES = LATE.splitlines(keepends=True)
STAMP = '"captured_at": "2026-09-23T23:31:05+00:00"}\n'
# The topic's days before its first row, back to its creation (ADR: past values a source can rebuild are filled,
# never changed): its replies and last post by the end of each day; null for its views and likes then.
REBUILT = "".join(
    f'{{"date": "2026-09-{day}", "topic_id": 1001, "label": "main", "views": null, "replies": {replies}, '
    f'"likes": null, "last_post": "2026-09-{last}", "backfilled": true, ' + STAMP
    for day, replies, last in ((18, 1, 18), (19, 1, 18), (20, 3, 20))
)
TODAY = (
    '{"date": "2026-09-23", "topic_id": 1001, "label": "main", "views": 500, "replies": 4, "likes": 7, '
    '"last_post": "2026-09-22", "participants": 3, "closed": false, "solved": false, "tags": ["example"], ' + STAMP
)

SCENARIOS = [
    Scenario("two_threads", CALLS, [topic(1001, 5400, 61, 88), topic(1002, 700, 5, 2)], config=TWO),
    Scenario(
        "failed_or_unparseable_threads_are_skipped",
        CALLS,
        [
            failed(topic_url(1001)),
            exchange(topic_url(1002), "<html>Oops</html>"),
            topic(1003, 90, 2, 0),
            exchange(topic_url(1004), '{"errors":["The requested URL or resource could not be found."]}', status=404),
            exchange(
                topic_url(1005), {**discourse_topic(1005, views=10, posts_count=1, like_count=0), "posts_count": None}
            ),
        ],
        config={
            "FORUM_THREADS": ["1001:main", "1002:other-thread", "1003:third-thread", "1004:gone", "1005:no-posts-count"]
        },
    ),
    Scenario(
        "missing_counts_read_as_zero",
        CALLS,
        [exchange(topic_url(1001), {"id": 1001, "last_posted_at": None}), topic(1002, 700, 1, 2)],
        config=TWO,
    ),
    Scenario(
        "a_rerun_replaces_only_the_same_thread_and_day",
        CALLS,
        [topic(1001, 5500, 62, 90)],
        config={"FORUM_THREADS": ["1001:main"]},
        seed={
            FILE: '{"date": "2026-09-22", "topic_id": 1001, "label": "main", "views": 5300}\n'
            '{"date": "2026-09-23", "topic_id": 1001, "label": "main", "views": 5400}\n'
            '{"date": "2026-09-23", "topic_id": "1001", "label": "main", "views": 5400}\n'  # a string: stays
            "\n"  # dropped
            "not json\n"
            '{"date": "2026-09-23", "topic_id": 1002, "label": "other-thread", "views": 700}\n'
        },
    ),
    # leadline's own, which the script doesn't do: reviewed rows.
    Scenario(
        "a_topic_starting_late_gets_rows_back_to_its_creation",
        [],
        HISTORY,
        config={"FORUM_THREADS": ["1001:main"]},
        seed={FILE: LATE},
        expected={FILE: LATE_LINES[0] + REBUILT + LATE_LINES[1] + TODAY},
    ),
]

MORE_DATA = "more data is captured than the script did"
DIVERGENCES = {
    # leadline also keeps each topic's participants, closed, solved and tags, which the script's rows lack.
    "two_threads": MORE_DATA,
    "a_rerun_replaces_only_the_same_thread_and_day": MORE_DATA,
    "failed_or_unparseable_threads_are_skipped": "ADR: a failure is recorded as a failure",
    "missing_counts_read_as_zero": "ADR: a failure is recorded as a failure",
}
