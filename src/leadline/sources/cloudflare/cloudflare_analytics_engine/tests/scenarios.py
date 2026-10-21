"""cloudflare_analytics_engine: a dataset's daily event counts from Cloudflare Workers Analytics Engine, over SQL.

The script's usage collector (LABEL DATASET) sums each event (blob1) per day, and the day's
`active.day` events whose blob2 isn't empty, over the last USAGE_REFETCH_DAYS complete days, and
rewrites those days in its row file, keeping every other line but blank ones (a day given twice
keeps its last line; lines that aren't a row come first); each query is tried up to three times
while the request fails, and a body that doesn't parse isn't retried (a retry would make a request
with no saved response left, and the runner refuses the run). A failure is recorded on the newest
day, only where that day has no good row: `http_code` "0" when the connection failed, and at most
200 characters of an error's body as the reason. A count is cut to a whole number, and an event
given twice for a day keeps the last. With USAGE_REFETCH_DAYS 0 the window has no day: the file is
rewritten, and the collector then fails.

A result that isn't an object, or a result row without its `day`, `event` or `n`, or with `n` null,
stops the collector before it writes anything.

leadline reads a dataset by the layouts its config gives (docs/CONFIGURATION.md, "Source entries"):
LEADLINE_CONFIG has the script's layout until a made-up switch after the run date, so leadline sends
the script's two queries, and a newer layout after it. leadline differs by decision on the scenarios in
DIVERGENCES, asserted in test_cloudflare_analytics_engine.py: an answer it can't read is a
`parse_failed` row, a seed line that isn't a row is kept, and a config that reads no day is refused.

The script reads no row after the switch, so the scenarios of the newer layouts (NEW_LAYOUT) have no
oracle: they carry reviewed rows (`expected`), for a usage dataset across the switch and a screens and
a diagnostics dataset in one layout each, every name and value made up.

The script names the collector, and its row file, after the maintainer's app. The scenarios keep that
name out of the repo: CALLS finds the collector by its shape (`collect_<name>_usage`), and gives its
row file the name `usage-<label>.jsonl` before the run (for the seed) and after it (for the golden).
"""

from __future__ import annotations

import json
import shlex
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from scenarios import CREDENTIALS, Scenario
from scenarios.builders.common import Exchange, exchange, failed

from leadline.sources.cloudflare.tests.builders import (
    CLOUDFLARE_API,
    COUNT_BY_DAY_COLUMNS,
    EVENTS_BY_DAY_COLUMNS,
    analytics_engine_result,
)

LABEL = "example"
DATASET = "example_usage"
FILE = f"usage-{LABEL}.jsonl"
RUN = date(2026, 9, 23)
SQL_URL = f"{CLOUDFLARE_API}/accounts/{CREDENTIALS['CLOUDFLARE_ACCOUNT_ID']}/analytics_engine/sql"


def calls(label: str = LABEL, dataset: str = DATASET) -> list[str]:
    """Find the collector, name the seed file as the script does, run it, and give its file the neutral name."""
    script_file = f'"${{METRICS_DIR}}/usage-${{app}}-{label}.jsonl"'
    neutral_file = f'"${{METRICS_DIR}}/usage-{label}.jsonl"'
    return [
        'app=$(declare -F | sed -En "s/^declare -f collect_([a-z]+)_usage$/\\1/p") && [[ -n "$app" ]] || exit 3',
        f"[[ ! -f {neutral_file} ]] || mv {neutral_file} {script_file}",
        f'"collect_${{app}}_usage" "{label}" "{dataset}"',
        f"[[ ! -f {script_file} ]] || mv {script_file} {neutral_file}",
    ]


def window(days: int) -> str:
    first = RUN - timedelta(days=days)
    return f"timestamp >= toDateTime('{first} 00:00:00') AND timestamp < toDateTime('{RUN} 00:00:00')"


def totals(answer: Any, days: int = 7, status: int = 200) -> Exchange:
    """The events-by-day query, and its answer."""
    query = (  # the script's query, as it sends it
        f"SELECT toStartOfDay(timestamp) AS day, blob1 AS event, SUM(_sample_interval) AS n "  # noqa: S608
        f"FROM {DATASET} WHERE {window(days)} GROUP BY day, event FORMAT JSON"
    )
    return exchange(SQL_URL, answer, method="POST", sent=query, status=status)


