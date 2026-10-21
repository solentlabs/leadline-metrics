"""Run a copy of collect-metrics.sh against saved responses; the rows it writes are the expected rows.

The script is the `script` local setting (tests/oracle/local_settings.py), and is never modified or
run in place: a copy runs in a temporary folder, with its BASE_DIR pointed there, `curl`, `date` and
`sleep` replaced by shims, and every Python it starts running under shim.install(). It gets no
credentials unless the case gives dummy ones. A case is a folder laid out as docs/TESTING.md,
"Case folders", describes; tests/scenarios/ writes each scenario as one.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, Union

import local_settings
import pytest

ORACLE = Path(__file__).resolve().parent
BASE_DIR = re.compile(r'BASE_DIR="([^"]+)"')  # the script's one line that sets where it writes
MAIN_LINE = 'main "$@"'
# The first line after the script's constants; a case's config overrides go just above it.
DATE_LINE = "DATE=$(date +%Y-%m-%d)"
TZ = "UTC"  # the zone a case's `now` is local time in, unless the case sets `tz`

# The script, its Python heredocs, these shims and the capture add-on run on Linux. A test that runs any of
# them skips on Windows, where the shims (#!/bin/sh files on PATH) don't run and the heredocs write CRLF.
script_side = pytest.mark.skipif(sys.platform == "win32", reason="runs the script's code, which runs on Linux")

Config = Mapping[str, Union[str, Sequence[str]]]


@dataclass
class Run:
    metrics: Path  # the script's metrics folder: its row files and collect-metrics.log
    returncode: int
    output: str  # stdout and stderr, interleaved
    unmatched: list[str]  # requests that had no saved response, "METHOD URL"


def _overrides(lines: list[str], config: Config) -> list[str]:
    """Bash assignments for `config`: a string sets a constant, a list sets an array."""
    assignments = []
    for name, value in config.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise ValueError(f"config: {name!r} is not a constant's name")
        if not any(line.startswith(f"{name}=") for line in lines):
            raise ValueError(f"config: the script defines no constant {name}")
        quoted = shlex.quote(value) if isinstance(value, str) else f"({' '.join(map(shlex.quote, value))})"
        assignments.append(f"{name}={quoted}")
    if assignments and lines.count(DATE_LINE) != 1:
        raise ValueError(f"config: expected one {DATE_LINE!r} line to put the overrides above")
    return assignments


def prepare(script: str, base_dir: Path, calls: Sequence[str], config: Config | None = None) -> str:
    """The script, rewritten to write under `base_dir` and to run `calls` in place of main.

    Each call runs as main runs a collector, `CALL || true`. `config` overrides
    the script's constants (a string or a list of strings each), set after
    them and before anything the script derives from them. Refuses a script
    that could still reach the real community folder (its own BASE_DIR, a NAS
    volume or a mounted drive, outside comments) or bypass the shims.
    """
    lines = script.splitlines()
    base_dirs = [(i, found.group(1)) for i, found in enumerate(map(BASE_DIR.fullmatch, lines)) if found]
    if len(base_dirs) != 1 or lines[-1] != MAIN_LINE:
        raise ValueError(f'expected one BASE_DIR="..." line and a final {MAIN_LINE!r} line')
    at, real_base_dir = base_dirs[0]
    assignments = _overrides(lines, config or {})
    lines[at] = f'BASE_DIR="{base_dir}"'
    lines[-1:] = [f"{call} || true" for call in calls]
    if assignments:
        date_index = lines.index(DATE_LINE)
        lines[date_index:date_index] = assignments
    for line in lines:
        code = line.strip()
        if code.startswith("#"):
            continue
        if real_base_dir in code or re.search(r"/volume\d|/mnt/", code):
            raise ValueError(f"script still names a real path: {code}")
        if re.search(r"/(curl|date|sleep)\b", code) or re.search(r"\bpython3\s+-[a-zA-Z]*[IES]", code):
            raise ValueError(f"script could bypass the shims: {code}")
    return "\n".join(lines) + "\n"


def _write_command(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def environment(work: Path, responses: Path, now: str, env: dict[str, str], tz: str = TZ) -> dict[str, str]:
    """The script's whole environment: shims first on PATH, no inherited credentials, the zone `tz`, plus `env`."""
    bin_dir, state = work / "bin", work / "state"
    for folder in (bin_dir, state, work / "home", work / "tmp"):
        folder.mkdir(parents=True, exist_ok=True)
    for command in ("curl", "date"):
        _write_command(bin_dir / command, f'#!/bin/sh\nexec "{sys.executable}" "{ORACLE / "shim.py"}" {command} "$@"\n')
    _write_command(bin_dir / "sleep", "#!/bin/sh\nexit 0\n")
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(work / "home"),
        "TMPDIR": str(work / "tmp"),
        "TZ": tz,
        "LANG": "C.UTF-8",
        "PYTHONPATH": f"{ORACLE / 'site'}{os.pathsep}{ORACLE}",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LEADLINE_ORACLE_NOW": now,
        "LEADLINE_ORACLE_RESPONSES": str(responses),
        "LEADLINE_ORACLE_STATE": str(state),
        **env,
    }


def script_path() -> Path:
    """The real script: the `script` local setting."""
    return local_settings.path("script")


def run_script(case: Path, work: Path, script: Path | None = None) -> Run:
    """Run the script's copy for `case` inside `work`.

    `case/case.json` gives `now` (naive ISO time for the clock), `calls` (the
    bash lines to run, e.g. `collect_hacs`, or `main`), and optionally `tz`
    (the zone `now` is local time in, TZ when absent), `env` (dummy
    credentials) and `config` (overrides for the script's constants).
    `case/seed/`, when present, is copied into the metrics folder first.
    """
    spec = json.loads((case / "case.json").read_text(encoding="utf-8"))
    base_dir = work / "community"
    metrics = base_dir / "metrics"
    metrics.mkdir(parents=True)
    if (case / "seed").is_dir():
        shutil.copytree(case / "seed", metrics, dirs_exist_ok=True)
    copy = work / "collect-metrics.sh"
    text = (script or script_path()).read_text(encoding="utf-8")
    prepared = prepare(text, base_dir, spec["calls"], spec.get("config"))
    copy.write_text(prepared, encoding="utf-8")
    env = environment(work, case / "responses", spec["now"], spec.get("env", {}), spec.get("tz", TZ))

    done = subprocess.run(
        ["bash", str(copy)],
        cwd=work,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=300,
        check=False,
    )
    unmatched_log = work / "state" / "unmatched.log"
    unmatched = unmatched_log.read_text(encoding="utf-8").splitlines() if unmatched_log.exists() else []
    return Run(metrics, done.returncode, done.stdout, unmatched)
