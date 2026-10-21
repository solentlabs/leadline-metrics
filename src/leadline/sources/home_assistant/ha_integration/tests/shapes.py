"""The kind of response the oracle's shape test checks for ha_integration: the custom integrations feed."""

from __future__ import annotations

import random

from scenarios.builders import common
from shape_kind import Kind

from leadline.sources.home_assistant.tests import builders as home_assistant

KINDS = [
    Kind(
        "Home Assistant custom integrations",
        lambda r: r["url"] == home_assistant.HA_INTEGRATIONS_URL,
        [
            common.exchange(
                home_assistant.HA_INTEGRATIONS_URL,
                home_assistant.ha_integrations_feed(
                    random.Random(1), {"example": home_assistant.ha_integration_entry(95, {"1.0.0": 95})}
                ),
            )
        ],
        maps=frozenset({"$", "${}.versions"}),
    ),
]