def shared(answer: Any, days: int = 7, status: int = 200) -> Exchange:
    """The opted-in active.day-by-day query, and its answer."""
    query = (  # the script's query, as it sends it
        f"SELECT toStartOfDay(timestamp) AS day, SUM(_sample_interval) AS n "  # noqa: S608
        f"FROM {DATASET} WHERE {window(days)} AND blob1 = 'active.day' AND blob2 != '' GROUP BY day FORMAT JSON"
    )
    return exchange(SQL_URL, answer, method="POST", sent=query, status=status)


def unreachable(query: Exchange) -> Exchange:
    """The same query, with the connection failing."""
    return failed(SQL_URL, method="POST", sent=query["request"]["body"])


def events(rows: Sequence[Sequence[Any]]) -> Any:
    return analytics_engine_result(EVENTS_BY_DAY_COLUMNS, rows)


def active(rows: Sequence[Sequence[Any]]) -> Any:
    return analytics_engine_result(COUNT_BY_DAY_COLUMNS, rows)


def as_given(row: Any) -> Any:
    """An events-by-day result of one row given as it is, so it can lack a column."""
    return {**events([]), "data": [row], "rows": 1, "rows_before_limit_at_least": 1}


def day(n: int) -> date:
    return date(2026, 9, n)


WEEK_EVENTS = events(
    [
        (day(15), "active.day", 9),  # before the window: not a row's
        (day(18), "signin.ok", 1),
        (day(18), "active.day", 1),
        (day(21), "refresh.ok", 20),
        (day(21), "active.day", 3),
        (day(22), "active.day", "2"),
        (day(22), "signout.ok", 1.0),
    ]
)
WEEK_SHARED = active([(day(21), 1), (day(22), 2)])
KEPT = '{"date": "2026-09-01", "dataset": "example_usage", "status": "ok", "events": {}, "active_day_shared": 0}\n'
GOOD_NEWEST = (
    '{"date": "2026-09-22", "dataset": "example_usage", "status": "ok", "events": {"active.day": 5}, '
    '"active_day_shared": 1, "captured_at": "2026-09-23T08:00:00+00:00"}\n'
)
FAILED_NEWEST = '{"date": "2026-09-22", "dataset": "example_usage", "status": "fetch_failed", "http_code": "500"}\n'
SERVER_ERROR = '{"errors":[{"message":"try again"}]}'
LONG_ERROR = '{"errors":[{"message":"' + "the query ran out of time; try again later. " * 6 + '"}]}'
UNUSABLE_ROWS = {  # scenario: the one result row, each stopping the collector
    "a_row_without_a_day_writes_nothing": {"event": "active.day", "n": "1"},
    "a_row_without_an_event_writes_nothing": {"day": "2026-09-22 00:00:00", "n": "1"},
    "a_row_without_a_count_writes_nothing": {"day": "2026-09-22 00:00:00", "event": "active.day"},
    "a_null_count_writes_nothing": {"day": "2026-09-22 00:00:00", "event": "active.day", "n": None},
}

