"""cloudflare_zone_traffic's code that makes requests and writes rows: collect, check; declarations are in spec.py."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from typing import Any, Dict

from leadline import store
from leadline.context import Context, Finding
from leadline.http import HttpError, Response
from leadline.sources.cloudflare.cloudflare_zone_traffic import spec
from leadline.storage import File

ENV: tuple[str, ...] = ("CLOUDFLARE_API_TOKEN",)
LAGGING = True  # yesterday's row is the newest a run can write
GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"
ATTEMPTS = 3
UNEXPECTED = "response had an unexpected shape"
NONE = "(none)"  # the name of a count whose item has none

Fields = Dict[str, Any]  # a row's fields, in the order the row has them
PerDay = Dict[str, Fields]  # each day's fields, by its date


class _FailureRowError(Exception):
    """The answer is a failure: `fields` are its row's status and reason."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(status)
        self.fields = {"status": status, "reason": reason}


class _UnreadableError(Exception):
    """The answer parses as JSON but not as the query's result; `why` names what was wrong, never a value."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


class _PartError(Exception):
    """A part of the per-request dataset that could not be read: `reason` names it, `error` is the request's, if any."""

    def __init__(self, reason: str, error: HttpError | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.error = error


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether the token can read each zone's analytics; writes nothing, never raises for the API.

    A token without Zone > Analytics > Read, or one for another account, reads every night as an error; a zone tag
    the token can't see reads as a zone that matched nothing.
    """
    day = ctx.date - timedelta(days=1)
    findings = []
    for entry in cfg:
        label, zone = entry["label"], entry["zone"]
        try:
            data = json.loads(_post(ctx, spec.probe_query(zone, day), attempts=1).body.decode("utf-8", "replace"))
            _zone(data)
        except HttpError as e:
            level = "error" if e.unfixable else "warn"
            findings.append(Finding(level, f"{label}: could not query the zone's analytics: HTTP {e.code}"))
        except _FailureRowError as e:
            findings.append(
                Finding("error", f"{label}: the token cannot read the zone's analytics: {e.fields['reason']}")
            )
        except (ValueError, _UnreadableError):
            findings.append(Finding("warn", f"{label}: could not read the zone's analytics answer"))
        else:
            findings.append(Finding("ok", f"{label}: the token can read the zone's analytics"))
    return findings


def _post(ctx: Context, query: str, attempts: int = ATTEMPTS) -> Response:
    """The query as {"query": ...}, tried up to `attempts` times while it fails or its body is empty."""
    body = json.dumps({"query": query}).encode("utf-8")
    headers = {"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}", "Content-Type": "application/json"}
    return ctx.http.request(GRAPHQL, method="POST", headers=headers, body=body, attempts=attempts, require_body=True)


# --- reading an answer ---


def _answer(response: Response) -> dict[str, Any]:
    """The zone the answer is about. Raises _FailureRowError (the API's own error, or no zone) or _UnreadableError."""
    try:
        data = json.loads(response.body.decode("utf-8", "surrogateescape"))
    except ValueError:
        raise _FailureRowError("parse_failed", "response was not JSON") from None
    return _zone(data)


def _zone(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise _UnreadableError("answer is not an object")
    errors = data.get("errors")
    if errors:
        first = errors[:3] if isinstance(errors, list) else None
        if first is None or not all(isinstance(e, dict) for e in first):
            raise _UnreadableError("errors is not a list of objects")
        raise _FailureRowError("graphql_error", "; ".join(str(e.get("message", e))[:200] for e in first))
    result = data.get("data")
    viewer = result.get("viewer") if isinstance(result, dict) else None
    zones = viewer.get("zones") if isinstance(viewer, dict) else None
    if not isinstance(zones, list):
        raise _UnreadableError("zones is not a list")
    if not zones:
        raise _FailureRowError("no_zone", "zone filter matched nothing")
    if not isinstance(zones[0], dict):
        raise _UnreadableError("zone is not an object")
    return zones[0]


def _object(value: Any, name: str) -> dict[str, Any]:
    """`value or {}`: a part (called `name`) that may be missing, which must then be an object."""
    if not value:
        return {}
    if not isinstance(value, dict):
        raise _UnreadableError(f"{name} is not an object")
    return value


def _groups(zone: dict[str, Any], alias: str) -> list[dict[str, Any]]:
    """The groups under `alias`: a list of objects, an absent one none."""
    found = zone.get(alias)
    if not found:
        return []
    if not isinstance(found, list) or not all(isinstance(group, dict) for group in found):
        raise _UnreadableError(f"{alias} is not a list of objects")
    return found


def _count(value: Any, name: str) -> int:
    """A count (called `name`): none is zero, a number is cut to a whole one; anything else (a boolean, a negative)
    is unreadable."""
    if not value:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise _UnreadableError(f"{name} is not a non-negative number")
    return int(value)


def _day(group: dict[str, Any]) -> str:
    """The group's date, as the API gives it (`YYYY-MM-DD`)."""
    day = _object(group.get("dimensions"), "dimensions").get("date")
    if not isinstance(day, str):
        raise _UnreadableError("group date is not YYYY-MM-DD")
    try:
        return date.fromisoformat(day).isoformat()
    except ValueError:
        raise _UnreadableError("group date is not YYYY-MM-DD") from None


def _name(value: Any) -> str:
    """The name of an item: its text, `(none)` for a count with none, a number as its digits."""
    if value is None or value == "":
        return NONE
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _UnreadableError("an item's name is not text or a number")
    return str(value)


def _ranked(counts: dict[str, int], top: int | None = None) -> dict[str, int]:
    """The counts, the largest first and the name deciding a tie, without zeros, cut to the first `top`."""
    ranked = sorted(((name, n) for name, n in counts.items() if n), key=lambda item: (-item[1], item[0]))
    return dict(ranked[:top])


def _items(value: Any, dimension: str, count: str) -> dict[str, int]:
    """A map of the daily dataset (`countryMap`): its items' `count`, by the name `dimension` gives each."""
    if not value:
        return {}
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise _UnreadableError("a map is not a list of objects")
    totals: dict[str, int] = {}
    for item in value:
        name = _name(item.get(dimension))
        totals[name] = totals.get(name, 0) + _count(item.get(count), count)
    return _ranked(totals)


def _read_daily(zone: dict[str, Any]) -> PerDay:
    """Each day's fields from the daily dataset, the last given for a day winning."""
    days: PerDay = {}
    for group in _groups(zone, "daily"):
        sums = _object(group.get("sum"), "sum")
        fields: Fields = {key: _count(sums.get(api), key) for key, api in spec.SCALARS}
        fields[spec.UNIQUES] = _count(_object(group.get("uniq"), "uniq").get("uniques"), spec.UNIQUES)
        for key, api_map, dimension, count in spec.MAPS:
            fields[key] = _items(sums.get(api_map), dimension, count)
        days[_day(group)] = fields
    return days


def _read_totals(zone: dict[str, Any]) -> PerDay:
    """Each day's visits and origin response times from the per-request dataset."""
    days: PerDay = {}
    for group in _groups(zone, "groups"):
        quantiles, averages = _object(group.get("quantiles"), "quantiles"), _object(group.get("avg"), "avg")
        found = {"avg": averages.get("originResponseDurationMs"), **quantiles}
        timing = {key: _timing(found.get(api)) for key, api in spec.ORIGIN_KEYS}
        days[_day(group)] = {
            spec.VISITS: _count(_object(group.get("sum"), "sum").get(spec.VISITS), spec.VISITS),
            spec.ORIGIN_MS: {key: ms for key, ms in timing.items() if ms is not None},
        }
    return days


def _timing(value: Any) -> int | None:
    """A response time in milliseconds, rounded; none where the API has none (null, or the -1 it gives a mean for a
    day without origin responses)."""
    if value is None or (not isinstance(value, bool) and value == -1):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise _UnreadableError("response time is not a non-negative number")
    return round(float(value))


def _read_list(zone: dict[str, Any], dimension: str, top: int) -> dict[str, dict[str, int]]:
    """Each day's top `top` values of `dimension` by request count."""
    counts: dict[str, dict[str, int]] = {}
    for group in _groups(zone, "groups"):
        name = _name(_object(group.get("dimensions"), "dimensions").get(dimension))
        day = counts.setdefault(_day(group), {})
        day[name] = day.get(name, 0) + _count(group.get("count"), "count")
    return {day: _ranked(found, top) for day, found in counts.items()}


# --- collecting ---


def _collect(ctx: Context, entry: Any) -> None:
    label, zone, top = entry["label"], entry["zone"], entry.get("top", spec.TOP)
    file = ctx.file(spec.file_name(entry))
    yesterday = ctx.date - timedelta(days=1)
    window_start = yesterday - timedelta(days=spec.WINDOW_DAYS - 1)
    existing = store.rows_by_date(file)
    # The year is read once: until the file holds a row older than the 28 days every run heals.
    older = any((parsed := store.parse_date(day)) is not None and parsed < window_start for day in existing)
    start = window_start if older else yesterday - timedelta(days=spec.HISTORY_DAYS - 1)
    ctx.log.info("Collecting Cloudflare zone traffic for %s (%s to %s)...", label, start, yesterday)
    try:
        daily = _read_daily(_answer(_post(ctx, spec.daily_query(zone, start, yesterday))))
    except HttpError as e:
        ctx.log.info("ERROR: Cloudflare %s failed (HTTP %s)", label, e.http_code)
        ctx.record_unfixable(spec.NAME, file.name, e, f"{label}: HTTP {e.http_code} from Cloudflare's GraphQL API")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": "graphql request failed"}
        _write_failure(ctx, file, {"date": yesterday.isoformat(), "zone": label, **failure})
        return
    except _FailureRowError as e:
        _write_failure(ctx, file, {"date": yesterday.isoformat(), "zone": label, **e.fields})
        return
    except _UnreadableError:
        fields = {"status": "parse_failed", "reason": UNEXPECTED}
        _write_failure(ctx, file, {"date": yesterday.isoformat(), "zone": label, **fields})
        return
    requests, failed = _read_requests(ctx, zone, label, file.name, window_start, yesterday, top)
    rows, partial = _rows(label, daily, requests, failed, existing, window_start, yesterday)
    if rows:
        ctx.log.info("Cloudflare %s: %d day(s) %s..%s", label, len(rows), rows[0]["date"], rows[-1]["date"])
        store.write_window(file, rows, ctx, spec.NAME)
    if partial is not None:
        store.write_partial(file, partial, failed, ctx, spec.NAME)


def _read_requests(
    ctx: Context, zone: str, label: str, file: str, start: date, end: date, top: int
) -> tuple[dict[str, PerDay], dict[str, list[str]]]:
    """The per-request dataset's parts for the days start to end: each part's days by the fields it gives, and
    what failed: the reason of each part that could not be read, mapped to the row fields it leaves null."""
    parts: dict[str, PerDay] = {}
    failed: dict[str, list[str]] = {}
    unfixable: HttpError | None = None
    queries: list[tuple[str, str | None, str, list[str]]] = [
        ("totals", None, spec.totals_query(zone, start, end), [*spec.TOTALS])
    ]
    queries += [
        (field, dimension, spec.list_query(zone, dimension, start, end), [field]) for field, dimension in spec.LISTS
    ]
    for name, dimension, query, fields in queries:
        try:
            parts[name] = _read_part(ctx, name, dimension, query, top)
        except _PartError as e:
            failed[e.reason] = fields
            if e.error is not None and e.error.unfixable and unfixable is None:
                unfixable = e.error
    if unfixable is not None:
        reason = f"{label}: HTTP {unfixable.http_code} from Cloudflare's GraphQL API"
        ctx.record_unfixable(spec.NAME, file, unfixable, reason)
    return parts, failed


def _read_part(ctx: Context, name: str, dimension: str | None, query: str, top: int) -> PerDay:
    """One part's days, or a _PartError that says why it could not be read."""
    try:
        answer = _answer(_post(ctx, query))
        if dimension is None:
            return _read_totals(answer)
        return {day: {name: counts} for day, counts in _read_list(answer, dimension, top).items()}
    except HttpError as e:
        ctx.log.info("WARN: Cloudflare %s request failed (HTTP %s)", name, e.http_code)
        raise _PartError(f"{name} request failed (HTTP {e.http_code})", e) from e
    except _FailureRowError as e:
        raise _PartError(f"{name} query failed: {e.fields['reason']}") from e
    except _UnreadableError as e:
        raise _PartError(f"{name} answer was unreadable: {e.why}") from None


def _zero() -> Fields:
    """A day with no requests: every total zero, every map empty."""
    fields: Fields = {key: 0 for key, _ in spec.SCALARS}
    fields[spec.UNIQUES] = 0
    fields.update({key: {} for key, *_ in spec.MAPS})
    return fields


def _rows(
    label: str,
    daily: PerDay,
    requests: dict[str, PerDay],
    failed: dict[str, list[str]],
    existing: dict[str, dict[str, Any]],
    window_start: date,
    yesterday: date,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """A row for each day from the first one the daily dataset has (or yesterday) to yesterday, and yesterday's row
    when a part of the per-request dataset failed (it goes through write_partial, with the other days' rows).

    A day with a good row keeps it: the daily totals are corrected to what the API says now, and a field of the
    per-request dataset is added where the row lacks it, never changed (Cloudflare samples it). A day with none, or
    a failed one, is written fresh, marked `backfilled` unless it is yesterday.
    """
    first = date.fromisoformat(min(daily)) if daily else yesterday
    left_out = {field for fields in failed.values() for field in fields}
    rows: list[dict[str, Any]] = []
    partial: dict[str, Any] | None = None
    for n in range((yesterday - first).days + 1):
        day = first + timedelta(days=n)
        iso = day.isoformat()
        totals = daily.get(iso) or _zero()
        extra = _requests_of(requests, left_out, day >= window_start, iso, failed if day == yesterday else {})
        old = existing.get(iso)
        if old is not None and store.is_good(old):
            row = {key: value for key, value in old.items() if key != "captured_at"}
            row.update(totals)
            for key, value in extra.items():
                if value is not None:
                    row.setdefault(key, value)
        else:
            row = {"date": iso, "zone": label, "status": "ok", **totals, **extra}
            if day != yesterday:
                row[store.BACKFILLED] = True
        if day == yesterday and any(value is None for value in extra.values()):
            row.update({key: None for key, value in extra.items() if value is None})  # store restores a stored value
            partial = row
        else:
            rows.append(row)
    return rows, partial


def _requests_of(
    requests: dict[str, PerDay], left_out: set[str], in_window: bool, iso: str, null: dict[str, list[str]]
) -> Fields:
    """The per-request fields of a day: those of each part read, in the row's order. Within the days it can tell
    (`in_window`), a part read with nothing for the day is empty; a part that failed is left out, or null for
    yesterday (`null` names its fields), whose row is then partial."""
    if not in_window:
        return {}
    nulls = {field for fields in null.values() for field in fields}
    fields: Fields = {}
    names = [*spec.TOTALS, *(field for field, _ in spec.LISTS)]
    for name in names:
        if name in nulls:
            fields[name] = None
        elif name in left_out:
            continue
        elif name == spec.VISITS or name == spec.ORIGIN_MS:
            found = requests.get("totals", {}).get(iso, {})
            fields[name] = found.get(name, 0 if name == spec.VISITS else {})
        else:
            fields[name] = requests.get(name, {}).get(iso, {}).get(name, {})
    return fields


def _write_failure(ctx: Context, file: File, row: dict[str, Any]) -> None:
    """The failure row, logged; store keeps a good row in its place."""
    ctx.log.info("Cloudflare %s result: %s", row["zone"], json.dumps(row))
    store.write_failure(file, row, ctx.now)
