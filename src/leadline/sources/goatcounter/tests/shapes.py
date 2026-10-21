"""The kinds of response the oracle's shape test checks for goatcounter: hits, total and a page's referrers."""

from __future__ import annotations

import re
from datetime import date, timedelta

from scenarios.builders import common
from shape_kind import Kind

from leadline.sources.goatcounter.tests import builders as goatcounter

DAY = date(2026, 9, 22)
WEEK = [DAY - timedelta(days=n) for n in range(6, -1, -1)]

KINDS = [
    Kind(
        "GoatCounter hits",
        lambda r: re.search(r"\.goatcounter\.com/api/v0/stats/hits\?", r["url"]) is not None,
        [
            common.exchange(
                "hits", goatcounter.goatcounter_hits([goatcounter.goatcounter_path(1, "/", dict.fromkeys(WEEK, 3))])
            )
        ],
    ),
    Kind(
        "GoatCounter total",
        lambda r: re.search(r"\.goatcounter\.com/api/v0/stats/total\?", r["url"]) is not None,
        [common.exchange("total", goatcounter.goatcounter_total(dict.fromkeys(WEEK, 3)))],
    ),
    Kind(
        "GoatCounter referrers of a page",
        lambda r: re.search(r"\.goatcounter\.com/api/v0/stats/hits/\d+\?", r["url"]) is not None,
        [common.exchange("refs", goatcounter.goatcounter_refs([("", 5), ("github.com", 2)]))],
    ),
]
