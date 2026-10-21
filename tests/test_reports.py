"""reports: marked blocks in Markdown files, each a table or lines built from the row files (ADR: reports are
config-driven marked blocks).

Every file and value here is made up.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import port
import pytest

from leadline import reports
from leadline.config import Hide, Section, Shown
from leadline.context import Context, Critical
from leadline.http import Http

NOW = datetime(2026, 9, 24, 23, 30, tzinfo=timezone.utc)


def context(folder: Path) -> Context:
    folder.mkdir(parents=True, exist_ok=True)
    return Context(now=NOW, out_dir=folder, log=logging.getLogger("leadline"), http=Http("test/1.0"))


def seed(folder: Path, name: str, rows: Iterable[Mapping[str, Any]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def daily(field: str, values: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A row per date, each with `field`."""
    return [{"date": day, field: value} for day, value in values.items()]


def run(ctx: Context, entries: list[dict[str, Any]]) -> list[str]:
    """Validate the report config (it must be valid), write it, and return the run's log lines."""
    assert reports.validate(entries) == []
    lines: list[str] = []
    with port.captured_log(lines):
        reports.write(ctx, entries)
    return lines


def lines_block(name: str, lines: list[dict[str, Any]], **entry: Any) -> dict[str, Any]:
    return {"file": "report.md", "block": name, "place": "bottom", "lines": lines, **entry}


def block_text(name: str, *body: str, heading: str | None = None) -> str:
    """A block as the engine writes it: its markers, its heading and a blank line, and its body."""
    head = [f"## {heading}", ""] if heading is not None else []
    return "\n".join([f"<!-- leadline:start {name} -->", *head, *body, f"<!-- leadline:end {name} -->"]) + "\n"


def written(ctx: Context, name: str = "report.md") -> str:
    return (ctx.out_dir / name).read_text(encoding="utf-8")


def rendered(tmp_path: Path, line: dict[str, Any]) -> list[str]:
    """The lines one line entry gives, from the files already in tmp_path/out."""
    ctx = context(tmp_path / "out")
    run(ctx, [lines_block("b", [line])])
    text = written(ctx)
    return text.splitlines()[1:-1]


# ---- Blocks in a file ----------------------------------------------------------------------------


def test_a_file_that_does_not_exist_is_created_with_the_block(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 5}])

    run(ctx, [lines_block("n", [{"label": "N", "from": "a.jsonl", "field": "n"}], heading="Counts")])

    assert written(ctx) == block_text("n", "- N: 5", heading="Counts")


@pytest.mark.parametrize(
    ("place", "expected"),
    [
        ("bottom", "# Title\n\nKept text.\n\n" + block_text("n", "- N: 5")),
        ("top", block_text("n", "- N: 5") + "\n# Title\n\nKept text.\n"),
    ],
)
def test_a_new_block_goes_at_the_place_the_config_gives(tmp_path: Path, place: str, expected: str) -> None:
    ctx = context(tmp_path)
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 5}])
    (tmp_path / "report.md").write_text("# Title\n\nKept text.\n", encoding="utf-8")

    run(ctx, [lines_block("n", [{"label": "N", "from": "a.jsonl", "field": "n"}], place=place)])

    assert written(ctx) == expected


def test_new_blocks_keep_the_config_order_at_the_top_and_at_the_bottom(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 5}])
    (tmp_path / "report.md").write_text("Middle\n", encoding="utf-8")
    line = [{"label": "N", "from": "a.jsonl", "field": "n"}]

    run(ctx, [lines_block(name, line, place=place) for name, place in [("a", "top"), ("b", "bottom"), ("c", "top")]])

    a, b, c = (block_text(name, "- N: 5") for name in "abc")
    assert written(ctx) == f"{a}\n{c}\nMiddle\n\n{b}"


def test_an_existing_block_is_rewritten_where_it_is_and_nothing_else_changes(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 6}])
    before = "Intro  \n\n" + block_text("n", "- N: 5", heading="Old") + "\nOutro, no final newline"
    (tmp_path / "report.md").write_text(before, encoding="utf-8")

    run(ctx, [lines_block("n", [{"label": "N", "from": "a.jsonl", "field": "n"}], place="top", heading="New")])

    assert written(ctx) == "Intro  \n\n" + block_text("n", "- N: 6", heading="New") + "\nOutro, no final newline"


def test_a_file_with_windows_line_ends_keeps_them(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 6}])
    (tmp_path / "report.md").write_bytes(b"Intro\r\n\r\n")

    run(ctx, [lines_block("n", [{"label": "N", "from": "a.jsonl", "field": "n"}])])

    expected = "Intro\r\n\r\n\r\n" + block_text("n", "- N: 6").replace("\n", "\r\n")
    assert (tmp_path / "report.md").read_bytes() == expected.encode()


def test_a_block_with_nothing_to_show_is_only_its_markers(tmp_path: Path) -> None:
    """A heading over nothing would say there is something: a critical block on a night with none shows nothing."""
    ctx = context(tmp_path)

    run(ctx, [lines_block("n", [{"label": "N", "from": "missing.jsonl", "field": "n"}], heading="Counts")])

    assert written(ctx) == block_text("n")


def test_the_write_replaces_the_file_whole_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 6}])

    run(ctx, [lines_block("n", [{"label": "N", "from": "a.jsonl", "field": "n"}])])

    assert sorted(path.name for path in tmp_path.iterdir()) == ["a.jsonl", "report.md"]


def test_a_block_can_be_in_a_file_outside_the_output_folder(tmp_path: Path) -> None:
    """`file` is relative to the output folder, so --out moves the reports with the rows."""
    ctx = context(tmp_path / "metrics")
    seed(ctx.out_dir, "a.jsonl", [{"date": "2026-09-24", "n": 6}])

    run(ctx, [lines_block("n", [{"label": "N", "from": "a.jsonl", "field": "n"}], file="../report.md")])

    assert (tmp_path / "report.md").read_text(encoding="utf-8") == block_text("n", "- N: 6")


BROKEN = {
    "two_starts": (
        block_text("n", "- old") + "<!-- leadline:start n -->\n",
        "block n: its start marker is in report.md 2 times",
    ),
    "two_ends": (
        block_text("n", "- old") + "<!-- leadline:end n -->\n",
        "block n: its end marker is in report.md 2 times",
    ),
    "start_only": ("<!-- leadline:start n -->\n- old\n", "block n: its end marker is in report.md 0 times"),
    "end_only": ("- old\n<!-- leadline:end n -->\n", "block n: its start marker is in report.md 0 times"),
    "end_first": (
        "<!-- leadline:end n -->\n- old\n<!-- leadline:start n -->\n",
        "block n: its end marker comes before its start marker in report.md",
    ),
    "another_inside": (
        "<!-- leadline:start n -->\n" + block_text("other", "- kept") + "<!-- leadline:end n -->\n",
        "block n: holds another block's marker in report.md",
    ),
}


@pytest.mark.parametrize(("text", "reason"), BROKEN.values(), ids=BROKEN.keys())
def test_a_broken_marker_is_an_error_for_its_block_which_is_left_as_it_is(
    tmp_path: Path, text: str, reason: str
) -> None:
    """Never a guess that could eat the user's text: the block is left, critical, and the other blocks written."""
    ctx = context(tmp_path)
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 6}])
    (tmp_path / "report.md").write_text(text, encoding="utf-8")
    line = [{"label": "N", "from": "a.jsonl", "field": "n"}]

    log = run(ctx, [lines_block("n", line), lines_block("fine", line)])

    assert written(ctx) == text + "\n" + block_text("fine", "- N: 6")
    assert ctx.critical == [Critical("reports", "report.md", reason)]
    assert f"ERROR: reports is critical (report.md): {reason}" in log


