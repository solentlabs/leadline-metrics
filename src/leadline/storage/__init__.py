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
from typing import Iterable, Protocol


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