SCENARIOS = [
    Scenario("week_of_events", calls(), [totals(WEEK_EVENTS), shared(WEEK_SHARED)], seed={FILE: KEPT}),
    Scenario(
        # 21:30 on the 23rd in Los Angeles is 04:30 on the 24th in UTC, where Cloudflare's days are: the
        # window still ends at the local run date, and captured_at carries the local offset.
        "the_window_ends_at_the_local_run_date",
        calls(),
        [totals(WEEK_EVENTS), shared(WEEK_SHARED)],
        now="2026-09-23T21:30:00",
        tz="America/Los_Angeles",
    ),
    Scenario(
        "retries_after_a_server_error",
        calls(),
        [totals(SERVER_ERROR, status=500), totals(WEEK_EVENTS), shared(WEEK_SHARED)],
    ),
    Scenario(
        "three_failures_mark_the_newest_day",
        calls(),
        [
            totals(SERVER_ERROR, status=500),
            unreachable(totals("")),
            totals("bad gateway", status=502),
        ],
        seed={FILE: KEPT + FAILED_NEWEST},
    ),
    Scenario(
        "three_connection_failures_record_http_code_0",
        calls(),
        [unreachable(totals(""))] * 3,  # each of the three attempts
        seed={FILE: KEPT},
    ),
    Scenario(
        "an_error_body_is_cut_to_200_characters",
        calls(),
        [totals(LONG_ERROR, status=500)] * 3,  # each of the three attempts
        seed={FILE: KEPT},
    ),
    Scenario(
        "a_failure_keeps_a_good_row",
        calls(),
        [totals("", status=503)] * 3,  # each of the three attempts
        seed={FILE: GOOD_NEWEST + KEPT},
    ),
    Scenario(
        "a_failed_second_query_writes_no_days",
        calls(),
        [totals(WEEK_EVENTS), *[shared("", status=503)] * 3],  # each of the second query's three attempts
        seed={FILE: KEPT},
    ),
    Scenario("a_body_that_is_not_json", calls(), [totals("<html>Bad gateway</html>")]),
    Scenario("a_result_without_data", calls(), [totals('{"errors":[{"message":"syntax error"}]}')]),
    Scenario("a_result_that_is_not_an_object_writes_nothing", calls(), [totals([])], seed={FILE: KEPT}),
    *[
        Scenario(name, calls(), [totals(as_given(row)), shared(WEEK_SHARED)], seed={FILE: KEPT})
        for name, row in UNUSABLE_ROWS.items()
    ],
    Scenario(
        "a_count_is_cut_to_a_whole_number_and_a_repeated_event_keeps_the_last",
        calls(),
        [
            totals(events([(day(22), "active.day", "2.9"), (day(22), "signin.ok", 3), (day(22), "signin.ok", 5)])),
            shared(active([(day(22), 1), (day(22), 2)])),
        ],
    ),
    Scenario(
        "rewrites_the_window_and_keeps_every_other_line",
        calls(),
        [totals(events([(day(21), "active.day", 4)]), days=3), shared(active([]), days=3)],
        config={"USAGE_REFETCH_DAYS": "3"},
        seed={
            FILE: '{"date": "2026-09-21", "dataset": "example_usage", "status": "ok", "events": {"active.day": 1}}\n'
            + KEPT
            + "not json\n"
            + '{"no_date": true}\n'
            + '{"date": "2026-09-19", "dataset": "example_usage", "status": "ok", "events": {}}\n'
        },
    ),
    Scenario(
        "a_repeated_day_keeps_its_last_line_and_blank_lines_go",
        calls(),
        [totals(WEEK_EVENTS), shared(WEEK_SHARED)],
        seed={FILE: KEPT + "[1]\n\nnull\n" + KEPT.replace('"active_day_shared": 0', '"active_day_shared": 3')},
    ),
    Scenario(
        "no_refetch_days_rewrites_the_file_and_writes_no_day",
        calls(),
        [totals(events([]), days=0), shared(active([]), days=0)],
        config={"USAGE_REFETCH_DAYS": "0"},
        seed={FILE: FAILED_NEWEST + KEPT},
    ),
    Scenario(
        "a_date_that_is_not_a_string_stops_the_write",
        calls(),
        [totals(WEEK_EVENTS), shared(WEEK_SHARED)],
        seed={FILE: KEPT + '{"date": null}\n'},
    ),
    Scenario("refuses_a_dataset_that_is_not_an_identifier", calls(dataset="Example-Usage")),
    Scenario("skips_without_credentials", calls(), env={"CLOUDFLARE_API_TOKEN": CREDENTIALS["CLOUDFLARE_API_TOKEN"]}),
]


# --- leadline's config, and where it differs ---

