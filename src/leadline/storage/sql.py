"""What every SQL backend shares: the queries, and how a file's lines go to the tables and come back.

A backend (SQLite, Cloudflare D1) says only how to run statements: `_run` executes a list in order and returns what
each selected. The rest (the tables of `storage.tables`, the digest that finds what a write changed, the multi-row
inserts that keep a statement under a database's limit on bound values) is here, once.
"""

from __future__ import annotations

import itertools
from typing import Any, Iterable, List, Sequence, Tuple

from leadline.storage import check_name, tables

Statement = Tuple[str, Sequence[Any]]  # SQL and its bound values
Rows = List[Tuple[Any, ...]]

INSERT_ROWS = 9  # rows in one INSERT: ten bound values a row, and D1 allows 100 in a statement
IN_VALUES = 90  # values in one IN (...)

_DIGESTS = (
    "SELECT digest FROM (SELECT seq, digest FROM line WHERE file_id = (SELECT id FROM file WHERE name = ?) "
    "UNION ALL SELECT seq, digest FROM raw_line WHERE file_id = (SELECT id FROM file WHERE name = ?)) ORDER BY seq"
)
_READ = (
    "SELECT seq, data FROM raw_line WHERE file_id = (SELECT id FROM file WHERE name = ?)",
    "SELECT seq FROM line WHERE file_id = (SELECT id FROM file WHERE name = ?)",
    "SELECT seq, node, parent, ord, key, kind, num, text FROM value "
    "WHERE file_id = (SELECT id FROM file WHERE name = ?) ORDER BY seq, node",
)
_DROP_FROM = (
    "DELETE FROM value WHERE file_id = ? AND seq >= ?",
    "DELETE FROM line WHERE file_id = ? AND seq >= ?",
    "DELETE FROM raw_line WHERE file_id = ? AND seq >= ?",
)
_VALUE = "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"


class SqlBackend:
    """The files in the tables of `storage.tables`; a subclass runs the statements."""

    cached = False  # whether a file's lines are kept after they are read or written (the run is the only writer)

    def __init__(self) -> None:
        self._lines_of: dict[str, list[str]] = {}
        self._field_ids: dict[str, int] = {}

    def _run(self, statements: Sequence[Statement]) -> list[Rows]:
        """Execute the statements in order, as one unit where the database can; each one's rows (none for a write)."""
        raise NotImplementedError

    def _to_stored(self, data: bytes) -> Any:
        """A raw line's bytes as the database keeps them."""
        return data

    def _from_stored(self, value: Any) -> bytes:
        return bytes(value)

    def read_lines(self, file: str) -> list[str]:
        check_name(file)
        if self.cached and file in self._lines_of:
            return list(self._lines_of[file])
        lines = self._read(file)
        if self.cached:
            self._lines_of[file] = lines
        return list(lines)

    def write_lines(self, file: str, lines: Iterable[str]) -> None:
        check_name(file)
        new = list(lines)  # a source of lines that fails does so before the database is touched
        digests = [tables.digest(line) for line in new]
        old = [tables.digest(line) for line in self._lines_of[file]] if file in self._lines_of else None
        if old is None:
            (found,) = self._run([(_DIGESTS, (file, file))])
            old = [row[0] for row in found]
        same = sum(1 for _ in itertools.takewhile(lambda pair: pair[0] == pair[1], zip(old, digests)))
        prepared = [(seq, new[seq], digests[seq], tables.shred(new[seq])) for seq in range(same, len(new))]
        file_id = self._prepare(file, [n.path for _, _, _, nodes in prepared for n in nodes or ()])
        statements: list[Statement] = []
        if same < len(old):
            statements += [(statement, (file_id, same)) for statement in _DROP_FROM]
        statements += self._inserts(file_id, prepared)
        if statements:
            self._run(statements)
        if self.cached:
            self._lines_of[file] = new

    def files(self) -> list[str]:
        (found,) = self._run([("SELECT name FROM file ORDER BY name", ())])
        return [name for (name,) in found]

    def _read(self, file: str) -> list[str]:
        raw, plain, values = self._run([(sql, (file,)) for sql in _READ])
        found = {seq: self._from_stored(data).decode("utf-8", "surrogateescape") for seq, data in raw}
        grouped = itertools.groupby(values, key=lambda row: row[0])
        nodes = {
            seq: [tables.Node(r[1], r[2], r[3], r[4], "", r[5], r[6], r[7]) for r in rows] for seq, rows in grouped
        }
        for (seq,) in plain:
            found[seq] = tables.rebuild(nodes.get(seq, []))
        return [found[seq] for seq in sorted(found)]

    def _prepare(self, file: str, paths: Iterable[str]) -> int:
        """Make the file's row and the field rows the new lines need, and return the file's id."""
        wanted = sorted(set(paths) - set(self._field_ids))
        statements: list[Statement] = [("INSERT OR IGNORE INTO file (name) VALUES (?)", (file,))]
        for chunk in _chunks(wanted, INSERT_ROWS * 10):
            marks = ", ".join("(?)" for _ in chunk)
            sql = f"INSERT OR IGNORE INTO field (path) VALUES {marks}"  # noqa: S608 (placeholders only)
            statements.append((sql, tuple(chunk)))
        statements.append(("SELECT id FROM file WHERE name = ?", (file,)))
        for chunk in _chunks(wanted, IN_VALUES):
            marks = ", ".join("?" for _ in chunk)
            sql = f"SELECT id, path FROM field WHERE path IN ({marks})"  # noqa: S608 (placeholders only)
            statements.append((sql, tuple(chunk)))
        results = self._run(statements)
        file_id = next(
            rows[0][0] for statement, rows in zip(statements, results) if statement[0].startswith("SELECT id FROM file")
        )
        for statement, rows in zip(statements, results):
            if statement[0].startswith("SELECT id, path"):
                self._field_ids.update({path: id_ for id_, path in rows})
        return int(file_id)

    def _inserts(self, file_id: int, prepared: list[tuple[int, str, str, list[tables.Node] | None]]) -> list[Statement]:
        statements: list[Statement] = []
        for seq, line, digest, nodes in prepared:
            if nodes is None:
                data = self._to_stored(line.encode("utf-8", "surrogateescape"))
                statements.append(
                    (
                        "INSERT INTO raw_line (file_id, seq, data, digest) VALUES (?, ?, ?, ?)",
                        (file_id, seq, data, digest),
                    )
                )
                continue
            date = next((n.text for n in nodes if n.parent is None and n.key == "date" and n.kind == "text"), None)
            statements.append(
                ("INSERT INTO line (file_id, seq, date, digest) VALUES (?, ?, ?, ?)", (file_id, seq, date, digest))
            )
            for chunk in _chunks(nodes, INSERT_ROWS):
                params = [
                    v
                    for n in chunk
                    for v in (
                        file_id,
                        seq,
                        n.node,
                        n.parent,
                        n.ord,
                        n.key,
                        self._field_ids[n.path],
                        n.kind,
                        n.num,
                        n.text,
                    )
                ]
                marks = ", ".join(_VALUE for _ in chunk)
                sql = (
                    "INSERT INTO value (file_id, seq, node, parent, ord, key, field_id, kind, num, text) "  # noqa: S608 (placeholders only)
                    f"VALUES {marks}"
                )
                statements.append((sql, params))
        return statements


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
