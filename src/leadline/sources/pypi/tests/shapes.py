"""The kinds of response the oracle's shape test checks for pypi: pypistats' downloads and PyPI's project."""

from __future__ import annotations

import re
from datetime import date, timedelta

from scenarios.builders import common
from shape_kind import Kind

from leadline.sources.pypi.tests import builders as pypi

DAY = date(2026, 9, 22)
WEEK = [DAY - timedelta(days=n) for n in range(6, -1, -1)]

KINDS = [
    Kind(
        "pypistats downloads by day",
        lambda r: re.fullmatch(r"https://pypistats\.org/api/packages/[^/]+/overall", r["url"]) is not None,
        [
            common.exchange(
                "overall", pypi.pypistats_overall("example-package", {d: None if d == DAY else 4 for d in WEEK})
            )
        ],
    ),
    Kind(
        "PyPI project",
        lambda r: re.fullmatch(r"https://pypi\.org/pypi/[^/]+/json", r["url"]) is not None,
        [
            common.exchange(
                "project",
                pypi.pypi_project("example-package", {"0.1.0": "2026-07-01T10:00:00", "0.2.0": "2026-08-01T10:00:00"}),
            )
        ],
        maps=frozenset({"$.releases", "$.info.project_urls"}),
    ),
]
