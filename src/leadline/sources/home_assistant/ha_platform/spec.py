"""What ha_platform declares: its name, its file, its config, and an empty summary. No requests."""

from __future__ import annotations

from typing import Any

from leadline import config

NAME = "ha_platform"
DEFAULT_FILE = config.default_file(NAME)  # ha-platform.jsonl


def validate(cfg: Any) -> list[str]:
    if not isinstance(cfg, dict):
        return [": must be an object"]
    if unknown := config.unknown_keys(cfg, ()):
        return [f".{problem}" for problem in unknown]
    try:
        file_name(cfg)
    except config.ConfigError as e:
        return [f".{e}"]
    return []


def files(cfg: Any) -> dict[str, str]:
    return {"": file_name(cfg)}


def summary(cfg: Any) -> list[config.Section]:  # noqa: ARG001 (the contract's signature)
    """None: it reports on Home Assistant as a whole, not the user's projects, so the summary and trends skip it."""
    return []


def file_name(cfg: Any) -> str:
    return config.file_name(cfg, DEFAULT_FILE, {})
