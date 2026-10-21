"""What cloudflare_web declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

from typing import Any

from leadline import config

NAME = "cloudflare_web"
DEFAULT_FILE = config.default_file(NAME, "label")  # cloudflare-web-{label}.jsonl

KEYS = ("label", "site_tag")  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def _problems(entry: dict[str, Any]) -> list[str]:
    bad = [key for key in ("label", "site_tag") if not isinstance(entry.get(key), str) or not entry[key]]
    return [f"{key}: must be a non-empty string" for key in bad]


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


# The fields the default summary shows of each entry's file.
SHOWN = (config.Shown("pageviews", "Page views", "daily"), config.Shown("visits", "Visits", "daily"))
LABEL = "Web analytics"  # what trends heads its sections with


def summary(cfg: Any) -> list[config.Section]:
    return config.entry_summary(NAME, cfg, "label", file_name, SHOWN, LABEL)


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, {"label": entry["label"]})
