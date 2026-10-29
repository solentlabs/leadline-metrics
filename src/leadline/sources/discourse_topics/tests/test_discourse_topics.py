"""discourse_topics: the script's rows on its scenarios, and leadline's side of each decided divergence.

The rows are compared with the goldens through tests/port.py; the scenarios where leadline differs by
a decision are listed in scenarios.py (DIVERGENCES) and asserted here instead.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import port
import pytest
import scenarios
from scenarios.builders.common import exchange, failed, retried, thrice

from leadline.context import Critical, Finding
from leadline.http import SavedResponses
from leadline.sources import discourse_topics as source
from leadline.sources.discourse_topics.tests import scenarios as dt
from leadline.sources.discourse_topics.tests.builders import discourse_topic

NAME = "discourse_topics"
FILE = dt.FILE
BASE = dt.BASE_URL
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it
ONE = {"FORUM_THREADS": ["1001:main"]}
MAIN = [{"id": 1001, "label": "main"}]
ADDED = ("participants", "closed", "solved", "tags")  # the fields leadline adds (not in the script's rows)
BUILT = {"participants": 3, "closed": False, "solved": False, "tags": ["example"]}  # what the builder's topic gives


def with_added(text: str, **changes: Any) -> str:
    """The script's rows in `text` with the four added fields after `last_post`, as the builder's topic gives
    them unless `changes` say otherwise."""
    out = []
    for line in text.splitlines():
        row = json.loads(line)
        if "last_post" in row:
            row = {k: v for key, value in row.items() for k, v in [(key, value), *_after(key)]}
            row.update(changes)
        out.append(json.dumps(row) + "\n")
    return "".join(out)


def _after(key: str) -> list[tuple[str, Any]]:
    return list(BUILT.items()) if key == "last_post" else []


def without_added(text: str) -> str:
    """The rows in `text` without the added fields; a line that isn't a row stays as it is."""
    out = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        out.append(json.dumps({k: v for k, v in row.items() if k not in ADDED}) if isinstance(row, dict) else line)
    return "".join(f"{line}\n" for line in out)


GOOD = with_added(
    '{"date": "2026-09-23", "topic_id": 1001, "label": "main", "views": 5400, "replies": 60, "likes": 88, '
    f'"last_post": "2026-09-20", "captured_at": "{NOW}"}}\n'
)  # topic 1001 of two_threads, as the script writes it, with leadline's added fields


def config(topics: list[dict[str, Any]] = MAIN, **entry: Any) -> list[dict[str, Any]]:
    return [{"base_url": BASE, "topics": topics, "file": FILE, **entry}]


def failure_row(topic_id: int, label: str, status: str, reason: str, http_code: str | None = None) -> str:
    code = f', "http_code": "{http_code}"' if http_code is not None else ""
    return (
        f'{{"date": "2026-09-23", "topic_id": {topic_id}, "label": "{label}", "status": "{status}"{code}, '
        f'"reason": "{reason}", "captured_at": "{NOW}"}}\n'
    )


def run(tmp_path: Path, *responses: Any, seed: str = "", cfg: Any = None) -> port.PortRun:
    """A run of topic 1001 ("main") on `responses`."""
    scenario = replace(
        port.scenario(NAME, "two_threads"), responses=list(responses), config=ONE, seed={FILE: seed} if seed else {}
    )
    return port.run(source, cfg or config(), scenario, tmp_path)


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert source.NAME == NAME
    assert source.ENV == ()  # a public forum: no credentials
    assert source.LAGGING is False


def test_a_valid_config_has_no_problems() -> None:
    cfg = [
        {"base_url": "https://forum.example", "topics": [{"id": 1, "label": "a"}, {"id": 2, "label": "b"}]},
        {"base_url": "http://other.example/forum/", "topics": [{"id": 1, "label": "a"}], "file": "other.jsonl"},
    ]

    assert source.validate(cfg) == []


