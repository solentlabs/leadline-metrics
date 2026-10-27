"""JSONL output files, written exactly as collect-metrics.sh writes them.

The script has two write paths, and each is reproduced here byte for byte
(tests/test_store.py runs the script's own code as the oracle), except that
no line in the file makes a write fail (docs/ARCHITECTURE_DECISIONS.md):

- `upsert`: every source but cloudflare_analytics_engine. One row per key,
  existing lines kept as they are, the new row appended.
- `replace_window`: cloudflare_analytics_engine. A window of days rewritten,
  the file re-sorted.

Neither reads the clock: each stamps `captured_at` with the `now` it is given,
the run's `ctx.now` (docs/CORE.md, "Store"). A source writes through
neither directly, but through the functions that keep the decided rules:
`write_good` (an upsert) and `write_window` (a window replace) record each
correction they make on the context; `write_failure`, and `write_window` for a
failure row in its window, write a failure row only where no good or partial
row holds its key; `write_partial` writes a reading
some of whose parts failed, keeping the stored values of those parts;
`write_merged` writes a reading a source merged with the `stored` row itself;
`fill` adds past values a source rebuilt where a row lacks them, changing
none; and `recompute` writes a derived source's past rows, recomputed under
today's rules, recording each correction it makes.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, Iterator, Mapping, NamedTuple, Sequence

from leadline import config
from leadline.storage import File, jsonl

if TYPE_CHECKING:
    from leadline.context import Context

log = logging.getLogger("leadline")

Row = Dict[str, Any]

# The one key a row can have besides `date`: the forum's, one row per topic a day. The forum source
# writes by it (`second_key=SECOND_KEY`), and rowdiff matches rows by it.
SECOND_KEY = "topic_id"

# The status of a partial row: a reading that kept its good parts where others failed, and whose
# missing parts no run has got yet (write_partial).
PARTIAL = "partial"


def _captured_at(now: datetime) -> str:
    """`now` as `captured_at` gives it: local time with its UTC offset, to the second."""
    if now.utcoffset() is None:
        raise ValueError(f"captured_at needs a time with its UTC offset, not {now.isoformat()}")
    return now.isoformat(timespec="seconds")


def _loads(line: str) -> Any:
    """A line parsed as JSON. A byte order mark before it is ignored, and a byte that is not valid UTF-8 reads as
    U+FFFD, so no value carries an escape that could not be written as text."""
    return json.loads(line.encode("utf-8", "surrogateescape").decode("utf-8", "replace").lstrip("\ufeff"))


def write_text(path: Path, text: str) -> None:
    """Replace the file atomically with `text`, in UTF-8, its line ends as given.

    Every file leadline writes, row files and reports, is written by this one routine (`storage.jsonl.write_text`).
    """
    jsonl.write_text(path, text)


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_date(value: Any) -> date | None:
    """A row's `date` as a date: a `YYYY-MM-DD` string naming a real day; else None, a line readers skip."""
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def rows(file: File) -> list[Row]:
    """Every line of the file that parses as a JSON object, in file order; a missing file has none.

    A source that reads its rows as the script does, line by line, reads them here.
    """
    return [row for row in _parsed(file) if isinstance(row, dict)]


def rows_by_date(file: File) -> dict[str, Row]:
    """The file's rows keyed by `date`, the last line for a date winning.

    Lines that aren't a row (they don't parse, aren't an object or have no string `date`) are left
    out; a missing file has none. A source reads its rows here to decide what to write.
    """
    return {row["date"]: row for row in rows(file) if isinstance(row.get("date"), str)}


def is_good(row: Row) -> bool:
    """A good row: its `status` is absent or `ok`; any other is a failure row (docs/ARCHITECTURE.md, "Terms")."""
    return row.get("status") in (None, "ok")


def value(row: Any, field: str) -> Any:
    """The value at `field` in `row`, a dotted path (`issues.open`); None where there is none."""
    found = row
    for key in field.split("."):
        if not isinstance(found, dict):
            return None
        found = found.get(key)
    return found


class Dated(NamedTuple):
    """A file's rows as reports and trends read them."""

    good: list[Row]  # the good rows, one a date (its last line), oldest first
    newest: str | None  # the newest date with a row, good or not


