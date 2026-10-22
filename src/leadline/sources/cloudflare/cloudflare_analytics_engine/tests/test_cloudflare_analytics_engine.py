"""cloudflare_analytics_engine: the script's rows on its scenarios, the newer layouts' reviewed rows, and
leadline's side of each decided divergence.

The rows are compared with the goldens through tests/port.py; the scenarios where leadline differs by
a decision are listed in scenarios.py (DIVERGENCES) and asserted here.
The privacy rules are tested on every query leadline sends: a private column is never read, and no
query totals two dimensions; a value outside the layout reaches no file, log line or finding.
"""

from __future__ import annotations

import copy
import json
import re
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any

import port
import pytest
import scenarios
from scenarios import CREDENTIALS, Scenario
from scenarios.builders.common import exchange, failed

from leadline.config import ConfigError
from leadline.context import Correction, Critical, Finding
from leadline.http import Response, SavedResponses
from leadline.sources.cloudflare import cloudflare_analytics_engine
from leadline.sources.cloudflare.cloudflare_analytics_engine.tests import scenarios as ae
from leadline.sources.cloudflare.tests.builders import analytics_engine_result

NAME = "cloudflare_analytics_engine"
FILE = ae.FILE
NOW = "2026-09-23T23:31:05+00:00"  # the script's scenarios' clock, as captured_at gives it
SWITCH_SCENARIO = "reads_each_layout_on_its_side_of_the_switch"


def failure_row(status: str, http_code: str, reason: str) -> str:
    row = {"date": "2026-09-22", "dataset": ae.DATASET, "status": status, "http_code": http_code, "reason": reason}
    return json.dumps({**row, "captured_at": NOW}) + "\n"


def golden(name: str) -> str:
    rows: str = scenarios.load_golden(NAME)[name]["rows"][FILE]
    return rows


def usage(**changes: Any) -> dict[str, Any]:
    """The switch scenario's entry, with `changes`."""
    return {**ae.NEW_LAYOUT[SWITCH_SCENARIO][0], **changes}


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert cloudflare_analytics_engine.NAME == NAME
    assert cloudflare_analytics_engine.ENV == ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID")
    assert cloudflare_analytics_engine.LAGGING is True


@pytest.mark.parametrize(
    "cfg",
    [
        ae.leadline_config(port.scenario(NAME, "week_of_events")),
        *ae.NEW_LAYOUT.values(),
        [
            {
                "dataset": "a",
                "layouts": [
                    {"dimensions": {"e": {"column": "blob1", "values": ["x"]}}, "fields": [{"key": "k", "by": "e"}]}
                ],
            }
        ],
    ],
    ids=["the script's layout, then a newer one", "usage", "screens", "diagnostics", "the least"],
)
def test_a_valid_config_has_no_problems(cfg: Any) -> None:
    assert cloudflare_analytics_engine.validate(cfg) == []


DIMENSION = {"column": "blob1", "values": ["x"]}
FIELD = {"key": "k", "by": "e"}
NAME_PROBLEM = (
    "a name is lowercase letters, digits and _, from a letter, and not day, n, timestamp, dataset or a column's name"
)


def one(**changes: Any) -> list[dict[str, Any]]:
    """A config of one entry with one layout, one dimension `e` and one field, with `changes` to the layout."""
    return [{"dataset": "a", "layouts": [{"dimensions": {"e": DIMENSION}, "fields": [FIELD], **changes}]}]


def dimension(**spec: Any) -> list[dict[str, Any]]:
    return one(dimensions={"e": spec})


def field(**spec: Any) -> list[dict[str, Any]]:
    return one(fields=[spec])


OLD = {"until": "2026-09-25T12:00:00Z", "dimensions": {"e": DIMENSION}, "fields": [FIELD]}
AT = "[0].layouts[0]"  # the one layout's path
ROW_KEYS = ("date", "dataset", "status", "http_code", "reason", "captured_at")
KINDS = "must give by (a total by a dimension) or count_nonempty (a count where a column is set), one of the two"