BASE_URL = "base_url: must be an http(s) URL"
TOPICS = "topics: must be a non-empty list of topics, each an id and a label"


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"base_url": BASE}, [": must be a list of entries"]),
        ([BASE], ["[0]: must be an object"]),
        ([{"topics": MAIN}], [f"[0].{BASE_URL}"]),
        ([{"base_url": "", "topics": MAIN}], [f"[0].{BASE_URL}"]),
        ([{"base_url": "forum.example", "topics": MAIN}], [f"[0].{BASE_URL}"]),
        ([{"base_url": "https://", "topics": MAIN}], [f"[0].{BASE_URL}"]),
        ([{"base_url": BASE}], [f"[0].{TOPICS}"]),
        ([{"base_url": BASE, "topics": []}], [f"[0].{TOPICS}"]),
        ([{"base_url": BASE, "topics": {"id": 1, "label": "a"}}], [f"[0].{TOPICS}"]),
        ([{"base_url": BASE, "topics": [1001]}], ["[0].topics[0]: must be an object"]),
        ([{"base_url": BASE, "topics": [{"label": "a"}]}], ["[0].topics[0].id: must be a positive integer"]),
        ([{"base_url": BASE, "topics": [{"id": "1", "label": "a"}]}], ["[0].topics[0].id: must be a positive integer"]),
        ([{"base_url": BASE, "topics": [{"id": 0, "label": "a"}]}], ["[0].topics[0].id: must be a positive integer"]),
        (
            [{"base_url": BASE, "topics": [{"id": True, "label": "a"}]}],
            ["[0].topics[0].id: must be a positive integer"],
        ),
        ([{"base_url": BASE, "topics": [{"id": 1}]}], ["[0].topics[0].label: must be a non-empty string"]),
        ([{"base_url": BASE, "topics": [{"id": 1, "label": ""}]}], ["[0].topics[0].label: must be a non-empty string"]),
        (
            [{"base_url": BASE, "topics": [{"id": 1, "label": "a"}, {"id": 1, "label": "b"}]}],
            ["[0].topics[1].id: 1 is topics[0]'s too"],
        ),
        (
            [{"base_url": BASE, "topics": MAIN, "file": "forum-{label}.jsonl"}],
            ["[0].file: 'forum-{label}.jsonl' uses {label}, which is not one of: none"],
        ),
    ],
    ids=[
        "not a list",
        "an entry not an object",
        "no base_url",
        "an empty base_url",
        "a base_url without a scheme",
        "a base_url without a host",
        "no topics",
        "no topics listed",
        "topics an object",
        "a topic not an object",
        "no id",
        "an id a string",
        "an id zero",
        "an id a boolean",
        "no label",
        "an empty label",
        "an id twice",
        "a bad file",
    ],
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert source.validate(cfg) == problems


def test_a_topic_with_a_key_it_does_not_read_is_a_problem() -> None:
    cfg = [{"base_url": BASE, "topics": [{"id": 1, "label": "a", "bogus": 1}]}]

    assert source.validate(cfg) == ["[0].topics[0].bogus: not one of: id, label"]


def test_files_are_the_default_or_the_file_each_entry_names() -> None:
    cfg = [{"base_url": BASE, "topics": MAIN}, {"base_url": "https://f.example", "topics": MAIN, "file": "f.jsonl"}]

    assert source.files(cfg) == {"[0]": "discourse-topics.jsonl", "[1]": "f.jsonl"}


def test_it_needs_no_credentials(tmp_path: Path) -> None:
    scenario = replace(port.scenario(NAME, "two_threads"), env={})

    done = port.run(source, dt.leadline_config(scenario), scenario, tmp_path)

    (tmp_path / FILE).write_text(without_added(done.rows[FILE]), encoding="utf-8")
    assert port.golden_differences(NAME, "two_threads", tmp_path) == []
    assert not any(line.startswith("SKIP:") for line in done.log)


