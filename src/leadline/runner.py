"""One run: every enabled source, in config order, each isolated; then checks, health, the reports and trends."""

from __future__ import annotations

import os
import platform
from dataclasses import asdict
from typing import Any, Callable, ContextManager, Mapping, Sequence, TypeVar

from leadline import __version__, health, reports, trends
from leadline.config import Config, ConfigError, hidden
from leadline.context import Context, ReportContext, Reporter, open_context, open_report_context
from leadline.sources import Registry, Source, Spec, discover, summary_sections

S = TypeVar("S", bound=Spec, covariant=True)


def run(
    config: Config,
    only: Sequence[str] | None = None,
    registry: Registry[Source] | None = None,
    context: Callable[[Config], ContextManager[Context]] = open_context,
) -> int:
    """Run the sources, check them, write health, the reports and trends; returns the exit status: 1 when anything was
    critical.

    The config is `validated` first, and nothing is written when it isn't. Then `collect`: every
    enabled source in config order (`only` narrows it), and the retry pass. Then each source that
    wasn't skipped runs its check(), in config order, its findings recorded as the health row's
    `checks`; a check that raises is a bug, critical as a crash in collection is. Then health
    writes the run's row (leadline.health), which also records critical each source file with no
    good row for two consecutive expected dates. Last, the reports rebuild their blocks from the
    output files, this run's health row included: the step `build` that `report` runs alone (the config's reports,
    or with none the default summary; trends.json and its pages); a failure there is critical.
    `registry` is the sources to use; with none, `discover()` finds them, once. A configured source that failed to
    load is critical ("failed to load"), and the others run; one not configured is a warning.
    `context` opens the run's context; tests give one with a frozen clock and saved responses.
    """
    registry = discover() if registry is None else registry
    names, by_name = validated(config, only, registry=registry)
    broken = [name for name in names if name in registry.failed]
    names = [name for name in names if name not in registry.failed]
    loaded = {name: cfg for name, cfg in config.sources.items() if name in by_name}
    with context(config) as ctx:
        log = ctx.log
        log.info("=== leadline run ===")
        log.info("leadline %s", __version__)
        log.info("Python %s", platform.python_version())
        log.info("Date: %s", ctx.date)
        log.info("Output: %s", ctx.out_dir)
        log.info("Sources: %s", ", ".join(names) or "none")
        _failed_to_load(ctx, config, registry, broken)
        collected = collect(ctx, config, names, by_name)
        checks = _checks(ctx, config, by_name, collected)
        written = {
            file: health.Written(name, by_name[name].LAGGING)
            for name, cfg in loaded.items()
            for file in by_name[name].files(cfg).values()
        }
        health.write(ctx, written, checks, config.config_hash)
        build(ctx, config, registry)
    return exit_status(ctx)


def report(
    config: Config,
    registry: Registry[Spec] | None = None,
    context: Callable[[Config], ContextManager[ReportContext]] = open_report_context,
) -> int:
    """Build the reports, trends.json and the trends pages from the output folder's files alone; returns the exit
    status: 1 when a report block or a trends file failed, or a configured source failed to load.

    The config is `validated` as `run` validates it, and nothing is written when it isn't. Then `build`, the step a
    run ends with. No source is collected or checked, so no credential is needed, no request is made, no health row is
    written (the last is read) and no row file changes. `registry` is the specs to use; with none,
    `discover(specs_only=True)` finds them, and no source's capture code is imported.
    """
    registry = discover(specs_only=True) if registry is None else registry
    names, _ = validated(config, registry=registry)
    broken = [name for name in names if name in registry.failed]
    with context(config) as ctx:
        ctx.log.info("=== leadline report ===")
        ctx.log.info("leadline %s", __version__)
        ctx.log.info("Output: %s", ctx.out_dir)
        _failed_to_load(ctx, config, registry, broken)
        build(ctx, config, registry)
    return exit_status(ctx)


