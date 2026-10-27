"""Row files over a test folder: the JSONL backend as the tests use it."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from leadline.storage import File
from leadline.storage.jsonl import Jsonl


def files_in(folder: Path) -> Callable[[str], File]:
    """Opens the row file of a name in `folder`, as a run's `ctx.file` does."""
    return lambda name: File(Jsonl(folder), name)


def file_at(path: Path) -> File:
    """The row file at `path`: the file of that name in its folder."""
    return File(Jsonl(path.parent), path.name)
