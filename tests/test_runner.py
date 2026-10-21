"""The runner, with fake sources standing in for the registry."""

from __future__ import annotations

import json
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator

import pytest

from leadline import health, runner, trends
from leadline.config import Config, ConfigError, Section, Shown
from leadline.context import LOG_FILE, Context, Critical, Finding, open_context
from leadline.http import Http, Response
from leadline.sources import Registry, Source


@dataclass
class FakeSource:
    NAME: str
    DEFAULT_FILE: str = "fake.jsonl"
    ENV: tuple[str, ...] = ()
    LAGGING: bool = False
    error: Exception | None = None
    critical: str | None = None  # a reason collect() reports as critical
    problems: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)  # what files() gives: path in the config -> file
    fetch: str | None = None  # a URL collect() GETs through ctx.http
    findings: list[Finding] = field(default_factory=list)  # what check() finds
    check_error: Exception | None = None
    calls: list[tuple[Context, Any]] = field(default_factory=list)
    validated: list[Any] = field(default_factory=list)
    checked: list[tuple[Context, Any]] = field(default_factory=list)
    events: list[str] | None = None  # shared with other sources, to see the order of the calls
    sections: list[Section] = field(default_factory=list)  # what summary() gives

    def validate(self, cfg: Any) -> list[str]:
        self.validated.append(cfg)
        return self.problems

    def files(self, cfg: Any) -> dict[str, str]:
        assert not self.problems, "files() is asked only of a valid config"
        return self.outputs

    def collect(self, ctx: Context, cfg: Any) -> None:
        if self.events is not None:
            self.events.append(f"collect {self.NAME}")
        self.calls.append((ctx, cfg))
        if self.fetch is not None:
            ctx.http.get_json(self.fetch)
        if self.critical is not None:
            ctx.record_critical(self.NAME, f"{self.NAME}.jsonl", self.critical)
        if self.error is not None:
            raise self.error

    def check(self, ctx: Context, cfg: Any) -> list[Finding]:
        if self.events is not None:
            self.events.append(f"check {self.NAME}")
        self.checked.append((ctx, cfg))
        if self.check_error is not None:
            raise self.check_error
        return self.findings

    def summary(self, cfg: Any) -> list[Section]:
        return self.sections


@dataclass
class RetryingSource(FakeSource):
    """A source that asks for a retry pass (PyPI does), by defining retry()."""

    retry_error: Exception | None = None
    retries: list[tuple[Context, Any]] = field(default_factory=list)

    def retry(self, ctx: Context, cfg: Any) -> None:
        if self.events is not None:
            self.events.append(f"retry {self.NAME}")
        self.retries.append((ctx, cfg))
        if self.retry_error is not None:
            raise self.retry_error


def reg(*fakes: FakeSource) -> Registry[Source]:
    """A registry of these sources, as discovery would give it."""
    return Registry({source.NAME: source for source in fakes})


def config(tmp_path: Path, reports: Any = (), **source_cfgs: Any) -> Config:
    return Config(
        out_dir=tmp_path,
        user_agent="Agent/1.0",
        sources=source_cfgs,
        config_hash="0123456789ab",
        reports=None if reports is None else list(reports),
    )


def log_text(tmp_path: Path) -> str:
    return (tmp_path / LOG_FILE).read_text(encoding="utf-8")


def health_row(tmp_path: Path) -> dict[str, Any]:
    """The run's row in health.jsonl."""
    row: dict[str, Any] = json.loads((tmp_path / health.FILE).read_text(encoding="utf-8").splitlines()[-1])
    return row


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()  # noqa: DTZ011 (the run date is local)


def test_the_run_uses_the_context_it_is_given(tmp_path: Path) -> None:
    """Tests give the run a context with a frozen clock and saved responses (tests/port.py)."""
    a = FakeSource("a")
    opened: list[Config] = []

    @contextmanager
    def given(cfg: Config) -> Iterator[Context]:
        opened.append(cfg)
        with open_context(cfg) as ctx:
            yield ctx

    cfg = config(tmp_path, a={})
    assert runner.run(cfg, registry=reg(a), context=given) == 0

    assert opened == [cfg]
    assert len(a.calls) == 1


