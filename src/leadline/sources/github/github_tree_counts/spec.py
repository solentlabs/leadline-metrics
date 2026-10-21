"""What github_tree_counts declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

import re
from typing import Any

from leadline import config
from leadline.sources.github.spec import file_fields, repo_problems

NAME = "github_tree_counts"
DEFAULT_FILE = config.default_file(NAME, "repo_name")  # github-tree-counts-{repo_name}.jsonl
DEFAULT_REF = "HEAD"
# The counts, in the order the script writes them, each under its key unless the entry's `keys` rename it.
COUNTS = ("items", "files", "groups", "items_per_group")
KEY_NAMES = ("files", "items", "groups", "items_per_group")  # as the config and its problems name them
# The keys a row has besides its counts, which a count can't take: every row's, a failure or partial row's, and a
# backfilled row's.
ROW_KEYS = ("date", "tree_truncated", "captured_at", "status", "reason", config.BACKFILLED)
PATTERN_KEYS = ("pattern", "content")  # what a listed pattern's object holds
CONTENT_KEYS = ("pattern", "key", "map")  # what an entry's `content` holds
READING_KEYS = ("pattern", "map")  # what a listed pattern's `content` object holds: its own way of reading
BLOBS_SUFFIX = "_blobs"  # added to the content's key for the row's key of the blobs it read

KEYS = ("repo", "pattern", "ref", "keys", "content")  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


LABEL = "Tree counts"  # what trends heads its sections with


def summary(cfg: Any) -> list[config.Section]:
    return [config.Section(NAME, entry["repo"], file_name(entry), _shown(entry), LABEL) for entry in cfg]


def _shown(entry: Any) -> list[config.Shown]:
    """The counts the default summary shows, under the entry's keys: items_per_group, an object, aside; and the
    counts by content, a breakdown, which add up to the files: the files are the top of its stack, not a chart
    of their own."""
    keys = entry.get("keys", {})
    by = entry["content"]["key"] if "content" in entry else ""
    shown = [
        config.Shown(keys.get(name, name), keys.get(name, name), into=by if name == "files" else "")
        for name in COUNTS
        if name != "items_per_group"
    ]
    if by:
        shown.append(config.Shown(by, by, "breakdown"))
    return shown


def _problems(entry: dict[str, Any]) -> list[str]:
    """What is wrong with an entry's keys, each as `key: problem`."""
    problems = repo_problems(entry)
    problems += _pattern_problems(entry.get("pattern"), "content" in entry)
    ref = entry.get("ref", DEFAULT_REF)
    if not isinstance(ref, str) or not ref:
        problems.append("ref: must be a non-empty string")
    problems += _key_problems(entry.get("keys", {}))
    if "content" in entry:
        problems += _content_problems(entry["content"], entry.get("keys", {}))
        if isinstance(entry["content"], dict) and "map" in entry["content"]:
            problems += _map_problems(entry["content"]["map"])
    return problems


def patterns(entry: Any) -> list[tuple[str, Any]]:
    """The entry's patterns in priority order, each with how `content` reads its files: True (the entry's way), False
    (not read) or an object of its own `pattern` and `map`. `pattern` is one regex, or a list whose items are a regex
    or an object `{"pattern": regex, "content": true, false or {"pattern": regex, "map": {...}}}`."""
    listed = entry["pattern"]
    return [
        (item, True) if isinstance(item, str) else (item["pattern"], item.get("content", True))
        for item in ([listed] if isinstance(listed, str) else listed)
    ]


def _pattern_problems(pattern: Any, content: bool) -> list[str]:
    """What is wrong with `pattern`: a non-empty string, or a non-empty list of them or of objects with a `pattern`
    and optionally `content`; `content` is whether the entry has its own."""
    if isinstance(pattern, list):
        if not pattern:
            return ["pattern: must not be an empty list"]
        return [
            problem for i, item in enumerate(pattern) for problem in _listed_problems(item, f"pattern[{i}]", content)
        ]
    return _regex_problems(pattern, "pattern")


def _listed_problems(item: Any, where: str, content: bool) -> list[str]:
    if isinstance(item, str):
        return _regex_problems(item, where)
    if not isinstance(item, dict):
        return [f"{where}: must be a regex or an object with a pattern"]
    problems = [f"{where}.{name}: not one of: {', '.join(PATTERN_KEYS)}" for name in item if name not in PATTERN_KEYS]
    problems += _regex_problems(item.get("pattern"), f"{where}.pattern")
    reading = item.get("content")
    if isinstance(reading, dict):
        problems += (
            _reading_problems(reading, f"{where}.content")
            if content
            else [f"{where}.content: needs the entry's content"]
        )
    elif "content" in item and not isinstance(reading, bool):
        problems.append(f"{where}.content: must be true, false or an object of a pattern and a map")
    return problems


