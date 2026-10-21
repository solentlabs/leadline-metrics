"""What github_repo_stats declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

from typing import Any

from leadline import config
from leadline.sources.github.spec import STARS_AND_FORKS, file_fields, repo_problems

NAME = "github_repo_stats"
DEFAULT_FILE = config.default_file(NAME, "repo_name")  # github-repo-stats-{repo_name}.jsonl

KEYS = ("repo",)  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, repo_problems, file_name)


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


# The fields the default summary shows of each entry's file.
SHOWN = (
    config.Shown("stars", "Stars", color="s3"),
    config.Shown("forks", "Forks", color="s6"),
    config.Shown("views_14d", "Views, 14 days", charted=False),  # the Traffic card's other rows chart the views
)
LABEL = "Repo stats"  # what trends heads its sections with


def summary(cfg: Any) -> list[config.Section]:
    return config.entry_summary(NAME, cfg, "repo", file_name, SHOWN, LABEL, card=STARS_AND_FORKS)


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, file_fields(entry["repo"]))
