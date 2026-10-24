"""Cloudflare builders: Web Analytics over GraphQL and the Workers Analytics Engine SQL API."""

# cspell:ignore pathing
from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

from scenarios.builders.common import Json

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
CLOUDFLARE_GRAPHQL = f"{CLOUDFLARE_API}/graphql"
# The columns of the two Analytics Engine queries the scenarios serve (events by day, and one
# event's count by day), with the types the API reports.
EVENTS_BY_DAY_COLUMNS = {"day": "DateTime", "event": "String", "n": "UInt64"}
COUNT_BY_DAY_COLUMNS = {"day": "DateTime", "n": "UInt64"}


# --- Cloudflare Web Analytics: rumPageloadEventsAdaptiveGroups over GraphQL ---


def rum_day(day: date, pageviews: int, visits: int) -> Json:
    """A `daily` group: one day's page loads (`count`) and visits."""
    return {"count": pageviews, "sum": {"visits": visits}, "dimensions": {"date": day.isoformat()}}


def rum_referrer(host: str, count: int) -> Json:
    """A `referrers` group: page loads from one referring host ("" for direct)."""
    return {"count": count, "dimensions": {"refererHost": host}}


def web_analytics(daily: Sequence[Json] = (), referrers: Sequence[Json] = ()) -> Json:
    """The query's result for one account."""
    return {"data": {"viewer": {"accounts": [{"daily": list(daily), "referrers": list(referrers)}]}}, "errors": None}


def rum_group(dimension: str, value: str | None, count: int) -> Json:
    """A `list` group: page loads for one value of a dimension (`countryName="US"`)."""
    return {"count": count, "dimensions": {dimension: value}}


def rum_list(groups: Sequence[Json]) -> Json:
    """The result of a list query (one dimension, over the week) for one account.

    The script never asks for these, so the capture holds no example and the shape test cannot hold this to one.
    The dimension names (`countryName`, `deviceType`, `requestPath`) were checked against the account's GraphQL
    schema by introspection (2026-10-24), and the group is the same type as the `referrers` group.
    """
    return {"data": {"viewer": {"accounts": [{"list": list(groups)}]}}, "errors": None}


def rum_site(site_tag: str, site_token: str, host: str = "www.example.com") -> Json:
    """A Web Analytics site, as the site list gives it: its site tag, and the beacon token its snippet carries.

    From Cloudflare's API documentation, "List Web Analytics sites"
    (https://developers.cloudflare.com/api/resources/rum/subresources/site_info/methods/list/): the
    script never lists sites, so the capture has none to hold this to.
    """
    return {
        "auto_install": False,
        "created": "2026-01-04T10:00:00.000000Z",
        "rules": [],
        "ruleset": {"id": f"ruleset-{site_tag}", "enabled": True, "zone_name": host, "zone_tag": f"zone-{site_tag}"},
        "site_tag": site_tag,
        "site_token": site_token,
        "snippet": f"<script defer src='https://static.cloudflareinsights.com/beacon.min.js' "
        f'data-cf-beacon=\'{{"token": "{site_token}"}}\'></script>',
    }


def rum_sites(sites: Sequence[Json], *, page: int = 1, total: int | None = None) -> Json:
    """One page of the site list (same document as `rum_site`): the v4 envelope, `total` sites in all."""
    return {
        "success": True,
        "errors": [],
        "messages": [],
        "result": list(sites),
        "result_info": {
            "count": len(sites),
            "page": page,
            "per_page": 10,
            "total_count": len(sites) if total is None else total,
        },
    }


# --- Cloudflare Workers Analytics Engine: the SQL API ---


def _sql_value(value: Any, kind: str) -> Any:
    """A value as the SQL API's JSON gives it: a day as its midnight, a UInt64 quoted."""
    if kind == "DateTime" and isinstance(value, date):
        return f"{value.isoformat()} 00:00:00"
    if kind == "UInt64" and isinstance(value, int):
        return str(value)
    return value


def analytics_engine_result(columns: Mapping[str, str], rows: Sequence[Sequence[Any]]) -> Json:
    """A `FORMAT JSON` result: `columns` (name: type) and each row's values in their order."""
    data = [{name: _sql_value(v, kind) for (name, kind), v in zip(columns.items(), row)} for row in rows]
    return {
        "meta": [{"name": name, "type": kind} for name, kind in columns.items()],
        "data": data,
        "rows": len(data),
        "rows_before_limit_at_least": len(data),
    }


