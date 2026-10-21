"""The code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict
from urllib.parse import quote

from leadline import store
from leadline.context import Context, Finding
from leadline.http import HttpError, Response
from leadline.sources.cloudflare.cloudflare_analytics_engine import spec

ENV: tuple[str, ...] = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID")
LAGGING = True
API = "https://api.cloudflare.com/client/v4"
ATTEMPTS = 3
REASON_CHARS = 200  # of an error's body, as a failure row's reason

SQL_TIME = "%Y-%m-%d %H:%M:%S"

NOT_JSON = "response was not JSON"
NO_DATA = "response had no data array"
UNEXPECTED = "response had an unexpected shape"


class _UnreadableError(Exception):
    """An answer it can't read: `status`, the answer's HTTP status, and `reason` are its failure row's."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class _Query:
    """One query of a read: a field's, or an `only_when` check of a dimension (`check`), over [start, end)."""

    sql: str
    start: datetime
    end: datetime
    field: spec.Field | None = None
    check: spec.Dimension | None = None


Totals = Dict[str, Dict[str, Any]]  # each day's fields: a total's values and their counts, or a count


# --- collecting ---


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def _collect(ctx: Context, entry: Any) -> None:
    dataset, path = entry["dataset"], ctx.out_dir / spec.file_name(entry)
    first = ctx.date - timedelta(days=entry.get("refetch_days", spec.REFETCH_DAYS))
    yesterday = ctx.date - timedelta(days=1)
    ctx.log.info("Collecting Analytics Engine dataset %s...", dataset)
    queries = _queries(dataset, spec.layouts(entry["layouts"]), _midnight(first), _midnight(ctx.date))
    try:
        answers = [(query, _read(_post(ctx, query.sql))) for query in queries]
        days, outside = _totals(answers, first, yesterday)
    except HttpError as e:
        # The script's http_code: the status as it is, "0" for no response.
        ctx.record_unfixable(spec.NAME, path.name, e, f"{dataset}: HTTP {e.code} from Analytics Engine's SQL API")
        failure = ("fetch_failed", str(e.code), e.reason[:REASON_CHARS])
    except _UnreadableError as e:
        failure = ("parse_failed", str(e.status), e.reason)
    else:
        rows = [_row(dataset, day, days.get(day, {}), queries) for day in _dates(first, yesterday)]
        store.write_window(path, rows, ctx, spec.NAME)
        ctx.log.info("Analytics Engine %s: %d day(s) %s..%s", dataset, len(rows), first, yesterday)
        for reason in outside:
            ctx.record_critical(spec.NAME, path.name, f"{dataset}: {reason}")
        return
    status, http_code, reason = failure
    ctx.log.info("ERROR: Analytics Engine %s: %s (HTTP %s): %s", dataset, status, http_code, reason)
    row = {"date": yesterday.isoformat(), "dataset": dataset, "status": status, "http_code": http_code}
    store.write_window(path, [{**row, "reason": reason}], ctx, spec.NAME)  # kept off a good row by store


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time())


def _dates(first: date, last: date) -> list[str]:
    return [(first + timedelta(days=n)).isoformat() for n in range((last - first).days + 1)]


def _window(start: datetime, end: datetime) -> str:
    return f"timestamp >= toDateTime('{start:{SQL_TIME}}') AND timestamp < toDateTime('{end:{SQL_TIME}}')"


def _queries(dataset: str, layouts: list[spec.Layout], start: datetime, end: datetime) -> list[_Query]:
    """Each layout's queries over its part of [start, end): each field's, then each `only_when` check.

    The script's layout gives the script's two queries, byte for byte. A private column is only ever
    tested for being set, and no query selects or groups by more than the day and one dimension.
    """
    queries: list[_Query] = []
    begins = start
    for layout in layouts:
        ends = min(end, layout.until) if layout.until is not None else end
        if begins < ends:
            window = _window(begins, ends)
            queries += [_Query(_field_sql(dataset, window, f), begins, ends, field=f) for f in layout.fields]
            queries += [
                _Query(_check_sql(dataset, window, d), begins, ends, check=d)
                for d in layout.dimensions
                if d.only_when is not None
            ]
        if layout.until is not None:
            begins = max(begins, layout.until)
    return queries


