"""What cloudflare_zone_traffic declares: its name, its file, its config's checks, its queries, and its summary.

No requests. The queries are text built here, from the fields below, so the code that sends them and the tests that
serve them name one set of fields.
"""

# cspell:ignore pathing
from __future__ import annotations

from datetime import date
from typing import Any

from leadline import config

NAME = "cloudflare_zone_traffic"
DEFAULT_FILE = config.default_file(NAME, "label")  # cloudflare-zone-traffic-{label}.jsonl

KEYS = ("label", "zone", "top")  # the keys an entry may have, besides `file`
TOP = 50  # the default length of each list of the per-request dataset
MAX_TOP = 1000
# The daily dataset keeps 365 days and an hour (notOlderThan, 31539600 s); two days of slack for a run west of UTC.
HISTORY_DAYS = 363
WINDOW_DAYS = 28  # re-read every run; the per-request dataset keeps 31 days, and a run west of UTC is a day ahead
PAGE = 10000  # the most groups an answer holds (maxPageSize)

# The daily dataset (httpRequests1dGroups), exact totals for a whole day: a row field and the API's.
SCALARS = (
    ("requests", "requests"),
    ("cached_requests", "cachedRequests"),
    ("bytes", "bytes"),
    ("cached_bytes", "cachedBytes"),
    ("encrypted_requests", "encryptedRequests"),
    ("encrypted_bytes", "encryptedBytes"),
    ("page_views", "pageViews"),
    ("threats", "threats"),
)
UNIQUES = "uniques"
# Its maps: a row field, the API's map, the dimension that names an item, and the count read of each item.
MAPS = (
    ("countries", "countryMap", "clientCountryName", "requests"),
    ("country_bytes", "countryMap", "clientCountryName", "bytes"),
    ("country_threats", "countryMap", "clientCountryName", "threats"),
    ("status_codes", "responseStatusMap", "edgeResponseStatus", "requests"),
    ("browser_page_views", "browserMap", "uaBrowserFamily", "pageViews"),
    ("content_types", "contentTypeMap", "edgeResponseContentTypeName", "requests"),
    ("content_type_bytes", "contentTypeMap", "edgeResponseContentTypeName", "bytes"),
    ("http_versions", "clientHTTPVersionMap", "clientHTTPProtocol", "requests"),
    ("ssl_protocols", "clientSSLMap", "clientSSLProtocol", "requests"),
    ("ip_classes", "ipClassMap", "ipType", "requests"),
    ("threat_paths", "threatPathingMap", "threatPathingName", "requests"),
)

# The per-request dataset (httpRequestsAdaptiveGroups), which Cloudflare samples and keeps 31 days: a row field and
# the dimension its counts are grouped by. A visitor's address (clientIP, originIP) is never asked for: leadline
# keeps counts, not who made the requests (ADR: zone traffic is kept as counts).
LISTS = (
    ("paths", "clientRequestPath"),
    ("hosts", "clientRequestHTTPHost"),
    ("user_agents", "userAgent"),
    ("browsers", "userAgentBrowser"),
    ("operating_systems", "userAgentOS"),
    ("device_types", "clientDeviceType"),
    ("data_centers", "coloCode"),
    ("cache_statuses", "cacheStatus"),
    ("methods", "clientRequestHTTPMethodName"),
    ("bot_categories", "verifiedBotCategory"),
    ("security_actions", "securityAction"),
    ("security_sources", "securitySource"),
    ("request_sources", "requestSource"),
)
VISITS = "visits"
ORIGIN_MS = "origin_response_ms"  # the origin's response time: its mean and the 50th, 95th and 99th percentiles
ORIGIN_KEYS = (
    ("avg", "avg"),
    ("p50", "originResponseDurationMsP50"),
    ("p95", "originResponseDurationMsP95"),
    ("p99", "originResponseDurationMsP99"),
)
TOTALS = (VISITS, ORIGIN_MS)  # the fields of the per-request dataset that are not lists


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def _problems(entry: dict[str, Any]) -> list[str]:
    problems = [
        f"{key}: must be a non-empty string"
        for key in ("label", "zone")
        if not isinstance(entry.get(key), str) or not entry[key]
    ]
    top = entry.get("top", TOP)
    if not config.is_count(top) or not 1 <= top <= MAX_TOP:
        problems.append(f"top: must be a whole number from 1 to {MAX_TOP}")
    return problems


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, {"label": entry["label"]})


# --- the queries ---

DAILY = """
{
  viewer {
    zones(filter: {zoneTag: "%s"}) {
      daily: httpRequests1dGroups(
        limit: %d
        filter: {date_geq: "%s", date_leq: "%s"}
        orderBy: [date_ASC]
      ) {
        dimensions { date }
        sum {
%s
        }
        uniq { uniques }
      }
    }
  }
}
"""