# --- Cloudflare zone analytics: httpRequests1dGroups and httpRequestsAdaptiveGroups over GraphQL ---
# From Cloudflare's GraphQL Analytics documentation and the fields the API lists for a zone (its `settings`
# `availableFields`); no capture holds these answers, so no shape is verified against a real one.


def zone_day(
    day: date,
    requests: int,
    *,
    cached: int = 0,
    bytes_: int = 0,
    cached_bytes: int = 0,
    encrypted: int = 0,
    encrypted_bytes: int = 0,
    page_views: int = 0,
    threats: int = 0,
    uniques: int = 0,
    countries: Mapping[str, tuple[int, int, int]] | None = None,
    statuses: Mapping[int, int] | None = None,
    browsers: Mapping[str, int] | None = None,
    content_types: Mapping[str, tuple[int, int]] | None = None,
    http_versions: Mapping[str, int] | None = None,
    ssl: Mapping[str, int] | None = None,
    ip_classes: Mapping[str, int] | None = None,
    threat_paths: Mapping[str, int] | None = None,
) -> Json:
    """A `daily` group: one day's totals and maps. A country is (requests, bytes, threats), a content type
    (requests, bytes)."""
    return {
        "dimensions": {"date": day.isoformat()},
        "sum": {
            "requests": requests,
            "cachedRequests": cached,
            "bytes": bytes_,
            "cachedBytes": cached_bytes,
            "encryptedRequests": encrypted,
            "encryptedBytes": encrypted_bytes,
            "pageViews": page_views,
            "threats": threats,
            "countryMap": [
                {"clientCountryName": name, "requests": r, "bytes": b, "threats": t}
                for name, (r, b, t) in (countries or {}).items()
            ],
            "responseStatusMap": [{"edgeResponseStatus": code, "requests": n} for code, n in (statuses or {}).items()],
            "browserMap": [{"uaBrowserFamily": name, "pageViews": n} for name, n in (browsers or {}).items()],
            "contentTypeMap": [
                {"edgeResponseContentTypeName": name, "requests": r, "bytes": b}
                for name, (r, b) in (content_types or {}).items()
            ],
            "clientHTTPVersionMap": [
                {"clientHTTPProtocol": name, "requests": n} for name, n in (http_versions or {}).items()
            ],
            "clientSSLMap": [{"clientSSLProtocol": name, "requests": n} for name, n in (ssl or {}).items()],
            "ipClassMap": [{"ipType": name, "requests": n} for name, n in (ip_classes or {}).items()],
            "threatPathingMap": [
                {"threatPathingName": name, "requests": n} for name, n in (threat_paths or {}).items()
            ],
        },
        "uniq": {"uniques": uniques},
    }


def zone_answer(alias: str, groups: Sequence[Json]) -> Json:
    """A query's result for one zone: its `groups` under the query's alias."""
    return {"data": {"viewer": {"zones": [{alias: list(groups)}]}}, "errors": None}


def zone_daily(days: Sequence[Json]) -> Json:
    """The daily query's result."""
    return zone_answer("daily", days)


def zone_request_group(day: date, count: int, **dimension: str) -> Json:
    """A per-request group: `count` requests on `day` for one value of a dimension (`clientRequestPath="/"`)."""
    return {"count": count, "dimensions": {"date": day.isoformat(), **dimension}}


def zone_requests(groups: Sequence[Json]) -> Json:
    """A per-request list query's result."""
    return zone_answer("groups", groups)


def zone_totals_group(day: date, visits: int, avg: float, p50: float, p95: float, p99: float) -> Json:
    """A per-request totals group: a day's visits and the origin's response times in milliseconds."""
    return {
        "sum": {"visits": visits},
        "avg": {"originResponseDurationMs": avg},
        "quantiles": {
            "originResponseDurationMsP50": p50,
            "originResponseDurationMsP95": p95,
            "originResponseDurationMsP99": p99,
        },
        "dimensions": {"date": day.isoformat()},
    }