def _count_sql(dataset: str, where: str) -> str:
    """The events a day where `where` holds: no column selected."""
    return (
        f"SELECT toStartOfDay(timestamp) AS day, SUM(_sample_interval) AS n "  # noqa: S608 (validate checks each part)
        f"FROM {dataset} WHERE {where} GROUP BY day FORMAT JSON"
    )


def _field_sql(dataset: str, window: str, field: spec.Field) -> str:
    """A count's query, or a total's: the day, one dimension and the total, only where `only_when` allows it."""
    if field.by is None:
        where = f"{field.where[0].column} = '{field.where[1]}' AND " if field.where is not None else ""
        return _count_sql(dataset, f"{window} AND {where}{field.column} != ''")
    dimension = field.by
    measure = f"_sample_interval * {field.sums}" if field.sums is not None else "_sample_interval"
    if dimension.only_when is not None:
        other, values = dimension.only_when
        tests = [f"{other.column} = '{value}'" for value in values]
        window += " AND " + (tests[0] if len(tests) == 1 else f"({' OR '.join(tests)})")
    return (
        f"SELECT toStartOfDay(timestamp) AS day, {dimension.column} AS {dimension.name}, "  # noqa: S608 (validated)
        f"SUM({measure}) AS n FROM {dataset} WHERE {window} GROUP BY day, {dimension.name} FORMAT JSON"
    )


def _check_sql(dataset: str, window: str, dimension: spec.Dimension) -> str:
    """The layout check of `only_when`: the events that set the dimension where the other one doesn't allow it."""
    other, values = dimension.only_when or (dimension, ())
    unset = "".join(f" AND {other.column} != '{value}'" for value in values)
    return _count_sql(dataset, f"{window}{unset} AND {dimension.column} != ''")


def _post(ctx: Context, sql: str, attempts: int = ATTEMPTS) -> Response:
    """The script's request: the query as the body, tried up to three times while it fails."""
    account = quote(os.environ["CLOUDFLARE_ACCOUNT_ID"], safe="")
    headers = {"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}"}
    url = f"{API}/accounts/{account}/analytics_engine/sql"
    return ctx.http.request(url, method="POST", headers=headers, body=sql.encode("utf-8"), attempts=attempts)


def _read(response: Response) -> tuple[int, list[Any]]:
    """The answer's status and its result rows, as the script reads them. Raises _UnreadableError."""
    try:
        answer = json.loads(response.body.decode("utf-8", "replace"))
    except ValueError:
        raise _UnreadableError(response.status, NOT_JSON) from None
    data = answer.get("data") if isinstance(answer, dict) else None
    if not isinstance(data, list):
        raise _UnreadableError(response.status, NO_DATA)
    return response.status, data


