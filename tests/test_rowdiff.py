from __future__ import annotations

from pathlib import Path

import pytest

from leadline import cli, rowdiff

ROW = '{"date": "2026-09-23", "total": 95, "captured_at": "2026-09-23T23:31:05-04:00"}\n'
SAME_ROW_LATER = '{"date": "2026-09-23", "total": 95, "captured_at": "2026-09-24T00:02:11-04:00"}\n'
OLDER = '{"date": "2026-09-22", "total": 94, "captured_at": "2026-09-22T23:31:04-04:00"}\n'


def folders(tmp_path: Path, a: dict[str, str], b: dict[str, str]) -> tuple[Path, Path]:
    dirs = []
    for name, files in (("a", a), ("b", b)):
        folder = tmp_path / name
        folder.mkdir()
        for file, text in files.items():
            (folder / file).write_text(text, encoding="utf-8")
        dirs.append(folder)
    return dirs[0], dirs[1]


def diff(tmp_path: Path, a: dict[str, str], b: dict[str, str]) -> list[str]:
    return rowdiff.compare(*folders(tmp_path, a, b))


def test_identical_folders(tmp_path: Path) -> None:
    assert diff(tmp_path, {"hacs.jsonl": OLDER + ROW}, {"hacs.jsonl": OLDER + ROW}) == []


def test_captured_at_is_ignored(tmp_path: Path) -> None:
    assert diff(tmp_path, {"hacs.jsonl": ROW}, {"hacs.jsonl": SAME_ROW_LATER}) == []


def test_captured_at_must_still_be_present(tmp_path: Path) -> None:
    bare = '{"date": "2026-09-23", "total": 95}\n'

    assert diff(tmp_path, {"hacs.jsonl": ROW}, {"hacs.jsonl": bare}) == [
        "hacs.jsonl date=2026-09-23: captured_at: only in A"
    ]


def test_only_jsonl_files_are_compared(tmp_path: Path) -> None:
    assert diff(tmp_path, {"collect-metrics.log": "x\n"}, {"leadline.log": "y\n"}) == []


def test_missing_and_extra_files(tmp_path: Path) -> None:
    found = diff(tmp_path, {"hacs.jsonl": ROW, "forum.jsonl": ROW}, {"hacs.jsonl": ROW, "pypi-x.jsonl": ROW})

    assert found == ["forum.jsonl: only in A", "pypi-x.jsonl: only in B"]


def test_missing_and_extra_rows(tmp_path: Path) -> None:
    newer = '{"date": "2026-09-24", "total": 96, "captured_at": "2026-09-24T23:31:05-04:00"}\n'

    found = diff(tmp_path, {"hacs.jsonl": OLDER + ROW}, {"hacs.jsonl": ROW + newer})

    assert found == ["hacs.jsonl date=2026-09-22: only in A", "hacs.jsonl date=2026-09-24: only in B"]


def test_differing_fields(tmp_path: Path) -> None:
    a = '{"date": "2026-09-23", "total": 95, "rank": 1, "versions": {"1.0": 3}, "gone": 0}\n'
    b = '{"date": "2026-09-23", "total": 96, "rank": 1.0, "versions": {"1.0": 4}, "new": 0}\n'

    found = diff(tmp_path, {"hacs.jsonl": a}, {"hacs.jsonl": b})

    assert found == [
        "hacs.jsonl date=2026-09-23: total: 95 != 96",
        "hacs.jsonl date=2026-09-23: rank: 1 != 1.0",
        'hacs.jsonl date=2026-09-23: versions: {"1.0": 3} != {"1.0": 4}',
        "hacs.jsonl date=2026-09-23: gone: only in A",
        "hacs.jsonl date=2026-09-23: new: only in B",
    ]


def test_key_order_is_significant(tmp_path: Path) -> None:
    a = '{"date": "2026-09-23", "total": 95, "rank": 1}\n'
    b = '{"date": "2026-09-23", "rank": 1, "total": 95}\n'

    assert diff(tmp_path, {"hacs.jsonl": a}, {"hacs.jsonl": b}) == [
        'hacs.jsonl date=2026-09-23: key order: ["date", "total", "rank"] != ["date", "rank", "total"]'
    ]


def test_nested_key_order_is_significant(tmp_path: Path) -> None:
    a = '{"date": "2026-09-23", "versions": {"1": 1, "2": 2}}\n'
    b = '{"date": "2026-09-23", "versions": {"2": 2, "1": 1}}\n'

    assert diff(tmp_path, {"hacs.jsonl": a}, {"hacs.jsonl": b}) == [
        'hacs.jsonl date=2026-09-23: versions: {"1": 1, "2": 2} != {"2": 2, "1": 1}'
    ]


