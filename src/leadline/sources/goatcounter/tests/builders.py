"""GoatCounter builders."""

from __future__ import annotations

from datetime import date
from typing import Mapping, Sequence

from scenarios.builders.common import Json


def goatcounter_stat(day: date, daily: int, *, totals: bool = True) -> Json:
    """One day of a stats list, by hour and in all; `totals` adds the week's and month's so far."""
    hourly = [0] * 24
    for visit in range(daily):
        hourly[(7 * visit) % 24] += 1
    stat: Json = {"day": day.isoformat(), "hourly": hourly, "daily": daily}
    if totals:
        stat.update({"weekly": daily, "monthly": daily})
    return stat


def _goatcounter_stats(days: Mapping[date, int]) -> list[Json]:
    """A window's days; the first has no week or month totals, as the API gives it."""
    return [goatcounter_stat(day, n, totals=i > 0) for i, (day, n) in enumerate(days.items())]


def goatcounter_path(path_id: int, path: str, days: Mapping[date, int]) -> Json:
    """An item of `stats/hits`: one page's visits by day."""
    return {
        "count": sum(days.values()),
        "path_id": path_id,
        "path": path,
        "event": False,
        "title": f"Example page {path_id}",
        "max": max(days.values(), default=0),
        "stats": _goatcounter_stats(days),
    }


def goatcounter_hits(paths: Sequence[Json], *, more: bool = False) -> Json:
    return {"hits": list(paths), "total": sum(p["count"] for p in paths), "more": more}


def goatcounter_total(days: Mapping[date, int]) -> Json:
    """`stats/total`: every page's visits by day, and the window's total."""
    total = sum(days.values())
    return {"total": total, "total_events": 0, "total_utc": total, "stats": _goatcounter_stats(days)}


def goatcounter_refs(refs: Sequence[tuple[str, int]], *, more: bool = False) -> Json:
    """`stats/hits/PATH_ID`: a page's referrers and their visits; "" is direct visits."""
    return {"refs": [{"name": name, "count": n, "ref_scheme": "h" if name else "o"} for name, n in refs], "more": more}


def goatcounter_stats(items: Sequence[tuple[str, int]], *, more: bool = False) -> Json:
    """`stats/browsers` and the like (`systems`, `locations`): each name and its visitors.

    From GoatCounter's API reference (https://www.goatcounter.com/api.json, `GET /api/v0/stats/{page}`: a `stats`
    list of `HitStat` objects with `id`, `name` and `count`, and `more`): the script never asks for these, so the
    capture holds no example and the shape test cannot hold this to one.
    """
    return {"stats": [{"id": name, "name": name, "count": n} for name, n in items], "more": more}