def _reading_problems(reading: dict[str, Any], where: str) -> list[str]:
    """What is wrong with a listed pattern's `content` object: a value pattern and a map, each as the entry's."""
    problems = [
        f"{where}.{name}: not one of: {', '.join(READING_KEYS)}" for name in reading if name not in READING_KEYS
    ]
    if "pattern" in reading:
        problems += _value_pattern_problems(reading["pattern"], f"{where}.pattern")
    if "map" in reading:
        problems += _map_problems(reading["map"], f"{where}.map")
    return problems


def _regex_problems(pattern: Any, where: str) -> list[str]:
    if not isinstance(pattern, str) or not pattern:
        return [f"{where}: must be a non-empty string"]
    try:
        groups = re.compile(pattern).groups
    except re.error as e:
        return [f"{where}: not a valid regex: {e.msg}"]
    if groups < 2:
        return [f"{where}: must have two groups (the group, then the item), not {groups}"]
    return []


def _key_problems(keys: Any) -> list[str]:
    if not isinstance(keys, dict):
        return ["keys: must be an object"]
    problems = [f"keys.{name}: not one of: {', '.join(KEY_NAMES)}" for name in keys if name not in KEY_NAMES]
    taken: dict[str, str] = {}
    for name in KEY_NAMES:
        key = keys.get(name, name)
        if not isinstance(key, str) or not key:
            problems.append(f"keys.{name}: must be a non-empty string")
        elif key in ROW_KEYS:
            problems.append(f"keys.{name}: {key!r} is a key a row has")
        elif key in taken:
            problems.append(f"keys.{name}: {key!r} is already the key for {taken[key]}")
        else:
            taken[key] = name
    return problems


def _content_problems(content: Any, keys: Any) -> list[str]:
    """What is wrong with `content`: a pattern that compiles with one group, and a key (and its blobs key) no count or
    row key takes. `keys` is the entry's, whose problems are its own."""
    if not isinstance(content, dict):
        return ["content: must be an object with a pattern (one group) and a key"]
    problems = [
        f"content.{name}: not one of: {', '.join(CONTENT_KEYS)}" for name in content if name not in CONTENT_KEYS
    ]
    problems += _value_pattern_problems(content.get("pattern"), "content.pattern")
    key = content.get("key")
    if not isinstance(key, str) or not key:
        return [*problems, "content.key: must be a non-empty string"]
    chosen = keys if isinstance(keys, dict) else {}
    taken = {chosen[name] if isinstance(chosen.get(name), str) else name: name for name in KEY_NAMES}
    if key in ROW_KEYS:
        problems.append(f"content.key: {key!r} is a key a row has")
    elif key in taken:
        problems.append(f"content.key: {key!r} is already the key for {taken[key]}")
    blobs = key + BLOBS_SUFFIX
    if blobs in ROW_KEYS or blobs in taken:
        owner = f"for {taken[blobs]}" if blobs in taken else "every row has"
        problems.append(f"content.key: {blobs!r}, where the blobs read are kept, is already the key {owner}")
    return problems


def _value_pattern_problems(pattern: Any, where: str) -> list[str]:
    """What is wrong with a regex that reads a value: a non-empty string that compiles with one group."""
    if not isinstance(pattern, str) or not pattern:
        return [f"{where}: must be a non-empty string"]
    try:
        groups = re.compile(pattern).groups
    except re.error as e:
        return [f"{where}: not a valid regex: {e.msg}"]
    return [] if groups == 1 else [f"{where}: must have one group (the value), not {groups}"]


def _map_problems(mapped: Any, where: str = "content.map") -> list[str]:
    """What is wrong with a `map`: an object of non-empty names to non-empty names, none of them itself a name
    that is mapped, so a value is renamed once and maps never chain or cycle."""
    if not isinstance(mapped, dict):
        return [f"{where}: must be an object of values to values"]
    problems = [f"{where}: '' must not be empty"] if "" in mapped else []
    for name, final in mapped.items():
        if name and (not isinstance(final, str) or not final):
            problems.append(f"{where}.{name}: must be a non-empty string")
        elif name and final in mapped:
            problems.append(f"{where}.{name}: {final!r} is itself mapped; a value maps to a final name")
    return problems


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, file_fields(entry["repo"]))