def build(ctx: Reporter, config: Config, registry: Registry[Spec]) -> None:
    """The step `run` and `report` share: the reports (leadline.reports), then trends.json and its pages
    (leadline.trends), from the output files and each source's summary() sections.

    The reports are the config's, or with none the default summary (`default_reports`); trends group the sections by
    the config's projects. A block or file that fails is critical (recorded on `ctx`), and the rest are still written.
    """
    sections = summary_sections(config, registry)
    blocks = reports.default(sections, health.FILE) if config.reports is None else config.reports
    reports.write(ctx, blocks, hidden(sections))
    trends.write(ctx, sections, health.FILE, config.projects, config.pages)


def _failed_to_load(ctx: Reporter, config: Config, registry: Registry[Spec], broken: Sequence[str]) -> None:
    """Log each source that failed to load and wasn't configured as a warning; record each configured one (`broken`)
    as critical, with a warning that the projects and pages could not be validated against its sections."""
    for name, why in registry.failed.items():
        if name not in config.sources:
            ctx.log.info("WARN: source %s failed to load: %s", name, why)
    for name in broken:
        ctx.record_critical(name, None, f"failed to load: {registry.failed[name]}")
    if broken and (config.projects is not None or config.pages is not None):
        ctx.log.info("WARN: projects and pages are not validated: %s failed to load", ", ".join(broken))


def exit_status(ctx: Reporter) -> int:
    """1 when anything in the run was critical, else 0: a failure row alone is recoverable."""
    return 1 if ctx.critical else 0


def validated(
    config: Config, only: Sequence[str] | None = None, *, registry: Registry[S]
) -> tuple[list[str], dict[str, S]]:
    """The names of the sources to run, in config order, and the registry by name; raises ConfigError.

    A source is enabled by its entry under "sources" in the config. Every enabled source's config
    is validated, and then no two entries may write one file; then the reports' config, if any; then the trends
    projects, if any, against the sections' titles; then the trends pages, if any, against the projects. Any
    problem raises ConfigError, naming the source or report, entry and key, before anything is
    written. `only` narrows the run to those names.

    A configured source that failed to load (`registry.failed`) is left out of all of that, and its sections are
    missing, so the projects and pages are not validated either; the names returned include it.
    """
    by_name = registry.sources
    unknown = [name for name in config.sources if name not in by_name and name not in registry.failed]
    if unknown:
        raise ConfigError(f"unknown sources: {', '.join(unknown)}")
    loaded = {name: cfg for name, cfg in config.sources.items() if name in by_name}
    problems = [f"sources.{name}{problem}" for name, cfg in loaded.items() for problem in by_name[name].validate(cfg)]
    if not problems:
        problems = _shared_files(loaded, by_name)
    if problems:
        raise ConfigError("\n  ".join(["invalid source config:", *problems]))
    problems = [] if config.reports is None else [f"reports{problem}" for problem in reports.validate(config.reports)]
    if problems:
        raise ConfigError("\n  ".join(["invalid report config:", *problems]))
    if len(loaded) == len(config.sources):
        titles = [section.title for section in summary_sections(config, registry)]
        problems = [f"projects{problem}" for problem in trends.validate(config.projects, titles)]
        if problems:
            raise ConfigError("\n  ".join(["invalid projects config:", *problems]))
        problems = [f"pages{problem}" for problem in trends.validate_pages(config.pages, config.projects)]
        if problems:
            raise ConfigError("\n  ".join(["invalid pages config:", *problems]))
    names = list(config.sources)
    if only is not None:
        missing = [name for name in only if name not in config.sources]
        if missing:
            raise ConfigError(f"--only names sources not in the config: {', '.join(missing)}")
        names = [name for name in names if name in only]
    return names, by_name


