"""Port tests: run a source on its scenarios as the script ran them, and compare the rows with the golden.

`run` runs one source through the real runner's collection (validate, files, the credential skip,
collect, the retry pass; not the checks or health, which have tests of their own) on one scenario:
the scenario's `env` in place of the credential variables, its `seed` files in the output folder,
its `now` in its `tz` as the run's clock, and its saved responses served by `http.SavedResponses`
exactly as the oracle harness serves them (each once, several for one request in order), so every
request the source makes is one the script made. `golden_differences` compares the output folder with
the scenario's golden rows through `rowdiff`, which ignores `captured_at`.

A source's scenario module holds, beside SCENARIOS, `DIVERGENCES`: the scenarios where leadline
differs from the script by a decision, each with the decision's name. `golden_scenarios` leaves
those out; the source's tests assert leadline's side of each explicitly. Design: docs/TESTING.md,
"Port tests", and docs/ADDING_A_SOURCE.md.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from unittest import mock

import pytest
import scenarios
from scenarios import Scenario

from leadline import reports, rowdiff, runner
from leadline.config import Config
from leadline.context import Context
from leadline.http import Http, SavedResponses
from leadline.sources import Registry, Source
from leadline.storage import Backend, copy_files
from leadline.storage.jsonl import Jsonl

USER_AGENT = "leadline-port-test"


def local_time(now: str, tz: str) -> datetime:
    """The naive local time `now` in the zone `tz` (a TZ name), with its UTC offset, as ctx.now has it.

    A zone other than UTC is set through TZ and time.tzset, which Windows lacks, so there it skips the test.
    """
    if tz == "UTC":
        return datetime.fromisoformat(now).replace(tzinfo=timezone.utc)
    if not hasattr(time, "tzset"):
        pytest.skip(f"the zone {tz} needs time.tzset, which Windows lacks")
    before = os.environ.get("TZ")
    os.environ["TZ"] = tz
    time.tzset()
    try:
        return datetime.fromisoformat(now).astimezone()
    finally:
        if before is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = before
        time.tzset()


def context(
    out_dir: Path, transport: SavedResponses, now: datetime, sleeps: list[float], backend: Backend | None = None
) -> Context:
    """A run's context with a frozen clock, the saved responses as HTTP, and waits recorded, not slept."""
    return Context(
        now=now,
        out_dir=out_dir,
        log=logging.getLogger("leadline"),
        http=Http(USER_AGENT, transport, sleeps.append),
        backend=backend,
    )


@dataclass
class PortRun:
    status: int  # the run's exit status
    ctx: Context  # what the source reported: corrections, critical
    rows: dict[str, str] = field(default_factory=dict)  # every row file in the output folder, with its text
    sleeps: list[float] = field(default_factory=list)  # the waits between attempts
    log: list[str] = field(default_factory=list)  # the run's log messages


class _Lines(logging.Handler):
    def __init__(self, lines: list[str]) -> None:
        super().__init__(logging.INFO)
        self.lines = lines

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@contextmanager
def captured_log(lines: list[str]) -> Iterator[None]:
    """Each message the "leadline" logger logs meanwhile, appended to `lines`."""
    log = logging.getLogger("leadline")
    handler, level = _Lines(lines), log.level
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        yield
    finally:
        log.removeHandler(handler)
        log.setLevel(level)


@contextmanager
def credentials(env: Mapping[str, str], names: Sequence[str]) -> Iterator[None]:
    """The scenario's credentials and no others: every credential variable unset, then `env` set."""
    with mock.patch.dict(os.environ):
        for name in {*scenarios.CREDENTIALS, *names}:
            os.environ.pop(name, None)
        os.environ.update(env)
        yield


def run(source: Source, cfg: Any, scenario: Scenario, out_dir: Path, backend: Backend | None = None) -> PortRun:
    """Run `source` with config `cfg` on `scenario`, through the runner's collection, writing to `out_dir`.

    With a `backend`, the scenario's seed files are copied into it and the run keeps its row files there; the
    result's `rows` and the files in `out_dir` are what it holds afterwards, exported as jsonl.

    Fails the test when the source makes a request the scenario has no response left for, or never
    reaches one of its responses, as the harness refuses the script's run.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    seeded = out_dir if backend is None else out_dir / "seed"
    seeded.mkdir(exist_ok=True)
    for name, text in scenario.seed.items():
        (seeded / name).write_text(text, encoding="utf-8")
    if backend is not None:
        copy_files(Jsonl(seeded), backend)
    transport = SavedResponses(scenario.responses)
    now = local_time(scenario.now, scenario.tz)
    sleeps: list[float] = []

    config = Config(out_dir, USER_AGENT, {source.NAME: cfg})
    names, by_name = runner.validated(config, registry=Registry({source.NAME: source}))
    ctx = context(out_dir, transport, now, sleeps, backend)
    lines: list[str] = []
    with captured_log(lines), credentials(scenario.env, source.ENV):
        runner.collect(ctx, config, names, by_name)
    if backend is not None:
        copy_files(backend, Jsonl(out_dir))
    problems = [f"no saved response: {r}" for r in transport.unmatched]
    problems += [f"never served: {r}" for r in transport.never_served()]
    assert not problems, f"{scenario.name}: " + "; ".join(problems)
    return PortRun(runner.exit_status(ctx), ctx, scenarios.row_files(out_dir), sleeps, lines)


def divergences(source: str) -> dict[str, str]:
    """The source's scenarios where leadline differs from the script by a decision: name -> decision."""
    found: dict[str, str] = getattr(scenarios.module(source), "DIVERGENCES", {})
    return found


def golden_scenarios(source: str) -> list[Scenario]:
    """The source's scenarios whose rows leadline must match: every one but its decided divergences."""
    defined = scenarios.scenarios(source)
    unknown = sorted(set(divergences(source)) - {s.name for s in defined})
    assert not unknown, f"DIVERGENCES names no scenario: {', '.join(unknown)}"
    return [s for s in defined if s.name not in divergences(source)]


def scenario(source: str, name: str) -> Scenario:
    return next(s for s in scenarios.scenarios(source) if s.name == name)


def golden_differences(source: str, name: str, out_dir: Path) -> list[str]:
    """How the row files in `out_dir` differ from scenario `name`'s golden rows, `captured_at` aside."""
    with tempfile.TemporaryDirectory() as tmp:
        golden = Path(tmp)
        for file, text in scenarios.load_golden(source)[name]["rows"].items():
            (golden / file).write_text(text, encoding="utf-8")
        return rowdiff.compare(golden, out_dir)


def summary(source: Source, cfg: Any, name: str, out_dir: Path, rows: dict[str, str] | None = None) -> list[str]:
    """What the source's summary() shows in the default summary from scenario `name`'s golden rows, or from `rows`
    (file to text) where given: the lines of SUMMARY.md's block after its heading, with no health row, so no health
    lines. The report config must be valid."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for file, text in (rows or scenarios.load_golden(source.NAME)[name]["rows"]).items():
        (out_dir / file).write_text(text, encoding="utf-8")
    blocks = reports.default(source.summary(cfg), "health.jsonl")
    assert reports.validate(blocks) == []
    now = local_time(scenarios.scenarios(source.NAME)[0].now, "UTC")
    ctx = Context(now=now, out_dir=out_dir, log=logging.getLogger("leadline"), http=Http(USER_AGENT))
    reports.write(ctx, blocks)
    assert ctx.critical == []
    return (out_dir / "SUMMARY.md").read_text(encoding="utf-8").splitlines()[3:-1]
