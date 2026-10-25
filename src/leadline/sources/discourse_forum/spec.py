"""What discourse_forum declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

import re
from typing import Any

from leadline import config

NAME = "discourse_forum"
DEFAULT_FILE = config.default_file(NAME)  # discourse-forum.jsonl
_URL = re.compile(r"https?://[^/\s]+(/\S*)?")

KEYS = ("base_url",)  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def _problems(entry: dict[str, Any]) -> list[str]:
    base_url = entry.get("base_url")
    return [] if isinstance(base_url, str) and _URL.fullmatch(base_url) else ["base_url: must be an http(s) URL"]


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


# The fields the default summary shows of each forum.
SHOWN = (
    config.Shown("users_count", "Members"),
    config.Shown("topics_count", "Topics"),
    config.Shown("posts_count", "Posts"),
    config.Shown("active_users_7_days", "Active members, 7 days"),
)
LABEL = "Forum site"  # what trends heads its sections with


def summary(cfg: Any) -> list[config.Section]:
    """One section per forum, named by its address."""
    return config.entry_summary(NAME, cfg, "base_url", file_name, SHOWN, LABEL)


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, {})
