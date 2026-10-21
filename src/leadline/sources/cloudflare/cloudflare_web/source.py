"""cloudflare_web's code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import quote

from leadline import store
from leadline.context import Context, Finding
from leadline.http import HttpError, Response
from leadline.sources.cloudflare.cloudflare_web import spec

ENV: tuple[str, ...] = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID")
LAGGING = True
API = "https://api.cloudflare.com/client/v4"
GRAPHQL = f"{API}/graphql"
ATTEMPTS = 3
WEEK_DAYS = 7  # the week the row for yesterday sums, and its top referrers
HEAL_DAYS = 30  # the complete days re-read every run, yesterday the last (the script's CLOUDFLARE_HEAL_DAYS)
NUMBERS = ("pageviews", "visits")  # a day's numbers: what a correction compares
UNEXPECTED = "response had an unexpected shape"

# The script's query, byte for byte (values inlined: Cloudflare's schema spells its scalars in lower
# case, so a variable declaration that guesses the type is rejected).
QUERY = """
{
  viewer {
    accounts(filter: {accountTag: "%s"}) {
      daily: rumPageloadEventsAdaptiveGroups(
        filter: {date_geq: "%s", date_leq: "%s", siteTag: "%s"}
        limit: 100
        orderBy: [date_ASC]
      ) {
        count
        sum { visits }
        dimensions { date }
      }
      referrers: rumPageloadEventsAdaptiveGroups(
        filter: {date_geq: "%s", date_leq: "%s", siteTag: "%s"}
        limit: 10
        orderBy: [count_DESC]
      ) {
        count
        dimensions { refererHost }
      }
    }
  }
}
"""


class _FailureRowError(Exception):
    """The answer is a failure: `fields` are its row's status and reason, as the script writes them."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(status)
        self.fields = {"status": status, "reason": reason}


class _UnreadableError(Exception):
    """The answer parses as JSON but not as the query's result."""


