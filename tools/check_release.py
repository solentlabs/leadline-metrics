"""Check a release: its tag is `v` and the package version, CHANGELOG.md has that version's heading, and, with
`--on`, the tagged commit is in the history of a branch (main).

release.yml runs it before building, so a tag that disagrees, or sits on a commit that never reached main,
publishes nothing (docs/RELEASING.md, "Release"). The version is read from src/leadline/__init__.py as
hatchling reads it.

    python tools/check_release.py vX.Y.Z [--on origin/main]    # exit 0, or 1 naming each problem
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = "src/leadline/__init__.py"


def problems(tag: str, root: Path = ROOT) -> list[str]:
    """What stops `tag` being released from `root`; empty when nothing does."""
    found = []
    source = (root / VERSION_FILE).read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', source, re.MULTILINE)
    version = match.group(1) if match else None
    if tag != f"v{version}":
        found.append(f"tag {tag} does not match the package version {version} ({VERSION_FILE})")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = rf"^## {re.escape(str(version))} - \d{{4}}-\d{{2}}-\d{{2}}$"
    if not re.search(heading, changelog, re.MULTILINE):
        found.append(f"CHANGELOG.md has no heading `## {version} - YYYY-MM-DD`")
    return found


def not_on(branch: str, root: Path = ROOT) -> list[str]:
    """What stops the commit checked out in `root` being released: it is neither `branch` nor in its history.

    A tag can be pushed on any commit, and the release workflow publishes whatever it names; a commit that never
    reached main has not been reviewed as main's are.
    """
    # git from the PATH, with arguments the workflow gives: the branch, and nothing from the tag's contents.
    done = subprocess.run(  # noqa: S603
        ["git", "merge-base", "--is-ancestor", "HEAD", branch],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode == 0:
        return []
    if done.returncode == 1:
        return [f"the tagged commit is not on {branch}: merge it there first"]
    return [f"cannot compare the tagged commit with {branch}: {done.stderr.strip()}"]


def main(argv: list[str], root: Path = ROOT) -> int:
    found = problems(argv[0], root)
    if "--on" in argv:
        found += not_on(argv[argv.index("--on") + 1], root)
    for problem in found:
        sys.stderr.write(f"{problem}\n")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