def dated_rows(file: File, where: Mapping[str, Any] | None = None, hide: Sequence[config.Hide] = ()) -> Dated:
    """The rows of the file that `where` keeps (each dotted field equal to its value), by date: a line whose `date`
    isn't a real `YYYY-MM-DD` day is skipped, and the last line for a date wins. The good rows come without the items
    `hide` names (copies: the file and the rows read are as they were)."""
    by_date: dict[str, Row] = {}
    for row in rows(file):
        if parse_date(row.get("date")) and all(value(row, k) == v for k, v in (where or {}).items()):
            by_date[row["date"]] = row
    good = [by_date[day] for day in sorted(by_date) if is_good(by_date[day])]
    for rule in hide:
        good = [_without(row, rule.field.split("."), rule) for row in good]
    return Dated(good, max(by_date, default=None))


def _without(row: Row, path: Sequence[str], rule: config.Hide) -> Row:
    """`row` as a copy without the items of the list at `path` that `rule` hides; the row itself where it has no list
    there."""
    found = row.get(path[0])
    if len(path) > 1:
        return {**row, path[0]: _without(found, path[1:], rule)} if isinstance(found, dict) else row
    if not isinstance(found, list):
        return row
    kept = [
        item
        for item in found
        if not (
            isinstance(item, dict)
            and isinstance(item.get(rule.key), str)
            and config.untagged(item[rule.key]) in rule.values
        )
    ]
    return {**row, path[0]: kept}


def write_failure(file: File, row: Row, now: datetime, second_key: str | None = None) -> bool:
    """Upsert the failure row `row`, unless a good or partial row holds its key; returns whether it wrote.

    A failure never overwrites or deletes the values a run got (docs/ARCHITECTURE_DECISIONS.md): where
    a line for the key (its `date`, and `second_key` when given) is a good or partial row, which the
    upsert would drop, nothing is written and a WARN line says so. An older failure row is replaced.
    Every source writes its failure rows here, so the rule is kept in one place.
    """
    if is_good(row):
        raise ValueError(f"not a failure row: status is {row.get('status')!r}")
    if _held(file, row, second_key):
        return False
    upsert(file, row, now, second_key)
    return True


def _held(file: File, failure: Row, second_key: str | None = None) -> bool:
    """Whether a good or partial row holds the failure row's key, which the failure must then leave; says so."""
    key = _key(failure, second_key)
    stored = _stored(file, key)
    if stored is None:
        return False
    held = "good" if is_good(stored) else PARTIAL
    log.info("WARN: kept the %s row for %s in %s; %s not recorded", held, _describe(key), file.name, failure["status"])
    return True


def write_partial(
    file: File,
    row: Row,
    failed: Mapping[str, Sequence[str]],
    ctx: Context,
    source: str,
    second_key: str | None = None,
) -> None:
    """Write `row`, a reading some of whose parts failed, keeping the stored values of those parts.

    `row` has the source's fields in its order, each failed part null (never 0), and no `status`, or
    `ok` where the source's good rows carry one, which a partial row's status replaces in place.
    `failed` maps each failure, as the row's `reason` names it, to the fields of the part it left null.
    A run writes what it got and never erases what it didn't (docs/ARCHITECTURE_DECISIONS.md): each
    failed part is filled from the stored good or partial row for the key where that row holds it
    (every field present, and not null in a partial row), and logged. Where every part is filled, the
    row is good and is written by `write_good`, corrections recorded; otherwise it gets `status`
    PARTIAL and a `reason` naming each part still missing.
    """
    if not failed:
        raise ValueError("a partial row names what failed")
    if row.get("status", "ok") != "ok":
        raise ValueError(f"a partial row has no status of its own but ok, not {row['status']!r}")
    unset = [field for fields in failed.values() for field in fields if field not in row or row[field] is not None]
    if unset:
        raise ValueError(f"a failed part's fields are in the row and null; not {', '.join(unset)}")
    held = stored(file, row, second_key)
    merged = dict(row)
    kept: list[str] = []  # the fields filled from the stored row
    missing: list[str] = []  # the failures whose parts no run has got
    for reason, fields in failed.items():
        if held is not None and all(_knows(held, field) for field in fields):
            merged.update((field, held[field]) for field in fields)
            kept += fields
        else:
            missing.append(reason)
    if kept:
        which = ", ".join(kept)
        where = _describe(_key(row, second_key))
        log.info("Kept the stored %s for %s in %s, which this run failed to get", which, where, file.name)
    write_merged(file, merged, missing, ctx, source, second_key)


