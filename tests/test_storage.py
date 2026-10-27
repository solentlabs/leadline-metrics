"""The storage contract: what every backend must do (docs/ARCHITECTURE_DECISIONS.md, "Storage is a backend").

`BACKENDS` lists each backend with a way to make one on an empty folder. A new backend is added there and must
pass every test unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from leadline.storage import Backend
from leadline.storage.jsonl import Jsonl
from leadline.storage.sqlite import Sqlite

Make = Callable[[Path], Backend]

BACKENDS: dict[str, Make] = {"jsonl": Jsonl, "sqlite": lambda folder: Sqlite(folder / "leadline.db")}

FILE = "example.jsonl"

# Lines as a file can hold them: a line leadline writes, the script's older forms, and what is not a row.
LINES = [
    '{"date": "2026-09-21", "n": 1, "name": "caf\\u00e9"}\n',
    '{"date":"2026-09-22","n":2}\n',  # another spacing, kept as it is
    "not json\n",
    "[1, 2]\n",
    '{"date": "2026-09-23", "n": 3}   \n',  # trailing spaces, kept
    '{"date": "2026-09-24", "n": "\udcff"}\n',  # a byte that is not UTF-8, as the store reads it
    '{"date": "2026-09-26", "n": "\\ud800"}\n',  # a lone surrogate, escaped: valid JSON, not valid UTF-8 as text
    '{"date": "2026-09-27", "b": 1, "a": [{"x": 1.0, "y": null}, true, "t"], "big": 12345678901234567890}\n',
    '{"date": "2026-09-28", "nan": NaN, "inf": Infinity, "small": 1e-07}\n',
    '{"date": "2026-09-29", "n": 5}',  # no line end: only the last line can lack one
]


@pytest.fixture(params=sorted(BACKENDS))
def backend(request: pytest.FixtureRequest, tmp_path: Path) -> Backend:
    return BACKENDS[request.param](tmp_path)


def test_a_file_never_written_has_no_lines(backend: Backend) -> None:
    assert backend.read_lines(FILE) == []


def test_every_line_reads_back_exactly_as_written(backend: Backend) -> None:
    backend.write_lines(FILE, LINES)

    assert backend.read_lines(FILE) == LINES


def test_a_write_replaces_the_file(backend: Backend) -> None:
    backend.write_lines(FILE, LINES)
    backend.write_lines(FILE, LINES[:2])

    assert backend.read_lines(FILE) == LINES[:2]


def test_the_order_and_repeats_of_lines_are_kept(backend: Backend) -> None:
    lines = ['{"date": "2026-09-22"}\n', '{"date": "2026-09-21"}\n', '{"date": "2026-09-22"}\n']
    backend.write_lines(FILE, lines)

    assert backend.read_lines(FILE) == lines


def test_a_file_written_with_no_lines_exists_and_is_empty(backend: Backend) -> None:
    backend.write_lines(FILE, [])

    assert backend.read_lines(FILE) == []
    assert backend.files() == [FILE]


def test_files_names_each_written_file_in_order(backend: Backend) -> None:
    assert backend.files() == []

    backend.write_lines("b.jsonl", ["{}\n"])
    backend.write_lines("a.jsonl", ["{}\n"])

    assert backend.files() == ["a.jsonl", "b.jsonl"]


def test_files_are_independent(backend: Backend) -> None:
    backend.write_lines("a.jsonl", ['{"date": "2026-09-21"}\n'])
    backend.write_lines("b.jsonl", ['{"date": "2026-09-22"}\n'])

    assert backend.read_lines("a.jsonl") == ['{"date": "2026-09-21"}\n']
    assert backend.read_lines("b.jsonl") == ['{"date": "2026-09-22"}\n']


def test_a_write_that_fails_part_way_leaves_the_file_as_it_was(backend: Backend) -> None:
    backend.write_lines(FILE, LINES[:2])

    def failing() -> object:
        yield LINES[2]
        raise RuntimeError("the source of lines failed")

    with pytest.raises(RuntimeError):
        backend.write_lines(FILE, failing())  # type: ignore[arg-type]

    assert backend.read_lines(FILE) == LINES[:2]


@pytest.mark.parametrize("name", ["", "a/b.jsonl", "../a.jsonl", "a.txt", ".jsonl", "a\\b.jsonl"])
def test_a_file_name_is_a_plain_row_file_name(backend: Backend, name: str) -> None:
    with pytest.raises(ValueError, match="file"):
        backend.read_lines(name)
    with pytest.raises(ValueError, match="file"):
        backend.write_lines(name, [])
