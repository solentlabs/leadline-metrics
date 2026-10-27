"""ha_integration: the script's rows on its scenarios, and leadline's side of each decided divergence.

The rows are compared with the goldens through tests/port.py; the scenarios where leadline differs by
a decision are listed in scenarios.py (DIVERGENCES) and asserted here instead.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import port
import pytest
import scenarios
from row_files import files_in
from scenarios.builders.common import exchange, failed

from leadline import config, trends
from leadline.context import Critical, Finding
from leadline.http import SavedResponses
from leadline.sources.home_assistant import ha_integration
from leadline.sources.home_assistant.ha_integration.tests import scenarios as ha
from leadline.sources.home_assistant.tests.builders import HA_INTEGRATIONS_URL, ha_integration_entry

NAME = "ha_integration"
CONFIG = ha.LEADLINE_CONFIG
FILE = "hacs.jsonl"
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert ha_integration.NAME == NAME
    assert ha_integration.ENV == ()  # a public feed: no credentials
    assert ha_integration.LAGGING is False


def test_a_valid_config_has_no_problems() -> None:
    assert ha_integration.validate([{"integration": "a"}, {"integration": "b", "file": "x-{integration}.jsonl"}]) == []


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"integration": "a"}, [": must be a list of entries"]),
        (["a"], ["[0]: must be an object"]),
        ([{}], ["[0].integration: must be a non-empty string"]),
        ([{"integration": ""}], ["[0].integration: must be a non-empty string"]),
        ([{"integration": 5}], ["[0].integration: must be a non-empty string"]),
        ([{"integration": "a", "bands": "x"}], ["[0].bands: must be one of line, release, not 'x'"]),
        ([{"integration": "a", "bands": 3}], ["[0].bands: must be one of line, release, not 3"]),
        (
            [{"integration": "a"}, {"integration": "b", "file": "x-{label}.jsonl"}],
            ["[1].file: 'x-{label}.jsonl' uses {label}, which is not one of: integration"],
        ),
        (
            [{"integration": "a/b"}],
            [
                "[0].file: 'ha-integration-{integration}.jsonl' must fill to a plain *.jsonl name, "
                "not 'ha-integration-a/b.jsonl'"
            ],
        ),
    ],
    ids=[
        "not a list",
        "an entry not an object",
        "no integration",
        "empty integration",
        "integration not a string",
        "unknown bands",
        "bands not a string",
        "a bad file",
        "an integration that can't name a file",
    ],
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert ha_integration.validate(cfg) == problems


def test_valid_notes_have_no_problems() -> None:
    notes = [{"date": "2026-10-22", "text": "HA changed the feed"}, {"date": "2026-10-23", "text": "x" * 60}]

    assert ha_integration.validate([{"integration": "a", "notes": notes}, {"integration": "b", "notes": []}]) == []


@pytest.mark.parametrize(
    ("notes", "problems"),
    [
        ("x", ["[0].notes: must be a list of {date, text} objects"]),
        ([1], ["[0].notes[0]: must be an object with date and text"]),
        ([{"date": "2026-10-22"}], ["[0].notes[0]: must be an object with date and text"]),
        ([{"date": "2026-10-22", "text": "a", "x": 1}], ["[0].notes[0]: must be an object with date and text"]),
        ([{"date": "2026-13-01", "text": "a"}], ["[0].notes[0].date: must be a date, YYYY-MM-DD"]),
        ([{"date": "26-10-22", "text": "a"}], ["[0].notes[0].date: must be a date, YYYY-MM-DD"]),
        ([{"date": 20261022, "text": "a"}], ["[0].notes[0].date: must be a date, YYYY-MM-DD"]),
        ([{"date": "2026-10-22", "text": ""}], ["[0].notes[0].text: must be 1 to 60 characters"]),
        ([{"date": "2026-10-22", "text": "x" * 61}], ["[0].notes[0].text: must be 1 to 60 characters"]),
        ([{"date": "2026-10-22", "text": 5}], ["[0].notes[0].text: must be 1 to 60 characters"]),
        ([{"date": "2026-10-22", "text": "a"}] * 11, ["[0].notes: at most 10 notes, not 11"]),
    ],
    ids=[
        "not a list",
        "a note not an object",
        "no text",
        "an extra key",
        "not a calendar date",
        "not a full date",
        "a date not text",
        "empty text",
        "text too long",
        "text not a string",
        "too many",
    ],
)
def test_validate_names_each_problem_with_notes(notes: Any, problems: list[str]) -> None:
    assert ha_integration.validate([{"integration": "a", "notes": notes}]) == problems


def test_its_summary_passes_the_entrys_notes_on() -> None:
    plain, noted = ha_integration.summary(
        [{"integration": "a"}, {"integration": "b", "notes": [{"date": "2026-10-22", "text": "HA changed the feed"}]}]
    )

    assert (plain.notes, noted.notes) == ((), (config.Note("2026-10-22", "HA changed the feed"),))


def test_valid_bands_have_no_problems() -> None:
    assert (
        ha_integration.validate([{"integration": "a", "bands": "line"}, {"integration": "b", "bands": "release"}]) == []
    )


def test_its_summary_bands_the_versions_by_line_unless_the_entry_says_release() -> None:
    plain, line, release = ha_integration.summary(
        [{"integration": "a"}, {"integration": "b", "bands": "line"}, {"integration": "c", "bands": "release"}]
    )

    def versions(section: config.Section) -> config.Shown:
        return next(shown for shown in section.shown if shown.field == "versions")

    assert plain.shown == line.shown == ha_integration.SHOWN
    assert versions(release) == config.Shown("versions", "Installs by version", "breakdown", bands="release")
    assert [s for s in release.shown if s.field != "versions"] == [s for s in plain.shown if s.field != "versions"]


def test_files_are_the_default_or_the_template_of_each_entry() -> None:
    cfg = [{"integration": "a"}, {"integration": "b", "file": "hacs.jsonl"}]

    assert ha_integration.files(cfg) == {"[0]": "ha-integration-a.jsonl", "[1]": "hacs.jsonl"}


def test_it_needs_no_credentials_so_it_is_never_skipped(tmp_path: Path) -> None:
    """ENV is empty: with no credential variables set at all, it still runs."""
    scenario = replace(port.scenario(NAME, "ranks_among_the_feed"), env={})

    done = port.run(ha_integration, CONFIG, scenario, tmp_path)

    assert not any(line.startswith("SKIP:") for line in done.log)
    assert '"date": "2026-09-23", "total": 95' in done.rows[FILE]


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    done = port.run(ha_integration, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == [
        "a_feed_not_an_object_writes_nothing",
        "a_null_total_writes_nothing",
        "an_empty_feed_reads_as_no_installs",
        "failed_fetch_writes_nothing",
        "the_integrations_entry_not_an_object_writes_nothing",
        "unparseable_feed_writes_nothing",
    ]


# --- decided divergences: ADR "A failure is recorded as a failure" ---


def failure_row(status: str, reason: str, http_code: str | None = None) -> str:
    code = f', "http_code": "{http_code}"' if http_code is not None else ""
    return f'{{"date": "2026-09-23", "status": "{status}"{code}, "reason": "{reason}", "captured_at": "{NOW}"}}\n'


def test_a_failed_fetch_is_a_fetch_failed_row(tmp_path: Path) -> None:
    done = port.run(ha_integration, CONFIG, port.scenario(NAME, "failed_fetch_writes_nothing"), tmp_path)

    assert done.rows == {FILE: ha.EARLIER + failure_row("fetch_failed", "analytics request failed", "503")}
    assert (done.status, done.ctx.critical) == (0, [])  # recoverable: the next run writes the day


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("unparseable_feed_writes_nothing", "response was not JSON"),
        ("a_feed_not_an_object_writes_nothing", "response had an unexpected shape"),
        ("the_integrations_entry_not_an_object_writes_nothing", "response had an unexpected shape"),
        ("a_null_total_writes_nothing", "response had an unexpected shape"),
    ],
)
def test_an_answer_it_cant_read_is_a_parse_failed_row(tmp_path: Path, name: str, reason: str) -> None:
    done = port.run(ha_integration, CONFIG, port.scenario(NAME, name), tmp_path)

    assert done.rows == {FILE: ha.EARLIER + failure_row("parse_failed", reason)}
    assert (done.status, done.ctx.critical) == (0, [])


@pytest.mark.parametrize(
    "feed",
    [
        {ha.INTEGRATION: None},
        {ha.INTEGRATION: {"total": 3, "versions": None}},
        {ha.INTEGRATION: {"total": 3, "versions": ["1.0.0"]}},
        {ha.INTEGRATION: {"total": "3"}, "other": {"total": 5}},
        {ha.INTEGRATION: {"total": 3, "versions": {"1.²": 3}}},
        "5",
        None,
    ],
    ids=[
        "the entry null",
        "versions null",
        "versions a list",
        "a total not a number",
        "a version part isdigit takes but int refuses",
        "a number",
        "null",
    ],
)
def test_any_answer_the_scripts_code_stops_on_is_a_parse_failed_row(tmp_path: Path, feed: Any) -> None:
    scenario = replace(
        port.scenario(NAME, "failed_fetch_writes_nothing"), responses=[exchange(HA_INTEGRATIONS_URL, feed)]
    )

    done = port.run(ha_integration, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: ha.EARLIER + failure_row("parse_failed", "response had an unexpected shape")}


@pytest.mark.parametrize("feed", [{}, {"a_list": [1], "a_number": 12}], ids=["empty", "no entry an object"])
def test_a_feed_that_lists_no_integrations_is_a_parse_failed_row_not_zeros(tmp_path: Path, feed: Any) -> None:
    """Zero means zero: a feed with nothing in it is an outage, not an integration with no installs."""
    scenario = replace(
        port.scenario(NAME, "an_empty_feed_reads_as_no_installs"), responses=[exchange(HA_INTEGRATIONS_URL, feed)]
    )

    done = port.run(ha_integration, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: ha.EARLIER + failure_row("parse_failed", "the feed lists no integrations")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_failure_keeps_a_good_row(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data."""
    good = '{"date": "2026-09-23", "total": 96, "versions": {"3.13.1": 96}, "rank": 1}\n'
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), seed={FILE: ha.EARLIER + good})

    done = port.run(ha_integration, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: ha.EARLIER + good}
    assert f"WARN: kept the good row for 2026-09-23 in {FILE}; fetch_failed not recorded" in done.log