def test_zero_sources_run_cleanly(tmp_path: Path) -> None:
    assert runner.run(config(tmp_path), registry=reg()) == 0

    assert "Sources: none" in log_text(tmp_path)
    assert "0 ran, 0 crashed, 0 skipped" in log_text(tmp_path)


def test_enabled_sources_run_in_config_order_with_their_config(tmp_path: Path) -> None:
    a, b, unused = FakeSource("a"), FakeSource("b"), FakeSource("unused")

    status = runner.run(config(tmp_path, b={"x": 1}, a=[2]), registry=reg(a, unused, b))

    assert status == 0
    assert [call[1] for call in b.calls + a.calls] == [{"x": 1}, [2]]
    assert unused.calls == []
    log = log_text(tmp_path)
    assert log.index("Running b") < log.index("Running a")


def test_all_sources_share_one_context(tmp_path: Path) -> None:
    a, b = FakeSource("a"), FakeSource("b")

    runner.run(config(tmp_path, a={}, b={}), registry=reg(a, b))

    assert a.calls[0][0] is b.calls[0][0]


def test_sources_asking_for_the_same_thing_in_one_run_make_one_request(tmp_path: Path) -> None:
    """The run's one Http keeps each successful GET's answer, so the second source's is reused."""
    sent: list[str] = []

    def transport(request: urllib.request.Request, timeout: float) -> Response:
        sent.append(request.full_url)
        return Response(200, {}, b"{}")

    @contextmanager
    def given(cfg: Config) -> Iterator[Context]:
        with open_context(cfg) as ctx:
            yield replace(ctx, http=Http(cfg.user_agent, transport))

    a, b = FakeSource("a", fetch="https://x.test/same"), FakeSource("b", fetch="https://x.test/same")

    assert runner.run(config(tmp_path, a={}, b={}), registry=reg(a, b), context=given) == 0

    assert sent == ["https://x.test/same"]
    assert "Reused the answer to GET https://x.test/same" in log_text(tmp_path)


def test_a_crashing_source_is_critical_and_does_not_stop_the_others(tmp_path: Path) -> None:
    """An unexpected exception is a bug, which waiting can't fix."""
    a, bad, c = FakeSource("a"), FakeSource("bad", error=RuntimeError("boom")), FakeSource("c")
    opened: list[Context] = []

    @contextmanager
    def given(cfg: Config) -> Iterator[Context]:
        with open_context(cfg) as ctx:
            opened.append(ctx)
            yield ctx

    status = runner.run(config(tmp_path, a={}, bad={}, c={}), registry=reg(a, bad, c), context=given)

    assert status == 1
    assert len(a.calls) == len(c.calls) == 1
    assert opened[0].critical == [Critical("bad", None, "crashed: RuntimeError: boom")]
    log = log_text(tmp_path)
    assert "ERROR: bad crashed: RuntimeError: boom" in log
    assert "Traceback" in log
    assert "ERROR: bad is critical: crashed: RuntimeError: boom" in log
    assert "2 ran, 1 crashed (bad), 0 skipped" in log


def test_a_critical_finding_exits_1_and_the_next_source_runs(tmp_path: Path) -> None:
    rejected, after = FakeSource("rejected", critical="HTTP 401"), FakeSource("after")

    status = runner.run(config(tmp_path, rejected={}, after={}), registry=reg(rejected, after))

    assert status == 1
    assert len(after.calls) == 1
    log = log_text(tmp_path)
    assert "ERROR: rejected is critical (rejected.jsonl): HTTP 401" in log
    assert "2 ran, 0 crashed, 0 skipped" in log


def test_the_retry_pass_follows_every_sources_collection_in_config_order(tmp_path: Path) -> None:
    """A source that defines retry() gets its second pass after all collection, as the script's PyPI pass."""
    events: list[str] = []
    a, plain, b = RetryingSource("a", events=events), FakeSource("plain"), RetryingSource("b", events=events)

    status = runner.run(config(tmp_path, b={"x": 1}, plain={}, a=[2]), registry=reg(a, plain, b))

    assert status == 0
    assert events == ["collect b", "collect a", "retry b", "retry a", "check b", "check a"]
    assert [call[1] for call in b.retries + a.retries] == [{"x": 1}, [2]]
    assert b.retries[0][0] is b.calls[0][0]  # the run's one context
    log = log_text(tmp_path)
    assert log.index("Retry pass: b") < log.index("Retry pass: a") < log.index("Done:")