def test_a_block_that_crashes_is_critical_and_the_others_are_written(tmp_path: Path) -> None:
    """An exception building a block is a bug (here a delta of text): critical, logged, and the rest still written."""
    ctx = context(tmp_path)
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-23", "n": "x"}, {"date": "2026-09-24", "n": "y"}])
    seed(tmp_path, "b.jsonl", [{"date": "2026-09-24", "n": 6}])

    log = run(
        ctx,
        [
            lines_block("bad", [{"label": "N", "from": "a.jsonl", "field": "n", "compare": "yesterday"}]),
            lines_block("good", [{"label": "N", "from": "b.jsonl", "field": "n"}]),
        ],
    )

    assert written(ctx) == block_text("good", "- N: 6")
    assert [(c.source, c.file) for c in ctx.critical] == [("reports", "report.md")]
    assert ctx.critical[0].reason.startswith("block bad crashed: TypeError: ")
    assert any(line.startswith("ERROR: block bad in report.md crashed: TypeError: ") for line in log)


def test_a_file_that_cannot_be_read_fails_its_blocks_and_not_the_other_files(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 6}])
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe not utf-8\n")
    line = [{"label": "N", "from": "a.jsonl", "field": "n"}]

    run(ctx, [lines_block("n", line, file="bad.md"), lines_block("n", line, file="good.md")])

    assert (tmp_path / "bad.md").read_bytes() == b"\xff\xfe not utf-8\n"
    assert written(ctx, "good.md") == block_text("n", "- N: 6")
    assert len(ctx.critical) == 1
    assert ctx.critical[0].file == "bad.md"
    assert ctx.critical[0].reason.startswith("block n: can't read bad.md: ")


def test_a_value_cannot_break_a_line_or_forge_a_marker(tmp_path: Path) -> None:
    seed(tmp_path / "out", "a.jsonl", [{"date": "2026-09-24", "t": "one\n<!-- leadline:end b -->\ntwo"}])

    assert rendered(tmp_path, {"label": "T", "from": "a.jsonl", "field": "t"}) == [
        "- T: one &lt;!-- leadline:end b --> two"
    ]


def test_a_value_that_is_a_whole_line_cannot_forge_a_marker_the_next_run_trips_on(tmp_path: Path) -> None:
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "t": "<!-- leadline:end n -->"}])
    entry = lines_block("n", [{"from": "a.jsonl", "field": "t", "text": "{row.t}"}])
    ctx = context(tmp_path)
    run(ctx, [entry])
    first = written(ctx)

    again = context(tmp_path)
    run(again, [entry])

    assert again.critical == []
    assert written(again) == first
    assert first.count("<!-- leadline:end n -->") == 1


def test_a_byte_order_mark_at_the_start_of_the_file_is_kept_and_does_not_hide_a_marker(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 6}])
    old = block_text("n", "- N: 5")
    (tmp_path / "report.md").write_bytes(b"\xef\xbb\xbf" + old.encode() + b"Notes\n")

    run(ctx, [lines_block("n", [{"label": "N", "from": "a.jsonl", "field": "n"}])])

    assert ctx.critical == []
    assert (tmp_path / "report.md").read_bytes() == b"\xef\xbb\xbf" + block_text("n", "- N: 6").encode() + b"Notes\n"


def test_a_lone_surrogate_in_a_row_is_shown_as_the_replacement_character(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "a.jsonl").write_text('{"date": "2026-09-24", "t": "a\\ud800b"}\n', encoding="utf-8")

    assert rendered(tmp_path, {"label": "T", "from": "a.jsonl", "field": "t"}) == ["- T: a\ufffdb"]


def test_a_heading_cannot_break_a_line_either(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 6}])

    run(ctx, [lines_block("n", [{"label": "N", "from": "a.jsonl", "field": "n"}], heading="Two\nlines")])

    assert written(ctx) == block_text("n", "- N: 6", heading="Two lines")


# ---- Lines ---------------------------------------------------------------------------------------

TOTALS = {"2026-09-10": 80, "2026-09-16": 90, "2026-09-17": 96, "2026-09-22": 97, "2026-09-23": 100, "2026-09-24": 101}


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ({"label": "Now", "field": "total"}, "- Now: 101"),
        ({"label": "Now", "field": "total", "compare": "yesterday"}, "- Now: 101 (+1 vs yesterday)"),
        ({"label": "Now", "field": "total", "compare": "7d"}, "- Now: 101 (+5 vs 7d ago)"),  # at or before 09-17
        ({"label": "Now", "field": "total", "compare": "30d"}, "- Now: 101"),  # no row that old
        ({"label": "Last days", "field": "total", "series": 3}, "- Last days: 97 100 101"),
        ({"label": "Last days", "field": "total", "series": 9}, "- Last days: 80 90 96 97 100 101"),
        ({"label": "Weekly", "field": "total", "series": 3, "every": 7}, "- Weekly: 80 96 101"),
        ({"label": "Peak", "field": "total", "aggregate": "peak"}, "- Peak: 101 on 2026-09-24"),
        (
            {"label": "Sum", "field": "total", "aggregate": "sum", "count": 2},
            "- Sum: 201 through 2026-09-24 (prior 193)",
        ),
        (  # the prior window isn't complete: 2 rows of 4
            {"label": "Sum", "field": "total", "aggregate": "sum", "count": 4},
            "- Sum: 394 through 2026-09-24",
        ),
        (
            {"label": "Sum", "field": "total", "aggregate": "sum", "count": 3},
            "- Sum: 298 through 2026-09-24 (prior 266)",
        ),
        ({"label": "Sum", "field": "total", "aggregate": "sum", "count": 6}, "- Sum: 564 through 2026-09-24"),
        ({"label": "Sum", "field": "total", "aggregate": "sum", "count": 7}, None),  # fewer rows than it sums
    ],
)
def test_a_line_shows_a_value_a_comparison_or_a_series(tmp_path: Path, line: dict[str, Any], expected: str) -> None:
    seed(tmp_path / "out", "t.jsonl", daily("total", TOTALS))

    assert rendered(tmp_path, {"from": "t.jsonl", **line}) == ([expected] if expected else [])


