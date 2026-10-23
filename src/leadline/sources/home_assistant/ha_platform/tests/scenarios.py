"""ha_platform: Home Assistant's platform-wide analytics, the denominator for ha_integration's installs.

The script's collect_ha_platform reads data.json once a night and writes one row for the run's date
into ha-platform.jsonl: active installations; how many report integrations, add-ons and statistics;
the share whose integrations are visible; installation types; average integrations; the top fifteen
countries and how many report. A failed fetch, a body that doesn't parse, or a null country count
(which the sort can't order) writes nothing; a body without `current` reads as no installations.

Where leadline differs by a decision, the scenario is in DIVERGENCES, and test_ha_platform.py
asserts leadline's side: each of those answers writes a failure row (`fetch_failed` or `parse_failed`)
for the run's date, where the script writes nothing or zeros (ADR: a failure is recorded as a failure).
The rows also hold `ha_versions`, `os_boards` and `supervisor_arch`, which the script drops: the scenarios
that write a row are in DIVERGENCES for them (more data is captured than the script did).
"""

from __future__ import annotations

import random

from scenarios import Scenario
from scenarios.builders.common import exchange

from leadline.sources.home_assistant.tests.builders import HA_PLATFORM_URL, ha_platform

CALLS = ["collect_ha_platform"]
FILE = "ha-platform.jsonl"
EARLIER = '{"date": "2026-09-22", "active_installations": 1000, "reports_integrations": 400}\n'

# leadline's config for these scenarios: the default file is the script's.
LEADLINE_CONFIG: dict[str, str] = {}

MORE_DATA = "more data is captured than the script did"

# The scenarios where leadline's rows differ from the script's by a decision, and the decision.
DIVERGENCES = {
    # leadline also keeps the core versions, the OS boards and the supervisor architectures (null where the
    # answer lacks them), which the script's rows lack.
    "totals_and_the_top_fifteen_countries": MORE_DATA,
    "missing_totals_and_no_installations": MORE_DATA,
    "tied_countries_keep_the_feeds_order": MORE_DATA,
    "null_reports_integrations_count_as_zero": MORE_DATA,
    "failed_fetch_writes_nothing": "ADR: a failure is recorded as a failure",
    "unparseable_body_writes_nothing": "ADR: a failure is recorded as a failure",
    "a_null_country_count_writes_nothing": "ADR: a failure is recorded as a failure",
    "a_body_without_current_writes_zeros": "ADR: a failure is recorded as a failure",
}

# Seventeen made-up country counts, out of order: the row keeps the largest fifteen, largest first.
CODES = ["AA", "BB", "CC", "DD", "EE", "FF", "GG", "HH", "II", "JJ", "KK", "LL", "MM", "NN", "OO", "PP", "QQ"]
COUNTRIES = {code: 10 * (i % 9) + i for i, code in enumerate(CODES)}
NONE_REPORTED = {"current": {"active_installations": 0, "reports_integrations": 5, "countries": None}}
# Sixteen counts with ties, which keep the feed's order: BB comes before OO, and at the cut QQ is kept
# and AA is not.
TIED = {
    "QQ": 1,
    "PP": 9,
    "BB": 20,
    "OO": 20,
    "CC": 30,
    "NN": 12,
    "DD": 13,
    "MM": 14,
    "EE": 15,
    "LL": 16,
    "FF": 17,
    "KK": 18,
    "GG": 19,
    "JJ": 21,
    "HH": 22,
    "AA": 1,
}

SCENARIOS = [
    Scenario(
        "totals_and_the_top_fifteen_countries",
        CALLS,
        [
            exchange(
                HA_PLATFORM_URL,
                ha_platform(random.Random(4), active=250000, reports_integrations=99000, countries=COUNTRIES),
            )
        ],
        seed={FILE: EARLIER},
    ),
    Scenario(
        "missing_totals_and_no_installations",
        CALLS,
        [exchange(HA_PLATFORM_URL, NONE_REPORTED)],
    ),
    Scenario(
        "failed_fetch_writes_nothing",
        CALLS,
        [exchange(HA_PLATFORM_URL, "Service Unavailable", status=503)],
        seed={FILE: EARLIER},
    ),
    Scenario(
        "unparseable_body_writes_nothing",
        CALLS,
        [exchange(HA_PLATFORM_URL, "<html>maintenance</html>")],
        seed={FILE: EARLIER},
    ),
    Scenario(
        "tied_countries_keep_the_feeds_order",
        CALLS,
        [
            exchange(
                HA_PLATFORM_URL, ha_platform(random.Random(4), active=500, reports_integrations=400, countries=TIED)
            )
        ],
    ),
    Scenario(
        "null_reports_integrations_count_as_zero",
        CALLS,
        [exchange(HA_PLATFORM_URL, {"current": {"active_installations": 100, "reports_integrations": None}})],
    ),
    Scenario(
        "a_null_country_count_writes_nothing",
        CALLS,
        [exchange(HA_PLATFORM_URL, {"current": {"active_installations": 100, "countries": {"AA": 3, "BB": None}}})],
        seed={FILE: EARLIER},
    ),
    Scenario(
        "a_body_without_current_writes_zeros",
        CALLS,
        [exchange(HA_PLATFORM_URL, {"history": [], "schema_version": 4})],
        seed={FILE: EARLIER},
    ),
]