def test_the_retry_pass_skips_a_source_that_was_skipped_or_crashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TOKEN_UNSET", raising=False)
    skipped = RetryingSource("skipped", ENV=("TOKEN_UNSET",))
    crashed = RetryingSource("crashed", error=RuntimeError("boom"))

    runner.run(config(tmp_path, skipped={}, crashed={}), registry=reg(skipped, crashed))

    assert skipped.retries == crashed.retries == []


def test_a_retry_pass_that_crashes_is_critical_and_the_next_one_runs(tmp_path: Path) -> None:
    bad, after = RetryingSource("bad", retry_error=RuntimeError("boom")), RetryingSource("after")
    opened: list[Context] = []

    @contextmanager
    def given(cfg: Config) -> Iterator[Context]:
        with open_context(cfg) as ctx:
            opened.append(ctx)
            yield ctx

    status = runner.run(config(tmp_path, bad={}, after={}), registry=reg(bad, after), context=given)

    assert status == 1
    assert len(after.retries) == 1
    assert opened[0].critical == [Critical("bad", None, "crashed in its retry pass: RuntimeError: boom")]
    log = log_text(tmp_path)
    assert "ERROR: bad crashed in its retry pass: RuntimeError: boom" in log
    assert "1 ran, 1 crashed (bad), 0 skipped" in log


def test_a_source_without_its_credentials_is_skipped_not_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOKEN_SET", "t")
    monkeypatch.setenv("TOKEN_EMPTY", "")
    monkeypatch.delenv("TOKEN_UNSET", raising=False)
    ready = FakeSource("ready", ENV=("TOKEN_SET",))
    blocked = FakeSource("blocked", ENV=("TOKEN_SET", "TOKEN_EMPTY", "TOKEN_UNSET"))

    status = runner.run(config(tmp_path, ready={}, blocked={}), registry=reg(ready, blocked))

    assert status == 0
    assert len(ready.calls) == 1
    assert blocked.calls == []
    log = log_text(tmp_path)
    assert "SKIP: blocked (TOKEN_EMPTY, TOKEN_UNSET not set)" in log
    assert "1 ran, 0 crashed, 1 skipped" in log


def test_only_runs_the_named_subset_in_config_order(tmp_path: Path) -> None:
    a, b, c = FakeSource("a"), FakeSource("b"), FakeSource("c")

    runner.run(config(tmp_path, a={}, b={}, c={}), only=["c", "a"], registry=reg(a, b, c))

    assert (len(a.calls), len(b.calls), len(c.calls)) == (1, 0, 1)
    log = log_text(tmp_path)
    assert log.index("Running a") < log.index("Running c")


def test_a_configured_source_that_is_not_registered_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown sources: nope"):
        runner.run(config(tmp_path, a={}, nope={}), registry=reg(FakeSource("a")))

    assert not (tmp_path / LOG_FILE).exists()


def test_only_naming_a_source_not_in_the_config_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="--only names sources not in the config: b"):
        runner.run(config(tmp_path, a={}), only=["b"], registry=reg(FakeSource("a"), FakeSource("b")))


def test_every_configured_source_is_validated_with_its_own_config_first(tmp_path: Path) -> None:
    a, b, unused = FakeSource("a"), FakeSource("b", ENV=("TOKEN_UNSET_FOR_THIS_TEST",)), FakeSource("unused")

    runner.run(config(tmp_path, a={"x": 1}, b=[{"y": 2}]), only=["a"], registry=reg(a, b, unused))

    assert (a.validated, b.validated, unused.validated) == ([{"x": 1}], [[{"y": 2}]], [])


