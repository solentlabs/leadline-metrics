"""What ha_integration declares: its name, its file, its config, and what the summary shows. No requests."""

from __future__ import annotations

from typing import Any

from leadline import config

NAME = "ha_integration"
DEFAULT_FILE = config.default_file(NAME, "integration")  # ha-integration-{integration}.jsonl

KEYS = ("integration", "bands", "notes")  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def _problems(entry: dict[str, Any]) -> list[str]:
    integration = entry.get("integration")
    found = [] if isinstance(integration, str) and integration else ["integration: must be a non-empty string"]
    problem = config.bands_problem(entry.get("bands", config.BANDS[0]))
    return found + ([f"bands: {problem}"] if problem else []) + config.notes_problems(entry.get("notes", []))


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


CHART = "Custom integrations in the HA feed"  # the chart the feed's size and the integrations we outrank share
# The fields the default summary shows of each entry's file, and the chart trends draws. Home Assistant changes what
# its feed lists, so a rank alone moves with no change in installs: the chart draws the feed's size and the number of
# integrations we outrank (its size less our rank, the percentile as a count) on one axis, shaded between, which rise
# together on such a change. The outranked count is for the chart alone; the rank and percentile are in the summary
# and on the chart's hover.
SHOWN = (
    config.Shown("total", "Installs", into="versions"),
    config.Shown("versions", "Installs by version", "breakdown"),
    config.Shown(
        "tracked_integrations",
        "Custom integrations HA tracks",
        chart=CHART,
        color="s3",
        tip=("rank", "percentile"),
    ),
    config.Shown(
        "tracked_integrations",
        "Integrations we outrank",
        chart=CHART,
        color="s1",
        minus="rank",
        band="tracked_integrations",
    ),
    config.Shown("rank", "Rank in the HA feed (lower is better)", charted=False),
    config.Shown(
        "percentile", "Percentile rank (HA custom integrations)", format=".1f", complement=True, charted=False
    ),
)


LABEL = "HA installs"  # what trends heads its sections with


def summary(cfg: Any) -> list[config.Section]:
    return [
        config.Section(NAME, entry["integration"], file_name(entry), _shown(entry), LABEL, notes=config.notes(entry))
        for entry in cfg
    ]


def _shown(entry: dict[str, Any]) -> tuple[config.Shown, ...]:
    """The fields the summary shows of an entry's file; its versions banded as the entry's `bands` says."""
    bands = entry.get("bands", config.BANDS[0])
    return tuple(shown._replace(bands=bands) if shown.field == "versions" else shown for shown in SHOWN)


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, {"integration": entry["integration"]})
