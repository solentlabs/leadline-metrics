"""Every source rejects a key in its entry that it doesn't read (docs/CONFIGURATION.md, "Settings")."""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

from leadline.sources import discover

REGISTRY = discover(specs_only=True).sources


def example(name: str) -> Any:
    """The first JSON block of the source's README, its config entry, as a config has it: the object or a list."""
    readme = Path(inspect.getfile(cast(Any, REGISTRY[name]))).parent / "README.md"
    block = re.search(r"^```json\n(.*?)^```", readme.read_text(encoding="utf-8"), re.M | re.S)
    assert block, f"{name}'s README has a config example"
    entry = json.loads(block.group(1))
    if "regions" in entry:  # config.load puts the named table here
        entry["regions"] = {}
    return entry


OBJECT_SOURCES = ("pypi", "ha_platform")  # configured as one object, not a list of entries


def configured(name: str, entry: Any) -> Any:
    return entry if name in OBJECT_SOURCES else [entry]


@pytest.mark.parametrize("name", list(REGISTRY))
def test_a_key_the_source_does_not_read_is_a_config_error(name: str) -> None:
    spec = REGISTRY[name]
    entry = example(name)
    assert spec.validate(configured(name, entry)) == []

    problems = spec.validate(configured(name, {**entry, "bogus": 1}))

    assert len(problems) == 1
    assert re.fullmatch(r"(\[0\])?\.?bogus: unknown key.*", problems[0]), problems


@pytest.mark.parametrize("name", list(REGISTRY))
def test_file_is_a_key_every_source_reads(name: str) -> None:
    entry = {**example(name), "file": "elsewhere.jsonl"}

    assert REGISTRY[name].validate(configured(name, entry)) == []