def test_a_config_problem_in_any_source_stops_the_run_before_anything_is_written(tmp_path: Path) -> None:
    good = FakeSource("good")
    bad = FakeSource("bad", problems=["[1].repo: is required", "[2].file: must be a string, not 5"])
    worse = FakeSource("worse", problems=[".packages: must be a list"])
    out = tmp_path / "out"

    with pytest.raises(ConfigError) as raised:
        runner.run(
            Config(out, "Agent/1.0", {"good": {}, "bad": [], "worse": {}}),
            only=["good"],
            registry=reg(good, bad, worse),
        )

    assert str(raised.value).splitlines() == [
        "invalid source config:",
        "  sources.bad[1].repo: is required",
        "  sources.bad[2].file: must be a string, not 5",
        "  sources.worse.packages: must be a list",
    ]
    assert good.calls == []
    assert not out.exists()


@pytest.mark.parametrize(
    ("a", "b", "clash"),
    [
        (
            {"[0]": "same.jsonl", "[1]": "other.jsonl", "[2]": "same.jsonl"},
            {},
            "sources.a[0] and sources.a[2] write the same file: same.jsonl",
        ),
        (
            {"[0]": "a.jsonl", "[1]": "same.jsonl"},
            {".packages[0]": "b.jsonl", ".packages[1]": "same.jsonl"},
            "sources.a[1] and sources.b.packages[1] write the same file: same.jsonl",
        ),
        (
            # Case apart, a file system that ignores case (OneDrive's, on Windows) holds them as one.
            {"[0]": "Same.jsonl"},
            {"": "same.jsonl"},
            "sources.a[0] and sources.b write the same file: Same.jsonl, same.jsonl",
        ),
    ],
    ids=["within a source", "across sources", "names that differ only in case"],
)
def test_two_entries_writing_one_file_stop_the_run_before_anything_is_written(
    tmp_path: Path, a: dict[str, str], b: dict[str, str], clash: str
) -> None:
    out = tmp_path / "out"
    first, second = FakeSource("a", outputs=a), FakeSource("b", outputs=b)

    with pytest.raises(ConfigError) as raised:
        runner.run(Config(out, "Agent/1.0", {"a": [], "b": {}}), registry=reg(first, second))

    assert str(raised.value).splitlines() == ["invalid source config:", f"  {clash}"]
    assert first.calls == second.calls == []
    assert not out.exists()


def test_three_writers_of_one_file_are_named_together(tmp_path: Path) -> None:
    writers = FakeSource("a", outputs={"[0]": "x.jsonl", "[1]": "x.jsonl", "[2]": "x.jsonl"})

    with pytest.raises(ConfigError, match=r"sources.a\[0\], sources.a\[1\] and sources.a\[2\] write the same file"):
        runner.run(config(tmp_path, a=[]), registry=reg(writers))


def test_files_are_compared_only_once_every_config_is_valid(tmp_path: Path) -> None:
    bad = FakeSource("bad", problems=["[0].repo: is required"], outputs={"[0]": "x.jsonl"})
    good = FakeSource("good", outputs={"[0]": "x.jsonl"})

    with pytest.raises(ConfigError, match=r"sources.bad\[0\].repo: is required"):
        runner.run(config(tmp_path, bad=[], good=[]), registry=reg(bad, good))


def test_every_run_writes_its_health_row_last(tmp_path: Path) -> None:
    """Even with nothing to run: the row says what the folder holds, with the config's hash."""
    status = runner.run(config(tmp_path), registry=reg())

    assert status == 0
    row = health_row(tmp_path)
    assert (row["checked"], row["script_hash"], row["checks"], row["critical"]) == (0, "0123456789ab", [], [])
    assert log_text(tmp_path).index("Done:") < log_text(tmp_path).index("Checking metrics freshness...")