SWITCH = "2026-09-25T12:00:00Z"  # made up: the time the dataset's layout changed
EVENT = r"[a-z]+(\.[a-z]+)*"  # an event's name, as `signin.ok`
VERSION = r"([0-9]+\.[0-9]+\.[0-9]+)?"  # an app version, or none
OLD_LAYOUT = {  # the script's: blob1 the event, blob2 a per-person id counted where set, never read
    "until": SWITCH,
    "dimensions": {"event": {"column": "blob1", "pattern": EVENT}},
    "private": ["blob2"],
    "fields": [
        {"key": "events", "by": "event"},
        {"key": "active_day_shared", "count_nonempty": "blob2", "where": {"event": "active.day"}},
    ],
}
USAGE_LAYOUT = {  # after the switch: the event in index1, a dimension per blob, the id in blob5
    "dimensions": {
        "event": {"column": "index1", "pattern": EVENT},
        "country": {"column": "blob1", "pattern": "[A-Z][A-Z0-9]"},
        "region": {"column": "blob2", "pattern": "([A-Z]{2})?", "only_when": {"country": ["US"]}},
        "browser": {"column": "blob3", "values": ["chrome", "firefox", "safari", "other"]},
        "app_version": {"column": "blob4", "pattern": VERSION},
    },
    "private": ["blob5"],
    "fields": [
        {"key": "events", "by": "event"},
        {"key": "active_day_shared", "count_nonempty": "blob5", "where": {"event": "active.day"}},
        {"key": "country", "by": "country"},
        {"key": "region", "by": "region"},
        {"key": "browser", "by": "browser"},
        {"key": "app_version", "by": "app_version"},
    ],
}


def leadline_config(scenario: Scenario) -> list[dict[str, Any]]:
    """leadline's config for `scenario`: the dataset and USAGE_REFETCH_DAYS of the script's call, or NEW_LAYOUT's."""
    if scenario.name in NEW_LAYOUT:
        return NEW_LAYOUT[scenario.name]
    dataset = shlex.split(scenario.calls[2])[2]  # the call's DATASET
    refetch = int(str(scenario.config.get("USAGE_REFETCH_DAYS", 7)))
    return [{"dataset": dataset, "file": FILE, "refetch_days": refetch, "layouts": [OLD_LAYOUT, USAGE_LAYOUT]}]


UNREADABLE = "ADR: a failure is recorded as a failure"
REFUSED = "a config validate refuses stops the run before anything runs"
# The scenarios where leadline's rows differ from the script's by a decision, and the decision.
DIVERGENCES = {
    "a_result_that_is_not_an_object_writes_nothing": UNREADABLE,
    **dict.fromkeys(UNUSABLE_ROWS, UNREADABLE),
    "a_date_that_is_not_a_string_stops_the_write": "ADR: store never fails on a line it can't use",
    # validate refuses the config, so nothing runs (docs/ARCHITECTURE.md, "The run"): a window of no day,
    # where the script rewrites the file; a dataset name that isn't an identifier, as the script refuses it
    "no_refetch_days_rewrites_the_file_and_writes_no_day": REFUSED,
    "refuses_a_dataset_that_is_not_an_identifier": REFUSED,
}


# --- the newer layouts, which only leadline reads: reviewed rows ---

NEW_RUN = date(2026, 9, 27)
NEW_NOW = f"{NEW_RUN}T23:31:05"
NEW_STAMP = f"{NEW_NOW}+00:00"


def since(first: str) -> str:
    """The window from `first` to NEW_RUN's midnight."""
    return f"timestamp >= toDateTime('{first}') AND timestamp < toDateTime('{NEW_RUN} 00:00:00')"


def query(answer: Any, sql: str) -> Exchange:
    return exchange(SQL_URL, answer, method="POST", sent=f"{sql} FORMAT JSON")


def total(dataset: str, where: str, column: str, name: str, rows: Sequence[Sequence[Any]], sums: str = "") -> Exchange:
    """A total by one dimension, and its answer: SUM(_sample_interval), times the `sums` column if given."""
    measure = f"_sample_interval * {sums}" if sums else "_sample_interval"
    sql = (
        f"SELECT toStartOfDay(timestamp) AS day, {column} AS {name}, SUM({measure}) AS n "  # noqa: S608
        f"FROM {dataset} WHERE {where} GROUP BY day, {name}"
    )
    kind = "Float64" if sums else "UInt64"
    return query(analytics_engine_result({"day": "DateTime", name: "String", "n": kind}, rows), sql)


