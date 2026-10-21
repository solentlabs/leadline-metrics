"""The kinds of response the oracle's shape test checks, as each package lists them (tests/oracle/shape_kind.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
import shape_kind
from scratch_package import make

KIND = "from shape_kind import Kind\nKINDS = [Kind('{name}', lambda r: r['url'] == 'https://example.test', [])]\n"


def test_the_kinds_are_those_each_packages_shapes_module_lists_found_by_walking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make(
        tmp_path,
        monkeypatch,
        {
            "sources/b/tests/shapes.py": KIND.format(name="b one") + "KINDS.append(KINDS[0])\n",
            "sources/a/tests/shapes.py": KIND.format(name="a one"),
            "sources/c/tests/test_c.py": "",
        },
    )

    assert [kind.name for kind in shape_kind.package_kinds()] == ["a one", "b one", "b one"]


def test_no_package_with_shapes_gives_no_kinds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make(tmp_path, monkeypatch, {"sources/c/tests/test_c.py": ""})

    assert shape_kind.package_kinds() == []


def test_a_shapes_module_without_kinds_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make(tmp_path, monkeypatch, {"sources/c/tests/shapes.py": "OTHER = []\n"})

    with pytest.raises(AttributeError, match="KINDS"):
        shape_kind.package_kinds()
