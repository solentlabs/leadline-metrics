"""discourse_forum: the reviewed rows on its scenarios, and the behavior the scenarios can't show.

The source has no script behind it, so each scenario carries the rows it must leave (scenarios.py, `expected`), and
they are compared with the golden through tests/port.py.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import port
import pytest
import scenarios
from scenarios.builders.common import exchange, failed, thrice

from leadline.context import Critical, Finding
from leadline.http import SavedResponses
from leadline.sources import discourse_forum as source
from leadline.sources.discourse_forum.tests import scenarios as df
from leadline.sources.discourse_forum.tests.builders import discourse_about

NAME = "discourse_forum"
FILE = df.FILE
BASE = df.BASE_URL
URL = df.URL
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it
CHALLENGE = "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body></body></html>"
FIRST = "writes_the_days_site_statistics"


def run(tmp_path: Path, *responses: Any, seed: str = "", cfg: Any = None) -> port.PortRun:
    """A run of the one forum on `responses`."""
    scenario = replace(port.scenario(NAME, FIRST), responses=list(responses), seed={FILE: seed} if seed else {})
    return port.run(source, cfg or df.LEADLINE_CONFIG, scenario, tmp_path)


def rows(done: port.PortRun) -> list[dict[str, Any]]:
    return [json.loads(line) for line in done.rows[FILE].splitlines()]


def failure_row(status: str, reason: str, http_code: str | None = None) -> str:
    code = f', "http_code": "{http_code}"' if http_code is not None else ""
    return (
        f'{{"date": "2026-09-23", "base_url": "{BASE}", "status": "{status}"{code}, '
        f'"reason": "{reason}", "captured_at": "{NOW}"}}\n'
    )


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert source.NAME == NAME
    assert source.ENV == ()  # a public forum: no credentials
    assert source.LAGGING is False
    assert source.DEFAULT_FILE == "discourse-forum.jsonl"


def test_a_valid_config_has_no_problems() -> None:
    cfg = [{"base_url": "https://forum.example"}, {"base_url": "http://other.example/forum/", "file": "other.jsonl"}]

    assert source.validate(cfg) == []


BASE_URL = "base_url: must be an http(s) URL"


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"base_url": BASE}, [": must be a list of entries"]),
        ([BASE], ["[0]: must be an object"]),
        ([{}], [f"[0].{BASE_URL}"]),
        ([{"base_url": ""}], [f"[0].{BASE_URL}"]),
        ([{"base_url": 5}], [f"[0].{BASE_URL}"]),
        ([{"base_url": "forum.example"}], [f"[0].{BASE_URL}"]),
        ([{"base_url": "https://"}], [f"[0].{BASE_URL}"]),
        ([{"base_url": BASE, "topics": []}], ["[0].topics: unknown key (the keys are base_url, file)"]),
        (
            [{"base_url": BASE, "file": "forum-{label}.jsonl"}],
            ["[0].file: 'forum-{label}.jsonl' uses {label}, which is not one of: none"],
        ),
    ],
    ids=[
        "not a list",
        "an entry not an object",
        "no base_url",
        "an empty base_url",
        "a base_url a number",
        "a base_url without a scheme",
        "a base_url without a host",
        "a key it does not read",
        "a bad file",
    ],
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert source.validate(cfg) == problems


def test_files_are_the_default_or_the_file_each_entry_names() -> None:
    cfg = [{"base_url": BASE}, {"base_url": "https://f.example", "file": "f.jsonl"}]

    assert source.files(cfg) == {"[0]": "discourse-forum.jsonl", "[1]": "f.jsonl"}


# --- the reviewed rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_reviewed_rows(scenario: Any, tmp_path: Path) -> None:
    done = port.run(source, df.LEADLINE_CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_row_is_the_date_the_base_url_and_each_integer_stat_in_the_forums_order(tmp_path: Path) -> None:
    (row,) = rows(run(tmp_path, df.about()))

    assert list(row)[:4] == ["date", "base_url", "topics_last_day", "topics_7_days"]
    assert list(row)[-1] == "captured_at"
    assert (row["users_count"], row["topics_count"], row["posts_count"]) == (45000, 100000, 600000)
    assert row["chat_messages_count"] == 9000


def test_a_stat_that_is_not_a_count_is_left_out(tmp_path: Path) -> None:
    about = discourse_about(users=45000, topics=100000, posts=600000, active_7_days=300)
    about["about"]["stats"].update(
        {"flag_count": True, "label": "many", "ratio": 0.5, "nothing": None, "list": [1], "negative_count": -3}
    )

    (row,) = rows(run(tmp_path, exchange(URL, about)))

    assert not {"flag_count", "label", "ratio", "nothing", "list", "negative_count"} & set(row)
    assert row["users_count"] == 45000


def test_a_base_url_with_a_trailing_slash_asks_for_the_same_file(tmp_path: Path) -> None:
    done = run(tmp_path, df.about(), cfg=[{"base_url": f"{BASE}/", "file": FILE}])

    assert rows(done)[0]["base_url"] == f"{BASE}/"
    assert (done.status, done.ctx.critical) == (0, [])


def test_it_needs_no_credentials(tmp_path: Path) -> None:
    scenario = replace(port.scenario(NAME, FIRST), env={})

    done = port.run(source, df.LEADLINE_CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, FIRST, tmp_path) == []
    assert not any(line.startswith("SKIP:") for line in done.log)


# --- ADR: a failure is recorded as a failure ---


def test_a_forum_that_does_not_answer_is_a_fetch_failed_row(tmp_path: Path) -> None:
    done = run(tmp_path, *thrice(failed(URL)))

    assert done.rows == {FILE: failure_row("fetch_failed", "about request failed", "000")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_failed_about_request_is_tried_up_to_three_times(tmp_path: Path) -> None:
    """ADR: a request is tried up to three times; the forum's one request gets the default."""
    done = run(tmp_path, failed(URL), exchange(URL, "", status=503), df.about())

    assert rows(done)[0]["users_count"] == 45000
    assert (done.status, done.ctx.critical, done.sleeps) == (0, [], [10, 20])

    failing = run(tmp_path / "failing", *thrice(failed(URL)))

    assert failing.sleeps == [10, 20]


