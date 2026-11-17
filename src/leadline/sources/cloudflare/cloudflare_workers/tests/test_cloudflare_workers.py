"""cloudflare_workers: the reviewed rows on its scenarios, and the behavior the scenarios can't show.

The source has no script behind it, so each scenario carries the rows it must leave (scenarios.py, `expected`), and
they are compared with the golden through tests/port.py.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import port
import pytest
import scenarios
from scenarios.builders.common import exchange

from leadline.context import Critical, Finding
from leadline.http import SavedResponses
from leadline.sources.cloudflare import cloudflare_workers
from leadline.sources.cloudflare.cloudflare_workers import spec
from leadline.sources.cloudflare.cloudflare_workers.tests import scenarios as cw
from leadline.sources.cloudflare.tests.builders import (
    CLOUDFLARE_GRAPHQL,
    workers_day,
    workers_metrics,
    workers_outcome,
)

NAME = "cloudflare_workers"
CONFIG = cw.LEADLINE_CONFIG
FILE = cw.FILE


def read(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (tmp_path / FILE).read_text(encoding="utf-8").splitlines()]


def run(tmp_path: Path, *responses: Any, seed: dict[str, str] | None = None) -> port.PortRun:
    base = port.scenario(NAME, "writes_a_row_for_each_day_of_the_window")
    return port.run(cloudflare_workers, CONFIG, replace(base, responses=responses, seed=seed or {}), tmp_path)


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert cloudflare_workers.NAME == NAME
    assert cloudflare_workers.ENV == ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID")
    assert cloudflare_workers.LAGGING is True


def test_a_valid_config_has_no_problems() -> None:
    cfg = [{"script": "a"}, {"script": "b", "file": "x-{script}.jsonl"}]

    assert cloudflare_workers.validate(cfg) == []


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"script": "a"}, [": must be a list of entries"]),
        (["a"], ["[0]: must be an object"]),
        ([{}], ["[0].script: must be a non-empty string"]),
        ([{"script": ""}], ["[0].script: must be a non-empty string"]),
        ([{"script": 5}], ["[0].script: must be a non-empty string"]),
        ([{"script": "a", "bogus": 1}], ["[0].bogus: unknown key (the keys are file, script)"]),
    ],
    ids=["not a list", "an entry not an object", "no script", "an empty script", "a number", "an unknown key"],
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert cloudflare_workers.validate(cfg) == problems


def test_files_are_the_default_or_the_template_of_each_entry() -> None:
    cfg = [{"script": "one"}, {"script": "two", "file": "t-{script}.jsonl"}]

    assert cloudflare_workers.files(cfg) == {"[0]": "cloudflare-workers-one.jsonl", "[1]": "t-two.jsonl"}


def test_it_is_skipped_without_its_account_id(tmp_path: Path) -> None:
    done = port.run(cloudflare_workers, CONFIG, port.scenario(NAME, "skips_without_the_account_id"), tmp_path)

    assert (done.status, done.rows) == (0, {})
    assert "SKIP: cloudflare_workers (CLOUDFLARE_ACCOUNT_ID not set)" in done.log


# --- the query ---


def test_the_query_reads_the_scripts_days_in_both_parts() -> None:
    query = spec.query("acct", "my-worker", cw.WINDOW_START, cw.YESTERDAY)

    assert 'accounts(filter: {accountTag: "acct"})' in query
    assert query.count('filter: {scriptName: "my-worker", date_geq: "2026-08-26", date_leq: "2026-09-22"}') == 2
    assert "daily: workersInvocationsAdaptive(" in query
    assert "outcomes: workersInvocationsAdaptive(" in query
    assert "quantiles { cpuTimeP50 cpuTimeP99 }" in query
    assert "dimensions { date status }" in query


def test_a_quote_in_a_script_name_cannot_end_the_querys_string() -> None:
    query = spec.query("acct", 'a"b', cw.WINDOW_START, cw.YESTERDAY)

    assert 'scriptName: "a\\"b"' in query


# --- the reviewed rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_reviewed_rows(scenario: Any, tmp_path: Path) -> None:
    done = port.run(cloudflare_workers, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert done.status == 0


def test_the_script_has_no_collector_so_there_are_no_divergences() -> None:
    assert port.divergences(NAME) == {}


def test_a_run_writes_the_28_days_ending_yesterday(tmp_path: Path) -> None:
    port.run(cloudflare_workers, CONFIG, port.scenario(NAME, "a_quiet_worker_writes_zeros"), tmp_path)

    dates = [r["date"] for r in read(tmp_path)]
    assert (len(dates), dates[0], dates[-1]) == (28, "2026-08-26", "2026-09-22")


# --- the window ---


def test_a_changed_day_is_a_correction_and_a_filled_failure_is_not(tmp_path: Path) -> None:
    done = port.run(
        cloudflare_workers, CONFIG, port.scenario(NAME, "corrects_a_changed_day_and_fills_a_failed_one"), tmp_path
    )

    assert [(c.date, c.old, c.new) for c in done.ctx.corrections] == [
        ("2026-09-20", {"requests": 110}, {"requests": 120})
    ]


def test_a_day_outside_the_window_in_the_answer_is_left_out(tmp_path: Path) -> None:
    outside = cw.WINDOW_START - timedelta(days=1)
    run(tmp_path, cw.answer(workers_metrics([workers_day(outside, 7)], [workers_outcome(outside, "success", 7)])))

    rows = read(tmp_path)
    assert rows[0]["date"] == "2026-08-26"
    assert sum(r["requests"] for r in rows) == 0


def test_an_outcome_given_twice_for_a_day_is_added_up(tmp_path: Path) -> None:
    given = [workers_outcome(cw.day(0), "success", 4), workers_outcome(cw.day(0), "success", 5)]
    run(tmp_path, cw.answer(workers_metrics([workers_day(cw.day(0), 9)], given)))

    assert read(tmp_path)[-1]["outcomes"] == {"success": 9}


def test_a_status_cloudflare_adds_is_kept_by_its_name(tmp_path: Path) -> None:
    given = [workers_outcome(cw.day(0), "somethingNew", 3)]
    run(tmp_path, cw.answer(workers_metrics([workers_day(cw.day(0), 3)], given)))

    assert read(tmp_path)[-1]["outcomes"] == {"somethingNew": 3}


# --- failures ---


def test_a_failed_request_waits_10_then_20_seconds(tmp_path: Path) -> None:
    done = port.run(
        cloudflare_workers, CONFIG, port.scenario(NAME, "three_failed_attempts_write_a_failure_row"), tmp_path
    )

    assert done.sleeps == [10, 20]


@pytest.mark.parametrize("code", [401, 403])
def test_a_rejected_token_is_critical_and_not_retried(tmp_path: Path, code: int) -> None:
    done = run(tmp_path, cw.answer("", status=code))

    assert (read(tmp_path)[0]["status"], read(tmp_path)[0]["http_code"]) == ("fetch_failed", str(code))
    assert done.sleeps == []
    assert done.ctx.critical == [
        Critical(NAME, FILE, f"example-worker: HTTP {code} from Cloudflare's GraphQL API; empty body")
    ]
    assert done.status == 1


NOT_A_GROUP: Any = "group"
DAY = {"dimensions": {"date": cw.YESTERDAY.isoformat()}}
SUMS = {"sum": {"requests": 1, "errors": 0, "subrequests": 0}, "quantiles": {"cpuTimeP50": 1, "cpuTimeP99": 1}}


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"data": {"viewer": {"accounts": "none"}}},
        {"data": {"viewer": {"accounts": ["account"]}}},
        {"data": None},
        {"errors": "broken"},
        workers_metrics([NOT_A_GROUP]),
        workers_metrics([{**SUMS}]),
        workers_metrics([{**SUMS, "dimensions": {"date": "yesterday"}}]),
        workers_metrics([{**DAY, **SUMS, "sum": {"requests": True, "errors": 0, "subrequests": 0}}]),
        workers_metrics([{**DAY, **SUMS, "sum": {"requests": "1", "errors": 0, "subrequests": 0}}]),
        workers_metrics([{**DAY, **SUMS, "sum": {"requests": 1, "errors": None, "subrequests": 0}}]),
        workers_metrics([{**DAY, **SUMS, "sum": {"requests": 1, "errors": 0}}]),
        workers_metrics([{**DAY, **SUMS, "quantiles": {"cpuTimeP50": -1, "cpuTimeP99": 1}}]),
        workers_metrics([{**DAY, **SUMS, "quantiles": None}]),
        workers_metrics([], [{**DAY, "sum": {"requests": 1}}]),
        workers_metrics([], [{"dimensions": {"date": cw.YESTERDAY.isoformat(), "status": 5}, "sum": {"requests": 1}}]),
        workers_metrics([], [workers_outcome(cw.YESTERDAY, "success", -2)]),
    ],
    ids=[
        "not an object",
        "accounts not a list",
        "account not an object",
        "data null",
        "errors not a list",
        "group not an object",
        "no date",
        "bad date",
        "boolean count",
        "text count",
        "null count",
        "missing count",
        "negative cpu",
        "no quantiles",
        "outcome without status",
        "status a number",
        "negative outcome",
    ],
)
def test_an_answer_of_another_shape_is_parse_failed_for_yesterday(tmp_path: Path, body: Any) -> None:
    done = run(tmp_path, cw.answer(body))

    assert (tmp_path / FILE).read_text(encoding="utf-8") == cw.failure_row(
        "parse_failed", "response had an unexpected shape"
    )
    assert (done.status, done.ctx.critical) == (0, [])


def test_graphql_errors_give_the_first_three_messages(tmp_path: Path) -> None:
    errors = [{"message": f"problem {n}"} for n in range(4)]
    run(tmp_path, cw.answer({"data": None, "errors": errors}))

    assert read(tmp_path)[0]["reason"] == "problem 0; problem 1; problem 2"


# --- check ---


def check(tmp_path: Path, *responses: Any) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, cloudflare_workers.ENV):
        findings = cloudflare_workers.check(ctx, CONFIG)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


def probe(body: Any, status: int = 200) -> Any:
    start = cw.YESTERDAY - timedelta(days=spec.CHECK_DAYS - 1)
    query = spec.query(cw.ACCOUNT, cw.SCRIPT, start, cw.YESTERDAY)
    return exchange(CLOUDFLARE_GRAPHQL, body, method="POST", sent=cw.sent(query), status=status)


def test_check_finds_a_worker_with_invocations(tmp_path: Path) -> None:
    found = check(tmp_path, probe(workers_metrics([workers_day(cw.day(3), 5)])))

    assert found == [Finding("ok", "example-worker: 5 invocations in 7 days")]


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (
            probe(workers_metrics()),
            Finding("warn", "example-worker: no invocations in 7 days; is it the Worker's script name?"),
        ),
        (
            probe({"data": None, "errors": [{"message": "not authorized for this account"}]}),
            Finding(
                "error",
                "example-worker: the token cannot read the account's Workers metrics: not authorized for this account",
            ),
        ),
        (
            probe({"data": {"viewer": {"accounts": []}}, "errors": None}),
            Finding(
                "error",
                "example-worker: the token cannot read the account's Workers metrics: account filter matched nothing",
            ),
        ),
        (probe("", status=403), Finding("error", "example-worker: could not query the Workers metrics: HTTP 403")),
        (probe("", status=502), Finding("warn", "example-worker: could not query the Workers metrics: HTTP 502")),
        (probe("not json"), Finding("warn", "example-worker: could not read the Workers metrics answer")),
        (
            probe(workers_metrics([NOT_A_GROUP])),
            Finding("warn", "example-worker: could not read the Workers metrics answer"),
        ),
    ],
    ids=["no invocations", "not authorized", "no such account", "rejected", "unavailable", "not json", "unreadable"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(tmp_path: Path, response: Any, finding: Finding) -> None:
    assert check(tmp_path, response) == [finding]


# --- the summary ---


def test_its_summary_shows_the_days_requests_and_errors(tmp_path: Path) -> None:
    lines = port.summary(cloudflare_workers, CONFIG, "writes_a_row_for_each_day_of_the_window", tmp_path)

    assert lines[:2] == ["", "### cloudflare_workers: example-worker"]
    assert any(line.startswith("- Requests") for line in lines)
    assert any(line.startswith("- Errors") for line in lines)


def test_trends_draws_the_outcomes_and_the_cpu_time_as_half_width_charts() -> None:
    (section,) = cloudflare_workers.summary(CONFIG)

    assert (section.name, section.label, section.file) == ("example-worker", "Workers", FILE)
    assert [(s.field, s.kind, s.half, s.lines) for s in section.charts] == [
        ("outcomes", "breakdown", True, False),
        ("cpu_us", "breakdown", True, True),
    ]
