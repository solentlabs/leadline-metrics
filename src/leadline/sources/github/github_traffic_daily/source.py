"""github_traffic_daily's code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from leadline import store
from leadline.config import is_count
from leadline.context import Context, Finding
from leadline.http import HttpError, Response, UnusableError, read_json, unexpected
from leadline.sources.github import api as github
from leadline.sources.github.github_traffic_daily import spec
from leadline.storage import File

ENV: tuple[str, ...] = (github.CREDENTIAL,)
LAGGING = True
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether each entry's token can read the repo's traffic; writes nothing, never raises for the API."""
    return [github.check_traffic(ctx, entry["repo"]) for entry in cfg]


def _days(kind: str, answer: Any) -> list[tuple[str, int, int]]:
    """Each day's date, count and uniques, in the answer's order.

    Raises UnusableError for any other answer, a day without a timestamp included: its counts belong
    to a day that can't be told. An answer without the list of days, or a day without its count or uniques,
    is not one of zeros: the error names what is missing (ADR: a failure is recorded as a failure).
    """
    if not isinstance(answer, dict):
        raise unexpected(kind)
    if kind not in answer:
        raise UnusableError(f"{kind} response is missing {kind}")
    items = answer[kind]
    if not isinstance(items, list):
        raise unexpected(kind)
    days = []
    for item in items:
        if not isinstance(item, dict):
            raise unexpected(kind)
        absent = [field for field in ("count", "uniques") if field not in item]
        if absent:
            raise UnusableError(f"{kind} response is missing {', '.join(absent)} in a day")
        stamp, count, unique = item.get("timestamp"), item["count"], item["uniques"]
        if not (isinstance(stamp, str) and _DAY.match(stamp) and is_count(count) and is_count(unique)):
            raise unexpected(kind)
        days.append((stamp[:10], count, unique))
    return days


def _collect(ctx: Context, entry: Any) -> None:
    repo, file = entry["repo"], ctx.file(spec.file_name(entry))
    ctx.log.info("Collecting GitHub daily traffic for %s...", repo)
    answers: dict[str, Response] = {}
    try:
        for kind in ("views", "clones"):
            answers[kind] = ctx.http.request(f"{github.API}/repos/{repo}/traffic/{kind}", headers=github.headers())
        views = {day: (count, unique) for day, count, unique in _days("views", read_json("views", answers["views"]))}
        clones = {
            day: (count, unique) for day, count, unique in _days("clones", read_json("clones", answers["clones"]))
        }
    except HttpError as e:
        ctx.log.info("ERROR: Failed to fetch GitHub %s for daily for %s: HTTP %s", kind, repo, e.http_code)
        ctx.record_unfixable(spec.NAME, file.name, e, f"{repo}: HTTP {e.http_code} from GitHub's traffic API")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": f"{kind} request failed"}
    except UnusableError as e:
        ctx.log.info("ERROR: Failed to parse GitHub daily traffic for %s: %s", repo, e)
        failure = {"status": "parse_failed", "reason": str(e)}
    else:
        _write(ctx, file, repo, views, clones)
        return
    store.write_failure(file, {"date": _yesterday(ctx), **failure}, ctx.now)


def _yesterday(ctx: Context) -> str:
    """The day before the run's: the newest day the window has settled, and the date the newest row should have."""
    return (ctx.date - timedelta(days=1)).isoformat()


def _write(
    ctx: Context, file: File, repo: str, views: dict[str, tuple[int, int]], clones: dict[str, tuple[int, int]]
) -> None:
    """A row for each day of the window the file has no good row for, and for yesterday again; never today.

    A day is in the window if GitHub counted views or clones for it; a day with only one has zero of the other.
    """
    if not views and not clones:
        ctx.log.info("GitHub daily traffic: no days in the window for %s", repo)
        return
    today, yesterday = ctx.date.isoformat(), _yesterday(ctx)
    good = {day for day, row in store.rows_by_date(file).items() if store.is_good(row)}
    due = [day for day in sorted(views.keys() | clones.keys()) if day < today and (day not in good or day == yesterday)]
    if not due:
        ctx.log.info("GitHub daily traffic: already current")
    for day in due:
        if day not in good:
            ctx.log.info("GitHub daily traffic backfill: %s", day)
        count, unique = views.get(day, (0, 0))
        clone_count, clone_unique = clones.get(day, (0, 0))
        row = {
            "date": day,
            "views": count,
            "views_unique": unique,
            "clones": clone_count,
            "clones_unique": clone_unique,
        }
        store.write_good(file, row, ctx, spec.NAME)
