"""cloudflare_web: the script's rows on its scenarios, and leadline's side of each decided divergence.

The rows are compared with the goldens through tests/port.py; the scenarios where leadline differs by
a decision are listed in scenarios.py (DIVERGENCES) and asserted here instead.
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

from leadline.context import Correction, Critical, Finding
from leadline.http import SavedResponses
from leadline.sources.cloudflare import cloudflare_web
from leadline.sources.cloudflare.cloudflare_web.tests import scenarios as cf
from leadline.sources.cloudflare.tests.builders import (
    CLOUDFLARE_API,
    CLOUDFLARE_GRAPHQL,
    rum_day,
    rum_group,
    rum_list,
    rum_site,
    rum_sites,
    web_analytics,
)

NAME = "cloudflare_web"
CONFIG = cf.LEADLINE_CONFIG
FILE = cf.FILE
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it


def run(scenario: Any, folder: Path, cfg: Any = CONFIG, lists: bool | None = None) -> Any:
    """The source on the scenario, given leadline's two list answers too where it reads the main answer, which
    the script never asks for (`lists` says so where the scenario's responses were replaced)."""
    if lists is None:
        lists = scenario.name in cf.READS_LISTS
    responses = [*scenario.responses, *cf.lists()] if lists else scenario.responses
    return port.run(cloudflare_web, cfg, replace(scenario, responses=responses), folder)


def filled(when: date, pageviews: int, visits: int) -> str:
    """The row a heal writes for a past day: read under the configured site tag, marked backfilled."""
    row = {"date": when.isoformat(), "site": "example", "site_tag": cf.SITE, "status": "ok"}
    return json.dumps({**row, "pageviews": pageviews, "visits": visits, "backfilled": True, "captured_at": NOW}) + "\n"


def listed(line: str) -> str:
    """The script's row for yesterday with the top countries and devices leadline adds, before `captured_at`."""
    row = json.loads(line)
    captured_at = row.pop("captured_at")
    return (
        json.dumps(
            {**row, "top_countries": cf.TOP_COUNTRIES, "top_devices": cf.TOP_DEVICES, "captured_at": captured_at}
        )
        + "\n"
    )


def corrected(stored: str, pageviews: int, visits: int) -> str:
    """A stored good row with its numbers corrected, its other fields kept, as leadline rewrites it."""
    row = {**json.loads(stored), "pageviews": pageviews, "visits": visits, "captured_at": NOW}
    return json.dumps(row) + "\n"


YESTERDAY_ROW = (
    '{"date": "2026-09-22", "site": "example", "site_tag": "site0tag0example", "status": "ok", "pageviews": 40, '
    '"visits": 10, "window_start": "2026-09-16", "window_end": "2026-09-22", "window_pageviews": 175, '
    '"window_visits": 49, "days_returned": 7, "top_referrers": [{"ref": "(direct)", "count": 60}, '
    '{"ref": "github.com", "count": 20}, {"ref": "community.home-assistant.io", "count": 5}], '
    '"top_countries": [{"name": "US", "count": 30}, {"name": "DE", "count": 12}, {"name": "(unknown)", "count": 3}], '
    '"top_devices": [{"name": "desktop", "count": 25}, {"name": "mobile", "count": 20}], '
    '"captured_at": "' + NOW + '"}\n'
)


def parse_failed(reason: str) -> str:
    return (
        f'{{"date": "2026-09-22", "site": "example", "status": "parse_failed", "reason": "{reason}", '
        f'"captured_at": "{NOW}"}}\n'
    )


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert cloudflare_web.NAME == NAME
    assert cloudflare_web.ENV == ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID")
    assert cloudflare_web.LAGGING is True


def test_a_valid_config_has_no_problems() -> None:
    assert (
        cloudflare_web.validate(
            [{"label": "a", "site_tag": "t1"}, {"label": "b", "site_tag": "t2", "file": "x-{label}.jsonl"}]
        )
        == []
    )


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"label": "a"}, [": must be a list of entries"]),
        (["a"], ["[0]: must be an object"]),
        ([{"site_tag": "t"}], ["[0].label: must be a non-empty string"]),
        ([{"label": "", "site_tag": "t"}], ["[0].label: must be a non-empty string"]),
        ([{"label": "a"}], ["[0].site_tag: must be a non-empty string"]),
        ([{"label": "a", "site_tag": 5}], ["[0].site_tag: must be a non-empty string"]),
        (
            [{"label": "a", "site_tag": "t"}, {"label": "b", "site_tag": "t", "file": "x-{site}.jsonl"}],
            ["[1].file: 'x-{site}.jsonl' uses {site}, which is not one of: label"],
        ),
        (
            [{"label": "a/b", "site_tag": "t"}],
            [
                "[0].file: 'cloudflare-web-{label}.jsonl' must fill to a plain *.jsonl name, "
                "not 'cloudflare-web-a/b.jsonl'"
            ],
        ),
    ],
    ids=[
        "not a list",
        "an entry not an object",
        "no label",
        "empty label",
        "no site tag",
        "site tag not a string",
        "a bad file",
        "a label that can't name a file",
    ],
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert cloudflare_web.validate(cfg) == problems