def test_checks_run_after_collection_and_the_retry_pass_for_each_source_not_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR: check() runs on every run, after collection. A skipped source has no credentials to check with."""
    monkeypatch.delenv("TOKEN_UNSET", raising=False)
    events: list[str] = []
    a = RetryingSource("a", events=events, findings=[Finding("ok", "the repo exists"), Finding("warn", "slow")])
    b = FakeSource("b", events=events, findings=[Finding("error", "no such site")])
    skipped = FakeSource("skipped", ENV=("TOKEN_UNSET",), events=events)

    status = runner.run(config(tmp_path, b={"x": 1}, skipped={}, a=[2]), registry=reg(a, b, skipped))

    assert status == 0  # a finding is never critical
    assert events == ["collect b", "collect a", "retry a", "check b", "check a"]
    assert [call[1] for call in b.checked + a.checked] == [{"x": 1}, [2]]
    assert b.checked[0][0] is b.calls[0][0]  # the run's one context
    assert health_row(tmp_path)["checks"] == [
        {"source": "b", "level": "error", "message": "no such site"},
        {"source": "a", "level": "ok", "message": "the repo exists"},
        {"source": "a", "level": "warn", "message": "slow"},
    ]
    log = log_text(tmp_path)
    assert "ERROR: check b: no such site" in log
    assert "Check a: the repo exists" in log
    assert "WARN: check a: slow" in log


def test_a_check_that_crashes_is_critical_and_the_next_check_runs(tmp_path: Path) -> None:
    bad = FakeSource("bad", check_error=RuntimeError("boom"))
    after = FakeSource("after", findings=[Finding("ok", "fine")])

    status = runner.run(config(tmp_path, bad={}, after={}), registry=reg(bad, after))

    assert status == 1
    assert len(after.checked) == 1
    row = health_row(tmp_path)
    assert row["critical"] == [{"source": "bad", "file": None, "reason": "crashed in its check: RuntimeError: boom"}]
    assert row["checks"] == [{"source": "after", "level": "ok", "message": "fine"}]
    assert "ERROR: bad crashed in its check: RuntimeError: boom" in log_text(tmp_path)


def test_a_source_with_no_good_row_two_nights_running_makes_the_run_exit_1(tmp_path: Path) -> None:
    """ADR: one failed night recovers; consecutive failures are critical."""
    (tmp_path / "a.jsonl").write_text(
        f'{{"date": "{days_ago(2)}"}}\n{{"date": "{days_ago(1)}", "status": "fetch_failed"}}\n', encoding="utf-8"
    )
    quiet = FakeSource("quiet", outputs={"[0]": "a.jsonl"})  # writes nothing tonight

    status = runner.run(config(tmp_path, quiet=[]), registry=reg(quiet))

    assert status == 1
    reason = f"no good row for 2 nights, since {days_ago(1)}"
    assert health_row(tmp_path)["critical"] == [{"source": "quiet", "file": "a.jsonl", "reason": reason}]
    assert f"ERROR: quiet is critical (a.jsonl): {reason}" in log_text(tmp_path)


def test_a_lagging_source_gets_a_day_more(tmp_path: Path) -> None:
    """Its expected date is yesterday (LAGGING), so a newest good row two days old is neither stale nor critical."""
    for name in ("lags.jsonl", "daily.jsonl"):
        (tmp_path / name).write_text(f'{{"date": "{days_ago(2)}"}}\n', encoding="utf-8")
    lags = FakeSource("lags", LAGGING=True, outputs={"": "lags.jsonl"})
    daily = FakeSource("daily", outputs={"": "daily.jsonl"})

    status = runner.run(config(tmp_path, lags={}, daily={}), registry=reg(lags, daily))

    assert status == 1
    row = health_row(tmp_path)
    assert [s["file"] for s in row["stale"]] == ["daily.jsonl"]
    assert [c["source"] for c in row["critical"]] == ["daily"]


def test_health_judges_every_configured_source_whatever_ran(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A source left out by --only, or skipped for its credentials, still has no data: it is critical all the same."""
    monkeypatch.delenv("TOKEN_UNSET", raising=False)
    for name in ("left-out.jsonl", "skipped.jsonl"):
        (tmp_path / name).write_text(f'{{"date": "{days_ago(3)}"}}\n', encoding="utf-8")
    left_out = FakeSource("left_out", outputs={"": "left-out.jsonl"})
    skipped = FakeSource("skipped", ENV=("TOKEN_UNSET",), outputs={"": "skipped.jsonl"})

    registry = reg(left_out, skipped, FakeSource("a"))

    status = runner.run(config(tmp_path, left_out={}, skipped={}, a={}), only=["skipped", "a"], registry=registry)

    assert status == 1
    assert [c["source"] for c in health_row(tmp_path)["critical"]] == ["left_out", "skipped"]
    assert left_out.checked == skipped.checked == []


