"""What goatcounter declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

import re
from typing import Any

from leadline import config

NAME = "goatcounter"
DEFAULT_FILE = config.default_file(NAME, "site")  # goatcounter-{site}.jsonl
_SITE = re.compile(r"[A-Za-z0-9-]+")  # a site code: the host name's first label

KEYS = ("site",)  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def _problems(entry: dict[str, Any]) -> list[str]:
    site = entry.get("site")
    if isinstance(site, str) and _SITE.fullmatch(site):
        return []
    return ["site: must be a GoatCounter site code (letters, digits and dashes)"]


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


# The fields the default summary shows of each entry's file.
SHOWN = (config.Shown("pageviews", "Page views", "daily"),)
LABEL = "Site visits"  # what trends heads its sections with


def summary(cfg: Any) -> list[config.Section]:
    return config.entry_summary(NAME, cfg, "site", file_name, SHOWN, LABEL)


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, {"site": entry["site"]})