def _count(value: Any) -> int:
    """A total as the script reads it: a number, or one quoted (UInt64), cut to a whole one."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(UNEXPECTED)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(UNEXPECTED)
    return int(number)


def _result(row: Any, value: str | None) -> tuple[str, str | None, int]:
    """A result row's day (`YYYY-MM-DD`), its dimension's value (named `value`, if any) and total."""
    if not isinstance(row, dict) or not isinstance(row.get("day"), str):
        raise ValueError(UNEXPECTED)
    got = row.get(value) if value is not None else None
    if value is not None and not isinstance(got, str):
        raise ValueError(UNEXPECTED)
    return row["day"][:10], got, _count(row.get("n"))


def _totals(answers: list[tuple[_Query, tuple[int, list[Any]]]], first: date, last: date) -> tuple[Totals, list[str]]:
    """Each day's fields from the answers, and what lay outside the layout, as a critical reason each.

    Within one answer, a value given twice for a day keeps the last, as the script reads it; the answers
    of two layouts for one day are added up. A value outside its dimension's layout is left out and
    only counted; an `only_when` check's answer is the events that set its dimension where they may not.
    """
    window = set(_dates(first, last))
    days: Totals = {}
    outside: dict[str, set[str]] = {}  # each dimension's values outside the layout: counted, never shown
    unset: list[str] = []  # each `only_when` check that found events
    for query, (status, data) in answers:
        dimension = query.field.by if query.field is not None else None
        read: dict[str, Any] = {}
        try:
            for row in data:
                day, value, n = _result(row, dimension.name if dimension is not None else None)
                if query.check is not None:
                    read[day] = read.get(day, 0) + n  # every event the check finds counts
                elif dimension is None:
                    read[day] = n
                elif value is not None and not dimension.allows(value):
                    outside.setdefault(dimension.name, set()).add(value)
                else:
                    read.setdefault(day, {})[value] = n
        except ValueError:
            raise _UnreadableError(status, UNEXPECTED) from None
        if query.field is not None:
            _add(days, window, query.field, read)
        elif query.check is not None and query.check.only_when is not None and sum(read.values()):
            other, values = query.check.only_when
            where = f"{other.name} is not {' or '.join(values)}"
            unset.append(
                f"{sum(read.values())} event(s) set {query.check.name} where {where}, outside the layout; never read"
            )
    reasons = [f"{len(v)} {name} value(s) outside the layout, left out of the rows" for name, v in outside.items()]
    return days, reasons + unset


def _add(days: Totals, window: set[str], field: spec.Field, read: dict[str, Any]) -> None:
    """Add one answer's reading of `field` to each day's."""
    for day, got in read.items():
        if day not in window:
            continue
        fields = days.setdefault(day, {})
        if field.by is None:
            fields[field.key] = fields.get(field.key, 0) + got
        else:
            totals = fields.setdefault(field.key, {})
            for value, n in got.items():
                totals[value] = totals.get(value, 0) + n


def _row(dataset: str, day: str, got: dict[str, Any], queries: list[_Query]) -> dict[str, Any]:
    """The day's row: the fields of every layout read for the day, in the layouts' order, zero where none."""
    start = _midnight(date.fromisoformat(day))
    end = start + timedelta(days=1)
    row: dict[str, Any] = {"date": day, "dataset": dataset, "status": "ok"}
    for query in queries:
        field = query.field
        if field is None or field.key in row or not (query.start < end and start < query.end):
            continue
        if field.by is None:
            row[field.key] = got.get(field.key, 0)
        else:
            row[field.key] = dict(sorted(got.get(field.key, {}).items()))
    return row


# --- check ---


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether each entry's dataset answers, with events in its window; writes nothing, never raises for the API.

    A misspelled dataset reads as no events every night. The query selects no column but the total.
    """
    findings = []
    for entry in cfg:
        dataset, days = entry["dataset"], entry.get("refetch_days", spec.REFETCH_DAYS)
        window = _window(_midnight(ctx.date - timedelta(days=days)), _midnight(ctx.date))
        sql = f"SELECT SUM(_sample_interval) AS n FROM {dataset} WHERE {window} FORMAT JSON"  # noqa: S608 (validated)
        try:
            status, data = _read(_post(ctx, sql, attempts=1))
            if not all(isinstance(row, dict) for row in data):
                raise _UnreadableError(status, UNEXPECTED)
            try:
                events = sum(_count(row.get("n")) for row in data)
            except ValueError:
                raise _UnreadableError(status, UNEXPECTED) from None
        except HttpError as e:
            level = "error" if e.unfixable else "warn"
            findings.append(Finding(level, f"{dataset}: could not query the dataset: HTTP {e.code}"))
            continue
        except _UnreadableError as e:
            findings.append(Finding("warn", f"{dataset}: could not read the answer: {e.reason}"))
            continue
        if events:
            findings.append(Finding("ok", f"{dataset}: the dataset has events in the last {days} days"))
        else:
            findings.append(
                Finding("warn", f"{dataset}: no events in the last {days} days; is the dataset's name right?")
            )
    return findings
