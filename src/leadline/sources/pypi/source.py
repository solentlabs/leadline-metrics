"""pypi's code that makes requests and writes rows: collect, retry, check; the declarations are in spec.py."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, NamedTuple

from leadline import store
from leadline.config import is_count
from leadline.context import Context, Finding
from leadline.http import HttpError, UnusableError, read_json, unexpected
from leadline.sources.pypi import spec
from leadline.storage import File

ENV: tuple[str, ...] = ()  # pypistats and PyPI are public
LAGGING = False  # pypistats publishes a UTC day just after it ends: a 23:30 run finds its own date
ATTEMPTS = 2  # for the history in each pass; the retry pass gives two more
WEEK_DAYS, MONTH_DAYS = 7, 30
METADATA = ["latest_version", "requires_python"]  # the row's fields the metadata gives


class History(NamedTuple):
    """pypistats' daily history: downloads without mirrors by day, the same with mirrors (None when the answer
    has no with-mirrors rows), and the latest and first days it has."""

    daily: dict[str, int]
    mirrored: dict[str, int] | None
    published: str
    start: str


class Metadata(NamedTuple):
    """PyPI's metadata as the script reads it: the version and required Python ("" where absent), and
    the first upload's day ("" for none)."""

    version: Any
    requires_python: Any
    first_upload: str


def collect(ctx: Context, cfg: Any) -> None:
    """Every package, in the night's order; one's failure row doesn't stop the next."""
    _read(ctx, cfg, _order(ctx, cfg["packages"]))


