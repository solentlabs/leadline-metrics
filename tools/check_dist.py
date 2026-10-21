"""Check what a release uploads: the wheel holds the package and the sdist the package and its notices, or it stops.

release.yml runs it on the built files before it publishes anything (docs/RELEASING.md, "Release"). A package's
tests and README.md stay in the repo, and so does everything that is only the repo's own tooling (`.claude`, `.github`,
`.vscode`, `tests`, `docs`): a release is the package, not the project's workshop.

    python tools/check_dist.py dist    # exit 0, or 1 naming each problem
"""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

PLACES = ("countries", "zones", "states")  # the vendored data a run reads, which must be in the wheel
NOTICES = (
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "pyproject.toml",
    "PKG-INFO",
    ".gitignore",
)  # besides src/; hatchling always adds .gitignore


def _internal(parts: list[str]) -> str | None:
    """Why a path under the package has no place in a release: it is a test or a Markdown file."""
    if "tests" in parts[:-1]:
        return "tests"
    if parts[-1].endswith(".md"):
        return "Markdown"
    return None


def wheel_problems(path: Path) -> list[str]:
    names = [name for name in zipfile.ZipFile(path).namelist() if not name.endswith("/")]
    found = []
    for name in names:
        parts = name.split("/")
        if parts[0] == "leadline":
            why = _internal(parts)
            if why:
                found.append(f"the wheel holds {name}, which is {why} (they stay in the repo)")
        elif not parts[0].endswith(".dist-info"):
            found.append(f"the wheel holds {name}, which is outside the package")
    found += [
        f"the wheel lacks leadline/places/{place}.json"
        for place in PLACES
        if f"leadline/places/{place}.json" not in names
    ]
    return found


def sdist_problems(path: Path) -> list[str]:
    with tarfile.open(path) as archive:
        members = [m.name for m in archive.getmembers() if m.isfile()]
    found = []
    for name in members:
        relative = name.split("/", 1)[1] if "/" in name else name  # without the folder named for the version
        parts = relative.split("/")
        shipped = relative in NOTICES or (parts[:2] == ["src", "leadline"] and _internal(parts) is None)
        if not shipped:
            found.append(f"the sdist holds {relative}, which a release does not ship")
    return found


def problems(folder: Path) -> list[str]:
    """What stops the wheel and the sdist in `folder` being published; empty when nothing does."""
    wheels, archives = sorted(folder.glob("*.whl")), sorted(folder.glob("*.tar.gz"))
    found = [
        f"expected one {kind} in the release folder, found {len(files)}"
        for kind, files in ((".whl", wheels), (".tar.gz", archives))
        if len(files) != 1
    ]
    if found:
        return found
    return wheel_problems(wheels[0]) + sdist_problems(archives[0])


def main(argv: list[str]) -> int:
    found = problems(Path(argv[0]))
    for problem in found:
        sys.stderr.write(f"{problem}\n")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