CRITICAL_BLOCK = {
    "file": "report.md",
    "block": "critical",
    "place": "top",
    "heading": "Critical",
    "lines": [
        {
            "label": "Critical",
            "from": health.FILE,
            "field": "critical",
            "aggregate": "items",
            "item": "{source}: {reason}",
        }
    ],
}


def test_reports_are_written_after_health_and_read_this_runs_row(tmp_path: Path) -> None:
    """ADR: critical sources surface at the top of SUMMARY.md; the block is read from this run's health row."""
    bad = FakeSource("bad", error=RuntimeError("boom"))

    status = runner.run(config(tmp_path, reports=[CRITICAL_BLOCK], bad={}), registry=reg(bad))

    assert status == 1
    assert (tmp_path / "report.md").read_text(encoding="utf-8").splitlines() == [
        "<!-- leadline:start critical -->",
        "## Critical",
        "",
        "- Critical: bad: crashed: RuntimeError: boom",
        "<!-- leadline:end critical -->",
    ]
    log = log_text(tmp_path)
    assert log.index("Checking metrics freshness...") < log.index("Wrote report.md: critical")


def test_a_report_that_fails_makes_the_run_exit_1(tmp_path: Path) -> None:
    """A broken marker won't mend itself: critical, though health, written before, can't list it."""
    (tmp_path / "report.md").write_text("<!-- leadline:start critical -->\n", encoding="utf-8")

    status = runner.run(config(tmp_path, reports=[CRITICAL_BLOCK]), registry=reg())

    assert status == 1
    assert health_row(tmp_path)["critical"] == []
    assert "ERROR: reports is critical (report.md): block critical: its end marker" in log_text(tmp_path)


def test_a_report_config_problem_stops_the_run_before_anything_is_written(tmp_path: Path) -> None:
    out = tmp_path / "out"
    a = FakeSource("a")

    with pytest.raises(ConfigError) as raised:
        runner.run(config(out, reports=[{**CRITICAL_BLOCK, "place": "middle"}], a={}), registry=reg(a))

    assert str(raised.value).splitlines() == ["invalid report config:", "  reports[0].place: must be top or bottom"]
    assert a.calls == []
    assert not out.exists()


def section(name: str) -> list[Section]:
    """A source's sections: one, showing the value in its file."""
    return [Section(name, None, f"{name}.jsonl", [Shown("n", name)], "Label")]


def test_with_no_reports_the_run_writes_the_default_summary_each_sources_sections_in_config_order(
    tmp_path: Path,
) -> None:
    a, b = FakeSource("a", sections=section("a")), FakeSource("b", sections=section("b"))
    for name, n in (("a", 1), ("b", 2)):
        (tmp_path / f"{name}.jsonl").write_text(json.dumps({"date": "2026-09-24", "n": n}) + "\n", encoding="utf-8")

    status = runner.run(config(tmp_path, reports=None, b={}, a={}), registry=reg(a, b))

    lines = (tmp_path / "SUMMARY.md").read_text(encoding="utf-8").splitlines()
    assert status == 0
    assert lines[:4] == ["<!-- leadline:start summary -->", "## Summary", "", f"- Run: {health_row(tmp_path)['date']}"]
    assert lines[-9:] == ["", "### b", "", "- b: 2", "", "### a", "", "- a: 1", "<!-- leadline:end summary -->"]


def test_an_empty_reports_list_writes_no_report(tmp_path: Path) -> None:
    a = FakeSource("a", sections=section("a"))

    runner.run(config(tmp_path, reports=[], a={}), registry=reg(a))

    assert sorted(path.name for path in tmp_path.iterdir()) == sorted([LOG_FILE, health.FILE, trends.DATA, trends.PAGE])


