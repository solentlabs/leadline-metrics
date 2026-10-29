"""github_traffic's code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

import re
from typing import Any, Callable

from leadline import store
from leadline.config import is_count
from leadline.context import Context, Finding
from leadline.http import HttpError, Response, UnusableError, counts, read_json, unexpected
from leadline.sources.github import api as github
from leadline.sources.github.github_traffic import spec
from leadline.storage import File

ENV: tuple[str, ...] = (github.CREDENTIAL,)
LAGGING = False
TOP_REFERRERS = 5
TOP_PATHS = 10
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether each entry's token can read the repo's traffic; writes nothing, never raises for the API."""
    return [github.check_traffic(ctx, entry["repo"]) for entry in cfg]


def _totals(kind: str, answer: Any) -> tuple[int, int]:
    """The answer's `count` and `uniques`; one it lacks is unusable (counts)."""
    found = counts(kind, answer, {"total": "count", "unique": "uniques"})
    return found["total"], found["unique"]


def _window_end(views: dict[str, Any]) -> str | None:
    """The last day the views hold; a day without a timestamp is skipped, as in the script."""
    days = views.get("views", [])
    if not isinstance(days, list) or not all(isinstance(day, dict) for day in days):
        raise unexpected("views")
    stamps = [day["timestamp"] for day in days if day.get("timestamp")]
    if not all(isinstance(stamp, str) and _DAY.match(stamp) for stamp in stamps):
        raise unexpected("views")
    return max(stamp[:10] for stamp in stamps) if stamps else None


def _referrer(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise unexpected("referrers")
    ref, count, unique = item.get("referrer"), item.get("count"), item.get("uniques")
    if not (isinstance(ref, str) and is_count(count) and is_count(unique)):
        raise unexpected("referrers")
    return {"ref": ref, "count": count, "unique": unique}


def _popular_path(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise unexpected("paths")
    file, title, count, unique = item.get("path"), item.get("title"), item.get("count"), item.get("uniques")
    if not (isinstance(file, str) and isinstance(title, str) and is_count(count) and is_count(unique)):
        raise unexpected("paths")
    return {"path": file, "title": title, "count": count, "uniques": unique}


def _ratio(total: int, unique: int) -> float:
    """Total to unique, a noise indicator (over 2 suggests automated or repeat traffic)."""
    return round(total / unique, 2) if unique > 0 else 0


def _row(
    views: Any, clones: Any, referrers: list[dict[str, Any]] | None, paths: list[dict[str, Any]] | None, day: str
) -> dict[str, Any]:
    """The script's row, in its key order, and `top_paths`; each of `top_referrers` and `top_paths` is null where
    it could not be read. Raises UnusableError."""
    views_total, views_unique = _totals("views", views)
    window_end = _window_end(views)
    clones_total, clones_unique = _totals("clones", clones)
    return {
        "date": day,
        "window_end": window_end,
        "views_unique": views_unique,
        "views_total": views_total,
        "views_ratio": _ratio(views_total, views_unique),
        "clones_unique": clones_unique,
        "clones_total": clones_total,
        "clones_ratio": _ratio(clones_total, clones_unique),
        "top_referrers": referrers,
        "top_paths": paths,
    }


def _popular(
    ctx: Context, repo: str, file: File, kind: str, parse: Callable[[Any], list[dict[str, Any]]]
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """The popular `kind` ("referrers" or "paths") parsed by `parse` and None, or, where they failed or can't be
    used, None and the reason: they are one part of the row (ADR: a partly failed row keeps its good parts)."""
    try:
        answer = ctx.http.request(f"{github.API}/repos/{repo}/traffic/popular/{kind}", headers=github.headers())
        return parse(read_json(kind, answer)), None
    except HttpError as e:
        ctx.log.info("WARN: Failed to fetch GitHub %s for %s: HTTP %s", kind, repo, e.http_code)
        ctx.record_unfixable(spec.NAME, file.name, e, f"{repo}: HTTP {e.http_code} from GitHub's traffic API")
        return None, e.failed(kind)
    except UnusableError as e:
        ctx.log.info("WARN: Failed to parse GitHub %s for %s: %s", kind, repo, e)
        return None, str(e)


def _top_referrers(found: Any) -> list[dict[str, Any]]:
    """The first TOP_REFERRERS referrers, as the script reads them."""
    if not isinstance(found, list):
        raise unexpected("referrers")
    return [_referrer(item) for item in found[:TOP_REFERRERS]]


def _top_paths(found: Any) -> list[dict[str, Any]]:
    """The TOP_PATHS most viewed paths, whatever order the API lists them in."""
    if not isinstance(found, list):
        raise unexpected("paths")
    return sorted((_popular_path(item) for item in found), key=lambda item: -item["count"])[:TOP_PATHS]


def _collect(ctx: Context, entry: Any) -> None:
    repo, file, day = entry["repo"], ctx.file(spec.file_name(entry)), ctx.date.isoformat()
    ctx.log.info("Collecting GitHub traffic for %s...", repo)
    answers: dict[str, Response] = {}
    try:
        for kind in ("views", "clones"):
            answers[kind] = ctx.http.request(f"{github.API}/repos/{repo}/traffic/{kind}", headers=github.headers())
        referrers, why = _popular(ctx, repo, file, "referrers", _top_referrers)
        paths, why_paths = _popular(ctx, repo, file, "paths", _top_paths)
        views, clones = read_json("views", answers["views"]), read_json("clones", answers["clones"])
        row = _row(views, clones, referrers, paths, day)
    except HttpError as e:
        ctx.log.info("ERROR: Failed to fetch GitHub %s for %s: HTTP %s", kind, repo, e.http_code)
        ctx.record_unfixable(spec.NAME, file.name, e, f"{repo}: HTTP {e.http_code} from GitHub's traffic API")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": e.failed(kind)}
    except UnusableError as e:
        ctx.log.info("ERROR: Failed to parse GitHub traffic for %s: %s", repo, e)
        failure = {"status": "parse_failed", "reason": str(e)}
    else:
        failed = {reason: [field] for reason, field in ((why, "top_referrers"), (why_paths, "top_paths")) if reason}
        if failed:
            store.write_partial(file, row, failed, ctx, spec.NAME)
        else:
            store.write_good(file, row, ctx, spec.NAME)
        return
    store.write_failure(file, {"date": day, **failure}, ctx.now)
