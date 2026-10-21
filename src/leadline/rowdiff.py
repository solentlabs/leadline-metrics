"""Compare the row files in two folders, ignoring `captured_at`.

Two files match when their text is identical once every `captured_at` value
is blanked: same rows, same keys in the same order, same values, same
serialization, same line order. That is the compatibility the port promises
(docs/ARCHITECTURE.md, "What it does").

When they differ, rows are matched by their upsert key (`date`, plus
`store.SECOND_KEY`, the forum's `topic_id`, when the row has one) and each
difference is one line: a row only in one folder, a field that differs, key
order, serialization, or, when every row matches, line order and layout. Lines with no key (unparseable, or no
`date`) are matched by their text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from leadline.store import SECOND_KEY

_CAPTURED_AT = re.compile(r'("captured_at"\s*:\s*)"[^"]*"')


def _masked(text: str) -> str:
    return _CAPTURED_AT.sub(r'\1""', text)


def _dump(value: Any) -> str:
    return json.dumps(value)


def _label(line: str) -> str:
    """The row's key as a report label: `date=D` (and `topic_id=T`), or the line's text."""
    try:
        row = json.loads(line)
    except ValueError:
        row = None
    if isinstance(row, dict) and "date" in row:
        label = f"date={row['date']}"
        if SECOND_KEY in row:
            label += f" {SECOND_KEY}={row[SECOND_KEY]}"
        return label
    return f"line {line!r}"


def _rows(text: str) -> dict[str, list[str]]:
    """The non-blank lines of a masked file, grouped by label in file order."""
    grouped: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.strip():
            grouped.setdefault(_label(line), []).append(line)
    return grouped


def _row_differences(a: str, b: str) -> list[str]:
    """How two masked rows with the same key differ, one entry per difference.

    Only rows labeled by their key get here, so both parse as objects.
    """
    row_a, row_b = json.loads(a), json.loads(b)
    found = []
    for field in list(row_a) + [f for f in row_b if f not in row_a]:
        if field not in row_b:
            found.append(f"{field}: only in A")
        elif field not in row_a:
            found.append(f"{field}: only in B")
        elif _dump(row_a[field]) != _dump(row_b[field]):
            found.append(f"{field}: {_dump(row_a[field])} != {_dump(row_b[field])}")
    if not found and list(row_a) != list(row_b):
        found.append(f"key order: {_dump(list(row_a))} != {_dump(list(row_b))}")
    if not found:
        found.append("same values, different serialization")
    return found


def _file_differences(name: str, a: Path, b: Path) -> list[str]:
    text_a, text_b = (_masked(p.read_text(encoding="utf-8", errors="surrogateescape")) for p in (a, b))
    if text_a == text_b:
        return []
    rows_a, rows_b = _rows(text_a), _rows(text_b)
    found = []
    for label in sorted(set(rows_a) | set(rows_b)):
        lines_a, lines_b = rows_a.get(label, []), rows_b.get(label, [])
        where = f"{name} {label}"
        if not lines_b:
            found.append(f"{where}: only in A")
        elif not lines_a:
            found.append(f"{where}: only in B")
        elif len(lines_a) != len(lines_b):
            found.append(f"{where}: {len(lines_a)} rows in A, {len(lines_b)} in B")
        else:
            for line_a, line_b in zip(lines_a, lines_b):
                if line_a != line_b:
                    found += [f"{where}: {d}" for d in _row_differences(line_a, line_b)]
    return found or [f"{name}: same rows, different line order or layout"]


def row_files(folder: Path) -> list[str]:
    """The names of the row files (`*.jsonl`) directly in `folder`."""
    return sorted(p.name for p in folder.glob("*.jsonl"))


def differences(a: Path, b: Path) -> dict[str, list[str]]:
    """Each row file that differs between folders `a` and `b`, in name order, with its differences."""
    files_a, files_b = row_files(a), row_files(b)
    found = {}
    for name in sorted(set(files_a) | set(files_b)):
        if name not in files_b:
            lines = [f"{name}: only in A"]
        elif name not in files_a:
            lines = [f"{name}: only in B"]
        else:
            lines = _file_differences(name, a / name, b / name)
        if lines:
            found[name] = lines
    return found


def compare(a: Path, b: Path) -> list[str]:
    """Every difference between the row files in folders `a` and `b`; empty when they match."""
    return [line for lines in differences(a, b).values() for line in lines]
