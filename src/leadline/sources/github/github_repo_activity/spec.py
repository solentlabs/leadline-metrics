"""What github_repo_activity declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

import re
from typing import Any

from leadline import config
from leadline.sources.github.spec import file_fields, repo_problems

NAME = "github_repo_activity"
DEFAULT_FILE = config.default_file(NAME, "label")  # github-repo-activity-{label}.jsonl
# The script's patterns, matched ignoring case against each commit's first line.
FIX_PATTERN = r"\bfix\b|bug|crash|revert"
REDESIGN_PATTERN = r"redesign|refine|polish|refactor"
PATTERNS = {"fix_pattern": FIX_PATTERN, "redesign_pattern": REDESIGN_PATTERN}

KEYS = ("repo", "label", *PATTERNS)  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def _problems(entry: dict[str, Any]) -> list[str]:
    problems = repo_problems(entry)
    if "label" in entry and not (isinstance(entry["label"], str) and entry["label"]):
        problems.append("label: must be a non-empty string")
    return problems + [problem for key in PATTERNS if key in entry for problem in _pattern_problems(key, entry[key])]


def _pattern_problems(key: str, pattern: Any) -> list[str]:
    if not isinstance(pattern, str):
        return [f"{key}: must be a regex"]
    try:
        re.compile(pattern)
    except re.error as e:
        return [f"{key}: must be a regex: {e}"]
    return []


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


# The fields the default summary shows of each entry's file.
SHOWN = (
    config.Shown("velocity_7d.commits", "Commits, last 7 days", compare=("7d",)),
    config.Shown("stars", "Stars"),
    config.Shown("latest_release.tag", "Latest release", "value"),
    config.Shown("latest_release.date", "Released", "value"),
)
LABEL = "Repo activity"  # what trends heads its sections with


def summary(cfg: Any) -> list[config.Section]:
    return config.entry_summary(NAME, cfg, "repo", file_name, SHOWN, LABEL)


def label(entry: Any) -> str:
    label: str = entry.get("label", file_fields(entry["repo"])["repo_name"])
    return label


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, {**file_fields(entry["repo"]), "label": label(entry)})
