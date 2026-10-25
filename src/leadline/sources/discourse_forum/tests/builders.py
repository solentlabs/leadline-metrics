"""Discourse forum builders."""

from __future__ import annotations

from scenarios.builders.common import Json


def discourse_about(
    *,
    users: int,
    topics: int,
    posts: int,
    active_7_days: int,
    chat: bool = True,
    stats: bool = True,
) -> Json:
    """A forum's `about.json` as Discourse documents it: the `about` object with its site `stats`, made-up counts.

    `chat` adds the chat counts a forum with chat gives; `stats=False` leaves `stats` out, as for a forum whose
    `can_see_about_stats` is false.
    """
    counts: Json = {
        "topics_last_day": topics // 5000,
        "topics_7_days": topics // 900,
        "topics_30_days": topics // 200,
        "topics_count": topics,
        "posts_last_day": posts // 6000,
        "posts_7_days": posts // 900,
        "posts_30_days": posts // 200,
        "posts_count": posts,
        "users_last_day": users // 9000,
        "users_7_days": users // 1500,
        "users_30_days": users // 300,
        "users_count": users,
        "active_users_last_day": active_7_days // 5,
        "active_users_7_days": active_7_days,
        "active_users_30_days": active_7_days * 3,
        "likes_last_day": posts // 9000,
        "likes_7_days": posts // 1200,
        "likes_30_days": posts // 250,
        "likes_count": posts // 3,
        "participating_users_last_day": active_7_days // 9,
        "participating_users_7_days": active_7_days // 2,
        "participating_users_30_days": active_7_days,
    }
    if chat:
        counts.update({"chat_messages_last_day": 40, "chat_messages_count": 9000, "chat_messages_7_days": 300})
    about: Json = {
        "stats": counts,
        "title": "Example Forum",
        "locale": "en",
        "version": "3.5.0",
        "can_see_about_stats": stats,
        "category_moderators": [],
        "moderators": [],
        "admins": [],
        "contact_url": None,
        "contact_email": "forum@example.com",
    }
    if not stats:
        del about["stats"]
    return {"users": [], "about": about}