def count(dataset: str, where: str, rows: Sequence[Sequence[Any]]) -> Exchange:
    """A count by day where `where` holds, and its answer."""
    sql = f"SELECT toStartOfDay(timestamp) AS day, SUM(_sample_interval) AS n FROM {dataset} WHERE {where} GROUP BY day"  # noqa: S608
    return query(active(rows), sql)


def row(day_: date, dataset: str, **fields: Any) -> str:
    return json.dumps(
        {"date": day_.isoformat(), "dataset": dataset, "status": "ok", **fields, "captured_at": NEW_STAMP}
    )


OLD_SIDE = "timestamp >= toDateTime('2026-09-24 00:00:00') AND timestamp < toDateTime('2026-09-25 12:00:00')"
NEW_SIDE = since("2026-09-25 12:00:00")
USAGE_SWITCH_RESPONSES = [
    total(
        DATASET,
        OLD_SIDE,
        "blob1",
        "event",
        [(day(24), "active.day", 4), (day(24), "signin.ok", 2), (day(25), "active.day", 3)],
    ),
    count(DATASET, f"{OLD_SIDE} AND blob1 = 'active.day' AND blob2 != ''", [(day(24), 1), (day(25), 2)]),
    total(
        DATASET,
        NEW_SIDE,
        "index1",
        "event",
        [(day(25), "active.day", 2), (day(25), "signin.ok", 1), (day(26), "active.day", 5)],
    ),
    count(DATASET, f"{NEW_SIDE} AND index1 = 'active.day' AND blob5 != ''", [(day(25), 1), (day(26), 3)]),
    total(
        DATASET,
        NEW_SIDE,
        "blob1",
        "country",
        [(day(25), "GB", 1), (day(25), "US", 2), (day(26), "US", 4), (day(26), "XX", 1)],
    ),
    total(
        DATASET,
        f"{NEW_SIDE} AND blob1 = 'US'",
        "blob2",
        "region",
        [(day(25), "CA", 2), (day(26), "NY", 3), (day(26), "", 1)],
    ),
    total(DATASET, NEW_SIDE, "blob3", "browser", [(day(25), "firefox", 3), (day(26), "chrome", 5)]),
    total(DATASET, NEW_SIDE, "blob4", "app_version", [(day(25), "", 1), (day(25), "1.4.0", 2), (day(26), "1.4.1", 5)]),
    count(DATASET, f"{NEW_SIDE} AND blob1 != 'US' AND blob2 != ''", []),  # the layout check: no region outside the US
]
USAGE_SWITCH_ROWS = (
    "\n".join(
        [
            KEPT.rstrip("\n"),
            row(day(24), DATASET, events={"active.day": 4, "signin.ok": 2}, active_day_shared=1),  # the script's row
            row(
                day(25),  # the switch day: each layout's events on its side of the switch, added up
                DATASET,
                events={"active.day": 5, "signin.ok": 1},
                active_day_shared=3,
                country={"GB": 1, "US": 2},
                region={"CA": 2},
                browser={"firefox": 3},
                app_version={"": 1, "1.4.0": 2},
            ),
            row(
                day(26),
                DATASET,
                events={"active.day": 5},
                active_day_shared=3,
                country={"US": 4, "XX": 1},
                region={"": 1, "NY": 3},
                browser={"chrome": 5},
                app_version={"1.4.1": 5},
            ),
        ]
    )
    + "\n"
)

