"""A kind of response the shape test checks (test_builder_shapes.py), and where the kinds are listed.

A source's or platform group's package lists its kinds: `KINDS` in its tests/shapes.py (a group's, those several
of its sources read). `package_kinds` finds those by walking, so a package added or removed changes no shared file.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Sequence

import scenarios
from scenarios.builders import common

Request = Dict[str, Any]


@dataclass
class Kind:
    name: str
    matches: Callable[[Request], bool]  # is a captured request one of this kind
    built: Sequence[common.Exchange]  # this kind's responses as the builders make them
    maps: frozenset[str] = frozenset()  # paths of objects whose keys are data, not field names
    unverified: frozenset[str] = field(default_factory=frozenset)  # paths the capture has no example of
    unverified_link: bool = False  # the capture has no Link header of this kind to hold a built one to


def package_kinds() -> list[Kind]:
    """The KINDS of every package's tests/shapes.py, in the order of the packages' folder names."""
    found: list[Kind] = []
    for _name, (module, _path) in sorted(scenarios.package_files("shapes").items()):
        found += importlib.import_module(module).KINDS
    return found
