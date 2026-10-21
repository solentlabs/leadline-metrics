"""Home Assistant analytics builders: the integrations feed and the platform-wide totals."""

from __future__ import annotations

import random
from typing import Mapping, Sequence

from scenarios.builders.common import Json

HA_INTEGRATIONS_URL = "https://analytics.home-assistant.io/custom_integrations.json"
HA_PLATFORM_URL = "https://analytics.home-assistant.io/data.json"


# --- Home Assistant analytics: custom_integrations.json ---


def ha_integration_entry(total: int, versions: Mapping[str, int] | None = None) -> Json:
    """One integration's entry: installs in total and by version."""
    return {"total": total, "versions": dict(versions) if versions is not None else {"1.0.0": total}}


def ha_integrations_feed(rng: random.Random, entries: Mapping[str, Json], others: int = 50) -> Json:
    """The feed: `entries` among `others` made-up integrations, most small, a few large."""
    feed = {}
    for i in range(others):
        total = int(10 ** rng.uniform(0, 4))
        latest = rng.randint(1, total)
        feed[f"made_up_{i:04d}"] = ha_integration_entry(total, {f"1.{i % 7}.0": latest, "1.0.0": total - latest})
    feed.update(entries)
    return feed


# --- Home Assistant analytics: data.json, the platform-wide totals ---


def ha_platform(rng: random.Random, *, active: int, reports_integrations: int, countries: Mapping[str, int]) -> Json:
    """The platform's analytics: the current totals, made up around `active`, and a short history."""
    kinds = ("os", "container", "core", "supervised", "unsupported_container", "unknown")
    boards = ("ova", "generic-x86-64", "rpi4-64", "rpi5-64")

    def spread(names: Sequence[str], total: int) -> dict[str, int]:
        return {name: rng.randint(0, max(total // len(names), 1)) for name in names}

    current = {
        "last_updated": 1790000000000,
        "countries": dict(countries),
        "installation_types": spread(kinds, active),
        "active_installations": active,
        "avg_users": 2,
        "avg_automations": 30,
        "avg_integrations": 25,
        "avg_addons": 6,
        "avg_states": 900,
        "integrations": spread([f"made_up_{i}" for i in range(20)], active),
        "operating_system": {
            "boards": spread(boards, active // 2),
            "versions": spread(["16.3", "17.0", "17.1"], active // 2),
            "board_versions": {board: spread(["16.3", "17.1"], active // 8) for board in boards},
        },
        "supervisor": {"arch": spread(["amd64", "aarch64"], active // 2), "unhealthy": 10, "unsupported": 20},
        "reports_integrations": reports_integrations,
        "reports_addons": reports_integrations // 2,
        "reports_statistics": reports_integrations // 3,
        "versions": spread(["2026.8.3", "2026.9.1", "2026.9.2"], active),
        "certificate_count_configured": 100,
        "energy": {"count_configured": active // 4},
        "recorder": {
            "engines": {
                engine: {"versions": spread(["1.0", "2.0"], active // 4), "count_configured": active // 4}
                for engine in ("sqlite", "mysql")
            }
        },
        "extended_data_from": reports_integrations // 2,
    }
    history: list[Json] = [
        {
            "timestamp": str(1780000000000 + day * 86400000),
            "active_installations": active - 100 * (3 - day),
            "installation_types": spread(kinds, active),
            "versions": spread(["2026.8.3", "2026.9.1"], active) if day else {},  # the oldest rows have none
        }
        for day in range(3)
    ]
    history[1]["active_installations"] = float(history[1]["active_installations"])  # some rows hold floats
    return {"current": current, "history": history, "schema_version": 4}
