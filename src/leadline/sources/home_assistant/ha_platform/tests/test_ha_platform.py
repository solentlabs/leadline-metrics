"""ha_platform: the script's rows on its scenarios, and leadline's side of each decided divergence.

The rows are compared with the goldens through tests/port.py; the scenarios where leadline differs by
a decision are listed in scenarios.py (DIVERGENCES) and asserted here instead.
"""

from __future__ import annotations

import random
from dataclasses import replace
from pathlib import Path
from typing import Any

import port
import pytest
import scenarios
from scenarios.builders.common import exchange, failed

from leadline import http, runner
from leadline.config import Config, ConfigError
from leadline.context import Critical, Finding
from leadline.sources.home_assistant import ha_platform
from leadline.sources.home_assistant.ha_platform.tests import scenarios as hp
from leadline.sources.home_assistant.tests.builders import HA_PLATFORM_URL
from leadline.sources.home_assistant.tests.builders import ha_platform as ha_platform_builder

NAME = "ha_platform"
CONFIG = hp.LEADLINE_CONFIG
FILE = hp.FILE
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert ha_platform.NAME == NAME
    assert ha_platform.ENV == ()  # a public feed: no credentials
    assert ha_platform.LAGGING is False


@pytest.mark.parametrize("cfg", [{}, {"file": "platform.jsonl"}], ids=["defaults", "a file"])
def test_a_valid_config_has_no_problems(cfg: Any) -> None:
    assert ha_platform.validate(cfg) == []


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ([], [": must be an object"]),
        (None, [": must be an object"]),
        ({"file": "x-{label}.jsonl"}, [".file: 'x-{label}.jsonl' uses {label}, which is not one of: none"]),
        ({"file": "x.txt"}, [".file: 'x.txt' must fill to a plain *.jsonl name, not 'x.txt'"]),
        ({"file": 5}, [".file: must be a string, not 5"]),
    ],
    ids=["a list", "null", "a template field", "not jsonl", "file not a string"],
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert ha_platform.validate(cfg) == problems


@pytest.mark.parametrize(
    ("cfg", "file"), [({}, "ha-platform.jsonl"), ({"file": "platform.jsonl"}, "platform.jsonl")], ids=["default", "set"]
)
def test_files_is_the_one_file_it_writes(cfg: Any, file: str) -> None:
    assert ha_platform.files(cfg) == {"": file}


def test_the_runner_names_the_object_itself_when_its_file_clashes(tmp_path: Path) -> None:
    config = Config(tmp_path, port.USER_AGENT, {"ha_integration": [{"integration": "a", "file": FILE}], NAME: {}})

    with pytest.raises(
        ConfigError, match=f"sources.ha_integration\\[0\\] and sources.ha_platform write the same file: {FILE}"
    ):
        runner.run(config)


def test_it_needs_no_credentials_so_it_is_never_skipped(tmp_path: Path) -> None:
    """ENV is empty: with no credential variables set at all, it still runs."""
    scenario = replace(port.scenario(NAME, "totals_and_the_top_fifteen_countries"), env={})

    done = port.run(ha_platform, CONFIG, scenario, tmp_path)

    assert not any(line.startswith("SKIP:") for line in done.log)
    assert '"date": "2026-09-23", "active_installations": 250000' in done.rows[FILE]


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    done = port.run(ha_platform, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == [
        "a_body_without_current_writes_zeros",
        "a_null_country_count_writes_nothing",
        "failed_fetch_writes_nothing",
        "unparseable_body_writes_nothing",
    ]


# --- decided divergences: ADR "A failure is recorded as a failure" ---


def failure_row(status: str, reason: str, http_code: str | None = None) -> str:
    code = f', "http_code": "{http_code}"' if http_code is not None else ""
    return f'{{"date": "2026-09-23", "status": "{status}"{code}, "reason": "{reason}", "captured_at": "{NOW}"}}\n'


def test_a_failed_fetch_is_a_fetch_failed_row(tmp_path: Path) -> None:
    done = port.run(ha_platform, CONFIG, port.scenario(NAME, "failed_fetch_writes_nothing"), tmp_path)

    assert done.rows == {FILE: hp.EARLIER + failure_row("fetch_failed", "analytics request failed", "503")}
    assert (done.status, done.ctx.critical) == (0, [])  # recoverable: the next run writes the day


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("unparseable_body_writes_nothing", "response was not JSON"),
        ("a_null_country_count_writes_nothing", "response had an unexpected shape"),
        ("a_body_without_current_writes_zeros", "response had an unexpected shape"),
    ],
)
def test_an_answer_it_cant_read_is_a_parse_failed_row(tmp_path: Path, name: str, reason: str) -> None:
    done = port.run(ha_platform, CONFIG, port.scenario(NAME, name), tmp_path)

    assert done.rows == {FILE: hp.EARLIER + failure_row("parse_failed", reason)}
    assert (done.status, done.ctx.critical) == (0, [])


