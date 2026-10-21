"""What github_traffic declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

from typing import Any

from leadline import config
from leadline.sources.github.spec import file_fields, repo_problems

NAME = "github_traffic"
DEFAULT_FILE = config.default_file(NAME, "repo_name")  # github-traffic-{repo_name}.jsonl

KEYS = ("repo",)  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, repo_problems, file_name)


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


# The fields the default summary shows of each entry's file.
SHOWN = (
    config.Shown("views_total", "Views, 14 days", color="s1"),
    config.Shown("views_unique", "Unique visitors, 14 days", color="s4"),
    config.Shown("clones_total", "Clones, 14 days", color="s2"),
)
LABEL = "Traffic"  # what trends heads its sections with
# The card of trends it is a row of, with github_traffic_daily: both name it, neither knows the other.
CARD = config.Card(
    "Traffic",
    "Rolling 14 days",
    "Each point is the total of the 14 days ending that day, GitHub's own window. "
    "It is the only place unique visitors appear.",
)


def summary(cfg: Any) -> list[config.Section]:
    return config.entry_summary(NAME, cfg, "repo", file_name, SHOWN, LABEL, card=CARD)


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, file_fields(entry["repo"]))
