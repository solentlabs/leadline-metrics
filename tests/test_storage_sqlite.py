"""The SQLite backend: the files of a run in normalized tables (docs/ARCHITECTURE.md, "Storage")."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from leadline.storage import StorageError, tables
from leadline.storage.sqlite import Sqlite

ROW = '{"date": "2026-09-21", "n": 1, "top_bots": [{"name": "0", "count": 14}, {"name": "1", "count": 2}]}\n'


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "leadline.db"


def query(db: Path, sql: str, *args: object) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute(sql, args).fetchall()


def test_the_database_and_its_tables_are_made_on_first_access(tmp_path: Path) -> None:
    db = tmp_path / "made" / "here" / "leadline.db"

    Sqlite(db).read_lines("a.jsonl")

    assert query(db, "SELECT version FROM schema_version") == [(tables.VERSION,)]
    names = {row[0] for row in query(db, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"schema_version", "file", "field", "line", "value", "raw_line"} <= names


def test_reading_a_file_never_written_makes_no_file_row(db: Path) -> None:
    Sqlite(db).read_lines("a.jsonl")

    assert query(db, "SELECT name FROM file") == []


def test_a_row_is_split_into_values_a_viewer_can_query(db: Path) -> None:
    Sqlite(db).write_lines("a.jsonl", [ROW])

    found = query(
        db,
        "SELECT f.path, v.num FROM value v JOIN field f ON f.id = v.field_id "
        "WHERE v.kind = 'int' AND f.path = 'top_bots[].count' ORDER BY v.ord, v.node",
    )
    assert found == [("top_bots[].count", 14), ("top_bots[].count", 2)]
    assert query(db, "SELECT date FROM line") == [("2026-09-21",)]


def test_the_reference_tables_hold_each_file_and_field_once(db: Path) -> None:
    backend = Sqlite(db)
    backend.write_lines("a.jsonl", [ROW])
    backend.write_lines("a.jsonl", [ROW, ROW.replace("09-21", "09-22")])
    backend.write_lines("b.jsonl", [ROW])

    assert query(db, "SELECT name FROM file ORDER BY name") == [("a.jsonl",), ("b.jsonl",)]
    paths = [str(row[0]) for row in query(db, "SELECT path FROM field ORDER BY path")]
    assert paths == sorted(set(paths))
    assert "top_bots[].name" in paths


def test_a_line_not_in_the_form_leadline_writes_is_kept_as_it_came(db: Path) -> None:
    lines = ['{"date":"2026-09-21","n":1}\n', "not json\n", '{"date": "café"}\n']
    Sqlite(db).write_lines("a.jsonl", lines)

    assert len(query(db, "SELECT seq FROM raw_line")) == 3
    assert query(db, "SELECT seq FROM line") == []
    assert Sqlite(db).read_lines("a.jsonl") == lines


def test_canonical_and_raw_lines_keep_their_places(db: Path) -> None:
    lines = [ROW, "not json\n", ROW.replace("09-21", "09-22")]
    Sqlite(db).write_lines("a.jsonl", lines)

    assert Sqlite(db).read_lines("a.jsonl") == lines


@pytest.mark.parametrize(
    "line",
    [
        '{"b": 1, "a": 2}\n',
        '{"i": 1, "f": 1.0, "e": 1e-07, "big": 12345678901234567890, "neg": -5}\n',
        '{"nan": NaN, "inf": Infinity, "neg_inf": -Infinity}\n',
        '{"t": true, "f": false, "n": null, "s": ""}\n',
        '{"a": [], "o": {}, "l": [[1, [2]], {"k": []}]}\n',
        '{"key.with.dots": {"b": 1}, "list[]": [1], "": 0}\n',
    ],
)
def test_a_row_is_rebuilt_exactly(db: Path, line: str) -> None:
    Sqlite(db).write_lines("a.jsonl", [line])

    assert Sqlite(db).read_lines("a.jsonl") == [line]
    assert query(db, "SELECT seq FROM raw_line") == []  # it was split, not kept whole


def test_a_row_added_at_the_end_deletes_nothing(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [ROW, ROW.replace("09-21", "09-22")]
    Sqlite(db).write_lines("a.jsonl", rows)
    statements: list[str] = []
    real = sqlite3.connect

    def traced(*args: object, **kwargs: object) -> sqlite3.Connection:
        conn: sqlite3.Connection = real(*args, **kwargs)  # type: ignore[call-overload]
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", traced)

    Sqlite(db).write_lines("a.jsonl", [*rows, ROW.replace("09-21", "09-23")])

    assert [s for s in statements if s.lstrip().upper().startswith("DELETE")] == []
    assert len(query(db, "SELECT seq FROM line")) == 3


def test_a_write_finds_what_changed_by_digest_and_reads_no_values(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [ROW, ROW.replace("09-21", "09-22")]
    Sqlite(db).write_lines("a.jsonl", rows)
    statements: list[str] = []
    real = sqlite3.connect

    def traced(*args: object, **kwargs: object) -> sqlite3.Connection:
        conn: sqlite3.Connection = real(*args, **kwargs)  # type: ignore[call-overload]
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", traced)

    Sqlite(db).write_lines("a.jsonl", [*rows, ROW.replace("09-21", "09-23")])

    reads = [s for s in statements if s.lstrip().upper().startswith("SELECT") and "FROM value" in s]
    assert reads == []
    assert query(db, "SELECT count(*) FROM line WHERE digest IS NOT NULL") == [(3,)]


def test_a_changed_row_rewrites_from_there_on(db: Path) -> None:
    backend = Sqlite(db)
    first, second = ROW, ROW.replace("09-21", "09-22")
    backend.write_lines("a.jsonl", [first, second])

    backend.write_lines("a.jsonl", [first, second.replace('"n": 1', '"n": 9')])

    assert backend.read_lines("a.jsonl") == [first, second.replace('"n": 1', '"n": 9')]
    assert query(db, "SELECT count(*) FROM line") == [(2,)]


def test_a_file_shortened_drops_its_rows(db: Path) -> None:
    backend = Sqlite(db)
    backend.write_lines("a.jsonl", [ROW, ROW.replace("09-21", "09-22")])

    backend.write_lines("a.jsonl", [ROW])

    assert query(db, "SELECT count(*) FROM line") == [(1,)]
    assert query(db, "SELECT count(*) FROM value WHERE seq = 1") == [(0,)]


def test_a_database_made_by_a_newer_leadline_stops_the_run(db: Path) -> None:
    Sqlite(db).read_lines("a.jsonl")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE schema_version SET version = ?", (tables.VERSION + 1,))

    with pytest.raises(StorageError, match="newer"):
        Sqlite(db).read_lines("a.jsonl")


def test_a_path_that_is_not_a_database_stops_the_run_with_its_name(tmp_path: Path) -> None:
    bad = tmp_path / "not-a-db"
    bad.mkdir()

    with pytest.raises(StorageError, match="not-a-db"):
        Sqlite(bad).read_lines("a.jsonl")


def test_an_older_database_is_migrated_to_the_version_this_leadline_reads(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Sqlite(db).write_lines("a.jsonl", [ROW])
    monkeypatch.setattr(tables, "VERSION", tables.VERSION + 1)
    monkeypatch.setattr(tables, "MIGRATIONS", {tables.VERSION - 1: ("CREATE TABLE added (x INTEGER)",)})

    assert Sqlite(db).read_lines("a.jsonl") == [ROW]

    assert query(db, "SELECT version FROM schema_version") == [(tables.VERSION,)]
    assert query(db, "SELECT count(*) FROM added") == [(0,)]


def test_a_database_that_leadline_did_not_make_is_left_alone(tmp_path: Path) -> None:
    foreign = tmp_path / "other.db"
    with closing(sqlite3.connect(foreign)) as conn, conn:
        conn.execute("CREATE TABLE notes (x TEXT)")

    with pytest.raises(StorageError, match="did not make"):
        Sqlite(foreign).write_lines("a.jsonl", [ROW])

    assert query(foreign, "SELECT name FROM sqlite_master") == [("notes",)]
