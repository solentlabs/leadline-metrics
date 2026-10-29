"""The guide's nuget_downloads module is the template a new source is copied from: it runs, and keeps every rule.

The module is read out of docs/ADDING_A_SOURCE.md, step 5, and run through the runner (tests/port.py) on
made-up NuGet answers: a good row, a failure row for each kind of failure, critical for an error waiting
can't fix, and a good row kept (ADRs: a failure never replaces good data; a failure is recorded as a
failure; one failed night recovers, consecutive failures are critical).
"""

from __future__ import annotations

import re
import types
from pathlib import Path
from typing import Any

import port
import pytest
from scenarios import Scenario
from scenarios.builders.common import exchange, thrice

from leadline import reports

GUIDE = Path(__file__).parent.parent / "docs" / "ADDING_A_SOURCE.md"
URL = "https://azuresearch-usnc.nuget.org/query?q=packageid:Example.Package"
FILE = "nuget-downloads-Example.Package.jsonl"
CONFIG = [{"package": "Example.Package"}]
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it
DAY = '{"date": "2026-09-23", "package": "Example.Package"'


def example() -> Any:
    """The guide's module, compiled from the guide's text and loaded as a module."""
    blocks = re.findall(r'```python\n("""nuget_downloads: .*?)```', GUIDE.read_text(encoding="utf-8"), re.DOTALL)
    assert len(blocks) == 1, "the guide has one nuget_downloads module"
    module = types.ModuleType("nuget_downloads")
    exec(compile(blocks[0], str(GUIDE), "exec"), module.__dict__)  # noqa: S102 (the repo's own guide)
    return module


def run(tmp_path: Path, response: Any, status: int = 200, seed: str | None = None) -> port.PortRun:
    scenario = Scenario(
        "guide_example",
        calls=[],
        responses=thrice(exchange(URL, response, status=status))
        if status >= 500
        else [exchange(URL, response, status=status)],
        seed={FILE: seed} if seed is not None else {},
        expected={},
    )
    return port.run(example(), CONFIG, scenario, tmp_path)


def search(*hits: Any) -> dict[str, Any]:
    return {"totalHits": len(hits), "data": list(hits)}


def test_the_example_validates_and_names_its_file() -> None:
    module = example()

    assert module.validate(CONFIG) == []
    assert module.validate([{"package": ""}]) == ["[0].package: must be a non-empty string"]
    assert module.validate([{"package": "A", "bogus": 1}]) == ["[0].bogus: unknown key (the keys are file, package)"]
    assert module.files(CONFIG) == {"[0]": FILE}


def test_the_example_writes_the_days_total_stamped_with_the_runs_clock(tmp_path: Path) -> None:
    done = run(tmp_path, search({"id": "Example.Package", "totalDownloads": 1234}))

    assert done.rows == {FILE: f'{DAY}, "downloads": 1234, "captured_at": "{NOW}"}}\n'}
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_example_records_a_failed_fetch_as_a_recoverable_failure_row(tmp_path: Path) -> None:
    done = run(tmp_path, "unavailable", status=503)

    assert done.rows == {
        FILE: f'{DAY}, "status": "fetch_failed", "http_code": "503", '
        f'"reason": "search request failed (HTTP 503, text body)", "captured_at": "{NOW}"}}\n'
    }
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_example_names_a_bot_challenge_in_the_failure_row(tmp_path: Path) -> None:
    done = run(
        tmp_path, "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body></body></html>", status=503
    )

    assert '"reason": "search request failed (HTTP 503, bot challenge)"' in done.rows[FILE]
    assert (done.status, done.ctx.critical) == (0, [])


@pytest.mark.parametrize("code", [401, 403, 404])
def test_the_example_marks_an_error_waiting_cant_fix_critical(tmp_path: Path, code: int) -> None:
    done = run(tmp_path, "no", status=code)

    assert f'"status": "fetch_failed", "http_code": "{code}"' in done.rows[FILE]
    assert [c.reason for c in done.ctx.critical] == [f"Example.Package: HTTP {code} from NuGet; text body"]
    assert done.status == 1


@pytest.mark.parametrize(
    "response",
    ["not json", [], {"data": None}, {"data": [1]}, search({"id": "Example.Package"})],
    ids=["not JSON", "a list", "null data", "a hit that is not an object", "no totalDownloads"],
)
def test_the_example_records_an_unusable_answer_as_parse_failed(tmp_path: Path, response: Any) -> None:
    done = run(tmp_path, response)

    assert done.rows == {
        FILE: f'{DAY}, "status": "parse_failed", "reason": "response was not a search result", '
        f'"captured_at": "{NOW}"}}\n'
    }
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_example_records_a_missing_package_as_a_critical_failure_row(tmp_path: Path) -> None:
    done = run(tmp_path, search())

    assert done.rows == {FILE: f'{DAY}, "status": "not_found", "reason": "no such package", "captured_at": "{NOW}"}}\n'}
    assert [c.reason for c in done.ctx.critical] == ["NuGet has no package Example.Package"]
    assert done.status == 1


def test_the_example_keeps_a_good_row_through_a_failure(tmp_path: Path) -> None:
    good = f'{DAY}, "downloads": 1200, "captured_at": "2026-09-23T08:00:00+00:00"}}\n'

    done = run(tmp_path, "unavailable", status=503, seed=good)

    assert done.rows == {FILE: good}
    assert f"WARN: kept the good row for 2026-09-23 in {FILE}; fetch_failed not recorded" in done.log


def test_the_examples_summary_shows_the_packages_downloads(tmp_path: Path) -> None:
    done = run(tmp_path, search({"id": "Example.Package", "totalDownloads": 1234}))
    blocks = reports.default(example().summary(CONFIG), "health.jsonl")
    assert reports.validate(blocks) == []

    reports.write(done.ctx, blocks)

    lines = (tmp_path / "SUMMARY.md").read_text(encoding="utf-8").splitlines()
    assert lines[3:-1] == ["", "### nuget_downloads: Example.Package", "", "- Downloads: 1234"]


def test_the_examples_sections_carry_its_readable_label() -> None:
    assert [section.label for section in example().summary(CONFIG)] == ["NuGet downloads"]