def test_a_base_url_with_a_trailing_slash_asks_for_the_same_topic(tmp_path: Path) -> None:
    done = run(tmp_path, dt.topic(1001, 5400, 61, 88), cfg=config(base_url=f"{BASE}/"))

    assert done.rows == {FILE: GOOD}


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    done = port.run(source, dt.leadline_config(scenario), scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


@pytest.mark.parametrize(
    "scenario",
    [s for s in scenarios.scenarios(NAME) if port.divergences(NAME).get(s.name) == dt.MORE_DATA],
    ids=lambda s: s.name,
)
def test_rows_are_the_scripts_with_the_added_fields(scenario: Any, tmp_path: Path) -> None:
    """More data is captured than the script did: but for the four fields, the rows are the script's."""
    done = port.run(source, dt.leadline_config(scenario), scenario, tmp_path)

    assert all(key in json.loads(done.rows[FILE].splitlines()[-1]) for key in ADDED)
    (tmp_path / FILE).write_text(without_added(done.rows[FILE]), encoding="utf-8")
    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


def written(tmp_path: Path, **changes: Any) -> dict[str, Any]:
    """The row topic 1001 gets from an answer: the builder's topic with `changes` (None removes a key)."""
    answer = {**discourse_topic(1001, views=5400, posts_count=61, like_count=88), **changes}
    answer = {k: v for k, v in answer.items() if not (k in changes and changes[k] is _ABSENT)}
    row: dict[str, Any] = json.loads(run(tmp_path, exchange(dt.topic_url(1001), answer)).rows[FILE].splitlines()[-1])
    return row


_ABSENT = object()


def test_the_row_adds_the_participants_closed_solved_and_tags_after_the_last_post(tmp_path: Path) -> None:
    row = written(tmp_path, participant_count=7, closed=True, has_accepted_answer=True, tags=["a", "b"])

    assert (row["participants"], row["closed"], row["solved"], row["tags"]) == (7, True, True, ["a", "b"])
    assert list(row)[:3] == ["date", "topic_id", "label"]
    assert list(row)[3:] == ["views", "replies", "likes", "last_post", *ADDED, "captured_at"]


def test_the_builders_topic_is_open_unsolved_with_its_tag(tmp_path: Path) -> None:
    row = written(tmp_path)

    assert {k: row[k] for k in ADDED} == BUILT


@pytest.mark.parametrize(
    ("changes", "solved"),
    [
        ({"has_accepted_answer": True}, True),
        ({"has_accepted_answer": False}, False),
        ({"has_accepted_answer": _ABSENT, "accepted_answer": {"post_number": 3}}, True),
        ({"has_accepted_answer": _ABSENT, "accepted_answer": None}, False),
        ({"has_accepted_answer": False, "accepted_answer": {"post_number": 3}}, True),
        ({"has_accepted_answer": _ABSENT}, None),
    ],
    ids=["has it", "has not", "an accepted answer", "a null accepted answer", "either", "neither key"],
)
def test_solved_is_true_for_an_accepted_answer_and_null_when_the_topic_cannot_say(
    tmp_path: Path, changes: Any, solved: bool | None
) -> None:
    assert written(tmp_path, **changes)["solved"] is solved


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"tags": [{"id": 1, "name": "x", "slug": "x"}, "y"]}, {"tags": ["x", "y"]}),
        ({"tags": []}, {"tags": []}),
        ({"tags": [{"id": 1}, 5, None, "z"]}, {"tags": ["z"]}),
        ({"tags": _ABSENT}, {"tags": None}),
        ({"tags": "x"}, {"tags": None}),
        ({"participant_count": _ABSENT}, {"participants": None}),
        ({"participant_count": "3"}, {"participants": None}),
        ({"participant_count": True}, {"participants": None}),
        ({"closed": _ABSENT}, {"closed": None}),
        ({"closed": "yes"}, {"closed": None}),
    ],
    ids=[
        "tags as objects and strings",
        "no tags",
        "tags it cannot name are left out",
        "tags absent",
        "tags not a list",
        "no participant count",
        "a participant count a string",
        "a participant count a boolean",
        "closed absent",
        "closed not a boolean",
    ],
)
def test_an_optional_key_it_cant_use_is_null_and_the_row_stays_good(
    tmp_path: Path, changes: Any, expected: dict[str, Any]
) -> None:
    row = written(tmp_path, **changes)

    assert {k: row[k] for k in expected} == expected
    assert "status" not in row
    assert row["views"] == 5400