def test_complement_shows_100_less_the_value_and_compares_it(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", daily("p", {"2026-09-23": 27.1, "2026-09-24": 26.6}))
    line = {"label": "Ahead", "from": "t.jsonl", "field": "p", "complement": True, "format": ".1f"}

    assert rendered(tmp_path, {**line, "compare": "yesterday"}) == ["- Ahead: 73.4 (+0.5 vs yesterday)"]


def test_complement_leaves_out_a_line_whose_value_is_not_a_number(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", [{"date": "2026-09-24", "p": "high"}])

    assert rendered(tmp_path, {"label": "Ahead", "from": "t.jsonl", "field": "p", "complement": True}) == []


def test_the_peak_is_its_first_day(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", daily("n", {"2026-09-20": 3, "2026-09-21": 7, "2026-09-22": 7, "2026-09-23": 2}))

    assert rendered(tmp_path, {"label": "Peak", "from": "t.jsonl", "field": "n", "aggregate": "peak"}) == [
        "- Peak: 7 on 2026-09-21"
    ]


def test_a_format_applies_to_every_number_a_line_shows(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", daily("h", {"2026-09-23": 40.25, "2026-09-24": 41.5}))

    assert rendered(tmp_path, {"label": "H", "from": "t.jsonl", "field": "h", "compare": "yesterday", "format": ".1f"})[
        0
    ] == ("- H: 41.5 (+1.2 vs yesterday)")


def test_a_field_can_be_nested(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", [{"date": "2026-09-24", "issues": {"open": 4}}])

    assert rendered(tmp_path, {"label": "Open", "from": "t.jsonl", "field": "issues.open"}) == ["- Open: 4"]


def test_a_line_whose_value_is_missing_is_left_out(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", [{"date": "2026-09-24", "issues": {}}])

    assert rendered(tmp_path, {"label": "Open", "from": "t.jsonl", "field": "issues.open"}) == []


def test_failure_rows_are_skipped_and_the_line_says_how_old_its_value_is(tmp_path: Path) -> None:
    """ADR: a failure is recorded as a failure. The last good value is shown, dated, as newer nights failed."""
    rows: list[dict[str, Any]] = [
        {"date": "2026-09-22", "n": 572},
        {"date": "2026-09-23", "status": "fetch_failed"},
        {"date": "2026-09-24", "status": "partial", "n": 9},
    ]
    seed(tmp_path / "out", "t.jsonl", rows)

    assert rendered(tmp_path, {"label": "Weekly", "from": "t.jsonl", "field": "n"}) == [
        "- Weekly: 572 (as of 2026-09-22)"
    ]


def test_where_keeps_the_rows_with_those_values(tmp_path: Path) -> None:
    rows = [
        {"date": "2026-09-17", "label": "main", "views": 100},
        {"date": "2026-09-17", "label": "other", "views": 5},
        {"date": "2026-09-24", "label": "main", "views": 163},
        {"date": "2026-09-24", "label": "other", "views": 9},
    ]
    seed(tmp_path / "out", "forum.jsonl", rows)

    line = {"label": "Views", "from": "forum.jsonl", "field": "views", "where": {"label": "main"}, "compare": "7d"}
    assert rendered(tmp_path, line) == ["- Views: 163 (+63 vs 7d ago)"]


def test_items_are_a_line_each_from_a_list_in_the_newest_row(tmp_path: Path) -> None:
    items = [
        {"number": 7, "title": "Newer", "age_days": 14, "flag": False},
        {"number": 3, "title": "Oldest", "age_days": 210, "flag": True},
        {"number": 9, "title": "Middle", "age_days": 30, "flag": False},
    ]
    seed(tmp_path / "out", "t.jsonl", [{"date": "2026-09-24", "queue": {"items": items}}])
    line = {
        "label": "Waiting",
        "from": "t.jsonl",
        "field": "queue.items",
        "aggregate": "items",
        "item": "#{number} {title}, {age_days}d, {flag}",
        "sort": "age_days",
        "count": 2,
    }

    assert rendered(tmp_path, line) == ["- Waiting: #3 Oldest, 210d, True", "- Waiting: #9 Middle, 30d, False"]


def test_an_item_whose_template_names_a_field_it_lacks_is_left_out(tmp_path: Path) -> None:
    """As for every template: a field with no value leaves out what names it, here the item."""
    items = [{"file": "a.jsonl", "age_days": 3}, {"file": "b.jsonl", "age_days": None}, {"file": "c.jsonl"}]
    seed(tmp_path / "out", "health.jsonl", [{"date": "2026-09-24", "stale": items}])
    line = {
        "label": "Stale",
        "from": "health.jsonl",
        "field": "stale",
        "aggregate": "items",
        "item": "{file} {age_days}d",
    }

    assert rendered(tmp_path, line) == ["- Stale: a.jsonl 3d"]


def test_the_critical_block_is_a_line_per_critical_entry_in_health(tmp_path: Path) -> None:
    """ADR: critical sources surface at the top of SUMMARY.md; nothing when nothing is critical."""
    critical = [
        {"source": "example_source", "file": "a.jsonl", "reason": "no good row for 2 nights, since 2026-09-23"},
        {"source": "other_source", "file": None, "reason": "crashed: RuntimeError: boom"},
    ]
    seed(
        tmp_path / "out",
        "health.jsonl",
        [{"date": "2026-09-23", "critical": []}, {"date": "2026-09-24", "critical": critical}],
    )
    line = {
        "label": "Critical",
        "from": "health.jsonl",
        "field": "critical",
        "aggregate": "items",
        "item": "{source}: {reason}",
    }

    assert rendered(tmp_path, line) == [
        "- Critical: example_source: no good row for 2 nights, since 2026-09-23",
        "- Critical: other_source: crashed: RuntimeError: boom",
    ]


def test_a_versions_line_is_the_version_breakdown(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", [{"date": "2026-09-24", "versions": {"2.0.0": 5, "1.9.0": 2}}])

    assert rendered(
        tmp_path, {"label": "Versions", "from": "t.jsonl", "field": "versions", "aggregate": "versions"}
    ) == ["- Versions: **v1.9.0:** 2 **v2.0.0:** 5"]


def release(tag: str, published: str | None, downloads: list[int], **flags: bool) -> dict[str, Any]:
    assets = [{"name": f"a{i}.zip", "downloads": n} for i, n in enumerate(downloads)]
    return {"tag": tag, "prerelease": False, "draft": False, "published_at": published, "assets": assets, **flags}


RELEASES = [
    {
        "date": "2026-09-17",
        "releases": [release("v1.1.0", "2026-09-10T08:00:00Z", [40]), release("v1.0.0", "2026-08-01T08:00:00Z", [300])],
    },
    {
        "date": "2026-09-24",
        "releases": [
            release("v2.0.0", None, [], draft=True),
            release("v1.2.0-beta.1", "2026-09-22T08:00:00Z", [2], prerelease=True),
            release("v1.1.0", "2026-09-10T08:00:00Z", [400, 55]),
            release("v1.0.0", "2026-08-01T08:00:00Z", [309]),
        ],
    },
]


def test_the_latest_release_is_the_newest_published_one_that_is_not_a_prerelease(tmp_path: Path) -> None:
    seed(tmp_path / "out", "r.jsonl", RELEASES)
    line = {"label": "Latest", "from": "r.jsonl", "field": "releases", "aggregate": "latest_release"}

    assert rendered(tmp_path, line) == ["- Latest: v1.1.0, published 2026-09-10 (14 days ago), 455 downloads"]


@pytest.mark.parametrize(
    ("count", "expected"), [(None, "v1.1.0 +415 · v1.0.0 +9 · v1.2.0-beta.1 +2"), (2, "v1.1.0 +415 · v1.0.0 +9")]
)
def test_release_downloads_are_each_releases_downloads_since_then_most_first(
    tmp_path: Path, count: int | None, expected: str
) -> None:
    seed(tmp_path / "out", "r.jsonl", RELEASES)
    line: dict[str, Any] = {
        "label": "Zip",
        "from": "r.jsonl",
        "field": "releases",
        "aggregate": "release_downloads",
        "compare": "7d",
    }
    if count is not None:
        line["count"] = count

    assert rendered(tmp_path, line) == [f"- Zip: {expected}"]


def hidden_run(tmp_path: Path, line: dict[str, Any], *tags: str) -> list[str]:
    seed(tmp_path / "out", "r.jsonl", RELEASES)
    ctx = context(tmp_path / "out")
    hide = {"r.jsonl": [Hide("releases", "tag", frozenset(tags))]}
    entries = [lines_block("b", [line])]
    assert reports.validate(entries) == []
    reports.write(ctx, entries, hide)
    return written(ctx).splitlines()[1:-1]


def test_a_hidden_release_is_not_the_latest_and_gains_nothing_in_a_report(tmp_path: Path) -> None:
    latest = {"label": "Latest", "from": "r.jsonl", "field": "releases", "aggregate": "latest_release"}
    gains = {"label": "Zip", "from": "r.jsonl", "field": "releases", "aggregate": "release_downloads", "compare": "7d"}

    assert hidden_run(tmp_path, latest, "1.1.0") == [
        "- Latest: v1.0.0, published 2026-08-01 (54 days ago), 309 downloads"
    ]
    assert hidden_run(tmp_path / "g", gains, "1.1.0") == ["- Zip: v1.0.0 +9 · v1.2.0-beta.1 +2"]


def test_release_downloads_need_an_earlier_row(tmp_path: Path) -> None:
    seed(tmp_path / "out", "r.jsonl", RELEASES[1:])
    line = {"label": "Zip", "from": "r.jsonl", "field": "releases", "aggregate": "release_downloads", "compare": "7d"}

    assert rendered(tmp_path, line) == []


COUNTS = [
    {"date": "2026-09-23", "types": {"os": 1}},
    {"date": "2026-09-24", "types": {"os": 30, "container": 50, "core": 20, "none": 0}},
]


def test_counts_are_an_objects_counts_as_items_the_largest_first_joined_by_default(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", COUNTS)
    line = {"label": "Types", "from": "t.jsonl", "field": "types", "aggregate": "counts"}

    assert rendered(tmp_path, line) == ["- Types: container 50 · os 30 · core 20 · none 0"]


def test_counts_give_each_items_share_and_the_rest_past_count(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", COUNTS)
    line = {
        "from": "t.jsonl",
        "field": "types",
        "aggregate": "counts",
        "count": 2,
        "item": "{key} {share}%",
        "join": " · ",
        "parts": [{"text": "- Types: {items}"}, {"text": "other {rest} of {total}", "when": [["rest", ">", 0]]}],
    }

    assert rendered(tmp_path, line) == ["- Types: container 50% · os 30% · other 20 of 100"]


def test_counts_show_nothing_where_the_newest_row_has_no_object(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", [*COUNTS, {"date": "2026-09-25", "types": 7}])
    line = {"label": "Types", "from": "t.jsonl", "field": "types", "aggregate": "counts"}

    assert rendered(tmp_path, line) == []


# ---- Templates and parts -------------------------------------------------------------------------


def test_a_text_template_is_the_whole_line(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", daily("total", TOTALS))

    line = {"text": "As of {date}: {value} ({change:+} since {then})", "from": "t.jsonl", "field": "total"}
    assert rendered(tmp_path, {**line, "compare": "7d"}) == ["As of 2026-09-24: 101 (+5 since 96)"]


def test_a_line_that_reads_no_file_is_its_text(tmp_path: Path) -> None:
    """Static text, a blank line too, always shows, so a block of it is never only its markers."""
    assert rendered(tmp_path, {"text": "# Title"}) == ["# Title"]
    assert rendered(tmp_path, {"text": ""}) == [""]


def test_a_template_field_without_a_value_leaves_out_what_names_it(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", daily("total", TOTALS))

    assert (
        rendered(tmp_path, {"text": "{value} ({change:+})", "from": "t.jsonl", "field": "total", "compare": "30d"})
        == []
    )


def test_parts_join_the_ones_that_show_and_a_line_with_none_is_left_out(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", daily("total", TOTALS))
    parts = [
        {"text": "{days}: {then} ({change:+})", "from": "t.jsonl", "field": "total", "compare": days}
        for days in ("7d", "30d")
    ]
    parts = [{**part, "text": part["text"].replace("{days}", part["compare"])} for part in parts]

    assert rendered(tmp_path, {"text": "- {parts}", "parts": parts, "join": " · "}) == ["- 7d: 96 (+5)"]
    assert rendered(tmp_path, {"text": "- {parts}", "parts": parts[1:], "join": " · "}) == []


def test_a_part_reads_the_fields_of_the_piece_it_is_in(tmp_path: Path) -> None:
    """A part without `from` fills its text from the piece around it: the optional bits of one line."""
    rows = [
        {"date": "2026-09-17", "stars": 50, "forks": 3},
        {"date": "2026-09-24", "stars": 53, "forks": 4, "rank": {"of": 900}},
    ]
    seed(tmp_path / "out", "t.jsonl", rows)
    line = {
        "from": "t.jsonl",
        "field": "stars",
        "compare": "7d",
        "parts": [
            {"text": "- Stars {value}"},
            {"text": " ({change:+} this week)"},
            {"text": " · forks {row.forks}"},
            {"text": " · of {row.rank.of}"},
            {"text": " · {row.missing}"},
        ],
    }

    assert rendered(tmp_path, line) == ["- Stars 53 (+3 this week) · forks 4 · of 900"]


WHEN = {
    "equal": (["value", "=", 101], True),
    "not_equal": (["value", "!=", 101], False),
    "greater": (["value", ">", 100], True),
    "at_least": (["value", ">=", 102], False),
    "less": (["change", "<", 0], False),
    "at_most": (["change", "<=", 5], True),
    "a_list_by_its_length": (["row.list", ">", 1], True),
    "no_value_fails": (["row.missing", "!=", 1], False),
    "text_does_not_order": (["row.name", ">", 1], False),
}


@pytest.mark.parametrize(("condition", "shows"), WHEN.values(), ids=WHEN.keys())
def test_when_shows_a_piece_only_where_each_condition_holds(tmp_path: Path, condition: list[Any], shows: bool) -> None:
    rows = [{"date": "2026-09-17", "n": 96}, {"date": "2026-09-24", "n": 101, "list": [1, 2], "name": "x"}]
    seed(tmp_path / "out", "t.jsonl", rows)
    line = {"text": "{value}", "from": "t.jsonl", "field": "n", "compare": "7d", "when": [condition]}

    assert rendered(tmp_path, line) == (["101"] if shows else [])


def test_series_peak_and_sum_give_their_fields(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", daily("total", TOTALS))
    base = {"from": "t.jsonl", "field": "total"}

    assert rendered(tmp_path, {**base, "series": 3, "text": "Last {count}: {values}"}) == ["Last 3: 97 100 101"]
    weekly = {**base, "series": 8, "every": 7, "text": "Weekly, {count}: {values}"}
    assert rendered(tmp_path, weekly) == ["Weekly, 3: 80 96 101"]
    assert rendered(tmp_path, {**weekly, "when": [["count", ">", 3]]}) == []
    peak = {**base, "aggregate": "peak", "text": "Peak {value} on {date}, {age} days before"}
    assert rendered(tmp_path, peak) == ["Peak 101 on 2026-09-24, 0 days before"]
    summed = {**base, "aggregate": "sum", "count": 2, "text": "{total} to {date} ({prior}, {change:+})"}
    assert rendered(tmp_path, summed) == ["201 to 2026-09-24 (193, +8)"]


def test_a_sum_gives_its_prior_and_change_only_when_the_window_before_is_complete(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", daily("total", TOTALS))  # 6 rows
    line = {"from": "t.jsonl", "field": "total", "aggregate": "sum", "count": 4}

    assert rendered(tmp_path, {**line, "parts": [{"text": "{total}"}, {"text": " ({change:+})"}]}) == ["394"]
    assert rendered(tmp_path, {**line, "count": 3, "text": "{total} ({prior}, {change:+})"}) == ["298 (266, +32)"]


def test_replace_and_shorten_clean_a_value_where_a_template_shows_it(tmp_path: Path) -> None:
    rows = [{"date": "2026-09-24", "title": "[Request] A very long title that goes on and on and on", "ok": True}]
    seed(tmp_path / "out", "t.jsonl", rows)
    line = {
        "from": "t.jsonl",
        "field": "title",
        "text": "{value} ({date}, {row.ok})",
        "replace": {"value": [["^\\[[^\\]]*\\]\\s*", ""]], "date": [["^\\d{4}-", ""]], "row.ok": [["^True$", "yes"]]},
        "shorten": {"value": 19},
    }

    assert rendered(tmp_path, line) == ["A very long title… (09-24, yes)"]


def test_shorten_drops_the_space_before_its_mark_and_leaves_a_short_value(tmp_path: Path) -> None:
    seed(tmp_path / "out", "t.jsonl", [{"date": "2026-09-24", "a": "one two three", "b": "one two"}])
    base = {"from": "t.jsonl", "field": "a", "shorten": {"value": 9, "row.b": 7}}

    assert rendered(tmp_path, {**base, "text": "{value}|{row.b}"}) == ["one two…|one two"]


def test_a_custom_as_of_follows_its_piece(tmp_path: Path) -> None:
    rows: list[dict[str, Any]] = [
        {"date": "2026-09-22", "n": 572},
        {"date": "2026-09-23", "status": "fetch_failed"},
    ]
    seed(tmp_path / "out", "a.jsonl", rows)
    seed(tmp_path / "out", "b.jsonl", [{"date": "2026-09-23", "n": 540}])
    part = {"field": "n", "text": "{value}/wk", "as_of": " ({date})", "replace": {"date": [["^\\d{4}-", ""]]}}
    line = {
        "text": "- PyPI: {parts}",
        "parts": [{**part, "from": "a.jsonl"}, {**part, "from": "b.jsonl"}],
        "join": " · ",
    }

    assert rendered(tmp_path, line) == ["- PyPI: 572/wk (09-22) · 540/wk"]


def test_items_can_be_matched_skipped_and_joined_on_one_line_with_a_count_of_those_found(tmp_path: Path) -> None:
    failed = [
        {"file": "pkg-a.jsonl", "streak": 1},
        {"file": "other.jsonl", "streak": 5},
        {"file": "pkg-c.jsonl", "streak": 3},
        {"file": "pkg-b.jsonl", "streak": 2},
    ]
    seed(tmp_path / "out", "health.jsonl", [{"date": "2026-09-24", "failed": failed}])
    base = {"from": "health.jsonl", "field": "failed", "aggregate": "items", "match": {"file": "^pkg-"}}
    line = {
        **base,
        "sort": "file",
        "skip": 1,
        "join": ", ",
        "item": {"parts": [{"text": "{file}"}, {"text": " ({streak} nights)", "when": [["streak", ">", 1]]}]},
        "replace": {"file": [["^pkg-", ""], ["\\.jsonl$", ""]]},
        "text": "- Failed ({found}): {items}",
    }

    assert rendered(tmp_path, line) == ["- Failed (3): b (2 nights), a"]
    assert rendered(tmp_path, {**base, "item": "{file}", "join": "", "text": "none", "when": [["found", "=", 0]]}) == []


def test_joined_items_with_none_to_show_leave_out_what_names_them(tmp_path: Path) -> None:
    seed(tmp_path / "out", "health.jsonl", [{"date": "2026-09-24", "failed": []}])
    base = {"from": "health.jsonl", "field": "failed", "aggregate": "items", "item": "{file}", "join": ", "}

    assert rendered(tmp_path, {**base, "text": "- Failed: {items}"}) == []
    assert rendered(tmp_path, {**base, "text": "- All fetched", "when": [["found", "=", 0]]}) == ["- All fetched"]


VERSIONS = {
    "2.0.0": 20,
    "2.1.0": 60,
    "1.9.0": 3,
    "1.8.0": 2,
    "1.7.0": 1,
    "2.2.0-beta.1": 1,
    "2.2.0-beta.3": 2,
    "2.1.0-beta.9": 4,
    "2.0.0-alpha.2": 1,
}
TRAIL = [
    {"date": "2026-09-21", "versions": {"2.0.0": 85}},
    {"date": "2026-09-22", "versions": {"2.1.0": 24, "2.0.0": 60}},
    {"date": "2026-09-23", "versions": {"2.1.0": 45, "2.0.0": 40}},
    {"date": "2026-09-24", "versions": VERSIONS},
]


def versions_line(**line: Any) -> dict[str, Any]:
    return {"from": "installs.jsonl", "field": "versions", "aggregate": "versions", **line}


def test_the_newest_release_with_its_share_and_trail(tmp_path: Path) -> None:
    """Share is of all the counts; the trail is the version's count in each of the newest rows, 0 where absent."""
    seed(tmp_path / "out", "installs.jsonl", TRAIL)
    item = {
        "parts": [
            {"text": "{version}: {count} ({share}%)"},
            {"text": ", trail {trail}", "when": [["trail_change", ">", 0]]},
        ]
    }
    line = versions_line(pick="releases", count=1, trail=4, item=item)

    assert rendered(tmp_path, line) == ["2.1.0: 60 (64%), trail 0 24 45 60"]


def test_earlier_releases_skip_the_newest_and_add_up_the_rest(tmp_path: Path) -> None:
    seed(tmp_path / "out", "installs.jsonl", TRAIL)
    line = versions_line(
        pick="releases",
        skip=1,
        count=2,
        join=" · ",
        item="{version} {count}",
        parts=[{"text": "- Earlier: {items}"}, {"text": "older {rest}", "when": [["rest", ">", 0]]}],
    )

    assert rendered(tmp_path, line) == ["- Earlier: 2.0.0 20 · 1.9.0 3 · older 3"]


def test_the_newest_prereleases_are_those_of_the_newest_prereleases_version_and_the_rest_are_older(
    tmp_path: Path,
) -> None:
    seed(tmp_path / "out", "installs.jsonl", TRAIL)
    newest = versions_line(
        pick="newest_prereleases", join=" · ", item="{tag} {count}", text="- {base}: {total} ({items})"
    )
    older = versions_line(pick="older_prereleases", item="{version}", join=", ", text="- Older: {total} ({items})")

    assert rendered(tmp_path, newest) == ["- 2.2.0: 3 (beta.3 2 · beta.1 1)"]
    assert rendered(tmp_path, older) == ["- Older: 5 (2.1.0-beta.9, 2.0.0-alpha.2)"]


def test_a_release_found_by_a_tag_from_the_piece_around_it(tmp_path: Path) -> None:
    """The release of the version another file shows: `tag` is a template of the enclosing piece's fields."""
    seed(tmp_path / "out", "installs.jsonl", [{"date": "2026-09-24", "versions": {"1.0.0": 5, "1.1.0": 9}}])
    seed(tmp_path / "out", "r.jsonl", RELEASES)
    released = {
        "from": "r.jsonl",
        "field": "releases",
        "aggregate": "latest_release",
        "tag": "v{version}",
        "text": "- {version}: released {date}, {age} days ago, {downloads} downloads",
    }

    assert rendered(tmp_path, versions_line(pick="releases", count=1, item=released)) == [
        "- 1.1.0: released 2026-09-10, 14 days ago, 455 downloads"
    ]
    other = versions_line(pick="releases", count=1, item={**released, "tag": "x{version}"})
    assert rendered(tmp_path, other) == []
    stripped = {**released, "tag": "{version}", "replace": {"tag": [["^v+", ""]]}}
    assert rendered(tmp_path, versions_line(pick="releases", count=1, item=stripped)) == [
        "- 1.1.0: released 2026-09-10, 14 days ago, 455 downloads"
    ], "a release's tag is compared as a template shows it, after replace"


def test_release_downloads_take_an_item_template_and_a_join(tmp_path: Path) -> None:
    seed(tmp_path / "out", "r.jsonl", RELEASES)
    line = {
        "from": "r.jsonl",
        "field": "releases",
        "aggregate": "release_downloads",
        "compare": "7d",
        "count": 2,
        "item": "{tag} {gain}",
        "join": ", ",
        "replace": {"tag": [["^v", ""]]},
        "text": "- Zip: {items}",
    }

    assert rendered(tmp_path, line) == ["- Zip: 1.1.0 415, 1.0.0 9"]


# ---- Tables --------------------------------------------------------------------------------------


def table_block(table: dict[str, Any], **entry: Any) -> dict[str, Any]:
    return {"file": "report.md", "block": "t", "place": "bottom", "table": table, **entry}


def table_rows(tmp_path: Path, table: dict[str, Any]) -> list[str]:
    ctx = context(tmp_path / "out")
    run(ctx, [table_block(table)])
    return written(ctx).splitlines()[1:-1]


# Weeks start on Monday: 2026-08-31, 09-07, 09-14 and 09-21 are Mondays, so these are four weeks' rows.
OPEN = {
    "2026-09-01": 7,
    "2026-09-03": 3,
    "2026-09-06": 4,
    "2026-09-08": 3,
    "2026-09-13": 3,
    "2026-09-18": 4,
    "2026-09-22": 5,
}


def test_a_table_groups_rows_by_week_and_shows_the_latest_weeks_a_row_a_day(tmp_path: Path) -> None:
    seed(tmp_path / "out", "issues.jsonl", daily("open", OPEN))
    table = {
        "from": "issues.jsonl",
        "group": "week",
        "recent_weeks": 2,
        "columns": [
            {"title": "Date", "field": "date"},
            {"title": "Open", "field": "open", "bold": True},
            {"title": "Change", "field": "open", "aggregate": "change"},
            {"title": "Range", "field": "open", "aggregate": "range"},
        ],
    }

    assert table_rows(tmp_path, table) == [
        "| Date | Open | Change | Range |",
        "|---|---|---|---|",
        "| 2026-09-06 (wk) | **4** | +0 | 3-7 |",
        "| 2026-09-13 (wk) | **3** | -1 | 3 |",
        "| 2026-09-18 | **4** | +1 | 4 |",
        "| 2026-09-22 | **5** | +1 | 5 |",
    ]


def test_a_table_by_day_has_a_row_a_day(tmp_path: Path) -> None:
    seed(tmp_path / "out", "issues.jsonl", daily("open", {"2026-09-01": 7, "2026-09-03": 3}))
    table = {
        "from": "issues.jsonl",
        "columns": [{"title": "Date", "field": "date"}, {"title": "Open", "field": "open"}],
    }

    assert table_rows(tmp_path, table)[2:] == ["| 2026-09-01 | 7 |", "| 2026-09-03 | 3 |"]


def test_a_column_from_another_file_is_joined_by_date_and_a_ratio_is_formatted(tmp_path: Path) -> None:
    out = tmp_path / "out"
    seed(out, "issues.jsonl", [{"date": d, "issues": {"open": n}, "queue": {"total": 2}} for d, n in OPEN.items()])
    seed(out, "installs.jsonl", daily("total", {"2026-09-06": 90, "2026-09-18": 99, "2026-09-22": 0}))
    table = {
        "from": "issues.jsonl",
        "group": "week",
        "recent_weeks": 1,
        "columns": [
            {"title": "Date", "field": "date"},
            {"title": "Queue", "field": "queue.total"},
            {"title": "Installs", "field": "total", "from": "installs.jsonl"},
            {
                "title": "Per install",
                "field": "issues.open",
                "per": {"field": "total", "from": "installs.jsonl"},
                "format": ".2f",
            },
        ],
    }

    assert table_rows(tmp_path, table)[2:] == [
        "| 2026-09-06 (wk) | 2 | 90 | 0.04 |",
        "| 2026-09-13 (wk) | 2 | - | - |",
        "| 2026-09-18 (wk) | 2 | 99 | 0.04 |",
        "| 2026-09-22 | 2 | 0 | - |",
    ]


def test_a_versions_column_collapses_older_prereleases(tmp_path: Path) -> None:
    """The newest prerelease stands alone; older ones collapse by base version and channel, unless alone."""
    versions = {
        "1.9.0": 1,
        "2.0.0": 50,
        "2.1.0-beta.2": 1,
        "2.1.0-beta.10": 3,
        "2.1.0-alpha.1": 2,
        "2.1.1-beta.1": 4,
        "2.1.1-beta.3": 2,
        "2.1.1": 9,
    }
    seed(tmp_path / "out", "installs.jsonl", [{"date": "2026-09-24", "versions": versions}])
    table = {"from": "installs.jsonl", "columns": [{"title": "Versions", "field": "versions", "aggregate": "versions"}]}

    assert table_rows(tmp_path, table)[2] == (
        "| **v1.9.0:** 1 **v2.0.0:** 50 **v2.1.0-alpha.1:** 2 **v2.1.0-beta:** 4 **v2.1.1-beta.1:** 4"
        " **v2.1.1-beta.3:** 2 **v2.1.1:** 9 |"
    )


def test_a_column_text_combines_its_aggregates(tmp_path: Path) -> None:
    """A cell can show several aggregates of its values: `**last** (+change) · range low-high` where it varies."""
    seed(tmp_path / "out", "issues.jsonl", daily("open", OPEN))
    installs = {
        "title": "Open",
        "field": "open",
        "parts": [{"text": "**{last}** ({change:+})"}, {"text": " · range {low}-{high}", "when": [["spread", ">", 0]]}],
    }
    table = {
        "from": "issues.jsonl",
        "group": "week",
        "recent_weeks": 1,
        "columns": [{"title": "Date", "field": "date"}, installs],
    }

    assert table_rows(tmp_path, table)[2:] == [
        "| 2026-09-06 (wk) | **4** (+0) · range 3-7 |",
        "| 2026-09-13 (wk) | **3** (-1) |",
        "| 2026-09-18 (wk) | **4** (+1) |",
        "| 2026-09-22 | **5** (+1) |",
    ]


def test_a_cell_whose_text_does_not_show_is_missing(tmp_path: Path) -> None:
    seed(tmp_path / "out", "issues.jsonl", daily("open", {"2026-09-01": 7, "2026-09-03": 3}))
    column = {"title": "Change", "field": "open", "text": "{change:+}", "when": [["change", "!=", 0]]}
    table = {"from": "issues.jsonl", "columns": [column]}

    assert table_rows(tmp_path, table)[2:] == ["| - |", "| -4 |"]


def test_a_table_skips_failure_rows(tmp_path: Path) -> None:
    rows: list[dict[str, Any]] = [
        {"date": "2026-09-01", "open": 7},
        {"date": "2026-09-02", "status": "fetch_failed"},
        {"date": "x", "open": 1},
    ]
    seed(tmp_path / "out", "issues.jsonl", rows)
    table = {
        "from": "issues.jsonl",
        "columns": [{"title": "Date", "field": "date"}, {"title": "Open", "field": "open"}],
    }

    assert table_rows(tmp_path, table)[2:] == ["| 2026-09-01 | 7 |"]


def test_a_table_with_no_rows_is_only_its_markers(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    table = {"from": "missing.jsonl", "columns": [{"title": "Open", "field": "open"}]}

    run(ctx, [table_block(table, heading="Support")])

    assert written(ctx) == block_text("t")


# ---- Config --------------------------------------------------------------------------------------

LINE = {"label": "N", "from": "a.jsonl", "field": "n"}
TABLE = {"from": "a.jsonl", "columns": [{"title": "N", "field": "n"}]}


def entry(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"file": "r.md", "block": "b", "place": "top", "lines": [LINE]}
    base.update(changes)
    return {key: value for key, value in base.items() if value is not None}


PROBLEMS = {
    "not_a_list": ({"file": "r.md"}, ": must be a list of reports"),
    "not_an_object": (["x"], "[0]: must be an object"),
    "unknown_key": ([entry(title="x")], "[0]: unknown keys: title"),
    "no_file": ([entry(file=None)], "[0].file: must be a *.md path"),
    "not_md": ([entry(file="r.jsonl")], "[0].file: must be a *.md path"),
    "bad_block": ([entry(block="two words")], "[0].block: must be letters, digits, - or _"),
    "bad_place": ([entry(place="middle")], "[0].place: must be top or bottom"),
    "empty_heading": ([entry(heading="")], "[0].heading: must be a non-empty string"),
    "both_kinds": ([entry(table=TABLE)], "[0]: must have one of table or lines"),
    "no_kind": ([entry(lines=None)], "[0]: must have one of table or lines"),
    "same_block_twice": ([entry(), entry(file="./R.md")], "[1].block: b is already in r.md"),
    "empty_lines": ([entry(lines=[])], "[0].lines: must be a non-empty list"),
    "line_key": ([entry(lines=[{**LINE, "every": 7}])], "[0].lines[0].every: needs series"),
    "line_from": ([entry(lines=[{**LINE, "from": "../a.jsonl"}])], "[0].lines[0].from: must be a plain *.jsonl name"),
    "line_label": ([entry(lines=[{**LINE, "label": 3}])], "[0].lines[0].label: must be a non-empty string"),
    "line_two_kinds": (
        [entry(lines=[{**LINE, "series": 3, "compare": "7d"}])],
        "[0].lines[0]: has more than one of compare, series, aggregate",
    ),
    "bad_compare": ([entry(lines=[{**LINE, "compare": "week"}])], "[0].lines[0].compare: must be yesterday or <N>d"),
    "bad_series": ([entry(lines=[{**LINE, "series": 0}])], "[0].lines[0].series: must be a whole number, 1 or more"),
    "bad_aggregate": (
        [entry(lines=[{**LINE, "aggregate": "average"}])],
        "[0].lines[0].aggregate: must be one of peak, sum, versions, items, latest_release, release_downloads, counts",
    ),
    "sum_count": ([entry(lines=[{**LINE, "aggregate": "sum"}])], "[0].lines[0].count: sum needs it"),
    "items_item": ([entry(lines=[{**LINE, "aggregate": "items"}])], "[0].lines[0].item: items needs it"),
    "stray_item": (
        [entry(lines=[{**LINE, "item": "{x}"}])],
        "[0].lines[0].item: only items, release_downloads, versions and counts take it",
    ),
    "stray_sort": ([entry(lines=[{**LINE, "sort": "x"}])], "[0].lines[0].sort: only items takes it"),
    "stray_count": (
        [entry(lines=[{**LINE, "count": 2}])],
        "[0].lines[0].count: only sum, items, release_downloads, versions and counts take it",
    ),
    "downloads_compare": (
        [entry(lines=[{**LINE, "aggregate": "release_downloads"}])],
        "[0].lines[0].compare: release_downloads needs it",
    ),
    "bad_complement": ([entry(lines=[{**LINE, "complement": 1}])], "[0].lines[0].complement: must be true"),
    "complement_kind": (
        [entry(lines=[{**LINE, "series": 3, "complement": True}])],
        "[0].lines[0].complement: only a field alone or with compare takes it",
    ),
    "bad_where": ([entry(lines=[{**LINE, "where": []}])], "[0].lines[0].where: must be an object"),
    "bad_format": (
        [entry(lines=[{**LINE, "format": "zz"}])],
        "[0].lines[0].format: must be a format spec without a sign, such as .2f",
    ),
    "signed_format": (
        [entry(lines=None, table={**TABLE, "columns": [{"title": "N", "field": "n", "format": "+d"}]})],
        "[0].table.columns[0].format: must be a format spec without a sign, such as .2f",
    ),
    "table_group": ([entry(lines=None, table={**TABLE, "group": "month"})], "[0].table.group: must be day or week"),
    "recent_weeks_by_day": (
        [entry(lines=None, table={**TABLE, "recent_weeks": 2})],
        "[0].table.recent_weeks: needs group week",
    ),
    "table_columns": (
        [entry(lines=None, table={**TABLE, "columns": []})],
        "[0].table.columns: must be a non-empty list",
    ),
    "table_key": ([entry(lines=None, table={**TABLE, "rows": 3})], "[0].table: unknown keys: rows"),
    "column_aggregate": (
        [entry(lines=None, table={**TABLE, "columns": [{"title": "N", "field": "n", "aggregate": "sum"}]})],
        "[0].table.columns[0].aggregate: must be one of last, change, range, versions",
    ),
    "column_bold": (
        [entry(lines=None, table={**TABLE, "columns": [{"title": "N", "field": "n", "bold": "yes"}]})],
        "[0].table.columns[0].bold: must be true or false",
    ),
    "line_not_object": ([entry(lines=["x"])], "[0].lines[0]: must be an object"),
    "bad_replacement": (
        [entry(lines=[{**LINE, "replace": {"n": [["a", "\\9"]]}}])],
        "[0].lines[0].replace.n: must be a list of [pattern, replacement]",
    ),
    "replace_not_object": ([entry(lines=[{**LINE, "replace": []}])], "[0].lines[0].replace: must be an object"),
    "match_not_object": (
        [entry(lines=[{**LINE, "aggregate": "items", "item": "{a}", "match": ["a"]}])],
        "[0].lines[0].match: must be an object",
    ),
    "label_and_text": ([entry(lines=[{**LINE, "text": "x"}])], "[0].lines[0]: has both label and text"),
    "static_label": ([entry(lines=[{"label": "N"}])], "[0].lines[0].label: needs from"),
    "static_nothing": (
        [entry(lines=[{"join": ", "}])],
        "[0].lines[0]: must have text or parts, or read a file with from",
    ),
    "static_data_key": ([entry(lines=[{"text": "x", "field": "n"}])], "[0].lines[0]: field needs from"),
    "bad_template": ([entry(lines=[{"text": "{value"}])], "[0].lines[0].text: must be a template of named fields"),
    "positional_template": ([entry(lines=[{"text": "{}"}])], "[0].lines[0].text: must be a template of named fields"),
    "conversion_template": (
        [entry(lines=[{"text": "{a!r}"}])],
        "[0].lines[0].text: must be a template of named fields",
    ),
    "empty_parts": ([entry(lines=[{"parts": []}])], "[0].lines[0].parts: must be a non-empty list"),
    "bad_part": (
        [entry(lines=[{"parts": [{"text": 3}]}])],
        "[0].lines[0].parts[0].text: must be a template of named fields",
    ),
    "bad_join": ([entry(lines=[{"parts": [{"text": "x"}], "join": 1}])], "[0].lines[0].join: must be a string"),
    "bad_when": (
        [entry(lines=[{**LINE, "when": [["n", "~", 1]]}])],
        "[0].lines[0].when[0]: must be [field, op, value], op one of <, <=, =, !=, >=, >",
    ),
    "when_not_list": ([entry(lines=[{**LINE, "when": {"n": 1}}])], "[0].lines[0].when: must be a list"),
    "bad_replace": (
        [entry(lines=[{**LINE, "replace": {"n": [["(", ""]]}}])],
        "[0].lines[0].replace.n: must be a list of [pattern, replacement]",
    ),
    "bad_shorten": (
        [entry(lines=[{**LINE, "shorten": {"n": 1}}])],
        "[0].lines[0].shorten.n: must be a whole number, 2 or more",
    ),
    "bad_as_of": ([entry(lines=[{**LINE, "as_of": "{"}])], "[0].lines[0].as_of: must be a template of named fields"),
    "bad_match": (
        [entry(lines=[{**LINE, "aggregate": "items", "item": "{a}", "match": {"a": "["}}])],
        "[0].lines[0].match.a: must be a regular expression",
    ),
    "bad_skip": (
        [entry(lines=[{**LINE, "aggregate": "items", "item": "{a}", "skip": -1}])],
        "[0].lines[0].skip: must be a whole number, 0 or more",
    ),
    "stray_match": (
        [entry(lines=[{**LINE, "match": {"a": "x"}}])],
        "[0].lines[0].match: only items, release_downloads, versions and counts take it",
    ),
    "stray_pick": ([entry(lines=[{**LINE, "pick": "releases"}])], "[0].lines[0].pick: only versions takes it"),
    "bad_pick": (
        [entry(lines=[{**LINE, "aggregate": "versions", "pick": "all", "item": "{a}"}])],
        "[0].lines[0].pick: must be one of releases, newest_prereleases, older_prereleases",
    ),
    "pick_item": (
        [entry(lines=[{**LINE, "aggregate": "versions", "pick": "releases"}])],
        "[0].lines[0].item: versions needs it with pick",
    ),
    "versions_item_no_pick": (
        [entry(lines=[{**LINE, "aggregate": "versions", "item": "{a}"}])],
        "[0].lines[0].pick: versions needs it for item",
    ),
    "bad_trail": (
        [entry(lines=[{**LINE, "aggregate": "versions", "pick": "releases", "item": "{a}", "trail": 0}])],
        "[0].lines[0].trail: must be a whole number, 1 or more",
    ),
    "stray_tag": ([entry(lines=[{**LINE, "tag": "v{a}"}])], "[0].lines[0].tag: only latest_release takes it"),
    "nested_items_join": (
        [entry(lines=[{"parts": [{**LINE, "aggregate": "items", "item": "{a}"}]}])],
        "[0].lines[0].parts[0].join: items needs it inside another piece",
    ),
    "bad_item": (
        [entry(lines=[{**LINE, "aggregate": "items", "item": 3}])],
        "[0].lines[0].item: must be a template or a piece",
    ),
    "item_piece": (
        [entry(lines=[{**LINE, "aggregate": "items", "item": {"text": "{"}}])],
        "[0].lines[0].item.text: must be a template of named fields",
    ),
    "column_part_reads": (
        [entry(lines=None, table={**TABLE, "columns": [{"title": "N", "field": "n", "parts": [LINE]}]})],
        "[0].table.columns[0].parts[0]: a column's parts can't read a file",
    ),
    "column_label": (
        [entry(lines=None, table={**TABLE, "columns": [{"title": "N", "field": "n", "label": "x"}]})],
        "[0].table.columns[0]: unknown keys: label",
    ),
    "column_per": (
        [entry(lines=None, table={**TABLE, "columns": [{"title": "N", "field": "n", "per": {"from": "b.jsonl"}}]})],
        "[0].table.columns[0].per.field: must be a non-empty string",
    ),
}


@pytest.mark.parametrize(("cfg", "problem"), PROBLEMS.values(), ids=PROBLEMS.keys())
def test_each_problem_in_the_config_is_named_by_its_path(cfg: Any, problem: str) -> None:
    assert problem in reports.validate(cfg)


def test_a_valid_config_has_no_problems() -> None:
    table = {
        "from": "a.jsonl",
        "group": "week",
        "recent_weeks": 2,
        "columns": [
            {"title": "Date", "field": "date"},
            {"title": "N", "field": "n", "aggregate": "change", "bold": True, "format": ".1f"},
            {"title": "Per", "field": "n", "from": "b.jsonl", "per": {"field": "m"}, "format": ".2f"},
            {"title": "C", "field": "n", "parts": [{"text": "{last}"}], "join": "", "when": [["last", ">", 0]]},
        ],
    }
    lines = [
        {**LINE, "compare": "yesterday", "where": {"label": "main"}},
        {**LINE, "series": 8, "every": 7},
        {**LINE, "aggregate": "sum", "count": 14},
        {**LINE, "aggregate": "items", "item": "{a}", "sort": "b", "count": 2},
        {**LINE, "aggregate": "counts", "item": "{key}", "skip": 1, "count": 2, "match": {"key": "^a"}},
        {**LINE, "aggregate": "release_downloads", "compare": "7d", "count": 3, "item": "{tag}", "join": ", "},
        {"text": "static"},
        {
            "text": "- {parts}",
            "parts": [{"from": "a.jsonl", "field": "n", "text": "{value}", "when": [["value", ">", 1]]}],
            "join": " · ",
        },
        {
            **LINE,
            "aggregate": "versions",
            "pick": "releases",
            "trail": 4,
            "skip": 1,
            "count": 1,
            "match": {"version": "^2"},
            "item": {"from": "r.jsonl", "field": "releases", "aggregate": "latest_release", "tag": "v{version}"},
            "replace": {"version": [["^v", ""]]},
            "shorten": {"version": 9},
            "as_of": " ({date})",
        },
    ]
    cfg = [entry(heading="H", lines=lines), entry(block="t", place="bottom", lines=None, table=table)]

    assert reports.validate(cfg) == []


# ---- The default summary -------------------------------------------------------------------------


def health_row(**fields: Any) -> dict[str, Any]:
    """A health row with nothing stale, failed, critical or found, but `fields`."""
    row = {"date": "2026-09-24", "stale_count": 0, "stale": [], "failed_count": 0, "failed": []}
    return {**row, "checks": [], "critical": [], **fields}


def summary(tmp_path: Path, sections: Sequence[Section] = ()) -> list[str]:
    """SUMMARY.md, as the default summary writes it from the files in tmp_path/out."""
    ctx = context(tmp_path / "out")
    run(ctx, reports.default(sections, "health.jsonl"))
    return written(ctx, "SUMMARY.md").splitlines()


def test_the_default_summary_is_one_block_in_summary_md_with_the_runs_health_then_the_sections(tmp_path: Path) -> None:
    seed(tmp_path / "out", "health.jsonl", [health_row()])
    seed(tmp_path / "out", "t.jsonl", [{"date": "2026-09-24", "n": 5}])
    sections = [
        Section("s", "a", "t.jsonl", [Shown("n", "N")], "Label"),
        Section("s", "b", "t.jsonl", [Shown("n", "M")], "Label"),
    ]

    assert summary(tmp_path, sections) == [
        "<!-- leadline:start summary -->",
        "## Summary",
        "",
        "- Run: 2026-09-24",
        "- All sources current",
        "",
        "### s: a",
        "",
        "- N: 5",
        "",
        "### s: b",
        "",
        "- M: 5",
        "<!-- leadline:end summary -->",
    ]


def test_the_summary_health_lines_name_what_failed_is_stale_critical_or_found_wrong(tmp_path: Path) -> None:
    row = health_row(
        stale_count=2,
        stale=[{"file": "a.jsonl", "last": "2026-09-20", "age_days": 4}, {"file": "b.jsonl", "last": None}],
        failed_count=2,
        failed=[
            {"file": "c.jsonl", "last": "2026-09-24", "status": "fetch_failed", "streak": 1, "since": "2026-09-24"},
            {"file": "d.jsonl", "last": "2026-09-24", "status": "parse_failed", "streak": 3, "since": "2026-09-22"},
        ],
        critical=[{"source": "d_source", "file": "d.jsonl", "reason": "no good row for 3 nights, since 2026-09-22"}],
        checks=[
            {"source": "a_source", "level": "ok", "message": "fine"},
            {"source": "b_source", "level": "warn", "message": "looks odd"},
            {"source": "c_source", "level": "error", "message": "wrong site"},
        ],
    )
    seed(tmp_path / "out", "health.jsonl", [row])

    assert summary(tmp_path)[3:-1] == [
        "- Run: 2026-09-24",
        "- Failed: c.jsonl, fetch_failed since 2026-09-24",
        "- Failed: d.jsonl, parse_failed since 2026-09-22 (3 nights)",
        "- Stale: a.jsonl, last row 2026-09-20",
        "- Stale: b.jsonl",
        "- Critical: d_source: no good row for 3 nights, since 2026-09-22",
        "- Check b_source (warn): looks odd",
        "- Check c_source (error): wrong site",
    ]


def test_the_default_summary_is_a_valid_report_config() -> None:
    assert reports.validate(reports.default([], "health.jsonl")) == []


# ---- A nested piece that reads a file (docs/REPORTS.md, "Inherits fields") ---------------------------


def test_a_nested_piece_that_reads_a_file_shows_the_outer_pieces_change_when_it_has_none_of_its_own(
    tmp_path: Path,
) -> None:
    seed(tmp_path / "out", "a.jsonl", daily("n", {"2026-09-23": 10, "2026-09-24": 15}))
    seed(tmp_path / "out", "b.jsonl", daily("m", {"2026-09-23": 1, "2026-09-24": 2}))
    outer = {
        "from": "a.jsonl",
        "field": "n",
        "compare": "yesterday",
        "parts": [{"from": "b.jsonl", "field": "m", "text": "m {value} change {change:+} then {then}"}],
    }

    assert rendered(tmp_path, outer) == ["m 2 change +5 then 10"]


def test_sum_has_no_prior_for_a_history_shorter_than_twice_its_window(tmp_path: Path) -> None:
    seed(tmp_path / "out", "a.jsonl", daily("n", {"2026-09-22": 1, "2026-09-23": 2, "2026-09-24": 3}))
    line = {"from": "a.jsonl", "field": "n", "aggregate": "sum", "count": 2}

    assert rendered(tmp_path, line) == ["5 through 2026-09-24"]
