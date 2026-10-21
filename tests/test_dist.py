"""tools/check_dist.py: what a release uploads holds the package and little else, or the release stops.

release.yml builds the wheel and the sdist and runs the check before it uploads anything (docs/RELEASING.md,
"Release"). These tests build small archives laid out as hatchling lays them out.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import check_dist
import pytest

VERSION = "0.2.0"
WHEEL_FILES = {
    "leadline/__init__.py": "",
    "leadline/places/countries.json": "{}",
    "leadline/places/zones.json": "{}",
    "leadline/places/states.json": "{}",
    "leadline/sources/pypi/spec.py": "",
    f"leadline_metrics-{VERSION}.dist-info/METADATA": "",
    f"leadline_metrics-{VERSION}.dist-info/licenses/LICENSE": "",
}
SDIST_FILES = {
    "PKG-INFO": "",
    "README.md": "# readme",
    "LICENSE": "MIT",
    "CHANGELOG.md": "# Changelog",
    "pyproject.toml": "",
    ".gitignore": "",  # hatchling puts it in every sdist
    "src/leadline/__init__.py": "",
    "src/leadline/places/countries.json": "{}",
    "src/leadline/sources/pypi/spec.py": "",
}


def dist(root: Path, wheel: dict[str, str] | None = None, sdist: dict[str, str] | None = None) -> Path:
    """A folder holding one wheel and one sdist, with these files (the defaults are a good release)."""
    root.mkdir(exist_ok=True)
    with zipfile.ZipFile(root / f"leadline_metrics-{VERSION}-py3-none-any.whl", "w") as z:
        for name, text in (WHEEL_FILES if wheel is None else wheel).items():
            z.writestr(name, text)
    with tarfile.open(root / f"leadline_metrics-{VERSION}.tar.gz", "w:gz") as t:
        for name, text in (SDIST_FILES if sdist is None else sdist).items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"leadline_metrics-{VERSION}/{name}")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return root


def test_a_good_release_has_no_problems(tmp_path: Path) -> None:
    assert check_dist.problems(dist(tmp_path)) == []


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("leadline/sources/pypi/tests/test_pypi.py", "tests"),
        ("leadline/sources/pypi/README.md", "Markdown"),
        ("tests/conftest.py", "outside the package"),
        (".claude/settings.json", "outside the package"),
    ],
)
def test_a_wheel_holding_tests_markdown_or_anything_outside_the_package_is_a_problem(
    tmp_path: Path, name: str, kind: str
) -> None:
    root = dist(tmp_path, wheel={**WHEEL_FILES, name: ""})

    [problem] = check_dist.problems(root)

    assert problem.startswith(f"the wheel holds {name}")
    assert kind in problem


def test_a_wheel_without_the_vendored_places_is_a_problem(tmp_path: Path) -> None:
    files = {k: v for k, v in WHEEL_FILES.items() if "zones" not in k}

    assert check_dist.problems(dist(tmp_path, wheel=files)) == ["the wheel lacks leadline/places/zones.json"]


@pytest.mark.parametrize(
    "name",
    [
        ".claude/settings.json",
        "CLAUDE.md",
        ".github/workflows/release.yml",
        ".vscode/tasks.json",
        "tests/test_pyz.py",
        "docs/ARCHITECTURE.md",
        "src/leadline/sources/pypi/tests/golden.json",
        "src/leadline/sources/pypi/README.md",
    ],
)
def test_an_sdist_holding_more_than_the_package_and_its_notices_is_a_problem(tmp_path: Path, name: str) -> None:
    root = dist(tmp_path, sdist={**SDIST_FILES, name: ""})

    assert check_dist.problems(root) == [f"the sdist holds {name}, which a release does not ship"]


def test_a_release_needs_one_wheel_and_one_sdist(tmp_path: Path) -> None:
    root = dist(tmp_path)
    (root / f"leadline_metrics-{VERSION}.tar.gz").unlink()

    assert check_dist.problems(root) == ["expected one .tar.gz in the release folder, found 0"]


def test_the_command_exits_1_naming_each_problem_or_0(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = dist(tmp_path / "good")
    bad = dist(tmp_path / "bad", sdist={**SDIST_FILES, ".claude/settings.json": ""})

    assert check_dist.main([str(good)]) == 0
    assert check_dist.main([str(bad)]) == 1
    assert "the sdist holds .claude/settings.json" in capsys.readouterr().err