def test_rows_rebuilt_for_past_days_are_not_given_the_added_fields(tmp_path: Path) -> None:
    """Only a night's own answer has them: the past days' posts can't tell who took part, or what was solved."""
    scenario = port.scenario(NAME, LATE_SCENARIO)

    done = port.run(source, dt.leadline_config(scenario), scenario, tmp_path)

    rebuilt = [json.loads(line) for line in done.rows[FILE].splitlines() if '"backfilled"' in line]
    assert len(rebuilt) == 3
    assert not any(key in row for row in rebuilt for key in ADDED)


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == [
        "a_rerun_replaces_only_the_same_thread_and_day",
        "failed_or_unparseable_threads_are_skipped",
        "missing_counts_read_as_zero",
        "two_threads",
    ]


def test_a_row_whose_topic_id_is_a_string_is_a_line_without_a_usable_key(tmp_path: Path) -> None:
    """ADR: store never fails on a line it can't use. It is kept, never replaced, and never holds a key.

    As in the script (a_rerun_replaces_only_the_same_thread_and_day), a rerun leaves it beside the
    topic's row; and it keeps no failure row out, since it isn't the topic's row.
    """
    stray = '{"date": "2026-09-23", "topic_id": "1001", "label": "main", "views": 5400}\n'

    done = run(tmp_path, *thrice(failed(dt.topic_url(1001))), seed=stray)

    assert done.rows == {
        FILE: stray
        + failure_row(1001, "main", "fetch_failed", "topic request failed (HTTP 000, connection refused)", "000")
    }


# --- ADR: a failure is recorded as a failure, per topic ---


def test_a_topic_without_its_counts_is_a_parse_failed_row_naming_them_not_zeros(tmp_path: Path) -> None:
    """The script reads views and likes it can't find as 0; the other topic is still written."""
    scenario = port.scenario(NAME, "missing_counts_read_as_zero")

    done = port.run(source, dt.leadline_config(scenario), scenario, tmp_path)

    good = with_added(scenarios.load_golden(NAME)[scenario.name]["rows"][FILE].splitlines(keepends=True)[-1])
    assert done.rows == {
        FILE: failure_row(1001, "main", "parse_failed", "topic response is missing views, like_count, posts_count")
        + good
    }


def test_each_topic_that_fails_writes_its_own_failure_row(tmp_path: Path) -> None:
    """The script skips each; leadline writes each its failure row, and the good topic its row."""
    scenario = port.scenario(NAME, "failed_or_unparseable_threads_are_skipped")
    scenario = replace(scenario, responses=retried(scenario.responses))

    done = port.run(source, dt.leadline_config(scenario), scenario, tmp_path)

    good = with_added(scenarios.load_golden(NAME)[scenario.name]["rows"][FILE])
    assert done.rows == {
        FILE: failure_row(1001, "main", "fetch_failed", "topic request failed (HTTP 000, connection refused)", "000")
        + failure_row(1002, "other-thread", "parse_failed", "topic response was not JSON")
        + good
        + failure_row(1004, "gone", "fetch_failed", "topic request failed (HTTP 404, JSON body)", "404")
        + failure_row(1005, "no-posts-count", "parse_failed", "topic response had an unexpected shape")
    }
    assert done.ctx.critical == [Critical(NAME, FILE, f"{BASE}: HTTP 404 for topic 1004 (gone); JSON body")]
    assert done.status == 1  # the topic doesn't exist: waiting can't fix it


TOPIC = discourse_topic(1001, views=5400, posts_count=61, like_count=88)


@pytest.mark.parametrize(
    "body",
    [
        [TOPIC],
        {**TOPIC, "views": None},
        {**TOPIC, "like_count": "88"},
        {**TOPIC, "posts_count": True},
        {**TOPIC, "last_posted_at": 20260920},
    ],
    ids=["a list", "null views", "likes a string", "posts a boolean", "the last post a number"],
)
def test_a_topic_it_cant_use_is_a_parse_failed_row(tmp_path: Path, body: Any) -> None:
    done = run(tmp_path, exchange(dt.topic_url(1001), body))

    assert done.rows == {FILE: failure_row(1001, "main", "parse_failed", "topic response had an unexpected shape")}
    assert (done.status, done.ctx.critical) == (0, [])


