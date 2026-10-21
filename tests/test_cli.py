from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Any

import pytest

import leadline
from leadline import cli, reports, runner, sources
from leadline.config import Config, Section, load
from leadline.context import LOG_FILE
from leadline.sources import Registry, Source


def write_config(tmp_path: Path, data: Any) -> Path:
    path = tmp_path / "leadline.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_run_with_zero_sources(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_config(tmp_path, {"out_dir": "metrics"})

    assert cli.main(["run", "--config", str(path)]) == 0

    assert "=== leadline run ===" in capsys.readouterr().out
    assert (tmp_path / "metrics" / LOG_FILE).exists()


def test_version_names_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(["--version"])

    assert exited.value.code == 0
    assert capsys.readouterr().out == f"leadline {leadline.__version__}\n"


def test_flags_reach_the_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Config, Any]] = []

    def fake_run(config: Config, only: list[str] | None = None, registry: Registry[Source] | None = None) -> int:
        seen.append((config, only))
        return 1

    monkeypatch.setattr(runner, "run", fake_run)
    path = write_config(tmp_path, {"out_dir": "metrics", "sources": {"a": {}, "b": {}}})

    status = cli.main(["run", "--config", str(path), "--only", "b, a", "--out", "/shadow", "--user-agent", "UA/2"])

    assert status == 1
    config, only = seen[0]
    assert (config.out_dir, config.user_agent, only) == (Path("/shadow"), "UA/2", ["b", "a"])


def test_config_error_exits_2_with_a_message(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_config(tmp_path, {"out_dir": "m", "sources": {"nope": {}}})

    assert cli.main(["run", "--config", str(path)]) == 2

    assert capsys.readouterr().err == "leadline: error: unknown sources: nope\n"


class Invalid:
    """A registered source whose config always has a problem."""

    NAME = "invalid"
    DEFAULT_FILE = "invalid.jsonl"
    ENV: tuple[str, ...] = ()
    LAGGING = False

    @staticmethod
    def validate(cfg: Any) -> list[str]:
        return [f"[0].repo: is required, in {cfg}"]

    @staticmethod
    def files(cfg: Any) -> dict[str, str]:
        raise AssertionError("asked only of a valid config")

    @staticmethod
    def summary(cfg: Any) -> list[Section]:
        raise AssertionError("asked only of a valid config")

    @staticmethod
    def collect(ctx: Any, cfg: Any) -> None:
        raise AssertionError("never runs")

    @staticmethod
    def check(ctx: Any, cfg: Any) -> list[Any]:
        raise AssertionError("never runs")


def test_a_source_config_problem_exits_2_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_config(tmp_path, {"out_dir": "metrics", "sources": {"invalid": [{}]}})
    status = cli.main(["run", "--config", str(path)], registry=Registry({"invalid": Invalid()}))

    assert status == 2
    assert capsys.readouterr().err == (
        "leadline: error: invalid source config:\n  sources.invalid[0].repo: is required, in [{}]\n"
    )
    assert not (tmp_path / "metrics").exists()


def test_a_command_is_required() -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main([])

    assert raised.value.code == 2


def test_console_script_is_installed(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"out_dir": "metrics"})
    # pip's scripts folder: beside python on Linux and macOS, Scripts\ with a .exe on Windows.
    script = shutil.which("leadline", path=sysconfig.get_path("scripts"))
    assert script, "the leadline console script is installed"

    done = subprocess.run([script, "run", "--config", str(path)], capture_output=True, text=True, check=False)

    assert done.returncode == 0, done.stderr
    assert "0 ran, 0 crashed, 0 skipped" in done.stdout


def test_only_needs_a_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_config(tmp_path, {"out_dir": "metrics"})

    with pytest.raises(SystemExit) as raised:
        cli.main(["run", "--config", str(path), "--only", " , "])

    assert raised.value.code == 2
    assert "needs at least one source name" in capsys.readouterr().err


def test_default_reports_prints_the_default_summary_as_a_reports_setting_to_paste(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repos = [{"repo": "your-name/your-repo"}]
    path = write_config(tmp_path, {"out_dir": "metrics", "sources": {"github_repo_stats": repos}, "reports": []})

    assert cli.main(["default-reports", "--config", str(path)]) == 0

    printed = json.loads(capsys.readouterr().out)
    config = load(path, {})
    assert printed == {"reports": sources.default_reports(config, sources.discover())}
    assert reports.validate(printed["reports"]) == []
    assert not (tmp_path / "metrics").exists()


def test_default_reports_exits_2_for_an_unusable_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_config(tmp_path, {"out_dir": "m", "sources": {"nope": {}}})

    assert cli.main(["default-reports", "--config", str(path)]) == 2
    assert capsys.readouterr().err == "leadline: error: unknown sources: nope\n"


def test_python_dash_m_leadline_runs_the_command() -> None:
    done = subprocess.run([sys.executable, "-m", "leadline", "--version"], capture_output=True, text=True, check=False)

    assert done.returncode == 0
    assert done.stdout == f"leadline {leadline.__version__}\n"


# --- the console: UTF-8 whatever the locale's code page is ---


def narrow_streams(monkeypatch: pytest.MonkeyPatch) -> tuple[io.BytesIO, io.BytesIO]:
    """stdout and stderr as a Windows console pipe can be: a code page that lacks most characters."""
    out, err = io.BytesIO(), io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(out, encoding="ascii"))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(err, encoding="ascii"))
    return out, err


def test_output_a_code_page_lacks_is_written_as_utf8_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, _ = narrow_streams(monkeypatch)
    a, b = tmp_path / "naïve", tmp_path / "bé"
    a.mkdir()
    b.mkdir()

    assert cli.main(["diff", str(a), str(b)]) == 0

    sys.stdout.flush()
    assert "naïve".encode() in out.getvalue()


def test_an_error_message_a_code_page_lacks_is_written_as_utf8_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, err = narrow_streams(monkeypatch)

    assert cli.main(["diff", str(tmp_path / "café"), str(tmp_path)]) == 2

    sys.stderr.flush()
    assert "café".encode() in err.getvalue()


def test_streams_that_cannot_be_reconfigured_are_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Plain:
        """A stream with no `reconfigure`, as a host that replaces sys.stdout may give."""

        def write(self, text: str) -> int:
            return len(text)

        def flush(self) -> None:
            pass

    monkeypatch.setattr(sys, "stdout", Plain())
    monkeypatch.setattr(sys, "stderr", Plain())
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    assert cli.main(["diff", str(tmp_path / "a"), str(tmp_path / "b")]) == 0
