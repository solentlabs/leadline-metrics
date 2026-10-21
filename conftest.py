"""The oracle split: only a test marked `oracle` may read what the local settings name.

The local settings (tests/oracle/local_settings.py) name the real script, the capture, the
forbidden-terms list and the maintainer's leadline config, which stay out of the repo. pyproject.toml
deselects the tests that need them, so `make check` and CI run only what needs the repo; `make oracle`
selects them, and stops before running any when a setting is missing or names nothing. A test without
the marker that opens a file a setting names, or lists or opens anything under it, fails at that call.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Iterator

import local_settings
import pytest

NEEDS = {
    "script": "collect-metrics.sh",
    "capture": "the capture",
    "forbidden_terms": "the forbidden-terms list",
    "leadline_config": "the leadline config",
}
READS = frozenset({"open", "os.listdir", "os.scandir"})  # the audit events that read a file or a folder


class Guard:
    """An audit hook that refuses to read a held path, or anything under it, while a test without the marker runs."""

    active = False
    held: tuple[Path, ...] = tuple(path.resolve() for path in local_settings.read().values())

    def __call__(self, event: str, args: tuple[Any, ...]) -> None:
        if not (self.active and event in READS and args and isinstance(args[0], (str, bytes, os.PathLike))):
            return
        path = Path(os.fsdecode(args[0])).resolve()
        if any(path == held or held in path.parents for held in self.held):
            raise RuntimeError(f"{path} is read by a test not marked oracle")


GUARD = Guard()
sys.addaudithook(GUARD)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item) -> Iterator[None]:
    GUARD.active = item.get_closest_marker("oracle") is None
    yield
    GUARD.active = False


def missing() -> list[str]:
    """What the oracle tests need and don't have: each setting that isn't set, or names nothing."""
    problems = []
    for name, what in NEEDS.items():
        try:
            path = local_settings.path(name)
        except local_settings.MissingSettingError as unset:
            problems.append(str(unset))
            continue
        if not path.exists():
            problems.append(f"{what} is not at {path} (the setting {name!r})")
    return problems


def pytest_collection_finish(session: pytest.Session) -> None:
    if not any(item.get_closest_marker("oracle") for item in session.items):
        return
    problems = missing()
    if problems:
        pytest.exit("the oracle tests can't run:\n" + "\n".join(problems), returncode=pytest.ExitCode.USAGE_ERROR)
