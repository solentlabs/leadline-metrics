"""What github_issues declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

import re
from typing import Any, Iterable

from leadline import config
from leadline.sources.github.spec import STARS_AND_FORKS, file_fields, repo_problems

NAME = "github_issues"
DEFAULT_FILE = config.default_file(NAME, "repo_name")  # github-issues-{repo_name}.jsonl
OTHER_KIND = "other"  # the kind of an issue that carries none of the kinds' labels
UNKNOWN_REGION = "unknown"  # the region of an issue or a person the region table can't place
REGION_FIELDS = {
    "stars": "stars_by_region",
    "forks": "forks_by_region",
}  # each count and the `repo` field of its regions
KIND_KEYS = ("name", "label")  # what each of an entry's `kinds` holds
QUEUE_KEY, ITEMS_KEY = "label_queue", "issues"  # the queue's keys unless its config names them
# The keys a queue's own keys must not take: the row's, the queue's and a listed issue's.
ROW_KEYS = ("date", "repo", "issues", "contribution", "age", "engagement", "labels", "triage", "captured_at")
QUEUE_KEYS = (
    "total",
    "avg_age_days",
    "with_comments",
    "regions",
    "requested_total",
    "resolved_total",
    "median_days_to_resolve",
    "requesters",
)
ITEM_KEYS = ("number", "title", "age_days", "comments", "labels", "region")

KEYS = (
    "repo",
    "maintainer",
    "queue",
    "regions",
    "kinds",
    "stars_and_forks",
)  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


# The fields the default summary shows of each entry's file.
SHOWN = (
    config.Shown("issues.open", "Open issues"),
    config.Shown("issues.open_prs", "Open pull requests", charted=False),
    config.Shown("issues.opened_total", "Issues opened", "change"),
)
LABEL = "Issues"  # what trends heads its sections with


# The fields of the second section of an entry with `stars_and_forks`: the repo's counts in each row. With a `regions`
# table the rows also hold the counts by region, which the section does not chart.
STARS_AND_FORKS_SHOWN = (
    config.Shown("repo.stars", "Stars", color="s3"),
    config.Shown("repo.forks", "Forks", color="s6"),
)
STARS_PART = "stars"  # what its title adds to the entry's


def summary(cfg: Any) -> list[config.Section]:
    sections = []
    for entry in cfg:
        sections.append(config.Section(NAME, entry["repo"], file_name(entry), _shown(entry), LABEL))
        if entry.get("stars_and_forks"):
            stars = config.Section(
                NAME,
                entry["repo"],
                file_name(entry),
                STARS_AND_FORKS_SHOWN,
                LABEL,
                card=STARS_AND_FORKS,
                part=STARS_PART,
            )
            sections.append(stars)
    return sections


def by_region(entry: dict[str, Any]) -> bool:
    """Whether the entry counts its stars and forks by region: it has `stars_and_forks` and a `regions` table (which
    config.load puts in place of the name)."""
    return bool(entry.get("stars_and_forks")) and isinstance(entry.get("regions"), dict)


def _shown(entry: dict[str, Any]) -> tuple[config.Shown, ...]:
    """The fields the summary shows of an entry's file: the issues opened by kind and the open by kind, breakdowns,
    where it has kinds, and then the total opened is the top of its stack and the open issues the top of theirs, not
    charts of their own."""
    if "kinds" not in entry:
        return SHOWN
    opened = SHOWN[2]._replace(into="issues.opened_by_kind")
    open_now = SHOWN[0]._replace(into="issues.open_by_kind")
    return (
        open_now,
        SHOWN[1],
        opened,
        config.Shown("issues.opened_by_kind", "Opened by kind", "breakdown", half=True),
        config.Shown("issues.open_by_kind", "Open by kind", "breakdown", half=True),
    )


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, file_fields(entry["repo"]))


def _problems(entry: dict[str, Any]) -> list[str]:
    problems = repo_problems(entry)
    if not isinstance(entry.get("maintainer", ""), str):
        problems.append("maintainer: must be a login, or empty for none")
    if not isinstance(entry.get("stars_and_forks", False), bool):
        problems.append("stars_and_forks: must be true or false")
    if "queue" in entry:
        problems += _queue_problems(entry["queue"])
    elif "regions" in entry and not entry.get("stars_and_forks"):
        problems.append("regions: only a queue or stars_and_forks uses a region table; add one or remove it")
    if "kinds" in entry:
        problems += _kinds_problems(entry["kinds"])
    return problems


def _kinds_problems(kinds: Any) -> list[str]:
    """The problems with `kinds`: a non-empty list of objects, each a name and a label, no name or label twice, and no
    name `other`, which is the kind of an issue with none of the labels."""
    if not (isinstance(kinds, list) and kinds):
        return ["kinds: must be a non-empty list of kinds, each a name and a label"]
    problems: list[str] = []
    names: dict[str, int] = {}
    labels: dict[str, int] = {}
    for i, kind in enumerate(kinds):
        where = f"kinds[{i}]"
        if not isinstance(kind, dict):
            problems.append(f"{where}: must be an object with a name and a label")
            continue
        problems += [f"{where}.{key}: not one of: {', '.join(KIND_KEYS)}" for key in kind if key not in KIND_KEYS]
        for key, seen in (("name", names), ("label", labels)):
            value = kind.get(key)
            if not (isinstance(value, str) and value):
                problems.append(f"{where}.{key}: must be a non-empty string")
            elif key == "name" and value == OTHER_KIND:
                problems.append(f"{where}.name: {value!r} is the kind of an issue with none of the labels")
            elif value in seen:
                then = f"kinds[{seen[value]}]"
                tail = f", so no issue could be of kind {kind.get('name')!r}" if key == "label" else ""
                problems.append(f"{where}.{key}: {value!r} is already the {key} of {then}{tail}")
            else:
                seen[value] = i
    return problems


def _name_problems(where: str, settings: dict[str, Any], key: str, taken: Iterable[str], whose: str) -> list[str]:
    """The problem with a key the config names, unless it is a non-empty string no other key takes."""
    name = settings.get(key)
    if not (isinstance(name, str) and name):
        return [f"{where}{key}: must be a non-empty string"]
    return [f"{where}{key}: must not be one of {whose} own keys"] if name in taken else []


def _queue_problems(queue: Any) -> list[str]:
    if not isinstance(queue, dict):
        return ["queue: must be an object with a label"]
    problems = config.unknown_nested("queue", queue, ("label", "key", "items_key", "body_match"))
    problems += _name_problems("queue.", queue, "label", (), "")
    settings = {"key": QUEUE_KEY, "items_key": ITEMS_KEY, **queue}
    problems += _name_problems("queue.", settings, "key", ROW_KEYS, "the row's")
    problems += _name_problems("queue.", settings, "items_key", QUEUE_KEYS, "the queue's")
    if "body_match" not in queue:
        return problems
    match = queue["body_match"]
    if not isinstance(match, dict):
        return [*problems, "queue.body_match: must be an object"]
    problems += config.unknown_nested("queue.body_match", match, ("pattern", "flag_key", "count_key"))
    problems += _pattern_problems(match.get("pattern"))
    items_key = settings["items_key"]
    problems += _name_problems("queue.body_match.", match, "flag_key", ITEM_KEYS, "an item's")
    return problems + _name_problems("queue.body_match.", match, "count_key", (*QUEUE_KEYS, items_key), "the queue's")


def _pattern_problems(pattern: Any) -> list[str]:
    if not isinstance(pattern, str):
        return ["queue.body_match.pattern: must be a regex"]
    try:
        re.compile(pattern)
    except re.error as e:
        return [f"queue.body_match.pattern: must be a regex: {e}"]
    return []