Days = Dict[str, Tuple[Any, Any]]  # each day's page views and visits, the last given for a day winning


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Each entry's `site_tag` against the account's Web Analytics sites; writes nothing, never raises for the API.

    The GraphQL query needs a site's tag. Its beacon token, from the snippet on the site's pages,
    looks the same and matches nothing, so every night would read as zero traffic.
    """
    try:
        sites = _sites(ctx)
    except HttpError as e:
        level = "error" if e.unfixable else "warn"
        trouble = f"could not list the account's Web Analytics sites: HTTP {e.code}"
        return [Finding(level, f"{entry['label']}: {trouble}") for entry in cfg]
    except _UnreadableError as e:
        trouble = f"could not read the account's Web Analytics sites: {e}"
        return [Finding("warn", f"{entry['label']}: {trouble}") for entry in cfg]
    findings = []
    for entry in cfg:
        label, given = entry["label"], entry["site_tag"]
        tagged = [site for site in sites if site.get("site_tag") == given]
        tokened = [site for site in sites if site.get("site_token") == given]
        if tagged:
            findings.append(Finding("ok", f"{label}: site tag {given} is a Web Analytics site in the account"))
        elif tokened:
            tag = tokened[0].get("site_tag")
            message = f"{label}: the configured site_tag {given} is the site's beacon token; its site tag is {tag}"
            findings.append(Finding("error", message))
        else:
            unknown = "no Web Analytics site in the account has it as its site tag or beacon token"
            findings.append(Finding("error", f"{label}: unknown site tag {given}: {unknown}"))
    return findings


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}"}


def _sites(ctx: Context) -> list[dict[str, Any]]:
    """Every Web Analytics site in the account, from each page of the site list.

    https://developers.cloudflare.com/api/resources/rum/subresources/site_info/methods/list/
    """
    account = quote(os.environ["CLOUDFLARE_ACCOUNT_ID"], safe="")
    sites: list[dict[str, Any]] = []
    page = 1
    while True:
        response = ctx.http.request(f"{API}/accounts/{account}/rum/site_info/list?page={page}", headers=_auth())
        try:
            body = response.json()
        except ValueError as e:
            raise _UnreadableError("not JSON") from e
        result = body.get("result") if isinstance(body, dict) and body.get("success") is True else None
        if not isinstance(result, list) or not all(isinstance(site, dict) for site in result):
            raise _UnreadableError("unexpected answer")
        sites += result
        info = body.get("result_info")
        total = info.get("total_count") if isinstance(info, dict) else None
        if not result or not isinstance(total, int) or len(sites) >= total:
            return sites
        page += 1


def _collect(ctx: Context, entry: Any) -> None:
    label, tag, path = entry["label"], entry["site_tag"], ctx.out_dir / spec.file_name(entry)
    yesterday = (ctx.date - timedelta(days=1)).isoformat()
    week_ago = (ctx.date - timedelta(days=WEEK_DAYS)).isoformat()
    heal_start = ctx.date - timedelta(days=HEAL_DAYS)
    account = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    query = QUERY % (account, heal_start.isoformat(), yesterday, tag, week_ago, yesterday, tag)
    ctx.log.info("Collecting Cloudflare Web Analytics for %s...", label)
    try:
        days, row = _read(_post(ctx, query).body, label, tag, week_ago, yesterday)
    except HttpError as e:
        ctx.log.info("ERROR: Cloudflare %s failed (HTTP %s)", label, e.http_code)
        ctx.record_unfixable(spec.NAME, path.name, e, f"{label}: HTTP {e.http_code} from Cloudflare's GraphQL API")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": "graphql request failed"}
        _write_failure(ctx, path, {"date": yesterday, "site": label, **failure})
        return
    except _FailureRowError as e:
        _write_failure(ctx, path, {"date": yesterday, "site": label, **e.fields})
        return
    past = [(heal_start + timedelta(days=n)).isoformat() for n in range(HEAL_DAYS - 1)]  # oldest first
    _heal(ctx, path, label, tag, past, days)
    _write_yesterday(ctx, path, row)


def _post(ctx: Context, query: str) -> Response:
    """The script's request: the query as {"query": ...}, three attempts while it fails or its body is empty."""
    body = json.dumps({"query": query}).encode("utf-8")
    headers = {**_auth(), "Content-Type": "application/json"}
    return ctx.http.request(GRAPHQL, method="POST", headers=headers, body=body, attempts=ATTEMPTS, require_body=True)


def _read(body: bytes, label: str, tag: str, week_ago: str, yesterday: str) -> tuple[Days, dict[str, Any]]:
    """The answer as the script reads it: each day's numbers and yesterday's row. Raises _FailureRowError."""
    try:
        data = json.loads(body.decode("utf-8", "surrogateescape"))
    except ValueError:
        raise _FailureRowError("parse_failed", "response was not JSON") from None
    try:
        return _result(data, label, tag, week_ago, yesterday)
    except _UnreadableError:
        raise _FailureRowError("parse_failed", UNEXPECTED) from None


def _object(value: Any) -> dict[str, Any]:
    """`value or {}`, as the script reads a part that may be missing, which must then be an object."""
    if not value:
        return {}
    if not isinstance(value, dict):
        raise _UnreadableError
    return value


def _list(value: Any) -> list[Any]:
    """`value or []`, as the script reads a list that may be missing, which must then be a list of objects."""
    if not value:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise _UnreadableError
    return value


def _number(value: Any) -> Any:
    """`value or 0`, which the script adds up, so it must then be a number."""
    if not value:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _UnreadableError
    return value


def _errors(data: dict[str, Any]) -> None:
    """GraphQL's own errors, inside a 200: the script's graphql_error, reading the first three."""
    errors = data.get("errors")
    if not errors:
        return
    first = errors[:3] if isinstance(errors, list) else None
    if first is None or not all(isinstance(e, dict) for e in first):
        raise _UnreadableError
    raise _FailureRowError("graphql_error", "; ".join(str(e.get("message", e))[:200] for e in first))


