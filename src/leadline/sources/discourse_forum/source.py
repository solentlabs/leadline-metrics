"""discourse_forum's code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

from typing import Any

from leadline import store
from leadline.config import is_count
from leadline.context import Context, Finding
from leadline.http import HttpError, UnusableError, read_json, unexpected
from leadline.sources.discourse_forum import spec

ENV: tuple[str, ...] = ()  # a public forum: no credentials
LAGGING = False


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry's forum in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether each entry's forum answers with its site statistics; writes nothing, never raises for the API.

    It asks as collection does, so a run reuses the answers.
    """
    findings = []
    for entry in cfg:
        base_url = entry["base_url"]
        try:
            found = _read(ctx, base_url)
        except HttpError as e:
            if e.code == 0:
                findings.append(Finding("warn", f"{base_url}: the forum did not answer: HTTP 0"))
            elif e.code == 404:
                findings.append(Finding("warn", f"{base_url}: no about.json: HTTP 404"))
            else:
                level = "error" if e.unfixable else "warn"
                findings.append(Finding(level, f"{base_url}: could not read about.json: HTTP {e.code}"))
        except UnusableError as e:
            findings.append(Finding("warn", f"{base_url}: could not read about.json: {e}"))
        else:
            findings.append(Finding("ok", f"{base_url}: about.json gives {len(found)} site statistics"))
    return findings


def _read(ctx: Context, base_url: str) -> dict[str, int]:
    """The forum's site statistics. Raises HttpError for a failed request, UnusableError for an answer it can't use."""
    return _stats(read_json("about", ctx.http.request(f"{base_url.rstrip('/')}/about.json")))


def _stats(answer: Any) -> dict[str, int]:
    """Every integer of the answer's `about.stats` (a boolean is not a count), under the forum's own names.

    Raises UnusableError, naming the part the answer lacks. A forum that hides its statistics gives none.
    """
    if not isinstance(answer, dict):
        raise unexpected("about")
    about = answer.get("about")
    if not isinstance(about, dict):
        raise UnusableError("about response has no about object")
    stats = about.get("stats")
    if not isinstance(stats, dict):
        raise UnusableError("about response has no stats object")
    found = {key: value for key, value in stats.items() if is_count(value)}
    if not found:
        raise UnusableError("about response has no integer stats")
    return found


def _collect(ctx: Context, entry: dict[str, Any]) -> None:
    base_url = entry["base_url"]
    file = ctx.file(spec.file_name(entry))
    row: dict[str, Any] = {"date": ctx.date.isoformat(), "base_url": base_url}
    ctx.log.info("Forum: fetching the site statistics of %s", base_url)
    try:
        row.update(_read(ctx, base_url))
    except HttpError as e:
        ctx.log.info("ERROR: Failed to fetch the site statistics of %s: HTTP %s", base_url, e.http_code)
        ctx.record_unfixable(spec.NAME, file.name, e, f"{base_url}: HTTP {e.http_code} for about.json")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": e.failed("about")}
    except UnusableError as e:
        ctx.log.info("ERROR: Failed to parse the site statistics of %s: %s", base_url, e)
        failure = {"status": "parse_failed", "reason": str(e)}
    else:
        store.write_good(file, row, ctx, spec.NAME)
        return
    store.write_failure(file, {**row, **failure}, ctx.now)
