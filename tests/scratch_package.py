"""A scratch copy of the package layout, to prove the test plumbing on packages without moving a real source.

`make` writes the files under a temporary folder as a package of its own (a made-up name, so it never meets
`leadline`), with an `__init__.py` in every folder, puts the folder on the import path, and points the scenario
framework at it (`scenarios.PACKAGE`) until the test ends.
"""

# cspell:ignore syspath
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import scenarios


def make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> Path:
    """Write `files` (paths relative to the package) as a new package; returns the package's folder."""
    package = tmp_path / f"scratch_{uuid.uuid4().hex[:8]}"
    for name, text in {"__init__.py": "", **files}.items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for folder in {p.parent for p in package.rglob("*.py")} | {package / "sources"}:
        folder.mkdir(exist_ok=True)
        (folder / "__init__.py").touch()
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(scenarios, "PACKAGE", package)
    return package
