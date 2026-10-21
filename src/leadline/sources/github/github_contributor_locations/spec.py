"""What github_contributor_locations declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

from typing import Any

from leadline import config
from leadline.sources.github.spec import file_fields, repo_problems

NAME = "github_contributor_locations"
DEFAULT_FILE = config.default_file(NAME, "repo_name")  # github-contributor-locations-{repo_name}.jsonl
UNKNOWN = "unknown"
CONFIGURED = "configured"  # a person's source where the table's `known` gave their region
BODY_CHARS = 500  # the part of each issue body and comment searched, as the script reads a body

KEYS = ("repo", "regions", "comments")  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


# The fields the default summary shows of each entry's file.
SHOWN = (
    config.Shown("total_unique_creators", "Contributors", into="region_summary"),
    config.Shown("with_inferred_region", "With a region", into="region_summary"),
    config.Shown("region_summary", "Regions", "breakdown", neutral=(UNKNOWN,)),
)
LABEL = "Contributors"  # what trends heads its sections with


def summary(cfg: Any) -> list[config.Section]:
    return config.entry_summary(NAME, cfg, "repo", file_name, SHOWN, LABEL)


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, file_fields(entry["repo"]))


def _problems(entry: dict[str, Any]) -> list[str]:
    problems = repo_problems(entry)
    if not isinstance(entry.get("regions"), dict):  # config.load puts the named table here
        problems.append("regions: must name a region table in shared.regions")
    if "comments" in entry and not isinstance(entry["comments"], bool):
        problems.append("comments: must be true or false")
    return problems