def stored(file: File, row: Row, second_key: str | None = None) -> Row | None:
    """The last good or partial row in the file for `row`'s key (its `date`, and `second_key`), or None.

    What a source that merges inside a part itself reads, as write_partial reads it for whole parts.
    """
    return _stored(file, _key(row, second_key))


def write_merged(
    file: File,
    row: Row,
    missing: Sequence[str],
    ctx: Context,
    source: str,
    second_key: str | None = None,
) -> None:
    """Write `row`, already merged with the stored row (`stored`), whose `missing` failures no run has got.

    With nothing missing the row is good, written by `write_good`, corrections recorded; otherwise it
    gets `status` PARTIAL (where `ok` was, when the row carries one) and a `reason` joining `missing`
    with "; ", after its fields. write_partial ends here, and a source whose merge is inside a part (a
    list of people) writes here after merging it itself.
    """
    if row.get("status", "ok") != "ok":
        raise ValueError(f"a merged row has no status of its own but ok, not {row['status']!r}")
    if missing:
        upsert(file, {**row, "status": PARTIAL, "reason": "; ".join(missing)}, ctx.now, second_key)
    else:
        write_good(file, row, ctx, source, second_key)


def _has_values(row: Any) -> bool:
    """A good or partial row: one that holds values a run got, which no failure may replace."""
    return isinstance(row, dict) and (is_good(row) or row.get("status") == PARTIAL)


def _stored(file: File, key: Row) -> Row | None:
    """The last good or partial row for `key` in the file, or None."""
    rows = [existing for existing in _parsed(file) if _matches(existing, key) and _has_values(existing)]
    return rows[-1] if rows else None


def _knows(stored: Row, field: str) -> bool:
    """Whether the stored row knows `field`: a good row's value, null included, or a partial row's non-null one."""
    return field in stored and (is_good(stored) or stored[field] is not None)


def _parsed(file: File) -> Iterator[Any]:
    """Each line of the file that parses as JSON, parsed."""
    for line in file.read_lines():
        try:
            yield _loads(line)
        except ValueError:
            continue


def _key(row: Row, second_key: str | None) -> Row:
    key = {"date": row["date"]}
    if second_key is not None:
        key[second_key] = row[second_key]
    return key


def _matches(existing: Any, key: Row) -> bool:
    return isinstance(existing, dict) and all(existing.get(k) == v for k, v in key.items())


def _describe(key: Row) -> str:
    """A key as the log names it: `2026-09-23`, or `2026-09-23 topic_id=7`."""
    return " ".join(str(v) if k == "date" else f"{k}={v}" for k, v in key.items())


def upsert(file: File, row: Row, now: datetime, second_key: str | None = None) -> Row | None:
    """Write `row` as the file's one row for its `date` (and `second_key`, when given).

    Lines with the same key are dropped; every other line is kept as it is,
    except that blank lines are dropped and a missing final newline is added.
    Lines that don't parse, or parse to something other than an object, are
    kept (where the script crashes on the latter, docs/ARCHITECTURE_DECISIONS.md).
    The row gets `captured_at`, from `now`, and is appended. Returns the row it
    replaced (the last line with the key), or None.
    """
    key = _key(row, second_key)
    record = dict(row)
    record["captured_at"] = _captured_at(now)

    kept: list[str] = []
    replaced: Row | None = None
    for line in file.read_lines():
        try:
            existing = _loads(line)
        except ValueError:
            existing = None  # kept rather than silently dropped
        if _matches(existing, key):
            replaced = existing
            continue
        kept.append(line if line.endswith("\n") else line + "\n")
    kept.append(json.dumps(record) + "\n")
    file.write_lines(kept)

    verb = "Replaced %s in %s" if replaced is not None else "Added %s to %s"
    log.info(verb, _describe(key), file.name)
    return replaced


