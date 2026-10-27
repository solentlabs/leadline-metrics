"""Where a run keeps its rows: the contract every backend meets, and the backends.

A file is a source entry's row file, named as the config names it (`github-releases-example.jsonl`); a database
backend keeps it as rows in its tables. `store` keeps every rule about what to write, and reaches a file only through
a `Backend`. The unit is the line, because `store` keeps the
lines it does not change exactly as they are: a backend returns each line as it was written, line end and all
(docs/ARCHITECTURE_DECISIONS.md, "Storage is a backend behind one contract"). `tests/test_storage.py` is the
conformance suite every backend passes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from leadline import config

SUFFIX = ".jsonl"


class StorageError(Exception):
    """A backend that cannot be used (a database it cannot open or does not understand); the run stops with it."""


def check_name(file: str) -> None:
    """Raise ValueError unless `file` is a plain `*.jsonl` file name, which every backend takes."""
    if not config.plain_file(file, SUFFIX):
        raise ValueError(f"file {file!r} must be a plain {SUFFIX} file name")


class Backend(Protocol):
    """Where the lines of the files are kept."""

    def read_lines(self, file: str) -> list[str]:
        """The file's lines in order, each as written; a file never written has none."""
        ...

    def write_lines(self, file: str, lines: Iterable[str]) -> None:
        """Replace the file with `lines`, atomically: a write that fails leaves the file as it was."""
        ...

    def files(self) -> list[str]:
        """The names of the files that exist, in order."""
        ...


@dataclass(frozen=True)
class File:
    """A file of a backend: what `store` reads and writes, and what a source holds in place of a path."""

    backend: Backend
    name: str

    def read_lines(self) -> list[str]:
        return self.backend.read_lines(self.name)

    def write_lines(self, lines: Iterable[str]) -> None:
        self.backend.write_lines(self.name, lines)


def open_backend(storage: config.Storage, out_dir: Path) -> Backend:
    """The backend the config names, checked to be usable: a database is opened (and made or migrated) here, so a
    run that cannot keep its files stops before it starts; raises StorageError."""
    backend: Backend
    if storage.backend == "sqlite" and storage.path is not None:
        from leadline.storage.sqlite import Sqlite  # here, not at the top: a jsonl run never loads sqlite3

        backend = Sqlite(storage.path)
        backend.files()
        return backend
    from leadline.storage.jsonl import Jsonl

    return Jsonl(out_dir)


def copy_files(source: Backend, target: Backend) -> tuple[int, int]:
    """Copy every file of `source` into `target`, replacing the file of that name there, and check each reads back as
    it was; returns the number of files and of lines. Raises StorageError for a file that reads back different."""
    files = lines = 0
    for name in source.files():
        found = source.read_lines(name)
        target.write_lines(name, found)
        if target.read_lines(name) != found:
            raise StorageError(f"{name} did not read back as it was written")
        files, lines = files + 1, lines + len(found)
    return files, lines
