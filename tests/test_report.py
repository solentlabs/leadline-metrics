"""`leadline report`: the reports, the default summary, trends.json and the trends pages, built from the output
folder's files alone, by the same step a run ends with. No network, no credentials, no collection, no health row."""

# cspell:ignore caplog
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import port
import pytest
import scenarios
from test_runner import CRITICAL_BLOCK, FakeSource, broken, config, reg

from leadline import cli, health, reports, runner, trends
from leadline.config import Config, ConfigError, Section, Shown
from leadline.context import LOG_FILE, Context, ReportContext
from leadline.http import SavedResponses
from leadline.sources import discover

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
SOURCE = "github_repo_stats"
SCRIPT_SRC = Path(__file__).parent.parent / "src"


@contextmanager
def offline(cfg: Config) -> Iterator[ReportContext]:
    """The report's context with a frozen clock, and no log file."""
    yield ReportContext(now=NOW, out_dir=cfg.out_dir, log=logging.getLogger("leadline"))


@pytest.fixture
def leadline_log(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Capture the "leadline" logger in caplog whatever `propagate` an earlier run of the real context left on it."""
    log = logging.getLogger("leadline")
    log.addHandler(caplog.handler)
    log.setLevel(logging.INFO)
    try:
        yield
    finally:
        log.removeHandler(caplog.handler)


def hashes(folder: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir()) if p.is_file()}


def outputs(folder: Path) -> dict[str, str]:
    """The files the report step writes, with their text."""
    names = [reports.SUMMARY["file"], trends.DATA, trends.PAGE]
    return {name: (folder / name).read_text(encoding="utf-8") for name in names}


def scenario_run(out: Path, **settings: Any) -> Config:
    """A run of a real source on its first scenario's saved responses, with a frozen clock: returns its config."""
    source = discover().sources[SOURCE]
    scenario = scenarios.scenarios(SOURCE)[0]
    cfg = Config(out, "UA/1", {SOURCE: scenarios.module(SOURCE).LEADLINE_CONFIG}, **settings)
    now = port.local_time(scenario.now, scenario.tz)

    @contextmanager
    def frozen(c: Config) -> Iterator[Context]:
        c.out_dir.mkdir(parents=True, exist_ok=True)
        yield port.context(c.out_dir, SavedResponses(scenario.responses), now, [])

    with port.credentials(scenario.env, source.ENV):
        assert runner.run(cfg, context=frozen) in (0, 1)
    return cfg


def frozen_report(cfg: Config, registry: Any = None) -> int:
    scenario = scenarios.scenarios(SOURCE)[0]
    now = port.local_time(scenario.now, scenario.tz)

    @contextmanager
    def opened(c: Config) -> Iterator[ReportContext]:
        yield ReportContext(now=now, out_dir=c.out_dir, log=logging.getLogger("leadline"))

    return runner.report(cfg, registry=registry or discover(specs_only=True), context=opened)


def test_a_report_after_a_run_rewrites_the_run_s_files_byte_for_byte(tmp_path: Path) -> None:
    cfg = scenario_run(tmp_path)
    written = outputs(tmp_path)
    rows = {k: v for k, v in hashes(tmp_path).items() if k.endswith(".jsonl")}
    for name in written:
        (tmp_path / name).unlink()

    assert frozen_report(cfg) == 0

    assert outputs(tmp_path) == written
    assert {k: v for k, v in hashes(tmp_path).items() if k.endswith(".jsonl")} == rows


def test_a_report_writes_no_health_row_and_leaves_the_rows_and_health_as_they_are(tmp_path: Path) -> None:
    cfg = scenario_run(tmp_path)
    before = hashes(tmp_path)

    assert frozen_report(cfg) == 0

    changed = {name for name, digest in hashes(tmp_path).items() if before.get(name) != digest}
    assert changed <= {reports.SUMMARY["file"], trends.DATA, trends.PAGE}
    assert before[health.FILE] == hashes(tmp_path)[health.FILE]


def test_a_report_with_no_health_row_builds_what_trends_builds_with_none(tmp_path: Path) -> None:
    cfg = scenario_run(tmp_path)
    (tmp_path / health.FILE).unlink()
    for name in (trends.DATA, trends.PAGE):
        (tmp_path / name).unlink()
    sections = discover(specs_only=True).sources[SOURCE].summary(cfg.sources[SOURCE])

    assert frozen_report(cfg) == 0

    expected = trends.build(tmp_path, NOW.date(), sections, health.FILE)
    scenario = scenarios.scenarios(SOURCE)[0]
    expected = trends.build(tmp_path, port.local_time(scenario.now, scenario.tz).date(), sections, health.FILE)
    assert json.loads((tmp_path / trends.DATA).read_text(encoding="utf-8")) == json.loads(json.dumps(expected))
    assert not (tmp_path / health.FILE).exists()


def test_a_report_needs_no_credentials_and_does_not_skip_a_source_whose_variables_are_unset(
    tmp_path: Path, leadline_log: None, caplog: pytest.LogCaptureFixture
) -> None:
    waiting = FakeSource("waiting", ENV=("NO_SUCH_TOKEN_FOR_LEADLINE",), outputs={"": "waiting.jsonl"})
    (tmp_path / "waiting.jsonl").write_text('{"date": "2026-09-23", "status": "ok"}\n', encoding="utf-8")

    status = runner.report(config(tmp_path, waiting={}), registry=reg(waiting), context=offline)

    assert status == 0
    assert (tmp_path / trends.DATA).is_file()
    assert "SKIP" not in caplog.text
    assert waiting.calls == waiting.checked == []


def test_a_report_collects_nothing_and_asks_nothing_of_a_source_but_its_spec(tmp_path: Path) -> None:
    a = FakeSource("a", outputs={"": "a.jsonl"})

    assert runner.report(config(tmp_path, a={}), registry=reg(a), context=offline) == 0

    assert a.validated == [{}]
    assert a.calls == a.checked == []


def test_a_failing_report_block_exits_1_and_the_other_files_are_still_written(tmp_path: Path) -> None:
    (tmp_path / "report.md").write_text("<!-- leadline:start critical -->\n", encoding="utf-8")
    other = {**CRITICAL_BLOCK, "file": "other.md", "block": "other"}

    status = runner.report(config(tmp_path, reports=[CRITICAL_BLOCK, other]), registry=reg(), context=offline)

    assert status == 1
    assert (tmp_path / "other.md").is_file()
    assert (tmp_path / trends.DATA).is_file()


def test_trends_that_cannot_be_written_exit_1_and_the_reports_are_still_written(tmp_path: Path) -> None:
    (tmp_path / trends.DATA).mkdir()

    status = runner.report(config(tmp_path, reports=[CRITICAL_BLOCK]), registry=reg(), context=offline)

    assert status == 1
    assert (tmp_path / "report.md").is_file()


def test_with_no_reports_setting_a_report_writes_the_default_summary(tmp_path: Path) -> None:
    a = FakeSource("a", outputs={"": "a.jsonl"}, sections=[Section("a", None, "a.jsonl", [Shown("n", "n")], "Label")])
    (tmp_path / "a.jsonl").write_text('{"date": "2026-09-23", "status": "ok", "n": 3}\n', encoding="utf-8")

    assert runner.report(config(tmp_path, reports=None, a={}), registry=reg(a), context=offline) == 0

    assert "- n: 3" in (tmp_path / reports.SUMMARY["file"]).read_text(encoding="utf-8")


def test_a_source_the_registry_does_not_have_is_a_config_error_and_writes_nothing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown sources: nope"):
        runner.report(config(tmp_path, nope={}), registry=reg(), context=offline)

    assert list(tmp_path.iterdir()) == []


def test_a_configured_source_that_failed_to_load_is_critical_and_the_report_is_still_written(
    tmp_path: Path, leadline_log: None, caplog: pytest.LogCaptureFixture
) -> None:
    status = runner.report(config(tmp_path, reports=None, bad={"x": 1}), registry=broken("bad"), context=offline)

    assert status == 1
    assert (tmp_path / trends.DATA).is_file()
    assert "bad is critical: failed to load: ImportError: no module named x" in caplog.text


def test_the_command_reports_with_exit_codes_0_1_and_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "leadline.json"
    path.write_text(json.dumps({"out_dir": "metrics", "reports": [CRITICAL_BLOCK]}), encoding="utf-8")
    (tmp_path / "metrics").mkdir()

    assert cli.main(["report", "--config", str(path)], registry=reg()) == 0
    assert (tmp_path / "metrics" / "report.md").is_file()
    assert not (tmp_path / "metrics" / LOG_FILE).exists()
    assert "Wrote report.md: critical" in capsys.readouterr().out

    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "report.md").write_text("<!-- leadline:start critical -->\n", encoding="utf-8")
    assert cli.main(["report", "--config", str(path), "--out", str(tmp_path / "other")], registry=reg()) == 1

    path.write_text(json.dumps({"out_dir": "metrics", "sources": {"nope": {}}}), encoding="utf-8")
    assert cli.main(["report", "--config", str(path)], registry=reg()) == 2
    assert "unknown sources: nope" in capsys.readouterr().err


