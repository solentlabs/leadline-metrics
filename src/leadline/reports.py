"""Reports: marked blocks in Markdown files, each a table or lines built from the row files.

The config's `reports` lists the blocks (docs/REPORTS.md). leadline owns the text between
`<!-- leadline:start NAME -->` and `<!-- leadline:end NAME -->` and never changes a byte outside it: a new
block goes at the top or bottom of its file, and after that it is rewritten where it is. Each block is
built on its own; one that fails (a marker missing or repeated, a file that can't be read, a bug) is
logged and recorded critical, and the rest are written. Reports read the row files through store, and
skip failure rows (ADR: a failure is recorded as a failure).

A line is a piece: it may read a file and name a kind (a value, a series, an aggregate), which gives
fields; its text is a template of those fields and of the pieces it is in; its parts are pieces, joined;
and a piece whose template names a field with no value, or whose `when` fails, doesn't show.
"""

from __future__ import annotations

import contextlib
import operator
import os
import re
import string
import typing
from collections import ChainMap
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, NamedTuple, Sequence, Tuple

from leadline import store
from leadline.config import Hide, Section, complement, plain_jsonl, section_lines, version_key
from leadline.context import Reporter

Row = Dict[str, Any]
Fields = typing.ChainMap[str, Any]  # a piece's template fields: its own, then those of the pieces it is in