@pytest.mark.parametrize("code", [401, 403, 404])
def test_an_error_waiting_cant_fix_is_critical_at_once(tmp_path: Path, code: int) -> None:
    done = run(tmp_path, exchange(dt.topic_url(1001), "", status=code))

    assert done.rows == {
        FILE: failure_row(1001, "main", "fetch_failed", f"topic request failed (HTTP {code}, empty body)", str(code))
    }
    assert done.ctx.critical == [Critical(NAME, FILE, f"{BASE}: HTTP {code} for topic 1001 (main); empty body")]
    assert done.status == 1


CHALLENGE = "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body></body></html>"


def test_a_bot_challenge_is_recoverable_not_critical(tmp_path: Path) -> None:
    """Live 2026-10-21: the forum's host challenged the NAS; one failed night recovers (ADR)."""
    done = run(tmp_path, *thrice(exchange(dt.topic_url(1001), CHALLENGE, status=403)))

    assert done.rows == {
        FILE: failure_row(1001, "main", "fetch_failed", "topic request failed (HTTP 403, bot challenge)", "403")
    }
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_topic_request_is_retried_after_10_then_20_seconds(tmp_path: Path) -> None:
    """Live 2026-10-21 lost a night to one challenge; a snapshot can't be backfilled, so the request is retried."""
    url = dt.topic_url(1001)

    done = run(tmp_path, *[exchange(url, CHALLENGE, status=403)] * 2, dt.topic(1001, 5400, 61, 88))

    assert done.rows == {FILE: GOOD}
    assert done.sleeps == [10, 20]


def test_three_challenged_attempts_are_a_recoverable_failure_row(tmp_path: Path) -> None:
    url = dt.topic_url(1001)

    done = run(tmp_path, *thrice(exchange(url, CHALLENGE, status=403)))

    assert done.rows == {
        FILE: failure_row(1001, "main", "fetch_failed", "topic request failed (HTTP 403, bot challenge)", "403")
    }
    assert (done.status, done.ctx.critical, done.sleeps) == (0, [], [10, 20])


