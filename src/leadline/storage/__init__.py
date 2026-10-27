"""Where a run keeps its rows: the contract every backend meets, and the backends.

A stream is a row file, named as the config names it (`github-releases-example.jsonl`). `store` keeps every rule
about what to write, and reaches a stream only through a `Backend`. The unit is the line, because `store` keeps the
lines it does not change exactly as they are: a backend returns each line as it was written, line end and all
(docs/ARCHITECTURE_DECISIONS.md, "Storage is a backend behind one contract"). `tests/test_storage.py` is the
conformance suite every backend passes.
"""

from __future__ import annotations

from typing import Iterable, Protocol


class Backend(Protocol):
    """Where the lines of the streams are kept."""

    def read_lines(self, stream: str) -> list[str]:
        """The stream's lines in order, each as written; a stream never written has none."""
        ...

    def write_lines(self, stream: str, lines: Iterable[str]) -> None:
        """Replace the stream with `lines`, atomically: a write that fails leaves the stream as it was."""
        ...

    def streams(self) -> list[str]:
        """The names of the streams that exist, in order."""
        ...