SCREENS = "example_screens"
SCREENS_LAYOUT = {
    "dimensions": {
        "screen": {"column": "index1", "values": ["home", "list", "detail"]},
        "app_version": {"column": "blob1", "pattern": VERSION},
    },
    "fields": [
        {"key": "views", "by": "screen", "sum": "double1"},
        {"key": "seconds", "by": "screen", "sum": "double2"},
        {"key": "app_version", "by": "app_version"},
    ],
}
SCREENS_WINDOW = since("2026-09-25 00:00:00")  # two days
SCREENS_RESPONSES = [
    total(
        SCREENS,
        SCREENS_WINDOW,
        "index1",
        "screen",
        [(day(25), "home", 3.0), (day(25), "list", 1.0), (day(26), "home", 2.0)],
        "double1",
    ),
    total(
        SCREENS,
        SCREENS_WINDOW,
        "index1",
        "screen",
        [(day(25), "home", 42.5), (day(25), "list", 7.9), (day(26), "home", 30.0)],
        "double2",
    ),
    total(SCREENS, SCREENS_WINDOW, "blob1", "app_version", [(day(25), "1.4.0", 4), (day(26), "1.4.1", 2)]),
]
SCREENS_ROWS = "".join(
    line + "\n"
    for line in [
        # seconds are cut to whole ones, as every count is
        row(day(25), SCREENS, views={"home": 3, "list": 1}, seconds={"home": 42, "list": 7}, app_version={"1.4.0": 4}),
        row(day(26), SCREENS, views={"home": 2}, seconds={"home": 30}, app_version={"1.4.1": 2}),
    ]
)

DIAGNOSTICS = "example_diagnostics"
DIAGNOSTICS_LAYOUT = {
    "dimensions": {
        "code": {"column": "index1", "pattern": "[A-Z][A-Z_]*"},
        "app_version": {"column": "blob1", "pattern": VERSION},
        "browser": {"column": "blob2", "values": ["chrome", "firefox", "safari", "other"]},
    },
    "private": ["blob3"],
    "fields": [
        {"key": "code", "by": "code"},
        {"key": "browser", "by": "browser"},
        {"key": "app_version", "by": "app_version"},
    ],
}
DIAGNOSTICS_RESPONSES = [
    total(DIAGNOSTICS, SCREENS_WINDOW, "index1", "code", [(day(26), "E_TIMEOUT", 2)]),
    total(DIAGNOSTICS, SCREENS_WINDOW, "blob2", "browser", [(day(26), "safari", 2)]),
    total(DIAGNOSTICS, SCREENS_WINDOW, "blob1", "app_version", [(day(26), "1.4.1", 2)]),
]
DIAGNOSTICS_ROWS = "".join(
    line + "\n"
    for line in [
        row(day(25), DIAGNOSTICS, code={}, browser={}, app_version={}),  # a quiet day is a row of zeros
        row(day(26), DIAGNOSTICS, code={"E_TIMEOUT": 2}, browser={"safari": 2}, app_version={"1.4.1": 2}),
    ]
)

# Each newer-layout scenario's config: a usage dataset across the switch, and two datasets in one layout.
NEW_LAYOUT: Mapping[str, list[dict[str, Any]]] = {
    "reads_each_layout_on_its_side_of_the_switch": [
        {"dataset": DATASET, "file": FILE, "refetch_days": 3, "layouts": [OLD_LAYOUT, USAGE_LAYOUT]}
    ],
    "totals_a_measure_by_a_dimension": [{"dataset": SCREENS, "refetch_days": 2, "layouts": [SCREENS_LAYOUT]}],
    "totals_each_dimension_on_its_own": [{"dataset": DIAGNOSTICS, "refetch_days": 2, "layouts": [DIAGNOSTICS_LAYOUT]}],
}
SCREENS_FILE = f"cloudflare-analytics-engine-{SCREENS}.jsonl"
DIAGNOSTICS_FILE = f"cloudflare-analytics-engine-{DIAGNOSTICS}.jsonl"

SCENARIOS += [
    Scenario(
        "reads_each_layout_on_its_side_of_the_switch",
        [],
        USAGE_SWITCH_RESPONSES,
        seed={FILE: KEPT},
        now=NEW_NOW,
        expected={FILE: USAGE_SWITCH_ROWS},
    ),
    Scenario(
        "totals_a_measure_by_a_dimension", [], SCREENS_RESPONSES, now=NEW_NOW, expected={SCREENS_FILE: SCREENS_ROWS}
    ),
    Scenario(
        "totals_each_dimension_on_its_own",
        [],
        DIAGNOSTICS_RESPONSES,
        now=NEW_NOW,
        expected={DIAGNOSTICS_FILE: DIAGNOSTICS_ROWS},
    ),
]