def test_a_good_row_replaces_a_failure_row(tmp_path: Path) -> None:
    failed = (
        '{"date": "2026-09-23", "status": "fetch_failed", "http_code": "000", "reason": "analytics request failed"}\n'
    )
    scenario = replace(port.scenario(NAME, "rerun_replaces_the_days_row"), seed={FILE: ha.EARLIER + failed})

    port.run(ha_integration, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, "rerun_replaces_the_days_row", tmp_path) == []


# --- the feed, read once for every entry ---

OTHER = {"integration": "other", "file": "other.jsonl"}


def test_every_entry_reads_the_one_feed(tmp_path: Path) -> None:
    done = port.run(ha_integration, [*CONFIG, OTHER], port.scenario(NAME, "the_integration_without_versions"), tmp_path)

    assert port.golden_differences(NAME, "the_integration_without_versions", tmp_path) == ["other.jsonl: only in B"]
    assert done.rows["other.jsonl"] == (
        '{"date": "2026-09-23", "total": 5, "versions": {"1.0.0": 5}, "rank": 2, "tracked_integrations": 2, '
        f'"percentile": 100.0, "captured_at": "{NOW}"}}\n'
    )


def test_one_entry_it_cant_read_does_not_stop_the_next(tmp_path: Path) -> None:
    done = port.run(
        ha_integration,
        [*CONFIG, OTHER],
        port.scenario(NAME, "the_integrations_entry_not_an_object_writes_nothing"),
        tmp_path,
    )

    assert done.rows[FILE] == ha.EARLIER + failure_row("parse_failed", "response had an unexpected shape")
    assert '"total": 5, "versions": {"1.0.0": 5}, "rank": 1, "tracked_integrations": 1' in done.rows["other.jsonl"]


