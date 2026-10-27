"""The SQLite backend: the files of a run in the normalized tables of one database file (`storage.tables`).

It uses the standard library's `sqlite3`. Each call opens the database, makes or migrates its tables when they are not
at `tables.VERSION`, and runs its statements as one transaction.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Sequence

from leadline.storage import StorageError
from leadline.storage.sql import Rows, SqlBackend, Statement, schema_statements


class Sqlite(SqlBackend):
    """The files in one SQLite database."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def _run(self, statements: Sequence[Statement]) -> list[Rows]:
        with self._open() as conn, conn:
            return [conn.execute(sql, params).fetchall() for sql, params in statements]

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
    found = conn.execute("SELECT version FROM schema_version").fetchone() if "schema_version" in existing else None
    for sql, params in schema_statements(existing, found[0] if found else None, str(path)):
        conn.execute(sql, params)
