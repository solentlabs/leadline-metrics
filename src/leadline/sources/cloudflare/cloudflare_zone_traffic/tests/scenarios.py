"""cloudflare_zone_traffic: a zone's HTTP traffic from Cloudflare's GraphQL Analytics API.

The script has no collector for it, so each scenario carries the rows the source must leave (`expected`), written by
hand and reviewed; they are the source's golden. The run is 2026-09-23, so the day it writes is 2026-09-22 (the zone's
UTC date), the 30 days it heals begin 2026-08-24, and the year it reads on a first run begins 2025-09-23.

Every answer is built by the builders of the cloudflare group from made-up values (no capture holds these answers).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from scenarios import Scenario
from scenarios.builders.common import Exchange, exchange, failed

from leadline.sources.cloudflare.cloudflare_zone_traffic import spec
from leadline.sources.cloudflare.tests.builders import (
    CLOUDFLARE_GRAPHQL,
    zone_daily,
    zone_day,
    zone_request_group,
    zone_requests,
    zone_totals_group,
)

LABEL = "example"
ZONE = "zone0tag0example"  # a made-up zone tag
FILE = f"cloudflare-zone-traffic-{LABEL}.jsonl"
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it
YESTERDAY = date(2026, 9, 22)
WINDOW_START = YESTERDAY - timedelta(days=spec.WINDOW_DAYS - 1)  # 2026-08-24
HISTORY_START = YESTERDAY - timedelta(days=spec.HISTORY_DAYS - 1)  # 2025-09-25
LEADLINE_CONFIG = [{"label": LABEL, "zone": ZONE}]
DIVERGENCES: dict[str, str] = {}  # the script has no collector for this source


def sent(query: str) -> str:
    return json.dumps({"query": query})


def daily_answer(body: Any, start: date = HISTORY_START, status: int = 200) -> Exchange:
    return exchange(
        CLOUDFLARE_GRAPHQL, body, method="POST", sent=sent(spec.daily_query(ZONE, start, YESTERDAY)), status=status
    )


def daily_failure(start: date = HISTORY_START) -> Exchange:
    return failed(CLOUDFLARE_GRAPHQL, method="POST", sent=sent(spec.daily_query(ZONE, start, YESTERDAY)))


def list_answer(dimension: str, body: Any, status: int = 200) -> Exchange:
    query = spec.list_query(ZONE, dimension, WINDOW_START, YESTERDAY)
    return exchange(CLOUDFLARE_GRAPHQL, body, method="POST", sent=sent(query), status=status)


def list_failure(dimension: str) -> Exchange:
    query = spec.list_query(ZONE, dimension, WINDOW_START, YESTERDAY)
    return failed(CLOUDFLARE_GRAPHQL, method="POST", sent=sent(query))


def totals_answer(body: Any) -> Exchange:
    query = spec.totals_query(ZONE, WINDOW_START, YESTERDAY)
    return exchange(CLOUDFLARE_GRAPHQL, body, method="POST", sent=sent(query))


def per_request(
    paths: list[Any] | None = None,
    totals: list[Any] | None = None,
    *,
    failing: tuple[str, ...] = (),
    others: dict[str, list[Any]] | None = None,
) -> list[Exchange]:
    """The answers to the totals query and the 13 list queries: `paths` and `totals` as given, any list in `others`
    (by its dimension), the rest empty; a list named in `failing` has its connection fail, all three attempts."""
    groups = {"clientRequestPath": paths or [], **(others or {})}
    answers = [totals_answer(zone_requests(totals or []))]
    for _, dimension in spec.LISTS:
        if dimension in failing:
            answers += [list_failure(dimension)] * 3
        else:
            answers.append(list_answer(dimension, zone_requests(groups.get(dimension, []))))
    return answers


def day(n: int) -> date:
    """Day `n` before yesterday: 0 is yesterday, 1 the day before."""
    return YESTERDAY - timedelta(days=n)


def zone_days() -> list[Any]:
    """Three days of daily totals and maps, the last yesterday."""
    return [
        zone_day(
            day(2),
            100,
            cached=40,
            bytes_=5000,
            cached_bytes=2000,
            encrypted=90,
            encrypted_bytes=4500,
            page_views=30,
            threats=2,
            uniques=12,
            countries={"Utopia": (60, 3000, 0), "Elsewhere": (40, 2000, 2)},
            statuses={200: 70, 404: 30},
            browsers={"Chrome": 20, "Firefox": 10},
            content_types={"text/html": (50, 2500), "image/svg+xml": (50, 2500)},
            http_versions={"HTTP/2": 80, "HTTP/3": 20},
            ssl={"TLSv1.3": 90, "none": 10},
            ip_classes={"clean": 95, "botnet": 5},
            threat_paths={"bot": 2},
        ),
        zone_day(day(1), 120, cached=50, bytes_=6000, page_views=36, uniques=15, statuses={200: 100, 301: 20}),
        zone_day(day(0), 150, cached=60, bytes_=7500, page_views=45, uniques=18, statuses={200: 120, 404: 30}),
    ]


def path_groups() -> list[Any]:
    return [
        zone_request_group(day(2), 50, clientRequestPath="/"),
        zone_request_group(day(2), 10, clientRequestPath="/privacy"),
        zone_request_group(day(1), 60, clientRequestPath="/"),
        zone_request_group(day(0), 80, clientRequestPath="/"),
        zone_request_group(day(0), 80, clientRequestPath="/about"),  # a tie: the name decides
        zone_request_group(day(0), 5, clientRequestPath="/config/.env"),
    ]


def totals_groups() -> list[Any]:
    return [zone_totals_group(day(2), 20, 100.4, 90, 250, 400), zone_totals_group(day(0), 40, 120.6, 100.2, 300, 450)]


# The row for a day, in the order the source writes its fields. `fields` replaces the defaults.
def row(when: date, backfilled: bool = False, *, omit: tuple[str, ...] = (), **fields: Any) -> str:
    base: dict[str, Any] = {
        "date": when.isoformat(),
        "zone": LABEL,
        "status": "ok",
        "requests": 0,
        "cached_requests": 0,
        "bytes": 0,
        "cached_bytes": 0,
        "encrypted_requests": 0,
        "encrypted_bytes": 0,
        "page_views": 0,
        "threats": 0,
        "uniques": 0,
        "countries": {},
        "country_bytes": {},
        "country_threats": {},
        "status_codes": {},
        "browser_page_views": {},
        "content_types": {},
        "content_type_bytes": {},
        "http_versions": {},
        "ssl_protocols": {},
        "ip_classes": {},
        "threat_paths": {},
        "visits": 0,
        "origin_response_ms": {},
        "paths": {},
        "hosts": {},
        "user_agents": {},
        "browsers": {},
        "operating_systems": {},
        "device_types": {},
        "data_centers": {},
        "cache_statuses": {},
        "methods": {},
        "bot_categories": {},
        "security_actions": {},
        "security_sources": {},
        "request_sources": {},
    }
    unknown = set(fields) - set(base)
    if unknown:
        raise ValueError(f"not a field of a row: {sorted(unknown)}")
    base.update(fields)
    for name in omit:  # a field no run has read yet is not in the row
        del base[name]
    if backfilled:
        base["backfilled"] = True
    base["captured_at"] = NOW
    return json.dumps(base) + "\n"


# The rows zone_days(), path_groups() and totals_groups() give, as a first run writes them.
DAY_2: dict[str, Any] = {
    "requests": 100,
    "cached_requests": 40,
    "bytes": 5000,
    "cached_bytes": 2000,
    "encrypted_requests": 90,
    "encrypted_bytes": 4500,
    "page_views": 30,
    "threats": 2,
    "uniques": 12,
    "countries": {"Utopia": 60, "Elsewhere": 40},
    "country_bytes": {"Utopia": 3000, "Elsewhere": 2000},
    "country_threats": {"Elsewhere": 2},
    "status_codes": {"200": 70, "404": 30},
    "browser_page_views": {"Chrome": 20, "Firefox": 10},
    "content_types": {"image/svg+xml": 50, "text/html": 50},
    "content_type_bytes": {"image/svg+xml": 2500, "text/html": 2500},
    "http_versions": {"HTTP/2": 80, "HTTP/3": 20},
    "ssl_protocols": {"TLSv1.3": 90, "none": 10},
    "ip_classes": {"clean": 95, "botnet": 5},
    "threat_paths": {"bot": 2},
    "visits": 20,
    "origin_response_ms": {"avg": 100, "p50": 90, "p95": 250, "p99": 400},
    "paths": {"/": 50, "/privacy": 10},
}
DAY_1: dict[str, Any] = {
    "requests": 120,
    "cached_requests": 50,
    "bytes": 6000,
    "page_views": 36,
    "uniques": 15,
    "status_codes": {"200": 100, "301": 20},
    "paths": {"/": 60},
}
DAY_0: dict[str, Any] = {
    "requests": 150,
    "cached_requests": 60,
    "bytes": 7500,
    "page_views": 45,
    "uniques": 18,
    "status_codes": {"200": 120, "404": 30},
    "visits": 40,
    "origin_response_ms": {"avg": 121, "p50": 100, "p95": 300, "p99": 450},
    "paths": {"/": 80, "/about": 80, "/config/.env": 5},
}
FIRST_RUN = row(day(2), True, **DAY_2) + row(day(1), True, **DAY_1) + row(day(0), **DAY_0)


def stored(when: date, backfilled: bool = False, **fields: Any) -> str:
    """A row an earlier run wrote (the same text as `row`, its clock an earlier night's)."""
    return row(when, backfilled, **fields).replace(NOW, "2026-09-22T23:30:02+00:00")


def failure_row(when: date, captured: bool = False) -> str:
    """The failure row for a daily request that failed to connect; `captured` for the one this run writes."""
    fields = {"date": when.isoformat(), "zone": LABEL, "status": "fetch_failed", "http_code": "000"}
    fields["reason"] = "graphql request failed"
    return json.dumps({**fields, **({"captured_at": NOW} if captured else {})}) + "\n"


def partial_row(when: date, **fields: Any) -> str:
    """A row with a part missing: `partial`, its reason after the other fields, as store writes it."""
    ordered = json.loads(row(when, **{k: v for k, v in fields.items() if v is not None}))
    ordered.update({k: v for k, v in fields.items() if v is None})  # the failed parts, null
    full = json.loads(row(when))
    merged = {key: ordered.get(key, full[key]) for key in full if key != "captured_at"}
    merged["status"] = "partial"
    merged["reason"] = "paths request failed (HTTP 000)"
    merged["captured_at"] = NOW
    return json.dumps(merged) + "\n"


SCENARIOS = [
    Scenario(
        "first_run_fills_the_history_and_yesterday",
        [],
        [daily_answer(zone_daily(zone_days())), *per_request(path_groups(), totals_groups())],
        expected={FILE: FIRST_RUN},
    ),
    Scenario(
        "a_quiet_zone_writes_zeros_for_yesterday",
        [],
        [daily_answer(zone_daily([])), *per_request()],
        expected={FILE: row(YESTERDAY)},
    ),
    Scenario(
        "a_day_the_answer_leaves_out_is_zero_after_the_series_began",
        [],
        [
            daily_answer(zone_daily([zone_day(day(2), 7, page_views=2), zone_day(day(0), 9, page_views=3)])),
            *per_request(),
        ],
        expected={
            FILE: row(day(2), True, requests=7, page_views=2)
            + row(day(1), True)
            + row(day(0), requests=9, page_views=3)
        },
    ),
    Scenario(
        "a_nightly_run_reads_30_days_and_corrects_a_changed_total",
        [],
        [
            daily_answer(zone_daily(zone_days()), start=WINDOW_START),
            *per_request(path_groups(), totals_groups()),
        ],
        seed={
            FILE: stored(day(60), True, requests=11)  # older than the 30 days: the year was read before
            + stored(day(2), True, **{**DAY_2, "requests": 99})
            + stored(day(1), True, **DAY_1)
        },
        expected={
            FILE: stored(day(60), True, requests=11)
            + row(day(2), True, **DAY_2)
            + row(day(1), True, **DAY_1)
            + row(day(0), **DAY_0)
        },
    ),
    Scenario(
        "fills_a_failed_day_and_a_day_with_no_row",
        [],
        [daily_answer(zone_daily(zone_days()), start=WINDOW_START), *per_request(path_groups(), totals_groups())],
        seed={FILE: stored(day(60), True, requests=11) + failure_row(day(1))},
        expected={
            FILE: stored(day(60), True, requests=11)
            + row(day(2), True, **DAY_2)
            + row(day(1), True, **DAY_1)
            + row(day(0), **DAY_0)
        },
    ),
    Scenario(
        "a_list_that_fails_leaves_yesterday_partial",
        [],
        [
            daily_answer(zone_daily(zone_days())),
            *per_request(path_groups(), totals_groups(), failing=("clientRequestPath",)),
        ],
        expected={
            FILE: row(day(2), True, omit=("paths",), **DAY_2)
            + row(day(1), True, omit=("paths",), **DAY_1)
            + partial_row(day(0), **{**DAY_0, "paths": None})
        },
    ),
    Scenario(
        "three_failed_attempts_write_a_failure_row",
        [],
        [daily_failure()] * 3,
        expected={FILE: failure_row(YESTERDAY, captured=True)},
    ),
    Scenario(
        "a_failure_keeps_a_good_row",
        [],
        [daily_failure(WINDOW_START)] * 3,
        seed={FILE: stored(day(60), True, requests=11) + stored(YESTERDAY, **DAY_0)},
        expected={FILE: stored(day(60), True, requests=11) + stored(YESTERDAY, **DAY_0)},
    ),
    Scenario(
        "graphql_errors_in_a_success",
        [],
        [daily_answer({"data": None, "errors": [{"message": "not authorized", "path": ["viewer"]}]})],
        expected={
            FILE: json.dumps(
                {
                    "date": YESTERDAY.isoformat(),
                    "zone": LABEL,
                    "status": "graphql_error",
                    "reason": "not authorized",
                    "captured_at": NOW,
                }
            )
            + "\n"
        },
    ),
    Scenario(
        "a_zone_the_token_cannot_see_is_no_zone",
        [],
        [daily_answer({"data": {"viewer": {"zones": []}}, "errors": None})],
        expected={
            FILE: json.dumps(
                {
                    "date": YESTERDAY.isoformat(),
                    "zone": LABEL,
                    "status": "no_zone",
                    "reason": "zone filter matched nothing",
                    "captured_at": NOW,
                }
            )
            + "\n"
        },
    ),
    Scenario(
        "a_body_that_is_not_json",
        [],
        [daily_answer("<html>Bad gateway</html>")],
        expected={
            FILE: json.dumps(
                {
                    "date": YESTERDAY.isoformat(),
                    "zone": LABEL,
                    "status": "parse_failed",
                    "reason": "response was not JSON",
                    "captured_at": NOW,
                }
            )
            + "\n"
        },
    ),
    Scenario(
        "retries_until_the_request_succeeds",
        [],
        [
            daily_failure(),
            daily_answer("", status=500),
            daily_answer(zone_daily([zone_day(YESTERDAY, 9)])),
            *per_request(),
        ],
        expected={FILE: row(YESTERDAY, requests=9)},
    ),
    Scenario("skips_without_credentials", [], env={}, expected={}),
]