def retry(ctx: Context, cfg: Any) -> None:
    """The retry pass: after `retry_wait_s`, each package whose run-date row is a failed history request
    that waiting can fix, asked again, in the night's order."""
    failed = [package for package in _order(ctx, cfg["packages"]) if _retryable(ctx, cfg, package)]
    if not failed:
        ctx.log.info("PyPI retry pass: nothing to retry")
        return
    ctx.log.info("PyPI retry pass: %d package(s) to retry — %s", len(failed), " ".join(failed))
    ctx.log.info("PyPI retry pass: waiting %ss before retrying", cfg["retry_wait_s"])
    ctx.http.wait(cfg["retry_wait_s"])
    _read(ctx, cfg, failed)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether PyPI has each package; writes nothing, never raises for the API, and never asks pypistats.

    It asks for the metadata collection asks for, so a run reuses that answer.
    """
    findings = []
    for package in cfg["packages"]:
        try:
            metadata = _metadata(read_json("metadata", ctx.http.request(_project_url(package))))
        except HttpError as e:
            if e.unfixable:
                message = f"HTTP {e.code} from PyPI: the package doesn't exist, or PyPI refuses it"
                findings.append(Finding("error", f"{package}: {message}"))
            else:
                findings.append(Finding("warn", f"{package}: could not read PyPI: HTTP {e.code}"))
        except UnusableError as e:
            findings.append(Finding("warn", f"{package}: could not read PyPI: {e}"))
        else:
            findings.append(Finding("ok", f"{package}: PyPI has the package, at version {metadata.version}"))
    return findings


def _overall_url(package: str) -> str:
    return f"https://pypistats.org/api/packages/{package}/overall"


def _project_url(package: str) -> str:
    return f"https://pypi.org/pypi/{package}/json"


def _order(ctx: Context, packages: list[str]) -> list[str]:
    """The night's order: the packages rotated one place further along each night, on the date's ordinal."""
    start = ctx.date.toordinal() % len(packages)
    return packages[start:] + packages[:start]


def _read(ctx: Context, cfg: Any, packages: list[str]) -> None:
    """Each package in turn, `sleep_s` apart, as pypistats wants its requests spaced."""
    for i, package in enumerate(packages):
        if i:
            ctx.http.wait(cfg["sleep_s"])
        _collect(ctx, cfg, package)


def _retryable(ctx: Context, cfg: Any, package: str) -> bool:
    """Whether the run-date row is a failed history request that waiting can fix.

    Not a date pypistats hasn't published yet (`http_code` "200"), which minutes won't change; not a
    good or partial row, or an answer the source couldn't use; and not an error recorded critical for
    the file this run, which waiting can't fix (ADR: one failed night recovers).
    """
    file = ctx.file(spec.file_name(cfg, package))
    row = store.rows_by_date(file).get(ctx.date.isoformat())
    if row is None or row.get("status") != "fetch_failed" or row.get("http_code") == "200":
        return False
    return not any(critical.source == spec.NAME and critical.file == file.name for critical in ctx.critical)


def _collect(ctx: Context, cfg: Any, package: str) -> None:
    file, day = ctx.file(spec.file_name(cfg, package)), ctx.date.isoformat()
    ctx.log.info("Collecting PyPI stats for %s...", package)
    try:
        response = ctx.http.request(_overall_url(package), attempts=ATTEMPTS, backoff_s=cfg["sleep_s"])
    except HttpError as e:
        ctx.log.info("ERROR: Failed to fetch PyPI download history for %s (last HTTP %s)", package, e.http_code)
        ctx.record_unfixable(spec.NAME, file.name, e, f"{package}: HTTP {e.http_code} from pypistats")
        reason = "rate limited by pypistats" if e.code == 429 else "pypistats overall endpoint unavailable"
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": reason}
        store.write_failure(file, {"date": day, "package": package, **failure}, ctx.now)
        return
    stored = store.rows_by_date(file)  # before this run writes, as the script reads the file
    try:
        history = _history(read_json("overall", response))
    except UnusableError as e:
        ctx.log.info("ERROR: Failed to parse PyPI data for %s: %s", package, e)
        store.write_failure(
            file, {"date": day, "package": package, "status": "parse_failed", "reason": str(e)}, ctx.now
        )
        return
    metadata, failed = _fetch_metadata(ctx, file, package)
    if history.published >= day:
        row = {"date": day, "package": package, "status": "ok", **_downloads(history, ctx.date)}
        row["latest_version"] = metadata.version if metadata else None
        row["requires_python"] = metadata.requires_python if metadata else None
        if failed is None:
            store.write_good(file, row, ctx, spec.NAME)
        else:
            store.write_partial(file, row, {failed: METADATA}, ctx, spec.NAME)
    else:
        # An off-hours run may find its date not published yet: marked, not the previous day mislabeled.
        ctx.log.info("WARN: pypistats has not published %s for %s yet; the next run backfills it", day, package)
        reason = f"pypistats has not published {day} yet (latest {history.published})"
        failure = {"status": "fetch_failed", "http_code": "200", "reason": reason}
        store.write_failure(file, {"date": day, "package": package, **failure}, ctx.now)
    _backfill(ctx, file, package, history, metadata, stored)


def _fetch_metadata(ctx: Context, file: File, package: str) -> tuple[Metadata | None, str | None]:
    """The package's metadata, or None and the reason a partial row gives for it."""
    try:
        return _metadata(read_json("metadata", ctx.http.request(_project_url(package)))), None
    except HttpError as e:
        ctx.log.info("WARN: Failed to fetch PyPI metadata for %s (HTTP %s)", package, e.http_code)
        ctx.record_unfixable(spec.NAME, file.name, e, f"{package}: HTTP {e.http_code} from PyPI")
        return None, e.failed("metadata")
    except UnusableError as e:
        ctx.log.info("WARN: Failed to parse PyPI metadata for %s: %s", package, e)
        return None, str(e)


