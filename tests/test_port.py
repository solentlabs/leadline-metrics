"""The port-test helper (tests/port.py): saved responses served as the harness serves them, a frozen clock."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import port
import pytest
import scenarios
import scratch_package
from scenarios import Scenario
from scenarios.builders.common import exchange, failed

from leadline import http, store
from leadline.config import Section
from leadline.context import Context, Finding
from leadline.http import HttpError

URL = "https://example.test/n"


@dataclass
class Fetcher:
    """A stand-in source: fetches URL `times` times, keeping the last answer, and writes it as a row.

    Only a failed GET, or a POST, is sent again: a run reuses a GET's successful answer (http.Http).
    """

    NAME: str = "fetcher"
    DEFAULT_FILE: str = "fetcher.jsonl"
    ENV: tuple[str, ...] = ("DUMMY_TOKEN",)
    LAGGING: bool = False
    times: int = 1
    method: str = "GET"
    seen: list[Any] = field(default_factory=list)

    def validate(self, cfg: Any) -> list[str]:
        return []

    def files(self, cfg: Any) -> dict[str, str]:
        return {"": "fetcher.jsonl"}

    def collect(self, ctx: Context, cfg: Any) -> None:
        self.seen.append((ctx.now, os.environ.get("DUMMY_TOKEN"), os.environ.get("GITHUB_TOKEN")))
        for _ in range(self.times):
            got = ctx.http.request(URL, method=self.method, attempts=2).json()
        store.write_good(ctx.out_dir / "fetcher.jsonl", {"date": ctx.date.isoformat(), "got": got}, ctx, self.NAME)

    def check(self, ctx: Context, cfg: Any) -> list[Finding]:
        return []

    def summary(self, cfg: Any) -> list[Section]:
        return []


SCENARIO = Scenario(
    "fetches",
    ["collect_fetcher"],
    [exchange(URL, {"n": 1})],
    env={"DUMMY_TOKEN": "dummy"},
    seed={"other.jsonl": "not a row\n"},
    now="2026-09-23T23:31:05",
    tz="Asia/Kolkata",
)


def test_runs_the_source_on_the_scenario_through_the_runner(tmp_path: Path) -> None:
    source = Fetcher()

    done = port.run(source, {}, SCENARIO, tmp_path)

    assert done.status == 0
    assert done.rows == {
        "fetcher.jsonl": '{"date": "2026-09-23", "got": {"n": 1}, "captured_at": "2026-09-23T23:31:05+05:30"}\n',
        "other.jsonl": "not a row\n",
    }
    assert source.seen == [(done.ctx.now, "dummy", None)]  # only the scenario's credentials
    assert "Running fetcher" in done.log


def test_responses_for_one_request_are_served_once_each_in_order(tmp_path: Path) -> None:
    served = [
        exchange(URL, "", status=503, method="POST"),
        exchange(URL, {"n": 1}, method="POST"),
        failed(URL, method="POST"),
        exchange(URL, {"n": 2}, method="POST"),
    ]

    done = port.run(Fetcher(times=2, method="POST"), {}, replace(SCENARIO, responses=served), tmp_path)

    assert '"got": {"n": 2}' in done.rows["fetcher.jsonl"]
    assert done.sleeps == [10, 10]


@pytest.mark.parametrize(
    ("responses", "method", "times", "refusal"),
    [
        ([], "GET", 1, "no saved response: GET https://example.test/n"),
        ([exchange(URL, 1), exchange(URL, 2)], "GET", 1, "never served: GET https://example.test/n"),
        ([exchange(URL, 1, method="POST")], "POST", 2, "no saved response: POST https://example.test/n"),
    ],
    ids=["a request with no saved response", "a response never served", "asked more often than answered"],
)
def test_a_run_that_strays_from_the_scenarios_requests_fails(
    tmp_path: Path, responses: list[Any], method: str, times: int, refusal: str
) -> None:
    with pytest.raises(AssertionError, match=refusal):
        port.run(Fetcher(times=times, method=method), {}, replace(SCENARIO, responses=responses), tmp_path)


def test_a_saved_failure_is_an_http_error_with_the_scripts_code() -> None:
    """A failed connection reads as the real transport gives it: the text of urllib's URLError."""
    transport = http.SavedResponses([failed(URL, "refused"), exchange(URL, "busy", status=429)])
    ctx = port.context(Path(), transport, port.local_time("2026-09-23T00:00:00", "UTC"), [])

    with pytest.raises(HttpError) as refused:
        ctx.http.get_json(URL)
    with pytest.raises(HttpError) as busy:
        ctx.http.get_json(URL)

    assert (refused.value.code, refused.value.reason) == (0, "<urlopen error refused>")
    assert (busy.value.code, busy.value.reason) == (429, "busy")


def test_the_credential_skip_is_the_runners(tmp_path: Path) -> None:
    source = Fetcher()

    done = port.run(source, {}, replace(SCENARIO, env={}, responses=[]), tmp_path)

    assert source.seen == []
    assert "SKIP: fetcher (DUMMY_TOKEN not set)" in done.log


def test_the_clock_is_the_scenarios_local_time_with_its_offset() -> None:
    assert port.local_time("2026-01-15T12:00:00", "America/New_York").isoformat() == "2026-01-15T12:00:00-05:00"
    assert port.local_time("2026-07-15T12:00:00", "America/New_York").isoformat() == "2026-07-15T12:00:00-04:00"


def test_a_utc_clock_needs_no_tzset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows has no time.tzset, so most port tests, whose zone is UTC, run there too."""
    monkeypatch.delattr(time, "tzset", raising=False)

    assert port.local_time("2026-09-23T23:31:05", "UTC").isoformat() == "2026-09-23T23:31:05+00:00"


def test_a_named_zone_without_tzset_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(time, "tzset", raising=False)

    with pytest.raises(pytest.skip.Exception, match="America/New_York needs time"):
        port.local_time("2026-01-15T12:00:00", "America/New_York")


def test_a_saved_failure_keeps_its_headers() -> None:
    """So a source can tell a rate limit from a rejection, as it does from the real transport."""
    transport = http.SavedResponses([exchange(URL, "limit", status=403, headers={"X-RateLimit-Remaining": "0"})])
    ctx = port.context(Path(), transport, port.local_time("2026-09-23T00:00:00", "UTC"), [])

    with pytest.raises(HttpError) as limited:
        ctx.http.get_json(URL)

    assert limited.value.headers == {"x-ratelimit-remaining": "0"}
    assert limited.value.rate_limited


def test_a_packages_divergences_and_golden_are_found_beside_its_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch_package.make(
        tmp_path,
        monkeypatch,
        {
            "sources/x/tests/scenarios.py": (
                "from scenarios import Scenario\n"
                "SCENARIOS = [Scenario('kept', calls=[], expected={'a.jsonl': '1\\n'}),\n"
                "             Scenario('moved', calls=[], expected={'a.jsonl': '2\\n'})]\n"
                "DIVERGENCES = {'moved': 'a decision'}\n"
            )
        },
    )
    scenarios.write_golden("x")
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.jsonl").write_text("1\n", encoding="utf-8")

    assert port.divergences("x") == {"moved": "a decision"}
    assert [s.name for s in port.golden_scenarios("x")] == ["kept"]
    assert port.scenario("x", "moved").name == "moved"
    assert port.golden_differences("x", "kept", out) == []
