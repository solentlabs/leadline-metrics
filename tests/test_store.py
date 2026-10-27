"""store reproduces the script's writers byte for byte.

The script is the oracle: its three writer heredocs are copied verbatim into
tests/oracle/*.heredoc (collect-metrics.sh 2026.09.23.1: upsert_jsonl lines
233-276, upsert_forum_jsonl 2179-2223, and the usage collector's writer
3575-3706). Each case runs the same input file and
row through the heredoc (under tests/oracle/shim.py) and through store, with
the clock frozen at the same instant in both, and compares the files' bytes,
`captured_at` included.
"""

# cspell:ignore getpid
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest
from row_files import file_at
from script_harness import script_side

from leadline import store
from leadline.config import Hide
from leadline.context import Context, Correction
from leadline.http import Http
from leadline.storage import jsonl

ORACLE = Path(__file__).parent / "oracle"
# Naive, as the script's clock reads (the shim freezes its datetime.now() to
# this); store is given the same instant with its local offset, as ctx.now.
NOW = datetime(2026, 9, 23, 23, 31, 5)  # noqa: DTZ001
WHEN = NOW.astimezone()

Row = Dict[str, Any]


def run_oracle(
    heredoc: str, args: list[str], replies: list[Any] | None = None, tmp: Path | None = None, rc: int = 0
) -> None:
    """Run a writer heredoc on the script's side; `rc` is the exit status it must end with."""
    responses = "-"
    if replies is not None:
        assert tmp is not None
        responses = str(tmp / "replies.json")
        Path(responses).write_text(json.dumps(replies), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(ORACLE / "shim.py"), "heredoc", NOW.isoformat(), responses, str(ORACLE / heredoc), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    assert done.returncode == rc, done.stderr


def seed(tmp_path: Path, existing: bytes | None) -> tuple[Path, Path]:
    theirs, ours = tmp_path / "script" / "m.jsonl", tmp_path / "leadline" / "m.jsonl"
    for path in (theirs, ours):
        path.parent.mkdir()
        if existing is not None:
            path.write_bytes(existing)
    return theirs, ours


# Existing file contents that exercise every rule the writers have.
UPSERT_CASES = {
    "new file": None,
    "append new date": b'{"date": "2026-09-21", "n": 1}\n{"date": "2026-09-22", "n": 2}\n',
    "replace same date": (
        b'{"date": "2026-09-21", "n": 1}\n{"date": "2026-09-23", "n": 9}\n{"date": "2026-09-22", "n": 2}\n'
    ),
    "replace duplicates of the date": b'{"date": "2026-09-23", "n": 8}\n{"date": "2026-09-23", "n": 9}\n',
    "blank lines dropped": b'\n{"date": "2026-09-21", "n": 1}\n   \n\t\n{"date": "2026-09-22", "n": 2}\n\n',
    "unparseable lines kept": b'{"date": "2026-09-21"\nnot json at all\n{"date": "2026-09-23", "n": 9}\n',
    "missing trailing newline": b'{"date": "2026-09-21", "n": 1}\n{"date": "2026-09-22", "n": 2}',
    "existing lines kept byte for byte": (
        b'{"date":"2026-09-21","n":1}\n'
        b'{ "n" : 2 , "date" : "2026-09-22" }  \n'
        b'{"date": "2026-09-20", "x": "caf\xc3\xa9"}\n'
    ),
    "nested date is not the key": b'{"date": "2026-09-01", "upstream": {"date": "2026-09-23"}}\n',
    "crlf line endings": b'{"date": "2026-09-21", "n": 1}\r\n{"date": "2026-09-23", "n": 9}\r\n',
}

NEW_ROW: Row = {"date": "2026-09-23", "name": "café → \U0001f680", "n": 3, "f": 1.5, "none": None}


@script_side
@pytest.mark.parametrize("existing", UPSERT_CASES.values(), ids=list(UPSERT_CASES))
def test_upsert_matches_script(tmp_path: Path, existing: bytes | None) -> None:
    theirs, ours = seed(tmp_path, existing)
    run_oracle("upsert_jsonl.heredoc", [str(theirs), NEW_ROW["date"], json.dumps(NEW_ROW, ensure_ascii=False)])

    store.upsert(file_at(ours), NEW_ROW, WHEN)

    assert ours.read_bytes() == theirs.read_bytes()
    assert list(ours.parent.glob("*.tmp")) == []


LATIN_1_LINE = b'{"date": "2026-09-21", "x": "caf\xe9"}\n'  # not valid UTF-8, as a Windows editor can save it


def test_a_line_that_is_not_valid_utf_8_is_kept_byte_for_byte(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    path.write_bytes(LATIN_1_LINE)

    store.upsert(file_at(path), NEW_ROW, WHEN)

    assert path.read_bytes().startswith(LATIN_1_LINE)
    assert json.loads(path.read_bytes().splitlines()[1])["date"] == NEW_ROW["date"]


def test_a_row_with_bad_bytes_is_still_that_days_row_and_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    path.write_bytes(LATIN_1_LINE)

    store.upsert(file_at(path), {"date": "2026-09-21", "x": "new"}, WHEN)

    assert [row["x"] for row in store.rows(file_at(path))] == ["new"]


def test_a_value_with_bad_bytes_reads_with_a_replacement_character(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    path.write_bytes(LATIN_1_LINE)

    (row,) = store.rows(file_at(path))

    assert row["x"] == "caf\ufffd"
    json.dumps(row, ensure_ascii=False).encode("utf-8")  # a later report or page can write it


def test_a_byte_order_mark_does_not_hide_the_first_row(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    path.write_bytes(b'\xef\xbb\xbf{"date": "2026-09-21", "x": "old"}\n{"date": "2026-09-22", "x": "keep"}\n')

    store.upsert(file_at(path), {"date": "2026-09-21", "x": "new"}, WHEN)

    assert [(row["date"], row["x"]) for row in store.rows(file_at(path))] == [
        ("2026-09-22", "keep"),
        ("2026-09-21", "new"),
    ]
    assert b"old" not in path.read_bytes()


def test_a_write_is_flushed_to_disk_before_the_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    real_fsync, real_replace = os.fsync, Path.replace

    def fsync(fd: int) -> None:
        order.append("fsync")
        real_fsync(fd)

    def replace(self: Path, target: Path) -> Path:
        order.append("replace")
        return real_replace(self, target)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(Path, "replace", replace)

    store.write_text(tmp_path / "m.jsonl", "x\n")

    assert order == ["fsync", "replace"]
    assert (tmp_path / "m.jsonl").read_text(encoding="utf-8") == "x\n"


def test_a_write_that_fails_leaves_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "m.jsonl"
    target.mkdir()  # a folder in the file's place: the rename fails

    with pytest.raises(OSError):  # noqa: PT011 (any OSError: which one the rename raises depends on the system)
        store.write_text(target, "x\n")

    assert list(tmp_path.glob("*.tmp")) == []


def test_two_processes_writing_one_file_do_not_share_a_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    real_replace = Path.replace

    def replace(self: Path, target: Path) -> Path:
        seen.append(self.name)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", replace)

    store.write_text(tmp_path / "m.jsonl", "a\n")
    monkeypatch.setattr(os, "getpid", lambda: 4242)
    store.write_text(tmp_path / "m.jsonl", "b\n")

    assert len(set(seen)) == 2


FORUM_EXISTING = (
    b'{"date": "2026-09-22", "topic_id": 4101, "posts": 1}\n'
    b'{"date": "2026-09-23", "topic_id": 4101, "posts": 2}\n'
    b'{"date": "2026-09-23", "topic_id": 4102, "posts": 3}\n'
    b'{"date": "2026-09-23"}\n'
)


@script_side
@pytest.mark.parametrize("topic_id", [4101, 4102, 5])
def test_second_key_upsert_matches_script(tmp_path: Path, topic_id: int) -> None:
    theirs, ours = seed(tmp_path, FORUM_EXISTING)
    row: Row = {"date": "2026-09-23", "topic_id": topic_id, "posts": 4}
    run_oracle("upsert_forum_jsonl.heredoc", [str(theirs), row["date"], str(topic_id), json.dumps(row)])

    store.upsert(file_at(ours), row, WHEN, second_key="topic_id")

    assert ours.read_bytes() == theirs.read_bytes()


def test_upsert_stamps_captured_at_last_without_changing_the_row(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    row: Row = {"date": "2026-09-23", "n": 1}

    store.upsert(file_at(path), row, WHEN)

    written = json.loads(path.read_text(encoding="utf-8"))
    assert list(written) == ["date", "n", "captured_at"]
    assert written["captured_at"] == WHEN.isoformat(timespec="seconds")
    assert row == {"date": "2026-09-23", "n": 1}


# --- unchanged data is not touched (docs/ARCHITECTURE_DECISIONS.md, "Unchanged rows are left as they are") ---

LATER = datetime(2026, 9, 24, 23, 31, 5).astimezone()  # a later run's clock


def test_upsert_of_an_unchanged_row_leaves_the_file_as_it_is(tmp_path: Path, logged: Logged) -> None:
    path = tmp_path / "m.jsonl"
    store.upsert(file_at(path), {"date": "2026-09-23", "n": 1}, WHEN)
    before = path.read_bytes()

    replaced = store.upsert(file_at(path), {"n": 1, "date": "2026-09-23"}, LATER)  # the same values, any key order

    assert path.read_bytes() == before  # captured_at is the first capture's
    assert replaced is not None
    assert replaced["n"] == 1
    assert logged.messages == ["Added 2026-09-23 to m.jsonl", "Kept 2026-09-23 in m.jsonl: unchanged"]


def test_upsert_of_a_changed_row_replaces_it_and_stamps_the_new_time(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    store.upsert(file_at(path), {"date": "2026-09-23", "n": 1}, WHEN)

    store.upsert(file_at(path), {"date": "2026-09-23", "n": 2}, LATER)

    [row] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert (row["n"], row["captured_at"]) == (2, LATER.isoformat(timespec="seconds"))


def test_upsert_of_a_row_with_a_field_more_adds_it(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    store.upsert(file_at(path), {"date": "2026-09-23", "n": 1}, WHEN)

    store.upsert(file_at(path), {"date": "2026-09-23", "n": 1, "m": 2}, LATER)

    [row] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert (row["m"], row["captured_at"]) == (2, LATER.isoformat(timespec="seconds"))


def test_upsert_of_an_unchanged_row_that_a_file_holds_twice_still_leaves_one(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    line = json.dumps({"date": "2026-09-23", "n": 1, "captured_at": "x"}) + "\n"
    path.write_text(line * 2, encoding="utf-8")

    store.upsert(file_at(path), {"date": "2026-09-23", "n": 1}, LATER)

    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_a_window_leaves_the_rows_whose_values_are_unchanged_as_they_are(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    store.replace_window(file_at(path), [{"date": "2026-09-21", "n": 1}, {"date": "2026-09-22", "n": 2}], WHEN)
    before = path.read_text(encoding="utf-8").splitlines()

    replaced = store.replace_window(
        file_at(path),
        [{"date": "2026-09-21", "n": 1}, {"date": "2026-09-22", "n": 9}, {"date": "2026-09-23", "n": 3}],
        LATER,
    )

    after = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert path.read_text(encoding="utf-8").splitlines()[0] == before[0]  # unchanged: the first capture's line
    stamp = LATER.isoformat(timespec="seconds")
    assert [(r["date"], r["n"], r["captured_at"] == stamp) for r in after] == [
        ("2026-09-21", 1, False),
        ("2026-09-22", 9, True),  # changed: the new time
        ("2026-09-23", 3, True),  # added
    ]
    assert sorted(replaced) == ["2026-09-21", "2026-09-22"]  # what the window held, for the corrections


def test_a_window_with_nothing_changed_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "m.jsonl"
    rows = [{"date": "2026-09-21", "n": 1}, {"date": "2026-09-22", "n": 2}]
    store.replace_window(file_at(path), rows, WHEN)
    writes: list[Path] = []
    monkeypatch.setattr(jsonl, "write_text", lambda p, text: writes.append(p))

    store.replace_window(file_at(path), rows, LATER)

    assert writes == []


# The usage writer, driven through its own fetch code with canned SQL replies.
RUN_DATE = date(2026, 9, 23)
REFETCH_DAYS = 3
DATASET = "example_usage"
WINDOW = [RUN_DATE - timedelta(days=d) for d in range(REFETCH_DAYS, 0, -1)]
TOTALS: list[Row] = [
    {"day": f"{WINDOW[1]} 00:00:00", "event": "active.day", "n": "4"},
    {"day": f"{WINDOW[1]} 00:00:00", "event": "café.open", "n": "2"},
    {"day": f"{WINDOW[2]} 00:00:00", "event": "active.day", "n": "7"},
    {"day": f"{WINDOW[2]} 00:00:00", "event": "a.first", "n": 1.0},
]
SHARED: list[Row] = [{"day": f"{WINDOW[2]} 00:00:00", "n": "3"}]
OK_REPLIES = [{"body": json.dumps({"data": TOTALS})}, {"body": json.dumps({"data": SHARED})}]
FAILED_REPLIES = [
    {"http_error": 500, "body": "upstream down"},
    {"url_error": "timed out"},
    {"http_error": 502, "body": "bad"},
]
FAILURE_ROW: Row = {
    "date": str(WINDOW[-1]),
    "dataset": DATASET,
    "status": "fetch_failed",
    "http_code": "502",
    "reason": "bad",
}


def usage_rows() -> list[Row]:
    """The rows the usage collector builds from TOTALS and SHARED."""
    rows = []
    for day in WINDOW:
        events = {r["event"]: int(float(r["n"])) for r in TOTALS if r["day"].startswith(str(day))}
        shared = [int(float(r["n"])) for r in SHARED if r["day"].startswith(str(day))]
        rows.append(
            {
                "date": str(day),
                "dataset": DATASET,
                "status": "ok",
                "events": dict(sorted(events.items())),
                "active_day_shared": shared[0] if shared else 0,
            }
        )
    return rows


USAGE_EXISTING = {
    "new file": None,
    "rows outside the window kept, all sorted and re-serialized": (
        b'{"date":"2026-09-10","status":"ok"}\n'
        b'{"date": "2026-09-21", "status": "fetch_failed"}\n'
        b'{"date": "2026-08-01", "x": "caf\xc3\xa9"}\n'
    ),
    "unparseable and dateless lines first": (
        b'{"date": "2026-09-12", "status": "ok"}\nnot json\n\n{"no_date": 1}\n'
        b'[1, 2]\n"str"\nnull\n5\n{"date": "2026-09-11"}'
    ),
    "duplicate dates, last wins": b'{"date": "2026-09-11", "n": 1}\n{"date": "2026-09-11", "n": 2}\r\n',
}


@script_side
@pytest.mark.parametrize("existing", USAGE_EXISTING.values(), ids=list(USAGE_EXISTING))
def test_window_replace_matches_script(tmp_path: Path, existing: bytes | None) -> None:
    theirs, ours = seed(tmp_path, existing)
    args = [str(theirs), DATASET, str(RUN_DATE), str(REFETCH_DAYS), "ua"]
    run_oracle("usage_writer.heredoc", args, OK_REPLIES, tmp_path)

    store.replace_window(file_at(ours), usage_rows(), WHEN)

    assert ours.read_bytes() == theirs.read_bytes()


@script_side
@pytest.mark.parametrize(
    "existing",
    [None, b'{"date": "2026-09-22", "status": "fetch_failed"}\n', b'{"date": "2026-09-22", "status": "ok"}\n'],
    ids=["new file", "newest day failed before", "newest day already ok"],
)
def test_window_replace_of_a_failure_matches_script(tmp_path: Path, existing: bytes | None) -> None:
    theirs, ours = seed(tmp_path, existing)
    args = [str(theirs), DATASET, str(RUN_DATE), str(REFETCH_DAYS), "ua"]
    run_oracle("usage_writer.heredoc", args, FAILED_REPLIES, tmp_path, rc=3)

    # The collector's rule: a failure never overwrites a good reading.
    newest_ok = existing is not None and b'"ok"' in existing
    store.replace_window(file_at(ours), [] if newest_ok else [FAILURE_ROW], WHEN)

    assert ours.read_bytes() == theirs.read_bytes()


# Nothing like the wall clock: another year, and an offset no test machine's zone is likely to have.
OTHER_TIME = datetime(2001, 2, 3, 4, 5, 6, 789, tzinfo=timezone(timedelta(hours=5, minutes=45)))


def test_upsert_stamps_the_time_it_is_given_never_the_wall_clock(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"

    store.upsert(file_at(path), {"date": "2026-09-23"}, OTHER_TIME)

    assert json.loads(path.read_text(encoding="utf-8"))["captured_at"] == "2001-02-03T04:05:06+05:45"


def test_window_replace_stamps_every_row_with_the_time_it_is_given(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"

    store.replace_window(file_at(path), usage_rows(), OTHER_TIME)

    stamps = {json.loads(line)["captured_at"] for line in path.read_text(encoding="utf-8").splitlines()}
    assert stamps == {"2001-02-03T04:05:06+05:45"}


@pytest.mark.parametrize("write", [store.upsert, store.replace_window], ids=["upsert", "replace_window"])
def test_a_time_without_its_offset_is_refused(tmp_path: Path, write: Any) -> None:
    path = tmp_path / "m.jsonl"
    rows: Any = {"date": "2026-09-23"} if write is store.upsert else usage_rows()

    with pytest.raises(ValueError, match="UTC offset"):
        write(path, rows, NOW)

    assert not path.exists()


@script_side
@pytest.mark.parametrize("line", [b"null\n", b"[1]\n", b'"text"\n', b"5\n"])
@pytest.mark.parametrize("second_key", [None, "topic_id"])
def test_upsert_keeps_a_non_object_json_line(tmp_path: Path, line: bytes, second_key: str | None) -> None:
    """A line that parses but isn't an object is kept, and the upsert completes.

    Here leadline departs from the script on purpose (docs/ARCHITECTURE_DECISIONS.md,
    "Store never fails on a line it can't use"): the script raises
    AttributeError on such a line, so the oracle can't give the expected
    file. It still gives the new row's bytes: run on an empty file, the
    script writes just the row, and ours must be the kept line followed by
    exactly that.
    """
    theirs, ours = seed(tmp_path, line)
    theirs.write_bytes(b"")
    row: Row = {**NEW_ROW, "topic_id": 5}
    if second_key is None:
        run_oracle("upsert_jsonl.heredoc", [str(theirs), row["date"], json.dumps(row)])
    else:
        run_oracle("upsert_forum_jsonl.heredoc", [str(theirs), row["date"], "5", json.dumps(row)])

    store.upsert(file_at(ours), row, WHEN, second_key=second_key)

    assert ours.read_bytes() == line + theirs.read_bytes()


UNUSABLE_KEY_LINES = [b'{"date": null}\n', b'{"date": 5}\n', b'{"date": [1]}\n', b'{"date": true, "n": 1}\n']


@script_side
@pytest.mark.parametrize("line", UNUSABLE_KEY_LINES)
def test_window_replace_keeps_a_line_whose_date_is_not_a_string(tmp_path: Path, line: bytes) -> None:
    """A line whose `date` isn't a string is kept first, and the write completes.

    Here leadline departs from the script on purpose (docs/ARCHITECTURE_DECISIONS.md,
    "Store never fails on a line it can't use"): beside normal rows, the
    script raises TypeError sorting such a line, so the oracle can't give the
    expected file. It still gives the rows' bytes: run on the file without
    the line, and ours must be the kept line followed by exactly that.
    """
    normal = b'{"date": "2026-09-12", "status": "ok"}\n'
    theirs, ours = seed(tmp_path, normal)
    ours.write_bytes(normal + line)
    args = [str(theirs), DATASET, str(RUN_DATE), str(REFETCH_DAYS), "ua"]
    run_oracle("usage_writer.heredoc", args, OK_REPLIES, tmp_path)

    store.replace_window(file_at(ours), usage_rows(), WHEN)

    assert ours.read_bytes() == line + theirs.read_bytes()


@script_side
@pytest.mark.parametrize("second_key", [None, "topic_id"])
def test_upsert_keeps_lines_whose_key_is_missing_or_not_a_string(tmp_path: Path, second_key: str | None) -> None:
    """Lines with no usable key never match the new row and are kept as they are.

    The script handles these without crashing, so the oracle gives the expected file.
    """
    unusable = b"".join(UNUSABLE_KEY_LINES) + b'{"n": 1}\n'
    existing = unusable + b'{"date": "2026-09-23", "topic_id": [5]}\n'
    theirs, ours = seed(tmp_path, existing)
    row: Row = {**NEW_ROW, "topic_id": 5}
    if second_key is None:
        run_oracle("upsert_jsonl.heredoc", [str(theirs), row["date"], json.dumps(row)])
    else:
        run_oracle("upsert_forum_jsonl.heredoc", [str(theirs), row["date"], "5", json.dumps(row)])

    store.upsert(file_at(ours), row, WHEN, second_key=second_key)

    assert ours.read_bytes() == theirs.read_bytes()
    assert ours.read_bytes().startswith(unusable)


def test_rows_by_date_reads_each_dated_row_the_last_for_a_date_winning(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_bytes(
        b'{"date": "2026-09-21", "n": 1}\n'
        b"not json\n"
        b"[1]\n"
        b"\n"
        b'{"date": 5, "n": 2}\n'
        b'{"n": 3}\n'
        b'{"date": "2026-09-20", "n": 4}\r\n'
        b'{"date": "2026-09-21", "n": 5}'
    )

    assert store.rows_by_date(file_at(path)) == {
        "2026-09-21": {"date": "2026-09-21", "n": 5},
        "2026-09-20": {"date": "2026-09-20", "n": 4},
    }


def test_rows_are_every_object_line_in_file_order(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_bytes(
        b'{"date": "2026-09-21", "n": 1}\n'
        b"not json\n"
        b"[1]\n"
        b"null\n"
        b"\n"
        b'{"date": 5, "n": 2}\n'
        b'{"n": 3}\n'
        b'{"date": "2026-09-21", "n": 5}'
    )

    assert store.rows(file_at(path)) == [
        {"date": "2026-09-21", "n": 1},
        {"date": 5, "n": 2},
        {"n": 3},
        {"date": "2026-09-21", "n": 5},
    ]


def test_rows_of_a_missing_file_are_none(tmp_path: Path) -> None:
    assert store.rows(file_at(tmp_path / "absent.jsonl")) == []


def test_rows_by_date_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert store.rows_by_date(file_at(tmp_path / "absent.jsonl")) == {}


def test_dated_rows_are_the_good_rows_one_a_date_the_last_line_winning_oldest_first(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text(
        '{"date": "2026-09-22", "n": 1}\n'
        '{"date": "2026-09-20", "n": 2, "status": "ok"}\n'
        '{"date": "2026-09-22", "n": 3}\n'
        '{"date": "2026-09-21", "status": "fetch_failed"}\n'
        '{"date": "2026-09-23", "n": 4}\n'
        '{"date": "2026-09-23", "status": "partial", "n": 5}\n'
        '{"date": "2026-02-30", "n": 6}\n'
        '{"date": "2026-09-24", "status": "fetch_failed"}\n',
        encoding="utf-8",
    )

    dated = store.dated_rows(file_at(path))

    assert dated.good == [{"date": "2026-09-20", "n": 2, "status": "ok"}, {"date": "2026-09-22", "n": 3}]
    assert dated.newest == "2026-09-24"


def test_dated_rows_leave_out_the_hidden_items_of_a_list_ignoring_a_leading_v_and_change_no_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rows.jsonl"
    row = {"date": "2026-09-24", "n": 1, "t": {"l": [{"tag": "v1.0.0", "d": 1}, {"tag": "1.1.0"}, {"tag": "v1.2.0"}]}}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    (shown,) = store.dated_rows(file_at(path), hide=[Hide("t.l", "tag", frozenset({"1.0.0", "1.1.0"}))]).good

    assert shown == {"date": "2026-09-24", "n": 1, "t": {"l": [{"tag": "v1.2.0"}]}}
    assert path.read_text(encoding="utf-8") == before
    assert store.dated_rows(file_at(path), hide=[Hide("t.x", "tag", frozenset({"1.0.0"}))]).good == [
        row
    ]  # no such list


def test_dated_rows_keep_the_rows_where_says_by_dotted_field(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    rows = [{"date": "2026-09-24", "t": {"id": i}, "n": i} for i in (1, 2)]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    assert store.dated_rows(file_at(path), {"t.id": 2}).good == [rows[1]]
    assert store.dated_rows(file_at(path), {"t.id": 3}) == store.Dated([], None)
    assert store.dated_rows(file_at(tmp_path / "absent.jsonl")) == store.Dated([], None)


@pytest.mark.parametrize(
    ("row", "field", "expected"),
    [
        ({"a": {"b": 2}}, "a.b", 2),
        ({"a": 1}, "a", 1),
        ({"a": 1}, "a.b", None),
        ({"a": None}, "a", None),
        ({}, "a", None),
        (None, "a", None),
    ],
)
def test_value_reads_a_dotted_field_none_where_there_is_none(row: Any, field: str, expected: Any) -> None:
    assert store.value(row, field) == expected


# --- failure rows (ADR: a failure never replaces good data) ---


class Logged(logging.Handler):
    """The "leadline" logger's messages, caught on the logger itself (a run turns off its propagation)."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def logged() -> Iterator[Logged]:
    log, handler = logging.getLogger("leadline"), Logged()
    level = log.level
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    yield handler
    log.removeHandler(handler)
    log.setLevel(level)


FAILURE = {"date": "2026-09-23", "site": "example", "status": "fetch_failed", "http_code": "503", "reason": "down"}


@pytest.mark.parametrize(
    ("row", "good"),
    [
        ({"date": "2026-09-23", "n": 1}, True),
        ({"date": "2026-09-23", "status": "ok", "n": 1}, True),
        ({"date": "2026-09-23", "status": "fetch_failed"}, False),
        ({"date": "2026-09-23", "status": "parse_failed"}, False),
        ({"date": "2026-09-23", "status": None}, True),  # absent and null read alike
        ({"date": "2026-09-23", "status": ""}, False),
        ({"date": "2026-09-23", "status": store.PARTIAL, "n": None, "m": 1}, False),  # health counts it failed
    ],
)
def test_a_good_row_has_no_status_or_ok(row: Row, good: bool) -> None:
    assert store.is_good(row) is good


@pytest.mark.parametrize(
    "existing",
    [
        None,
        b'{"date": "2026-09-22", "n": 1}\n',
        b'{"date": "2026-09-23", "site": "example", "status": "parse_failed", "reason": "x"}\n',
        b'{"date": "2026-09-23", "status": "fetch_failed"}\n{"date": "2026-09-23", "status": "parse_failed"}\n',
    ],
    ids=["no file", "no row for the date", "a failure row", "two failure rows"],
)
def test_write_failure_writes_where_no_good_row_holds_the_date(tmp_path: Path, existing: bytes | None) -> None:
    theirs, ours = seed(tmp_path, existing)
    store.upsert(file_at(theirs), FAILURE, WHEN)

    assert store.write_failure(file_at(ours), FAILURE, WHEN) is True
    assert ours.read_bytes() == theirs.read_bytes()


@pytest.mark.parametrize(
    "existing",
    [
        '{"date": "2026-09-23", "status": "ok", "n": 1}\n{"date": "2026-09-22", "status": "fetch_failed"}\n',
        '{"date": "2026-09-23", "n": 1}\n',
        # upsert would drop every line with the date, the good one too
        '{"date": "2026-09-23", "n": 1}\n{"date": "2026-09-23", "status": "fetch_failed"}\n',
    ],
    ids=["status ok", "no status", "a good row then a failure row"],
)
def test_write_failure_keeps_a_good_row_and_says_so(tmp_path: Path, existing: str, logged: Logged) -> None:
    path = tmp_path / "m.jsonl"
    path.write_text(existing, encoding="utf-8")

    wrote = store.write_failure(file_at(path), FAILURE, WHEN)

    assert wrote is False
    assert path.read_text(encoding="utf-8") == existing
    assert logged.messages == ["WARN: kept the good row for 2026-09-23 in m.jsonl; fetch_failed not recorded"]


def test_write_failure_by_a_second_key_keeps_only_that_keys_good_row(tmp_path: Path, logged: Logged) -> None:
    path = tmp_path / "m.jsonl"
    path.write_text('{"date": "2026-09-23", "topic_id": 1, "views": 5}\n', encoding="utf-8")
    failure = {"date": "2026-09-23", "topic_id": 2, "status": "fetch_failed"}

    assert store.write_failure(file_at(path), failure, WHEN, second_key=store.SECOND_KEY) is True
    assert store.write_failure(file_at(path), {**failure, "topic_id": 1}, WHEN, second_key=store.SECOND_KEY) is False

    assert path.read_text(encoding="utf-8") == (
        '{"date": "2026-09-23", "topic_id": 1, "views": 5}\n'
        f'{{"date": "2026-09-23", "topic_id": 2, "status": "fetch_failed", "captured_at": "{WHEN.isoformat()}"}}\n'
    )
    kept = "WARN: kept the good row for 2026-09-23 topic_id=1 in m.jsonl; fetch_failed not recorded"
    assert logged.messages[-1] == kept


@pytest.mark.parametrize("status", [{}, {"status": "ok"}])
def test_write_failure_refuses_a_good_row(tmp_path: Path, status: Row) -> None:
    path = tmp_path / "m.jsonl"

    with pytest.raises(ValueError, match="not a failure row"):
        store.write_failure(file_at(path), {"date": "2026-09-23", "n": 1, **status}, WHEN)
    assert not path.exists()


# --- partial rows (ADR: a partly failed row keeps its good parts; a run never erases what it didn't get) ---

READING = {"date": "2026-09-23", "site": "example", "n": 3, "m": None, "k": None}
M_FAILED, K_FAILED = "m request failed (HTTP 503)", "k response was not JSON"
BOTH_FAILED = {M_FAILED: ["m"], K_FAILED: ["k"]}


def line(**fields: Any) -> str:
    """A row an earlier run wrote for the run's date, as its line in the file."""
    return json.dumps({"date": "2026-09-23", "site": "example", **fields, "captured_at": "2026-09-23T08:00:00+00:00"})


def written(**fields: Any) -> str:
    """The file after this run's write of the run's date: its one row, in the source's field order."""
    return json.dumps({"date": "2026-09-23", "site": "example", **fields, "captured_at": WHEN.isoformat()}) + "\n"


def test_write_partial_writes_the_row_marked_partial_naming_each_failure(tmp_path: Path) -> None:
    theirs, ours = seed(tmp_path, b'{"date": "2026-09-22", "n": 1}\n')

    store.write_partial(file_at(ours), READING, BOTH_FAILED, run_context(tmp_path), "src")

    store.upsert(file_at(theirs), {**READING, "status": "partial", "reason": f"{M_FAILED}; {K_FAILED}"}, WHEN)
    assert ours.read_bytes() == theirs.read_bytes()


@pytest.mark.parametrize(
    "existing",
    [
        b'{"date": "2026-09-23", "site": "example", "status": "fetch_failed", "reason": "x"}\n',
        b'{"date": "2026-09-23", "status": "fetch_failed"}\n{"date": "2026-09-23", "status": "parse_failed"}\n',
    ],
    ids=["a failure row", "two failure rows"],
)
def test_a_partial_row_replaces_a_failure_row(tmp_path: Path, existing: bytes) -> None:
    """A failure row holds no values, so there is nothing to keep from it."""
    theirs, ours = seed(tmp_path, existing)

    store.write_partial(file_at(ours), READING, {M_FAILED: ["m"]}, run_context(tmp_path), "src")

    store.upsert(file_at(theirs), {**READING, "status": "partial", "reason": M_FAILED}, WHEN)
    assert ours.read_bytes() == theirs.read_bytes()


def test_a_failure_row_never_replaces_a_partial_row(tmp_path: Path, logged: Logged) -> None:
    path = tmp_path / "m.jsonl"
    existing = line(n=1, m=None, k=2, status="partial", reason=M_FAILED) + "\n"
    path.write_text(existing, encoding="utf-8")

    assert store.write_failure(file_at(path), FAILURE, WHEN) is False

    assert path.read_text(encoding="utf-8") == existing
    assert logged.messages == ["WARN: kept the partial row for 2026-09-23 in m.jsonl; fetch_failed not recorded"]


@pytest.mark.parametrize(
    ("existing", "row", "failed", "expected"),
    [
        (
            line(n=1, m=2, k=None, status="partial", reason=K_FAILED),
            {**READING, "k": 4},
            {M_FAILED: ["m"]},
            written(n=3, m=2, k=4),
        ),
        (
            line(n=1, m=None, k=None, status="partial", reason=f"{M_FAILED}; {K_FAILED}"),
            {**READING, "k": 4},
            {M_FAILED: ["m"]},
            written(n=3, m=None, k=4, status="partial", reason=M_FAILED),
        ),
        (
            line(n=1, m=2, k=None, status="partial", reason=K_FAILED),
            READING,
            BOTH_FAILED,
            written(n=3, m=2, k=None, status="partial", reason=K_FAILED),
        ),
        (line(n=1, m=2, k=3), READING, BOTH_FAILED, written(n=3, m=2, k=3)),
        (line(n=1, m=None, k=3), {**READING, "k": 4}, {M_FAILED: ["m"]}, written(n=3, m=None, k=4)),
        (
            line(n=1, m=2, k=None, status="partial", reason="p failed"),
            READING,
            {"p failed": ["m", "k"]},
            written(n=3, m=None, k=None, status="partial", reason="p failed"),
        ),
        (
            line(n=1, m=2, k=3) + "\n" + line(status="fetch_failed", reason="x"),
            READING,
            BOTH_FAILED,
            written(n=3, m=2, k=3),
        ),
    ],
    ids=[
        "each run's good values kept: partial becomes good once filled",
        "a value no run got stays null, named by this run's failure",
        "a value an earlier run got is kept; the reason names only what is still missing",
        "a good row fills every failed part, so the row stays good",
        "a good row's null is a value",
        "a part is filled whole or not at all",
        "a failure line after the stored row holds nothing",
    ],
)
def test_a_partial_reading_keeps_the_stored_values_it_failed_to_get(
    tmp_path: Path, existing: str, row: Row, failed: dict[str, list[str]], expected: str
) -> None:
    """A run writes what it got, and never erases what it didn't."""
    path = tmp_path / "m.jsonl"
    path.write_text(existing + "\n", encoding="utf-8")
    ctx = run_context(tmp_path)

    store.write_partial(file_at(path), row, failed, ctx, "src")

    assert path.read_text(encoding="utf-8") == expected
    assert ctx.corrections == []  # the run's own date


def test_a_merged_write_says_which_stored_values_it_kept(tmp_path: Path, logged: Logged) -> None:
    path = tmp_path / "m.jsonl"
    path.write_text(line(n=1, m=2, k=3) + "\n", encoding="utf-8")

    store.write_partial(file_at(path), READING, BOTH_FAILED, run_context(tmp_path), "src")

    assert logged.messages == [
        "Kept the stored m, k for 2026-09-23 in m.jsonl, which this run failed to get",
        "Replaced 2026-09-23 in m.jsonl",
    ]


def test_a_merged_write_that_changes_a_past_days_good_row_is_a_correction(tmp_path: Path) -> None:
    """The merged row is good, so it is written as write_good writes one: its changed values are recorded."""
    path = tmp_path / "m.jsonl"
    path.write_text('{"date": "2026-09-22", "site": "example", "n": 1, "m": 2, "k": 3}\n', encoding="utf-8")
    ctx = run_context(tmp_path)

    store.write_partial(file_at(path), {**READING, "date": "2026-09-22", "n": 5, "k": 3}, {M_FAILED: ["m"]}, ctx, "src")

    assert store.rows_by_date(file_at(path))["2026-09-22"] == {
        "date": "2026-09-22",
        "site": "example",
        "n": 5,
        "m": 2,
        "k": 3,
        "captured_at": WHEN.isoformat(),
    }
    assert ctx.corrections == [Correction("src", "m.jsonl", "2026-09-22", {"n": 1}, {"n": 5})]


def test_a_merge_by_a_second_key_reads_only_that_keys_row(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    rows = [
        '{"date": "2026-09-23", "topic_id": 1, "n": 1, "m": 2}',
        '{"date": "2026-09-23", "topic_id": 2, "n": 1, "m": 9}',
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    row = {"date": "2026-09-23", "topic_id": 2, "n": 3, "m": None}

    store.write_partial(
        file_at(path), row, {M_FAILED: ["m"]}, run_context(tmp_path), "src", second_key=store.SECOND_KEY
    )

    assert store.rows_by_date(file_at(path))["2026-09-23"] == {**row, "m": 9, "captured_at": WHEN.isoformat()}


def test_a_good_row_replaces_a_partial_row_and_is_no_correction(tmp_path: Path) -> None:
    """A partial row is no good row, so a later good reading of a past day fills it; it corrects nothing."""
    path = tmp_path / "m.jsonl"
    past = {**READING, "date": "2026-09-22"}
    ctx = run_context(tmp_path)
    store.write_partial(file_at(path), past, BOTH_FAILED, ctx, "src")

    store.write_good(file_at(path), {**past, "m": 2, "k": 3}, ctx, "src")

    assert store.rows_by_date(file_at(path))["2026-09-22"] == {**past, "m": 2, "k": 3, "captured_at": WHEN.isoformat()}
    assert ctx.corrections == []


@pytest.mark.parametrize(
    ("row", "failed", "problem"),
    [
        (READING, {}, "a partial row names what failed"),
        ({**READING, "status": "fetch_failed"}, {"m": ["m"]}, "a partial row has no status of its own but ok"),
        ({**READING, "status": "partial"}, {"m": ["m"]}, "a partial row has no status of its own but ok"),
        ({**READING, "m": 0}, {M_FAILED: ["m"]}, "a failed part's fields are in the row and null; not m$"),
        (READING, {M_FAILED: ["x"]}, "a failed part's fields are in the row and null; not x$"),
    ],
    ids=["nothing failed", "a failure status", "a partial status", "a failed part not null", "a failed part not in it"],
)
def test_write_partial_refuses_a_row_that_is_not_one(
    tmp_path: Path, row: Row, failed: dict[str, list[str]], problem: str
) -> None:
    path = tmp_path / "m.jsonl"

    with pytest.raises(ValueError, match=problem):
        store.write_partial(file_at(path), row, failed, run_context(tmp_path), "src")
    assert not path.exists()


# A source whose good rows carry `status: ok` (pypi) gives its reading with that status in place.
READING_OK = {"date": "2026-09-23", "site": "example", "status": "ok", "n": 3, "m": None}


def test_a_readings_ok_status_becomes_partial_in_place(tmp_path: Path) -> None:
    """The row keeps the source's field order: `partial` where `ok` was, the reason after the fields."""
    theirs, ours = seed(tmp_path, b"")

    store.write_partial(file_at(ours), READING_OK, {M_FAILED: ["m"]}, run_context(tmp_path), "src")

    store.upsert(file_at(theirs), {**READING_OK, "status": "partial", "reason": M_FAILED}, WHEN)
    assert ours.read_bytes() == theirs.read_bytes()
    assert list(json.loads(ours.read_text())) == ["date", "site", "status", "n", "m", "reason", "captured_at"]


def test_a_reading_with_an_ok_status_filled_from_the_stored_row_is_good_with_its_status(tmp_path: Path) -> None:
    theirs, ours = seed(tmp_path, (line(status="ok", n=1, m=2) + "\n").encode())

    store.write_partial(file_at(ours), READING_OK, {M_FAILED: ["m"]}, run_context(tmp_path), "src")

    store.upsert(file_at(theirs), {**READING_OK, "m": 2}, WHEN)
    assert ours.read_bytes() == theirs.read_bytes()


# --- a source's own merge: the stored row it merges with, and the merged row written ---


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        ("", None),
        (line(status="fetch_failed", reason="x"), None),
        (line(n=1), {"n": 1}),
        (line(n=1, status="partial", reason="x"), {"n": 1, "status": "partial", "reason": "x"}),
        (line(n=1) + "\n" + line(status="fetch_failed", reason="x"), {"n": 1}),
        ('{"date": "2026-09-22", "site": "example", "n": 1}', None),
    ],
    ids=[
        "no file's row",
        "a failure row",
        "a good row",
        "a partial row",
        "the good row before a failure",
        "another day",
    ],
)
def test_stored_is_the_rows_last_good_or_partial_line(tmp_path: Path, existing: str, expected: Row | None) -> None:
    path = tmp_path / "m.jsonl"
    path.write_text(existing + "\n", encoding="utf-8")

    found = store.stored(file_at(path), READING)

    stamp = {"captured_at": "2026-09-23T08:00:00+00:00"}
    assert found == (None if expected is None else {"date": "2026-09-23", "site": "example", **expected, **stamp})


def test_stored_reads_by_the_second_key_too(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    path.write_text('{"date": "2026-09-23", "topic_id": 1, "n": 1}\n{"date": "2026-09-23", "topic_id": 2, "n": 2}\n')

    found = store.stored(file_at(path), {"date": "2026-09-23", "topic_id": 1}, second_key=store.SECOND_KEY)

    assert found == {"date": "2026-09-23", "topic_id": 1, "n": 1}


def test_write_merged_with_something_missing_writes_a_partial_row_naming_it(tmp_path: Path) -> None:
    theirs, ours = seed(tmp_path, b'{"date": "2026-09-23", "site": "example", "n": 1}\n')

    store.write_merged(file_at(ours), READING, [M_FAILED, K_FAILED], run_context(tmp_path), "src")

    store.upsert(file_at(theirs), {**READING, "status": "partial", "reason": f"{M_FAILED}; {K_FAILED}"}, WHEN)
    assert ours.read_bytes() == theirs.read_bytes()


def test_write_merged_with_nothing_missing_is_a_good_row_and_records_a_correction(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    path.write_text('{"date": "2026-09-22", "site": "example", "n": 1}\n', encoding="utf-8")
    ctx = run_context(tmp_path)

    store.write_merged(file_at(path), {"date": "2026-09-22", "site": "example", "n": 5}, [], ctx, "src")

    assert store.rows_by_date(file_at(path))["2026-09-22"] == {
        "date": "2026-09-22",
        "site": "example",
        "n": 5,
        "captured_at": WHEN.isoformat(),
    }
    assert ctx.corrections == [Correction("src", "m.jsonl", "2026-09-22", {"n": 1}, {"n": 5})]


def test_write_merged_refuses_a_row_with_a_status_but_ok(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"

    with pytest.raises(ValueError, match="a merged row has no status of its own but ok"):
        store.write_merged(file_at(path), {**READING, "status": "partial"}, [M_FAILED], run_context(tmp_path), "src")
    assert not path.exists()


# --- good rows, and the corrections they make (ADR: every correction is recorded) ---


def run_context(tmp_path: Path) -> Context:
    """A run on WHEN, whose run date is 2026-09-23: 2026-09-22 is a past day."""
    return Context(now=WHEN, out_dir=tmp_path, log=logging.getLogger("leadline"), http=Http("ua"))


PAST = '{"date": "2026-09-22", "status": "ok", "n": 1, "m": 2, "captured_at": "2026-09-22T23:31:05+00:00"}\n'


@pytest.mark.parametrize(
    ("existing", "row", "old", "new"),
    [
        (PAST, {"date": "2026-09-22", "status": "ok", "n": 5, "m": 2}, {"n": 1}, {"n": 5}),
        (PAST, {"date": "2026-09-22", "status": "ok", "n": 1}, {"m": 2}, {}),
        (PAST, {"date": "2026-09-22", "status": "ok", "n": 1, "m": 2, "k": 3}, {}, {"k": 3}),
        (PAST, {"date": "2026-09-22", "n": 1, "m": 2}, {"status": "ok"}, {}),
        (PAST, {"date": "2026-09-22", "status": "ok", "n": 1, "m": 2.0}, {"m": 2}, {"m": 2.0}),
    ],
    ids=["a value changed", "a field gone", "a field added", "status dropped", "an int become a float"],
)
def test_a_good_row_replacing_a_past_days_good_row_with_other_values_is_a_correction(
    tmp_path: Path, existing: str, row: Row, old: Row, new: Row
) -> None:
    theirs, ours = seed(tmp_path, existing.encode())
    ctx = run_context(tmp_path)

    store.write_good(file_at(ours), row, ctx, "src")

    store.upsert(file_at(theirs), row, WHEN)
    assert ours.read_bytes() == theirs.read_bytes()
    assert ctx.corrections == [Correction("src", "m.jsonl", "2026-09-22", old, new)]


@pytest.mark.parametrize(
    ("existing", "row"),
    [
        (None, {"date": "2026-09-22", "n": 1}),
        ('{"date": "2026-09-21", "n": 1}\n', {"date": "2026-09-22", "n": 1}),
        ('{"date": "2026-09-22", "status": "fetch_failed", "http_code": "500"}\n', {"date": "2026-09-22", "n": 1}),
        (PAST, {"date": "2026-09-22", "status": "ok", "n": 1, "m": 2}),
        ('{"date": "2026-09-23", "n": 1}\n', {"date": "2026-09-23", "n": 2}),
        ('{"date": "2026-09-24", "n": 1}\n', {"date": "2026-09-24", "n": 2}),
    ],
    ids=["no file", "a new date", "a failure row replaced", "the same values", "the run's date", "a later date"],
)
def test_other_good_writes_are_no_correction(tmp_path: Path, existing: str | None, row: Row) -> None:
    theirs, ours = seed(tmp_path, None if existing is None else existing.encode())
    ctx = run_context(tmp_path)

    store.write_good(file_at(ours), row, ctx, "src")

    store.upsert(file_at(theirs), row, WHEN)
    assert ours.read_bytes() == theirs.read_bytes()
    assert ctx.corrections == []


def test_a_correction_by_a_second_key_names_its_key(tmp_path: Path) -> None:
    theirs, ours = seed(tmp_path, FORUM_EXISTING)
    ctx = run_context(tmp_path)
    row: Row = {"date": "2026-09-22", "topic_id": 4101, "posts": 7}

    store.write_good(file_at(ours), row, ctx, "forum", second_key=store.SECOND_KEY)
    store.write_good(
        file_at(ours), {"date": "2026-09-22", "topic_id": 4102, "posts": 1}, ctx, "forum", store.SECOND_KEY
    )

    store.upsert(file_at(theirs), row, WHEN, second_key=store.SECOND_KEY)
    store.upsert(
        file_at(theirs), {"date": "2026-09-22", "topic_id": 4102, "posts": 1}, WHEN, second_key=store.SECOND_KEY
    )
    assert ours.read_bytes() == theirs.read_bytes()
    assert ctx.corrections == [
        Correction("forum", "m.jsonl", "2026-09-22", {"posts": 1}, {"posts": 7}, {"topic_id": 4101})
    ]


@pytest.mark.parametrize("status", ["fetch_failed", "parse_failed"])
def test_write_good_refuses_a_failure_row(tmp_path: Path, status: str) -> None:
    path = tmp_path / "m.jsonl"

    with pytest.raises(ValueError, match="not a good row"):
        store.write_good(file_at(path), {"date": "2026-09-22", "status": status}, run_context(tmp_path), "src")
    assert not path.exists()


def test_a_window_replacing_past_days_good_rows_records_each_correction(tmp_path: Path) -> None:
    existing = (
        b'{"date": "2026-09-19", "status": "ok", "n": 1}\n'  # unchanged
        b'{"date": "2026-09-20", "status": "ok", "n": 1}\n'  # corrected
        b'{"date": "2026-09-21", "status": "fetch_failed"}\n'  # a failure row replaced
        b'{"date": "2026-09-23", "status": "ok", "n": 1}\n'  # the run's date
    )
    rows: list[Row] = [
        {"date": "2026-09-19", "status": "ok", "n": 1},
        {"date": "2026-09-20", "status": "ok", "n": 2},
        {"date": "2026-09-21", "status": "ok", "n": 3},
        {"date": "2026-09-23", "status": "ok", "n": 4},
        {"date": "2026-09-18", "status": "ok", "n": 5},  # a new day
    ]
    theirs, ours = seed(tmp_path, existing)
    ctx = run_context(tmp_path)

    store.write_window(file_at(ours), rows, ctx, "usage")

    store.replace_window(file_at(theirs), rows, WHEN)
    assert ours.read_bytes() == theirs.read_bytes()
    assert ctx.corrections == [Correction("usage", "m.jsonl", "2026-09-20", {"n": 1}, {"n": 2})]


WINDOW_FAILURE: Row = {"date": "2026-09-22", "status": "fetch_failed", "http_code": "503"}


@pytest.mark.parametrize(
    ("existing", "held"),
    [
        (b'{"date": "2026-09-22", "status": "ok", "n": 1}\n', "good"),
        (b'{"date": "2026-09-22", "n": 1}\n{"date": "2026-09-22", "status": "fetch_failed"}\n', "good"),
        (b'{"date": "2026-09-22", "status": "partial", "n": 1, "reason": "x"}\n', "partial"),
    ],
    ids=["a good row", "a good row then a failure row", "a partial row"],
)
def test_a_window_never_puts_a_failure_row_over_a_good_or_partial_row(
    tmp_path: Path, existing: bytes, held: str, logged: Logged
) -> None:
    """ADR: a failure never replaces good data; the file is still rewritten, as the script's window is."""
    theirs, ours = seed(tmp_path, existing + b'{"date": "2026-09-01", "status": "ok"}\n')

    store.write_window(
        file_at(ours), [WINDOW_FAILURE, {"date": "2026-09-21", "status": "ok"}], run_context(tmp_path), "usage"
    )

    store.replace_window(file_at(theirs), [{"date": "2026-09-21", "status": "ok"}], WHEN)
    assert ours.read_bytes() == theirs.read_bytes()
    assert f"WARN: kept the {held} row for 2026-09-22 in m.jsonl; fetch_failed not recorded" in logged.messages


@pytest.mark.parametrize(
    "existing", [None, b'{"date": "2026-09-22", "status": "parse_failed"}\n'], ids=["no row", "a failure row"]
)
def test_a_window_writes_a_failure_row_where_no_good_row_holds_its_date(tmp_path: Path, existing: bytes | None) -> None:
    theirs, ours = seed(tmp_path, existing)

    store.write_window(file_at(ours), [WINDOW_FAILURE], run_context(tmp_path), "usage")

    store.replace_window(file_at(theirs), [WINDOW_FAILURE], WHEN)
    assert ours.read_bytes() == theirs.read_bytes()


def test_nested_values_in_another_key_order_are_no_correction(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    path.write_text('{"date": "2026-09-22", "top": [{"ref": "a", "count": 1}]}\n', encoding="utf-8")
    ctx = run_context(tmp_path)

    store.write_good(file_at(path), {"date": "2026-09-22", "top": [{"count": 1, "ref": "a"}]}, ctx, "src")

    assert ctx.corrections == []


def test_a_window_with_a_date_twice_writes_as_replace_window_does_and_corrects_to_the_last(tmp_path: Path) -> None:
    existing = b'{"date": "2026-09-20", "status": "ok", "n": 1}\n'
    rows: list[Row] = [{"date": "2026-09-20", "status": "ok", "n": 2}, {"date": "2026-09-20", "status": "ok", "n": 3}]
    theirs, ours = seed(tmp_path, existing)
    ctx = run_context(tmp_path)

    store.write_window(file_at(ours), rows, ctx, "usage")

    store.replace_window(file_at(theirs), rows, WHEN)
    assert ours.read_bytes() == theirs.read_bytes()
    assert ctx.corrections == [Correction("usage", "m.jsonl", "2026-09-20", {"n": 1}, {"n": 3})]


# --- backfills: rebuilt past values filled in, never a value changed ---


def captured(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


def test_a_fill_adds_the_fields_a_past_good_row_lacks_in_place_and_keeps_its_captured_at(
    tmp_path: Path, logged: Logged
) -> None:
    path = tmp_path / "m.jsonl"
    old = '{"date": "2026-09-21", "issues": {"open": 2}, "repo": {"stars": 1}, "captured_at": "2026-09-21T23:00:00Z"}'
    path.write_text(f"{old}\n" + '{"date": "2026-09-22", "issues": {"open": 3, "opened_total": 5}}\n', encoding="utf-8")
    ctx = run_context(tmp_path)

    store.fill(file_at(path), [{"date": "2026-09-21", "issues": {"opened_total": 4, "closed_total": 1}}], ctx)

    filled = (
        '{"date": "2026-09-21", "issues": {"open": 2, "opened_total": 4, "closed_total": 1}, "repo": {"stars": 1}, '
    )
    filled += '"captured_at": "2026-09-21T23:00:00Z"}\n'
    assert (
        path.read_text(encoding="utf-8")
        == filled + '{"date": "2026-09-22", "issues": {"open": 3, "opened_total": 5}}\n'
    )
    assert ctx.corrections == []  # a backfill, not a correction
    assert logged.messages == ["Backfilled m.jsonl: 1 row(s) filled, 0 added, 2026-09-21 to 2026-09-21"]


def test_a_field_a_fill_adds_to_a_row_comes_before_its_captured_at_which_stays_last(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    path.write_text('{"date": "2026-09-21", "repo": {"stars": 1}, "captured_at": "2026-09-21T23:00:00Z"}\n', "utf-8")

    store.fill(file_at(path), [{"date": "2026-09-21", "issues": {"opened_total": 4}}], run_context(tmp_path))

    added = '"issues": {"opened_total": 4}, "captured_at": "2026-09-21T23:00:00Z"}\n'
    assert path.read_text(encoding="utf-8") == '{"date": "2026-09-21", "repo": {"stars": 1}, ' + added


@pytest.mark.parametrize(
    "existing",
    [
        '{"date": "2026-09-21", "issues": {"opened_total": 9, "closed_total": 9}}\n',
        '{"date": "2026-09-21", "issues": {"opened_total": null, "closed_total": 0}}\n',
        '{"date": "2026-09-21", "issues": null}\n',
        '{"date": "2026-09-21", "issues": [1]}\n',
        '{"date": "2026-09-21", "status": "fetch_failed", "reason": "x"}\n',
        '{"date": "2026-09-21", "status": "partial", "issues": {}, "reason": "x"}\n',
    ],
    ids=["other values", "a null", "a null object", "not an object", "a failure row", "a partial row"],
)
def test_a_fill_never_changes_a_value_a_row_has_nor_a_failure_or_partial_row(
    tmp_path: Path, existing: str, logged: Logged
) -> None:
    """A field the row has, null included, is the row's; a failure or partial row is the reading of its night."""
    path = tmp_path / "m.jsonl"
    path.write_text(existing, encoding="utf-8")
    ctx = run_context(tmp_path)

    store.fill(file_at(path), [{"date": "2026-09-21", "issues": {"opened_total": 4, "closed_total": 1}}], ctx)

    assert path.read_text(encoding="utf-8") == existing
    assert (ctx.corrections, logged.messages) == ([], [])


def test_a_fill_adds_a_row_for_a_day_with_none_marked_backfilled_in_date_order(tmp_path: Path, logged: Logged) -> None:
    path = tmp_path / "m.jsonl"
    existing = 'not json\n{"date": "2026-09-20", "total": 3}\n{"date": "2026-09-22", "total": 5}\n'
    path.write_text(existing, encoding="utf-8")
    ctx = run_context(tmp_path)

    days = ("2026-09-21", "2026-09-17", "2026-09-18")
    store.fill(file_at(path), [{"date": day, "total": n, "answered": None} for n, day in enumerate(days)], ctx)

    stamp = captured(WHEN)
    added = [
        f'{{"date": "{day}", "total": {n}, "answered": null, "backfilled": true, "captured_at": "{stamp}"}}\n'
        for n, day in enumerate(days)
    ]
    lines = existing.splitlines(keepends=True)
    assert path.read_text(encoding="utf-8") == "".join([lines[0], added[1], added[2], lines[1], added[0], lines[2]])
    assert ctx.corrections == []
    assert logged.messages == ["Backfilled m.jsonl: 0 row(s) filled, 3 added, 2026-09-17 to 2026-09-21"]


def test_a_fill_with_nothing_missing_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """So a run after the fill does nothing to the file."""
    path = tmp_path / "m.jsonl"
    path.write_text('{"date": "2026-09-21", "total": 3}\n', encoding="utf-8")
    writes: list[Path] = []
    monkeypatch.setattr(jsonl, "write_text", lambda p, text: writes.append(p))

    store.fill(file_at(path), [{"date": "2026-09-21", "total": 4}], run_context(tmp_path))

    assert writes == []


def test_a_fill_of_a_missing_file_adds_its_rows(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"

    store.fill(file_at(path), [{"date": "2026-09-22", "total": 4}], run_context(tmp_path))

    assert store.rows(file_at(path)) == [
        {"date": "2026-09-22", "total": 4, "backfilled": True, "captured_at": captured(WHEN)}
    ]


@pytest.mark.parametrize(
    "row",
    [{"date": "2026-09-23", "n": 1}, {"date": "2026-09-24", "n": 1}, {"date": "2026-09-22", "status": "partial"}],
    ids=["the run date", "a later date", "not a good row"],
)
def test_a_fill_refuses_a_row_not_a_good_one_for_a_past_day(tmp_path: Path, row: Row) -> None:
    with pytest.raises(ValueError, match="fill"):
        store.fill(file_at(tmp_path / "m.jsonl"), [row], run_context(tmp_path))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-24", date(2026, 9, 24)),
        ("2026-02-30", None),
        ("2026-9-24", None),
        ("x", None),
        (20260924, None),
        (None, None),
    ],
)
def test_a_rows_date_is_a_real_day_written_yyyy_mm_dd(value: object, expected: date | None) -> None:
    """How health and the reports date a row; anything else is a line they skip."""
    assert store.parse_date(value) == expected


def test_write_text_keeps_the_line_ends_it_is_given_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text("old\n", encoding="utf-8")

    store.write_text(path, "one\r\ntwo \u00b7\n")

    assert path.read_bytes() == "one\r\ntwo \u00b7\n".encode()
    assert [p.name for p in tmp_path.iterdir()] == ["a.md"]


def test_a_fill_fills_a_null_in_a_backfilled_row_with_a_value_but_never_a_nights_null(
    tmp_path: Path, logged: Logged
) -> None:
    """A backfilled row's null is what a rebuild couldn't tell, not a reading: a later rebuild that can tell it fills
    it. A null a night wrote stays (a fill never changes a reading), and a null rebuilt again changes nothing."""
    path = tmp_path / "m.jsonl"
    rebuilt = '{"date": "2026-09-20", "total": 1, "comments": null, "recent": null, "backfilled": true}\n'
    night = '{"date": "2026-09-21", "total": 2, "comments": null, "recent": []}\n'
    path.write_text(rebuilt + night, encoding="utf-8")
    ctx = run_context(tmp_path)

    store.fill(
        file_at(path),
        [{"date": day, "total": 9, "comments": 4, "recent": None} for day in ("2026-09-20", "2026-09-21")],
        ctx,
    )

    filled = '{"date": "2026-09-20", "total": 1, "comments": 4, "recent": null, "backfilled": true}\n'
    assert path.read_text(encoding="utf-8") == filled + night
    assert ctx.corrections == []
    assert logged.messages == ["Backfilled m.jsonl: 1 row(s) filled, 0 added, 2026-09-20 to 2026-09-20"]


def test_a_fill_by_a_second_key_adds_and_fills_each_keys_row_on_its_own(tmp_path: Path, logged: Logged) -> None:
    """The forum's rows: one per topic a day, so a day's row for one topic is not the other's."""
    path = tmp_path / "m.jsonl"
    existing = (
        '{"date": "2026-09-21", "topic_id": 1, "replies": 3}\n{"date": "2026-09-22", "topic_id": 1, "replies": 4}\n'
    )
    path.write_text(existing, encoding="utf-8")
    ctx = run_context(tmp_path)

    rows = [{"date": "2026-09-21", "topic_id": t, "replies": 1, "views": None} for t in (1, 2)]
    store.fill(file_at(path), rows, ctx, store.SECOND_KEY)

    lines = existing.splitlines(keepends=True)
    filled = '{"date": "2026-09-21", "topic_id": 1, "replies": 3, "views": null}\n'
    added = '{"date": "2026-09-21", "topic_id": 2, "replies": 1, "views": null, "backfilled": true, '
    added += f'"captured_at": "{captured(WHEN)}"}}\n'
    assert path.read_text(encoding="utf-8") == filled + added + lines[1]
    assert logged.messages == ["Backfilled m.jsonl: 1 row(s) filled, 1 added, 2026-09-21 to 2026-09-21"]


@pytest.mark.parametrize(
    ("existing", "start", "where", "days"),
    [
        ('{"date": "2026-09-20", "n": 1}\n', date(2026, 9, 17), None, ["2026-09-17", "2026-09-18", "2026-09-19"]),
        ('{"date": "2026-09-19", "status": "fetch_failed"}\n', date(2026, 9, 18), None, ["2026-09-18"]),
        (
            '{"date": "2026-09-17", "backfilled": true}\n{"date": "2026-09-19"}\n',
            date(2026, 9, 17),
            None,
            ["2026-09-17", "2026-09-18"],
        ),
        ('{"date": "2026-09-20", "n": 1}\n', date(2026, 9, 20), None, []),
        ('{"date": "2026-09-20", "n": 1}\n', None, None, []),
        ('{"date": "2026-09-23", "n": 1}\n', date(2026, 9, 21), None, []),
        ("not a row\n", date(2026, 9, 21), None, []),
        (
            '{"date": "2026-09-18", "t": 1}\n{"date": "2026-09-20", "t": 2}\n',
            date(2026, 9, 18),
            {"t": 2},
            ["2026-09-18", "2026-09-19"],
        ),
    ],
    ids=[
        "back to the start",
        "a failure row is a night's",
        "a backfilled row is not the first",
        "the start is the first row",
        "no start",
        "only the run date's row",
        "no row",
        "the rows `where` keeps",
    ],
)
def test_the_days_to_rebuild_run_from_the_start_to_the_day_before_the_first_row_a_night_wrote(
    tmp_path: Path, existing: str, start: date | None, where: dict[str, Any] | None, days: list[str]
) -> None:
    """None without a row a night wrote before the run date: a new file gets its history the night after."""
    path = tmp_path / "m.jsonl"
    path.write_text(existing, encoding="utf-8")

    found = store.rebuild_days(file_at(path), start, run_context(tmp_path), where)

    assert [day.isoformat() for day in found] == days


@pytest.mark.parametrize(
    ("row", "lacks"),
    [
        (None, True),
        ({"date": "2026-09-21"}, True),
        ({"date": "2026-09-21", "n": None, "backfilled": True}, True),
        ({"date": "2026-09-21", "n": None}, False),
        ({"date": "2026-09-21", "n": 0, "backfilled": True}, False),
    ],
    ids=["no row", "no field", "a backfilled null", "a night's null", "a value"],
)
def test_a_row_lacks_a_field_a_fill_would_add(row: Row | None, lacks: bool) -> None:
    assert store.lacks(row, "n") is lacks


# --- recomputed history: a derived source's past rows under today's rules, each change a correction ---


def test_a_recompute_replaces_a_past_good_rows_values_in_place_and_records_the_correction(
    tmp_path: Path, logged: Logged
) -> None:
    """The row keeps its place, its field order and the fields the recompute doesn't give; captured_at is the run's."""
    path = tmp_path / "m.jsonl"
    old = '{"date": "2026-09-21", "n": 1, "kept": "x", "m": 2, "captured_at": "2026-09-21T23:00:00Z"}\n'
    later = '{"date": "2026-09-22", "n": 3, "m": 4}\n'
    path.write_text(old + later, encoding="utf-8")
    ctx = run_context(tmp_path)

    store.recompute(file_at(path), [{"date": "2026-09-21", "n": 5, "m": 2, "added": None}], ctx, "src")

    new = f'{{"date": "2026-09-21", "n": 5, "kept": "x", "m": 2, "added": null, "captured_at": "{captured(WHEN)}"}}\n'
    assert path.read_text(encoding="utf-8") == new + later
    assert ctx.corrections == [Correction("src", "m.jsonl", "2026-09-21", {"n": 1}, {"n": 5, "added": None})]
    assert logged.messages[-1] == "Recomputed m.jsonl: 1 corrected, 0 added, 0 failure row(s) replaced"


def test_a_recompute_leaves_a_row_with_the_same_values_and_writes_nothing_when_none_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logged: Logged
) -> None:
    """So a run after the recompute does nothing to the file; nested values in another key order are the same."""
    path = tmp_path / "m.jsonl"
    path.write_text('{"date": "2026-09-21", "n": {"a": 1, "b": 2}, "captured_at": "x"}\n', encoding="utf-8")
    writes: list[Path] = []
    monkeypatch.setattr(jsonl, "write_text", lambda p, text: writes.append(p))
    ctx = run_context(tmp_path)

    store.recompute(file_at(path), [{"date": "2026-09-21", "n": {"b": 2, "a": 1}}], ctx, "src")

    assert (writes, ctx.corrections, logged.messages) == ([], [], [])


@pytest.mark.parametrize(
    "existing",
    [
        '{"date": "2026-09-21", "status": "fetch_failed", "http_code": "502", "reason": "x"}\n',
        '{"date": "2026-09-21", "n": null, "status": "partial", "reason": "x"}\n',
    ],
    ids=["a failure row", "a partial row"],
)
def test_a_recompute_replaces_a_failure_or_partial_row_with_the_good_row_and_no_correction(
    tmp_path: Path, existing: str, logged: Logged
) -> None:
    """As a good row replaces a failure or partial row; it is the night's, so it isn't marked backfilled."""
    path = tmp_path / "m.jsonl"
    path.write_text(existing + '{"date": "2026-09-22", "n": 3}\n', encoding="utf-8")
    ctx = run_context(tmp_path)

    store.recompute(file_at(path), [{"date": "2026-09-21", "n": 2}], ctx, "src")

    replaced = f'{{"date": "2026-09-21", "n": 2, "captured_at": "{captured(WHEN)}"}}\n'
    assert path.read_text(encoding="utf-8") == replaced + '{"date": "2026-09-22", "n": 3}\n'
    assert ctx.corrections == []
    assert logged.messages == ["Recomputed m.jsonl: 0 corrected, 0 added, 1 failure row(s) replaced"]


def test_a_recompute_adds_a_row_for_a_day_with_none_marked_backfilled_in_date_order(
    tmp_path: Path, logged: Logged
) -> None:
    path = tmp_path / "m.jsonl"
    existing = 'not json\n{"date": "2026-09-20", "n": 3}\n{"date": "2026-09-22", "n": 5}\n'
    path.write_text(existing, encoding="utf-8")
    ctx = run_context(tmp_path)

    days = ("2026-09-21", "2026-09-18", "2026-09-20", "2026-09-22")
    store.recompute(file_at(path), [{"date": day, "n": n} for n, day in zip((4, 1, 3, 5), days)], ctx, "src")

    lines = existing.splitlines(keepends=True)
    stamp = captured(WHEN)
    added = [
        f'{{"date": "{day}", "n": {n}, "backfilled": true, "captured_at": "{stamp}"}}\n'
        for n, day in ((1, "2026-09-18"), (4, "2026-09-21"))
    ]
    assert path.read_text(encoding="utf-8") == "".join([lines[0], added[0], lines[1], added[1], lines[2]])
    assert ctx.corrections == []
    assert logged.messages == ["Recomputed m.jsonl: 0 corrected, 2 added, 0 failure row(s) replaced"]


@pytest.mark.parametrize(
    "row",
    [{"date": "2026-09-23", "n": 1}, {"date": "2026-09-24", "n": 1}, {"date": "2026-09-22", "status": "ok"}],
    ids=["the run date", "a later date", "a status"],
)
def test_a_recompute_refuses_a_row_not_a_good_one_for_a_past_day(tmp_path: Path, row: Row) -> None:
    """The run date's row is the night's reading, written by write_good."""
    with pytest.raises(ValueError, match="recompute"):
        store.recompute(file_at(tmp_path / "m.jsonl"), [row], run_context(tmp_path), "src")


@pytest.mark.parametrize(
    ("existing", "start", "days"),
    [
        (
            '{"date": "2026-09-20", "n": 1}\n',
            date(2026, 9, 19),
            ["2026-09-19", "2026-09-20", "2026-09-21", "2026-09-22"],
        ),
        ('{"date": "2026-09-22", "status": "fetch_failed"}\n', date(2026, 9, 22), ["2026-09-22"]),
        ('{"date": "2026-09-20", "backfilled": true}\n', date(2026, 9, 21), []),
        ('{"date": "2026-09-20", "n": 1}\n', None, []),
        ('{"date": "2026-09-23", "n": 1}\n', date(2026, 9, 21), []),
        ("not a row\n", date(2026, 9, 21), []),
    ],
    ids=[
        "back to the start",
        "a failure row is a night's",
        "only backfilled rows",
        "no start",
        "only the run date's row",
        "no row",
    ],
)
def test_the_days_to_recompute_run_from_the_start_to_the_day_before_the_run_date(
    tmp_path: Path, existing: str, start: date | None, days: list[str]
) -> None:
    """None without a row a night wrote before the run date: a new file gets its history the night after."""
    path = tmp_path / "m.jsonl"
    path.write_text(existing, encoding="utf-8")

    found = store.recompute_days(file_at(path), start, run_context(tmp_path))

    assert [day.isoformat() for day in found] == days
