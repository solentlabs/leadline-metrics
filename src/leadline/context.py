"""What every source gets for a run: the run's clock and date, the output folder, the log, HTTP.

What health records for the run is reported through it too: each correction, which store records as a
source writes, and everything critical, which makes the run exit 1: an error waiting can't fix, which a
source reports, a crash, which the runner records, and a source with no good row for two nights running,
which health records. A report block that fails is recorded critical too, after health. `check()`
returns Findings.
"""

from __future__ import annotations

import json
import logging
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Mapping, Protocol

from leadline.config import Config, ConfigError
from leadline.storage import Backend, File, StorageError, open_backend
from leadline.storage.jsonl import Jsonl

if TYPE_CHECKING:  # the report path never loads http (docs/ARCHITECTURE.md, "Dependency rules")
    from leadline.http import Http

# Rewritten on every run, so it only ever describes the latest one.
LOG_FILE = "leadline.log"


LEVELS = ("ok", "warn", "error")


@dataclass(frozen=True)
class Finding:
    """One thing `check()` found about a source's config against the live API."""

    level: str  # one of LEVELS
    message: str

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"level must be ok, warn or error, not {self.level!r}")


@dataclass(frozen=True)
class Correction:
    """A good row for a past day replaced by a newer good reading: the values that changed, before and after.

    A value only one side has is left out of the other. `second_key` is the row's key besides its date
    (`{"topic_id": 7}`), empty when the file has none.
    """

    source: str
    file: str
    date: str
    old: Mapping[str, Any]
    new: Mapping[str, Any]
    second_key: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Critical:
    """Something critical (docs/ARCHITECTURE.md, "Terms"): an error waiting can't fix, a crash, or two failed nights.

    `file` is None when the source crashed, which is no one file's error. A report block that fails is
    recorded as `reports`, with its Markdown file.
    """

    source: str
    file: str | None
    reason: str


class RequestError(Protocol):
    """A failed request's error, which says whether waiting can fix it: `HttpError`, or `github.GraphqlError`."""

    @property
    def unfixable(self) -> bool: ...


class Reporter(Protocol):
    """What the report step (reports, trends) needs of a context: the clock's date, the folder, the log, and
    `record_critical`. A run's `Context` and the report command's `ReportContext` both give it."""

    @property
    def out_dir(self) -> Path: ...

    def file(self, name: str) -> File: ...

    @property
    def log(self) -> logging.Logger: ...

    @property
    def date(self) -> date: ...

    @property
    def critical(self) -> list[Critical]: ...

    def record_critical(self, source: str, file: str | None, reason: str) -> None: ...


@dataclass(frozen=True)
class ReportContext:
    """The context of `leadline report`: the clock, the output folder, the log and what is critical, and no HTTP."""

    now: datetime
    out_dir: Path
    log: logging.Logger
    critical: list[Critical] = field(default_factory=list)
    backend: Backend | None = None  # where the row files are kept; None is jsonl files in out_dir

    @property
    def date(self) -> date:
        return self.now.date()

    def storage(self) -> Backend:
        """Where the run's row files are kept: the backend it was given, or the jsonl files of `out_dir`."""
        return self.backend or Jsonl(self.out_dir)

    def file(self, name: str) -> File:
        """The row file `name` of the run's backend."""
        return File(self.storage(), name)

    def record_critical(self, source: str, file: str | None, reason: str) -> None:
        """Record something critical; the command exits 1."""
        _record_critical(self.critical, self.log, source, file, reason)


