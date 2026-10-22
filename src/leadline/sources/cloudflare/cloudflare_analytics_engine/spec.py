"""What it declares: its name, its file, its layouts and their checks, and its summary. No requests."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Pattern, Sequence

from leadline import config

NAME = "cloudflare_analytics_engine"
DEFAULT_FILE = config.default_file(NAME, "dataset")  # cloudflare-analytics-engine-{dataset}.jsonl
REFETCH_DAYS = 7  # the script's USAGE_REFETCH_DAYS
MAX_REFETCH_DAYS = 90  # Analytics Engine keeps three months; a day it no longer holds would be rewritten as zeros
# What a query may hold, each checked by validate before any query is built from it.
DATASET = re.compile(r"[a-z0-9_]+")  # the script's rule for a dataset's name
DIMENSION = re.compile(r"[a-z][a-z0-9_]*")  # a dimension's name, which is its column's alias in a query
# What a dimension's name (its column's alias in a query) may not be: the aliases the queries give the day and the
# total, and the names of columns a query reads, which an alias would shadow.
RESERVED = ("day", "n", "timestamp", "dataset")
READABLE = re.compile(r"index1|blob([1-9]|1[0-9]|20)")  # a column that holds a string
MEASURE = re.compile(r"double([1-9]|1[0-9]|20)")  # a column that holds a number
UNQUOTABLE = re.compile(r"['\\]")  # what a value put in a query can't hold
UNTIL = "%Y-%m-%dT%H:%M:%SZ"
ROW_KEYS = ("date", "dataset", "status", "http_code", "reason", "captured_at")  # a row's own keys: no field's


class _InvalidError(Exception):
    """A problem with an entry's layouts: `where` is its path in the entry."""

    def __init__(self, where: str, problem: str) -> None:
        super().__init__(f"{where}: {problem}")


@dataclass(frozen=True)
class Dimension:
    name: str
    column: str
    values: frozenset[str] | None  # its closed list of values, or
    pattern: Pattern[str] | None  # the pattern each whole value matches
    only_when: tuple[Dimension, tuple[str, ...]] | None = None  # set only where that one has those values

    def allows(self, value: str) -> bool:
        if self.values is not None:
            return value in self.values
        return self.pattern is not None and self.pattern.fullmatch(value) is not None


@dataclass(frozen=True)
class Field:
    """A row field: a total by a dimension (`by`, summing `sums` if given), or a count where `column` is set."""

    key: str
    by: Dimension | None = None
    sums: str | None = None
    column: str | None = None
    where: tuple[Dimension, str] | None = None


@dataclass(frozen=True)
class Layout:
    until: datetime | None  # the end of its time, None for the newest
    dimensions: tuple[Dimension, ...]
    fields: tuple[Field, ...]


KEYS = ("dataset", "layouts", "refetch_days")  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def _problems(entry: dict[str, Any]) -> list[str]:
    problems = []
    dataset = entry.get("dataset")
    if not isinstance(dataset, str) or not DATASET.fullmatch(dataset):
        problems.append("dataset: must be a name of lowercase letters, digits and _")
    days = entry.get("refetch_days", REFETCH_DAYS)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_REFETCH_DAYS:
        problems.append(f"refetch_days: must be a whole number of days, 1 to {MAX_REFETCH_DAYS}")
    try:
        layouts(entry.get("layouts"))
    except _InvalidError as e:
        problems.append(str(e))
    return problems


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


LABEL = "App usage"  # what trends heads its sections with


def summary(cfg: Any) -> list[config.Section]:
    return [config.Section(NAME, entry["dataset"], file_name(entry), _shown(entry), LABEL) for entry in cfg]


def _shown(entry: Any) -> list[config.Shown]:
    """Each count its layouts give, as a daily total; a total by a dimension, an object, isn't shown."""
    keys = [field.key for layout in layouts(entry["layouts"]) for field in layout.fields if field.by is None]
    return [config.Shown(key, key, "daily") for key in dict.fromkeys(keys)]


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, {"dataset": entry["dataset"]})


