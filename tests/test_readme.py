"""The README's examples are true: its starter config runs, its summary and trends examples are what a run of it
writes, and its links (and CONTRIBUTING.md's) resolve.

Everything is read out of README.md, as tests/test_adding_a_source.py reads its guide, so the README
can't drift from the code.
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest

from leadline import config, reports, runner, trends
from leadline.config import Config
from leadline.context import Context
from leadline.http import Http
from leadline.sources import discover

README = Path(__file__).parent.parent / "README.md"
REGISTRY = discover().sources


def section(title: str) -> str:
    """The README's `## <title>` section, up to the next `## ` heading outside a code block."""
    sections: dict[str, list[str]] = {}
    lines: list[str] = []
    fence = False
    for line in README.read_text(encoding="utf-8").splitlines(keepends=True):
        fence ^= line.startswith("```")
        if line.startswith("## ") and not fence:
            lines = sections.setdefault(line[3:].strip(), [])
        else:
            lines.append(line)
    assert title in sections, f"the README has a section {title!r}"
    return "".join(sections[title])


def fenced(text: str, lang: str) -> list[str]:
    return re.findall(rf"^```{lang}\n(.*?)^```", text, re.M | re.S)


def starter(tmp_path: Path) -> config.Config:
    """The quick start's config, saved as leadline.json in tmp_path and loaded as `leadline run` loads it."""
    blocks = [b for b in fenced(section("Quick start"), "json") if '"sources"' in b]
    assert len(blocks) == 1, "the quick start has one config"
    path = tmp_path / "leadline.json"
    path.write_text(blocks[0], encoding="utf-8")
    return config.load(path, {})


def test_the_starter_config_is_valid_and_needs_at_most_a_github_token(tmp_path: Path) -> None:
    cfg = starter(tmp_path)

    names, _ = runner.validated(cfg, registry=discover())

    assert names
    assert {var for name in names for var in REGISTRY[name].ENV} <= {"GITHUB_TOKEN"}


def test_the_starter_config_leaves_the_reports_to_the_default_summary(tmp_path: Path) -> None:
    assert starter(tmp_path).reports is None


def test_the_quick_start_names_every_file_the_starter_config_writes(tmp_path: Path) -> None:
    cfg = starter(tmp_path)
    text = section("Quick start")
    written = [file for name, entry in cfg.sources.items() for file in REGISTRY[name].files(entry).values()]

    for file in [*written, "health.jsonl", "leadline.log", reports.SUMMARY["file"], trends.DATA, trends.PAGE]:
        assert f"`{file}`" in text or f"/{file}`" in text, f"the quick start names {file}"


def daily_rows(last: date) -> list[dict[str, Any]]:
    """Three weeks of made-up github_traffic_daily rows ending on `last`, as a week of runs leaves them."""
    views = [14, 19, 11, 23, 17, 9, 13, 21, 18, 25, 16, 12, 20, 24, 22, 27, 19, 15, 26, 30, 23]
    clones = [3, 2, 4, 1, 5, 2, 3, 4, 3, 2, 6, 1, 3, 4, 5, 3, 4, 2, 6, 5, 4]
    first = last - timedelta(days=len(views) - 1)
    return [
        {
            "date": (first + timedelta(days=n)).isoformat(),
            "views": v,
            "views_unique": 1,
            "clones": c,
            "clones_unique": 1,
        }
        for n, (v, c) in enumerate(zip(views, clones))
    ]


def test_the_summary_and_trends_examples_are_what_a_run_of_the_starter_config_writes_after_a_week(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row shown, a made-up row a week before it, and three weeks of daily traffic give exactly the summary shown,
    and a trends.json holding the section shown.

    The run is a real one, its sources skipped for want of a token, on the row's date.
    """
    cfg = starter(tmp_path)
    text = section("What you get")
    (row,) = [json.loads(block) for block in fenced(text, "json") if '"captured_at"' in block]
    (excerpt,) = [json.loads(block) for block in fenced(text, "json") if '"fields"' in block]
    day = datetime.strptime(row["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    week_before = {**row, "date": (day - timedelta(days=7)).strftime("%Y-%m-%d"), "stars": 36, "views_14d": 187}
    files = {name: REGISTRY[name].files(entry)["[0]"] for name, entry in cfg.sources.items()}
    cfg.out_dir.mkdir(parents=True)
    seed(cfg.out_dir / files["github_repo_stats"], [week_before, row])
    seed(cfg.out_dir / files["github_traffic_daily"], daily_rows(day.date() - timedelta(days=1)))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    @contextmanager
    def on_the_rows_date(config: Config) -> Iterator[Context]:
        yield Context(now=day, out_dir=config.out_dir, log=logging.getLogger("leadline"), http=Http("test/1.0"))

    status = runner.run(cfg, context=on_the_rows_date)

    (shown,) = fenced(text, "markdown")
    assert status == 0
    assert (cfg.out_dir / reports.SUMMARY["file"]).read_text(encoding="utf-8") == shown
    assert excerpt in json.loads((cfg.out_dir / trends.DATA).read_text(encoding="utf-8"))["sections"]


def seed(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_every_relative_link_resolves() -> None:
    root = README.parent
    for doc in (README, root / "CONTRIBUTING.md"):
        text = doc.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
            if not re.match(r"[a-z]+:", target):
                assert (root / target).exists(), f"{doc.name} links to {target}"