def test_a_failed_fetch_is_a_failure_row_for_every_entry(tmp_path: Path) -> None:
    done = port.run(ha_integration, [*CONFIG, OTHER], port.scenario(NAME, "failed_fetch_writes_nothing"), tmp_path)

    assert done.rows["other.jsonl"] == failure_row("fetch_failed", "analytics request failed", "503")


# --- errors waiting can't fix ---


@pytest.mark.parametrize("code", [401, 403, 404])
def test_an_error_waiting_cant_fix_is_critical_at_once(tmp_path: Path, code: int) -> None:
    scenario = replace(
        port.scenario(NAME, "failed_fetch_writes_nothing"), responses=[exchange(HA_INTEGRATIONS_URL, "", status=code)]
    )

    done = port.run(ha_integration, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: ha.EARLIER + failure_row("fetch_failed", "analytics request failed", str(code))}
    assert done.ctx.critical == [
        Critical(NAME, FILE, f"{ha.INTEGRATION}: HTTP {code} from Home Assistant's custom integrations analytics")
    ]
    assert done.status == 1


def test_a_rate_limit_is_recoverable_not_critical(tmp_path: Path) -> None:
    """A 403 that says its limit is spent is one waiting fixes (http: HttpError.rate_limited)."""
    limited = exchange(HA_INTEGRATIONS_URL, "", status=403, headers={"Retry-After": "60"})
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=[limited])

    done = port.run(ha_integration, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: ha.EARLIER + failure_row("fetch_failed", "analytics request failed", "403")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_no_connection_is_recorded_as_000_and_is_recoverable(tmp_path: Path) -> None:
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=[failed(HA_INTEGRATIONS_URL)])

    done = port.run(ha_integration, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: ha.EARLIER + failure_row("fetch_failed", "analytics request failed", "000")}
    assert (done.status, done.sleeps) == (0, [])  # one attempt, as the script makes