def replace_window(file: File, rows: Iterable[Row], now: datetime) -> dict[str, Row]:
    """Write `rows` as the file's rows for their dates, and re-sort the whole file.

    Existing rows are keyed by `date` (the last line for a date wins) and
    re-serialized; lines that don't parse, aren't an object or have no string
    `date` are written first, as they were (where the script crashes sorting
    a non-string `date`, docs/ARCHITECTURE_DECISIONS.md). Then every row,
    sorted by date. Each of `rows` gets `captured_at`, from `now`. Returns the
    rows it replaced, by date.
    """
    captured_at = _captured_at(now)
    by_date: dict[str, Row] = {}
    unparsed: list[str] = []
    for line in file.read_lines():
        try:
            existing = _loads(line)
        except ValueError:
            existing = None
        if isinstance(existing, dict) and isinstance(existing.get("date"), str):
            by_date[existing["date"]] = existing
        else:
            unparsed.append(line.rstrip("\n"))
    before = dict(by_date)
    written: list[str] = []
    for row in rows:
        record = dict(row)
        record["captured_at"] = captured_at
        by_date[record["date"]] = record
        written.append(record["date"])

    lines = [line + "\n" for line in unparsed]
    lines += [json.dumps(by_date[d]) + "\n" for d in sorted(by_date)]
    file.write_lines(lines)
    log.info("Wrote %d row(s) to %s", len(written), file.name)
    return {day: before[day] for day in written if day in before}


def write_good(file: File, row: Row, ctx: Context, source: str, second_key: str | None = None) -> None:
    """Upsert the good row `row` for `source`, recording on `ctx` the correction it makes, if any.

    Every source writes its good rows here (or through `write_window`), so no correction goes
    unrecorded (docs/ARCHITECTURE_DECISIONS.md, "Every correction is recorded").
    """
    if not is_good(row):
        raise ValueError(f"not a good row: status is {row.get('status')!r}")
    replaced = upsert(file, row, ctx.now, second_key)
    _record_correction(ctx, source, file, replaced, row, second_key)


def write_window(file: File, rows: Iterable[Row], ctx: Context, source: str) -> None:
    """Replace the window `rows` covers for `source`, recording on `ctx` each correction it makes.

    `rows` may hold a failure row, as the script's window does. As `write_failure` does, it is left out
    where a good or partial row holds its date, and a WARN line says so (docs/ARCHITECTURE_DECISIONS.md,
    "A failure never replaces good data"); the rest of the window is written, and the file rewritten.
    """
    rows = [row for row in rows if is_good(row) or not _held(file, row)]
    replaced = replace_window(file, rows, ctx.now)
    last = {row["date"]: row for row in rows}  # the row that stands for each date, as replace_window keeps it
    for day, row in last.items():
        _record_correction(ctx, source, file, replaced.get(day), row, None)


BACKFILLED = config.BACKFILLED  # the mark of a row written for a past day that had none, from history


def first_night(file: File, ctx: Context, where: Mapping[str, Any] | None = None) -> date | None:
    """The date of the file's first row a night wrote before the run date: any row not marked BACKFILLED, good or not,
    that `where` keeps (each dotted field equal to its value); None without one."""
    nights = [
        day
        for row in rows(file)
        if not row.get(BACKFILLED) and all(value(row, k) == v for k, v in (where or {}).items())
        for day in [parse_date(row.get("date"))]
        if day is not None and day < ctx.date
    ]
    return min(nights, default=None)


def rebuild_days(file: File, start: date | None, ctx: Context, where: Mapping[str, Any] | None = None) -> list[date]:
    """The days a source rebuilds (ADR: past values a source can rebuild are filled, never changed): from `start`, the
    first day its history can tell, to the day before the file's `first_night`.

    None without a first night, so a new file gets its history the night after its first row; and none without a
    `start`. Days that already have a row are included: `fill` adds only what they lack.
    """
    return _days(start, first_night(file, ctx, where))


def recompute_days(file: File, start: date | None, ctx: Context) -> list[date]:
    """The days a derived source recomputes (ADR: derived history follows today's rules): from `start`, the first
    day its history can tell, to the day before the run date.

    None without a `first_night`, so a new file gets its history the night after its first row; and none without a
    `start`.
    """
    return _days(start, ctx.date) if first_night(file, ctx) is not None else []