def test_a_rate_limit_is_recoverable(tmp_path: Path) -> None:
    done = run(tmp_path, *thrice(exchange(dt.topic_url(1001), "", status=429)))

    assert done.rows == {
        FILE: failure_row(1001, "main", "fetch_failed", "topic request failed (HTTP 429, rate limited)", "429")
    }
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_failure_keeps_the_topics_good_row(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data; the key is the date and the topic."""
    done = run(tmp_path, *thrice(failed(dt.topic_url(1001))), seed=GOOD)

    assert done.rows == {FILE: GOOD}
    assert f"WARN: kept the good row for 2026-09-23 topic_id=1001 in {FILE}; fetch_failed not recorded" in done.log


def test_a_good_row_replaces_the_topics_failure_row(tmp_path: Path) -> None:
    earlier = failure_row(1001, "main", "fetch_failed", "topic request failed (HTTP 503, empty body)", "503")

    done = run(tmp_path, dt.topic(1001, 5400, 61, 88), seed=earlier)

    assert done.rows == {FILE: GOOD}


def test_another_topics_good_row_keeps_no_failure_row_out(tmp_path: Path) -> None:
    other = GOOD.replace('"topic_id": 1001', '"topic_id": 1002')

    done = run(tmp_path, *thrice(failed(dt.topic_url(1001))), seed=other)

    assert done.rows == {
        FILE: other
        + failure_row(1001, "main", "fetch_failed", "topic request failed (HTTP 000, connection refused)", "000")
    }


# --- check() ---


def check(tmp_path: Path, cfg: Any, *responses: Any) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials({}, source.ENV):
        findings = source.check(ctx, cfg)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


TWO = [{"id": 1001, "label": "main"}, {"id": 1002, "label": "other-thread"}]


def test_check_finds_each_topic(tmp_path: Path) -> None:
    findings = check(tmp_path, config(TWO), dt.topic(1001, 5400, 61, 88), dt.topic(1002, 700, 5, 2))

    assert findings == [
        Finding("ok", f"{BASE}: topic 1001 (main) exists"),
        Finding("ok", f"{BASE}: topic 1002 (other-thread) exists"),
    ]


@pytest.mark.parametrize(
    ("responses", "finding"),
    [
        ([exchange(dt.topic_url(1001), "", status=404)], Finding("warn", f"{BASE}: no topic 1001 (main): HTTP 404")),
        (
            [exchange(dt.topic_url(1001), "", status=403)],
            Finding("error", f"{BASE}: could not read topic 1001 (main): HTTP 403"),
        ),
        (
            thrice(exchange(dt.topic_url(1001), "", status=429)),
            Finding("warn", f"{BASE}: could not read topic 1001 (main): HTTP 429"),
        ),
        (
            thrice(exchange(dt.topic_url(1001), CHALLENGE, status=403)),
            Finding("warn", f"{BASE}: could not read topic 1001 (main): HTTP 403"),
        ),
        (
            [exchange(dt.topic_url(1001), "<html>Oops</html>")],
            Finding("warn", f"{BASE}: could not read topic 1001 (main): topic response was not JSON"),
        ),
        (
            [exchange(dt.topic_url(1001), [1])],
            Finding("warn", f"{BASE}: could not read topic 1001 (main): topic response had an unexpected shape"),
        ),
    ],
    ids=["missing", "private", "rate limited", "bot challenge", "not JSON", "not a topic"],
)
def test_check_reports_each_topics_trouble_and_goes_on(tmp_path: Path, responses: list[Any], finding: Finding) -> None:
    findings = check(tmp_path, config(TWO), *responses, dt.topic(1002, 700, 5, 2))

    assert findings == [finding, Finding("ok", f"{BASE}: topic 1002 (other-thread) exists")]


def test_check_finds_a_forum_that_does_not_answer_once(tmp_path: Path) -> None:
    """No answer at all is the base_url's trouble, not each topic's: one finding, and no more requests."""
    findings = check(tmp_path, config(TWO), *thrice(failed(dt.topic_url(1001))))

    assert findings == [Finding("warn", f"{BASE}: the forum did not answer: HTTP 0")]


# --- the summary ---


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(source, dt.leadline_config(port.scenario(NAME, "two_threads")), "two_threads", tmp_path) == [
        "",
        "### discourse_topics: main",
        "",
        "- Views: 5400",
        "- Replies: 60",
        "",
        "### discourse_topics: other-thread",
        "",
        "- Views: 700",
        "- Replies: 4",
    ]


# --- rebuilt history (ADR: past values a source can rebuild are filled, never changed) ---

LATE_SCENARIO = "a_topic_starting_late_gets_rows_back_to_its_creation"


def test_a_topics_days_before_its_first_row_are_rebuilt_once_and_said_so(tmp_path: Path) -> None:
    """The scenario's reviewed rows hold the values; a second run finds nothing missing, so asks for no posts."""
    scenario = port.scenario(NAME, LATE_SCENARIO)
    cfg = dt.leadline_config(scenario)

    first = port.run(source, cfg, scenario, tmp_path / "first")
    again = port.run(
        source, cfg, replace(scenario, seed=first.rows, responses=scenario.responses[:1]), tmp_path / "next"
    )

    assert f"Backfilled {FILE}: 0 row(s) filled, 3 added, 2026-09-18 to 2026-09-20" in first.log
    assert first.ctx.corrections == []
    assert again.rows == first.rows
    assert not [line for line in again.log if line.startswith("Backfilled")]


def test_a_topic_whose_stream_misses_posts_it_counts_rebuilds_nothing(tmp_path: Path) -> None:
    """Its replies count posts the stream doesn't name, so a past day's count can't be told."""
    scenario = port.scenario(NAME, LATE_SCENARIO)
    answer = {**dt.history_topic(), "posts_count": 6}

    done = port.run(
        source,
        dt.leadline_config(scenario),
        replace(scenario, responses=[exchange(dt.topic_url(1001), answer)]),
        tmp_path,
    )

    assert '"backfilled"' not in done.rows[FILE]
    assert "Not rebuilding past replies of topic 1001: its stream holds 5 of its posts" in done.log