def test_a_report_loads_no_collection_code(tmp_path: Path) -> None:
    """The report step imports each source's spec and nothing of its capture: no source, no group api, no http."""
    out = tmp_path / "metrics"
    out.mkdir()
    cfg = tmp_path / "leadline.json"
    cfg.write_text(
        json.dumps(
            {
                "out_dir": "metrics",
                "sources": {
                    SOURCE: [{"repo": "o/r", "file": "f.jsonl"}],
                    "pypi": {"packages": ["pkg"], "sleep_s": 0, "retry_wait_s": 0},
                },
            }
        ),
        encoding="utf-8",
    )
    code = (
        "import sys\n"
        "from leadline import cli\n"
        "status = cli.main(['report', '--config', sys.argv[1]])\n"
        "loaded = sorted(m for m in sys.modules if m.startswith('leadline.sources.') or m == 'leadline.http')\n"
        "print(status, *loaded)\n"
    )
    env = {"PYTHONPATH": str(SCRIPT_SRC), "PATH": ""}

    done = subprocess.run(
        [sys.executable, "-c", code, str(cfg)], capture_output=True, text=True, check=False, env=env, cwd=tmp_path
    )

    status, *loaded = done.stdout.splitlines()[-1].split()
    assert done.returncode == 0, done.stderr
    assert status == "0"
    assert "leadline.sources.github.github_repo_stats.spec" in loaded
    assert "leadline.sources.github.spec" in loaded
    capture = [m for m in loaded if m.endswith((".source", ".api")) or m == "leadline.http"]
    assert capture == []