def _days(start: date | None, end: date | None) -> list[date]:
    """Each day from `start` to the day before `end`; none without both."""
    if start is None or end is None:
        return []
    return [start + timedelta(days=n) for n in range((end - start).days)]


def lacks(row: Row | None, field: str) -> bool:
    """Whether a fill would add `field` to `row`, the stored row of a past day: none, no such field, or the field null
    in a BACKFILLED row (what a rebuild couldn't tell)."""
    return row is None or (row.get(field) is None and (field not in row or bool(row.get(BACKFILLED))))


def fill(file: File, rows: Iterable[Row], ctx: Context, second_key: str | None = None) -> None:
    """Backfill `rows`, good rows for days before the run date rebuilt from history, never changing a value.

    For each row's date, and its `second_key` where the source writes by one (docs/ARCHITECTURE.md, "Terms";
    docs/ARCHITECTURE_DECISIONS.md, "Past values a source can rebuild are filled, never changed"):
    - where the key's row (its last line) is good, each field of `row` it lacks is added, inside objects too, and
      the line keeps its place and its `captured_at`, still last; a field it has, null included, is left as it is,
      except a null in a BACKFILLED row, which a rebuild couldn't tell, filled where `row` has a value;
    - where it is a failure or partial row, it is left as it is;
    - where no line has the key, `row` is added, marked BACKFILLED, with `captured_at` from `ctx.now`, before the
      first row of a later date, so a file in date order stays in it.
    Nothing it writes is a correction, since no value changes. One line logs what it did; with nothing missing,
    nothing is written.
    """
    rows = _past(rows, ctx, "a fill")
    lines, parsed, last = _lines_by_key(file, second_key)
    filled, added = [], []
    for row in rows:
        if _fill_key(row, second_key) not in last:
            added.append(_backfilled(row, ctx))
            continue
        n, stored = last[_fill_key(row, second_key)]
        if is_good(stored) and _add_missing(stored, row, bool(stored.get(BACKFILLED))):
            if "captured_at" in stored:  # last, as upsert writes it
                stored["captured_at"] = stored.pop("captured_at")
            lines[n] = json.dumps(stored) + "\n"
            filled.append(row["date"])
    _insert(lines, parsed, added)
    if not (filled or added):
        return
    file.write_lines(lines)
    days = sorted(filled + [row["date"] for row in added])
    log.info(
        "Backfilled %s: %d row(s) filled, %d added, %s to %s", file.name, len(filled), len(added), days[0], days[-1]
    )


def recompute(file: File, rows: Iterable[Row], ctx: Context, source: str) -> None:
    """Write `rows`, a derived source's good rows for days before the run date, recomputed under today's rules,
    recording on `ctx` each correction it makes (ADR: derived history follows today's rules).

    For each row's date, by the file's last line for it:
    - where it is a good row, each value of `row` replaces the row's, a field only the row has is kept, and a field
      only `row` has is added, before `captured_at`; where a value changed, the line, in its place, gets `captured_at`
      from `ctx.now` and the change is a correction (docs/ARCHITECTURE_DECISIONS.md, "Every correction is
      recorded"); where none changed, the line is left as it is;
    - where it is a failure or partial row, `row` replaces it in its place, as a good row replaces one: no correction;
    - where no line has the date, `row` is added, marked BACKFILLED, as `fill` adds it.
    One line logs what it did; with nothing changed, nothing is written.
    """
    rows = _past(rows, ctx, "a recompute")
    lines, parsed, last = _lines_by_key(file, None)
    corrected = replaced = 0
    added = []
    for row in rows:
        if _fill_key(row, None) not in last:
            added.append(_backfilled(row, ctx))
            continue
        n, stored = last[_fill_key(row, None)]
        if is_good(stored):
            held = {key: value for key, value in stored.items() if key != "captured_at"}
            new = {**held, **row}
            if _json(new) == _json(held):
                continue
            corrected += 1
        else:
            new, replaced = dict(row), replaced + 1
        new["captured_at"] = _captured_at(ctx.now)
        lines[n] = json.dumps(new) + "\n"
        parsed[n] = new
        _record_correction(ctx, source, file, stored, new, None)
    _insert(lines, parsed, added)
    if not (corrected or replaced or added):
        return
    file.write_lines(lines)
    log.info(
        "Recomputed %s: %d corrected, %d added, %d failure row(s) replaced", file.name, corrected, len(added), replaced
    )