@pytest.mark.parametrize(
    "body",
    [
        {"current": None},
        {"current": [1]},
        {"current": {"active_installations": "many"}},
        {"current": {"active_installations": 100, "reports_integrations": "some"}},
        {"current": {"active_installations": 100, "countries": ["AA"]}},
        {"current": {"active_installations": 100, "countries": {"AA": "3"}}},
        [],
        None,
    ],
    ids=[
        "current null",
        "current a list",
        "installations not a number",
        "reports not a number",
        "countries a list",
        "a country count a string",
        "a list",
        "null",
    ],
)
def test_any_answer_the_scripts_code_stops_on_is_a_parse_failed_row(tmp_path: Path, body: Any) -> None:
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=[exchange(HA_PLATFORM_URL, body)])

    done = port.run(ha_platform, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: hp.EARLIER + failure_row("parse_failed", "response had an unexpected shape")}


def test_a_failure_keeps_a_good_row(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data."""
    good = '{"date": "2026-09-23", "active_installations": 1100, "reports_integrations": 450}\n'
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), seed={FILE: hp.EARLIER + good})

    done = port.run(ha_platform, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: hp.EARLIER + good}
    assert f"WARN: kept the good row for 2026-09-23 in {FILE}; fetch_failed not recorded" in done.log


def test_a_good_row_replaces_a_failure_row(tmp_path: Path) -> None:
    failed = '{"date": "2026-09-23", "status": "parse_failed", "reason": "response was not JSON"}\n'
    name = "totals_and_the_top_fifteen_countries"
    scenario = replace(port.scenario(NAME, name), seed={FILE: hp.EARLIER + failed})

    port.run(ha_platform, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, name, tmp_path) == []


# --- errors waiting can't fix ---


@pytest.mark.parametrize("code", [401, 403, 404])
def test_an_error_waiting_cant_fix_is_critical_at_once(tmp_path: Path, code: int) -> None:
    scenario = replace(
        port.scenario(NAME, "failed_fetch_writes_nothing"), responses=[exchange(HA_PLATFORM_URL, "", status=code)]
    )

    done = port.run(ha_platform, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: hp.EARLIER + failure_row("fetch_failed", "analytics request failed", str(code))}
    assert done.ctx.critical == [Critical(NAME, FILE, f"HTTP {code} from Home Assistant's platform analytics")]
    assert done.status == 1


def test_a_rate_limit_is_recoverable_not_critical(tmp_path: Path) -> None:
    """A 403 that says its limit is spent is one waiting fixes (http: HttpError.rate_limited)."""
    limited = exchange(HA_PLATFORM_URL, "", status=403, headers={"Retry-After": "60"})
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=[limited])

    done = port.run(ha_platform, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: hp.EARLIER + failure_row("fetch_failed", "analytics request failed", "403")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_no_connection_is_recorded_as_000_and_is_recoverable(tmp_path: Path) -> None:
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=[failed(HA_PLATFORM_URL)])

    done = port.run(ha_platform, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: hp.EARLIER + failure_row("fetch_failed", "analytics request failed", "000")}
    assert (done.status, done.sleeps) == (0, [])  # one attempt, as the script makes


# --- check() ---

FEED = "Home Assistant's platform analytics"


def check(tmp_path: Path, *responses: Any) -> list[Finding]:
    transport = http.SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials({}, ha_platform.ENV):
        findings = ha_platform.check(ctx, CONFIG)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


def test_check_finds_every_field_the_source_reads(tmp_path: Path) -> None:
    body = ha_platform_builder(random.Random(4), active=500, reports_integrations=400, countries={"AA": 3})

    assert check(tmp_path, exchange(HA_PLATFORM_URL, body)) == [
        Finding("ok", f"{FEED} answer with every field the source reads")
    ]


def test_check_names_the_fields_the_answer_lacks(tmp_path: Path) -> None:
    """A field it lacks reads as 0 or null every night, so the rows would look quiet, not broken."""
    body = {"current": {"active_installations": 100, "countries": {"AA": 3}, "avg_integrations": 20}}

    assert check(tmp_path, exchange(HA_PLATFORM_URL, body)) == [
        Finding(
            "error",
            f"{FEED} lack reports_integrations, reports_addons, reports_statistics, installation_types, "
            "which every row then reads as 0 or null",
        )
    ]


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (exchange(HA_PLATFORM_URL, "", status=503), Finding("warn", f"could not fetch {FEED}: HTTP 503")),
        (failed(HA_PLATFORM_URL), Finding("warn", f"could not fetch {FEED}: HTTP 0")),
        (exchange(HA_PLATFORM_URL, "", status=404), Finding("error", f"could not fetch {FEED}: HTTP 404")),
        (exchange(HA_PLATFORM_URL, "<html>"), Finding("warn", f"could not read {FEED}: not JSON")),
        (exchange(HA_PLATFORM_URL, {"current": None}), Finding("warn", f"could not read {FEED}: unexpected answer")),
    ],
    ids=["unavailable", "no connection", "gone", "not JSON", "no current"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(tmp_path: Path, response: Any, finding: Finding) -> None:
    assert check(tmp_path, response) == [finding]


# --- the summary ---


@pytest.mark.parametrize("cfg", [{}, {"file": "platform.jsonl"}], ids=["defaults", "a file"])
def test_it_has_no_summary_sections_since_it_reports_on_the_platform_not_the_users_projects(cfg: Any) -> None:
    """Nothing of it shows in the default summary or trends; its rows are collected all the same."""
    assert ha_platform.summary(cfg) == []
