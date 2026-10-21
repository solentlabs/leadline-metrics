"""What github_releases declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

from typing import Any

from leadline import config
from leadline.sources.github.spec import file_fields, repo_problems

NAME = "github_releases"
DEFAULT_FILE = config.default_file(NAME, "repo_name")  # github-releases-{repo_name}.jsonl

KEYS = ("repo", "bands", "exclude")  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def _problems(entry: dict[str, Any]) -> list[str]:
    problem = config.bands_problem(entry.get("bands", config.BANDS[0]))
    exclude = entry.get("exclude", [])
    listed = isinstance(exclude, list) and all(isinstance(tag, str) and tag for tag in exclude)
    return (
        repo_problems(entry)
        + ([f"bands: {problem}"] if problem else [])
        + ([] if listed else ["exclude: must be a list of release tags (non-empty text)"])
    )


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


LABEL = "Releases"  # what trends heads its sections with
# What only trends draws, from every release's assets (drafts too, prereleases left out): each version's downloads so
# far, as a line of its own, none summed with another. A release that leaves the list keeps its count, so no line
# falls (docs/TRENDS.md). The entry's `bands` bands the versions. A second chart draws the releases published since
# the file began against days since release.
DOWNLOADS = "releases[].assets[].downloads"


def _charts(entry: dict[str, Any]) -> tuple[config.Shown, ...]:
    bands = entry.get("bands", config.BANDS[0])
    by_version = config.Shown(
        DOWNLOADS, "Downloads by version", "breakdown", by="tag", lines=True, prereleases=False, half=True, bands=bands
    )
    # Side by side with it (`half`): the same counts against the days since each release's `published_at`, a line an
    # exact release.
    since = by_version._replace(label="Downloads since release", since="published_at", bands="release")
    return (by_version, since)


def summary(cfg: Any) -> list[config.Section]:
    return [_section(entry) for entry in cfg]


def _section(entry: Any) -> config.Section:
    """The entry's section of the default summary: its latest release, and each release's downloads in 7 days; and
    the charts of trends."""
    file = file_name(entry)
    releases = {"from": file, "field": "releases", "as_of": ""}
    lines = [
        {**releases, "label": "Latest release", "aggregate": "latest_release"},
        {**releases, "label": "Downloads in 7d", "aggregate": "release_downloads", "compare": "7d"},
    ]
    exclude = entry.get("exclude")
    hide = (config.Hide("releases", "tag", frozenset(config.untagged(tag) for tag in exclude)),) if exclude else ()
    return config.Section(NAME, entry["repo"], file, (), LABEL, lines=lines, charts=_charts(entry), hide=hide)


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, file_fields(entry["repo"]))
