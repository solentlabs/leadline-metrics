"""The maintainer's reports, as config, give what the script's report writers give (ADR: reports are config-driven
marked blocks).

Marked oracle: it needs the real script, the real metrics folder beside it, and the maintainer's leadline config
(the `leadline_config` local setting), whose `reports` it runs. Both sides work on copies; the real folder is
only read. The script runs its freshness check and its report writers on a copy of the real row files, through
the oracle harness; leadline's engine then runs the config on a copy of what the script left, so both read the
same rows, the health row included.

The config has one file of lines blocks, the lines file: the counterpart of the script's SCRIPT_LINES, whose name
may differ. Where it has several, the lines file is the one with the `critical` block, and the others are
leadline's own reports, which the script has nothing to match and the test leaves alone. Both sides start from
the real report files with any leadline marker lines taken out, so the test holds whether or not they carry
markers; the script's side holds the lines file under the script's name. Each report file is prepared as its
first leadline write would find it: a file of table blocks is that text with markers put around the tables the
blocks name (found by their header line), and the lines file starts empty, since the script
rewrites it whole. Then:

- a file of table blocks, once its marker lines are taken out, is the script's file, byte for byte: the tables
  match and nothing outside them is lost;
- the lines file holds, in its blocks with something to show, in order and a blank line apart, the script's
  SCRIPT_LINES.

In the lines file only the differences in DECIDED may remain, lines that match one of its patterns left
out on both sides, and the script's headings over nothing (EMPTY_SECTION).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import local_settings
import pytest
import script_harness
from row_files import file_at

from leadline import reports, store
from leadline.context import Context
from leadline.http import Http

pytestmark = pytest.mark.oracle

CALLS = ["check_freshness", "update_legacy_table", "update_support_table", "update_trends_file"]
# The file the script's lines writer rewrites whole, relative to the metrics folder.
SCRIPT_LINES = "../TRENDS.md"

# The differences a decision calls for, each a pattern for the lines it touches and the decision.
DECIDED = {
    r"^- Stale: ": "stale is health's term, where each source says whether it lags (ARCHITECTURE, 'Terms')",
    r"^- (?:Script|leadline) ": "the version line names what generated the page, leadline or the script",
}
# A block with nothing to show is only its markers (ADR: reports are config-driven marked blocks), where the
# script writes some sections' headings over nothing.
EMPTY_SECTION = re.compile(r"\n## [^\n]*\n\n(?=\n## |\Z)")


def _decided(text: str) -> list[str]:
    lines = EMPTY_SECTION.sub("", text).splitlines()
    return [line for line in lines if not any(re.search(pattern, line) for pattern in DECIDED)]


def _config() -> list[dict[str, Any]]:
    loaded = json.loads(local_settings.path("leadline_config").read_text(encoding="utf-8"))
    cfg: list[dict[str, Any]] = loaded["reports"]
    assert reports.validate(cfg) == []
    return cfg


def _by_file(cfg: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    files: dict[str, list[Mapping[str, Any]]] = {}
    for entry in cfg:
        files.setdefault(entry["file"], []).append(entry)
    return files


def _now(metrics: Path) -> str:
    """The run's frozen time: late on the newest date any row file holds, as the nightly run is."""
    newest = max(
        (day for path in metrics.glob("*.jsonl") for day in store.rows_by_date(file_at(path)) if store.parse_date(day)),
    )
    return f"{newest}T23:45:00"


def _header(table: Mapping[str, Any]) -> str:
    return "| " + " | ".join(column["title"] for column in table["columns"]) + " |"


def _marked(text: str, entries: Sequence[Mapping[str, Any]]) -> str:
    """`text` with each table block's markers around the table whose header line it writes."""
    lines = text.splitlines(keepends=True)
    for entry in entries:
        start = next(i for i, line in enumerate(lines) if line.strip() == _header(entry["table"]))
        end = start
        while end < len(lines) and lines[end].strip().startswith("|"):
            end += 1
        name = entry["block"]
        lines[start:end] = [f"<!-- leadline:start {name} -->\n", *lines[start:end], f"<!-- leadline:end {name} -->\n"]
    return "".join(lines)


def _blocks(text: str) -> list[str]:
    """The text of each block in `text` with something to show, markers left out, in order."""
    found = re.findall(r"^<!-- leadline:start (\S+) -->\n(.*?)^<!-- leadline:end \1 -->\n", text, re.M | re.S)
    return [body for _, body in found if body]


def _unmarked(text: str) -> str:
    return re.sub(r"^<!-- leadline:(?:start|end) \S+ -->\n", "", text, flags=re.M)


def _without_critical(text: str) -> str:
    """`text` without the critical lines: the script has none, and last night's would be carried into its file."""
    return re.sub(r"^## Critical\n.*?(?=^# )", "", text, flags=re.M | re.S)


def test_the_reports_config_gives_the_scripts_reports(tmp_path: Path) -> None:
    script = script_harness.script_path()
    real = script.parent / "metrics"
    files = _by_file(_config())
    cfg = [entry for entries in files.values() for entry in entries]
    lines_files = [file for file, entries in files.items() if not all("table" in entry for entry in entries)]
    if len(lines_files) > 1:  # the others are leadline's own reports, which the script has no counterpart of
        lines_files = [file for file in lines_files if any(entry["block"] == "critical" for entry in files[file])]
    assert len(lines_files) == 1, f"the config has {len(lines_files)} files of lines blocks, the script one"
    lines_file = lines_files[0]
    script_name = {lines_file: SCRIPT_LINES}
    own = [file for file in files if file != lines_file and not all("table" in entry for entry in files[file])]
    files = {file: entries for file, entries in files.items() if file not in own}

    case = tmp_path / "case"
    shutil.copytree(real, case / "seed", ignore=lambda _, names: [n for n in names if not n.endswith(".jsonl")])
    (case / "responses").mkdir()
    (case / "case.json").write_text(json.dumps({"now": _now(case / "seed"), "calls": CALLS}), encoding="utf-8")
    work = tmp_path / "script"
    (work / "community").mkdir(parents=True)
    start = {file: _unmarked((real / file).read_text(encoding="utf-8")) for file in files if (real / file).exists()}
    for file, text in start.items():
        seeded = Path(os.path.normpath(work / "community" / "metrics" / script_name.get(file, file)))
        seeded.write_text(_without_critical(text) if file == lines_file else text, encoding="utf-8")
    done = script_harness.run_script(case, work)
    assert done.returncode == 0, done.output
    assert done.unmatched == []

    out = tmp_path / "leadline" / "community" / "metrics"
    shutil.copytree(done.metrics, out, ignore=lambda _, names: [n for n in names if not n.endswith(".jsonl")])
    for file, entries in files.items():
        if all("table" in entry for entry in entries):
            (out / file).write_text(_marked(start[file], entries), encoding="utf-8")
    ctx = Context(
        now=datetime.now(timezone.utc), out_dir=out, log=logging.getLogger("leadline"), http=Http("oracle/1.0")
    )
    reports.write(ctx, cfg)
    assert ctx.critical == []

    for file in files:
        expected = (done.metrics / script_name.get(file, file)).read_text(encoding="utf-8")
        text = (out / file).read_text(encoding="utf-8")
        if file == lines_file:
            assert _decided("\n".join(_blocks(text))) == _decided(expected), file
        else:
            assert _unmarked(text) == expected, file