def test_a_config_with_its_own_reports_writes_only_those_and_leaves_an_old_summary_as_it_is(tmp_path: Path) -> None:
    (tmp_path / "SUMMARY.md").write_text("old\n", encoding="utf-8")
    a = FakeSource("a", sections=section("a"))

    runner.run(config(tmp_path, reports=[CRITICAL_BLOCK], a={}), registry=reg(a))

    assert (tmp_path / "SUMMARY.md").read_text(encoding="utf-8") == "old\n"
    assert (tmp_path / "report.md").exists()


@pytest.mark.parametrize("reports_setting", [None, [], [CRITICAL_BLOCK]], ids=["default", "none", "own"])
def test_every_run_writes_trends_of_each_sources_sections_after_the_reports(
    tmp_path: Path, reports_setting: Any
) -> None:
    a, b = FakeSource("a", sections=section("a")), FakeSource("b", sections=section("b"))
    (tmp_path / "a.jsonl").write_text(json.dumps({"date": "2026-09-24", "n": 1}) + "\n", encoding="utf-8")

    status = runner.run(config(tmp_path, reports=reports_setting, b={}, a={}), registry=reg(a, b))

    data = json.loads((tmp_path / trends.DATA).read_text(encoding="utf-8"))
    assert status == 0
    assert [(s["title"], s["dates"]) for s in data["sections"]] == [("b", []), ("a", ["2026-09-24"])]
    assert data["health"]["date"] == health_row(tmp_path)["date"]
    assert (tmp_path / trends.PAGE).is_file()
    log = log_text(tmp_path)
    assert log.index("Checking metrics freshness...") < log.index("Wrote trends.json, trends.html")
    if reports_setting:
        assert log.index("Wrote report.md") < log.index("Wrote trends.json")


def test_trends_group_the_sections_by_the_configs_projects(tmp_path: Path) -> None:
    a, b = FakeSource("a", sections=section("a")), FakeSource("b", sections=section("b"))
    cfg = replace(config(tmp_path, a={}, b={}), projects=[{"name": "B", "sections": ["b"]}])

    runner.run(cfg, registry=reg(a, b))

    data = json.loads((tmp_path / trends.DATA).read_text(encoding="utf-8"))
    assert [(s["title"], s["project"]) for s in data["sections"]] == [("b", "B"), ("a", None)]


def test_a_projects_problem_stops_the_run_before_anything_is_written(tmp_path: Path) -> None:
    out = tmp_path / "out"
    a = FakeSource("a", sections=section("a"))
    projects = [{"name": "A", "sections": ["a"]}, {"name": "A", "sections": ["a", "nope"]}]

    with pytest.raises(ConfigError) as raised:
        runner.run(replace(config(out, a={}), projects=projects), registry=reg(a))

    assert str(raised.value).splitlines() == [
        "invalid projects config:",
        '  projects[1].name: "A" is also projects[0]\'s',
        '  projects[1].sections[0]: "a" is also in projects[0]',
        '  projects[1].sections[1]: "nope" names no section',
    ]
    assert a.calls == []
    assert not out.exists()


def test_trends_write_the_configs_pages(tmp_path: Path) -> None:
    a, b = FakeSource("a", sections=section("a")), FakeSource("b", sections=section("b"))
    projects = [{"name": "A", "sections": ["a"]}, {"name": "B", "sections": ["b"]}]
    pages = [{"file": "b.html", "title": "Bee", "projects": ["B"]}]

    runner.run(replace(config(tmp_path, a={}, b={}), projects=projects, pages=pages), registry=reg(a, b))

    assert (tmp_path / "b.html").is_file()
    assert not (tmp_path / trends.PAGE).exists()


def test_a_pages_problem_stops_the_run_before_anything_is_written(tmp_path: Path) -> None:
    out = tmp_path / "out"
    a = FakeSource("a", sections=section("a"))
    pages = [{"file": "a.html", "title": "", "projects": ["A", "Z"]}]
    cfg = replace(config(out, a={}), projects=[{"name": "A", "sections": ["a"]}], pages=pages)

    with pytest.raises(ConfigError) as raised:
        runner.run(cfg, registry=reg(a))

    assert str(raised.value).splitlines() == [
        "invalid pages config:",
        "  pages[0].title: must be a non-empty string",
        '  pages[0].projects[1]: "Z" names no project',
    ]
    assert a.calls == []
    assert not out.exists()