def test_files_are_the_default_or_the_template_of_each_entry() -> None:
    cfg = [{"label": "a", "site_tag": "t1"}, {"label": "b", "site_tag": "t2", "file": "cloudflare-{label}.jsonl"}]

    assert cloudflare_web.files(cfg) == {"[0]": "cloudflare-web-a.jsonl", "[1]": "cloudflare-b.jsonl"}


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    run(scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == sorted(
        [
            *cf.MORE_DATA_SCENARIOS,
            *DECIDED,
        ]
    )


DECIDED = [
    "a_failure_replaces_a_good_row",
    "a_good_row_whose_numbers_changed_is_left",
    "a_list_body_writes_no_row",
    "a_null_body_writes_no_row",
    "a_null_viewer_writes_no_row",
    "a_seed_line_not_an_object_stops_the_write",
    "an_error_that_is_not_an_object_writes_no_row",
    "null_data_reads_as_no_account",
]


def test_it_is_skipped_without_its_credentials(tmp_path: Path) -> None:
    done = run(port.scenario(NAME, "skips_without_credentials"), tmp_path)

    assert (done.status, done.rows) == (0, {})
    assert "SKIP: cloudflare_web (CLOUDFLARE_ACCOUNT_ID not set)" in done.log


def test_retries_wait_10_then_20_seconds(tmp_path: Path) -> None:
    done = run(port.scenario(NAME, "retries_until_the_request_succeeds"), tmp_path)

    assert done.sleeps == [10, 20]
    assert done.status == 0


@pytest.mark.parametrize(
    "name", ["three_failed_attempts_record_the_last_status", "graphql_errors_in_a_success", "account_matched_nothing"]
)
def test_a_failure_row_is_recoverable_so_the_run_exits_0(tmp_path: Path, name: str) -> None:
    done = run(port.scenario(NAME, name), tmp_path)

    assert '"date": "2026-09-22", "site": "example", "status": "ok"' not in done.rows[FILE]  # yesterday failed
    assert (done.status, done.ctx.critical) == (0, [])
    assert not any(line.startswith("ERROR: cloudflare_web crashed") for line in done.log)


# --- decided divergences ---


def test_a_failure_keeps_a_good_row(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data (the script replaces it: a_failure_replaces_a_good_row)."""
    scenario = port.scenario(NAME, "a_failure_replaces_a_good_row")

    done = run(scenario, tmp_path)

    assert done.rows == {FILE: scenario.seed[FILE]}
    assert done.status == 0
    result = (
        'Cloudflare example result: {"date": "2026-09-22", "site": "example", "status": "fetch_failed", '
        '"http_code": "000", "reason": "graphql request failed"}'
    )
    kept = f"WARN: kept the good row for 2026-09-22 in {FILE}; fetch_failed not recorded"
    assert done.log[done.log.index(result) + 1] == kept


def test_a_failure_row_replaces_a_failure_row(tmp_path: Path) -> None:
    scenario = replace(
        port.scenario(NAME, "three_failed_attempts_record_the_last_status"), seed={FILE: cf.fetch_failed(cf.YESTERDAY)}
    )

    done = run(scenario, tmp_path)

    assert done.rows[FILE] == (
        '{"date": "2026-09-22", "site": "example", "status": "fetch_failed", "http_code": "503", '
        f'"reason": "graphql request failed", "captured_at": "{NOW}"}}\n'
    )


@pytest.mark.parametrize(
    "name",
    [
        "null_data_reads_as_no_account",
        "a_null_body_writes_no_row",
        "a_list_body_writes_no_row",
        "a_null_viewer_writes_no_row",
        "an_error_that_is_not_an_object_writes_no_row",
    ],
)
def test_a_malformed_answer_is_a_parse_failed_row(tmp_path: Path, name: str) -> None:
    """ADR: a failure is recorded as a failure: never no row, never zeros, never no_account for null data."""
    done = run(port.scenario(NAME, name), tmp_path)

    assert done.rows == {FILE: parse_failed("response had an unexpected shape")}
    assert done.status == 0


DATED = {"dimensions": {"date": "2026-09-22"}}  # a daily group the script reads: one with a date


@pytest.mark.parametrize(
    "body",
    [
        {"data": {}, "errors": None},
        {"data": {"viewer": {}}, "errors": None},
        {"data": {"viewer": {"accounts": None}}, "errors": None},
        {"data": {"viewer": {"accounts": ["x"]}}, "errors": None},
        {"data": {"viewer": {"accounts": [{"daily": {"count": 1}}]}}, "errors": None},
        {"data": {"viewer": {"accounts": [{"daily": [{"count": "5", **DATED}]}]}}, "errors": None},
        {"data": {"viewer": {"accounts": [{"daily": [{"sum": [1], **DATED}]}]}}, "errors": None},
        {"data": {"viewer": {"accounts": [{"referrers": [{"dimensions": "x"}]}]}}, "errors": None},
        {"data": {"viewer": {"accounts": [{"daily": [{"dimensions": {"date": 20260922}}]}]}}, "errors": None},
        {"errors": {"message": "busy"}},
        {"errors": "busy"},
        "5",
    ],
    ids=[
        "no viewer",
        "no accounts",
        "null accounts",
        "an account not an object",
        "daily not a list",
        "a count not a number",
        "a sum not an object",
        "dimensions not an object",
        "a date not a string",
        "errors an object",
        "errors a string",
        "a number",
    ],
)
def test_any_answer_it_cant_read_is_a_parse_failed_row(tmp_path: Path, body: Any) -> None:
    scenario = replace(port.scenario(NAME, "quiet_week"), responses=[cf.answer(body)])

    done = run(scenario, tmp_path, lists=False)

    assert done.rows == {FILE: parse_failed("response had an unexpected shape")}


def test_an_error_beyond_the_first_three_is_not_read(tmp_path: Path) -> None:
    """The script reads only the first three errors; a fourth that isn't an object changes nothing."""
    body = {"data": None, "errors": [{"message": "a"}, {"message": "b"}, {"message": "c"}, "d"]}
    scenario = replace(port.scenario(NAME, "quiet_week"), responses=[cf.answer(body)])

    done = run(scenario, tmp_path, lists=False)

    assert '"status": "graphql_error", "reason": "a; b; c"' in done.rows[FILE]


def test_a_seed_line_that_is_not_an_object_is_kept_and_the_row_written(tmp_path: Path) -> None:
    """ADR: store never fails on a line it can't use (the script stops reading its rows on it and writes none)."""
    done = run(port.scenario(NAME, "a_seed_line_not_an_object_stops_the_write"), tmp_path)

    assert done.rows == {FILE: "[1]\n" + filled(cf.YESTERDAY - timedelta(days=1), 35, 9) + YESTERDAY_ROW}
    assert done.status == 0


# --- ADR: cloudflare_web re-reads and corrects its last 30 days ---


def test_a_good_row_whose_numbers_changed_is_corrected(tmp_path: Path) -> None:
    """The script leaves it (a_good_row_whose_numbers_changed_is_left); leadline corrects it and records that."""
    scenario = port.scenario(NAME, "a_good_row_whose_numbers_changed_is_left")
    old = cf.stored(cf.day(cf.TENTH), 3, 1)

    done = run(scenario, tmp_path)

    script = scenarios.load_golden(NAME)[scenario.name]["rows"][FILE].splitlines(keepends=True)
    assert done.rows[FILE].splitlines(keepends=True) == [
        *(line for line in script[:-1] if line != old),
        corrected(old, 37, 6),  # appended, as upsert does
        listed(script[-1]),  # yesterday
    ]
    assert done.ctx.corrections == [
        Correction(NAME, FILE, "2026-09-10", {"pageviews": 3, "visits": 1}, {"pageviews": 37, "visits": 6})
    ]
    assert "Corrected 2026-09-10 in cloudflare-example.jsonl: pageviews 3 -> 37, visits 1 -> 6" in done.log


def test_the_30_days_are_healed_and_corrected_in_one_pass(tmp_path: Path) -> None:
    """Each kind of day, as the script heals it (filled, backfilled) or as leadline corrects it."""
    seed = cf.stored_month(
        instead={
            17: "",  # 2026-09-10: no row
            18: cf.fetch_failed(cf.day(18)),  # 2026-09-11: failed
            19: cf.stored(cf.day(19), 0, 0, site_tag=cf.OTHER_SITE),  # 2026-09-12: another tag
            20: cf.stored(cf.day(20), 0, 0, site_tag=None),  # 2026-09-13: no tag, before rows carried one
            21: cf.stored(cf.day(21), 1, 1),  # 2026-09-14: this tag, numbers changed
        }
    )
    scenario = replace(port.scenario(NAME, "heals_a_day_with_no_row"), seed={FILE: seed})

    done = run(scenario, tmp_path)

    lines = done.rows[FILE].splitlines(keepends=True)
    assert lines[:-6] == cf.stored_month(instead=dict.fromkeys(range(17, 22), "")).splitlines(keepends=True)
    assert lines[-6:-1] == [
        filled(*cf.month_day(17)),
        filled(*cf.month_day(18)),
        filled(*cf.month_day(19)),
        filled(*cf.month_day(20)),
        corrected(cf.stored(cf.day(21), 1, 1), 41, 6),
    ]
    assert lines[-1].startswith(
        '{"date": "2026-09-22", "site": "example", "site_tag": "site0tag0example", "status": "ok"'
    )
    assert [c.date for c in done.ctx.corrections] == ["2026-09-12", "2026-09-13", "2026-09-14"]
    healed = json.loads(filled(*cf.month_day(17)))
    del healed["captured_at"]
    assert f"Cloudflare example backfill: {json.dumps(healed)}" in done.log
    assert done.status == 0


def test_a_day_the_answer_leaves_out_reads_as_zero_as_yesterday_does(tmp_path: Path) -> None:
    seed = cf.stored(cf.YESTERDAY - timedelta(days=3), 5, 2) + cf.stored(cf.YESTERDAY - timedelta(days=2), 0, 0)
    scenario = replace(port.scenario(NAME, "yesterday_not_in_the_window"), seed={FILE: seed})

    done = run(scenario, tmp_path)

    lines = done.rows[FILE].splitlines(keepends=True)
    assert lines[:3] == [
        cf.stored(cf.YESTERDAY - timedelta(days=2), 0, 0),
        corrected(cf.stored(cf.YESTERDAY - timedelta(days=3), 5, 2), 0, 0),
        filled(cf.YESTERDAY - timedelta(days=1), 0, 0),
    ]
    assert [c.date for c in done.ctx.corrections] == ["2026-09-19"]


def test_a_new_file_gets_only_yesterdays_row(tmp_path: Path) -> None:
    done = run(replace(port.scenario(NAME, "week_of_traffic"), seed={}), tmp_path)

    assert done.rows == {FILE: YESTERDAY_ROW}


def test_rows_older_than_the_30_days_are_never_touched_even_when_the_answer_has_them(tmp_path: Path) -> None:
    seed = cf.fetch_failed(cf.day(-3)) + cf.stored(cf.day(-1), 3, 1)
    stray = web_analytics([rum_day(cf.day(-3), 9, 9), rum_day(cf.day(-1), 9, 9)])
    scenario = replace(port.scenario(NAME, "quiet_week"), responses=[cf.answer(stray)], seed={FILE: seed})

    done = run(scenario, tmp_path)

    assert done.rows[FILE].startswith(seed)
    assert done.ctx.corrections == []
    assert '"window_pageviews": 0' in done.rows[FILE]  # nor counted in the week


def test_yesterdays_good_row_is_rewritten_and_a_change_reported(tmp_path: Path) -> None:
    """An earlier run the same day read yesterday as 39 page views; each value that changed is reported."""
    earlier = (
        YESTERDAY_ROW.replace('"pageviews": 40', '"pageviews": 39')
        .replace('"window_pageviews": 175', '"window_pageviews": 174')
        .replace(NOW, "2026-09-23T08:00:00+00:00")
    )
    scenario = replace(port.scenario(NAME, "week_of_traffic"), seed={FILE: earlier})

    done = run(scenario, tmp_path)

    assert done.rows == {FILE: YESTERDAY_ROW}
    assert done.ctx.corrections == [
        Correction(
            NAME,
            FILE,
            "2026-09-22",
            {"pageviews": 39, "window_pageviews": 174},
            {"pageviews": 40, "window_pageviews": 175},
        )
    ]


def test_yesterdays_row_rewritten_unchanged_is_no_correction(tmp_path: Path) -> None:
    scenario = replace(port.scenario(NAME, "week_of_traffic"), seed={FILE: YESTERDAY_ROW})

    done = run(scenario, tmp_path)

    assert done.rows == {FILE: YESTERDAY_ROW}
    assert done.ctx.corrections == []


def test_an_error_answer_changes_no_past_day_and_is_recoverable(tmp_path: Path) -> None:
    """Only a successful re-read heals or corrects; an error, such as Cloudflare refusing the 30-day range, waits."""
    seed = cf.stored(cf.day(0), 1, 1)  # the days after it missing, but the answer is an error
    refused = cf.answer({"data": None, "errors": [{"message": "the time range is too large"}]})
    scenario = replace(port.scenario(NAME, "graphql_errors_in_a_success"), responses=[refused], seed={FILE: seed})

    done = run(scenario, tmp_path)

    assert done.rows[FILE] == seed + (
        '{"date": "2026-09-22", "site": "example", "status": "graphql_error", '
        f'"reason": "the time range is too large", "captured_at": "{NOW}"}}\n'
    )
    assert (done.status, done.ctx.critical, done.ctx.corrections) == (0, [], [])


# --- more data is captured than the script did: the week's top countries and devices ---


def differences(name: str, folder: Path) -> list[str]:
    """How the rows differ from the script's, but for the `top_countries` and `top_devices` leadline adds."""
    added = ("top_countries: only in B", "top_devices: only in B")
    return [d for d in port.golden_differences(NAME, name, folder) if not d.endswith(added)]


@pytest.mark.parametrize("name", cf.MORE_DATA_SCENARIOS)
def test_every_row_the_script_writes_is_the_same_with_the_lists_added(tmp_path: Path, name: str) -> None:
    done = run(port.scenario(NAME, name), tmp_path)

    last = json.loads(done.rows[FILE].splitlines()[-1])
    assert differences(name, tmp_path) == []
    assert (last["top_countries"], last["top_devices"]) == (cf.TOP_COUNTRIES, cf.TOP_DEVICES)
    assert list(last)[-4:] == ["top_referrers", "top_countries", "top_devices", "captured_at"]
    assert (done.status, done.ctx.critical) == (0, [])


def fresh(tmp_path: Path, countries: Any = cf.COUNTRIES, devices: Any = cf.DEVICES) -> Any:
    """A new file's run of the week of traffic, given these list answers."""
    scenario = replace(port.scenario(NAME, "week_of_traffic"), seed={})
    return run(replace(scenario, responses=[*scenario.responses, *cf.lists(countries, devices)]), tmp_path, lists=False)


def partial(reason: str, *, countries: bool = False, devices: bool = False) -> str:
    """The week of traffic's row with these lists null: a partial row (ADR: a partly failed row keeps good parts)."""
    row = json.loads(YESTERDAY_ROW)
    captured_at = row.pop("captured_at")
    row.update({"top_countries": None} if countries else {}, **({"top_devices": None} if devices else {}))
    return json.dumps({**row, "status": "partial", "reason": reason, "captured_at": captured_at}) + "\n"


def three(dimension: str, status: int = 500) -> list[Any]:
    """The list answers for `dimension` when every attempt fails."""
    return [cf.list_answer(dimension, "", status=status)] * 3


def test_the_lists_are_the_ten_largest_whatever_order_the_api_gives_and_a_tie_goes_to_the_name(tmp_path: Path) -> None:
    groups = [rum_group("countryName", f"C{n:02d}", 10 + n % 3) for n in range(10)]

    done = fresh(tmp_path, rum_list(groups))

    top = json.loads(done.rows[FILE])["top_countries"]
    assert top == sorted(top, key=lambda item: (-item["count"], item["name"]))
    assert [item["name"] for item in top][:4] == ["C02", "C05", "C08", "C01"]


def test_a_site_with_no_page_loads_has_empty_lists(tmp_path: Path) -> None:
    done = fresh(tmp_path, rum_list([]), rum_list([]))

    row = json.loads(done.rows[FILE])
    assert (row["top_countries"], row["top_devices"]) == ([], [])


def test_a_value_the_api_leaves_out_is_unknown(tmp_path: Path) -> None:
    done = fresh(tmp_path, rum_list([rum_group("countryName", None, 4), rum_group("countryName", "FR", 1)]))

    assert json.loads(done.rows[FILE])["top_countries"] == [
        {"name": "(unknown)", "count": 4},
        {"name": "FR", "count": 1},
    ]


def test_a_list_request_that_fails_leaves_that_list_null_in_a_partial_row(tmp_path: Path) -> None:
    scenario = replace(port.scenario(NAME, "week_of_traffic"), seed={})
    responses = [*scenario.responses, *three("countryName"), *cf.lists(None, cf.DEVICES)]

    done = run(replace(scenario, responses=responses), tmp_path, lists=False)

    assert done.rows == {FILE: partial("top_countries request failed (HTTP 500)", countries=True)}
    assert "WARN: Cloudflare top_countries request failed (HTTP 500)" in done.log
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_list_graphql_refuses_is_named_in_the_partial_row(tmp_path: Path) -> None:
    refused = {"data": None, "errors": [{"message": "unknown field deviceType"}]}
    done = fresh(tmp_path, cf.COUNTRIES, refused)

    assert done.rows == {FILE: partial("top_devices query failed: unknown field deviceType", devices=True)}
    assert (done.status, done.ctx.critical) == (0, [])


@pytest.mark.parametrize(
    "body",
    [
        {"data": {"viewer": {"accounts": [{"list": {"count": 1}}]}}, "errors": None},
        {
            "data": {"viewer": {"accounts": [{"list": [{"count": "5", "dimensions": {"deviceType": "x"}}]}]}},
            "errors": None,
        },
        {"data": {"viewer": {"accounts": [{"list": [{"count": 5, "dimensions": "x"}]}]}}, "errors": None},
        {"data": {"viewer": {"accounts": [{"list": [{"count": 5, "dimensions": {"deviceType": 7}}]}]}}, "errors": None},
        {"data": {"viewer": None}, "errors": None},
    ],
    ids=["not a list", "a count not a number", "dimensions not an object", "a name not text", "no viewer"],
)
def test_a_list_it_cant_read_is_a_partial_row_not_an_empty_list(tmp_path: Path, body: Any) -> None:
    done = fresh(tmp_path, cf.COUNTRIES, body)

    assert done.rows == {FILE: partial("top_devices answer was unreadable", devices=True)}


def test_both_lists_failing_are_one_partial_row_with_both_reasons(tmp_path: Path) -> None:
    scenario = replace(port.scenario(NAME, "week_of_traffic"), seed={})
    responses = [*scenario.responses, *three("countryName"), *three("deviceType", 502)]

    done = run(replace(scenario, responses=responses), tmp_path, lists=False)

    row = json.loads(done.rows[FILE])
    assert (row["status"], row["top_countries"], row["top_devices"]) == ("partial", None, None)
    assert row["reason"] == "top_countries request failed (HTTP 500); top_devices request failed (HTTP 502)"


def test_a_failed_list_keeps_the_list_an_earlier_run_got(tmp_path: Path) -> None:
    """ADR: a run never erases what it didn't get."""
    fresh(tmp_path / "first")
    scenario = replace(port.scenario(NAME, "week_of_traffic"), seed={FILE: (tmp_path / "first" / FILE).read_text()})
    responses = [*scenario.responses, *three("deviceType"), *cf.lists(cf.COUNTRIES, None)]

    done = run(replace(scenario, responses=responses), tmp_path / "second", lists=False)

    assert json.loads(done.rows[FILE])["top_devices"] == cf.TOP_DEVICES
    assert json.loads(done.rows[FILE])["status"] == "ok"


def test_lists_the_token_may_not_read_are_critical_and_a_rate_limit_is_not(tmp_path: Path) -> None:
    scenario = replace(port.scenario(NAME, "week_of_traffic"), seed={})
    denied = [cf.list_answer("countryName", '{"success": false}', status=403), *cf.lists(None, cf.DEVICES)]
    done = run(replace(scenario, responses=[*scenario.responses, *denied]), tmp_path / "denied", lists=False)

    assert done.ctx.critical == [Critical(NAME, FILE, "example: HTTP 403 from Cloudflare's GraphQL API")]
    assert done.status == 1
    limited = exchange(
        CLOUDFLARE_GRAPHQL,
        "",
        method="POST",
        sent=cf.list_answer("countryName", "")["request"]["body"],
        status=403,
        headers={"X-RateLimit-Remaining": "0"},
    )
    waiting = [limited] * 3 + cf.lists(None, cf.DEVICES)
    done = run(replace(scenario, responses=[*scenario.responses, *waiting]), tmp_path / "limited", lists=False)

    assert (done.status, done.ctx.critical) == (0, [])


# --- errors waiting can't fix ---


@pytest.mark.parametrize("code", [401, 403])
def test_rejected_credentials_are_not_retried_and_are_critical_at_once(tmp_path: Path, code: int) -> None:
    scenario = replace(port.scenario(NAME, "quiet_week"), responses=[cf.answer('{"success": false}', status=code)])

    done = run(scenario, tmp_path, lists=False)

    assert done.rows[FILE] == (
        f'{{"date": "2026-09-22", "site": "example", "status": "fetch_failed", "http_code": "{code}", '
        f'"reason": "graphql request failed", "captured_at": "{NOW}"}}\n'
    )
    assert done.sleeps == []
    assert done.ctx.critical == [Critical(NAME, FILE, f"example: HTTP {code} from Cloudflare's GraphQL API")]
    assert done.status == 1


def test_a_failure_that_waiting_can_fix_is_not_critical(tmp_path: Path) -> None:
    done = run(port.scenario(NAME, "three_failed_attempts_record_the_last_status"), tmp_path)

    assert (done.status, done.ctx.critical) == (0, [])


def test_a_rate_limit_is_retried_like_any_recoverable_failure_and_is_not_critical(tmp_path: Path) -> None:
    """A 403 that says its limit is spent is one waiting fixes (http: HttpError.rate_limited)."""
    limited = exchange(
        CLOUDFLARE_GRAPHQL, "", method="POST", sent=cf.SENT, status=403, headers={"X-RateLimit-Remaining": "0"}
    )
    scenario = replace(port.scenario(NAME, "quiet_week"), responses=[limited, limited, limited])

    done = run(scenario, tmp_path, lists=False)

    assert '"status": "fetch_failed", "http_code": "403"' in done.rows[FILE]
    assert done.sleeps == [10, 20]
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_failing_entry_does_not_stop_the_next(tmp_path: Path) -> None:
    other = {"label": "other", "site_tag": cf.SITE, "file": "cloudflare-{label}.jsonl"}
    responses = [cf.failure(), cf.failure(), cf.failure(), cf.answer(cf.WEEK)]
    scenario = replace(port.scenario(NAME, "quiet_week"), responses=responses)

    done = run(scenario, tmp_path, [*CONFIG, other], lists=True)

    assert '"status": "fetch_failed"' in done.rows[FILE]
    assert (
        '"site": "other", "site_tag": "site0tag0example", "status": "ok", "pageviews": 40'
        in done.rows["cloudflare-other.jsonl"]
    )
    assert done.status == 0


# --- check() ---

SITES_URL = f"{CLOUDFLARE_API}/accounts/{scenarios.CREDENTIALS['CLOUDFLARE_ACCOUNT_ID']}/rum/site_info/list"
TAG, TOKEN = "0123456789abcdef0123456789abcdef", "fedcba9876543210fedcba9876543210"


def check(tmp_path: Path, cfg: Any, *responses: Any) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, cloudflare_web.ENV):
        findings = cloudflare_web.check(ctx, cfg)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


def entry(site_tag: str, label: str = "example") -> dict[str, str]:
    return {"label": label, "site_tag": site_tag}


def page(n: int, sites: list[Any], total: int) -> Any:
    return exchange(f"{SITES_URL}?page={n}", rum_sites(sites, page=n, total=total))


def test_check_finds_the_site_tag(tmp_path: Path) -> None:
    findings = check(tmp_path, [entry(TAG)], page(1, [rum_site("a" * 32, "b" * 32), rum_site(TAG, TOKEN)], 2))

    assert findings == [Finding("ok", f"example: site tag {TAG} is a Web Analytics site in the account")]


def test_check_tells_a_beacon_token_from_the_site_tag(tmp_path: Path) -> None:
    findings = check(tmp_path, [entry(TOKEN)], page(1, [rum_site(TAG, TOKEN)], 1))

    assert findings == [
        Finding("error", f"example: the configured site_tag {TOKEN} is the site's beacon token; its site tag is {TAG}")
    ]


def test_check_finds_an_unknown_site_tag(tmp_path: Path) -> None:
    findings = check(tmp_path, [entry("nope")], page(1, [rum_site(TAG, TOKEN)], 1))

    assert findings == [
        Finding(
            "error",
            "example: unknown site tag nope: "
            "no Web Analytics site in the account has it as its site tag or beacon token",
        )
    ]


def test_check_reads_every_page_of_sites(tmp_path: Path) -> None:
    findings = check(
        tmp_path,
        [entry(TAG, "a"), entry(TOKEN, "b")],
        page(1, [rum_site("c" * 32, "d" * 32)], 2),
        page(2, [rum_site(TAG, TOKEN)], 2),
    )

    assert [f.level for f in findings] == ["ok", "error"]


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (
            exchange(f"{SITES_URL}?page=1", "", status=429),
            Finding("warn", "example: could not list the account's Web Analytics sites: HTTP 429"),
        ),
        (
            failed(f"{SITES_URL}?page=1"),
            Finding("warn", "example: could not list the account's Web Analytics sites: HTTP 0"),
        ),
        (
            exchange(f"{SITES_URL}?page=1", "", status=403),
            Finding("error", "example: could not list the account's Web Analytics sites: HTTP 403"),
        ),
        (
            exchange(f"{SITES_URL}?page=1", "<html>"),
            Finding("warn", "example: could not read the account's Web Analytics sites: not JSON"),
        ),
        (
            exchange(
                f"{SITES_URL}?page=1",
                {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}], "result": None},
            ),
            Finding("warn", "example: could not read the account's Web Analytics sites: unexpected answer"),
        ),
    ],
    ids=["rate limited", "no connection", "credentials rejected", "not JSON", "an unsuccessful answer"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(tmp_path: Path, response: Any, finding: Finding) -> None:
    assert check(tmp_path, [entry(TAG)], response) == [finding]


# --- the summary ---


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(cloudflare_web, CONFIG, "a_whole_month_read_as_it_is_changes_only_yesterday", tmp_path) == [
        "",
        "### cloudflare_web: example",
        "",
        "- Page views: 322 in 7d (+49) · 1035 in 30d",
        "- Visits: 45 in 7d (+1) · 193 in 30d",
    ]
