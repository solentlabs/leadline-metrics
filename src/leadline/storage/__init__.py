"""Where a run keeps its rows: the contract every backend meets, and the backends.

A file is a source entry's row file, named as the config names it (`github-releases-example.jsonl`); a database
backend keeps it as rows in its tables. `store` keeps every rule about what to write, and reaches a file only through
a `Backend`. The unit is the line, because `store` keeps the
lines it does not change exactly as they are: a backend returns each line as it was written, line end and all
(docs/ARCHITECTURE_DECISIONS.md, "Storage is a backend behind one contract"). `tests/test_storage.py` is the
conformance suite every backend passes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Collection, Iterable, NamedTuple, Protocol

from leadline import config
from leadline.storage import tables

if TYPE_CHECKING:
    from leadline.http import Http

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


def d1_http() -> Http:
    """The HTTP client the D1 backend sends its requests with (a test gives its own)."""
    from leadline import __version__
    from leadline.http import Http  # here, not at the top: a run that keeps its files locally loads no HTTP

    return Http(f"leadline-metrics/{__version__}")


def open_backend(storage: config.Storage, out_dir: Path) -> Backend:
    """The backend the config names, checked to be usable: a database is opened (and made or migrated) here, so a
    run that cannot keep its files stops before it starts; raises StorageError."""
    backend: Backend
    if storage.backend == "sqlite" and storage.path is not None:
        from leadline.storage.sqlite import Sqlite  # here, not at the top: a jsonl run never loads sqlite3

        backend = Sqlite(storage.path)
        backend.files()
        return backend
    if storage.backend == "d1" and storage.account_id and storage.database_id:
        from leadline.storage import d1

        token = os.environ.get(d1.CREDENTIAL, "")
        if not token:
            raise StorageError(f"set {d1.CREDENTIAL} to an API token that can edit the D1 database")
        backend = d1.D1(storage.account_id, storage.database_id, token, d1_http())
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


class Imported(NamedTuple):
    """What one `import_files` run did."""

    files: int  # files it added lines to
    lines: int
    values: int  # the values in those lines, what a database counts as written
    remaining: int  # lines still to import


def import_files(
    source: Backend, target: Backend, skip: Collection[str] = (), max_values: int | None = None
) -> Imported:
    """Copy the lines of `source`'s files that `target` lacks, in file order, up to `max_values` values in all.

    A file `target` holds is continued: what it holds must be the start of the file in `source`, and only the lines
    after it are added. If any file's held lines are not (it holds a night `source` lacks, or differs), nothing is
    copied and StorageError says which, so a later run is never erased. A line is never split: the first line of a
    run is taken even if it alone is over `max_values`, so every run makes progress. Each file is read back and
    checked after it is written.
    """
    names = [name for name in source.files() if name not in skip]
    present = set(target.files())
    wanted = {name: source.read_lines(name) for name in names}
    held = {name: target.read_lines(name) if name in present else [] for name in names}
    differing = [name for name in names if held[name] != wanted[name][: len(held[name])]]
    if differing:
        raise StorageError(
            f"{', '.join(differing)} in the destination holds lines the source lacks or differs from it; "
            "nothing was imported"
        )
    files = lines = values = 0
    stopped = False
    for name in names:
        taken: list[str] = []
        for line in wanted[name][len(held[name]) :]:
            cost = max(1, len(tables.shred(line) or ()))
            if max_values is not None and lines + len(taken) > 0 and values + cost > max_values:
                stopped = True
                break
            taken.append(line)
            values += cost
        if taken:
            target.write_lines(name, held[name] + taken)
            if target.read_lines(name) != held[name] + taken:
                raise StorageError(f"{name} did not read back as it was written")
            files, lines = files + 1, lines + len(taken)
        if stopped:
            break
    remaining = sum(len(wanted[name]) - len(held[name]) for name in names) - lines
    return Imported(files, lines, values, remaining)