def _past(rows: Iterable[Row], ctx: Context, what: str) -> list[Row]:
    """`rows`, each checked to be a good row without a status for a day before the run date."""
    rows = list(rows)
    for row in rows:
        if "status" in row or row["date"] >= ctx.date.isoformat():
            raise ValueError(f"{what} takes rows without a status for days before the run date, not {row}")
    return rows


def _lines_by_key(
    file: File, second_key: str | None
) -> tuple[list[str], list[Row | None], dict[tuple[Any, ...], tuple[int, Row]]]:
    """The file's lines, each ending in a newline, each line's object (None for a line that isn't one), and the
    last line's number and object for each key."""
    lines = [line if line.endswith("\n") else line + "\n" for line in file.read_lines()]
    parsed = [_object(line) for line in lines]
    last = {
        _fill_key(row, second_key): (n, row)
        for n, row in enumerate(parsed)
        if row is not None and isinstance(row.get("date"), str)
    }
    return lines, parsed, last


def _backfilled(row: Row, ctx: Context) -> Row:
    """`row` as it is added for a day with none: marked BACKFILLED, with `captured_at` from `ctx.now`."""
    return {**row, BACKFILLED: True, "captured_at": _captured_at(ctx.now)}


def _insert(lines: list[str], parsed: list[Row | None], added: Iterable[Row]) -> None:
    """Insert each of `added` before the first row of a later date (at the end, without one), so a file in date order
    stays in it."""
    for row in sorted(added, key=lambda r: r["date"]):
        at = next((n for n, line in enumerate(parsed) if (_dated(line) or "") > row["date"]), len(lines))
        lines.insert(at, json.dumps(row) + "\n")
        parsed.insert(at, row)


def _fill_key(row: Row, second_key: str | None) -> tuple[Any, ...]:
    return (row["date"], row.get(second_key)) if second_key is not None else (row["date"],)


def _object(line: str) -> Row | None:
    """The line's JSON object, or None for a line that isn't one."""
    try:
        found = _loads(line)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def _dated(row: Row | None) -> str | None:
    """The row's date where it is a real day, else None."""
    return row["date"] if row is not None and parse_date(row.get("date")) else None


def _add_missing(stored: Row, row: Row, rebuilt: bool) -> bool:
    """Add to `stored` each field of `row` it lacks, inside the objects both have too, and, in a `rebuilt` (BACKFILLED)
    row, replace a null with a value; whether it added any."""
    changed = False
    for key, value in row.items():
        if key not in stored or (rebuilt and stored[key] is None and value is not None):
            stored[key] = value
            changed = True
        elif isinstance(stored[key], dict) and isinstance(value, dict):
            changed = _add_missing(stored[key], value, rebuilt) or changed
    return changed


def _json(value: Any) -> str:
    """A value as a correction compares it: its JSON, an object's keys in any order."""
    return json.dumps(value, sort_keys=True)


def _record_correction(
    ctx: Context, source: str, file: File, old: Row | None, new: Row, second_key: str | None
) -> None:
    """Record a correction, where there is one: the values that differ, `captured_at` aside.

    A correction (docs/ARCHITECTURE.md, "Terms") is `new`, a good row, replacing `old`, a good row for a
    day before the run's date, with some value different.
    """
    if old is None or not (is_good(old) and is_good(new) and new["date"] < ctx.date.isoformat()):
        return
    fields = [f for f in list(old) + [f for f in new if f not in old] if f != "captured_at"]
    changed = [f for f in fields if f not in old or f not in new or _json(old[f]) != _json(new[f])]
    if changed:
        before = {f: old[f] for f in changed if f in old}
        after = {f: new[f] for f in changed if f in new}
        key = {second_key: new[second_key]} if second_key is not None else {}
        ctx.record_correction(source, file.name, new["date"], before, after, key)
