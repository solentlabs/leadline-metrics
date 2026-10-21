"""What discourse_topics declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

import re
from typing import Any

from leadline import config

NAME = "discourse_topics"
DEFAULT_FILE = config.default_file(NAME)  # discourse-topics.jsonl
_URL = re.compile(r"https?://[^/\s]+(/\S*)?")

KEYS = ("base_url", "topics")  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def _problems(entry: dict[str, Any]) -> list[str]:
    problems = []
    base_url = entry.get("base_url")
    if not (isinstance(base_url, str) and _URL.fullmatch(base_url)):
        problems.append("base_url: must be an http(s) URL")
    topics = entry.get("topics")
    if not isinstance(topics, list) or not topics:
        return [*problems, "topics: must be a non-empty list of topics, each an id and a label"]
    seen: dict[int, int] = {}  # each id, and the first topic that has it
    for i, topic in enumerate(topics):
        if not isinstance(topic, dict):
            problems.append(f"topics[{i}]: must be an object")
            continue
        problems += config.unknown_nested(f"topics[{i}]", topic, ("id", "label"))
        topic_id: Any = topic.get("id")
        label = topic.get("label")
        if not (config.is_count(topic_id) and topic_id > 0):
            problems.append(f"topics[{i}].id: must be a positive integer")
        elif topic_id in seen:
            problems.append(f"topics[{i}].id: {topic_id} is topics[{seen[topic_id]}]'s too")
        else:
            seen[topic_id] = i
        if not (isinstance(label, str) and label):
            problems.append(f"topics[{i}].label: must be a non-empty string")
    return problems


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


# The fields the default summary shows of each topic.
SHOWN = (config.Shown("views", "Views"), config.Shown("replies", "Replies"))
LABEL = "Forum"  # what trends heads its sections with


def summary(cfg: Any) -> list[config.Section]:
    """One section per topic, from its rows in its forum's file."""
    return [
        config.Section(NAME, topic["label"], file_name(entry), SHOWN, LABEL, {"topic_id": topic["id"]})
        for entry in cfg
        for topic in entry["topics"]
    ]


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, {})
