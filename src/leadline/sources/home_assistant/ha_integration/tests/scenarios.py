"""ha_integration: a custom integration's installs, from Home Assistant's analytics, and its rank in the feed.

The feed, custom_integrations.json, holds every custom integration's installs by version; the
entry's `integration` names the one to read. The script's collect_hacs reads the feed once a night
and writes one row for the run's date into hacs.jsonl, replacing any row for that date. A failed
fetch, a feed that doesn't parse or isn't an object, an integration entry that isn't an object, or a
total the ranking can't compare writes nothing. A feed that lists no integrations at all reads as
no installs and no rank.

Where leadline differs by a decision, the scenario is in DIVERGENCES, and test_ha_integration.py
asserts leadline's side: each of those answers writes a failure row (`fetch_failed` or `parse_failed`)
for the run's date, where the script writes nothing, or zeros for a feed that lists no integrations
(ADR: a failure is recorded as a failure).
"""

from __future__ import annotations

import random

from scenarios import Scenario
from scenarios.builders.common import exchange

from leadline.sources.home_assistant.tests.builders import (
    HA_INTEGRATIONS_URL,
    ha_integration_entry,
    ha_integrations_feed,
)

CALLS = ["collect_hacs"]
INTEGRATION = "cable_modem_monitor"  # the script's INTEGRATION_ID
EARLIER = '{"date": "2026-09-22", "total": 90, "versions": {"3.12.0": 90}, "rank": 3, "tracked_integrations": 60}\n'

# leadline's config for these scenarios: the script's integration, writing the script's file.
LEADLINE_CONFIG = [{"integration": INTEGRATION, "file": "hacs.jsonl"}]

# The scenarios where leadline's rows differ from the script's by a decision, and the decision.
DIVERGENCES = {
    "failed_fetch_writes_nothing": "ADR: a failure is recorded as a failure",
    "unparseable_feed_writes_nothing": "ADR: a failure is recorded as a failure",
    "a_feed_not_an_object_writes_nothing": "ADR: a failure is recorded as a failure",
    "the_integrations_entry_not_an_object_writes_nothing": "ADR: a failure is recorded as a failure",
    "a_null_total_writes_nothing": "ADR: a failure is recorded as a failure",
    "an_empty_feed_reads_as_no_installs": "ADR: a failure is recorded as a failure",
}


SCENARIOS = [
    Scenario(
        "ranks_among_the_feed",
        CALLS,
        [
            exchange(
                HA_INTEGRATIONS_URL,
                ha_integrations_feed(
                    random.Random(1), {INTEGRATION: ha_integration_entry(95, {"3.13.1": 40, "3.12.0": 55})}
                ),
            )
        ],
        seed={"hacs.jsonl": EARLIER},
    ),
    Scenario(
        "sorts_versions_numerically_with_prereleases_after_the_release",
        CALLS,
        [
            exchange(
                HA_INTEGRATIONS_URL,
                {
                    INTEGRATION: ha_integration_entry(
                        21, {"3.10.0": 1, "3.9.2": 2, "3.13.0-beta.2": 3, "3.13.0": 4, "3.13.0-beta.10": 5, "v2": 6}
                    ),
                    "other": ha_integration_entry(5),
                },
            )
        ],
    ),
    Scenario(
        "ties_rank_by_name_descending",
        CALLS,
        [
            exchange(
                HA_INTEGRATIONS_URL,
                {
                    "aaa_first": ha_integration_entry(10),
                    INTEGRATION: ha_integration_entry(10),
                    "zzz_last": ha_integration_entry(10),
                },
            )
        ],
    ),
    Scenario(
        "integration_missing_from_the_feed",
        CALLS,
        [exchange(HA_INTEGRATIONS_URL, ha_integrations_feed(random.Random(2), {}, others=30))],
    ),
    Scenario(
        "entries_without_a_total_or_not_objects",
        CALLS,
        [
            exchange(
                HA_INTEGRATIONS_URL,
                {
                    INTEGRATION: {"versions": {"1.0.0": 3}},
                    "no_total": {"versions": {}},
                    "counted": ha_integration_entry(7),
                    "a_list": [1, 2],
                    "a_number": 12,
                },
            )
        ],
    ),
    Scenario(
        "the_integration_without_versions",
        CALLS,
        [exchange(HA_INTEGRATIONS_URL, {INTEGRATION: {"total": 12}, "other": ha_integration_entry(5)})],
    ),
    Scenario(
        # 01:15 on the 24th in Kolkata is 19:45 on the 23rd in UTC: the row takes the local date, and
        # captured_at the local time with its +05:30 offset.
        "the_row_is_dated_and_stamped_in_local_time",
        CALLS,
        [exchange(HA_INTEGRATIONS_URL, {INTEGRATION: ha_integration_entry(96, {"3.13.1": 96})})],
        seed={"hacs.jsonl": EARLIER},
        now="2026-09-24T01:15:00",
        tz="Asia/Kolkata",
    ),
    Scenario(
        "rerun_replaces_the_days_row",
        CALLS,
        [exchange(HA_INTEGRATIONS_URL, {INTEGRATION: ha_integration_entry(96, {"3.13.1": 96})})],
        seed={"hacs.jsonl": EARLIER + '{"date": "2026-09-23", "total": 1}\n'},
    ),
    Scenario(
        # Blank lines are dropped, and a last line without its newline gets one before the new row.
        "a_blank_line_and_a_missing_final_newline_in_the_file",
        CALLS,
        [exchange(HA_INTEGRATIONS_URL, {INTEGRATION: ha_integration_entry(96, {"3.13.1": 96})})],
        seed={"hacs.jsonl": "\n" + EARLIER + "\n" + '{"date": "2026-09-21", "total": 88}'},
    ),
    Scenario(
        "failed_fetch_writes_nothing",
        CALLS,
        [exchange(HA_INTEGRATIONS_URL, "Service Unavailable", status=503)],
        seed={"hacs.jsonl": EARLIER},
    ),
    Scenario(
        "unparseable_feed_writes_nothing",
        CALLS,
        [exchange(HA_INTEGRATIONS_URL, "<html>maintenance</html>")],
        seed={"hacs.jsonl": EARLIER},
    ),
    Scenario(
        "a_feed_not_an_object_writes_nothing",
        CALLS,
        [exchange(HA_INTEGRATIONS_URL, [ha_integration_entry(95)])],
        seed={"hacs.jsonl": EARLIER},
    ),
    Scenario(
        "the_integrations_entry_not_an_object_writes_nothing",
        CALLS,
        [exchange(HA_INTEGRATIONS_URL, {INTEGRATION: [95], "other": ha_integration_entry(5)})],
        seed={"hacs.jsonl": EARLIER},
    ),
    Scenario(
        # Another integration's null total can't be ranked against the numbers: the script's sort fails.
        "a_null_total_writes_nothing",
        CALLS,
        [exchange(HA_INTEGRATIONS_URL, {INTEGRATION: ha_integration_entry(95), "other": {"total": None}})],
        seed={"hacs.jsonl": EARLIER},
    ),
    Scenario(
        "an_empty_feed_reads_as_no_installs",
        CALLS,
        [exchange(HA_INTEGRATIONS_URL, {})],
        seed={"hacs.jsonl": EARLIER},
    ),
]
