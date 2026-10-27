"""The `leadline` command.

- `leadline --version`: the package version.
- `leadline run --config P [--only a,b] [--out DIR] [--user-agent UA]`:
  exit status 1 when anything was critical (an error waiting can't fix, a
  source crashing, or a source with no good row two nights running), 0
  otherwise, failure rows included; 2 when the command line or the config
  is unusable (nothing runs).
- `leadline report --config P [--out DIR]`: build the reports, the default
  summary, trends.json and the trends pages from the output folder's files
  alone, with no collection, no network and no credentials, and no health
  row; exit status 1 when a report block or a trends file failed, 2 when the
  command line or the config is unusable (nothing is written).
- `leadline default-reports --config P`: the default summary a run writes
  when the config has no `reports`, printed as a `reports` setting to
  paste into the config and change; exit status 2 when the config is
  unusable.
- `leadline import --config P [--from DIR] [--max-values N]`: copy the lines of
  the `*.jsonl` row files in DIR (default: the output folder) that the config's
  `storage` database lacks, `health.jsonl` aside, and check each file reads
  back as it was. A run continues where the last stopped, and takes at most N
  values (the first line always, so each run makes progress): a database that
  counts what it writes (a free D1 plan) is filled over several runs. Exit
  status 2, copying nothing, when the database holds a night the folder lacks
  or differs from it, the storage is jsonl, DIR is not a folder, or the
  config is unusable.
- `leadline export --config P --to DIR`: copy the config's `storage` database
  into `*.jsonl` row files in DIR (made when missing), and check each reads
  back as it was; exit status 2, copying nothing, when DIR already holds a
  file of one of those names, and when the storage is jsonl or the config is
  unusable.
- `leadline diff A B`: compare the row files in two folders, ignoring
  `captured_at`: one line per difference, then a count of them by file;
  exit status 0 when they match, 1 when they differ, 2 when either is not
  a folder.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from leadline import __version__, rowdiff, runner, storage
from leadline.config import HEALTH_FILE, SETTINGS, Config, ConfigError, load
from leadline.sources import Registry, Source, Spec, default_reports, discover
from leadline.storage import Backend, StorageError
from leadline.storage.jsonl import Jsonl


def _names(value: str) -> list[str]:
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        raise argparse.ArgumentTypeError("needs at least one source name")
    return names


def _utf8_console() -> None:
    """Write stdout and stderr as UTF-8 whatever the locale's code page (a byte that isn't text becomes `?`).

    A Windows console or pipe uses the code page, so a login or a path with a character outside it would
    otherwise raise. A stream that can't be reconfigured (one a host replaced) is left as it is.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None, registry: Registry[Source] | None = None) -> int:
    """Run the command; `registry` is the sources to use, which tests give (`discover()` finds them otherwise)."""
    _utf8_console()
    parser = argparse.ArgumentParser(
        prog="leadline", description="Daily adoption and health metrics for software projects."
    )
    parser.add_argument("--version", action="version", version=f"leadline {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command", required=True)
    run = commands.add_parser("run", help="collect every enabled source once")
    run.add_argument("--config", required=True, type=Path, help="the JSON config file")
    run.add_argument("--only", type=_names, metavar="a,b", help="run only these sources, from the config")
    for key, (flag, help_text) in SETTINGS.items():
        run.add_argument(flag, dest=key, help=f"{help_text} (overrides {key} in the config)")
    report = commands.add_parser("report", help="rebuild the reports, summary and trends from the output files alone")
    report.add_argument("--config", required=True, type=Path, help="the JSON config file")
    report.add_argument("--out", dest="out_dir", help=f"{SETTINGS['out_dir'][1]} (overrides out_dir in the config)")
    default = commands.add_parser("default-reports", help="print the default summary as a reports setting to paste")
    default.add_argument("--config", required=True, type=Path, help="the JSON config file")
    copy = commands.add_parser("import", help="copy the jsonl row files of a folder into the configured database")
    copy.add_argument("--config", required=True, type=Path, help="the JSON config file")
    copy.add_argument("--from", dest="from_dir", type=Path, help="the folder of row files (default: out_dir)")
    copy.add_argument(
        "--max-values", type=int, help="stop after about this many values, to be continued by the next run"
    )
    export = commands.add_parser("export", help="copy the configured database into jsonl row files in a folder")
    export.add_argument("--config", required=True, type=Path, help="the JSON config file")
    export.add_argument("--to", dest="to_dir", required=True, type=Path, help="the folder to write the row files in")
    diff = commands.add_parser("diff", help="compare the row files in two folders, ignoring captured_at")
    diff.add_argument("a", type=Path, help="the reference folder, e.g. the script's")
    diff.add_argument("b", type=Path, help="the folder to check against it, e.g. leadline's")
    args = parser.parse_args(argv)

    if args.command == "diff":
        return _diff(args.a, args.b)
    try:
        if args.command == "default-reports":
            return _default_reports(load(args.config, {}), registry)
        if args.command == "export":
            return _export(load(args.config, {}), args.to_dir)
        if args.command == "import":
            return _import(load(args.config, {}), args.from_dir, args.max_values)
        if args.command == "report":
            return runner.report(load(args.config, {"out_dir": args.out_dir}), registry=registry)
        config = load(args.config, {key: getattr(args, key) for key in SETTINGS})
        return runner.run(config, only=args.only, registry=registry)
    except ConfigError as e:
        print(f"leadline: error: {e}", file=sys.stderr)
        return 2


