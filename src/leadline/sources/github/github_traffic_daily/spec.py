"""What github_traffic_daily declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

from typing import Any

from leadline import config
from leadline.sources.github.spec import file_fields, repo_problems

NAME = "github_traffic_daily"
DEFAULT_FILE = config.default_file(NAME, "repo_name")  # github-traffic-daily-{repo_name}.jsonl

KEYS = ("repo",)  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, repo_problems, file_name)


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


# The fields the default summary shows of each entry's file.
SHOWN = (
    config.Shown("views", "Views", "daily", trend=True, color="s1"),
    config.Shown("clones", "Clones", "daily", trend=True, color="s2"),
)
LABEL = "Daily traffic"  # what trends heads its sections with
# The card of trends it is a row of, with github_traffic: both name it, neither knows the other.
CARD = config.Card(
    "Traffic",
    "By day",
    "Each bar is one day. GitHub keeps only the last 14 days, so we save each day as it comes.",
)


def summary(cfg: Any) -> list[config.Section]:
    return config.entry_summary(NAME, cfg, "repo", file_name, SHOWN, LABEL, card=CARD)


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, file_fields(entry["repo"]))
