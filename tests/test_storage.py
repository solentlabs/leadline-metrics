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

Make = Callable[[Path], Backend]

BACKENDS: dict[str, Make] = {"jsonl": Jsonl}

STREAM = "example.jsonl"

# Lines as a stream can hold them: a line leadline writes, the script's older forms, and what is not a row.
LINES = [
    '{"date": "2026-09-21", "n": 1, "name": "caf\\u00e9"}\n',
    '{"date":"2026-09-22","n":2}\n',  # another spacing, kept as it is
    "not json\n",
    "[1, 2]\n",
    '{"date": "2026-09-23", "n": 3}   \n',  # trailing spaces, kept
    '{"date": "2026-09-24", "n": "\udcff"}\n',  # a byte that is not UTF-8, as the store reads it
    '{"date": "2026-09-25", "n": 5}',  # no line end
]


@pytest.fixture(params=sorted(BACKENDS))
def backend(request: pytest.FixtureRequest, tmp_path: Path) -> Backend:
    return BACKENDS[request.param](tmp_path)


def test_a_stream_never_written_has_no_lines(backend: Backend) -> None:
    assert backend.read_lines(STREAM) == []


def test_every_line_reads_back_exactly_as_written(backend: Backend) -> None:
    backend.write_lines(STREAM, LINES)

    assert backend.read_lines(STREAM) == LINES


def test_a_write_replaces_the_stream(backend: Backend) -> None:
    backend.write_lines(STREAM, LINES)
    backend.write_lines(STREAM, LINES[:2])

    assert backend.read_lines(STREAM) == LINES[:2]


def test_the_order_and_repeats_of_lines_are_kept(backend: Backend) -> None:
    lines = ['{"date": "2026-09-22"}\n', '{"date": "2026-09-21"}\n', '{"date": "2026-09-22"}\n']
    backend.write_lines(STREAM, lines)

    assert backend.read_lines(STREAM) == lines


def test_a_stream_written_with_no_lines_exists_and_is_empty(backend: Backend) -> None:
    backend.write_lines(STREAM, [])

    assert backend.read_lines(STREAM) == []
    assert backend.streams() == [STREAM]


def test_streams_names_each_written_stream_in_order(backend: Backend) -> None:
    assert backend.streams() == []

    backend.write_lines("b.jsonl", ["{}\n"])
    backend.write_lines("a.jsonl", ["{}\n"])

    assert backend.streams() == ["a.jsonl", "b.jsonl"]


def test_streams_are_independent(backend: Backend) -> None:
    backend.write_lines("a.jsonl", ['{"date": "2026-09-21"}\n'])
    backend.write_lines("b.jsonl", ['{"date": "2026-09-22"}\n'])

    assert backend.read_lines("a.jsonl") == ['{"date": "2026-09-21"}\n']
    assert backend.read_lines("b.jsonl") == ['{"date": "2026-09-22"}\n']


def test_a_write_that_fails_part_way_leaves_the_stream_as_it_was(backend: Backend) -> None:
    backend.write_lines(STREAM, LINES[:2])

    def failing() -> object:
        yield LINES[2]
        raise RuntimeError("the source of lines failed")

    with pytest.raises(RuntimeError):
        backend.write_lines(STREAM, failing())  # type: ignore[arg-type]

    assert backend.read_lines(STREAM) == LINES[:2]


@pytest.mark.parametrize("name", ["", "a/b.jsonl", "../a.jsonl", "a.txt", ".jsonl", "a\\b.jsonl"])
def test_a_stream_name_is_a_plain_row_file_name(backend: Backend, name: str) -> None:
    with pytest.raises(ValueError, match="stream"):
        backend.read_lines(name)
    with pytest.raises(ValueError, match="stream"):
        backend.write_lines(name, [])
