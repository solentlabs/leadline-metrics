"""pypi: the script's rows on its scenarios, and leadline's side of each decided divergence.

The rows are compared with the goldens through tests/port.py; the scenarios where leadline differs by
a decision are listed in scenarios.py (DIVERGENCES) and asserted here instead. Nothing here
calls pypistats or PyPI: every response is saved (docs/ARCHITECTURE_DECISIONS.md).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import port
import pytest
import scenarios
from scenarios.builders.common import exchange, failed

from leadline.context import Critical, Finding
from leadline.http import SavedResponses
from leadline.sources import pypi as source
from leadline.sources.pypi.tests import scenarios as sc
from leadline.sources.pypi.tests.builders import pypistats_overall

NAME = "pypi"
PACKAGE, OTHER = sc.PACKAGE, sc.OTHER
FILE, OTHER_FILE = f"pypi-{PACKAGE}.jsonl", f"pypi-{OTHER}.jsonl"
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it


def config(scenario: Any) -> dict[str, Any]:
    return sc.leadline_config(scenario)


def run_scenario(name: str, tmp_path: Path, **changes: Any) -> port.PortRun:
    """Scenario `name`, with `changes` (responses, seed) in place, run through the runner."""
    scenario = replace(port.scenario(NAME, name), **changes)
    return port.run(source, config(scenario), scenario, tmp_path)


def golden_lines(name: str, file: str = FILE) -> list[str]:
    lines: list[str] = scenarios.load_golden(NAME)[name]["rows"][file].splitlines(keepends=True)
    return lines


def row(**fields: Any) -> str:
    """A row this run writes for the run's date, in the order given."""
    return json.dumps({"date": "2026-09-23", **fields, "captured_at": NOW}) + "\n"


def failure(package: str, status: str, reason: str, http_code: str | None = None) -> str:
    code = {} if http_code is None else {"http_code": http_code}
    return row(package=package, status=status, **code, reason=reason)


MIRRORS = ("downloads_with_mirrors_last_day", "downloads_with_mirrors_last_week", "downloads_with_mirrors_last_month")


def with_mirrors(line: str, *values: int | None) -> str:
    """The script's row `line` with the three with-mirrors fields leadline adds, after the month's downloads."""
    fields = json.loads(line)
    out: dict[str, Any] = {}
    for key, value in fields.items():
        out[key] = value
        if key == "downloads_last_month":
            out.update(zip(MIRRORS, values))
    return json.dumps(out) + "\n"


def mirrors_on(history: dict[date, int | None], day: date, days: int, per_day: int = 3) -> int:
    """The with-mirrors downloads of the `days` ending on `day`: the builder gives each day its count plus
    `per_day` (a day without downloads has only the mirrors')."""
    return sum(
        (history.get(day - timedelta(days=n)) or 0) + per_day for n in range(days) if day - timedelta(days=n) in history
    )


# The good row for the run's date on the RECENT history, as the script writes it, with leadline's added fields.
GOOD = with_mirrors(
    golden_lines("downloads_for_the_day_week_and_month")[-1],
    mirrors_on(sc.RECENT, sc.RUN, 1),
    mirrors_on(sc.RECENT, sc.RUN, 7),
    mirrors_on(sc.RECENT, sc.RUN, 30),
)


def partial(good: str, reason: str) -> str:
    """`good` with its metadata unknown: null where the version was, `partial` where `ok` was."""
    fields = json.loads(good)
    del fields["captured_at"]
    nulls = {"latest_version": None, "requires_python": None}
    return json.dumps({**fields, "status": "partial", **nulls, "reason": reason, "captured_at": NOW}) + "\n"


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert source.NAME == NAME
    assert source.ENV == ()  # pypistats and PyPI are public
    assert source.LAGGING is False  # a 23:30 run finds its own date published


VALID = {"packages": ["example-package", "Other.Lib_2"], "sleep_s": 60, "retry_wait_s": 240}