def test_serialization_is_significant(tmp_path: Path) -> None:
    a = '{"date": "2026-09-23", "name": "caf\\u00e9"}\n'
    b = '{"date":"2026-09-23","name":"café"}\n'

    assert diff(tmp_path, {"hacs.jsonl": a}, {"hacs.jsonl": b}) == [
        "hacs.jsonl date=2026-09-23: same values, different serialization"
    ]


def test_row_order_is_significant(tmp_path: Path) -> None:
    assert diff(tmp_path, {"hacs.jsonl": OLDER + ROW}, {"hacs.jsonl": ROW + OLDER}) == [
        "hacs.jsonl: same rows, different line order or layout"
    ]


def test_trailing_newline_is_significant(tmp_path: Path) -> None:
    assert diff(tmp_path, {"hacs.jsonl": ROW}, {"hacs.jsonl": ROW.rstrip("\n")}) == [
        "hacs.jsonl: same rows, different line order or layout"
    ]


def test_forum_rows_are_keyed_by_topic_too(tmp_path: Path) -> None:
    a = '{"date": "2026-09-23", "topic_id": 1, "posts": 2}\n{"date": "2026-09-23", "topic_id": 5, "posts": 3}\n'
    b = '{"date": "2026-09-23", "topic_id": 1, "posts": 2}\n{"date": "2026-09-23", "topic_id": 5, "posts": 4}\n'

    assert diff(tmp_path, {"forum.jsonl": a}, {"forum.jsonl": b}) == [
        "forum.jsonl date=2026-09-23 topic_id=5: posts: 3 != 4"
    ]


def test_duplicate_rows_for_a_key(tmp_path: Path) -> None:
    assert diff(tmp_path, {"hacs.jsonl": ROW + ROW}, {"hacs.jsonl": ROW}) == [
        "hacs.jsonl date=2026-09-23: 2 rows in A, 1 in B"
    ]


def test_lines_without_a_key_are_matched_by_text(tmp_path: Path) -> None:
    a = 'not json\n{"no_date": 1}\n'
    b = '{"no_date": 1}\nnot json either\n'

    assert diff(tmp_path, {"m.jsonl": a}, {"m.jsonl": b}) == [
        "m.jsonl line 'not json either': only in B",
        "m.jsonl line 'not json': only in A",
    ]


def test_cli_identical_exits_0(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a, b = folders(tmp_path, {"hacs.jsonl": ROW}, {"hacs.jsonl": SAME_ROW_LATER})

    assert cli.main(["diff", str(a), str(b)]) == 0

    assert capsys.readouterr().out == f"A = {a}\nB = {b}\n1 file(s) compared: identical, ignoring captured_at\n"


def test_cli_difference_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a, b = folders(tmp_path, {"hacs.jsonl": ROW, "forum.jsonl": ROW}, {"hacs.jsonl": OLDER})

    assert cli.main(["diff", str(a), str(b)]) == 1

    assert capsys.readouterr().out.splitlines()[2:] == [
        "forum.jsonl: only in A",
        "hacs.jsonl date=2026-09-22: only in B",
        "hacs.jsonl date=2026-09-23: only in A",
        "2 file(s) compared: 3 difference(s), in forum.jsonl (1), hacs.jsonl (2)",
    ]


def test_cli_needs_two_folders(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["diff", str(tmp_path), str(tmp_path / "missing")]) == 2

    assert capsys.readouterr().err == f"leadline: error: not a folder: {tmp_path / 'missing'}\n"


def test_differences_are_grouped_by_file_in_name_order(tmp_path: Path) -> None:
    a, b = folders(tmp_path, {"hacs.jsonl": ROW, "forum.jsonl": ROW, "same.jsonl": ROW}, {"hacs.jsonl": OLDER})
    (b / "same.jsonl").write_text(ROW, encoding="utf-8")

    assert rowdiff.differences(a, b) == {
        "forum.jsonl": ["forum.jsonl: only in A"],
        "hacs.jsonl": ["hacs.jsonl date=2026-09-22: only in B", "hacs.jsonl date=2026-09-23: only in A"],
    }


def test_undecodable_bytes_are_compared_as_they_are(tmp_path: Path) -> None:
    a, b = folders(tmp_path, {}, {})
    (a / "m.jsonl").write_bytes(b'{"date": "2026-09-23", "x": "\xff"}\n')
    (b / "m.jsonl").write_bytes(b'{"date": "2026-09-23", "x": "\xfe"}\n')

    assert rowdiff.compare(a, b) == ['m.jsonl date=2026-09-23: x: "\\udcff" != "\\udcfe"']
