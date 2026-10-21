"""What github_response_times declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

from typing import Any

from leadline import config
from leadline.sources.github.spec import file_fields, maintainer_problems, repo_problems

NAME = "github_response_times"
DEFAULT_FILE = config.default_file(NAME, "repo_name")  # github-response-times-{repo_name}.jsonl

KEYS = ("repo", "maintainer")  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


CHART = "Hours to an answer"  # the chart the three hours share
# The fields the default summary shows of each entry's file.
SHOWN = (
    config.Shown("median_hours", "Median hours to an answer", format=".1f", chart=CHART),
    config.Shown("never_answered", "Not answered", charted=False),
)
# What only trends draws: the mean, 90th percentile and fastest, as lines of the median's chart.
CHARTS = (
    config.Shown("mean_hours", "Mean hours to an answer", chart=CHART),
    config.Shown("p90_hours", "90th percentile hours to an answer", chart=CHART),
    config.Shown("fastest_hours", "Fastest hours to an answer", chart=CHART),
)
LABEL = "Response times"  # what trends heads its sections with


def summary(cfg: Any) -> list[config.Section]:
    return config.entry_summary(NAME, cfg, "repo", file_name, SHOWN, LABEL, CHARTS)


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, file_fields(entry["repo"]))


def _problems(entry: dict[str, Any]) -> list[str]:
    return repo_problems(entry) + maintainer_problems(entry)
