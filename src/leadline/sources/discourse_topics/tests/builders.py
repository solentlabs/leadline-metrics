"""Discourse builders."""

from __future__ import annotations

from scenarios.builders.common import Json, stable_id


def _discourse_user(username: str) -> Json:
    return {
        "id": stable_id(username) % 10**6,
        "username": username,
        "name": username.replace("-", " ").title(),
        "avatar_template": f"/user_avatar/forum.example/{username}/{{size}}/1_2.png",
    }


def discourse_post(
    topic_id: int, number: int, username: str, *, reply_to: str | None = None, link: bool = False
) -> Json:
    """A post in a topic's stream. `reply_to` is the user it answers; `link` gives it counted links."""
    user = _discourse_user(username)
    when = f"2026-09-{number:02d}T10:00:00.000Z"
    post: Json = {
        "id": topic_id * 100 + number,
        "name": user["name"],
        "username": username,
        "avatar_template": user["avatar_template"],
        "created_at": when,
        "cooked": f"<p>Example post {number}.</p>",
        "post_number": number,
        "post_type": 1,
        "posts_count": 3,
        "updated_at": when,
        "reply_count": 0,
        "reply_to_post_number": number - 1 if reply_to else None,
        "quote_count": 0,
        "incoming_link_count": 0,
        "reads": 10,
        "readers_count": 9,
        "score": 1.2,
        "yours": False,
        "topic_id": topic_id,
        "topic_slug": "example-topic",
        "display_username": user["name"],
        "primary_group_name": None,
        **dict.fromkeys(("flair_name", "flair_url", "flair_bg_color", "flair_color", "flair_group_id")),
        "badges_granted": [],
        "version": 1,
        **dict.fromkeys(("can_edit", "can_delete", "can_recover", "can_see_hidden_post", "can_wiki"), False),
    }
    if link:
        post["link_counts"] = [
            {"url": "https://example.com/docs", "internal": False, "reflection": False, "title": "Docs", "clicks": 3},
            {"url": "https://example.com/other", "internal": False, "reflection": False, "clicks": 0},
        ]
    post.update(
        {
            "read": True,
            "user_title": None,
            "bookmarked": False,
            "actions_summary": [{"id": 2, "count": 1}],
            **dict.fromkeys(("moderator", "admin", "staff"), False),
            "user_id": user["id"],
            "hidden": False,
            "trust_level": 1,
            "deleted_at": None,
            "user_deleted": False,
            "edit_reason": None,
            "can_view_edit_history": True,
            "wiki": False,
            "post_url": f"/t/example-topic/{topic_id}/{number}",
            "reactions": [{"id": "heart", "type": "emoji", "count": 1}],
            "current_user_reaction": None,
            "reaction_users_count": 1,
            "current_user_used_main_reaction": False,
            "user_signature": None,
            "can_accept_answer": False,
            "can_unaccept_answer": False,
            "accepted_answer": False,
            "topic_accepted_answer": None,
        }
    )
    if reply_to:
        post["reply_to_user"] = _discourse_user(reply_to)
    if number == 1 and link:
        post["can_vote"] = False  # the forum gives it on a topic's first post
    return post


def _discourse_link(url: str, title: str | None, clicks: int, user_id: int) -> Json:
    return {
        "url": url,
        "title": title,
        "internal": False,
        "attachment": False,
        "reflection": False,
        "clicks": clicks,
        "user_id": user_id,
        "domain": "example.com",
        "root_domain": "example.com",
    }


def discourse_topic(
    topic_id: int,
    *,
    views: int,
    posts_count: int,
    like_count: int,
    last_posted_at: str | None = "2026-09-20T18:00:00.000Z",
    shared_issues: bool = False,
) -> Json:
    """A topic as `t/ID.json` gives it: its counts, and the first page of a stream of made-up posts."""
    posts = [
        discourse_post(topic_id, 1, "example-author", link=True),
        discourse_post(topic_id, 2, "example-reader", reply_to="example-author"),
        discourse_post(topic_id, 3, "example-author", reply_to="example-reader", link=True),
        discourse_post(topic_id, 4, "another-reader", link=True),
        discourse_post(topic_id, 5, "example-reader"),
    ]
    author, last = _discourse_user("example-author"), _discourse_user("example-reader")
    flair = dict.fromkeys(("primary_group_name", "flair_name", "flair_url", "flair_color", "flair_bg_color"))
    participant = {**author, "post_count": 2, **flair, "flair_group_id": None, "trust_level": 1}
    topic: Json = {
        "post_stream": {"posts": posts, "stream": [p["id"] for p in posts]},
        "timeline_lookup": [[1, 30], [3, 10]],
        "tags": [{"id": 1, "name": "example", "slug": "example"}],
        "tags_descriptions": {},
        "fancy_title": "Example topic",
        "id": topic_id,
        "title": "Example topic",
        "posts_count": posts_count,
        "created_at": "2026-01-10T10:00:00.000Z",
        "views": views,
        "reply_count": max(posts_count - 1, 0),
        "like_count": like_count,
        "last_posted_at": last_posted_at,
        "visible": True,
        "closed": False,
        "archived": False,
        "has_summary": False,
        "archetype": "regular",
        "slug": "example-topic",
        "category_id": 7,
        "word_count": 1200,
        "deleted_at": None,
        "user_id": author["id"],
        "featured_link": None,
        "pinned_globally": False,
        "pinned_at": None,
        "pinned_until": None,
        "image_url": "https://forum.example/uploads/example.png",
        "slow_mode_seconds": 0,
        "draft": None,
        "draft_key": f"topic_{topic_id}",
        "draft_sequence": None,
        "unpinned": None,
        "pinned": False,
        "current_post_number": 1,
        "highest_post_number": posts_count,
        "deleted_by": None,
        "actions_summary": [{"id": n, "count": 0, "hidden": False, "can_act": False} for n in (4, 8, 10, 7)],
        "chunk_size": 20,
        "bookmarked": False,
        "topic_timer": None,
        "message_bus_last_id": 5,
        "participant_count": 3,
        "show_read_indicator": False,
        "thumbnails": [
            {"max_width": None, "max_height": None, "width": 600, "height": 400, "url": "https://forum.example/a.png"},
            {"max_width": 300, "max_height": 300, "width": 300, "height": 200, "url": "https://forum.example/b.png"},
        ],
        "slow_mode_enabled_until": None,
        "summarizable": True,
        "has_cached_summary": False,
        "valid_reactions": ["heart", "+1"],
        "has_accepted_answer": False,
    }
    if shared_issues:
        topic["shared_issue_count"] = 0  # a field some topics carry and others don't
    topic.update(
        {
            "can_create_shared_issue": False,
            "shared_issue_visible": False,
            "can_vote": False,
            "vote_count": 0,
            "user_voted": False,
            "discourse_zendesk_plugin_zendesk_id": None,
            "discourse_zendesk_plugin_zendesk_url": "https://support.example",
            "details": {
                "can_edit": False,
                "notification_level": 1,
                "participants": [participant, {**participant, **_discourse_user("example-reader"), "name": None}],
                "created_by": author,
                "last_poster": last,
                "links": [
                    _discourse_link("https://example.com/docs", "Docs", 3, author["id"]),
                    _discourse_link("https://example.com/other", None, 0, author["id"]),
                ],
            },
            "bookmarks": [],
        }
    )
    return topic
