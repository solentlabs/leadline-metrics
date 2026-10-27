"""The tables a database backend keeps the files in, and how a line becomes values and back.

Pure: no database, so every SQL backend (SQLite, Cloudflare D1) shares it (docs/ARCHITECTURE.md, "Storage"). A
line in the form leadline writes, `json.dumps` of an object and a line end, is split into one `Node` for each value
in it, and `rebuild` gives back the same line. `shred` returns None for any other line, which a backend keeps whole.
"""

from __future__ import annotations

import json
import math
from typing import Any, Iterable, NamedTuple

VERSION = 1  # the version of the structure below

DDL = (
    "CREATE TABLE schema_version (version INTEGER NOT NULL)",
    "CREATE TABLE file (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)",
    "CREATE TABLE field (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE)",
    "CREATE TABLE line ("
    "file_id INTEGER NOT NULL REFERENCES file(id), seq INTEGER NOT NULL, date TEXT, "
    "PRIMARY KEY (file_id, seq))",
    "CREATE TABLE raw_line ("
    "file_id INTEGER NOT NULL REFERENCES file(id), seq INTEGER NOT NULL, data BLOB NOT NULL, "
    "PRIMARY KEY (file_id, seq))",
    "CREATE TABLE value ("
    "file_id INTEGER NOT NULL, seq INTEGER NOT NULL, node INTEGER NOT NULL, parent INTEGER, ord INTEGER NOT NULL, "
    "key TEXT, field_id INTEGER NOT NULL REFERENCES field(id), kind TEXT NOT NULL, num, text, "
    "PRIMARY KEY (file_id, seq, node), FOREIGN KEY (file_id, seq) REFERENCES line(file_id, seq))",
)

# The statements that take a database from version n to n + 1, for each n below VERSION.
MIGRATIONS: dict[int, tuple[str, ...]] = {}

_INT_LIMIT = 2**63  # SQLite's INTEGER is 64 bits; a bigger int keeps its exact text only


class Node(NamedTuple):
    """One value of a row: `node` numbers the row's values in order (a parent before its children); `parent` is the
    node it is in (None for a member of the row itself); `ord` its place there, and `key` its name in an object."""

    node: int
    parent: int | None
    ord: int
    key: str | None
    path: str  # the field it is: `issues.open`, `top_referrers[].ref`; a reference for queries, not for rebuilding
    kind: str  # object, list, text, int, float, bool or null
    num: Any  # the number, bool as 1 or 0, for a query; None where it has none that SQLite keeps exactly
    text: str | None  # a text's text; a number's exact JSON text


def shred(line: str) -> list[Node] | None:
    """The values of `line`, or None where it is not in the form leadline writes (or holds a text a database cannot
    keep), so a backend keeps it as it came."""
    if not line.endswith("\n"):
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if not isinstance(row, dict) or json.dumps(row) + "\n" != line:
        return None
    nodes: list[Node] = []
    for ord_, (key, value) in enumerate(row.items()):
        if not _add(nodes, value, None, ord_, key, key):
            return None
    return nodes


def _add(nodes: list[Node], value: Any, parent: int | None, ord_: int, key: str | None, path: str) -> bool:
    """Append `value` and what is in it to `nodes`; False where a text in it cannot be kept."""
    if key is not None and not _keeps(key):
        return False
    here = len(nodes)
    if isinstance(value, dict):
        nodes.append(Node(here, parent, ord_, key, path, "object", None, None))
        return all(_add(nodes, v, here, i, k, f"{path}.{k}") for i, (k, v) in enumerate(value.items()))
    if isinstance(value, list):
        nodes.append(Node(here, parent, ord_, key, path, "list", None, None))
        return all(_add(nodes, v, here, i, None, f"{path}[]") for i, v in enumerate(value))
    if isinstance(value, str):
        if not _keeps(value):
            return False
        nodes.append(Node(here, parent, ord_, key, path, "text", None, value))
    elif isinstance(value, bool):
        nodes.append(Node(here, parent, ord_, key, path, "bool", int(value), None))
    elif value is None:
        nodes.append(Node(here, parent, ord_, key, path, "null", None, None))
    elif isinstance(value, int):
        num = value if abs(value) < _INT_LIMIT else None
        nodes.append(Node(here, parent, ord_, key, path, "int", num, json.dumps(value)))
    else:
        num = value if math.isfinite(value) else None
        nodes.append(Node(here, parent, ord_, key, path, "float", num, json.dumps(value)))
    return True


def _keeps(text: str) -> bool:
    """Whether a database keeps `text` as it is: valid UTF-8 and no NUL."""
    if "\x00" in text:
        return False
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def rebuild(nodes: Iterable[Node]) -> str:
    """The line the `nodes` of one row were split from, in the order of their `node`."""
    row: dict[str, Any] = {}
    made: dict[int, Any] = {}
    for n in sorted(nodes, key=lambda n: n.node):
        value = _value(n)
        made[n.node] = value
        into = row if n.parent is None else made[n.parent]
        if isinstance(into, dict):
            into[n.key] = value  # type: ignore[index]  # a member of an object always has its key
        else:
            into.append(value)
    return json.dumps(row) + "\n"


def _value(n: Node) -> Any:
    if n.kind == "object":
        return {}
    if n.kind == "list":
        return []
    if n.kind == "text":
        return n.text
    if n.kind == "bool":
        return bool(n.num)
    if n.kind == "int":
        return int(n.text or "")
    if n.kind == "float":
        return float(n.text or "")
    return None
