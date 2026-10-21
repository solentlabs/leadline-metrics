"""ha_platform's code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

from typing import Any

from leadline import store
from leadline.context import Context, Finding
from leadline.http import HttpError
from leadline.sources.home_assistant.ha_platform import spec

ENV: tuple[str, ...] = ()  # a public feed: no credentials
LAGGING = False
FEED = "https://analytics.home-assistant.io/data.json"
TOP_COUNTRIES = 15
UNEXPECTED = "response had an unexpected shape"
ANALYTICS = "Home Assistant's platform analytics"  # the feed, as findings name it
# The fields of `current` a row reads.
FIELDS = (
    "active_installations",
    "reports_integrations",
    "reports_addons",
    "reports_statistics",
    "installation_types",
    "avg_integrations",
    "countries",
)


class _UnreadableError(Exception):
    """The answer parses as JSON but not as the analytics the source reads."""


def collect(ctx: Context, cfg: Any) -> None:
    """One row, or failure row, for the run's date."""
    path, day = ctx.out_dir / spec.file_name(cfg), ctx.date.isoformat()
    ctx.log.info("Collecting Home Assistant platform totals...")
    try:
        row = _row(ctx.http.get_json(FEED), day)
    except HttpError as e:
        ctx.record_unfixable(spec.NAME, path.name, e, f"HTTP {e.http_code} from {ANALYTICS}")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": "analytics request failed"}
    except ValueError:
        failure = {"status": "parse_failed", "reason": "response was not JSON"}
    except _UnreadableError:
        failure = {"status": "parse_failed", "reason": UNEXPECTED}
    else:
        store.write_good(path, row, ctx, spec.NAME)
        return
    ctx.log.info("ERROR: No Home Assistant platform totals: %s", failure["reason"])
    store.write_failure(path, {"date": day, **failure}, ctx.now)


def check(ctx: Context, cfg: Any) -> list[Finding]:  # noqa: ARG001 (the contract's signature; nothing in cfg to check)
    """Whether the analytics answer with every field the source reads; writes nothing, never raises for the API.

    A field the answer lacks reads as 0 or null every night, so the rows would look quiet, not broken.
    """
    try:
        data = ctx.http.get_json(FEED)
        _row(data, ctx.date.isoformat())
    except HttpError as e:
        return [Finding("error" if e.unfixable else "warn", f"could not fetch {ANALYTICS}: HTTP {e.code}")]
    except ValueError:
        return [Finding("warn", f"could not read {ANALYTICS}: not JSON")]
    except _UnreadableError:
        return [Finding("warn", f"could not read {ANALYTICS}: unexpected answer")]
    missing = [field for field in FIELDS if field not in data["current"]]
    if missing:
        return [Finding("error", f"{ANALYTICS} lack {', '.join(missing)}, which every row then reads as 0 or null")]
    return [Finding("ok", f"{ANALYTICS} answer with every field the source reads")]


def _number(value: Any) -> Any:
    """`value or 0`, as the script reads a count it divides by or sorts on, so it must then be a number."""
    if not value:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _UnreadableError
    return value


def _row(data: Any, day: str) -> dict[str, Any]:
    """The script's row: the totals, the share reporting integrations, and the top countries.

    Tied countries keep the feed's order, at the cut too, as the script's stable sort does.
    """
    current = data.get("current") if isinstance(data, dict) else None
    if not isinstance(current, dict):
        raise _UnreadableError
    active = _number(current.get("active_installations"))
    reports_integrations = _number(current.get("reports_integrations"))
    countries = current.get("countries") or {}
    if not isinstance(countries, dict):
        raise _UnreadableError
    if any(isinstance(count, bool) or not isinstance(count, (int, float)) for count in countries.values()):
        raise _UnreadableError  # the script's sort negates each count: a null or a string stops it
    top = sorted(countries.items(), key=lambda item: -item[1])[:TOP_COUNTRIES]
    return {
        "date": day,
        "active_installations": active,
        "reports_integrations": reports_integrations,
        "reports_addons": current.get("reports_addons"),
        "reports_statistics": current.get("reports_statistics"),
        # The share of analytics reporters whose custom integrations are visible at all.
        "integrations_report_pct": round(100 * reports_integrations / active, 1) if active else None,
        "installation_types": current.get("installation_types", {}),
        "avg_integrations": current.get("avg_integrations"),
        "top_countries": dict(top),
        "countries_tracked": len(countries),
    }