LIST = """
{
  viewer {
    zones(filter: {zoneTag: "%s"}) {
      groups: httpRequestsAdaptiveGroups(
        limit: %d
        filter: {date_geq: "%s", date_leq: "%s"}
        orderBy: [count_DESC]
      ) {
        count
        dimensions { date %s }
      }
    }
  }
}
"""

TOTALS_QUERY = """
{
  viewer {
    zones(filter: {zoneTag: "%s"}) {
      groups: httpRequestsAdaptiveGroups(
        limit: %d
        filter: {date_geq: "%s", date_leq: "%s"}
        orderBy: [date_ASC]
      ) {
        sum { visits }
        avg { originResponseDurationMs }
        quantiles { originResponseDurationMsP50 originResponseDurationMsP95 originResponseDurationMsP99 }
        dimensions { date }
      }
    }
  }
}
"""

PROBE = """
{
  viewer {
    zones(filter: {zoneTag: "%s"}) {
      daily: httpRequests1dGroups(limit: 1, filter: {date_geq: "%s", date_leq: "%s"}) {
        dimensions { date }
      }
    }
  }
}
"""


def _sum_fields() -> str:
    """The fields read under `sum` in the daily query: each total, then each map with the fields it reads."""
    lines = [f"          {api}" for _, api in SCALARS]
    for api_map, dimension, counts in _map_reads():
        lines.append(f"          {api_map} {{ {dimension} {' '.join(counts)} }}")
    return "\n".join(lines)


def _map_reads() -> list[tuple[str, str, list[str]]]:
    """Each map the daily query asks for once: its name, its dimension, and the counts read of its items."""
    reads: dict[str, tuple[str, list[str]]] = {}
    for _, api_map, dimension, count in MAPS:
        counts = reads.setdefault(api_map, (dimension, []))[1]
        if count not in counts:
            counts.append(count)
    return [(api_map, dimension, counts) for api_map, (dimension, counts) in reads.items()]


def daily_query(zone: str, start: date, end: date) -> str:
    """The daily dataset's totals and maps for the days start to end (UTC dates), oldest first."""
    return DAILY % (zone, HISTORY_DAYS + 1, start.isoformat(), end.isoformat(), _sum_fields())


def list_query(zone: str, dimension: str, start: date, end: date) -> str:
    """The per-request dataset's request count for each (day, `dimension` value), the most requested first."""
    return LIST % (zone, PAGE, start.isoformat(), end.isoformat(), dimension)


def totals_query(zone: str, start: date, end: date) -> str:
    """The per-request dataset's visits and origin response times for each day."""
    return TOTALS_QUERY % (zone, WINDOW_DAYS + 1, start.isoformat(), end.isoformat())


def probe_query(zone: str, day: date) -> str:
    """The smallest question that shows the token can read the zone's analytics."""
    return PROBE % (zone, day.isoformat(), day.isoformat())


# --- the summary ---

LABEL = "Zone traffic"  # what trends heads its sections with

# The default summary: a day's counts and the two lists a reader checks first.
SHOWN = (
    config.Shown("requests", "Requests", "daily"),
    config.Shown("page_views", "Page views", "daily"),
    config.Shown(VISITS, "Visits", "daily"),
    config.Shown(UNIQUES, "Unique visitors", "daily"),
    config.Shown("threats", "Threats", "daily"),
    config.Shown("bytes", "Data transferred (bytes)", "daily"),
    config.Shown("status_codes", "Status codes", "breakdown", charted=False),
    config.Shown("countries", "Countries", "breakdown", charted=False),
)


def _breakdown(field: str, label: str) -> config.Shown:
    return config.Shown(field, label, "breakdown", half=True)


# What only trends draws, after those of SHOWN: the same counts as stacks, and the lists as stacks of their top items.
# The rest of the row (user agents, methods, bot categories, security, byte counts by item) is kept in the file for
# reports that name it.
CHARTS = (
    config.Shown("cached_requests", "Cached requests", "daily"),
    config.Shown("cached_bytes", "Cached data (bytes)", "daily"),
    _breakdown("status_codes", "Status codes"),
    _breakdown("countries", "Countries"),
    _breakdown("cache_statuses", "Cache status"),
    _breakdown("http_versions", "HTTP versions"),
    _breakdown("browsers", "Browsers"),
    _breakdown("operating_systems", "Operating systems"),
    _breakdown("device_types", "Device types"),
    _breakdown("data_centers", "Data centers"),
    _breakdown("hosts", "Hosts"),
    _breakdown("paths", "Paths"),
    _breakdown("content_types", "Content types"),
    config.Shown(ORIGIN_MS, "Origin response time (ms)", "breakdown", lines=True, half=True),
)


def summary(cfg: Any) -> list[config.Section]:
    return config.entry_summary(NAME, cfg, "label", file_name, SHOWN, LABEL, CHARTS)
