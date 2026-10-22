"""goatcounter's code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any, Collection, Mapping, NamedTuple, Sequence

from leadline import store
from leadline.config import is_count
from leadline.context import Context, Finding
from leadline.http import HttpError, UnusableError, read_json, unexpected
from leadline.sources.goatcounter import spec

CREDENTIAL = "GOATCOUNTER_TOKEN"
ENV: tuple[str, ...] = (CREDENTIAL,)
LAGGING = True
ATTEMPTS = 3  # for the pages, as the script asks for them
WINDOW_DAYS = 7  # the week before the run: a single day's window comes back empty
PAGE_LIMIT = 200  # well past the site's pages: the API's default of 20 would miss some
REFERRER_PAGES = 10  # the pages whose referrers are fetched
TOP_REFERRERS = 5
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
# A 404 collection gets is recoverable: GoatCounter answers one for a readable site at times (ADR: one
# failed night recovers; consecutive failures are critical). check() asks without it.
RECOVERABLE = frozenset({404})


class Page(NamedTuple):
    """A page of `stats/hits`: its id, as the script reads it (None for none), and its stats' days and counts.

    A stat without a day has the day "".
    """

    id: str | None
    stats: list[tuple[str, int]]


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether each entry's site exists and the token can read it; writes nothing, never raises for the API.

    It asks for the week's total, as collection does, so a run reuses that answer. A 404 is an error
    here, so a wrong site shows the night it is wrong.
    """
    findings = []
    for entry in cfg:
        site = entry["site"]
        try:
            _total(ctx, site, recoverable=())
        except HttpError as e:
            if e.unfixable:
                message = f"HTTP {e.code}: the site doesn't exist, or the token can't read it"
                findings.append(Finding("error", f"{site}: {message}"))
            else:
                findings.append(Finding("warn", f"{site}: could not read the site: HTTP {e.code}"))
        except UnusableError as e:
            findings.append(Finding("warn", f"{site}: could not read the site: {e}"))
        else:
            findings.append(Finding("ok", f"{site}: the site exists and the token can read it"))
    return findings


def _get(
    ctx: Context,
    site: str,
    path: str,
    *,
    limit: str = "",
    attempts: int = 1,
    require_body: bool = False,
    recoverable: Collection[int] = RECOVERABLE,
) -> Any:
    """GET the API's `path` for the week before the run, with the token, as the script asks."""
    week_ago = (ctx.date - timedelta(days=WINDOW_DAYS)).isoformat()
    url = f"https://{site}.goatcounter.com/api/v0/{path}?start={week_ago}&end={ctx.date.isoformat()}{limit}"
    headers = {"Authorization": f"Bearer {os.environ[CREDENTIAL]}"}
    return ctx.http.request(url, headers=headers, attempts=attempts, require_body=require_body, recoverable=recoverable)


def _total(ctx: Context, site: str, recoverable: Collection[int] = RECOVERABLE) -> int:
    """GoatCounter's total for the week. Raises HttpError, or UnusableError for an answer without one."""
    answer = read_json("total", _get(ctx, site, "stats/total", recoverable=recoverable))
    if not (isinstance(answer, dict) and is_count(answer.get("total"))):
        raise unexpected("total")
    total: int = answer["total"]
    return total


def _collect(ctx: Context, entry: Any) -> None:
    site, path = entry["site"], ctx.out_dir / spec.file_name(entry)
    yesterday = (ctx.date - timedelta(days=1)).isoformat()
    ctx.log.info("Collecting GoatCounter metrics for %s...", site)
    try:
        hits = _get(ctx, site, "stats/hits", limit=f"&limit={PAGE_LIMIT}", attempts=ATTEMPTS, require_body=True)
    except HttpError as e:
        ctx.log.info("ERROR: GoatCounter hits for %s failed (HTTP %s)", site, e.http_code)
        ctx.record_unfixable(spec.NAME, path.name, e, f"{site}: HTTP {e.http_code} from GoatCounter's API")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": "hits request failed"}
        store.write_failure(path, {"date": yesterday, **failure}, ctx.now)
        return
    failed: dict[str, list[str]] = {}  # each failure, as the reason names it, and the field it left null
    total: int | None = None
    try:
        total = _total(ctx, site)
    except HttpError as e:
        failed[_request_failed(ctx, site, path, "total", e)] = ["window_total"]
    except UnusableError as e:
        ctx.log.info("WARN: Failed to parse GoatCounter total for %s: %s", site, e)
        failed[str(e)] = ["window_total"]
    try:
        pages = _pages(read_json("hits", hits))  # read after the total is asked for, as the script reads it
    except UnusableError as e:
        ctx.log.info("ERROR: Failed to parse GoatCounter hits for %s: %s", site, e)
        store.write_failure(path, {"date": yesterday, "status": "parse_failed", "reason": str(e)}, ctx.now)
        return
    referrers, reason = _referrers(ctx, site, path, pages)
    if reason is not None:
        failed[reason] = ["top_referrers"]
    _write(ctx, path, pages, total, referrers, failed)