def _backfill(
    ctx: Context, file: File, package: str, history: History, metadata: Metadata | None, stored: dict[str, Any]
) -> None:
    """A row for each earlier day since the file's first row that has no good row, from the history.

    From where a month's window is whole in the history: its first day for a package first uploaded
    within it, else 29 days on (pypistats keeps 180 days, so an older package's month would sum
    short). Up to the latest day published, never the run's date. The file's first row is its
    earliest date, so a package added last week isn't missing the months before.
    """
    days = [day for day in map(_day, stored) if day is not None]
    if not days:
        return
    start = date.fromisoformat(history.start)
    first_upload = metadata.first_upload if metadata else ""
    fill_from = start if first_upload and first_upload >= history.start else start + timedelta(days=MONTH_DAYS - 1)
    end = min(ctx.date, date.fromisoformat(history.published) + timedelta(days=1))
    day, missing = max(min(days), fill_from), []
    while day < end:
        row = stored.get(day.isoformat())
        if row is None or not store.is_good(row):
            missing.append(day)
        day += timedelta(days=1)
    if missing:
        ctx.log.info("PyPI %s: backfilling %d earlier day(s) from pypistats history", package, len(missing))
    for day in missing:
        row = {"date": day.isoformat(), "package": package, "status": "ok", **_downloads(history, day)}
        store.write_good(file, {**row, store.BACKFILLED: True}, ctx, spec.NAME)


def _downloads(history: History, day: date) -> dict[str, int | None]:
    """The day's, the week's and the month's downloads ending on `day`, without mirrors and then with them
    (null when pypistats gave no with-mirrors rows)."""

    def window(daily: dict[str, int], days: int) -> int:
        return sum(daily.get((day - timedelta(days=n)).isoformat(), 0) for n in range(days))

    row: dict[str, int | None] = {
        "downloads_last_day": window(history.daily, 1),
        "downloads_last_week": window(history.daily, WEEK_DAYS),
        "downloads_last_month": window(history.daily, MONTH_DAYS),
    }
    mirrored = history.mirrored
    for field, days in (("day", 1), ("week", WEEK_DAYS), ("month", MONTH_DAYS)):
        row[f"downloads_with_mirrors_last_{field}"] = None if mirrored is None else window(mirrored, days)
    return row


def _day(value: Any) -> date | None:
    """`value` as a date when it is one written `YYYY-MM-DD`, else None."""
    try:
        return date.fromisoformat(value) if isinstance(value, str) and len(value) == 10 else None
    except ValueError:
        return None


def _history(answer: Any) -> History:
    """The history as the script reads it: every day with a date, and each day's downloads without mirrors
    (and with them, where given).

    Raises UnusableError for an answer it can't use: not an object, `data` not a list of objects, a
    date that isn't one, a count that isn't one, or no days at all.
    """
    data = answer.get("data", []) if isinstance(answer, dict) else None
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise unexpected("overall")
    daily: dict[str, int] = {}
    mirrored: dict[str, int] = {}
    dates: list[str] = []
    for item in data:
        day, downloads = item.get("date"), item.get("downloads")
        if day and _day(day) is None:
            raise unexpected("overall")
        if day:
            dates.append(day)
        if item.get("category") == "without_mirrors":
            if not (day and is_count(downloads)):
                raise unexpected("overall")
            daily[day] = downloads
        elif item.get("category") == "with_mirrors" and day and is_count(downloads):
            mirrored[day] = downloads  # optional: a row it can't use is left out, as it always was
    if not dates:
        raise UnusableError("pypistats returned no history")
    return History(daily, mirrored or None, max(dates), min(dates))


def _metadata(answer: Any) -> Metadata:
    """The metadata as the script reads it. Raises UnusableError for an answer it can't use: not an
    object, `info` or `releases` not objects, a release's files not a list of objects, or an upload
    time that isn't text."""
    if not isinstance(answer, dict):
        raise unexpected("metadata")
    info, releases = answer.get("info", {}), answer.get("releases", {})
    if not (isinstance(info, dict) and isinstance(releases, dict)):
        raise unexpected("metadata")
    uploads = []
    for release in releases.values():
        if not isinstance(release, list) or not all(isinstance(file, dict) for file in release):
            raise unexpected("metadata")
        for file in release:
            uploaded = file.get("upload_time")
            if uploaded and not isinstance(uploaded, str):
                raise unexpected("metadata")
            if uploaded:
                uploads.append(uploaded[:10])
    return Metadata(info.get("version", ""), info.get("requires_python", ""), min(uploads, default=""))