@pytest.mark.parametrize("name", ["timestamp", "dataset", "index1", "blob2", "blob20", "double1", "double20"])
def test_a_dimension_named_as_a_column_or_a_query_alias_is_a_problem(name: str) -> None:
    cfg = one(dimensions={name: DIMENSION})

    assert cloudflare_analytics_engine.validate(cfg) == [f"{AT}.dimensions.{name}: {NAME_PROBLEM}"]


@pytest.mark.parametrize(
    ("cfg", "problem"),
    [
        ({"dataset": "a"}, ": must be a list of entries"),
        ([{**one()[0], "dataset": "Example-Usage"}], "[0].dataset: must be a name of lowercase letters, digits and _"),
        ([{**one()[0], "dataset": 5}], "[0].dataset: must be a name of lowercase letters, digits and _"),
        ([{**one()[0], "refetch_days": 0}], "[0].refetch_days: must be a whole number of days, 1 to 90"),
        ([{**one()[0], "refetch_days": True}], "[0].refetch_days: must be a whole number of days, 1 to 90"),
        ([{**one()[0], "refetch_days": 91}], "[0].refetch_days: must be a whole number of days, 1 to 90"),
        ([{"dataset": "a"}], "[0].layouts: must be a non-empty list of layouts, oldest first"),
        ([{"dataset": "a", "layouts": ["x"]}], "[0].layouts[0]: must be an object"),
        (one(until="2026-09-25T12:00:00Z"), f"{AT}.until: must be left out: the newest layout has no end"),
        (
            [{"dataset": "a", "layouts": [{**OLD, "until": None}, one()[0]["layouts"][0]]}],
            f"{AT}.until: must be a UTC time, as 2026-09-25T12:00:00Z",
        ),
        (
            [{"dataset": "a", "layouts": [{**OLD, "until": "2026-09-25 12:00"}, one()[0]["layouts"][0]]}],
            f"{AT}.until: must be a UTC time, as 2026-09-25T12:00:00Z",
        ),
        (
            [{"dataset": "a", "layouts": [OLD, OLD, one()[0]["layouts"][0]]}],
            "[0].layouts[1].until: must be later than the layout's before it",
        ),
        (one(extra=1), f"{AT}: unknown keys: extra"),
        (one(dimensions=[]), f"{AT}.dimensions: must be an object of named dimensions"),
        (
            one(dimensions={"Event": DIMENSION}),
            f"{AT}.dimensions.Event: {NAME_PROBLEM}",
        ),
        (
            one(dimensions={"day": DIMENSION}),
            f"{AT}.dimensions.day: {NAME_PROBLEM}",
        ),
        (dimension(column="blob21", values=["x"]), f"{AT}.dimensions.e.column: must be index1 or blob1 to blob20"),
        (dimension(column="double1", values=["x"]), f"{AT}.dimensions.e.column: must be index1 or blob1 to blob20"),
        (one(private=["blob1"]), f"{AT}.dimensions.e.column: blob1 is private, and a private column is never read"),
        (one(private="blob1"), f"{AT}.private: must be a list of columns: index1 or blob1 to blob20"),
        (dimension(column="blob1"), f"{AT}.dimensions.e: must give values or a pattern, one of the two"),
        (
            dimension(column="blob1", values=["x"], pattern="x"),
            f"{AT}.dimensions.e: must give values or a pattern, one of the two",
        ),
        (dimension(column="blob1", values=[]), f"{AT}.dimensions.e.values: must be a non-empty list of strings"),
        (dimension(column="blob1", pattern="("), f"{AT}.dimensions.e.pattern: must be a regular expression"),
        (dimension(column="blob1", pattern=5), f"{AT}.dimensions.e.pattern: must be a regular expression"),
        (dimension(column="blob1", values=["x"], other=1), f"{AT}.dimensions.e: unknown keys: other"),
        (
            dimension(column="blob1", values=["x"], only_when={"e": ["x"]}),
            f"{AT}.dimensions.e.only_when: must name another dimension and the values of it that allow this one",
        ),
        (
            one(dimensions={"e": DIMENSION, "f": {"column": "blob2", "values": ["y"], "only_when": {"e": ["z"]}}}),
            f'{AT}.dimensions.f.only_when: "z" is not a value e allows',
        ),
        (one(fields=[]), f"{AT}.fields: must be a non-empty list of fields"),
        (one(fields=["x"]), f"{AT}.fields[0]: must be an object"),
        (
            field(key="status", by="e"),
            f"{AT}.fields[0].key: must be a string, not one of the row's own keys: {', '.join(ROW_KEYS)}",
        ),
        (one(fields=[FIELD, FIELD]), f"{AT}.fields[1].key: k is given twice"),
        (
            field(key="k"),
            f"{AT}.fields[0]: {KINDS}",
        ),
        (
            field(key="k", by="e", count_nonempty="blob2"),
            f"{AT}.fields[0]: {KINDS}",
        ),
        (field(key="k", by="f"), f"{AT}.fields[0].by: names no dimension of the layout"),
        (field(key="k", by="e", sum="blob2"), f"{AT}.fields[0].sum: must be double1 to double20"),
        (field(key="k", by="e", where={"e": "x"}), f"{AT}.fields[0]: unknown keys: where"),
        (field(key="k", count_nonempty="double1"), f"{AT}.fields[0].count_nonempty: must be index1 or blob1 to blob20"),
        (
            field(key="k", count_nonempty="blob2", where={"e": "y"}),
            f"{AT}.fields[0].where: must name a dimension and one value it allows, without ' or \\",
        ),
        (
            one(
                dimensions={"e": {"column": "blob1", "pattern": ".*"}},
                fields=[{"key": "k", "count_nonempty": "blob2", "where": {"e": "it's"}}],
            ),
            f"{AT}.fields[0].where: must name a dimension and one value it allows, without ' or \\",
        ),
        (
            [
                {
                    "dataset": "a",
                    "layouts": [OLD, {**one()[0]["layouts"][0], "fields": [{"key": "k", "count_nonempty": "blob2"}]}],
                }
            ],
            "[0].layouts[1].fields[0].key: k is a total in one layout and a count in another",
        ),
        (
            [{**one()[0], "file": "x-{label}.jsonl"}],
            "[0].file: 'x-{label}.jsonl' uses {label}, which is not one of: dataset",
        ),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problem: str) -> None:
    assert cloudflare_analytics_engine.validate(cfg) == [problem]


def test_files_are_the_default_or_the_template_of_each_entry() -> None:
    cfg = [one()[0], {**one()[0], "dataset": "b", "file": "usage-{dataset}.jsonl"}]

    assert cloudflare_analytics_engine.files(cfg) == {
        "[0]": "cloudflare-analytics-engine-a.jsonl",
        "[1]": "usage-b.jsonl",
    }


# --- the script's rows, and the newer layouts' reviewed rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_golden(scenario: Scenario, tmp_path: Path) -> None:
    port.run(cloudflare_analytics_engine, ae.leadline_config(scenario), scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == [
        "a_date_that_is_not_a_string_stops_the_write",
        "a_null_count_writes_nothing",
        "a_result_that_is_not_an_object_writes_nothing",
        "a_row_without_a_count_writes_nothing",
        "a_row_without_a_day_writes_nothing",
        "a_row_without_an_event_writes_nothing",
        "no_refetch_days_rewrites_the_file_and_writes_no_day",
        "refuses_a_dataset_that_is_not_an_identifier",
    ]


def run(name: str, tmp_path: Path, **changes: Any) -> port.PortRun:
    scenario = replace(port.scenario(NAME, name), **changes)
    return port.run(cloudflare_analytics_engine, ae.leadline_config(port.scenario(NAME, name)), scenario, tmp_path)


def test_it_is_skipped_without_its_credentials(tmp_path: Path) -> None:
    done = run("skips_without_credentials", tmp_path)

    assert (done.status, done.rows) == (0, {})
    assert "SKIP: cloudflare_analytics_engine (CLOUDFLARE_ACCOUNT_ID not set)" in done.log


def test_retries_wait_10_then_20_seconds(tmp_path: Path) -> None:
    done = run("three_failures_mark_the_newest_day", tmp_path)

    assert done.sleeps == [10, 20]
    assert (done.status, done.ctx.critical) == (0, [])


@pytest.mark.parametrize("code", [401, 403])
def test_rejected_credentials_are_not_retried_and_are_critical_at_once(tmp_path: Path, code: int) -> None:
    done = run("three_failures_mark_the_newest_day", tmp_path, responses=[ae.totals("denied", status=code)])

    assert done.rows[FILE] == ae.KEPT + failure_row("fetch_failed", str(code), "denied")
    assert done.sleeps == []
    assert done.status == 1
    assert done.ctx.critical == [Critical(NAME, FILE, f"{ae.DATASET}: HTTP {code} from Analytics Engine's SQL API")]


def test_a_failure_keeps_a_good_row_and_says_so(tmp_path: Path) -> None:
    done = run("a_failure_keeps_a_good_row", tmp_path)

    assert done.status == 0
    assert f"WARN: kept the good row for 2026-09-22 in {FILE}; fetch_failed not recorded" in done.log


def test_a_reread_day_whose_counts_changed_is_a_correction(tmp_path: Path) -> None:
    stored = (
        '{"date": "2026-09-21", "dataset": "example_usage", "status": "ok", "events": {"active.day": 2}, '
        '"active_day_shared": 1}\n'
    )

    done = run("week_of_events", tmp_path, seed={FILE: stored})

    events = {"active.day": 3, "refresh.ok": 20}
    assert done.ctx.corrections == [
        Correction(NAME, FILE, "2026-09-21", {"events": {"active.day": 2}}, {"events": events})
    ]


# --- decided divergences ---


def test_a_result_that_is_not_an_object_is_a_parse_failed_row(tmp_path: Path) -> None:
    """ADR: a failure is recorded as a failure (the script crashes and writes nothing)."""
    done = run("a_result_that_is_not_an_object_writes_nothing", tmp_path)

    assert done.rows == {FILE: ae.KEPT + failure_row("parse_failed", "200", "response had no data array")}
    assert done.status == 0


@pytest.mark.parametrize("name", list(ae.UNUSABLE_ROWS))
def test_a_result_row_it_cant_read_is_a_parse_failed_row(tmp_path: Path, name: str) -> None:
    """ADR: a failure is recorded as a failure (the script crashes and writes nothing)."""
    done = run(name, tmp_path)

    assert done.rows == {FILE: ae.KEPT + failure_row("parse_failed", "200", "response had an unexpected shape")}
    assert done.status == 0


@pytest.mark.parametrize(
    "result_row",
    [
        {"day": 5, "event": "active.day", "n": "1"},
        {"day": "2026-09-22 00:00:00", "event": 5, "n": "1"},
        {"day": "2026-09-22 00:00:00", "event": "active.day", "n": "many"},
        {"day": "2026-09-22 00:00:00", "event": "active.day", "n": True},
        {"day": "2026-09-22 00:00:00", "event": "active.day", "n": "inf"},
        "x",
    ],
    ids=[
        "a day not a string",
        "a value not a string",
        "a count not a number",
        "a count true",
        "a count infinite",
        "a row not an object",
    ],
)
def test_any_result_row_it_cant_read_is_a_parse_failed_row(tmp_path: Path, result_row: Any) -> None:
    done = run(
        "a_row_without_a_day_writes_nothing",
        tmp_path,
        responses=[ae.totals(ae.as_given(result_row)), ae.shared(ae.WEEK_SHARED)],
    )

    assert done.rows == {FILE: ae.KEPT + failure_row("parse_failed", "200", "response had an unexpected shape")}


def test_a_seed_date_that_is_not_a_string_is_kept_and_the_window_written(tmp_path: Path) -> None:
    """ADR: store never fails on a line it can't use (the script's sort crashes, and it writes nothing)."""
    done = run("a_date_that_is_not_a_string_stops_the_write", tmp_path)

    assert done.rows == {FILE: '{"date": null}\n' + golden("week_of_events")}
    assert done.status == 0


@pytest.mark.parametrize(
    ("name", "problem"),
    [
        (
            "no_refetch_days_rewrites_the_file_and_writes_no_day",
            "[0].refetch_days: must be a whole number of days, 1 to 90",
        ),
        (
            "refuses_a_dataset_that_is_not_an_identifier",
            "[0].dataset: must be a name of lowercase letters, digits and _",
        ),
    ],
)
def test_a_config_it_refuses_runs_nothing(tmp_path: Path, name: str, problem: str) -> None:
    scenario = port.scenario(NAME, name)

    with pytest.raises(ConfigError, match=re.escape(f"sources.{NAME}{problem}")):
        port.run(cloudflare_analytics_engine, ae.leadline_config(scenario), scenario, tmp_path)

    assert scenarios.row_files(tmp_path) == dict(scenario.seed)


# --- the layouts ---

NEW_WINDOW = ae.since("2026-09-24 00:00:00")  # three days before the newer layouts' run
QUIET_NEW_LAYOUT = [
    ae.total(ae.DATASET, NEW_WINDOW, "index1", "event", []),
    ae.count(ae.DATASET, f"{NEW_WINDOW} AND index1 = 'active.day' AND blob5 != ''", []),
    ae.total(ae.DATASET, NEW_WINDOW, "blob1", "country", []),
    ae.total(ae.DATASET, f"{NEW_WINDOW} AND blob1 = 'US'", "blob2", "region", []),
    ae.total(ae.DATASET, NEW_WINDOW, "blob3", "browser", []),
    ae.total(ae.DATASET, NEW_WINDOW, "blob4", "app_version", []),
    ae.count(ae.DATASET, f"{NEW_WINDOW} AND blob1 != 'US' AND blob2 != ''", []),
]


def test_a_layout_that_ended_before_the_window_is_not_read_and_a_quiet_day_is_a_row_of_zeros(tmp_path: Path) -> None:
    old = {**ae.OLD_LAYOUT, "until": "2026-09-20T00:00:00Z"}
    scenario = replace(port.scenario(NAME, SWITCH_SCENARIO), responses=QUIET_NEW_LAYOUT, seed={}, expected={})

    port.run(cloudflare_analytics_engine, [usage(layouts=[old, ae.USAGE_LAYOUT])], scenario, tmp_path)

    zeros = {"events": {}, "active_day_shared": 0, "country": {}, "region": {}, "browser": {}, "app_version": {}}
    assert scenarios.row_files(tmp_path) == {
        FILE: "".join(ae.row(ae.day(n), ae.DATASET, **zeros) + "\n" for n in (24, 25, 26))
    }


def test_a_failed_query_of_a_newer_layout_writes_no_day(tmp_path: Path) -> None:
    """Every query is required, as both of the script's are: a failure is one row, on yesterday."""
    country = ae.total(ae.DATASET, ae.NEW_SIDE, "blob1", "country", [])
    failing = [exchange(ae.SQL_URL, "busy", method="POST", sent=country["request"]["body"], status=503)] * 3
    responses = [r for r in ae.USAGE_SWITCH_RESPONSES if r["request"]["body"] != country["request"]["body"]]
    scenario = replace(port.scenario(NAME, SWITCH_SCENARIO), responses=responses[:4] + failing)

    done = port.run(cloudflare_analytics_engine, ae.NEW_LAYOUT[SWITCH_SCENARIO], scenario, tmp_path)

    failure = {
        "date": "2026-09-26",
        "dataset": ae.DATASET,
        "status": "fetch_failed",
        "http_code": "503",
        "reason": "busy",
    }
    assert done.rows == {FILE: ae.KEPT + json.dumps({**failure, "captured_at": ae.NEW_STAMP}) + "\n"}
    assert done.status == 0


# --- privacy: a private column is never read, no query totals two dimensions, the layout is checked ---


class Recorded(SavedResponses):
    """Saved responses, recording the body of every query sent."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.sent: list[str] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> Response:
        assert isinstance(request.data, bytes)
        self.sent.append(request.data.decode("utf-8"))
        return super().__call__(request, timeout)


def queries(cfg: Any, scenario: Scenario, tmp_path: Path) -> list[str]:
    transport = Recorded(scenario.responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenario.now, scenario.tz), [])
    with port.credentials(CREDENTIALS, cloudflare_analytics_engine.ENV):
        cloudflare_analytics_engine.collect(ctx, cfg)
    assert transport.unmatched == transport.never_served() == []
    return transport.sent


SELECT = re.compile(
    r"^SELECT (?P<select>.*) FROM [a-z0-9_]+ WHERE timestamp >= toDateTime\('(?P<start>[^']*)'\)(?P<where>.*?)"
    r"(?: GROUP BY (?P<group>.*))? FORMAT JSON$"
)


def private_columns(cfg: Any, start: str) -> set[str]:
    """The private columns of the layout a query starting at `start` reads: each layout has its own."""
    for layout in cfg[0]["layouts"]:
        until = layout.get("until", "9999").replace("T", " ").rstrip("Z")
        if start < until:
            return set(layout.get("private", []))
    raise AssertionError(f"no layout holds {start}")


@pytest.mark.parametrize("name", [SWITCH_SCENARIO, "totals_each_dimension_on_its_own", "week_of_events"])
def test_no_query_reads_a_private_column_or_totals_two_dimensions(tmp_path: Path, name: str) -> None:
    scenario = port.scenario(NAME, name)
    cfg = ae.leadline_config(scenario)

    sent = queries(cfg, scenario, tmp_path)

    assert sent
    for sql in sent:
        parts = SELECT.match(sql)
        assert parts, sql
        selected, grouped = parts["select"].split(", "), (parts["group"] or "day").split(", ")
        assert selected[0] == "toStartOfDay(timestamp) AS day", sql
        assert selected[-1].endswith(" AS n"), sql
        assert len(selected) <= 3, sql  # the day, at most one dimension, the total
        assert grouped[0] == "day", sql
        assert len(grouped) <= 2, sql  # the day, at most one dimension
        for column in private_columns(cfg, parts["start"]):
            assert column not in parts["select"], sql
            assert column not in (parts["group"] or ""), sql
            assert column not in re.sub(rf"\b{column} != ''", "", parts["where"]), sql  # only ever tested for ''


PERSONAL = "someone@example.com"  # what must never be written: a value outside the layout


def test_a_value_outside_the_layout_reaches_no_file_log_or_finding(tmp_path: Path) -> None:
    responses = [copy.deepcopy(r) for r in ae.USAGE_SWITCH_RESPONSES]
    browser = next(r for r in responses if " AS browser," in r["request"]["body"])
    answer = json.loads(browser["response"]["body"])
    answer["data"] += [
        {"day": "2026-09-26 00:00:00", "browser": PERSONAL, "n": "2"},
        {"day": "2026-09-25 00:00:00", "browser": "Chrome", "n": "1"},
    ]
    browser["response"]["body"] = json.dumps(answer)
    region_check = responses[-1]
    region_check["response"]["body"] = json.dumps(ae.active([(ae.day(26), 4)]))
    old_events = responses[0]
    old_answer = json.loads(old_events["response"]["body"])
    old_answer["data"].append({"day": "2026-09-24 00:00:00", "event": PERSONAL, "n": "1"})
    old_events["response"]["body"] = json.dumps(old_answer)
    scenario = replace(port.scenario(NAME, SWITCH_SCENARIO), responses=responses)

    done = port.run(cloudflare_analytics_engine, ae.NEW_LAYOUT[SWITCH_SCENARIO], scenario, tmp_path)

    assert done.rows == {FILE: ae.USAGE_SWITCH_ROWS}  # the values outside the layout left out, nothing else
    assert done.status == 1
    assert done.ctx.critical == [
        Critical(NAME, FILE, f"{ae.DATASET}: 1 event value(s) outside the layout, left out of the rows"),
        Critical(NAME, FILE, f"{ae.DATASET}: 2 browser value(s) outside the layout, left out of the rows"),
        Critical(
            NAME, FILE, f"{ae.DATASET}: 4 event(s) set region where country is not US, outside the layout; never read"
        ),
    ]
    assert not any(PERSONAL in line or "Chrome" in line for line in done.log)
    with port.credentials(CREDENTIALS, cloudflare_analytics_engine.ENV):
        transport = SavedResponses([check_query(ae.DATASET, 3, result("5"))])
        ctx = port.context(tmp_path, transport, port.local_time(ae.NEW_NOW, "UTC"), [])
        findings = cloudflare_analytics_engine.check(ctx, ae.NEW_LAYOUT[SWITCH_SCENARIO])
    assert not any(PERSONAL in f.message for f in findings)


# --- check ---


def result(n: Any) -> Any:
    """check()'s answer: one row, the total."""
    return analytics_engine_result({"n": "UInt64"}, [(n,)])


def check_query(dataset: str, days: int, answer: Any, status: int = 200) -> Any:
    """check()'s one query per entry, the events over the entry's window, as the newer layouts' run reads it."""
    first = f"2026-09-{27 - days} 00:00:00"
    sql = f"SELECT SUM(_sample_interval) AS n FROM {dataset} WHERE {ae.since(first)} FORMAT JSON"  # noqa: S608
    return exchange(ae.SQL_URL, answer, method="POST", sent=sql, status=status)


def check(tmp_path: Path, cfg: Any, *responses: Any) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(ae.NEW_NOW, "UTC"), [])
    with port.credentials(CREDENTIALS, cloudflare_analytics_engine.ENV):
        findings = cloudflare_analytics_engine.check(ctx, cfg)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


def test_check_finds_events_in_each_dataset(tmp_path: Path) -> None:
    cfg = [*ae.NEW_LAYOUT["totals_a_measure_by_a_dimension"], *ae.NEW_LAYOUT["totals_each_dimension_on_its_own"]]

    findings = check(tmp_path, cfg, check_query(ae.SCREENS, 2, result(12)), check_query(ae.DIAGNOSTICS, 2, result("3")))

    assert findings == [
        Finding("ok", f"{ae.SCREENS}: the dataset has events in the last 2 days"),
        Finding("ok", f"{ae.DIAGNOSTICS}: the dataset has events in the last 2 days"),
    ]


@pytest.mark.parametrize(
    "answer",
    [result("0"), result(0), analytics_engine_result({"n": "UInt64"}, [])],
    ids=["zero", "zero unquoted", "no result row"],
)
def test_check_warns_of_a_dataset_with_no_events(tmp_path: Path, answer: Any) -> None:
    findings = check(tmp_path, ae.NEW_LAYOUT["totals_a_measure_by_a_dimension"], check_query(ae.SCREENS, 2, answer))

    assert findings == [Finding("warn", f"{ae.SCREENS}: no events in the last 2 days; is the dataset's name right?")]


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (
            check_query(ae.SCREENS, 2, "slow down", status=429),
            Finding("warn", f"{ae.SCREENS}: could not query the dataset: HTTP 429"),
        ),
        (
            check_query(ae.SCREENS, 2, "denied", status=403),
            Finding("error", f"{ae.SCREENS}: could not query the dataset: HTTP 403"),
        ),
        (
            check_query(ae.SCREENS, 2, "<html>", status=200),
            Finding("warn", f"{ae.SCREENS}: could not read the answer: response was not JSON"),
        ),
        (
            check_query(ae.SCREENS, 2, "[]", status=200),
            Finding("warn", f"{ae.SCREENS}: could not read the answer: response had no data array"),
        ),
        (
            check_query(ae.SCREENS, 2, '{"data": [{"n": "many"}]}', status=200),
            Finding("warn", f"{ae.SCREENS}: could not read the answer: response had an unexpected shape"),
        ),
    ],
    ids=["rate limited", "credentials rejected", "not JSON", "not an object", "a count not a number"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(tmp_path: Path, response: Any, finding: Finding) -> None:
    assert check(tmp_path, ae.NEW_LAYOUT["totals_a_measure_by_a_dimension"], response) == [finding]


def test_check_reports_a_failed_connection_as_a_finding(tmp_path: Path) -> None:
    sent = check_query(ae.SCREENS, 2, "")["request"]["body"]

    findings = check(
        tmp_path, ae.NEW_LAYOUT["totals_a_measure_by_a_dimension"], failed(ae.SQL_URL, method="POST", sent=sent)
    )

    assert findings == [Finding("warn", f"{ae.SCREENS}: could not query the dataset: HTTP 0")]


# --- the summary ---


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(
        cloudflare_analytics_engine,
        ae.leadline_config(port.scenario(NAME, "week_of_events")),
        "week_of_events",
        tmp_path,
    ) == [
        "",
        "### cloudflare_analytics_engine: example_usage",
        "",
        "- active_day_shared: 3 in 7d",
    ]
