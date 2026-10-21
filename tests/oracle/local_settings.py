"""Where the oracle tests find what stays out of the repo: one local settings file, never committed.

`.leadline-local.json` at the repo root is gitignored; `.leadline-local.example.json` shows its form:

    {"script": "/path/to/collect-metrics.sh", "capture": "/path/to/capture/2026-09-23",
     "forbidden_terms": "/path/to/forbidden-terms.txt", "leadline_config": "/path/to/leadline.json"}

`script` is the real collect-metrics.sh, `capture` a folder of one night's captured responses
(saved responses in the case-folder format, docs/TESTING.md), `forbidden_terms` the private list of words
the repo must never hold (tests/oracle/forbidden_terms.py), and `leadline_config` the maintainer's private
leadline config, whose `reports` tests/oracle/test_reports_oracle.py holds to the script's reports.
LEADLINE_LOCAL_SETTINGS names another settings file, as the tests of the oracle split do.
A setting that isn't set fails at its first use, naming it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SETTINGS = ("script", "capture", "forbidden_terms", "leadline_config")


class MissingSettingError(LookupError):
    pass


def settings_file() -> Path:
    return Path(os.environ.get("LEADLINE_LOCAL_SETTINGS") or REPO / ".leadline-local.json")


def read(file: Path | None = None) -> dict[str, Path]:
    """Every setting the file sets (none when there is no file), each as a path."""
    file = file or settings_file()
    if not file.exists():
        return {}
    loaded = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{file}: expected a JSON object")
    found = {}
    for name, value in loaded.items():
        if name not in SETTINGS:
            raise ValueError(f"{file}: unknown setting {name!r}; the settings are {', '.join(SETTINGS)}")
        if not isinstance(value, str):
            raise ValueError(f"{file}: {name} is not a path")
        if value:
            found[name] = Path(value).expanduser()
    return found


def path(name: str, file: Path | None = None) -> Path:
    """The setting's path; MissingSettingError, naming it and where to set it, when it isn't set."""
    file = file or settings_file()
    found = read(file).get(name)
    if found is None:
        raise MissingSettingError(
            f"the setting {name!r} is not set: add it to {file} (an example: .leadline-local.example.json)"
        )
    return found