def _default_reports(config: Config, registry: Registry[Spec] | None) -> int:
    registry = discover(specs_only=True) if registry is None else registry
    runner.validated(config, registry=registry)
    print(json.dumps({"reports": default_reports(config, registry)}, indent=2, ensure_ascii=False))
    return 0


def _import(config: Config, folder: Path | None, max_values: int | None) -> int:
    if config.storage.backend == "jsonl":
        raise ConfigError("storage is jsonl: set a database in the config's storage setting to import into it")
    folder = config.out_dir if folder is None else folder
    if not folder.is_dir():
        raise ConfigError(f"not a folder: {folder}")
    if max_values is not None and max_values < 1:
        raise ConfigError("--max-values must be at least 1")
    try:
        done = storage.import_files(
            Jsonl(folder),
            storage.open_backend(config.storage, config.out_dir),
            skip=(HEALTH_FILE,),
            max_values=max_values,
        )
    except StorageError as e:
        raise ConfigError(f"storage: {e}") from e
    left = f"{done.remaining} line(s) remain" if done.remaining else "everything is imported"
    print(f"Imported {done.lines} line(s) in {done.files} file(s) ({done.values} values) from {folder}; {left}")
    return 0


def _export(config: Config, folder: Path) -> int:
    if config.storage.backend == "jsonl":
        raise ConfigError("storage is jsonl: its row files are already jsonl files in out_dir")
    try:
        source, target = storage.open_backend(config.storage, config.out_dir), Jsonl(folder)
        _refuse_held(source, target, "folder")
        files, lines = storage.copy_files(source, target)
    except StorageError as e:
        raise ConfigError(f"storage: {e}") from e
    print(f"Exported {files} file(s), {lines} line(s) from the {config.storage.backend} database into {folder}")
    return 0


def _refuse_held(source: Backend, target: Backend, where: str) -> None:
    """Stop when `target` holds a file `source` has: copying replaces it, which would erase the nights it has since."""
    held = sorted(set(source.files()) & set(target.files()))
    if held:
        raise ConfigError(f"the {where} already holds {', '.join(held)}; copy into an empty {where}")


def _diff(a: Path, b: Path) -> int:
    for folder in (a, b):
        if not folder.is_dir():
            print(f"leadline: error: not a folder: {folder}", file=sys.stderr)
            return 2
    found = rowdiff.differences(a, b)
    print(f"A = {a}")
    print(f"B = {b}")
    for lines in found.values():
        for line in lines:
            print(line)
    count = len(set(rowdiff.row_files(a)) | set(rowdiff.row_files(b)))
    total = sum(len(lines) for lines in found.values())
    where = ", ".join(f"{name} ({len(lines)})" for name, lines in found.items())
    outcome = f"{total} difference(s), in {where}" if found else "identical, ignoring captured_at"
    print(f"{count} file(s) compared: {outcome}")
    return 1 if found else 0