def test_trends_that_cannot_be_written_make_the_run_exit_1_and_the_reports_are_still_written(tmp_path: Path) -> None:
    (tmp_path / trends.DATA).mkdir()

    status = runner.run(config(tmp_path, reports=[CRITICAL_BLOCK]), registry=reg())

    assert status == 1
    assert (tmp_path / "report.md").is_file()
    assert "ERROR: trends is critical (trends.json): can't write trends.json" in log_text(tmp_path)


# --- a source that failed to load ---


def broken(*names: str) -> Registry[Source]:
    """A registry in which these sources failed to load."""
    return Registry({}, dict.fromkeys(names, "ImportError: no module named x"))


def test_a_configured_source_that_failed_to_load_is_critical_and_exits_1_while_the_others_run(
    tmp_path: Path,
) -> None:
    a = FakeSource("a", outputs={"": "a.jsonl"}, sections=section("a"))
    registry = reg(a)
    registry.failed["bad"] = "ImportError: no module named x"

    status = runner.run(config(tmp_path, a={}, bad={}), registry=registry)

    assert status == 1
    assert len(a.calls) == len(a.checked) == 1
    assert [(c["source"], c["file"], c["reason"]) for c in health_row(tmp_path)["critical"]] == [
        ("bad", None, "failed to load: ImportError: no module named x")
    ]
    log = log_text(tmp_path)
    assert "ERROR: bad is critical: failed to load: ImportError: no module named x" in log
    assert "Sources: a\n" in log
    assert "Running bad" not in log


def test_a_source_that_failed_to_load_is_not_validated_checked_or_summarized(tmp_path: Path) -> None:
    status = runner.run(config(tmp_path, reports=None, bad={"x": 1}), registry=broken("bad"))

    assert status == 1
    assert (tmp_path / trends.DATA).is_file()
    assert json.loads((tmp_path / trends.DATA).read_text(encoding="utf-8"))["sections"] == []


def test_a_source_that_failed_to_load_and_is_not_configured_is_a_warning_only(tmp_path: Path) -> None:
    a = FakeSource("a")
    registry = reg(a)
    registry.failed["bad"] = "ImportError: no module named x"

    status = runner.run(config(tmp_path, a={}), registry=registry)

    assert status == 0
    assert health_row(tmp_path)["critical"] == []
    assert "WARN: source bad failed to load: ImportError: no module named x" in log_text(tmp_path)


def test_a_configured_name_with_no_module_is_still_unknown_sources(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown sources: nope"):
        runner.run(config(tmp_path, nope={}), registry=broken("bad"))


def test_only_leaves_a_failed_source_out_of_the_run(tmp_path: Path) -> None:
    a = FakeSource("a")

    status = runner.run(config(tmp_path, a={}, bad={}), only=["a"], registry=Registry({"a": a}, broken("bad").failed))

    assert status == 0
    assert "failed to load" not in log_text(tmp_path)


def test_projects_are_not_validated_when_a_configured_source_failed_to_load_since_its_sections_are_missing(
    tmp_path: Path,
) -> None:
    a = FakeSource("a", sections=section("a"))
    projects = [{"name": "P", "sections": ["a", "bad"]}]
    pages = [{"file": "p.html", "title": "P", "projects": ["P"]}]
    registry = Registry({"a": a}, broken("bad").failed)

    status = runner.run(replace(config(tmp_path, a={}, bad={}), projects=projects, pages=pages), registry=registry)

    assert status == 1
    assert (tmp_path / "p.html").is_file()
    assert "WARN: projects and pages are not validated: bad failed to load" in log_text(tmp_path)


def test_projects_are_validated_when_nothing_failed_to_load(tmp_path: Path) -> None:
    projects = [{"name": "P", "sections": ["missing"]}]

    with pytest.raises(ConfigError, match="names no section"):
        runner.run(replace(config(tmp_path, a={}), projects=projects), registry=reg(FakeSource("a")))
