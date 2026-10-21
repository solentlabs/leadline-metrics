from __future__ import annotations

import logging
import re
import urllib.request
from datetime import date, datetime
from pathlib import Path

import pytest

from leadline import http
from leadline.config import Config
from leadline.context import LOG_FILE, Correction, Critical, Finding, open_context, open_report_context
from leadline.http import HttpError, Response

LINE = re.compile(r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] (.*)$")


def config(out_dir: Path) -> Config:
    return Config(out_dir=out_dir, user_agent="Agent/1.0", sources={})


def test_context_anchors_date_and_creates_out_dir(tmp_path: Path) -> None:
    out = tmp_path / "a" / "metrics"

    with open_context(config(out)) as ctx:
        assert ctx.date == date.today()  # noqa: DTZ011 (the run date is local)
        assert ctx.out_dir == out
        assert out.is_dir()
        assert ctx.http.user_agent == "Agent/1.0"


def test_context_reads_the_clock_once_as_local_time_with_its_offset(tmp_path: Path) -> None:
    before = datetime.now().astimezone()
    with open_context(config(tmp_path)) as ctx:
        after = datetime.now().astimezone()

    assert before <= ctx.now <= after
    assert ctx.now.utcoffset() is not None
    assert ctx.now.utcoffset() == ctx.now.astimezone().utcoffset()  # the local zone's offset
    assert ctx.date == ctx.now.date()


def test_log_lines_go_to_stdout_and_the_log_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with open_context(config(tmp_path)) as ctx:
        ctx.log.info("hello caf\u00e9")
        logging.getLogger("leadline").info("from a module")

    printed = capsys.readouterr().out.splitlines()
    logged = (tmp_path / LOG_FILE).read_text(encoding="utf-8").splitlines()
    assert printed == logged
    assert [LINE.match(line).group(1) for line in logged] == ["hello caf\u00e9", "from a module"]  # type: ignore[union-attr]


def test_log_file_is_truncated_each_run_and_closed_after(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with open_context(config(tmp_path)) as ctx:
        ctx.log.info("first run")
    with open_context(config(tmp_path)) as ctx:
        ctx.log.info("second run")
    logging.getLogger("leadline").info("after the run")

    logged = (tmp_path / LOG_FILE).read_text(encoding="utf-8")
    assert "first run" not in logged
    assert "second run" in logged
    assert "after the run" not in logged
    assert "after the run" not in capsys.readouterr().out


def test_the_leadline_logger_is_left_as_it_was_found(tmp_path: Path) -> None:
    log = logging.getLogger("leadline")
    before = (log.level, log.propagate, list(log.handlers))
    with open_context(config(tmp_path)):
        pass
    assert (log.level, log.propagate, list(log.handlers)) == before
    with open_report_context(config(tmp_path)):
        pass
    assert (log.level, log.propagate, list(log.handlers)) == before


def test_a_source_reports_its_corrections_for_the_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with open_context(config(tmp_path)) as ctx:
        assert ctx.corrections == []
        ctx.record_correction(
            "web", "web-a.jsonl", "2026-09-18", {"pageviews": 30, "visits": 9}, {"pageviews": 31, "visits": 9}
        )

    assert ctx.corrections == [
        Correction("web", "web-a.jsonl", "2026-09-18", {"pageviews": 30, "visits": 9}, {"pageviews": 31, "visits": 9})
    ]
    logged = (tmp_path / LOG_FILE).read_text(encoding="utf-8")
    assert "Corrected 2026-09-18 in web-a.jsonl: pageviews 30 -> 31, visits 9 -> 9" in logged


def test_a_correction_logs_each_changed_value_and_its_second_key(tmp_path: Path) -> None:
    with open_context(config(tmp_path)) as ctx:
        ctx.record_correction(
            "forum", "forum.jsonl", "2026-09-18", {"title": "a", "gone": 1}, {"title": "b"}, {"topic_id": 7}
        )

    assert ctx.corrections == [
        Correction("forum", "forum.jsonl", "2026-09-18", {"title": "a", "gone": 1}, {"title": "b"}, {"topic_id": 7})
    ]
    logged = (tmp_path / LOG_FILE).read_text(encoding="utf-8")
    assert 'Corrected 2026-09-18 topic_id=7 in forum.jsonl: title "a" -> "b", gone 1 -> absent' in logged


def test_a_source_marks_an_error_waiting_cant_fix_critical_at_once(tmp_path: Path) -> None:
    with open_context(config(tmp_path)) as ctx:
        assert ctx.critical == []
        ctx.record_critical("web", "web-a.jsonl", "HTTP 401: credentials rejected")

    assert ctx.critical == [Critical("web", "web-a.jsonl", "HTTP 401: credentials rejected")]
    logged = (tmp_path / LOG_FILE).read_text(encoding="utf-8")
    assert "ERROR: web is critical (web-a.jsonl): HTTP 401: credentials rejected" in logged


@pytest.mark.parametrize("code", [401, 403, 404], ids=["rejected", "refused", "not there"])
def test_a_failed_request_waiting_cant_fix_is_recorded_critical(tmp_path: Path, code: int) -> None:
    error = HttpError("https://example.com/x", code, "")
    with open_context(config(tmp_path)) as ctx:
        ctx.record_unfixable("web", "web-a.jsonl", error, f"HTTP {error.http_code} from Example")

    assert ctx.critical == [Critical("web", "web-a.jsonl", f"HTTP {code} from Example")]


@pytest.mark.parametrize(
    ("code", "headers"),
    [(403, {"Retry-After": "60"}), (429, {}), (500, {}), (0, {})],
    ids=["a 403 rate limit", "429", "a server error", "no connection"],
)
def test_a_failed_request_waiting_can_fix_is_not_critical(tmp_path: Path, code: int, headers: dict[str, str]) -> None:
    with open_context(config(tmp_path)) as ctx:
        ctx.record_unfixable("web", "web-a.jsonl", HttpError("https://example.com/x", code, "", headers), "x")

    assert ctx.critical == []


def test_each_run_starts_with_nothing_reported(tmp_path: Path) -> None:
    with open_context(config(tmp_path)) as first:
        first.record_critical("web", "a.jsonl", "x")
    with open_context(config(tmp_path)) as second:
        assert second.critical == []
        assert second.corrections == []


def test_each_run_has_its_own_http_so_no_answer_outlives_its_run(tmp_path: Path) -> None:
    with open_context(config(tmp_path)) as first:
        pass
    with open_context(config(tmp_path)) as second:
        assert second.http is not first.http


@pytest.mark.parametrize("level", ["ok", "warn", "error"])
def test_a_finding_is_a_level_and_a_message(level: str) -> None:
    finding = Finding(level, "m")

    assert (finding.level, finding.message) == (level, "m")


def test_a_finding_refuses_an_unknown_level() -> None:
    with pytest.raises(ValueError, match="level must be ok, warn or error, not 'fine'"):
        Finding("fine", "m")


def test_every_request_is_sent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    def live(request: urllib.request.Request, timeout: float) -> Response:
        sent.append(request.full_url)
        return Response(200, {}, b"live")

    monkeypatch.setattr(http, "urlopen", live)

    with open_context(config(tmp_path)) as ctx:
        ctx.http.request("https://saved.test/a")

    assert sent == ["https://saved.test/a"]
