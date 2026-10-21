"""What pypi declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

import re
from typing import Any

from leadline import config

NAME = "pypi"
DEFAULT_FILE = config.default_file(NAME, "package")  # pypi-{package}.jsonl

# A package name as PyPI allows it (PEP 508): letters and digits, with `.`, `_` or `-` inside.
_PACKAGE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")

KEYS = ("packages", "sleep_s", "retry_wait_s")  # the keys the config may have, besides `file`


def validate(cfg: Any) -> list[str]:
    if not isinstance(cfg, dict):
        return [": must be an object"]
    problems = [f".{problem}" for problem in config.unknown_keys(cfg, KEYS)]
    packages = cfg.get("packages")
    if not isinstance(packages, list) or not packages:
        problems.append(".packages: must be a non-empty list of package names")
    else:
        problems += [
            f".packages[{i}]: must be a package name"
            for i, package in enumerate(packages)
            if not (isinstance(package, str) and _PACKAGE.fullmatch(package))
        ]
    for key in ("sleep_s", "retry_wait_s"):
        value = cfg.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            problems.append(f".{key}: must be a number of seconds, 0 or more")
    if not problems:
        try:
            files(cfg)
        except config.ConfigError as e:
            problems.append(f".{e}")
    return problems


def files(cfg: Any) -> dict[str, str]:
    return {f".packages[{i}]": file_name(cfg, package) for i, package in enumerate(cfg["packages"])}


# The fields the default summary shows of each package: each window's downloads against the window before.
SHOWN = (
    config.Shown("downloads_last_week", "Downloads, last week", compare=("7d",)),
    config.Shown("downloads_last_month", "Downloads, last month", compare=("30d",)),
    config.Shown("latest_version", "Version", "value"),
)
LABEL = "PyPI downloads"  # what trends heads its sections with


def summary(cfg: Any) -> list[config.Section]:
    """One section per package."""
    return [config.Section(NAME, package, file_name(cfg, package), SHOWN, LABEL) for package in cfg["packages"]]


def file_name(cfg: Any, package: str) -> str:
    return config.file_name(cfg, DEFAULT_FILE, {"package": package})