@pytest.mark.parametrize("code", [401, 403, 404])
def test_an_error_waiting_cant_fix_is_critical_at_once(tmp_path: Path, code: int) -> None:
    done = run(tmp_path, exchange(URL, "", status=code))

    assert done.rows == {FILE: failure_row("fetch_failed", "about request failed", str(code))}
    assert done.ctx.critical == [Critical(NAME, FILE, f"{BASE}: HTTP {code} for about.json")]
    assert done.status == 1


@pytest.mark.parametrize("code", [429, 500])
def test_a_rate_limit_or_a_server_error_is_recoverable_not_critical(tmp_path: Path, code: int) -> None:
    done = run(tmp_path, *thrice(exchange(URL, "", status=code)))

    assert done.rows == {FILE: failure_row("fetch_failed", "about request failed", str(code))}
    assert (done.status, done.ctx.critical, done.sleeps) == (0, [], [10, 20])


def test_a_bot_challenge_is_recoverable_not_critical(tmp_path: Path) -> None:
    done = run(tmp_path, *thrice(exchange(URL, CHALLENGE, status=403)))

    assert done.rows == {FILE: failure_row("fetch_failed", "about request failed", "403")}
    assert (done.status, done.ctx.critical) == (0, [])


ABOUT = discourse_about(users=45000, topics=100000, posts=600000, active_7_days=300)
NO_INTEGERS = {"about": {"stats": {"flag": True, "label": "x"}}}


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("<html>Oops</html>", "about response was not JSON"),
        ([ABOUT], "about response had an unexpected shape"),
        ({"users": []}, "about response has no about object"),
        ({"about": [1]}, "about response has no about object"),
        (
            discourse_about(users=1, topics=1, posts=1, active_7_days=1, stats=False),
            "about response has no stats object",
        ),
        ({"about": {"stats": []}}, "about response has no stats object"),
        ({"about": {"stats": {}}}, "about response has no integer stats"),
        (NO_INTEGERS, "about response has no integer stats"),
    ],
    ids=[
        "not JSON",
        "a list",
        "no about",
        "about a list",
        "stats hidden",
        "stats a list",
        "empty stats",
        "no integers",
    ],
)
def test_an_answer_it_cant_use_is_a_parse_failed_row_naming_the_part(tmp_path: Path, body: Any, reason: str) -> None:
    done = run(tmp_path, exchange(URL, body))

    assert done.rows == {FILE: failure_row("parse_failed", reason)}
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_failure_keeps_the_days_good_row(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data; the key is the date."""
    good = scenarios.load_golden(NAME)[FIRST]["rows"][FILE]

    done = run(tmp_path, *thrice(failed(URL)), seed=good)

    assert done.rows == {FILE: good}
    assert f"WARN: kept the good row for 2026-09-23 in {FILE}; fetch_failed not recorded" in done.log


def test_a_good_row_replaces_the_days_failure_row(tmp_path: Path) -> None:
    earlier = failure_row("fetch_failed", "about request failed", "503")

    done = run(tmp_path, df.about(), seed=earlier)

    assert done.rows == {FILE: scenarios.load_golden(NAME)[FIRST]["rows"][FILE]}


def test_one_forums_failure_does_not_stop_the_next(tmp_path: Path) -> None:
    other = "https://other.example"
    cfg = [{"base_url": BASE, "file": FILE}, {"base_url": other, "file": "other.jsonl"}]

    done = run(tmp_path, *thrice(failed(URL)), exchange(f"{other}/about.json", ABOUT), cfg=cfg)

    assert done.rows[FILE] == failure_row("fetch_failed", "about request failed", "000")
    assert json.loads(done.rows["other.jsonl"])["base_url"] == other


# --- check() ---


def check(tmp_path: Path, cfg: Any, *responses: Any) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials({}, source.ENV):
        findings = source.check(ctx, cfg)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


def test_check_finds_the_forums_site_statistics(tmp_path: Path) -> None:
    findings = check(tmp_path, df.LEADLINE_CONFIG, df.about())

    assert findings == [Finding("ok", f"{BASE}: about.json gives 25 site statistics")]


@pytest.mark.parametrize(
    ("answers", "finding"),
    [
        (thrice(failed(URL)), Finding("warn", f"{BASE}: the forum did not answer: HTTP 0")),
        ([exchange(URL, "", status=404)], Finding("warn", f"{BASE}: no about.json: HTTP 404")),
        ([exchange(URL, "", status=403)], Finding("error", f"{BASE}: could not read about.json: HTTP 403")),
        (thrice(exchange(URL, CHALLENGE, status=403)), Finding("warn", f"{BASE}: could not read about.json: HTTP 403")),
        (
            [exchange(URL, "<html>Oops</html>")],
            Finding("warn", f"{BASE}: could not read about.json: about response was not JSON"),
        ),
        (
            [exchange(URL, {"about": {}})],
            Finding("warn", f"{BASE}: could not read about.json: about response has no stats object"),
        ),
    ],
    ids=["no answer", "missing", "private", "bot challenge", "not JSON", "no stats"],
)
def test_check_reports_each_forums_trouble(tmp_path: Path, answers: list[Any], finding: Finding) -> None:
    """A failure waiting can fix is tried three times, one waiting can't fix once."""
    assert check(tmp_path, df.LEADLINE_CONFIG, *answers) == [finding]


def test_check_goes_on_to_the_next_forum(tmp_path: Path) -> None:
    other = "https://other.example"
    cfg = [{"base_url": BASE}, {"base_url": other}]

    findings = check(tmp_path, cfg, exchange(URL, "", status=404), exchange(f"{other}/about.json", ABOUT))

    assert findings == [
        Finding("warn", f"{BASE}: no about.json: HTTP 404"),
        Finding("ok", f"{other}: about.json gives 25 site statistics"),
    ]


# --- the summary ---


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(source, df.LEADLINE_CONFIG, FIRST, tmp_path) == [
        "",
        f"### discourse_forum: {BASE}",
        "",
        "- Members: 45000",
        "- Topics: 100000",
        "- Posts: 600000",
        "- Active members, 7 days: 300",
    ]
