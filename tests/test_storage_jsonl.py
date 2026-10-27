"""The JSONL backend: one flat file per file in a folder, as the script wrote them."""

# cspell:ignore getpid
from __future__ import annotations

import os
from pathlib import Path

from leadline.storage.jsonl import Jsonl


def test_a_file_is_the_file_of_that_name_in_the_folder(tmp_path: Path) -> None:
    Jsonl(tmp_path).write_lines("a.jsonl", ['{"n": 1}\n', '{"n": 2}'])

    assert (tmp_path / "a.jsonl").read_bytes() == b'{"n": 1}\n{"n": 2}'


def test_it_reads_a_file_as_the_script_does_crlf_as_lf_and_blank_lines_skipped(tmp_path: Path) -> None:
    (tmp_path / "a.jsonl").write_bytes(b'{"n": 1}\r\n\n  \n{"n": "\xff"}\n')

    assert Jsonl(tmp_path).read_lines("a.jsonl") == ['{"n": 1}\n', '{"n": "\udcff"}\n']


def test_a_byte_that_is_not_utf8_is_written_back_as_the_same_byte(tmp_path: Path) -> None:
    (tmp_path / "a.jsonl").write_bytes(b'{"n": "\xff"}\n')
    backend = Jsonl(tmp_path)

    backend.write_lines("a.jsonl", backend.read_lines("a.jsonl"))

    assert (tmp_path / "a.jsonl").read_bytes() == b'{"n": "\xff"}\n'


def test_files_are_the_row_files_and_nothing_else_in_the_folder(tmp_path: Path) -> None:
    for name in ("b.jsonl", "a.jsonl", "README.md", "trends.json", f"a.jsonl.{os.getpid()}.tmp"):
        (tmp_path / name).write_text("x\n", encoding="utf-8")
    (tmp_path / "dir.jsonl").mkdir()

    assert Jsonl(tmp_path).files() == ["a.jsonl", "b.jsonl"]


def test_a_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    Jsonl(tmp_path).write_lines("a.jsonl", ["{}\n"])

    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.jsonl"]


def test_the_folder_is_made_when_a_file_is_first_written(tmp_path: Path) -> None:
    folder = tmp_path / "metrics"

    Jsonl(folder).write_lines("a.jsonl", ["{}\n"])

    assert (folder / "a.jsonl").read_text(encoding="utf-8") == "{}\n"