# --- check() ---

FEED = "Home Assistant's custom integrations analytics"


def check(tmp_path: Path, cfg: Any, *responses: Any) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials({}, ha_integration.ENV):
        findings = ha_integration.check(ctx, cfg)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


def test_check_finds_the_integration_in_the_feed(tmp_path: Path) -> None:
    feed = exchange(HA_INTEGRATIONS_URL, {"other": ha_integration_entry(5), "mine": ha_integration_entry(9)})

    assert check(tmp_path, [{"integration": "mine"}], feed) == [
        Finding("ok", f"mine: listed in {FEED}, with 9 installs")
    ]


def test_check_finds_an_integration_the_feed_does_not_list(tmp_path: Path) -> None:
    """Every night would read as no installs: most likely the name isn't the integration's domain."""
    feed = exchange(HA_INTEGRATIONS_URL, {"other": ha_integration_entry(5)})

    assert check(tmp_path, [{"integration": "mine"}], feed) == [
        Finding(
            "error",
            f"mine: not listed in {FEED}, so every night reads as no installs; "
            "the entry names an integration by its domain",
        )
    ]


def test_check_reads_the_feed_once_for_every_entry(tmp_path: Path) -> None:
    feed = exchange(HA_INTEGRATIONS_URL, {"a": ha_integration_entry(5), "b": [1]})

    findings = check(tmp_path, [{"integration": "a"}, {"integration": "b"}, {"integration": "c"}], feed)

    assert [f.level for f in findings] == ["ok", "warn", "error"]
    assert findings[1] == Finding("warn", f"b: could not read {FEED}: unexpected answer")


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (exchange(HA_INTEGRATIONS_URL, "", status=503), Finding("warn", f"mine: could not fetch {FEED}: HTTP 503")),
        (failed(HA_INTEGRATIONS_URL), Finding("warn", f"mine: could not fetch {FEED}: HTTP 0")),
        (exchange(HA_INTEGRATIONS_URL, "", status=404), Finding("error", f"mine: could not fetch {FEED}: HTTP 404")),
        (exchange(HA_INTEGRATIONS_URL, "<html>"), Finding("warn", f"mine: could not read {FEED}: not JSON")),
        (exchange(HA_INTEGRATIONS_URL, [1]), Finding("warn", f"mine: could not read {FEED}: unexpected answer")),
        (
            exchange(HA_INTEGRATIONS_URL, {}),
            Finding("warn", f"mine: could not read {FEED}: the feed lists no integrations"),
        ),
    ],
    ids=["unavailable", "no connection", "gone", "not JSON", "not an object", "empty"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(tmp_path: Path, response: Any, finding: Finding) -> None:
    assert check(tmp_path, [{"integration": "mine"}], response) == [finding]


# --- the summary ---


def test_its_installs_total_is_charted_only_as_the_top_of_the_versions_stack() -> None:
    (section,) = ha_integration.summary(CONFIG)

    assert [(shown.field, shown.into) for shown in section.shown if shown.into or shown.kind == "breakdown"] == [
        ("total", "versions"),
        ("versions", ""),
    ]


def test_its_chart_has_the_feed_size_and_the_integrations_it_outranks_shaded_between() -> None:
    (section,) = ha_integration.summary(CONFIG)
    tracked, outranked = (shown for shown in section.shown if shown.chart)

    assert (tracked.field, tracked.chart, tracked.color, tracked.tip) == (
        "tracked_integrations",
        ha_integration.spec.CHART,
        "s3",
        ("rank", "percentile"),
    )
    assert (outranked.field, outranked.minus, outranked.band, outranked.chart, outranked.color) == (
        "tracked_integrations",
        "rank",
        "tracked_integrations",
        ha_integration.spec.CHART,
        "s1",
    )


def test_its_rank_and_percentile_are_in_the_summary_and_on_the_chart_only_as_the_hover() -> None:
    (section,) = ha_integration.summary(CONFIG)
    rank, percentile = (shown for shown in section.shown if shown.field in ("rank", "percentile"))

    assert (rank.charted, rank.chart, percentile.charted, percentile.chart) == (False, "", False, "")
    assert (percentile.format, percentile.complement) == (".1f", True)


def test_its_trends_chart_draws_the_two_lines_the_band_and_a_hover_with_the_rank_from_the_golden_rows(
    tmp_path: Path,
) -> None:
    (section,) = ha_integration.summary(
        [{**CONFIG[0], "notes": [{"date": "2026-09-22", "text": "HA changed the feed"}]}]
    )
    (tmp_path / "hacs.jsonl").write_text(
        '{"date": "2026-09-22", "total": 90, "rank": 3, "tracked_integrations": 60, "percentile": 5.0}\n'
        '{"date": "2026-09-23", "total": 95, "rank": 30, "tracked_integrations": 51, "percentile": 58.8}\n',
        encoding="utf-8",
    )

    data = trends.build(files_in(tmp_path), date(2026, 9, 23), [section], "health.jsonl")

    (built,) = data["sections"]
    by_label = {field["label"]: field for field in built["fields"]}
    assert by_label["Integrations we outrank"]["values"] == [57, 21]
    assert by_label["Custom integrations HA tracks"]["values"] == [60, 51]
    assert built["notes"] == [{"date": "2026-09-22", "text": "HA changed the feed"}]


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(ha_integration, CONFIG, "ranks_among_the_feed", tmp_path) == [
        "",
        "### ha_integration: cable_modem_monitor",
        "",
        "- Installs: 95",
        "- Installs by version: 3.12.0 55 · 3.13.1 40",
        "- Custom integrations HA tracks: 51",
        "- Rank in the HA feed (lower is better): 30",
        "- Percentile rank (HA custom integrations): 41.2",
    ]