def test_a_report_leaves_an_excluded_release_out_of_the_summary_and_trends_but_not_out_of_the_file(
    tmp_path: Path,
) -> None:
    row = {
        "date": "2026-09-23",
        "repo": "a/b",
        "releases": [
            {
                "tag": "v2.0.0",
                "prerelease": False,
                "draft": False,
                "published_at": "2026-09-01T08:00:00Z",
                "assets": [],
            },
            {
                "tag": "v1.0.0",
                "prerelease": False,
                "draft": False,
                "published_at": "2026-08-01T08:00:00Z",
                "assets": [{"name": "z", "downloads": 9, "size": 1}],
            },
        ],
    }
    file = tmp_path / "github-releases-b.jsonl"
    file.write_text(json.dumps(row) + "\n", encoding="utf-8")
    cfg = config(tmp_path, reports=None, github_releases=[{"repo": "a/b", "bands": "release", "exclude": ["2.0.0"]}])

    assert runner.report(cfg, registry=discover(specs_only=True), context=offline) == 0

    summary = (tmp_path / reports.SUMMARY["file"]).read_text(encoding="utf-8")
    data = json.loads((tmp_path / trends.DATA).read_text(encoding="utf-8"))
    assert "Latest release: v1.0.0" in summary
    assert "v2.0.0" not in summary
    (section,) = [s for s in data["sections"] if s["source"] == "github_releases"]
    assert section["fields"][0]["keys"] == ["1.0.0"]
    assert "v2.0.0" in file.read_text(encoding="utf-8")
