"""The kind of response the oracle's shape test checks for cloudflare_web: Web Analytics (GraphQL)."""

from __future__ import annotations

from datetime import date

from scenarios.builders import common
from shape_kind import Kind

from leadline.sources.cloudflare.tests import builders as cloudflare

DAY = date(2026, 9, 22)

KINDS = [
    Kind(
        "Cloudflare Web Analytics (GraphQL)",
        lambda r: r["url"] == cloudflare.CLOUDFLARE_GRAPHQL,
        [
            common.exchange(
                cloudflare.CLOUDFLARE_GRAPHQL,
                cloudflare.web_analytics(
                    [cloudflare.rum_day(DAY, 25, 9)],
                    [cloudflare.rum_referrer("", 50), cloudflare.rum_referrer("example.com", 5)],
                ),
                method="POST",
            )
        ],
        unverified=frozenset({"$.data.viewer.accounts[].daily[]", "$.data.viewer.accounts[].referrers[]"}),
    ),
]
