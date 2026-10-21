"""What github_discussions declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

from typing import Any

from leadline import config
from leadline.sources.github.spec import file_fields, maintainer_problems, repo_problems

NAME = "github_discussions"
DEFAULT_FILE = config.default_file(NAME, "repo_name")  # github-discussions-{repo_name}.jsonl

KEYS = ("repo", "maintainer")  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


# The fields the default summary shows of each entry's file.
# Comments, not answers: only Q&A discussions can have an accepted answer.
SHOWN = (config.Shown("total", "Discussion threads"), config.Shown("total_comments", "Comments on discussions"))
LABEL = "Discussions"  # what trends heads its sections with


def summary(cfg: Any) -> list[config.Section]:
    return config.entry_summary(NAME, cfg, "repo", file_name, SHOWN, LABEL)


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, file_fields(entry["repo"]))


def _problems(entry: dict[str, Any]) -> list[str]:
    return repo_problems(entry) + maintainer_problems(entry)