@dataclass(frozen=True)
class Context:
    # The run's clock: the local time when the run started, with its UTC offset,
    # read once. Every date and `captured_at` comes from it, never from the wall
    # clock, so a run that crosses midnight stays on one date.
    now: datetime
    out_dir: Path
    log: logging.Logger
    http: Http
    # What the run reports for health, which writes both to health.jsonl.
    corrections: list[Correction] = field(default_factory=list)
    critical: list[Critical] = field(default_factory=list)
    backend: Backend | None = None  # where the row files are kept; None is jsonl files in out_dir

    @property
    def date(self) -> date:
        """The run date: the local date when the run started."""
        return self.now.date()

    def storage(self) -> Backend:
        """Where the run's row files are kept: the backend it was given, or the jsonl files of `out_dir`."""
        return self.backend or Jsonl(self.out_dir)

    def file(self, name: str) -> File:
        """The row file `name` of the run's backend."""
        return File(self.storage(), name)

    def record_correction(
        self,
        source: str,
        file: str,
        day: str,
        old: Mapping[str, Any],
        new: Mapping[str, Any],
        second_key: Mapping[str, Any] | None = None,
    ) -> None:
        """Report that `source` replaced the good row for `day` in `file`: `old` and `new` are the values that changed.

        `store.write_good` and `store.write_window` call it for every correction they make.
        """
        key = dict(second_key or {})
        self.corrections.append(Correction(source, file, day, dict(old), dict(new), key))
        where = " ".join([day, *(f"{k}={v}" for k, v in key.items())])
        fields = list(old) + [f for f in new if f not in old]
        changes = ", ".join(f"{f} {_shown(old, f)} -> {_shown(new, f)}" for f in fields)
        self.log.info("Corrected %s in %s: %s", where, file, changes)

    def record_critical(self, source: str, file: str | None, reason: str) -> None:
        """Record something critical for `source`'s `file`, such as an error waiting can't fix; the run exits 1."""
        _record_critical(self.critical, self.log, source, file, reason)

    def record_unfixable(self, source: str, file: str | None, error: RequestError, reason: str) -> None:
        """Record `error` critical, with `reason`, when waiting can't fix it (`error.unfixable`); else nothing.

        Every source calls it for each failed request, so the rule lives here once.
        """
        if error.unfixable:
            self.record_critical(source, file, reason)


def _record_critical(critical: list[Critical], log: logging.Logger, source: str, file: str | None, reason: str) -> None:
    critical.append(Critical(source, file, reason))
    where = f" ({file})" if file is not None else ""
    log.error("ERROR: %s is critical%s: %s", source, where, reason)


def _shown(values: Mapping[str, Any], name: str) -> str:
    """A correction's value as the log shows it: its JSON, or `absent`."""
    return json.dumps(values[name]) if name in values else "absent"


@contextmanager
def _logging(*handlers: logging.Handler) -> Iterator[logging.Logger]:
    """The "leadline" logger with these handlers, whose lines read `[YYYY-MM-DD HH:MM:SS] message`, local time, as the
    script's log() writes them. The handlers are closed after, and the logger's level and propagate are put back."""
    log = logging.getLogger("leadline")
    level, propagate = log.level, log.propagate
    log.setLevel(logging.INFO)
    log.propagate = False
    formatter = logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in handlers:
        handler.setFormatter(formatter)
        log.addHandler(handler)
    try:
        yield log
    finally:
        for handler in handlers:
            log.removeHandler(handler)
            handler.close()
        log.setLevel(level)
        log.propagate = propagate


@contextmanager
def open_context(config: Config) -> Iterator[Context]:
    """Start a run: read the clock, create the output folder, log to stdout and LOG_FILE, and open its HTTP.

    HTTP sends every request (`http.urlopen`).

    Every module logs through the "leadline" logger.
    """
    from leadline import http  # here, not at the top: a report never loads it

    config.out_dir.mkdir(parents=True, exist_ok=True)
    backend = _backend(config)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.out_dir / LOG_FILE, "w", encoding="utf-8"),
    ]
    with _logging(*handlers) as log:
        # Local time, whose date is the script's `date +%F` and whose stamp is its captured_at.
        now = datetime.now().astimezone()
        transport: http.Transport = http.urlopen
        yield Context(
            now=now, out_dir=config.out_dir, log=log, http=http.Http(config.user_agent, transport), backend=backend
        )


@contextmanager
def open_report_context(config: Config) -> Iterator[ReportContext]:
    """Start a report: read the clock and log to stdout only, so the run's LOG_FILE stays the latest run's.

    The output folder must exist; a report makes no requests and writes only what it builds.
    """
    if not config.out_dir.is_dir():
        raise ConfigError(f"the output folder {config.out_dir} does not exist")
    backend = _backend(config)
    with _logging(logging.StreamHandler(sys.stdout)) as log:
        yield ReportContext(now=datetime.now().astimezone(), out_dir=config.out_dir, log=log, backend=backend)


def _backend(config: Config) -> Backend:
    try:
        return open_backend(config.storage, config.out_dir)
    except StorageError as e:
        raise ConfigError(f"storage: {e}") from e
