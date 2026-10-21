"""Build leadline.pyz: the package as one stdlib zipapp, which `python3 leadline.pyz` runs.

A host with Python 3.8 and no pip gets leadline as this one file (docs/RELEASING.md).
The archive holds the package's modules and data files (`places/*.json`), no caches, tests or Markdown, and a
`__main__.py` that exits with the command's status, which zipapp's generated one would drop.

    python tools/build_pyz.py [TARGET]    # default: dist/leadline.pyz (`make pyz`)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "leadline"
MAIN = "import sys\n\nfrom leadline.cli import main\n\nsys.exit(main())\n"


def build(target: Path, package: Path = PACKAGE) -> Path:
    """Write the archive of `package` (default: leadline) to `target`, replacing any there; returns `target`.

    A source's or API's own tests/ folder and its Markdown stay in the repo, not in the archive.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp)
        shutil.copytree(
            package, staged / "leadline", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "*.md")
        )
        (staged / "__main__.py").write_text(MAIN, encoding="utf-8")
        zipapp.create_archive(staged, target, compressed=True)
    return target


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "leadline.pyz")