def collect(ctx: Context, config: Config, names: Sequence[str], by_name: Mapping[str, Source]) -> list[str]:
    """Collect each of `names`, in order, then the retry pass; returns those not skipped, in order.

    A source whose credential variables are unset is skipped, which is not a failure. A failure row
    a source writes is recoverable and doesn't change the exit status; what it reports with
    `ctx.record_critical` does. An exception from a source is a bug, which waiting can't fix: it is
    logged, recorded as critical, and the next source runs. Then the retry pass: each source that
    ran and defines `retry` (PyPI) gets its second pass, in config order, isolated the same way.
    """
    log = ctx.log
    ran: list[str] = []
    crashed: list[str] = []
    skipped: list[str] = []
    for name in names:
        source = by_name[name]
        unset = [var for var in source.ENV if not os.environ.get(var)]
        if unset:
            log.info("SKIP: %s (%s not set)", name, ", ".join(unset))
            skipped.append(name)
            continue
        log.info("Running %s", name)
        (ran if _isolated(ctx, name, source.collect, config.sources[name]) else crashed).append(name)
    _retry_pass(ctx, config, by_name, ran, crashed)
    crashes = f" ({', '.join(crashed)})" if crashed else ""
    log.info("Done: %d ran, %d crashed%s, %d skipped", len(ran), len(crashed), crashes, len(skipped))
    return [name for name in names if name not in skipped]


def _checks(ctx: Context, config: Config, by_name: Mapping[str, Source], names: Sequence[str]) -> list[dict[str, str]]:
    """Each source's check() in turn, isolated; its findings, each with its source's name, logged."""
    found: list[dict[str, str]] = []
    for name in names:

        def check(ctx: Context, cfg: Any, name: str = name) -> None:
            for finding in by_name[name].check(ctx, cfg):
                found.append({"source": name, **asdict(finding)})
                ctx.log.info(_CHECK_LINES[finding.level], name, finding.message)

        _isolated(ctx, name, check, config.sources[name], " in its check")
    return found


# How each level of finding is logged: warnings and errors carry WARN: and ERROR:, as every log line does.
_CHECK_LINES = {"ok": "Check %s: %s", "warn": "WARN: check %s: %s", "error": "ERROR: check %s: %s"}


def _retry_pass(
    ctx: Context, config: Config, by_name: Mapping[str, Source], ran: list[str], crashed: list[str]
) -> None:
    """Each source that ran and defines `retry` (PyPI), in config order: its second pass, isolated.

    One that crashes moves from `ran` to `crashed`.
    """
    for name in list(ran):
        retry = getattr(by_name[name], "retry", None)
        if retry is None:
            continue
        ctx.log.info("Retry pass: %s", name)
        if not _isolated(ctx, name, retry, config.sources[name], " in its retry pass"):
            ran.remove(name)
            crashed.append(name)


def _isolated(ctx: Context, name: str, call: Callable[[Context, Any], None], cfg: Any, during: str = "") -> bool:
    """Call a source's `call(ctx, cfg)`; returns False when it raised, which is a bug, logged and critical."""
    try:
        call(ctx, cfg)
    except Exception as e:
        ctx.log.exception("ERROR: %s crashed%s: %s: %s", name, during, type(e).__name__, e)
        ctx.record_critical(name, None, f"crashed{during}: {type(e).__name__}: {e}")
        return False
    return True


def _shared_files(sources: Mapping[str, Any], by_name: Mapping[str, Spec]) -> list[str]:
    """Each output file that two or more entries write, with their paths; names that differ only in case clash."""
    writers: dict[str, list[tuple[str, str]]] = {}
    for name, cfg in sources.items():
        for path, file in by_name[name].files(cfg).items():
            writers.setdefault(file.casefold(), []).append((f"sources.{name}{path}", file))
    clashes = []
    for found in writers.values():
        if len(found) > 1:
            paths = [path for path, _ in found]
            who = ", ".join(paths[:-1]) + " and " + paths[-1]
            clashes.append(f"{who} write the same file: {', '.join(sorted({file for _, file in found}))}")
    return clashes