def _account(data: Any) -> dict[str, Any]:
    """The one account the query asks for.

    Only an empty list of accounts is an account that matched nothing; any other answer it can't read
    is `parse_failed` (ADR: a failure is recorded as a failure).
    """
    if not isinstance(data, dict):
        raise _UnreadableError
    _errors(data)
    result = data.get("data")
    if not isinstance(result, dict) or not isinstance(result.get("viewer"), dict):
        raise _UnreadableError
    accounts = result["viewer"].get("accounts")
    if not isinstance(accounts, list):
        raise _UnreadableError
    if not accounts:
        raise _FailureRowError("no_account", "account filter matched nothing")
    if not isinstance(accounts[0], dict):
        raise _UnreadableError
    return accounts[0]


def _result(data: Any, label: str, tag: str, week_ago: str, yesterday: str) -> tuple[Days, dict[str, Any]]:
    account = _account(data)
    days: Days = {}
    for group in _list(account.get("daily")):
        day = _object(group.get("dimensions")).get("date")
        if not day:
            continue  # the script leaves out a group without a date
        if not isinstance(day, str):
            raise _UnreadableError  # the script can't compare it with a date, and writes nothing
        days[day] = (_number(group.get("count")), _number(_object(group.get("sum")).get("visits")))
    # An empty refererHost is direct traffic, labeled so, as the script does.
    top = []
    for group in _list(account.get("referrers")):
        host = _object(group.get("dimensions")).get("refererHost") or ""
        top.append({"ref": host if host else "(direct)", "count": group.get("count") or 0})
    week = [numbers for day, numbers in days.items() if week_ago <= day <= yesterday]
    day_pv, day_visits = days.get(yesterday, (0, 0))
    row = {
        "date": yesterday,
        "site": label,
        "site_tag": tag,
        "status": "ok",
        "pageviews": day_pv,
        "visits": day_visits,
        "window_start": week_ago,
        "window_end": yesterday,
        "window_pageviews": sum(pv for pv, _ in week),
        "window_visits": sum(visits for _, visits in week),
        "days_returned": len(week),
        "top_referrers": top,
    }
    return days, row


def _numbers(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in NUMBERS}


def _heal(ctx: Context, path: Path, label: str, tag: str, past: list[str], days: Days) -> None:
    """The 30 days before yesterday: fill as the script does, and correct the changed (ADR: cloudflare_web
    re-reads and corrects its last 30 days).

    Only days from the file's first row on count: before it, the series hadn't begun. A day with no row,
    a failed row, or a row read under another site tag (or none, from before rows carried one) is
    filled, marked `backfilled`; a good row read under this tag whose numbers changed gets the new ones,
    its other fields kept. A day the answer leaves out had no page loads, as the script reads yesterday.
    """
    existing = store.rows_by_date(path)
    first = min(existing, default=None)
    for day in past:
        if first is None or day < first:
            continue
        pv, visits = days.get(day, (0, 0))
        new = {"pageviews": pv, "visits": visits}
        old = existing.get(day)
        if old is None or old.get("status") != "ok" or old.get("site_tag") != tag:
            filled = {"date": day, "site": label, "site_tag": tag, "status": "ok", **new, store.BACKFILLED: True}
            ctx.log.info("Cloudflare %s backfill: %s", label, json.dumps(filled))
            store.write_good(path, filled, ctx, spec.NAME)
        elif _numbers(old) != new:  # a correction, which store records
            kept = {key: value for key, value in old.items() if key != "captured_at"}
            store.write_good(path, {**kept, **new}, ctx, spec.NAME)


def _write_yesterday(ctx: Context, path: Path, row: dict[str, Any]) -> None:
    """Yesterday's row, always rewritten as the script does; store records a correction of a good one."""
    ctx.log.info("Cloudflare %s result: %s", row["site"], json.dumps(row))
    store.write_good(path, row, ctx, spec.NAME)


def _write_failure(ctx: Context, path: Path, row: dict[str, Any]) -> None:
    """The failure row, logged as the script logs a result; store keeps a good row in its place."""
    ctx.log.info("Cloudflare %s result: %s", row["site"], json.dumps(row))
    store.write_failure(path, row, ctx.now)
