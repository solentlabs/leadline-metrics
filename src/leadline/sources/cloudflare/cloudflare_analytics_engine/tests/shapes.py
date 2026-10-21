"""The kinds of response the oracle's shape test checks for cloudflare_analytics_engine: SQL queries."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Sequence

from scenarios.builders import common
from shape_kind import Kind

from leadline.sources.cloudflare.tests import builders as cloudflare

DAY = date(2026, 9, 22)
SQL_URL = re.compile(r"^https://api\.cloudflare\.com/client/v4/accounts/[^/]+/analytics_engine/sql$")


def _sql(columns: dict[str, str], rows: Sequence[Sequence[Any]]) -> dict[str, Any]:
    return cloudflare.analytics_engine_result(columns, rows)


KINDS = [
    Kind(
        "Analytics Engine SQL, events by day",
        lambda r: SQL_URL.match(r["url"]) is not None and "blob1 AS event" in r["body"],
        [
            common.exchange(
                "sql", _sql(cloudflare.EVENTS_BY_DAY_COLUMNS, [(DAY, "active.day", 3), (DAY, "refresh.ok", 20)])
            )
        ],
    ),
    Kind(
        "Analytics Engine SQL, one event's count by day",
        lambda r: SQL_URL.match(r["url"]) is not None and "blob1 AS event" not in r["body"],
        [common.exchange("sql", _sql(cloudflare.COUNT_BY_DAY_COLUMNS, [(DAY, 1)]))],
    ),
]
