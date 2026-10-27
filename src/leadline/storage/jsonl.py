"""The JSONL backend: a stream is a flat file of that name in the output folder, as the script wrote them."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from leadline import config

SUFFIX = ".jsonl"


def write_text(path: Path, text: str) -> None:
    """Replace the file atomically with `text`, in UTF-8, its line ends as given.

    The text goes to a temporary file of this process (`<file>.<pid>.tmp`, so two runs never share one), is
    flushed to disk, and is renamed over the file; a failed write leaves no temporary file behind. A run
    assumes it is the only writer of its folder: two overlapping runs can still lose one run's row.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("wb") as f:
            f.write(text.encode("utf-8", "surrogateescape"))
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


class Jsonl:
    """The streams of a folder."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def read_lines(self, stream: str) -> list[str]:
        """The file's non-blank lines. A byte that is not valid UTF-8 comes as a surrogate escape, which
        `write_lines` writes back as the same byte, so a line kept in a rewrite is never changed (the script drops
        such bytes). A CRLF line end reads as LF, as the script reads it."""
        path = self._path(stream)
        if not path.exists():
            return []
        with path.open(encoding="utf-8", errors="surrogateescape") as f:
            return [line for line in f if line.strip()]

    def write_lines(self, stream: str, lines: Iterable[str]) -> None:
        path = self._path(stream)
        text = "".join(lines)  # a source of lines that fails does so before the file is touched
        self.folder.mkdir(parents=True, exist_ok=True)
        write_text(path, text)

    def streams(self) -> list[str]:
        if not self.folder.is_dir():
            return []
        return sorted(p.name for p in self.folder.iterdir() if config.plain_file(p.name, SUFFIX) and p.is_file())

    def _path(self, stream: str) -> Path:
        if not config.plain_file(stream, SUFFIX):
            raise ValueError(f"stream {stream!r} must be a plain {SUFFIX} file name")
        return self.folder / stream
