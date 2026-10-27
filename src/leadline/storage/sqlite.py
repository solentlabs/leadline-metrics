"""The SQLite backend: the files of a run in the normalized tables of one database file (`storage.tables`).

It uses the standard library's `sqlite3`. Each operation opens the database, makes or migrates its tables when they
are not at `tables.VERSION`, and commits or rolls back as one transaction.
"""

from __future__ import annotations

import itertools
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterable

from leadline.storage import StorageError, check_name, tables

# What a file's lines from a place on are dropped by: its values before its lines, which they refer to.
_DROP_FROM = (
    "DELETE FROM value WHERE file_id = ? AND seq >= ?",
    "DELETE FROM line WHERE file_id = ? AND seq >= ?",
    "DELETE FROM raw_line WHERE file_id = ? AND seq >= ?",
)


class Sqlite:
    """The files in one SQLite database."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read_lines(self, file: str) -> list[str]:
        check_name(file)
        with self._open() as conn:
            found = conn.execute("SELECT id FROM file WHERE name = ?", (file,)).fetchone()
            return self._lines(conn, found[0]) if found else []

    def write_lines(self, file: str, lines: Iterable[str]) -> None:
        check_name(file)
        new = list(lines)  # a source of lines that fails does so before the database is touched
        with self._open() as conn, conn:
            conn.execute("INSERT OR IGNORE INTO file (name) VALUES (?)", (file,))
            file_id = conn.execute("SELECT id FROM file WHERE name = ?", (file,)).fetchone()[0]
            old = self._lines(conn, file_id)
            same = sum(1 for _ in itertools.takewhile(lambda pair: pair[0] == pair[1], zip(old, new)))
            if same < len(old):
                for statement in _DROP_FROM:
                    conn.execute(statement, (file_id, same))
            fields: dict[str, int] = {}
            for seq, line in enumerate(new[same:], start=same):
                self._insert(conn, file_id, seq, line, fields)

    def files(self) -> list[str]:
        with self._open() as conn:
            return [name for (name,) in conn.execute("SELECT name FROM file ORDER BY name")]

    def _insert(self, conn: sqlite3.Connection, file_id: int, seq: int, line: str, fields: dict[str, int]) -> None:
        nodes = tables.shred(line)
        if nodes is None:
            data = line.encode("utf-8", "surrogateescape")
            conn.execute("INSERT INTO raw_line (file_id, seq, data) VALUES (?, ?, ?)", (file_id, seq, data))
            return
        date = next((n.text for n in nodes if n.parent is None and n.key == "date" and n.kind == "text"), None)
        conn.execute("INSERT INTO line (file_id, seq, date) VALUES (?, ?, ?)", (file_id, seq, date))
        for n in nodes:
            if n.path not in fields:
                conn.execute("INSERT OR IGNORE INTO field (path) VALUES (?)", (n.path,))
                fields[n.path] = conn.execute("SELECT id FROM field WHERE path = ?", (n.path,)).fetchone()[0]
            conn.execute(
                "INSERT INTO value (file_id, seq, node, parent, ord, key, field_id, kind, num, text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (file_id, seq, n.node, n.parent, n.ord, n.key, fields[n.path], n.kind, n.num, n.text),
            )

    def _lines(self, conn: sqlite3.Connection, file_id: int) -> list[str]:
        """The file's lines in order: each split row rebuilt, each kept line as it came."""
        found: dict[int, str] = {}
        for seq, data in conn.execute("SELECT seq, data FROM raw_line WHERE file_id = ?", (file_id,)):
            found[seq] = bytes(data).decode("utf-8", "surrogateescape")
        grouped = itertools.groupby(
            conn.execute(
                "SELECT seq, node, parent, ord, key, path, kind, num, text FROM value "
                "JOIN field ON field.id = value.field_id WHERE file_id = ? ORDER BY seq, node",
                (file_id,),
            ),
            key=lambda row: row[0],
        )
        values = {seq: [tables.Node(*row[1:]) for row in rows] for seq, rows in grouped}
        for (seq,) in conn.execute("SELECT seq FROM line WHERE file_id = ?", (file_id,)):
            found[seq] = tables.rebuild(values.get(seq, []))
        return [found[seq] for seq in sorted(found)]

    def _open(self) -> closing[sqlite3.Connection]:
        """The database, open, with its tables at `tables.VERSION`; closed by the `with`."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path))
        except (OSError, sqlite3.Error) as e:
            raise StorageError(f"cannot open the database {self.path}: {e}") from e
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                _prepare(conn, self.path)
        except sqlite3.Error as e:
            conn.close()
            raise StorageError(f"cannot use the database {self.path}: {e}") from e
        except BaseException:
            conn.close()
            raise
        return closing(conn)


def _prepare(conn: sqlite3.Connection, path: Path) -> None:
    """Make the tables in an empty database, migrate an older one, and stop at one this leadline does not know."""
    existing = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not existing:
        for statement in tables.DDL:
            conn.execute(statement)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (tables.VERSION,))
        return
    if "schema_version" not in existing:
        raise StorageError(f"{path} is a database that leadline did not make")
    (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
    if version > tables.VERSION:
        raise StorageError(
            f"{path} was made by a newer leadline (its structure is version {version}, this one reads "
            f"{tables.VERSION}); update leadline"
        )
    for step in range(version, tables.VERSION):
        for statement in tables.MIGRATIONS[step]:
            conn.execute(statement)
        conn.execute("UPDATE schema_version SET version = ?", (step + 1,))
