"""leadline.pyz, the package as one stdlib zipapp, run as a host with no pip runs it: `python3 leadline.pyz ...`.

Each test runs the archive with the interpreter running the tests, so CI proves it on Python 3.8 to
3.14 (docs/RELEASING.md).
"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import build_pyz
import pytest

import leadline

SRC = Path(leadline.__file__).parent


@pytest.fixture(scope="module")
def pyz(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_pyz.build(tmp_path_factory.mktemp("dist") / "leadline.pyz")


def run(pyz: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """`python -I -S leadline.pyz ARGS`: no environment, no site-packages, so only the archive and the stdlib."""
    return subprocess.run(
        [sys.executable, "-I", "-S", str(pyz), *args], cwd=cwd, capture_output=True, text=True, check=False
    )


def test_the_archive_holds_every_module_and_its_entry_point_and_nothing_else(pyz: Path) -> None:
    modules = {f"leadline/{p.relative_to(SRC).as_posix()}" for p in SRC.rglob("*.py") if "tests" not in p.parts}
    files = {name for name in zipfile.ZipFile(pyz).namelist() if not name.endswith("/")}  # folders aside

    assert files == modules | {f"leadline/places/{n}.json" for n in ("countries", "zones", "states")} | {"__main__.py"}


def test_the_archive_holds_no_tests_scenarios_goldens_or_markdown(pyz: Path) -> None:
    names = zipfile.ZipFile(pyz).namelist()

    assert [n for n in names if "/tests/" in n or "scenarios" in n or "golden" in n or n.endswith(".md")] == []


def test_the_build_drops_a_packages_tests_folder_and_readme_and_caches(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    for name in (
        "__init__.py",
        "sources/x/__init__.py",
        "sources/x/spec.py",
        "sources/x/README.md",
        "sources/x/tests/__init__.py",
        "sources/x/tests/test_x.py",
        "sources/x/tests/scenarios.py",
        "sources/x/tests/golden.json",
        "sources/x/tests/shapes.py",
        "sources/x/__pycache__/spec.cpython-38.pyc",
        "sources/x/spec.pyc",
        "sources/g/__init__.py",
        "sources/g/api.py",
        "sources/g/README.md",
        "sources/g/tests/builders.py",
        "sources/g/x/__init__.py",
        "sources/g/x/tests/test_x.py",
        "docs.md",
    ):
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_text("", encoding="utf-8")

    built = build_pyz.build(tmp_path / "out" / "scratch.pyz", package)

    files = {name for name in zipfile.ZipFile(built).namelist() if not name.endswith("/")}
    assert files == {
        "leadline/__init__.py",
        "leadline/sources/x/__init__.py",
        "leadline/sources/x/spec.py",
        "leadline/sources/g/__init__.py",
        "leadline/sources/g/api.py",
        "leadline/sources/g/x/__init__.py",
        "__main__.py",
    }


def test_the_wheel_leaves_out_tests_and_markdown() -> None:
    config = (SRC.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    wheel = config.split("[tool.hatch.build.targets.wheel]")[1].split("\n[")[0]

    assert 'exclude = ["**/tests", "**/*.md"]' in wheel


def test_version_comes_from_the_archive(pyz: Path, tmp_path: Path) -> None:
    done = run(pyz, "--version", cwd=tmp_path)

    assert (done.returncode, done.stdout) == (0, f"leadline {leadline.__version__}\n")


def test_a_run_with_no_sources(pyz: Path, tmp_path: Path) -> None:
    (tmp_path / "leadline.json").write_text(json.dumps({"out_dir": "metrics"}), encoding="utf-8")

    done = run(pyz, "run", "--config", "leadline.json", cwd=tmp_path)

    assert done.returncode == 0, done.stderr
    assert "=== leadline run ===" in done.stdout
    assert (tmp_path / "metrics" / "leadline.log").is_file()


def test_the_exit_status_is_the_commands(pyz: Path, tmp_path: Path) -> None:
    (tmp_path / "leadline.json").write_text(json.dumps({"out_dir": "m", "sources": {"nope": {}}}), encoding="utf-8")

    done = run(pyz, "run", "--config", "leadline.json", cwd=tmp_path)

    assert (done.returncode, done.stderr) == (2, "leadline: error: unknown sources: nope\n")


def test_the_archive_discovers_every_source(pyz: Path, tmp_path: Path) -> None:
    code = (
        "import sys, json; sys.path.insert(0, sys.argv[1]);"
        "from leadline.sources import discover; r = discover();"
        "print(json.dumps([list(r.sources), r.failed]))"
    )

    done = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, str(pyz)], cwd=tmp_path, capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stderr
    found, failed = json.loads(done.stdout)
    sources = [p.parent.name for p in (SRC / "sources").glob("*/*/source.py")] + [
        p.parent.name for p in (SRC / "sources").glob("*/source.py")
    ]
    assert (sorted(found), failed) == (sorted(sources), {})
    assert len(found) == len(sources)


def test_the_archive_carries_the_vendored_places_so_the_nas_needs_nothing_fetched(pyz: Path, tmp_path: Path) -> None:
    names = zipfile.ZipFile(pyz).namelist()
    assert all(f"leadline/places/{n}.json" in names for n in ("countries", "zones", "states"))

    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); from leadline import places;"
        " print(places.country('UK'), places.ZONES['Europe/London'], places.STATES['TX'],"
        " [r['region'] for r in places.zone_rules() if 'europe/kyiv' in r['keywords']])"
    )
    found = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, str(pyz)], cwd=tmp_path, capture_output=True, text=True, check=False
    )

    assert (found.returncode, found.stdout.strip()) == (0, "Britain (UK) GB Texas ['UA']"), found.stderr
