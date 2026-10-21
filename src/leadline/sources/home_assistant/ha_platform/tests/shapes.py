"""The kind of response the oracle's shape test checks for ha_platform: the platform-wide analytics."""

from __future__ import annotations

import random

from scenarios.builders import common
from shape_kind import Kind

from leadline.sources.home_assistant.tests import builders as home_assistant

KINDS = [
    Kind(
        "Home Assistant platform analytics",
        lambda r: r["url"] == home_assistant.HA_PLATFORM_URL,
        [
            common.exchange(
                home_assistant.HA_PLATFORM_URL,
                home_assistant.ha_platform(
                    random.Random(2), active=1000, reports_integrations=400, countries={"DE": 3, "US": 2}
                ),
            )
        ],
        maps=frozenset(
            {
                "$.current.countries",
                "$.current.integrations",
                "$.current.versions",
                "$.current.operating_system.boards",
                "$.current.operating_system.versions",
                "$.current.operating_system.board_versions",
                "$.current.operating_system.board_versions{}",
                "$.current.supervisor.arch",
                "$.current.recorder.engines",
                "$.current.recorder.engines{}.versions",
                "$.history[].versions",
            }
        ),
    ),
]