def _request_failed(ctx: Context, site: str, path: Path, kind: str, error: HttpError) -> str:
    """An optional part's failed request, reported: the reason it gives a partial row."""
    ctx.record_unfixable(spec.NAME, path.name, error, f"{site}: HTTP {error.http_code} from GoatCounter's API")
    return f"{kind} request failed (HTTP {error.http_code})"


def _objects(kind: str, value: Any) -> list[dict[str, Any]]:
    """A list of objects, or `unexpected(kind)`."""
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise unexpected(kind)
    return value


def _require(kind: str, found: Mapping[str, Any], fields: Sequence[str], where: str = "") -> None:
    """Raises UnusableError naming each of `fields` the object lacks: what is absent is not zero or none (ADR: a
    failure is recorded as a failure)."""
    absent = [field for field in fields if field not in found]
    if absent:
        raise UnusableError(f"{kind} response is missing {', '.join(absent)}{where}")


def _pages(answer: Any) -> list[Page]:
    """Each page, with its id and stats, as the script reads them (an absent list or count makes the answer unusable).

    Raises UnusableError for any other answer: a stat whose day or count it can't read makes the whole
    answer one it can't use.
    """
    if not isinstance(answer, dict):
        raise unexpected("hits")
    _require("hits", answer, ["hits"])
    pages = []
    for hit in _objects("hits", answer["hits"]):
        _require("hits", hit, ["stats"], " in a page")
        stats = []
        for stat in _objects("hits", hit["stats"]):
            _require("hits", stat, ["daily"], " in a day")
            day, daily = stat.get("day", ""), stat["daily"]  # a stat with no day counts only in the window sum
            if not (isinstance(day, str) and (day == "" or _DAY.fullmatch(day)) and is_count(daily)):
                raise unexpected("hits")
            stats.append((day, daily))
        path_id = hit.get("id", hit.get("path_id"))
        pages.append(Page(str(path_id) if path_id else None, stats))
    return pages


def _refs(kind: str, answer: Any) -> list[tuple[str, int]]:
    """A page's referrers the script counts: each name, stripped, and its count, where both are set."""
    if not isinstance(answer, dict):
        raise unexpected(kind)
    _require(kind, answer, ["refs"])
    found = []
    for ref in _objects(kind, answer["refs"]):
        _require(kind, ref, ["name", "count"], " in a referrer")
        name, count = ref["name"], ref["count"]
        if not (isinstance(name, str) and is_count(count)):
            raise unexpected(kind)
        if name.strip() and count:
            found.append((name.strip(), count))
    return found


def _referrers(ctx: Context, site: str, path: Path, pages: list[Page]) -> tuple[list[Any] | None, str | None]:
    """The top referrers of the first ten pages with an id whose referrers could be fetched, and None.

    Where a page's request fails (which doesn't count toward the ten) or its answer can't be used
    (which does, as in the script), every page's are still asked for, and the result is None and the
    first failure's reason. Ties keep the order the pages came back in.
    """
    counted: Counter[str] = Counter()
    fetched = 0
    failure: str | None = None
    for page in pages:
        if fetched >= REFERRER_PAGES:
            break
        if page.id is None:
            continue
        kind = f"path {page.id} referrers"
        try:
            answer = _get(ctx, site, f"stats/hits/{page.id}", require_body=True)
        except HttpError as e:
            failure = failure or _request_failed(ctx, site, path, kind, e)
            continue
        fetched += 1
        try:
            for name, count in _refs(kind, read_json(kind, answer)):
                counted[name] += count
        except UnusableError as e:
            ctx.log.info("WARN: Failed to parse GoatCounter %s: %s", kind, e)
            failure = failure or str(e)
    ctx.log.info("GoatCounter: fetched referrers for %d paths", fetched)
    if failure is not None:
        return None, failure
    return [{"ref": name, "count": count} for name, count in counted.most_common(TOP_REFERRERS)], None


def _write(
    ctx: Context, path: Path, pages: list[Page], total: int | None, referrers: Any, failed: dict[str, list[str]]
) -> None:
    """A row for each day the file has no good or partial row for, and for yesterday again; never today."""
    yesterday = (ctx.date - timedelta(days=1)).isoformat()
    stats = [stat for page in pages for stat in page.stats]
    held = {
        day for day, row in store.rows_by_date(path).items() if store.is_good(row) or row["status"] == store.PARTIAL
    }
    for day in sorted({day for day, _ in stats if day and day <= yesterday}):
        if day in held and day != yesterday:
            continue
        if day not in held:
            ctx.log.info("GoatCounter backfill: %s", day)
        row: dict[str, Any] = {
            "date": day,
            "pageviews": sum(daily for stat_day, daily in stats if stat_day == day),
            "top_referrers": [],
        }
        if day != yesterday:
            store.write_good(path, row, ctx, spec.NAME)
            continue
        row["top_referrers"] = referrers
        row["window_sum"] = sum(daily for stat_day, daily in stats if stat_day <= yesterday)
        row["window_total"] = total
        row["paths_returned"] = len(pages)
        ctx.log.info("GoatCounter result: %s", json.dumps(row))
        if failed:
            store.write_partial(path, row, failed, ctx, spec.NAME)
        else:
            store.write_good(path, row, ctx, spec.NAME)
