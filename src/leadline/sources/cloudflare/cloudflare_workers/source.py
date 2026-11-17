"""cloudflare_workers' code that makes requests and writes rows: collect, check; declarations are in spec.py."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from typing import Any, Dict

from leadline import store
from leadline.context import Context, Finding
from leadline.http import ATTEMPTS, HttpError, Response
from leadline.sources.cloudflare.cloudflare_workers import spec

ENV: tuple[str, ...] = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID")
LAGGING = True  # yesterday's row is the newest a run can write
GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"
NOT_JSON = "response was not JSON"
UNEXPECTED = "response had an unexpected shape"

Fields = Dict[str, Any]  # a day's fields, in the order the row has them
PerDay = Dict[str, Fields]  # each day's fields, by its date


class _FailureRowError(Exception):
    """The answer is a failure: `fields` are its row's status and reason."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(status)
        self.fields = {"status": status, "reason": reason}


class _UnreadableError(Exception):
    """The answer parses as JSON but not as the query's result."""


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether the token can read each Worker's metrics, and the Worker had invocations in the last 7 days; writes
    nothing, never raises for the API. A misspelled script name reads as a Worker with no invocations every night."""
    yesterday = ctx.date - timedelta(days=1)
    start = yesterday - timedelta(days=spec.CHECK_DAYS - 1)
    findings = []
    for entry in cfg:
        script = entry["script"]
        query = spec.query(os.environ["CLOUDFLARE_ACCOUNT_ID"], script, start, yesterday)
        try:
            response = _post(ctx, query, attempts=1)
            days = _read(_account(json.loads(response.body.decode("utf-8", "replace"))), start, yesterday)
        except HttpError as e:
            level = "error" if e.unfixable else "warn"
            findings.append(Finding(level, f"{script}: could not query the Workers metrics: HTTP {e.code}"))
            continue
        except _FailureRowError as e:
            reason = e.fields["reason"]
            findings.append(
                Finding("error", f"{script}: the token cannot read the account's Workers metrics: {reason}")
            )
            continue
        except (ValueError, _UnreadableError):
            findings.append(Finding("warn", f"{script}: could not read the Workers metrics answer"))
            continue
        invocations = sum(fields["requests"] for fields in days.values())
        if invocations:
            findings.append(Finding("ok", f"{script}: {invocations} invocations in {spec.CHECK_DAYS} days"))
        else:
            message = f"{script}: no invocations in {spec.CHECK_DAYS} days; is it the Worker's script name?"
            findings.append(Finding("warn", message))
    return findings


def _post(ctx: Context, query: str, attempts: int = ATTEMPTS) -> Response:
    """The query as {"query": ...}, tried up to `attempts` times while it fails or its body is empty."""
    body = json.dumps({"query": query}).encode("utf-8")
    headers = {"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}", "Content-Type": "application/json"}
    return ctx.http.request(GRAPHQL, method="POST", headers=headers, body=body, attempts=attempts, require_body=True)


# --- collecting ---


def _collect(ctx: Context, entry: Any) -> None:
    script, file = entry["script"], ctx.file(spec.file_name(entry))
    yesterday = ctx.date - timedelta(days=1)
    window_start = yesterday - timedelta(days=spec.WINDOW_DAYS - 1)
    # Three months are read once: until the file holds a row older than the 28 days every run rewrites.
    older = any(
        (parsed := store.parse_date(day)) is not None and parsed < window_start for day in store.rows_by_date(file)
    )
    start = window_start if older else yesterday - timedelta(days=spec.HISTORY_DAYS - 1)
    ctx.log.info("Collecting Cloudflare Workers metrics for %s (%s to %s)...", script, start, yesterday)
    head = {"date": yesterday.isoformat(), "script": script}  # a failure row's
    try:
        days: PerDay = {}
        for first in range(0, (yesterday - start).days + 1, spec.WINDOW_DAYS):  # 28 days a query, oldest first
            span = (start + timedelta(days=first), start + timedelta(days=first + spec.WINDOW_DAYS - 1))
            response = _post(ctx, spec.query(os.environ["CLOUDFLARE_ACCOUNT_ID"], script, *span))
            days.update(_read(_answer(response), *span))
    except HttpError as e:
        ctx.log.info("ERROR: Cloudflare Workers %s failed (HTTP %s)", script, e.http_code)
        ctx.record_unfixable(spec.NAME, file.name, e, f"{script}: HTTP {e.http_code} from Cloudflare's GraphQL API")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": e.failed("graphql")}
    except _FailureRowError as e:
        failure = e.fields
    except _UnreadableError:
        failure = {"status": "parse_failed", "reason": UNEXPECTED}
    else:
        # From the first day the answers have, or the 28 days' first, to yesterday.
        first_day = min([window_start, *(date.fromisoformat(day) for day in days)])
        dates = [(first_day + timedelta(days=n)).isoformat() for n in range((yesterday - first_day).days + 1)]
        rows = [{"date": day, "script": script, "status": "ok", **days.get(day, _zero())} for day in dates]
        store.write_window(file, rows, ctx, spec.NAME)
        return
    row = {**head, **failure}
    ctx.log.info("Cloudflare Workers %s result: %s", script, json.dumps(row))
    store.write_failure(file, row, ctx.now)  # kept out where a good row holds the date


def _zero() -> Fields:
    """A day with no invocations."""
    return {**{key: 0 for key, _ in spec.SUMS}, spec.OUTCOMES: {}, spec.CPU: {}}


# --- reading an answer ---


def _answer(response: Response) -> dict[str, Any]:
    """The account the answer is about. Raises _FailureRowError (not JSON, the API's own error, no account) or
    _UnreadableError."""
    try:
        data = json.loads(response.body.decode("utf-8", "surrogateescape"))
    except ValueError:
        raise _FailureRowError("parse_failed", NOT_JSON) from None
    return _account(data)


def _account(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise _UnreadableError
    errors = data.get("errors")
    if errors:
        first = errors[:3] if isinstance(errors, list) else None
        if first is None or not all(isinstance(e, dict) for e in first):
            raise _UnreadableError
        raise _FailureRowError("graphql_error", "; ".join(str(e.get("message", e))[:200] for e in first))
    result = data.get("data")
    viewer = result.get("viewer") if isinstance(result, dict) else None
    accounts = viewer.get("accounts") if isinstance(viewer, dict) else None
    if not isinstance(accounts, list):
        raise _UnreadableError
    if not accounts:
        raise _FailureRowError("no_account", "account filter matched nothing")
    if not isinstance(accounts[0], dict):
        raise _UnreadableError
    return accounts[0]


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _UnreadableError
    return value


def _groups(account: dict[str, Any], alias: str) -> list[dict[str, Any]]:
    """The groups under `alias`: a list of objects, an absent one none."""
    found = account.get(alias)
    if found is None:
        return []
    if not isinstance(found, list) or not all(isinstance(group, dict) for group in found):
        raise _UnreadableError
    return found


def _number(value: Any) -> float:
    """A number of nothing or more; anything else (a boolean, text, null, a negative) is unreadable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise _UnreadableError
    return float(value)


def _day(group: dict[str, Any]) -> str:
    """The group's date, as the API gives it (`YYYY-MM-DD`)."""
    day = _object(group.get("dimensions")).get("date")
    if not isinstance(day, str):
        raise _UnreadableError
    try:
        return date.fromisoformat(day).isoformat()
    except ValueError:
        raise _UnreadableError from None


def _read(account: dict[str, Any], start: date, end: date) -> PerDay:
    """Each day's fields from start to end that the answer has: its sums, outcomes and CPU time. A day outside them
    is left out; the last daily group given for a day wins, and an outcome given twice for a day is added up."""
    days: PerDay = {}
    first, last = start.isoformat(), end.isoformat()
    for group in _groups(account, "daily"):
        day, sums = _day(group), _object(group.get("sum"))
        fields: Fields = {key: int(_number(sums.get(api))) for key, api in spec.SUMS}
        quantiles = _object(group.get("quantiles"))
        cpu = {key: round(_number(quantiles.get(api))) for key, api in spec.QUANTILES}
        fields[spec.CPU] = cpu if fields["requests"] else {}
        if first <= day <= last:
            days[day] = fields
    outcomes: dict[str, dict[str, int]] = {}
    for group in _groups(account, "outcomes"):
        day, status = _day(group), group["dimensions"].get("status")
        if not isinstance(status, str) or not status:
            raise _UnreadableError
        counts = outcomes.setdefault(day, {})
        counts[status] = counts.get(status, 0) + int(_number(_object(group.get("sum")).get("requests")))
    for day, counts in outcomes.items():
        if first <= day <= last:
            ranked = sorted(((name, n) for name, n in counts.items() if n), key=lambda item: (-item[1], item[0]))
            days.setdefault(day, _zero())[spec.OUTCOMES] = dict(ranked)
    return {day: {**_zero(), **fields} for day, fields in days.items()}
