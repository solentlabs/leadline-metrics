"""ha_integration's code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

import re
from typing import Any

from leadline import store
from leadline.context import Context, Finding
from leadline.http import HttpError
from leadline.sources.home_assistant.ha_integration import spec

ENV: tuple[str, ...] = ()  # a public feed: no credentials
LAGGING = False
FEED = "https://analytics.home-assistant.io/custom_integrations.json"
ANALYTICS = "Home Assistant's custom integrations analytics"  # the feed, as findings name it
FETCH_FAILED = "analytics request failed"
UNEXPECTED = "response had an unexpected shape"


class _UnreadableError(Exception):
    """The feed parses as JSON but can't be used: the reason is its failure row's."""

    def __init__(self, reason: str = UNEXPECTED) -> None:
        super().__init__(reason)
        self.reason = reason


def collect(ctx: Context, cfg: Any) -> None:
    """The feed, read once, then each entry's row, or failure row, for the run's date."""
    ctx.log.info("Collecting Home Assistant custom integration analytics...")
    try:
        feed = ctx.http.get_json(FEED)
    except HttpError as e:
        ctx.log.info("ERROR: Failed to fetch Home Assistant custom integration analytics: HTTP %s", e.http_code)
        for entry in cfg:
            ctx.record_unfixable(
                spec.NAME, spec.file_name(entry), e, f"{entry['integration']}: HTTP {e.http_code} from {ANALYTICS}"
            )
            _write_failure(ctx, entry, {"status": "fetch_failed", "http_code": e.http_code, "reason": FETCH_FAILED})
        return
    except ValueError:
        for entry in cfg:
            _write_failure(ctx, entry, {"status": "parse_failed", "reason": "response was not JSON"})
        return
    for entry in cfg:
        try:
            row = _row(feed, entry["integration"], ctx.date.isoformat())
        except _UnreadableError as e:
            _write_failure(ctx, entry, {"status": "parse_failed", "reason": e.reason})
        else:
            store.write_good(ctx.file(spec.file_name(entry)), row, ctx, spec.NAME)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Each entry's integration against the feed, read once; writes nothing, never raises for the API.

    The feed lists an integration by its domain. A name it doesn't list reads as no installs every
    night, which looks like a quiet integration rather than a wrong name.
    """
    try:
        feed = ctx.http.get_json(FEED)
    except HttpError as e:
        level = "error" if e.unfixable else "warn"
        return [Finding(level, f"{entry['integration']}: could not fetch {ANALYTICS}: HTTP {e.code}") for entry in cfg]
    except ValueError:
        return [Finding("warn", f"{entry['integration']}: could not read {ANALYTICS}: not JSON") for entry in cfg]
    return [_finding(feed, entry["integration"], ctx.date.isoformat()) for entry in cfg]


def _finding(feed: Any, integration: str, day: str) -> Finding:
    try:
        row = _row(feed, integration, day)
    except _UnreadableError as e:
        trouble = "unexpected answer" if e.reason == UNEXPECTED else e.reason
        return Finding("warn", f"{integration}: could not read {ANALYTICS}: {trouble}")
    if row["rank"] is None:
        unlisted = "so every night reads as no installs; the entry names an integration by its domain"
        return Finding("error", f"{integration}: not listed in {ANALYTICS}, {unlisted}")
    return Finding("ok", f"{integration}: listed in {ANALYTICS}, with {row['total']} installs")


def _write_failure(ctx: Context, entry: Any, failure: dict[str, str]) -> None:
    """The entry's failure row for the run's date; store keeps a good row in its place."""
    ctx.log.info("ERROR: No Home Assistant analytics for %s: %s", entry["integration"], failure["reason"])
    store.write_failure(ctx.file(spec.file_name(entry)), {"date": ctx.date.isoformat(), **failure}, ctx.now)


def _version_key(version: str) -> tuple[tuple[int, Any], ...]:
    """The script's version order: numeric parts as numbers, any other part after every number."""
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in re.split(r"[-.]", version))


def _total(entry: dict[str, Any]) -> Any:
    """An entry's `total` (0 when missing), which the ranking compares, so it must be a number."""
    total = entry.get("total", 0)
    if isinstance(total, bool) or not isinstance(total, (int, float)):
        raise _UnreadableError
    return total


def _row(feed: Any, integration: str, day: str) -> dict[str, Any]:
    """The script's row for `integration`: its installs by version, and its rank among the feed's entries.

    Entries that aren't objects aren't ranked. Ties rank by name, descending, as the script's sort does.
    A feed with no entry to rank is unusable, where the script writes no installs and no rank.
    """
    if not isinstance(feed, dict):
        raise _UnreadableError
    own = feed.get(integration, {})
    if not isinstance(own, dict) or not isinstance(own.get("versions", {}), dict):
        raise _UnreadableError
    try:
        versions = dict(sorted(own.get("versions", {}).items(), key=lambda item: _version_key(item[0])))
    except ValueError:  # a part str.isdigit() takes for a number that int() doesn't
        raise _UnreadableError from None
    ranked = sorted(((_total(entry), name) for name, entry in feed.items() if isinstance(entry, dict)), reverse=True)
    if not ranked:  # zero means zero: a feed with no integrations at all is an outage, not no installs
        raise _UnreadableError("the feed lists no integrations")
    rank = next((i for i, (_, name) in enumerate(ranked, 1) if name == integration), None)
    tracked = len(ranked)
    return {
        "date": day,
        "total": own.get("total", 0),
        "versions": versions,
        "rank": rank,
        "tracked_integrations": tracked,
        "percentile": round(100 * rank / tracked, 1) if rank and tracked else None,
    }