def test_a_valid_config_has_no_problems() -> None:
    assert source.validate(VALID) == []
    assert source.validate({**VALID, "sleep_s": 0, "retry_wait_s": 1.5, "file": "downloads-{package}.jsonl"}) == []


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ([VALID], [": must be an object"]),
        ({**VALID, "packages": []}, [".packages: must be a non-empty list of package names"]),
        ({**VALID, "packages": "example-package"}, [".packages: must be a non-empty list of package names"]),
        ({**VALID, "packages": ["ok", ""]}, [".packages[1]: must be a package name"]),
        ({**VALID, "packages": ["ok", "a/b"]}, [".packages[1]: must be a package name"]),
        ({**VALID, "packages": ["-dash"]}, [".packages[0]: must be a package name"]),
        ({**VALID, "packages": [5]}, [".packages[0]: must be a package name"]),
        (
            {"packages": ["x"]},
            [
                ".sleep_s: must be a number of seconds, 0 or more",
                ".retry_wait_s: must be a number of seconds, 0 or more",
            ],
        ),
        ({**VALID, "sleep_s": -1}, [".sleep_s: must be a number of seconds, 0 or more"]),
        ({**VALID, "retry_wait_s": True}, [".retry_wait_s: must be a number of seconds, 0 or more"]),
        ({**VALID, "retry_wait_s": "240"}, [".retry_wait_s: must be a number of seconds, 0 or more"]),
        (
            {**VALID, "file": "x-{label}.jsonl"},
            [".file: 'x-{label}.jsonl' uses {label}, which is not one of: package"],
        ),
    ],
    ids=[
        "not an object",
        "no packages",
        "packages not a list",
        "an empty name",
        "a name with a slash",
        "a name starting with a dash",
        "a name not a string",
        "no waits",
        "a negative wait",
        "a boolean wait",
        "a string wait",
        "a bad file",
    ],
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert source.validate(cfg) == problems