SOURCE = "reports"  # what a failed block is recorded critical as
PLACES = ("top", "bottom")
GROUPS = ("day", "week")
TABLE_AGGREGATES = ("last", "change", "range", "versions")
LINE_AGGREGATES = ("peak", "sum", "versions", "items", "latest_release", "release_downloads", "counts")
ITEM_AGGREGATES = ("items", "release_downloads", "versions", "counts")  # those that give items (versions with a pick)
PICKS = ("releases", "newest_prereleases", "older_prereleases")
OPS: dict[str, Callable[[Any, Any], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    "=": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    ">": operator.gt,
}
MISSING = "-"  # a value a row doesn't have, in a series or a table cell
WEEK = " (wk)"  # after the date of a week's row: the week's last day
AS_OF = " (as of {date})"  # after a piece whose file's newer rows are failure rows
SHORTENED = "…"  # ends a value `shorten` cut

_NAME = re.compile(r"[A-Za-z0-9_-]+")
_ANY_MARKER = re.compile(r"<!-- leadline:(?:start|end) \S+ -->")
_MARKER_OPENING = re.compile(r"<(!--\s*leadline:)")  # a value that opens one is shown with the `<` escaped
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
_COMPARE = re.compile(r"yesterday|[1-9][0-9]*d")
_FORMATTER = string.Formatter()

_ENTRY = {"file", "block", "place", "heading", "table", "lines"}
_TABLE = {"from", "group", "recent_weeks", "columns"}
_VIEW = {"text", "parts", "join", "when", "replace", "shorten"}  # how a piece shows, as a line, part, item or cell
_COLUMN = {"title", "field", "from", "aggregate", "bold", "format", "per"} | _VIEW
_PER = {"field", "from"}
_DATA = {
    "from",
    "where",
    "field",
    "format",
    "compare",
    "series",
    "every",
    "aggregate",
    "count",
    "days",
    "item",
    "sort",
    "match",
    "skip",
    "pick",
    "trail",
    "tag",
    "as_of",
    "complement",
}  # what a piece that reads a file may say about it
_PIECE = _DATA | _VIEW | {"label"}
# A piece's options that only some aggregates take, and the options some aggregates need.
_ONLY = {
    "count": ("sum", *ITEM_AGGREGATES),
    "days": ("counts",),
    "item": ITEM_AGGREGATES,
    "sort": ("items",),
    "match": ITEM_AGGREGATES,
    "skip": ITEM_AGGREGATES,
    "pick": ("versions",),
    "trail": ("versions",),
    "tag": ("latest_release",),
}
_NEEDS = {"sum": ("count",), "items": ("item",), "release_downloads": ("compare",)}
_PICKED = ("item", "count", "match", "skip", "trail")  # what versions takes only with a pick
# The item template and join of an aggregate that has them; no join is a line an item.
_ITEM = {"release_downloads": "{tag} +{gain}", "counts": "{key} {count}"}
_JOIN = {"release_downloads": " · ", "counts": " · "}


class BlockError(Exception):
    """A block that can't be written as the file stands, such as one whose marker is repeated."""


# ---- Config --------------------------------------------------------------------------------------


def validate(cfg: Any) -> list[str]:
    """Every problem with the `reports` config, each starting with its path (`[1].place: `); empty when usable."""
    if not isinstance(cfg, list):
        return [": must be a list of reports"]
    found: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    for i, entry in enumerate(cfg):
        if not isinstance(entry, dict):
            found.append(f"[{i}]: must be an object")
            continue
        own = _entry_problems(entry)
        found += [f"[{i}]{problem}" for problem in own]
        if not own:
            key = (_file_key(entry["file"]), entry["block"])
            if key in seen:
                found.append(f"[{i}].block: {entry['block']} is already in {seen[key]}")
            seen.setdefault(key, entry["file"])
    return found


def _file_key(file: str) -> str:
    """A report file as one file system holds it: `./R.md` and `r.md` are one file on OneDrive's."""
    return os.path.normpath(file).casefold()


def _unknown(obj: Mapping[str, Any], keys: set[str]) -> list[str]:
    unknown = sorted(set(obj) - keys)
    return [f": unknown keys: {', '.join(unknown)}"] if unknown else []


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _whole(value: Any, least: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= least


def _format_ok(spec: Any) -> bool:
    """A format spec a number takes, with a sign or without, as a change shows one."""
    try:
        format(0, spec)
        format(0, "+" + spec)
    except (TypeError, ValueError):
        return False
    return True


def _template_ok(text: Any) -> bool:
    """A template: text with `{field}` or `{field:spec}` in it, each field named, no conversion, no nested field."""
    if not isinstance(text, str):
        return False
    try:
        parsed = list(_FORMATTER.parse(text))
    except ValueError:
        return False
    return all(
        name is None or (name and not conversion and "{" not in (spec or "")) for _, name, spec, conversion in parsed
    )


def _pattern_ok(pattern: Any, replacement: str = "") -> bool:
    """A regular expression, and a replacement it takes."""
    try:
        re.compile(pattern).sub(replacement, "")
    except (TypeError, re.error):
        return False
    return True


def _common(obj: Mapping[str, Any], text_keys: Sequence[str]) -> list[str]:
    """The problems of keys several objects share: required text, an optional `from` and `format`."""
    found = [f".{key}: must be a non-empty string" for key in text_keys if not _text(obj.get(key))]
    if "from" in obj and not plain_jsonl(obj["from"]):
        found.append(".from: must be a plain *.jsonl name")
    if "format" in obj and not _format_ok(obj["format"]):
        found.append(".format: must be a format spec without a sign, such as .2f")
    return found


def _entry_problems(entry: Mapping[str, Any]) -> list[str]:
    found = _unknown(entry, _ENTRY)
    file = entry.get("file")
    if not (isinstance(file, str) and file.endswith(".md") and Path(file).name != ".md"):
        found.append(".file: must be a *.md path")
    if not (isinstance(entry.get("block"), str) and _NAME.fullmatch(entry["block"])):
        found.append(".block: must be letters, digits, - or _")
    if entry.get("place") not in PLACES:
        found.append(".place: must be top or bottom")
    if "heading" in entry and not _text(entry["heading"]):
        found.append(".heading: must be a non-empty string")
    if ("table" in entry) == ("lines" in entry):
        found.append(": must have one of table or lines")
    elif "table" in entry:
        found += [f".table{problem}" for problem in _table_problems(entry["table"])]
    else:
        found += [f".lines{problem}" for problem in _lines_problems(entry["lines"])]
    return found


def _table_problems(table: Any) -> list[str]:
    if not isinstance(table, dict):
        return [": must be an object"]
    found = _unknown(table, _TABLE)
    if not plain_jsonl(table.get("from")):
        found.append(".from: must be a plain *.jsonl name")
    group = table.get("group", "day")
    if group not in GROUPS:
        found.append(".group: must be day or week")
    if "recent_weeks" in table and group != "week":
        found.append(".recent_weeks: needs group week")
    elif "recent_weeks" in table and not _whole(table["recent_weeks"], 0):
        found.append(".recent_weeks: must be a whole number, 0 or more")
    columns = table.get("columns")
    if not (isinstance(columns, list) and columns):
        return [*found, ".columns: must be a non-empty list"]
    for j, column in enumerate(columns):
        found += [f".columns[{j}]{problem}" for problem in _column_problems(column)]
    return found


def _column_problems(column: Any) -> list[str]:
    if not isinstance(column, dict):
        return [": must be an object"]
    found = _unknown(column, _COLUMN) + _common(column, ("title", "field")) + _view_problems(column, reads=False)
    if column.get("aggregate", "last") not in TABLE_AGGREGATES:
        found.append(f".aggregate: must be one of {', '.join(TABLE_AGGREGATES)}")
    if not isinstance(column.get("bold", False), bool):
        found.append(".bold: must be true or false")
    if "per" in column:
        per = column["per"]
        if not isinstance(per, dict):
            found.append(".per: must be an object")
        else:
            found += [f".per{problem}" for problem in _unknown(per, _PER) + _common(per, ("field",))]
    return found


def _lines_problems(lines: Any) -> list[str]:
    if not (isinstance(lines, list) and lines):
        return [": must be a non-empty list"]
    return [f"[{j}]{problem}" for j, line in enumerate(lines) for problem in _piece_problems(line, nested=False)]


def _piece_problems(piece: Any, nested: bool, reads: bool = True) -> list[str]:
    """A line, or a part or item in one (`nested`); a column's parts can't read a file (`reads`)."""
    if not isinstance(piece, dict):
        return [": must be an object"]
    if "from" in piece and not reads:
        return [": a column's parts can't read a file"]
    found = _unknown(piece, _PIECE)
    if "label" in piece and "text" in piece:
        found.append(": has both label and text")
    if "label" in piece and not _text(piece["label"]):
        found.append(".label: must be a non-empty string")
    if "from" in piece:
        found += _data_problems(piece, nested)
    else:
        found += [f": {key} needs from" for key in sorted(_DATA & set(piece))]
        if "label" in piece:
            found.append(".label: needs from")
        elif "text" not in piece and "parts" not in piece:
            found.append(": must have text or parts, or read a file with from")
    return found + _view_problems(piece, reads)


def _view_problems(obj: Mapping[str, Any], reads: bool) -> list[str]:
    """The problems of how a piece shows: its text, parts, join, when, replace and shorten."""
    found = []
    if "text" in obj and not _template_ok(obj["text"]):
        found.append(".text: must be a template of named fields")
    if "parts" in obj:
        parts = obj["parts"]
        if not (isinstance(parts, list) and parts):
            found.append(".parts: must be a non-empty list")
        else:
            for k, part in enumerate(parts):
                found += [f".parts[{k}]{problem}" for problem in _piece_problems(part, nested=True, reads=reads)]
    if "join" in obj and not isinstance(obj["join"], str):
        found.append(".join: must be a string")
    if "when" in obj:
        found += _when_problems(obj["when"])
    for key, ok, need in (
        ("replace", _replacements_ok, "a list of [pattern, replacement]"),
        ("shorten", lambda n: _whole(n, 2), "a whole number, 2 or more"),
    ):
        if key not in obj:
            continue
        if not isinstance(obj[key], dict):
            found.append(f".{key}: must be an object")
        else:
            found += [f".{key}.{field}: must be {need}" for field, value in obj[key].items() if not ok(value)]
    return found


def _replacements_ok(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(pair, list) and len(pair) == 2 and isinstance(pair[1], str) and _pattern_ok(pair[0], pair[1])
            for pair in value
        )
    )


def _when_problems(when: Any) -> list[str]:
    if not isinstance(when, list):
        return [".when: must be a list"]
    need = f"must be [field, op, value], op one of {', '.join(OPS)}"
    return [f".when[{k}]: {need}" for k, condition in enumerate(when) if not _condition_ok(condition)]


def _condition_ok(condition: Any) -> bool:
    return (
        isinstance(condition, list)
        and len(condition) == 3
        and _text(condition[0])
        and condition[1] in OPS
        and (condition[2] is None or isinstance(condition[2], (str, int, float)))
    )


def _data_problems(piece: Mapping[str, Any], nested: bool) -> list[str]:
    """The problems of a piece that reads a file: its field, its kind and the kind's options."""
    found = _common(piece, ("field",))
    aggregate = piece.get("aggregate")
    kinds = [kind for kind in ("compare", "series", "aggregate") if kind in piece]
    if len(kinds) > 1 and not (kinds == ["compare", "aggregate"] and aggregate == "release_downloads"):
        found.append(": has more than one of compare, series, aggregate")
    if "compare" in piece and not (isinstance(piece["compare"], str) and _COMPARE.fullmatch(piece["compare"])):
        found.append(".compare: must be yesterday or <N>d")
    for key in ("series", "every"):
        if key in piece and not _whole(piece[key], 1):
            found.append(f".{key}: must be a whole number, 1 or more")
    if "every" in piece and "series" not in piece:
        found.append(".every: needs series")
    if "where" in piece and not isinstance(piece["where"], dict):
        found.append(".where: must be an object")
    if "aggregate" in piece and aggregate not in LINE_AGGREGATES:
        found.append(f".aggregate: must be one of {', '.join(LINE_AGGREGATES)}")
    if "as_of" in piece and not _template_ok(piece["as_of"]):
        found.append(".as_of: must be a template of named fields")
    if nested and _gives_items(piece) and aggregate not in _JOIN and "join" not in piece:
        found.append(f".join: {aggregate} needs it inside another piece")
    return found + _complement_problems(piece) + _option_problems(piece, aggregate)


def _complement_problems(piece: Mapping[str, Any]) -> list[str]:
    if "complement" not in piece:
        return []
    if piece["complement"] is not True:
        return [".complement: must be true"]
    return [] if _kind(piece) == "value" else [".complement: only a field alone or with compare takes it"]


def _gives_items(piece: Mapping[str, Any]) -> bool:
    aggregate = piece.get("aggregate")
    return aggregate in ("items", "release_downloads", "counts") or (aggregate == "versions" and "pick" in piece)


def _option_problems(piece: Mapping[str, Any], aggregate: Any) -> list[str]:
    """The options only some aggregates take, and those an aggregate needs."""
    found = [f".{key}: {aggregate} needs it" for key in _NEEDS.get(aggregate, ()) if key not in piece]
    if aggregate == "versions":
        if "pick" in piece and "item" not in piece:
            found.append(".item: versions needs it with pick")
        found += [f".pick: versions needs it for {key}" for key in _PICKED if key in piece and "pick" not in piece]
    for key, takers in _ONLY.items():
        if key not in piece:
            continue
        if aggregate not in takers:
            verb = "takes" if len(takers) == 1 else "take"
            found.append(f".{key}: only {_listed(takers)} {verb} it")
        else:
            found += [f".{key}{problem}" for problem in _option_value_problems(key, piece[key])]
    return found


def _option_value_problems(key: str, value: Any) -> list[str]:
    if key == "item":
        if isinstance(value, dict):
            return _piece_problems(value, nested=True)
        return [] if _template_ok(value) else [": must be a template or a piece"]
    if key == "match":
        if not isinstance(value, dict):
            return [": must be an object"]
        return [
            f".{field}: must be a regular expression" for field, pattern in value.items() if not _pattern_ok(pattern)
        ]
    least = {"count": 1, "days": 1, "trail": 1, "skip": 0}.get(key)
    if least is not None:
        return [] if _whole(value, least) else [f": must be a whole number, {least} or more"]
    if key == "pick":
        return [] if value in PICKS else [f": must be one of {', '.join(PICKS)}"]
    if key == "tag":
        return [] if _template_ok(value) else [": must be a template of named fields"]
    return [] if _text(value) else [": must be a non-empty string"]  # sort


def _listed(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


# ---- The default summary -------------------------------------------------------------------------

SUMMARY = {"file": "SUMMARY.md", "block": "summary", "place": "top", "heading": "Summary"}


def default(sections: Sequence[Section], health: str) -> list[dict[str, Any]]:
    """The reports of a config with none (docs/REPORTS.md, "The default summary"): one block, SUMMARY.md's
    `summary`, of the run's health lines from `health`, the health file, then `sections`, each source's."""
    lines = [line for section in sections for line in section_lines(section)]
    return [{**SUMMARY, "lines": [*_health_lines(health), *lines]}]


def _health_lines(health: str) -> list[dict[str, Any]]:
    """The run's date; all current, or each failed and stale file; each critical entry; each warning or error found."""

    def listed(field: str, item: Any, **more: Any) -> dict[str, Any]:
        return {"from": health, "field": field, "aggregate": "items", "item": item, **more}

    return [
        {"from": health, "field": "date", "text": "- Run: {value}"},
        {
            "from": health,
            "field": "date",
            "text": "- All sources current",
            "when": [["row.stale_count", "=", 0], ["row.failed_count", "=", 0]],
        },
        listed(
            "failed",
            {
                "parts": [
                    {"text": "- Failed: {file}, {status} since {since}"},
                    {"text": " ({streak} nights)", "when": [["streak", ">", 1]]},
                ]
            },
        ),
        listed("stale", {"parts": [{"text": "- Stale: {file}"}, {"text": ", last row {last}"}]}),
        listed("critical", "- Critical: {source}: {reason}"),
        listed("checks", "- Check {source} ({level}): {message}", match={"level": "^(warn|error)$"}),
    ]


# ---- Writing -------------------------------------------------------------------------------------


def write(ctx: Reporter, cfg: Sequence[Mapping[str, Any]], hide: Mapping[str, Sequence[Hide]] | None = None) -> None:
    """Write every block `cfg` (a valid config) names, each file once; a block that fails is critical. The blocks
    read their files without the items `hide` names for the file (`config.hidden`)."""
    files: dict[str, list[Mapping[str, Any]]] = {}
    for entry in cfg:
        files.setdefault(_file_key(entry["file"]), []).append(entry)
    for entries in files.values():
        _write_file(ctx, entries, Folder(ctx.out_dir, hide or {}))


def _write_file(ctx: Reporter, entries: Sequence[Mapping[str, Any]], folder: Folder) -> None:
    """Rewrite one file's blocks: each existing one where it is, each new one at its place, the rest untouched."""
    file = entries[0]["file"]
    path = ctx.out_dir / file
    try:
        text = path.read_bytes().decode("utf-8") if path.exists() else ""
    except (OSError, ValueError) as e:
        for entry in entries:
            _failed(ctx, entry, f"can't read {file}: {e}")
        return
    bom = "\ufeff" if text.startswith("\ufeff") else ""  # kept, but not part of the first line
    lines = text[len(bom) :].splitlines(keepends=True)
    newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    new: dict[str, list[str]] = {place: [] for place in PLACES}
    done: list[Mapping[str, Any]] = []
    for entry in entries:
        try:
            block = _block(folder, entry, newline)
            span = _span(lines, entry["block"], entry["file"])
        except BlockError as e:
            _failed(ctx, entry, str(e))
            continue
        except Exception as e:  # a bug building one block never stops the others: logged, and critical
            error = f"{type(e).__name__}: {e}"
            ctx.log.exception("ERROR: block %s in %s crashed: %s", entry["block"], entry["file"], error)
            ctx.record_critical(SOURCE, entry["file"], f"block {entry['block']} crashed: {error}")
            continue
        if span is None:
            new[entry["place"]].append("".join(block))
        else:
            lines[span[0] : span[1] + 1] = block
        done.append(entry)
    if not done:
        return
    try:
        store.write_text(path, bom + _placed("".join(lines), new, newline))
    except OSError as e:
        for entry in done:
            _failed(ctx, entry, f"can't write {file}: {e}")
        return
    ctx.log.info("Wrote %s: %s", file, ", ".join(entry["block"] for entry in done))


def _placed(text: str, new: Mapping[str, Sequence[str]], newline: str) -> str:
    """`text` with the new blocks at the top and the bottom, in config order, a blank line between each."""
    if new["top"]:
        text = newline.join(new["top"]) + (newline + text if text else "")
    if new["bottom"]:
        if text and not text.endswith("\n"):
            text += newline
        text += (newline if text else "") + newline.join(new["bottom"])
    return text


def _failed(ctx: Reporter, entry: Mapping[str, Any], reason: str) -> None:
    ctx.record_critical(SOURCE, entry["file"], f"block {entry['block']}: {reason}")


def _marker(which: str, name: str) -> str:
    return f"<!-- leadline:{which} {name} -->"


def _span(lines: Sequence[str], name: str, file: str) -> tuple[int, int] | None:
    """Where block `name` is in the file's lines, first and last, markers included; None when it isn't.

    Raises BlockError unless each marker is there once, the start first, and nothing between them is
    another block's marker: anything else could mean rewriting text that isn't the block's.
    """
    found = {
        which: [i for i, line in enumerate(lines) if line.strip() == _marker(which, name)] for which in ("start", "end")
    }
    if not found["start"] and not found["end"]:
        return None
    for which, at in found.items():
        if len(at) != 1:
            raise BlockError(f"its {which} marker is in {file} {len(at)} times")
    start, end = found["start"][0], found["end"][0]
    if end < start:
        raise BlockError(f"its end marker comes before its start marker in {file}")
    if any(_ANY_MARKER.fullmatch(line.strip()) for line in lines[start + 1 : end]):
        raise BlockError(f"holds another block's marker in {file}")
    return start, end


class Folder(NamedTuple):
    """Where the blocks read their files: the output folder, and the items to leave out of each file's lists."""

    path: Path
    hidden: Mapping[str, Sequence[Hide]]

    def rows(self, name: str, where: Mapping[str, Any] | None = None) -> store.Dated:
        """The dated rows of the file `name` (`store.dated_rows`) without the items hidden in it."""
        return store.dated_rows(self.path / name, where, self.hidden.get(name, ()))


def _block(folder: Folder, entry: Mapping[str, Any], newline: str) -> list[str]:
    """The block's lines, its markers included: the heading and a blank line, then the table or the lines.

    A block with nothing to show is only its markers.
    """
    if "table" in entry:
        body = _table(folder, entry["table"])
    else:
        body = [text for line in entry["lines"] for text in _line(folder, line)]
    head = [f"## {_one_line(entry['heading'])}", ""] if body and "heading" in entry else []
    name = entry["block"]
    return [text + newline for text in [_marker("start", name), *head, *body, _marker("end", name)]]


# ---- Reading rows --------------------------------------------------------------------------------


def _day(row: Mapping[str, Any]) -> date:
    return date.fromisoformat(row["date"])


def _at_or_before(rows: Sequence[Row], day: date) -> Row | None:
    """The newest row dated `day` or earlier."""
    earlier = [row for row in rows if _day(row) <= day]
    return earlier[-1] if earlier else None


def _days(compare: str) -> int:
    return 1 if compare == "yesterday" else int(compare[:-1])


def _then(rows: Sequence[Row], compare: str) -> Row | None:
    """The row a comparison is made with: the newest one `compare` or more days before the newest row."""
    return _at_or_before(rows[:-1], _day(rows[-1]) - timedelta(days=_days(compare)))


def _when(compare: str) -> str:
    return "yesterday" if compare == "yesterday" else f"{compare} ago"


def _shown(value: Any, spec: str = "") -> str:
    """A value as a report shows it, on one line: a value a row doesn't have is MISSING."""
    return MISSING if value is None else _one_line(format(value, spec))


def _one_line(text: str) -> str:
    """No value breaks a line, forges a marker or holds a lone surrogate (which no file can encode)."""
    text = _LONE_SURROGATE.sub("\N{REPLACEMENT CHARACTER}", " ".join(text.splitlines()))
    return _MARKER_OPENING.sub(r"&lt;\1", text)


# ---- Pieces --------------------------------------------------------------------------------------


class _Style(NamedTuple):
    """How templates show values, by field: `replace` and `shorten`, a piece's own over the pieces' it is in."""

    replace: Mapping[str, Sequence[Sequence[str]]]
    shorten: Mapping[str, int]

    def under(self, piece: Mapping[str, Any]) -> _Style:
        return _Style({**self.replace, **piece.get("replace", {})}, {**self.shorten, **piece.get("shorten", {})})


_PLAIN = _Style({}, {})


class _Found(NamedTuple):
    """What a kind finds in a file's rows: its fields, and for an aggregate that gives items, the items shown."""

    fields: dict[str, Any]
    items: list[dict[str, Any]] | None = None


def _line(folder: Folder, line: Mapping[str, Any]) -> list[str]:
    """The lines one entry of `lines` gives: none when it doesn't show, one, or one an item."""
    return _render(folder, line, ChainMap(), _PLAIN)


def _render(folder: Folder, piece: Mapping[str, Any], outer: Fields, style: _Style) -> list[str]:
    """The lines a piece gives, `outer` the fields of the pieces it is in: none when it doesn't show; one, or,
    for items without a join, one an item.

    Where the file it reads has newer failure rows, each line ends with `as_of`, the date of the good row shown.
    """
    style = style.under(piece)
    fields, items, as_of = outer, None, ""
    if "from" in piece:
        rows = folder.rows(piece["from"], piece.get("where"))
        found = _KINDS[_kind(piece)](rows.good, piece, outer, style) if rows.good else None
        if found is None:
            return []
        latest = rows.good[-1]
        fields, items = outer.new_child({"row": latest, **found.fields}), found.items
        if rows.newest != latest["date"]:
            as_of = _fill(piece.get("as_of", AS_OF), ChainMap({"date": latest["date"]}), style) or ""
    if not _holds(piece.get("when", ()), fields):
        return []
    variants = [fields]
    if items is not None:
        texts = [text for item in items for text in _item(folder, piece, fields.new_child(item), style)]
        join = piece.get("join", _JOIN.get(_kind(piece)))
        joined: list[str | None] = list(texts) if join is None else [join.join(texts) if texts else None]
        variants = [fields.new_child({"items": text, "text": text}) for text in joined]
    lines = []
    for variant in variants:
        shown = variant
        if "parts" in piece:
            parts = [text for part in piece["parts"] for text in _render(folder, part, variant, style)]
            if not parts:
                continue
            shown = variant.new_child({"parts": piece.get("join", "").join(parts)})
        text = _fill(_template(piece), shown, style)
        if text is not None:
            lines.append(text + as_of)
    return lines


def _kind(piece: Mapping[str, Any]) -> str:
    return str(piece.get("aggregate") or ("series" if "series" in piece else "value"))


def _template(piece: Mapping[str, Any]) -> str:
    """A piece's template: its `text`; or `- label: ` and its kind's text; or its parts; or its kind's text."""
    if "text" in piece:
        return str(piece["text"])
    if "label" in piece:
        return "- " + str(piece["label"]).replace("{", "{{").replace("}", "}}") + ": {text}"
    return "{parts}" if "parts" in piece else "{text}"


def _item(folder: Folder, piece: Mapping[str, Any], fields: Fields, style: _Style) -> list[str]:
    """One item's text, from the piece's `item`: a template, or a piece of its own."""
    item = piece.get("item", _ITEM.get(_kind(piece)))
    if isinstance(item, dict):
        return _render(folder, item, fields, style)
    text = _fill(item, fields, style)
    return [] if text is None else [text]


def _fill(template: str, fields: Fields, style: _Style) -> str | None:
    """`template` filled from `fields`, on one line; None when a field it names has no value."""
    out = []
    for literal, name, spec, _ in _FORMATTER.parse(template):
        out.append(literal)
        if name is None:
            continue
        value = _resolve(fields, name)
        if value is None:
            return None
        if name in style.replace or name in style.shorten:
            value = _cleaned(str(value), style.replace.get(name, ()), style.shorten.get(name))
        out.append(format(value, spec or ""))
    return _one_line("".join(out))


def _resolve(fields: Fields, path: str) -> Any:
    """The value at a dotted path (`row.repo.stars`): its first name a field, the rest keys in it."""
    head, _, rest = path.partition(".")
    value = fields.get(head)
    return store.value(value, rest) if rest and value is not None else value


def _cleaned(text: str, replace: Sequence[Sequence[str]], shorten: int | None) -> str:
    """`text` after each replacement in turn, then, where longer than `shorten`, cut to fit with SHORTENED."""
    for pattern, replacement in replace:
        text = re.sub(pattern, replacement, text)
    if shorten is not None and len(text) > shorten:
        text = text[: shorten - 1].rstrip() + SHORTENED
    return text


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _holds(conditions: Sequence[Sequence[Any]], fields: Fields) -> bool:
    """Whether every condition holds: a field with no value fails; a list or object compares by its length;
    only numbers are ordered."""
    for path, op, expected in conditions:
        value = _resolve(fields, path)
        if isinstance(value, (list, dict)):
            value = len(value)
        if value is None:
            return False
        if op not in ("=", "!=") and not (_number(value) and _number(expected)):
            return False
        if not OPS[op](value, expected):
            return False
    return True


# ---- Kinds ---------------------------------------------------------------------------------------
# Each takes a file's good rows (oldest first, at least one), the piece, the fields of the pieces it is in
# and its style, and gives its fields, `text` among them where it has one; None when it has nothing to show.


def _value_kind(rows: Sequence[Row], piece: Mapping[str, Any], _outer: Fields, _style: _Style) -> _Found | None:
    """The newest value, and with `compare` the value then and the change since; with `complement`, each 100 less
    the field's."""
    value, spec = _read(rows[-1], piece), piece.get("format", "")
    if value is None:
        return None
    fields = {"value": value, "date": rows[-1]["date"], "text": _shown(value, spec)}
    if "compare" in piece:
        then = _then(rows, piece["compare"])
        old = None if then is None else _read(then, piece)
        if old is not None:
            fields.update(then=old, change=value - old)
            fields["text"] += f" ({_shown(value - old, '+' + spec)} vs {_when(piece['compare'])})"
    return _Found(fields)


def _read(row: Row, piece: Mapping[str, Any]) -> Any:
    """The row's value at the piece's field, or with `complement`, 100 less it (None for what isn't a number)."""
    value = store.value(row, piece["field"])
    return complement(value) if piece.get("complement") else value


def _series(rows: Sequence[Row], piece: Mapping[str, Any], _outer: Fields, _style: _Style) -> _Found:
    """The newest `series` values, oldest first; with `every`, one each `every` days back from the newest."""
    count, every = piece["series"], piece.get("every")
    if every is None:
        picked: list[Row | None] = list(rows[-count:])
    else:
        newest = _day(rows[-1])
        picked = [_at_or_before(rows, newest - timedelta(days=every * k)) for k in range(count - 1, -1, -1)]
    shown = [row for row in picked if row is not None]
    values = " ".join(_shown(store.value(row, piece["field"]), piece.get("format", "")) for row in shown)
    return _Found({"values": values, "count": len(shown), "text": values})


def _peak(rows: Sequence[Row], piece: Mapping[str, Any], _outer: Fields, _style: _Style) -> _Found | None:
    """The highest value, the first day it was reached, and that day's age at the newest row."""
    values = [(value, row["date"]) for row in rows for value in [store.value(row, piece["field"])] if value is not None]
    if not values:
        return None
    top = max(value for value, _ in values)
    day = next(day for value, day in values if value == top)
    age = (_day(rows[-1]) - date.fromisoformat(day)).days
    return _Found({"value": top, "date": day, "age": age, "text": f"{_shown(top, piece.get('format', ''))} on {day}"})


def _sum(rows: Sequence[Row], piece: Mapping[str, Any], _outer: Fields, _style: _Style) -> _Found | None:
    """The total of the newest `count` rows, through the newest row's date; where the `count` rows before them are
    all there, their total, `prior`, and the change since, `change`."""
    count, spec = piece["count"], piece.get("format", "")
    if len(rows) < count:
        return None

    def total(part: Sequence[Row]) -> Any:
        return sum(value for row in part for value in [store.value(row, piece["field"])] if value is not None)

    fields = {"total": total(rows[-count:]), "prior": None, "change": None, "date": rows[-1]["date"]}
    fields["text"] = f"{_shown(fields['total'], spec)} through {fields['date']}"
    if len(rows) >= 2 * count:
        prior = total(rows[-2 * count : -count])
        fields.update(prior=prior, change=fields["total"] - prior)
        fields["text"] += f" (prior {_shown(prior, spec)})"
    return _Found(fields)


def _versions(rows: Sequence[Row], piece: Mapping[str, Any], _outer: Fields, _style: _Style) -> _Found | None:
    """The version breakdown; with `pick`, those versions as items, newest first."""
    versions = store.value(rows[-1], piece["field"])
    if versions is None:
        return None
    if "pick" not in piece:
        return _Found({"text": format_versions(versions)})
    newest_first = sorted(versions, key=version_key, reverse=True)
    pre = [version for version in newest_first if "-" in version]
    base = pre[0].partition("-")[0] if pre else None
    chosen = {
        "releases": [version for version in newest_first if "-" not in version],
        "newest_prereleases": [version for version in pre if version.partition("-")[0] == base],
        "older_prereleases": [version for version in pre if version.partition("-")[0] != base],
    }[piece["pick"]]
    everything = sum(versions.values())
    items = [_version_item(rows, piece, version, versions[version], everything) for version in chosen]
    matched, shown, rest = _selected(items, piece)
    fields = {
        "found": len(matched),
        "total": sum(item["count"] for item in matched),
        "rest": sum(item["count"] for item in rest),
    }
    if piece["pick"] == "newest_prereleases":
        fields["base"] = base
    return _Found(fields, shown)


def _version_item(rows: Sequence[Row], piece: Mapping[str, Any], version: str, count: int, everything: int) -> Row:
    """A version as an item: its base and prerelease tag, its count and share of all the counts, and with
    `trail` its count in each of the newest rows, 0 where a row doesn't have it, and the trail's change."""
    base, _, tag = version.partition("-")
    item: Row = {"version": version, "base": base, "tag": tag or None, "count": count}
    item["share"] = round(100 * count / everything) if everything else None
    if "trail" in piece:
        trail = []
        for row in rows[-piece["trail"] :]:
            then = store.value(row, piece["field"])
            trail.append(then.get(version, 0) if isinstance(then, dict) else 0)
        item.update(trail=" ".join(map(str, trail)), trail_change=trail[-1] - trail[0])
    return item


def _selected(items: Sequence[Row], piece: Mapping[str, Any]) -> tuple[list[Row], list[Row], list[Row]]:
    """The items `match` keeps, those `sort` puts the largest first; of them, after `skip`, the first `count`
    shown, and the rest."""
    matched = [
        item
        for item in items
        if all(
            store.value(item, key) is not None and re.search(pattern, str(store.value(item, key)))
            for key, pattern in piece.get("match", {}).items()
        )
    ]
    if "sort" in piece:
        key = piece["sort"]
        matched.sort(key=lambda item: (store.value(item, key) is not None, store.value(item, key)), reverse=True)
    after, count = matched[piece.get("skip", 0) :], piece.get("count")
    return matched, after[:count], after[count:] if count is not None else []


def _items(rows: Sequence[Row], piece: Mapping[str, Any], _outer: Fields, _style: _Style) -> _Found:
    """The objects in a list in the newest row, as items."""
    listed = [item for item in store.value(rows[-1], piece["field"]) or [] if isinstance(item, dict)]
    matched, shown, _ = _selected(listed, piece)
    return _Found({"found": len(matched)}, shown)


def _published(release: Mapping[str, Any]) -> bool:
    return not release.get("draft") and not release.get("prerelease") and bool(release.get("published_at"))


def _downloads(release: Mapping[str, Any]) -> int:
    return sum(asset.get("downloads") or 0 for asset in release.get("assets") or [])


def _latest_release(rows: Sequence[Row], piece: Mapping[str, Any], outer: Fields, style: _Style) -> _Found | None:
    """The newest published release that is neither a draft nor a prerelease, or with `tag` (a template of the
    enclosing pieces' fields) the first release whose tag, as a template shows it, is that: its tag, downloads,
    and the day it was published and that day's age at the row."""
    releases = store.value(rows[-1], piece["field"]) or []
    if "tag" in piece:
        tag = _fill(piece["tag"], outer, style)
        shown = (release for release in releases if tag is not None and _fill("{tag}", ChainMap(release), style) == tag)
        chosen = next(shown, None)
    else:
        published = [release for release in releases if _published(release)]
        chosen = max(published, key=lambda release: release["published_at"]) if published else None
    if chosen is None:
        return None
    fields = {"tag": chosen["tag"], "downloads": _downloads(chosen)}
    if chosen.get("published_at"):
        day = chosen["published_at"][:10]
        age = (_day(rows[-1]) - date.fromisoformat(day)).days
        text = f"{chosen['tag']}, published {day} ({age} days ago), {fields['downloads']} downloads"
        fields.update(date=day, age=age, text=text)
    return _Found(fields)


def _release_downloads(rows: Sequence[Row], piece: Mapping[str, Any], _outer: Fields, _style: _Style) -> _Found | None:
    """Each release's downloads gained since the row `compare` back, the most first, as items; only those that
    gained."""
    then = _then(rows, piece["compare"])
    if then is None:
        return None
    before = {release["tag"]: _downloads(release) for release in store.value(then, piece["field"]) or []}
    now = [
        {"tag": r["tag"], "gain": _downloads(r) - before.get(r["tag"], 0)}
        for r in store.value(rows[-1], piece["field"]) or []
    ]
    gains = sorted((gain for gain in now if gain["gain"] > 0), key=lambda gain: -gain["gain"])
    matched, shown, _ = _selected(gains, piece)
    return _Found({"found": len(matched)}, shown)


def _counts(rows: Sequence[Row], piece: Mapping[str, Any], _outer: Fields, _style: _Style) -> _Found | None:
    """An object of counts in the newest row (`{"os": 40}`), as items, the largest first: each its `key`, `count`
    and `share` (its percent of all the counts, rounded); `total`, of those matched, and `rest`, of those past
    `count`; `all`, of every count, matched or not. With `days`, the objects of the rows in that many days to the
    newest's, added up by key."""
    if "days" in piece:
        first = _day(rows[-1]) - timedelta(days=piece["days"] - 1)
        found = [store.value(row, piece["field"]) for row in rows if _day(row) >= first]
        objects = [counts for counts in found if isinstance(counts, dict)]
        if not objects:
            return None
        numbers: dict[str, Any] = {}
        for counts in objects:
            for key, n in counts.items():
                if _number(n):
                    numbers[key] = numbers.get(key, 0) + n
    else:
        counts = store.value(rows[-1], piece["field"])
        if not isinstance(counts, dict):
            return None
        numbers = {key: n for key, n in counts.items() if _number(n)}
    everything = sum(numbers.values())
    items = [
        {"key": key, "count": n, "share": round(100 * n / everything) if everything else None}
        for key, n in sorted(numbers.items(), key=lambda item: -item[1])
    ]
    matched, shown, rest = _selected(items, piece)
    total, rest_total = sum(item["count"] for item in matched), sum(item["count"] for item in rest)
    return _Found({"found": len(matched), "total": total, "rest": rest_total, "all": everything}, shown)


_KINDS: dict[str, Callable[[Sequence[Row], Mapping[str, Any], Fields, _Style], _Found | None]] = {
    "value": _value_kind,
    "series": _series,
    "peak": _peak,
    "sum": _sum,
    "versions": _versions,
    "items": _items,
    "latest_release": _latest_release,
    "release_downloads": _release_downloads,
    "counts": _counts,
}


# ---- Tables --------------------------------------------------------------------------------------

_Group = Tuple[str, List[Row]]  # a table row's date, as shown, and the rows it stands for


def _table(folder: Folder, table: Mapping[str, Any]) -> list[str]:
    """The table: a header, then a row a day, or a week, of the `from` file's good rows; none when it has none."""
    rows = folder.rows(table["from"]).good
    if not rows:
        return []
    groups = _groups(rows, table.get("group", "day"), table.get("recent_weeks", 0))
    columns = table["columns"]
    others = {spec["from"] for column in columns for spec in (column, column.get("per", {})) if "from" in spec}
    joined = {name: {row["date"]: row for row in folder.rows(name).good} for name in others}
    cells = [_cells(folder, column, groups, joined) for column in columns]
    header = ["| " + " | ".join(_one_line(column["title"]) for column in columns) + " |", "|" + "---|" * len(columns)]
    return header + ["| " + " | ".join(row) + " |" for row in zip(*cells)]


def _groups(rows: Sequence[Row], group: str, recent_weeks: int) -> list[_Group]:
    """The table's rows: a row a day, or a row a week (weeks start on Monday), the newest `recent_weeks` a day."""
    if group == "day":
        return [(row["date"], [row]) for row in rows]
    weeks: dict[date, list[Row]] = {}
    for row in rows:
        day = _day(row)
        weeks.setdefault(day - timedelta(days=day.weekday()), []).append(row)
    recent = set(sorted(weeks)[-recent_weeks:]) if recent_weeks else set()
    found: list[_Group] = []
    for week in sorted(weeks):
        members = weeks[week]
        if week in recent:
            found += [(row["date"], [row]) for row in members]
        else:
            found.append((members[-1]["date"] + WEEK, members))
    return found


def _member_value(column: Mapping[str, Any], row: Row, joined: Mapping[str, Mapping[str, Row]]) -> Any:
    """A column's value for one row: its field, from the row or the row of its date in `from`, over `per`."""

    def at(spec: Mapping[str, Any]) -> Any:
        source = joined[spec["from"]].get(row["date"]) if "from" in spec else row
        return store.value(source, spec["field"])

    value = at(column)
    if "per" not in column or value is None:
        return value
    divisor = at(column["per"])
    return value / divisor if divisor else None


def _cells(
    folder: Folder, column: Mapping[str, Any], groups: Sequence[_Group], joined: Mapping[str, Mapping[str, Row]]
) -> list[str]:
    """The column's cell in each table row, from the aggregates of its values over the rows it stands for:
    its `aggregate` (bold where it says), or its text or parts; MISSING where that doesn't show."""
    if column["field"] == "date" and "from" not in column:
        contexts: list[Row] = [{"text": label} for label, _ in groups]
    else:
        values = [[_member_value(column, row, joined) for row in members] for _, members in groups]
        contexts = _aggregates(values, column)
    view = {key: column[key] for key in _VIEW if key in column}
    cells = [_render(folder, view, ChainMap(context), _PLAIN) for context in contexts]
    return [cell[0] if cell else MISSING for cell in cells]


def _aggregates(values: Sequence[Sequence[Any]], column: Mapping[str, Any]) -> list[Row]:
    """Each table row's fields: `last`, `change` (the last value less the table row before's, +0 for the first),
    `low`, `high` and `spread`, each where the values have it; and `text`, the cell the column's aggregate shows."""
    spec, aggregate = column.get("format", ""), column.get("aggregate", "last")
    found: list[Row] = []
    previous = None
    for group in values:
        last = group[-1]
        fields: Row = {"last": last}
        if last is not None:
            if _number(last) and (previous is None or _number(previous)):
                fields["change"] = last - (last if previous is None else previous)
            previous = last
        present = [value for value in group if value is not None]
        with contextlib.suppress(TypeError, ValueError):  # no values, or values that don't order
            fields.update(low=min(present), high=max(present))
        if _number(fields.get("low")) and _number(fields.get("high")):
            fields["spread"] = fields["high"] - fields["low"]
        text = _aggregate_text(fields, aggregate, spec)
        fields["text"] = f"**{text}**" if column.get("bold") and text != MISSING else text
        found.append(fields)
    return found


def _aggregate_text(fields: Row, aggregate: str, spec: str) -> str:
    """The cell an aggregate shows: `lo-hi` for a range, or the one value when they show the same."""
    if aggregate == "change":
        return _shown(fields.get("change"), "+" + spec)
    if aggregate == "range":
        if "low" not in fields:
            return MISSING
        low, high = _shown(fields["low"], spec), _shown(fields["high"], spec)
        return low if low == high else f"{low}-{high}"
    if aggregate == "versions":
        return MISSING if fields["last"] is None else format_versions(fields["last"])
    return _shown(fields["last"], spec)


# ---- Versions ------------------------------------------------------------------------------------


def format_versions(versions: Mapping[str, int]) -> str:
    """The counts by version, oldest first: each release; the newest prerelease; each older prerelease added
    into its base version and channel (`1.2.0-beta`), or shown as itself when alone. The counts add up to
    the total.
    """
    pre = {version: n for version, n in versions.items() if "-" in version}
    newest = max(pre, key=version_key) if pre else None
    entries = [(version, n) for version, n in versions.items() if "-" not in version or version == newest]
    buckets: dict[str, list[tuple[str, int]]] = {}
    for version, n in pre.items():
        if version != newest:
            base, _, tag = version.partition("-")
            buckets.setdefault(f"{base}-{tag.split('.', 1)[0]}", []).append((version, n))
    for label, members in buckets.items():
        entries.append(members[0] if len(members) == 1 else (label, sum(n for _, n in members)))
    return " ".join(f"**v{version}:** {n}" for version, n in sorted(entries, key=lambda e: version_key(e[0])))