def layouts(value: Any) -> list[Layout]:
    """An entry's `layouts`, oldest first; raises _InvalidError at the first problem."""
    if not isinstance(value, list) or not value:
        raise _InvalidError("layouts", "must be a non-empty list of layouts, oldest first")
    layouts: list[Layout] = []
    kinds: dict[str, bool] = {}  # each key: whether it is a total, in every layout alike
    for i, spec in enumerate(value):
        at = f"layouts[{i}]"
        layout = _layout(at, spec, newest=i == len(value) - 1)
        before = layouts[-1].until if layouts else None
        if before is not None and layout.until is not None and layout.until <= before:
            raise _InvalidError(f"{at}.until", "must be later than the layout's before it")
        for j, field in enumerate(layout.fields):
            if kinds.setdefault(field.key, field.by is not None) != (field.by is not None):
                raise _InvalidError(
                    f"{at}.fields[{j}].key", f"{field.key} is a total in one layout and a count in another"
                )
        layouts.append(layout)
    return layouts


def _known(at: str, spec: dict[str, Any], keys: Sequence[str]) -> None:
    unknown = sorted(set(spec) - set(keys))
    if unknown:
        raise _InvalidError(at, f"unknown keys: {', '.join(unknown)}")


def _object(at: str, spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise _InvalidError(at, "must be an object")
    return spec


def _layout(at: str, spec: Any, newest: bool) -> Layout:
    spec = _object(at, spec)
    _known(at, spec, ("until", "dimensions", "private", "fields"))
    if newest and "until" in spec:
        raise _InvalidError(f"{at}.until", "must be left out: the newest layout has no end")
    until = None if newest else _until(f"{at}.until", spec.get("until"))
    private = spec.get("private", [])
    if not isinstance(private, list) or not all(isinstance(c, str) and READABLE.fullmatch(c) for c in private):
        raise _InvalidError(f"{at}.private", "must be a list of columns: index1 or blob1 to blob20")
    specs = spec.get("dimensions", {})
    if not isinstance(specs, dict):
        raise _InvalidError(f"{at}.dimensions", "must be an object of named dimensions")
    dimensions = {name: _dimension(f"{at}.dimensions.{name}", name, d, private) for name, d in specs.items()}
    for name, d in specs.items():
        if "only_when" in d:
            when = _only_when(f"{at}.dimensions.{name}.only_when", name, d["only_when"], dimensions)
            dimensions[name] = replace(dimensions[name], only_when=when)
    fields = spec.get("fields")
    if not isinstance(fields, list) or not fields:
        raise _InvalidError(f"{at}.fields", "must be a non-empty list of fields")
    read: list[Field] = []
    for j, f in enumerate(fields):
        field = _field(f"{at}.fields[{j}]", f, dimensions)
        if any(field.key == other.key for other in read):
            raise _InvalidError(f"{at}.fields[{j}].key", f"{field.key} is given twice")
        read.append(field)
    return Layout(until, tuple(dimensions.values()), tuple(read))


def _until(at: str, value: Any) -> datetime:
    """A layout's end: a UTC time, as `2026-09-25T12:00:00Z`, read as a naive UTC time (as the SQL API's are)."""
    try:
        until = datetime.strptime(value, UNTIL)  # noqa: DTZ007 (naive UTC, as the SQL API's times are)
    except (TypeError, ValueError):
        raise _InvalidError(at, "must be a UTC time, as 2026-09-25T12:00:00Z") from None
    if until.strftime(UNTIL) != value:
        raise _InvalidError(at, "must be a UTC time, as 2026-09-25T12:00:00Z")
    return until


def _dimension(at: str, name: str, spec: Any, private: list[str]) -> Dimension:
    if not DIMENSION.fullmatch(name) or name in RESERVED or READABLE.fullmatch(name) or MEASURE.fullmatch(name):
        raise _InvalidError(
            at,
            "a name is lowercase letters, digits and _, from a letter, and not day, n, timestamp, dataset or a "
            "column's name",
        )
    spec = _object(at, spec)
    _known(at, spec, ("column", "values", "pattern", "only_when"))
    column = spec.get("column")
    if not isinstance(column, str) or not READABLE.fullmatch(column):
        raise _InvalidError(f"{at}.column", "must be index1 or blob1 to blob20")
    if column in private:
        raise _InvalidError(f"{at}.column", f"{column} is private, and a private column is never read")
    if ("values" in spec) == ("pattern" in spec):
        raise _InvalidError(at, "must give values or a pattern, one of the two")
    if "values" in spec:
        values = spec["values"]
        if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
            raise _InvalidError(f"{at}.values", "must be a non-empty list of strings")
        return Dimension(name, column, frozenset(values), None)
    try:
        pattern = re.compile(spec["pattern"])
    except (TypeError, re.error):
        raise _InvalidError(f"{at}.pattern", "must be a regular expression") from None
    return Dimension(name, column, None, pattern)


def _only_when(at: str, name: str, spec: Any, dimensions: dict[str, Dimension]) -> tuple[Dimension, tuple[str, ...]]:
    """`{"<other>": [values]}`: the dimension is set only on events whose other one has one of those values."""
    other, values = next(iter(spec.items())) if isinstance(spec, dict) and len(spec) == 1 else (None, None)
    if (
        other == name
        or other not in dimensions
        or not isinstance(values, list)
        or not values
        or not all(isinstance(v, str) for v in values)
    ):
        raise _InvalidError(at, "must name another dimension and the values of it that allow this one")
    for value in values:
        if not dimensions[other].allows(value) or UNQUOTABLE.search(value):
            raise _InvalidError(at, f"{json.dumps(value)} is not a value {other} allows")
    return dimensions[other], tuple(values)


KINDS = "must give by (a total by a dimension) or count_nonempty (a count where a column is set), one of the two"


def _field(at: str, spec: Any, dimensions: dict[str, Dimension]) -> Field:
    spec = _object(at, spec)
    key = spec.get("key")
    if not isinstance(key, str) or not key or key in ROW_KEYS:
        raise _InvalidError(f"{at}.key", f"must be a string, not one of the row's own keys: {', '.join(ROW_KEYS)}")
    if ("by" in spec) == ("count_nonempty" in spec):
        raise _InvalidError(at, KINDS)
    if "by" in spec:
        _known(at, spec, ("key", "by", "sum"))
        by = spec["by"]
        if not isinstance(by, str) or by not in dimensions:
            raise _InvalidError(f"{at}.by", "names no dimension of the layout")
        sums = spec.get("sum")
        if "sum" in spec and not (isinstance(sums, str) and MEASURE.fullmatch(sums)):
            raise _InvalidError(f"{at}.sum", "must be double1 to double20")
        return Field(key, by=dimensions[by], sums=sums)
    _known(at, spec, ("key", "count_nonempty", "where"))
    column = spec["count_nonempty"]
    if not isinstance(column, str) or not READABLE.fullmatch(column):
        raise _InvalidError(f"{at}.count_nonempty", "must be index1 or blob1 to blob20")
    if "where" not in spec:
        return Field(key, column=column)
    where = spec["where"]
    dimension, value = next(iter(where.items())) if isinstance(where, dict) and len(where) == 1 else (None, None)
    if (
        dimension not in dimensions
        or not isinstance(value, str)
        or not dimensions[dimension].allows(value)
        or UNQUOTABLE.search(value)
    ):
        raise _InvalidError(f"{at}.where", "must name a dimension and one value it allows, without ' or \\")
    return Field(key, column=column, where=(dimensions[dimension], value))
