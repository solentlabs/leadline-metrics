"""cloudflare_zone_traffic: the reviewed rows on its scenarios, and the behavior the scenarios can't show.

The source has no script behind it, so each scenario carries the rows it must leave (scenarios.py, `expected`), and
they are compared with the golden through tests/port.py.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import port
import pytest
import scenarios
from scenarios.builders.common import exchange

from leadline.context import Critical, Finding
from leadline.http import SavedResponses
from leadline.sources.cloudflare import cloudflare_zone_traffic
from leadline.sources.cloudflare.cloudflare_zone_traffic import spec
from leadline.sources.cloudflare.cloudflare_zone_traffic.tests import scenarios as cz
from leadline.sources.cloudflare.tests.builders import CLOUDFLARE_GRAPHQL, zone_daily, zone_request_group

NAME = "cloudflare_zone_traffic"
CONFIG = cz.LEADLINE_CONFIG
FILE = cz.FILE


def read(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (tmp_path / FILE).read_text(encoding="utf-8").splitlines()]


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert cloudflare_zone_traffic.NAME == NAME
    assert cloudflare_zone_traffic.ENV == ("CLOUDFLARE_API_TOKEN",)
    assert cloudflare_zone_traffic.LAGGING is True


def test_a_valid_config_has_no_problems() -> None:
    cfg = [{"label": "a", "zone": "z1"}, {"label": "b", "zone": "z2", "top": 10, "file": "x-{label}.jsonl"}]

    assert cloudflare_zone_traffic.validate(cfg) == []


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"label": "a"}, [": must be a list of entries"]),
        (["a"], ["[0]: must be an object"]),
        ([{"zone": "z"}], ["[0].label: must be a non-empty string"]),
        ([{"label": "a"}], ["[0].zone: must be a non-empty string"]),
        ([{"label": "a", "zone": ""}], ["[0].zone: must be a non-empty string"]),
        ([{"label": "a", "zone": "z", "top": 0}], ["[0].top: must be a whole number from 1 to 1000"]),
        ([{"label": "a", "zone": "z", "top": 1001}], ["[0].top: must be a whole number from 1 to 1000"]),
        ([{"label": "a", "zone": "z", "top": True}], ["[0].top: must be a whole number from 1 to 1000"]),
        ([{"label": "a", "zone": "z", "bogus": 1}], ["[0].bogus: unknown key (the keys are file, label, top, zone)"]),
        ([{}], ["[0].label: must be a non-empty string", "[0].zone: must be a non-empty string"]),
    ],
    ids=[
        "not a list",
        "an entry not an object",
        "no label",
        "no zone",
        "an empty zone",
        "top zero",
        "top too large",
        "top a boolean",
        "an unknown key",
        "neither",
    ],
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert cloudflare_zone_traffic.validate(cfg) == problems


def test_files_are_the_default_or_the_template_of_each_entry() -> None:
    cfg = [{"label": "one", "zone": "z"}, {"label": "two", "zone": "z", "file": "t-{label}.jsonl"}]

    assert cloudflare_zone_traffic.files(cfg) == {"[0]": "cloudflare-zone-traffic-one.jsonl", "[1]": "t-two.jsonl"}


def test_it_is_skipped_without_its_token(tmp_path: Path) -> None:
    done = port.run(cloudflare_zone_traffic, CONFIG, port.scenario(NAME, "skips_without_credentials"), tmp_path)

    assert (done.status, done.rows) == (0, {})
    assert "SKIP: cloudflare_zone_traffic (CLOUDFLARE_API_TOKEN not set)" in done.log


# --- no visitor is identified ---


def test_no_query_asks_for_an_address() -> None:
    day = date(2026, 9, 22)
    queries = [
        spec.daily_query("z", day, day),
        spec.totals_query("z", day, day),
        spec.probe_query("z", day),
        *(spec.list_query("z", dimension, day, day) for _, dimension in spec.LISTS),
    ]

    assert [q for q in queries if "clientIP" in q or "originIP" in q] == []
    assert [dimension for _, dimension in spec.LISTS if dimension.endswith("IP")] == []


# --- the reviewed rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_reviewed_rows(scenario: Any, tmp_path: Path) -> None:
    done = port.run(cloudflare_zone_traffic, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert done.status == 0


def test_the_script_has_no_collector_so_there_are_no_divergences() -> None:
    assert port.divergences(NAME) == {}


def test_a_first_run_writes_one_row_a_day_and_marks_the_filled_days() -> None:
    first = read_expected("first_run_fills_the_history_and_yesterday")

    assert [(r["date"], r.get("backfilled")) for r in first] == [
        ("2026-09-20", True),
        ("2026-09-21", True),
        ("2026-09-22", None),
    ]


def read_expected(name: str) -> list[dict[str, Any]]:
    text = scenarios.load_golden(NAME)[name]["rows"][FILE]
    return [json.loads(line) for line in text.splitlines()]


# --- healing ---


def test_a_changed_daily_total_is_a_correction_and_the_day_keeps_its_other_fields(tmp_path: Path) -> None:
    done = port.run(
        cloudflare_zone_traffic,
        CONFIG,
        port.scenario(NAME, "a_nightly_run_reads_30_days_and_corrects_a_changed_total"),
        tmp_path,
    )

    assert [(c.date, sorted(c.old), c.old["requests"], c.new["requests"]) for c in done.ctx.corrections] == [
        ("2026-09-20", ["requests"], 99, 100)
    ]


def test_a_list_already_in_a_row_is_not_rewritten_by_a_later_sample(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "a_nightly_run_reads_30_days_and_corrects_a_changed_total")
    other = [zone_request_group(cz.day(2), 999, clientRequestPath="/")]  # Cloudflare's sample moved
    answers = cz.per_request(other, cz.totals_groups())
    scenario = replace(scenario, responses=[scenario.responses[0], *answers])

    port.run(cloudflare_zone_traffic, CONFIG, scenario, tmp_path)

    assert next(r for r in read(tmp_path) if r["date"] == "2026-09-20")["paths"] == {"/": 50, "/privacy": 10}


def test_the_top_setting_cuts_each_list_and_a_tie_goes_to_the_name(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "first_run_fills_the_history_and_yesterday")

    port.run(cloudflare_zone_traffic, [{**CONFIG[0], "top": 1}], scenario, tmp_path)

    assert [r["paths"] for r in read(tmp_path)] == [{"/": 50}, {"/": 60}, {"/": 80}]


def test_a_part_that_failed_last_night_is_filled_by_the_next_run(tmp_path: Path) -> None:
    first = port.scenario(NAME, "a_list_that_fails_leaves_yesterday_partial")
    port.run(cloudflare_zone_traffic, CONFIG, first, tmp_path / "one")
    partial = (tmp_path / "one" / FILE).read_text(encoding="utf-8")
    assert json.loads(partial.splitlines()[-1])["status"] == "partial"
    healed = [cz.daily_answer(zone_daily(cz.zone_days())), *cz.per_request(cz.path_groups(), cz.totals_groups())]

    done = port.run(
        cloudflare_zone_traffic, CONFIG, replace(first, seed={FILE: partial}, responses=healed), tmp_path / "two"
    )

    last = read(tmp_path / "two")[-1]
    assert (last["status"], last["paths"], "reason" in last) == (
        "ok",
        {"/": 80, "/about": 80, "/config/.env": 5},
        False,
    )
    assert done.ctx.critical == []


def test_a_list_that_fails_keeps_the_value_an_earlier_run_of_the_day_got(tmp_path: Path) -> None:
    base = port.scenario(NAME, "first_run_fills_the_history_and_yesterday")
    paths = cz.per_request(cz.path_groups(), cz.totals_groups(), failing=("clientRequestPath",))
    scenario = replace(
        base,
        seed={FILE: cz.stored(cz.YESTERDAY, **cz.DAY_0)},
        responses=[cz.daily_answer(zone_daily(cz.zone_days())), *paths],
    )

    done = port.run(cloudflare_zone_traffic, CONFIG, scenario, tmp_path)

    last = read(tmp_path)[-1]
    assert (last["status"], last["paths"], "reason" in last) == ("ok", cz.DAY_0["paths"], False)
    assert done.ctx.critical == []


def test_the_year_is_read_once_and_then_the_30_days(tmp_path: Path) -> None:
    first = port.scenario(NAME, "first_run_fills_the_history_and_yesterday")
    done = port.run(cloudflare_zone_traffic, CONFIG, first, tmp_path)
    assert done.status == 0

    # The file now holds days older than the 30; a second night asks only for the 30.
    old = {"date": "2026-01-01", "zone": "example", "status": "ok", "requests": 1}
    text = (tmp_path / FILE).read_text(encoding="utf-8")
    nightly = port.scenario(NAME, "a_nightly_run_reads_30_days_and_corrects_a_changed_total")
    seeded = replace(nightly, seed={FILE: json.dumps(old) + "\n" + text})

    port.run(cloudflare_zone_traffic, CONFIG, seeded, tmp_path / "next")


def test_the_first_day_asked_for_is_inside_the_daily_dataset_for_a_run_west_of_utc() -> None:
    # Cloudflare refuses a start older than notOlderThan (31539600 s) before the query is made; a run at
    # 23:30 in UTC-4 is already the next UTC day, so the start must leave that day and the hours of slack.
    run = datetime(2026, 10, 20, 23, 30, tzinfo=timezone(timedelta(hours=-4)))
    start = datetime.combine(run.date() - timedelta(days=1 + spec.HISTORY_DAYS - 1), datetime.min.time(), timezone.utc)

    assert run - start <= timedelta(seconds=31539600)


# --- failures ---


def test_a_failed_request_waits_10_then_20_seconds(tmp_path: Path) -> None:
    done = port.run(
        cloudflare_zone_traffic, CONFIG, port.scenario(NAME, "three_failed_attempts_write_a_failure_row"), tmp_path
    )

    assert done.sleeps == [10, 20]


@pytest.mark.parametrize("code", [401, 403])
def test_a_rejected_token_is_critical_and_not_retried(tmp_path: Path, code: int) -> None:
    rejected = cz.daily_answer("", status=code)
    scenario = replace(port.scenario(NAME, "three_failed_attempts_write_a_failure_row"), responses=[rejected])

    done = port.run(cloudflare_zone_traffic, CONFIG, scenario, tmp_path)

    assert read(tmp_path)[0]["http_code"] == str(code)
    assert done.sleeps == []
    assert done.ctx.critical == [Critical(NAME, FILE, f"example: HTTP {code} from Cloudflare's GraphQL API")]
    assert done.status == 1


def test_a_list_the_token_may_not_read_is_critical_once_and_leaves_the_row_partial(tmp_path: Path) -> None:
    refused = [
        exchange(
            CLOUDFLARE_GRAPHQL,
            "",
            method="POST",
            sent=cz.sent(spec.list_query(cz.ZONE, dimension, cz.WINDOW_START, cz.YESTERDAY)),
            status=403,
        )
        for dimension in ("clientRequestPath", "userAgent")
    ]
    base = port.scenario(NAME, "a_quiet_zone_writes_zeros_for_yesterday")
    answers = [r for r in base.responses if not any(r["request"]["body"] == q["request"]["body"] for q in refused)]
    scenario = replace(base, responses=[*answers, *refused])

    done = port.run(cloudflare_zone_traffic, CONFIG, scenario, tmp_path)

    assert done.ctx.critical == [Critical(NAME, FILE, "example: HTTP 403 from Cloudflare's GraphQL API")]
    last = read(tmp_path)[-1]
    assert (last["status"], last["paths"], last["user_agents"]) == ("partial", None, None)
    assert last["reason"] == "paths request failed (HTTP 403); user_agents request failed (HTTP 403)"


@pytest.mark.parametrize(
    ("dimension", "field", "reason"),
    [
        ("clientRequestPath", "paths", "paths answer was unreadable"),
        ("userAgent", "user_agents", "user_agents answer was unreadable"),
    ],
)
def test_a_list_answer_that_cannot_be_read_is_a_part_failure_not_a_crash(
    tmp_path: Path, dimension: str, field: str, reason: str
) -> None:
    base = port.scenario(NAME, "a_quiet_zone_writes_zeros_for_yesterday")
    query = spec.list_query(cz.ZONE, dimension, cz.WINDOW_START, cz.YESTERDAY)
    broken = exchange(
        CLOUDFLARE_GRAPHQL,
        {"data": {"viewer": {"zones": [{"groups": ["not a group"]}]}}, "errors": None},
        method="POST",
        sent=cz.sent(query),
    )
    kept = [r for r in base.responses if r["request"]["body"] != cz.sent(query)]

    done = port.run(cloudflare_zone_traffic, CONFIG, replace(base, responses=[*kept, broken]), tmp_path)

    last = read(tmp_path)[-1]
    assert (done.status, last["status"], last[field], last["reason"]) == (0, "partial", None, reason)


def test_unreadable_totals_leave_visits_and_the_timings_null(tmp_path: Path) -> None:
    base = port.scenario(NAME, "a_quiet_zone_writes_zeros_for_yesterday")
    query = spec.totals_query(cz.ZONE, cz.WINDOW_START, cz.YESTERDAY)
    broken = exchange(
        CLOUDFLARE_GRAPHQL,
        {"data": {"viewer": {"zones": [{"groups": [5]}]}}, "errors": None},
        method="POST",
        sent=cz.sent(query),
    )
    kept = [r for r in base.responses if r["request"]["body"] != cz.sent(query)]

    port.run(cloudflare_zone_traffic, CONFIG, replace(base, responses=[*kept, broken]), tmp_path)

    last = read(tmp_path)[-1]
    assert (last["status"], last["visits"], last["origin_response_ms"]) == ("partial", None, None)
    assert last["reason"] == "totals answer was unreadable"


# --- check ---


def check(tmp_path: Path, cfg: Any, *responses: Any) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, cloudflare_zone_traffic.ENV):
        findings = cloudflare_zone_traffic.check(ctx, cfg)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


def probe(body: Any, status: int = 200) -> Any:
    query = spec.probe_query(cz.ZONE, cz.YESTERDAY)
    return exchange(CLOUDFLARE_GRAPHQL, body, method="POST", sent=cz.sent(query), status=status)


def test_check_finds_a_zone_the_token_can_read(tmp_path: Path) -> None:
    found = check(tmp_path, CONFIG, probe({"data": {"viewer": {"zones": [{"daily": []}]}}, "errors": None}))

    assert found == [Finding("ok", "example: the token can read the zone's analytics")]


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (
            probe({"data": None, "errors": [{"message": "not authorized to access this zone"}]}),
            Finding("error", "example: the token cannot read the zone's analytics: not authorized to access this zone"),
        ),
        (
            probe({"data": {"viewer": {"zones": []}}, "errors": None}),
            Finding("error", "example: the token cannot read the zone's analytics: zone filter matched nothing"),
        ),
        (probe("", status=403), Finding("error", "example: could not query the zone's analytics: HTTP 403")),
        (probe("", status=502), Finding("warn", "example: could not query the zone's analytics: HTTP 502")),
        (probe("not json"), Finding("warn", "example: could not read the zone's analytics answer")),
    ],
    ids=["not authorized", "no such zone", "rejected", "unavailable", "unreadable"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(tmp_path: Path, response: Any, finding: Finding) -> None:
    assert check(tmp_path, CONFIG, response) == [finding]


# --- the summary ---


def test_its_summary_shows_the_days_counts_and_two_lists(tmp_path: Path) -> None:
    lines = port.summary(cloudflare_zone_traffic, CONFIG, "first_run_fills_the_history_and_yesterday", tmp_path)

    # A day's counts show once 7 rows are there; the two lists show from the first, and no other list does.
    assert lines == ["", "### cloudflare_zone_traffic: example", "", "- Status codes: 200 120 · 404 30"]