def test_files_are_each_packages_default_or_template() -> None:
    assert source.files(VALID) == {
        ".packages[0]": "pypi-example-package.jsonl",
        ".packages[1]": "pypi-Other.Lib_2.jsonl",
    }
    assert source.files({**VALID, "file": "dl-{package}.jsonl"}) == {
        ".packages[0]": "dl-example-package.jsonl",
        ".packages[1]": "dl-Other.Lib_2.jsonl",
    }


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    done = port.run(source, config(scenario), scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


def bare(text: str) -> str:
    """Row `text` without the with-mirrors fields leadline adds."""
    return "".join(
        json.dumps({k: v for k, v in json.loads(line).items() if k not in MIRRORS}) + "\n" for line in text.splitlines()
    )


def without_added(folder: Path) -> None:
    """Take the with-mirrors fields leadline adds (more data is captured than the script did) out of the rows in
    `folder`, so the script's own can be compared; each is asserted on its own below."""
    for file in folder.glob("*.jsonl"):
        lines = []
        for line in file.read_text(encoding="utf-8").splitlines():
            try:
                fields = json.loads(line)
            except ValueError:  # a seed line that isn't a row stays as it is
                fields = None
            if isinstance(fields, dict):
                lines.append(json.dumps({k: v for k, v in fields.items() if k not in MIRRORS}) + "\n")
            else:
                lines.append(line + "\n")
        file.write_text("".join(lines), encoding="utf-8")


@pytest.mark.parametrize(
    "scenario",
    [s for s in scenarios.scenarios(NAME) if port.divergences(NAME).get(s.name) == sc.MORE_DATA],
    ids=lambda s: s.name,
)
def test_rows_are_the_scripts_with_the_added_mirror_downloads(scenario: Any, tmp_path: Path) -> None:
    """More data is captured than the script did: but for the with-mirrors fields, the rows are the script's."""
    done = port.run(source, config(scenario), scenario, tmp_path)

    assert any(MIRRORS[0] in text for text in done.rows.values())
    without_added(tmp_path)
    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_row_adds_the_downloads_with_mirrors_after_those_without(tmp_path: Path) -> None:
    done = run_scenario("downloads_for_the_day_week_and_month", tmp_path)

    written = json.loads(done.rows[FILE].splitlines()[-1])
    assert (written[MIRRORS[0]], written[MIRRORS[1]], written[MIRRORS[2]]) == (
        mirrors_on(sc.RECENT, sc.RUN, 1),
        mirrors_on(sc.RECENT, sc.RUN, 7),
        mirrors_on(sc.RECENT, sc.RUN, 30),
    )
    assert written[MIRRORS[0]] > written["downloads_last_day"]  # with mirrors, not without
    assert list(written)[:9] == [
        "date",
        "package",
        "status",
        "downloads_last_day",
        "downloads_last_week",
        "downloads_last_month",
        *MIRRORS,
    ]
    assert list(written)[9:] == ["latest_version", "requires_python", "captured_at"]
    assert done.rows[FILE].splitlines(keepends=True)[-1] == GOOD


def test_a_backfilled_day_has_its_downloads_with_mirrors(tmp_path: Path) -> None:
    done = run_scenario("backfills_missing_and_failed_days", tmp_path)

    backfilled = [json.loads(line) for line in done.rows[FILE].splitlines() if '"backfilled"' in line]
    assert backfilled
    for fields in backfilled:
        day = date.fromisoformat(fields["date"])
        assert fields[MIRRORS[0]] == mirrors_on(sc.RECENT, day, 1)
        assert fields[MIRRORS[1]] == mirrors_on(sc.RECENT, day, 7)
        assert fields[MIRRORS[2]] == mirrors_on(sc.RECENT, day, 30)


def test_a_history_without_with_mirrors_rows_leaves_the_fields_null_not_zero(tmp_path: Path) -> None:
    body = pypistats_overall(PACKAGE, sc.RECENT)
    body["data"] = [d for d in body["data"] if d["category"] != "with_mirrors"]

    done = run_scenario(
        "downloads_for_the_day_week_and_month",
        tmp_path,
        responses=[exchange(sc.overall_url(PACKAGE), body), sc.project(PACKAGE, "2026-05-01")],
    )

    written = json.loads(done.rows[FILE].splitlines()[-1])
    assert [written[k] for k in MIRRORS] == [None, None, None]
    assert written["status"] == "ok"
    assert written["downloads_last_week"] == json.loads(GOOD)["downloads_last_week"]  # the row is otherwise as today


def test_a_with_mirrors_row_it_cant_use_is_left_out_not_a_failure(tmp_path: Path) -> None:
    body = pypistats_overall(PACKAGE, sc.RECENT)
    body["data"].append({"category": "with_mirrors", "date": "2026-09-23", "downloads": None})

    done = run_scenario(
        "downloads_for_the_day_week_and_month",
        tmp_path,
        responses=[exchange(sc.overall_url(PACKAGE), body), sc.project(PACKAGE, "2026-05-01")],
    )

    written = json.loads(done.rows[FILE].splitlines()[-1])
    assert written["status"] == "ok"
    assert written[MIRRORS[0]] == mirrors_on(sc.RECENT, sc.RUN, 1)  # the day's usable row stands


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == [
        "a_body_that_is_not_json_writes_no_row",
        "a_failure_replaces_a_good_row_for_the_run_date",
        "a_failure_row_keeps_the_last_status",
        "a_null_requires_python_is_kept",
        "a_package_not_found_is_asked_again_in_the_retry_pass",
        "a_seed_date_that_is_not_a_string_writes_nothing",
        "a_seed_line_not_an_object_writes_nothing",
        "an_empty_history_writes_nothing",
        "backfill_starts_where_a_months_window_is_whole",
        "backfills_missing_and_failed_days",
        "downloads_for_the_day_week_and_month",
        "the_retry_pass_asks_again_for_failed_packages",
        "the_run_date_not_published_yet",
        "without_metadata_the_version_is_blank_and_backfill_starts_a_month_in",
    ]


def test_requests_are_spaced_and_the_retry_pass_waits_first(tmp_path: Path) -> None:
    """As the script: sleep_s between packages and between a package's two attempts; retry_wait_s before
    the retry pass, whose first package isn't spaced again."""
    done = run_scenario("the_retry_pass_asks_again_for_failed_packages", tmp_path)

    assert done.sleeps == [60, 60, 60, 240, 60, 60]
    assert f"PyPI retry pass: 2 package(s) to retry — {PACKAGE} {OTHER}" in done.log


@pytest.mark.parametrize(
    ("day", "first"), [(date(2026, 9, 23), PACKAGE), (date(2026, 9, 24), OTHER)], ids=["even", "odd"]
)
def test_each_night_starts_one_package_further_along(tmp_path: Path, day: date, first: str) -> None:
    """The order rotates on the date's ordinal, as the script's PYPI_ORDER, so no package is always last."""
    scenario = port.scenario(NAME, "backfill_starts_where_a_months_window_is_whole")
    scenario = replace(scenario, now=f"{day.isoformat()}T23:31:05", responses=[], seed={})
    responses = [failed(sc.overall_url(p)) for p in (PACKAGE, OTHER) for _ in range(2)]

    done = port.run(source, config(scenario), replace(scenario, responses=responses * 2), tmp_path)

    collecting = [line for line in done.log if line.startswith("Collecting PyPI stats for ")]
    second = OTHER if first == PACKAGE else PACKAGE
    assert collecting == [f"Collecting PyPI stats for {p}..." for p in (first, second, first, second)]


# --- An error waiting can't fix is never retried, and is critical on its first night ---


def test_a_package_not_found_is_not_asked_again_and_is_critical(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "a_package_not_found_is_asked_again_in_the_retry_pass")

    done = run_scenario(scenario.name, tmp_path, responses=scenario.responses[:1])

    reason = "pypistats overall endpoint unavailable"  # the script's reason for any status but 429
    assert done.rows == {FILE: failure(PACKAGE, "fetch_failed", reason, "404")}
    assert done.ctx.critical == [Critical(NAME, FILE, f"{PACKAGE}: HTTP 404 from pypistats")]
    assert (done.status, done.sleeps) == (1, [])
    assert "PyPI retry pass: nothing to retry" in done.log


def test_a_missing_package_is_asked_once_and_writes_the_scripts_rows(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "a_failure_row_keeps_the_last_status")
    once = scenario.responses[:-1]  # the script asks for the missing package again in its retry pass
    assert once[-1]["request"]["url"] == sc.overall_url(sc.MISSING)

    done = run_scenario(scenario.name, tmp_path, responses=once)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    missing_file = f"pypi-{sc.MISSING}.jsonl"
    assert done.ctx.critical == [Critical(NAME, missing_file, f"{sc.MISSING}: HTTP 404 from pypistats")]
    assert done.status == 1


@pytest.mark.parametrize("code", [401, 403])
def test_a_rejected_request_is_not_retried_and_is_critical(tmp_path: Path, code: int) -> None:
    done = run_scenario(
        "a_package_not_found_is_asked_again_in_the_retry_pass",
        tmp_path,
        responses=[exchange(sc.overall_url(PACKAGE), "", status=code)],
    )

    reason = "pypistats overall endpoint unavailable"
    assert done.rows == {FILE: failure(PACKAGE, "fetch_failed", reason, str(code))}
    assert done.ctx.critical == [Critical(NAME, FILE, f"{PACKAGE}: HTTP {code} from pypistats")]


# --- ADR: a failure never replaces good data ---


def test_a_failure_keeps_the_run_dates_good_row_and_is_not_retried(tmp_path: Path) -> None:
    """An earlier run of the day got the row: nothing to retry, so pypistats isn't asked twice more."""
    scenario = port.scenario(NAME, "a_failure_replaces_a_good_row_for_the_run_date")

    done = run_scenario(scenario.name, tmp_path, responses=scenario.responses[:2])

    assert done.rows == scenario.seed
    assert f"WARN: kept the good row for 2026-09-23 in {FILE}; fetch_failed not recorded" in done.log
    assert "PyPI retry pass: nothing to retry" in done.log
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_run_date_not_published_keeps_a_good_row_and_still_backfills(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "the_run_date_not_published_yet")
    good = sc.row("2026-09-23")
    seed = {FILE: scenario.seed[FILE] + good}

    done = run_scenario(scenario.name, tmp_path, seed=seed)

    script = golden_lines(scenario.name)  # the seed's row, the not-published row, two backfilled days
    assert bare(done.rows[FILE]) == seed[FILE] + "".join(script[2:])
    assert "WARN: kept the good row for 2026-09-23 in " + FILE + "; fetch_failed not recorded" in done.log


# --- the retry pass and the run's reuse of answers ---


def test_the_retry_pass_sends_its_requests_afresh(tmp_path: Path) -> None:
    """It asks again only for a request that failed, and a failure is never kept, so nothing is reused:
    every saved response is served (port.run fails on one never served)."""
    done = run_scenario("the_retry_pass_asks_again_for_failed_packages", tmp_path)

    assert not [line for line in done.log if line.startswith("Reused the answer")]


def test_the_retry_pass_never_asks_again_for_an_answer_it_got(tmp_path: Path) -> None:
    """An answer it can't read is a parse_failed row, not retried: asking again would only be served the
    same kept answer (ADR: a run reuses its successful answers). An earlier run's failure is replaced."""
    earlier = failure(PACKAGE, "fetch_failed", "rate limited by pypistats", "429").replace(
        NOW, "2026-09-23T08:00:00+00:00"
    )
    responses = [exchange(sc.overall_url(PACKAGE), "<html>maintenance</html>")]

    done = run_scenario("downloads_for_the_day_week_and_month", tmp_path, responses=responses, seed={FILE: earlier})

    assert done.rows == {FILE: failure(PACKAGE, "parse_failed", "overall response was not JSON")}
    assert "PyPI retry pass: nothing to retry" in done.log
    assert not [line for line in done.log if line.startswith("Reused the answer")]


# --- ADR: a failure is recorded as a failure (the download history is the row's required part) ---


def test_a_history_that_is_not_json_is_a_parse_failed_row(tmp_path: Path) -> None:
    """The metadata isn't asked for: there are no downloads to write it with."""
    scenario = port.scenario(NAME, "a_body_that_is_not_json_writes_no_row")
    responses = [r for r in scenario.responses if r["request"]["url"] != sc.project_url(PACKAGE)]

    done = run_scenario(scenario.name, tmp_path, responses=responses)

    assert done.rows[FILE] == scenario.seed[FILE] + failure(PACKAGE, "parse_failed", "overall response was not JSON")
    assert done.rows[OTHER_FILE] == scenario.seed[OTHER_FILE] + partial(
        GOOD.replace(PACKAGE, OTHER), "metadata response was not JSON"
    )
    assert (done.status, done.ctx.critical) == (0, [])


def test_an_empty_history_is_a_parse_failed_row(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "an_empty_history_writes_nothing")

    done = run_scenario(scenario.name, tmp_path, responses=scenario.responses[:1])

    expected = scenario.seed[FILE] + failure(PACKAGE, "parse_failed", "pypistats returned no history")
    assert done.rows == {FILE: expected}


DAY = {"category": "without_mirrors", "date": "2026-09-23", "downloads": 3}


@pytest.mark.parametrize(
    "body",
    [
        [DAY],
        {"data": {}},
        {"data": [5]},
        {"data": [{**DAY, "date": None}]},
        {"data": [{**DAY, "date": "yesterday"}]},
        {"data": [{**DAY, "downloads": None}]},
        {"data": [{**DAY, "downloads": True}]},
        {"data": [{"category": "without_mirrors", "downloads": 3}]},
    ],
    ids=[
        "a list",
        "the data an object",
        "a day not an object",
        "a null date",
        "a date not a date",
        "a null count",
        "a count a boolean",
        "no date",
    ],
)
def test_a_history_it_cant_use_is_a_parse_failed_row(tmp_path: Path, body: Any) -> None:
    done = run_scenario(
        "downloads_for_the_day_week_and_month",
        tmp_path,
        responses=[exchange(sc.overall_url(PACKAGE), body)],
        seed={},
    )

    assert done.rows == {FILE: failure(PACKAGE, "parse_failed", "overall response had an unexpected shape")}


# --- ADR: a partly failed row keeps its good parts (the metadata is optional) ---


def test_without_metadata_the_version_is_null_in_a_partial_row_and_the_backfill_starts_a_month_in(
    tmp_path: Path,
) -> None:
    """The script writes "" for an unknown version; null keeps unknown apart from none. A 404 from PyPI
    is critical. Without the upload dates, the backfill still waits for a whole month's window."""
    scenario = port.scenario(NAME, "without_metadata_the_version_is_blank_and_backfill_starts_a_month_in")

    done = port.run(source, config(scenario), scenario, tmp_path)

    script = golden_lines(scenario.name)
    reason = "metadata request failed (HTTP 404)"
    assert bare(done.rows[FILE]) == "".join(script[:2]) + partial(script[2], reason) + "".join(script[3:])
    assert done.ctx.critical == [Critical(NAME, FILE, f"{PACKAGE}: HTTP 404 from PyPI")]
    assert done.status == 1


def test_a_recoverable_metadata_failure_is_a_partial_row_neither_retried_nor_critical(tmp_path: Path) -> None:
    responses = [sc.downloads(PACKAGE, sc.RECENT), failed(sc.project_url(PACKAGE))]

    done = run_scenario("downloads_for_the_day_week_and_month", tmp_path, responses=responses, seed={})

    assert done.rows == {FILE: partial(GOOD, "metadata request failed (HTTP 000)")}
    assert (done.status, done.ctx.critical) == (0, [])
    assert "PyPI retry pass: nothing to retry" in done.log


@pytest.mark.parametrize(
    "body",
    [[], {"info": []}, {"releases": []}, {"releases": {"0.1.0": {}}}, {"releases": {"0.1.0": [5]}}],
    ids=["a list", "the info a list", "the releases a list", "a release's files an object", "a file not an object"],
)
def test_metadata_it_cant_use_is_null_in_a_partial_row(tmp_path: Path, body: Any) -> None:
    responses = [sc.downloads(PACKAGE, sc.RECENT), exchange(sc.project_url(PACKAGE), body)]

    done = run_scenario("downloads_for_the_day_week_and_month", tmp_path, responses=responses, seed={})

    assert done.rows == {FILE: partial(GOOD, "metadata response had an unexpected shape")}


def test_metadata_without_info_reads_as_the_script_reads_it(tmp_path: Path) -> None:
    """An object without `info` is metadata that names no version: "" as in the script, not a failure."""
    responses = [sc.downloads(PACKAGE, sc.RECENT), exchange(sc.project_url(PACKAGE), {"releases": {}})]

    done = run_scenario("downloads_for_the_day_week_and_month", tmp_path, responses=responses, seed={})

    fields = {**json.loads(GOOD), "latest_version": "", "requires_python": ""}
    assert done.rows == {FILE: json.dumps(fields) + "\n"}


# --- ADR: a run never erases what it didn't get ---


def test_failed_metadata_keeps_the_version_an_earlier_run_got_and_the_row_is_good(tmp_path: Path) -> None:
    earlier = GOOD.replace(NOW, "2026-09-23T08:00:00+00:00")
    responses = [sc.downloads(PACKAGE, sc.RECENT), exchange(sc.project_url(PACKAGE), "", status=503)]

    done = run_scenario("downloads_for_the_day_week_and_month", tmp_path, responses=responses, seed={FILE: earlier})

    assert done.rows == {
        FILE: earlier
    }  # the row is as the earlier run wrote it: nothing changed, so nothing is touched
    stored = f"Kept the stored latest_version, requires_python for 2026-09-23 in {FILE}, which this run failed to get"
    assert stored in done.log


def test_a_past_days_partial_row_is_backfilled(tmp_path: Path) -> None:
    """A partial row's only values are its downloads, which the history re-reads whole: backfilled as
    a failed day is, as the script backfills any day not ok."""
    past = partial(GOOD, "metadata request failed (HTTP 503)").replace("2026-09-23", "2026-09-22")
    seed = {FILE: sc.row("2026-09-21") + past}

    done = run_scenario("downloads_for_the_day_week_and_month", tmp_path, seed=seed)

    backfilled = json.loads(done.rows[FILE].splitlines()[-1])
    assert (backfilled["date"], backfilled["status"], backfilled["backfilled"]) == ("2026-09-22", "ok", True)


# --- ADR: store never fails on a line it can't use ---


@pytest.mark.parametrize(
    "name", ["a_seed_line_not_an_object_writes_nothing", "a_seed_date_that_is_not_a_string_writes_nothing"]
)
def test_a_seed_line_that_is_not_a_row_is_kept_and_the_rows_written(tmp_path: Path, name: str) -> None:
    """The script stops on the line and writes nothing; leadline keeps it and writes the run's row."""
    scenario = port.scenario(NAME, name)

    done = port.run(source, config(scenario), scenario, tmp_path)

    assert done.rows == {FILE: scenario.seed[FILE] + GOOD}
    assert done.status == 0


def test_a_seed_date_that_is_not_a_date_is_left_out_of_the_backfill(tmp_path: Path) -> None:
    seed = {FILE: '{"date": "someday", "status": "ok"}\n' + sc.row("2026-09-22")}

    done = run_scenario("downloads_for_the_day_week_and_month", tmp_path, seed=seed)

    assert done.rows == {FILE: seed[FILE] + GOOD}


# --- check(): pypi.org only, never pypistats ---


def check(tmp_path: Path, *responses: Any) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    findings = source.check(ctx, {**VALID, "packages": [PACKAGE]})
    assert transport.unmatched == transport.never_served() == []  # a pypistats request would be unmatched
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


def test_check_finds_the_package_on_pypi(tmp_path: Path) -> None:
    findings = check(tmp_path, sc.project(PACKAGE, "2026-05-01"))

    assert findings == [Finding("ok", f"{PACKAGE}: PyPI has the package, at version 0.2.0")]


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (
            exchange(sc.project_url(PACKAGE), '{"message": "Not Found"}', status=404),
            Finding("error", f"{PACKAGE}: HTTP 404 from PyPI: the package doesn't exist, or PyPI refuses it"),
        ),
        (
            exchange(sc.project_url(PACKAGE), "", status=403),
            Finding("error", f"{PACKAGE}: HTTP 403 from PyPI: the package doesn't exist, or PyPI refuses it"),
        ),
        (
            exchange(sc.project_url(PACKAGE), "", status=429),
            Finding("warn", f"{PACKAGE}: could not read PyPI: HTTP 429"),
        ),
        (failed(sc.project_url(PACKAGE)), Finding("warn", f"{PACKAGE}: could not read PyPI: HTTP 0")),
        (
            exchange(sc.project_url(PACKAGE), "<html>maintenance</html>"),
            Finding("warn", f"{PACKAGE}: could not read PyPI: metadata response was not JSON"),
        ),
        (
            exchange(sc.project_url(PACKAGE), []),
            Finding("warn", f"{PACKAGE}: could not read PyPI: metadata response had an unexpected shape"),
        ),
    ],
    ids=["not found", "forbidden", "rate limited", "no connection", "not JSON", "not an object"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(tmp_path: Path, response: Any, finding: Finding) -> None:
    assert check(tmp_path, response) == [finding]


# --- the summary ---


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(
        source,
        sc.leadline_config(port.scenario(NAME, "backfill_starts_where_a_months_window_is_whole")),
        "backfill_starts_where_a_months_window_is_whole",
        tmp_path,
    ) == [
        "",
        "### pypi: example-package",
        "",
        "- Downloads, last week: 156 · +27 in 7d",
        "- Downloads, last month: 577",
        "- Version: 0.2.0",
        "",
        "### pypi: other-package",
        "",
        "- Downloads, last week: 168 · -11 in 7d",
        "- Downloads, last month: 479",
        "- Version: 0.2.0",
    ]
