"""health.jsonl: one row for the run date, describing the row files and the run.

The row keeps every field the script's check_freshness writes, in its order, computed from the row
files in the output folder as the script computes them (docs/ARCHITECTURE.md, "Terms" and
"Health"): each file whose newest row is stale, each whose newest row is a failure row with its
streak, and each file's gaps in the last GAP_WINDOW_DAYS days. `script_version` is the package's
version and `script_hash` the config file's. Then leadline adds `checks` (each source's check()
findings), `critical` (the run's critical errors, and each source file with no good row for two or
more consecutive expected dates, recorded critical here, so the run exits 1) and `corrections` (the
run's). A rerun replaces the day's row, as the script's does.

Health reads the output files and the run's context, never a source's module: the runner tells it
which source writes each file and whether that source lags (`Written`).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, NamedTuple, Sequence

from leadline import __version__, config, store
from leadline.context import Context

FILE = config.HEALTH_FILE
STALE_AFTER_DAYS = 1  # how far a file's newest row may be behind its expected date before it is stale
GAP_WINDOW_DAYS = 14  # the trailing days a gap is looked for in
LISTED = 10  # a gap lists this many of its missing days, and of its empty ones
CRITICAL_NIGHTS = 2  # consecutive expected dates with no good row that make a source critical


class Written(NamedTuple):
    """What health knows of a file a configured source writes: the source, and whether it lags."""

    source: str
    lagging: bool


def write(
    ctx: Context, written: Mapping[str, Written], checks: Sequence[Mapping[str, str]], config_hash: str | None
) -> None:
    """Write the run's health row, after recording each critical source file on the context.

    `written` maps each file a configured source writes to its source; `checks` are the findings of
    every source's check(), each with its source's name.
    """
    ctx.log.info("Checking metrics freshness...")
    today = ctx.date
    statuses = {
        path.name: _statuses(path)
        for path in sorted(ctx.out_dir.iterdir())
        if path.name.endswith(".jsonl") and path.name != FILE and path.is_file()
    }
    lagging = {name for name, file in written.items() if file.lagging}
    for name, file in written.items():
        if name in statuses:
            _judge(ctx, name, file, statuses[name], _good_dates(ctx.out_dir / name))
    row = {
        "date": today.isoformat(),
        "script_version": __version__,
        "script_hash": config_hash,
        **_freshness(statuses, today, lagging),
        "checks": [dict(check) for check in checks],
        "critical": [asdict(critical) for critical in ctx.critical],
        "corrections": [asdict(correction) for correction in ctx.corrections],
    }
    store.upsert(ctx.out_dir / FILE, row, ctx.now)
    _log(ctx, row)


def _statuses(path: Path) -> dict[date, Any]:
    """Each date's status in the file, the last line for a date winning, oldest date first.

    A line that isn't a row (it doesn't parse, isn't an object, or its `date` isn't a date) is
    skipped, as store keeps it (ADR: store never fails on a line it can't use).
    """
    found = {}
    for day, row in store.rows_by_date(path).items():
        parsed = store.parse_date(day)
        if parsed is not None:
            found[parsed] = row.get("status")
    return dict(sorted(found.items()))


def _good_dates(path: Path) -> set[date]:
    """The dates the file has a good row for, on any of its series: a forum file holds a row a day for each
    topic, and a topic that fails doesn't make the others' good rows count for nothing."""
    days = {
        store.parse_date(row["date"])
        for row in store.rows(path)
        if isinstance(row.get("date"), str) and store.is_good(row)
    }
    return {day for day in days if day is not None}


def _good(status: Any) -> bool:
    return store.is_good({"status": status})


def _freshness(statuses: Mapping[str, dict[date, Any]], today: date, lagging: set[str]) -> dict[str, Any]:
    """The script's fields after its version and hash: what is stale, failed, and missing, file by file."""
    stale: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    window_start = today - timedelta(days=GAP_WINDOW_DAYS - 1)
    for name, by_date in statuses.items():
        if not by_date:
            stale.append({"file": name, "last": None, "age_days": None})
            continue
        days = list(by_date)
        last = days[-1]
        age = (today - last).days
        if age > STALE_AFTER_DAYS + (1 if name in lagging else 0):
            stale.append({"file": name, "last": last.isoformat(), "age_days": age})
        if not _good(by_date[last]):
            streak = next((n for n, day in enumerate(reversed(days)) if _good(by_date[day])), len(days))
            since = days[-streak].isoformat()
            failed.append(
                {"file": name, "last": last.isoformat(), "status": by_date[last], "streak": streak, "since": since}
            )
        scan_from = max(window_start, days[0])
        scanned = [scan_from + timedelta(days=n) for n in range((last - scan_from).days + 1)]
        missing = [day.isoformat() for day in scanned if day not in by_date]
        empty = [day.isoformat() for day in scanned if day in by_date and not _good(by_date[day])]
        if missing or empty:
            gaps.append(
                {
                    "file": name,
                    "window": f"{scan_from.isoformat()}..{last.isoformat()}",
                    "missing_count": len(missing),
                    "missing": missing[:LISTED],
                    "empty_count": len(empty),
                    "empty": empty[:LISTED],
                }
            )
    return {
        "checked": len(statuses),
        "stale_count": len(stale),
        "stale": stale,
        "failed_count": len(failed),
        "failed": failed,
        "gap_window_days": GAP_WINDOW_DAYS,
        "gap_count": len(gaps),
        "gaps": gaps,
    }


def _judge(ctx: Context, name: str, file: Written, by_date: Mapping[date, Any], good: set[date]) -> None:
    """Record the file critical when it has no good row for CRITICAL_NIGHTS or more expected dates in a row.
    `good` is the dates with a good row on any of the file's series.

    The nights run from the day after its newest good row, or from its first row when it has none
    (the series began there), to its expected date: the run date, or the day before for a lagging
    source.
    """
    if not by_date:
        return
    expected = ctx.date - timedelta(days=1 if file.lagging else 0)
    since = max(good) + timedelta(days=1) if good else next(iter(by_date))
    nights = (expected - since).days + 1
    if nights >= CRITICAL_NIGHTS:
        ctx.record_critical(file.source, name, f"no good row for {nights} nights, since {since.isoformat()}")


def _log(ctx: Context, row: Mapping[str, Any]) -> None:
    """What is stale, failed or missing, as the script's check_freshness logs it."""
    for s in row["stale"]:
        age = "no dated records" if s["age_days"] is None else f"{s['age_days']}d behind (last {s['last']})"
        ctx.log.info("STALE: %s — %s", s["file"], age)
    for s in row["failed"]:
        run = "" if s["streak"] <= 1 else f" ({s['streak']} nights running, since {s['since']})"
        ctx.log.info("FAILED: %s — record for %s has status=%s%s", s["file"], s["last"], s["status"], run)
    for s in row["gaps"]:
        parts = []
        if s["missing_count"]:
            parts.append(f"{s['missing_count']} missing ({', '.join(s['missing'])})")
        if s["empty_count"]:
            parts.append(f"{s['empty_count']} empty ({', '.join(s['empty'])})")
        ctx.log.info("GAP: %s — %s in %s", s["file"], "; ".join(parts), s["window"])
    if row["stale_count"]:
        ctx.log.info("WARN: %d metrics source(s) stale — see %s", row["stale_count"], FILE)
    if row["failed_count"]:
        ctx.log.info("WARN: %d metrics source(s) reported but collected nothing — see %s", row["failed_count"], FILE)
    if not row["stale_count"] and not row["failed_count"]:
        ctx.log.info("All metrics sources current")
