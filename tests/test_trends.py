"""trends: trends.json, chart-ready series of the fields each source's summary() names, and trends.html, a local
viewer with a copy of the same data (docs/TRENDS.md).

Every file and value here is made up. The viewer's drawing is JavaScript, which these tests don't run: they check
the page's structure and the data it embeds.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import port
import pytest

from leadline import store, trends
from leadline.config import Card, Hide, Note, Section, Shown, section_lines
from leadline.context import Context, Critical
from leadline.http import Http

NOW = datetime(2026, 9, 24, 23, 30, tzinfo=timezone.utc)
HEALTH = "health.jsonl"


def context(folder: Path) -> Context:
    folder.mkdir(parents=True, exist_ok=True)
    return Context(now=NOW, out_dir=folder, log=logging.getLogger("leadline"), http=Http("test/1.0"))


def seed(folder: Path, name: str, rows: Iterable[Mapping[str, Any]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def health_row(**fields: Any) -> dict[str, Any]:
    row = {"date": "2026-09-24", "stale_count": 0, "stale": [], "failed_count": 0, "failed": []}
    return {**row, "checks": [], "critical": [], "corrections": [], **fields}


def built(folder: Path, *sections: Section, projects: Any = None) -> dict[str, Any]:
    return trends.build(folder, date(2026, 9, 24), sections, HEALTH, projects)


# ---- trends.json ---------------------------------------------------------------------------------


def test_the_data_is_the_format_the_run_date_the_health_and_a_section_an_entry(tmp_path: Path) -> None:
    seed(tmp_path, HEALTH, [health_row()])

    data = built(
        tmp_path, Section("a", "x/y", "a.jsonl", [Shown("n", "N")], "Label"), Section("b", None, "b.jsonl", [], "Label")
    )

    assert list(data) == ["format", "date", "health", "sections"]
    assert (data["format"], data["date"]) == (3, "2026-09-24")
    assert [section["title"] for section in data["sections"]] == ["a: x/y", "b"]


def test_a_section_is_its_names_file_dates_and_a_series_a_field_good_rows_last_per_date_oldest_first(
    tmp_path: Path,
) -> None:
    seed(
        tmp_path,
        "a.jsonl",
        [
            {"date": "2026-09-22", "n": 1, "v": "1.0", "d": 4},
            {"date": "2026-09-20", "n": 2, "v": "0.9", "d": 5, "o": {"x": 1}},
            {"date": "2026-09-22", "n": 3, "d": 6},
            {"date": "2026-09-23", "status": "fetch_failed"},
            {"date": "2026-09-24", "n": 7, "v": "1.1", "d": 8},
        ],
    )
    shown = [
        Shown("n", "Stars"),
        Shown("n", "Opened", "change"),
        Shown("d", "Views", "daily"),
        Shown("v", "Version", "value"),
        Shown("o.x", "Nested"),
    ]

    (section,) = built(tmp_path, Section("src", "x/y", "a.jsonl", shown, "Things"))["sections"]

    assert section == {
        "source": "src",
        "name": "x/y",
        "title": "src: x/y",
        "label": "Things",
        "heading": "Things · x/y",
        "project": None,
        "file": "a.jsonl",
        "where": None,
        "dates": ["2026-09-20", "2026-09-22", "2026-09-24"],
        "fields": [
            {"field": "n", "label": "Stars", "kind": "snapshot", "values": [2, 3, 7]},
            {"field": "n", "label": "Opened", "kind": "change", "values": [2, 3, 7]},
            {"field": "d", "label": "Views", "kind": "daily", "values": [5, 6, 8]},
            {"field": "v", "label": "Version", "kind": "value", "values": ["0.9", None, "1.1"]},
            {"field": "o.x", "label": "Nested", "kind": "snapshot", "values": [1, None, None]},
        ],
    }


def release(tag: str, *downloads: Any) -> dict[str, Any]:
    return {"tag": tag, "assets": [{"name": f"a{n}", "downloads": d} for n, d in enumerate(downloads)]}


def day(n: int, releases: Any) -> dict[str, Any]:
    return {"date": f"2026-09-{n:02d}", "releases": releases}


def stack(tmp_path: Path, rows: list[dict[str, Any]], bands: str = "release") -> dict[str, Any]:
    """The breakdown, drawn as lines, of the downloads of the `releases` of `rows`, by tag."""
    seed(tmp_path, "r.jsonl", rows)
    shown = [Shown("releases[].assets[].downloads", "Downloads", "breakdown", by="tag", lines=True, bands=bands)]
    (section,) = built(tmp_path, Section("s", None, "r.jsonl", shown, "L"))["sections"]
    field: dict[str, Any] = section["fields"][0]
    return field


def counts(field: dict[str, Any]) -> dict[str, list[Any]]:
    """Each key's series of a breakdown."""
    return dict(zip(field["keys"], field["values"]))


def test_a_list_total_is_a_breakdown_of_each_items_count_by_its_key_without_a_v(tmp_path: Path) -> None:
    rows = [day(1, [release("v1.0.1", 3, 4), release("v1.1.0", 10)]), day(2, [release("v1.0.1", 3, 6)])]

    field = stack(tmp_path, rows)

    assert counts(field) == {"1.0.1": [7, 9], "1.1.0": [10, 10]}  # 1.1.0 left the list and keeps its count
    assert (field["versions"], field["bands"], field["lines"]) == (True, "release", True)


def test_each_items_count_is_the_greatest_it_has_had_so_it_never_falls(tmp_path: Path) -> None:
    rows = [
        day(1, [release("v1.0.0", 5), release("v1.1.0", 10)]),
        day(2, [release("v1.0.0", 6)]),  # 1.1.0 is gone: still 10
        day(3, [release("v1.0.0", 4), release("v1.2.0", 2)]),  # 1.0.0 dropped: stays 6
        day(4, [release("v1.0.0", 8), release("v1.1.0", 12), release("v1.2.0", 2)]),
    ]

    assert counts(stack(tmp_path, rows)) == {
        "1.0.0": [5, 6, 6, 8],
        "1.1.0": [10, 10, 10, 12],
        "1.2.0": [0, 0, 2, 2],  # 0 before its first listing
    }


def test_published_at_is_not_read_a_release_rises_from_0_at_its_first_listing(tmp_path: Path) -> None:
    def published(tag: str, at: Any, downloads: int) -> dict[str, Any]:
        return {**release(tag, downloads), "published_at": at}

    late = "2026-09-03T00:30:00Z"  # a UTC date past the row that first lists it
    rows = [
        day(1, [published("v1.0.0", None, 5)]),
        day(2, [published("v1.0.0", None, 6), published("v1.1.0", late, 3)]),
        day(3, [published("v1.0.0", None, 6), published("v1.1.0", late, 4)]),
    ]

    assert counts(stack(tmp_path, rows)) == {"1.0.0": [5, 6, 6], "1.1.0": [0, 3, 4]}


def test_a_prerelease_is_counted_apart_as_in_any_breakdown_of_versions(tmp_path: Path) -> None:
    rows = [day(1, [release("v1.0.0", 5), release("v1.1.0-beta.1", 2), release("v1.1.0-beta.2", 3)])]

    field = stack(tmp_path, rows)

    assert counts(field) == {"1.0.0": [5]}
    assert field["prerelease"] == [5]
    assert (field["betas"]["keys"], field["betas"]["values"]) == (["1.1.0"], [[5]])  # a band holds its tags' sum


def test_releases_are_banded_by_line_unless_the_field_says_release(tmp_path: Path) -> None:
    rows = [day(1, [release("v1.0.0", 5), release("v1.0.1", 2), release("v1.1.0", 1)])]

    assert counts(stack(tmp_path, rows, bands="line")) == {"1.0": [7], "1.1": [1]}


def test_without_prereleases_a_breakdown_of_versions_has_none_drawn_or_added_to_anything(tmp_path: Path) -> None:
    rows = [day(1, [release("v1.0.0", 5), release("v1.1.0-beta.1", 2), release("v1.1.0-beta.2", 3)])]
    seed(tmp_path, "r.jsonl", rows)
    shown = [
        Shown(
            "releases[].assets[].downloads", "D", "breakdown", by="tag", lines=True, bands="release", prereleases=False
        )
    ]

    (section,) = built(tmp_path, Section("s", None, "r.jsonl", shown, "L"))["sections"]
    (field,) = section["fields"]

    assert counts(field) == {"1.0.0": [5]}
    assert (field["other"], field["prerelease"], field["betas"]["keys"]) == ([0], [0], [])


def test_a_hidden_item_is_in_no_line_and_not_in_older(tmp_path: Path) -> None:
    rows = [day(1, [release(f"v1.{n}.0", n) for n in range(1, 9)])]
    seed(tmp_path, "r.jsonl", rows)
    shown = [Shown("releases[].assets[].downloads", "D", "breakdown", by="tag", lines=True, bands="release")]
    hide = [Hide("releases", "tag", frozenset({"1.8.0", "1.1.0"}))]  # the newest, and the oldest

    (section,) = built(tmp_path, Section("s", None, "r.jsonl", shown, "L", hide=hide))["sections"]
    (field,) = section["fields"]

    assert field["keys"] == ["1.3.0", "1.4.0", "1.5.0", "1.6.0", "1.7.0"]
    assert field["other"] == [2]  # 1.2.0 alone: the hidden ones are not added to anything


def published(tag: str, at: str | None, downloads: int) -> dict[str, Any]:
    return {**release(tag, downloads), "published_at": at}


def since(tmp_path: Path, rows: list[dict[str, Any]], hide: list[Hide] | None = None) -> dict[str, Any]:
    """The chart of the downloads of the `releases` of `rows` by days since each was published."""
    seed(tmp_path, "r.jsonl", rows)
    shown = [
        Shown(
            "releases[].assets[].downloads",
            "Since release",
            "breakdown",
            by="tag",
            lines=True,
            prereleases=False,
            since="published_at",
            bands="release",
        )
    ]
    section = Section("s", None, "r.jsonl", shown, "L", hide=hide or [])
    (built_section,) = built(tmp_path, section)["sections"]
    field: dict[str, Any] = built_section["fields"][0]
    return field


def test_a_chart_since_draws_only_items_started_on_or_after_the_first_row_by_days_since_their_start(
    tmp_path: Path,
) -> None:
    old, new = "2026-08-01T08:00:00Z", "2026-09-03T09:00:00Z"
    rows = [
        day(
            n,
            [published("v1.0.0", old, 50 + n), published("v1.1.0", new, 10 * n)]
            if n >= 3
            else [published("v1.0.0", old, 50 + n)],
        )
        for n in range(1, 7)
    ]

    field = since(tmp_path, rows)

    # day 0 is 2026-09-03; the readings run to the last row, 2026-09-06, and the axis to day 10, 7 past the last
    assert counts(field) == {"1.1.0": [30, 40, 50, 60, *[None] * 7]}
    assert (field["since"], field["starts"], field["lines"]) == (True, {"1.1.0": "2026-09-03"}, True)


def test_a_chart_since_carries_a_count_over_a_day_without_a_row_and_is_none_before_the_first_listing(
    tmp_path: Path,
) -> None:
    rows = [
        day(1, [published("v1.0.0", None, 1)]),
        day(2, [published("v1.0.0", None, 1), published("v1.1.0", "2026-09-03T00:30:00Z", 3)]),  # listed before day 0
        day(4, [published("v1.1.0", "2026-09-03T00:30:00Z", 5), published("v1.2.0", "2026-09-02T09:00:00Z", 7)]),
        day(5, [published("v1.1.0", "2026-09-03T00:30:00Z", 6), published("v1.2.0", "2026-09-02T09:00:00Z", 8)]),
    ]

    field = counts(since(tmp_path, rows))

    assert field["1.1.0"][:4] == [3, 5, 6, None]  # day 0, 2026-09-03, has no row: the 3 of 2026-09-02 stands
    assert field["1.2.0"][:4] == [None, None, 7, 8]  # published 09-02, first listed 09-04: no reading before that


def test_a_chart_since_counts_each_release_at_its_greatest_and_keeps_the_newest_five_only(tmp_path: Path) -> None:
    def at(n: int) -> str:
        return f"2026-09-0{n}T08:00:00Z"

    rows = [
        day(1, [published("v0.9.0", None, 0)]),
        *[
            day(
                n,
                [published(f"v1.{k}.0", at(k), 10 * n - k) for k in range(2, 9) if k <= n]
                + [published("v0.9.0", None, 0)],
            )
            for n in range(2, 9)
        ],
    ]
    rows[3]["releases"][0]["assets"][0]["downloads"] = 1  # 1.2.0 drops on 2026-09-04: it stays

    field = since(tmp_path, rows)

    assert field["keys"] == ["1.4.0", "1.5.0", "1.6.0", "1.7.0", "1.8.0"]  # 1.2.0 and 1.3.0 are not drawn
    assert set(field["other"]) <= {0, None}  # and are not added into "older"
    series = since(
        tmp_path / "all",
        [
            day(1, [published("v0.9.0", None, 0)]),
            day(2, [published("v1.2.0", at(2), 5)]),
            day(3, [published("v1.2.0", at(2), 3)]),
        ],
    )
    assert counts(series) == {"1.2.0": [5, 5, *[None] * 7]}


def test_a_chart_since_skips_an_item_without_a_readable_start_and_honors_hide(tmp_path: Path) -> None:
    rows = [
        day(1, [published("v1.0.0", None, 1)]),
        day(2, [published("v1.1.0", None, 2), published("v1.2.0", "soon", 3), published("v1.3.0", "2026-09-02", 4)]),
        day(3, [published("v1.3.0", "2026-09-02", 6), published("v1.4.0", "2026-09-03T01:00:00Z", 1)]),
    ]

    assert counts(since(tmp_path, rows)) == {"1.3.0": [4, 6, *[None] * 6], "1.4.0": [1, *[None] * 7]}
    hide = [Hide("releases", "tag", frozenset({"1.3.0"}))]
    assert counts(since(tmp_path / "hidden", rows, hide)) == {"1.4.0": [1, *[None] * 7]}


def test_a_chart_since_ends_a_week_past_the_newest_items_age_however_old_the_older_ones_are(tmp_path: Path) -> None:
    rows = [
        day(
            n,
            [published("v1.0.0", "2026-09-01T08:00:00Z", n)]
            + ([published("v1.1.0", "2026-09-15T08:00:00Z", 100 + n)] if n >= 15 else []),
        )
        for n in range(1, 21)
    ]

    field = since(tmp_path, rows)

    assert len(field["other"]) == 5 + 7 + 1  # the newest is 5 days old on the last row: its day 0-5, then 7 more
    assert counts(field) == {"1.0.0": list(range(1, 14)), "1.1.0": [115, 116, 117, 118, 119, 120, *[None] * 7]}


def test_lines_of_exact_releases_keep_the_newest_five_and_add_up_the_older_ones(tmp_path: Path) -> None:
    rows = [day(1, [release(f"v1.{n}.0", n) for n in range(1, 8)])]

    field = stack(tmp_path, rows)

    assert field["keys"] == ["1.3.0", "1.4.0", "1.5.0", "1.6.0", "1.7.0"]
    assert field["other"] == [3]  # 1.1.0 and 1.2.0


def test_a_row_without_a_list_is_none_and_the_carried_counts_stay(tmp_path: Path) -> None:
    rows = [day(1, [release("v1.0.0", 5)]), {"date": "2026-09-02"}, day(3, [release("v1.0.0", 6)])]

    assert counts(stack(tmp_path, rows)) == {"1.0.0": [5, None, 6]}


def test_a_list_total_skips_what_it_cannot_count(tmp_path: Path) -> None:
    odd = {"tag": "v1.0.0", "assets": [{"downloads": None}, {"downloads": True}, {"downloads": "7"}, {}, 4]}
    rows = [day(1, [odd, {"assets": [{"downloads": 9}]}, "junk", release("v1.1.0", 1)]), day(2, [release("v1.1.0", 2)])]

    assert counts(stack(tmp_path, rows)) == {"1.0.0": [0, 0], "1.1.0": [1, 2]}


def test_charts_are_fields_after_the_shown_ones_that_only_trends_draws(tmp_path: Path) -> None:
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 1, "m": 2}])
    section = Section("s", None, "a.jsonl", [Shown("n", "N")], "L", charts=[Shown("m", "M")])

    (built_section,) = built(tmp_path, section)["sections"]

    assert [field["field"] for field in built_section["fields"]] == ["n", "m"]


def test_a_section_with_only_charts_is_drawn_and_its_default_summary_lines_are_unchanged() -> None:
    plain = Section("s", None, "a.jsonl", [Shown("n", "N")], "L")
    with_charts = plain._replace(charts=[Shown("m", "M")])

    assert section_lines(with_charts) == section_lines(plain)


def test_a_field_in_a_chart_names_it_and_one_alone_does_not(tmp_path: Path) -> None:
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "a": 1, "b": 2, "c": 3}])
    shown = [Shown("a", "A", chart="Hours"), Shown("b", "B", chart="Hours"), Shown("c", "C")]

    (section,) = built(tmp_path, Section("s", None, "a.jsonl", shown, "L"))["sections"]

    assert [field.get("chart") for field in section["fields"]] == ["Hours", "Hours", None]


def test_a_complement_field_holds_100_less_each_value_and_says_so(tmp_path: Path) -> None:
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-23", "p": 99.9}, {"date": "2026-09-24", "rank": 3}])

    (section,) = built(tmp_path, Section("a", None, "a.jsonl", [Shown("p", "Ahead", complement=True)], "L"))["sections"]

    assert section["fields"] == [
        {"field": "p", "label": "Ahead", "kind": "snapshot", "complement": True, "values": [0.1, None]}
    ]


def test_a_section_reads_only_the_rows_its_where_keeps(tmp_path: Path) -> None:
    seed(tmp_path, "f.jsonl", [{"date": "2026-09-24", "topic_id": i, "views": 10 * i} for i in (1, 2)])

    (section,) = built(tmp_path, Section("f", "b", "f.jsonl", [Shown("views", "Views")], "Label", {"topic_id": 2}))[
        "sections"
    ]

    assert (section["where"], section["dates"], section["fields"][0]["values"]) == (
        {"topic_id": 2},
        ["2026-09-24"],
        [20],
    )


def test_a_section_with_no_good_row_has_no_dates_and_empty_series(tmp_path: Path) -> None:
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "status": "fetch_failed"}])

    (section,) = built(
        tmp_path, Section("a", None, "a.jsonl", [Shown("n", "N"), Shown("b", "B", "breakdown")], "Label")
    )["sections"]

    assert section["dates"] == []
    assert section["fields"] == [
        {"field": "n", "label": "N", "kind": "snapshot", "values": []},
        {"field": "b", "label": "B", "kind": "breakdown", "versions": False, "keys": [], "values": [], "other": []},
    ]


def breakdown(tmp_path: Path, *objects: Any, bands: str = "line") -> dict[str, Any]:
    """The breakdown field of `objects`, a row a day each, oldest first."""
    rows = [{"date": f"2026-09-{20 + n}", "by": obj} for n, obj in enumerate(objects)]
    seed(tmp_path, "b.jsonl", rows)
    shown = Shown("by", "By", "breakdown", bands=bands)
    (section,) = built(tmp_path, Section("s", None, "b.jsonl", [shown], "Label"))["sections"]
    field: dict[str, Any] = section["fields"][0]
    return field


def test_a_breakdown_is_the_newest_rows_top_keys_largest_first_with_the_rest_as_other(tmp_path: Path) -> None:
    # Seven keys in the newest row: k1 and k2 past the top five. "old", once most of the counts, is gone now.
    first = {"old": 90, "k1": 1, "k2": 1, **{f"k{n}": 2 for n in range(3, 8)}}
    newest = {"k1": 1, "k2": 2, **{f"k{n}": 10 * n for n in range(3, 8)}}

    field = breakdown(tmp_path, first, "not counts", newest)

    keys = ["k7", "k6", "k5", "k4", "k3"]
    assert field == {
        "field": "by",
        "label": "By",
        "kind": "breakdown",
        "versions": False,
        "keys": keys,
        "values": [[first.get(key, 0), None, newest[key]] for key in keys],
        "other": [90 + 1 + 1, None, 1 + 2],
    }


def test_a_breakdown_fills_its_top_from_older_keys_by_their_largest_share_on_any_date(tmp_path: Path) -> None:
    field = breakdown(tmp_path, {"gone": 1, "old": 50, "a": 1}, {"a": 5, "b": 1})

    assert (field["versions"], field["keys"], field["values"], field["other"]) == (
        False,
        ["a", "b", "old", "gone"],
        [[1, 5], [0, 1], [50, 0], [1, 0]],
        [0, 0],
    )


def test_a_breakdown_of_versions_is_by_release_line_its_five_newest_oldest_first_the_older_ones_as_other(
    tmp_path: Path,
) -> None:
    # Seven lines: 1.8 and 1.9 are older than the newest five. Prereleases are no line's: they are counted apart.
    first = {"1.8.3": 7, "1.9.0": 50, "1.10.0": 30, "1.10.1": 4, "2.0.0-beta.1": 5}
    newest = {"1.10.1": 20, "2.0.0": 40, "2.0.1": 9, "2.1.0-beta.2": 3, "2.1.0": 2, "2.2": 1, "3": 6, "3.1-rc.1": 2}

    field = breakdown(tmp_path, first, newest)

    assert (field["versions"], field["keys"]) == (True, ["1.10", "2.0", "2.1", "2.2", "3"])
    assert field["values"] == [[34, 20], [0, 49], [0, 2], [0, 1], [0, 6]]
    assert field["other"] == [57, 0]
    assert field["prerelease"] == [5, 5]  # every day's counts add up: the bands, other, and the prereleases


def test_a_breakdown_of_versions_by_release_is_every_release_oldest_first_with_no_cap_and_no_other(
    tmp_path: Path,
) -> None:
    first = {"3.2.0": 9, "3.10.0": 5, "3.9.1": 4, "3.9.0": 3, "3.10.0-beta.1": 2}
    newest = {**{f"3.{n}.0": n for n in range(11, 20)}, "3.10.1": 7, "3.10.0": 1, "3.20.0-beta.1": 4}

    field = breakdown(tmp_path, first, newest, bands="release")

    assert field["bands"] == "release"
    assert field["keys"] == ["3.2.0", "3.9.0", "3.9.1", "3.10.0", "3.10.1", *(f"3.{n}.0" for n in range(11, 20))]
    assert len(field["values"]) == len(field["keys"]) == 14
    assert field["values"][0] == [9, 0]  # 3.2.0
    assert field["values"][3] == [5, 1]  # 3.10.0
    assert field["other"] == [0, 0]
    assert field["prerelease"] == [2, 4]  # prereleases are as they are by line
    assert field["betas"]["keys"] == ["3.10.0", "3.20.0"]


def test_a_breakdown_of_versions_by_line_is_the_default_and_says_nothing_of_it(tmp_path: Path) -> None:
    row = {"3.13.0": 1, "3.13.1": 2, "3.14.0": 3}

    assert breakdown(tmp_path, row) == breakdown(tmp_path, row, bands="line")
    assert "bands" not in breakdown(tmp_path, row)
    assert breakdown(tmp_path, row)["keys"] == ["3.13", "3.14"]


def test_the_bands_of_a_breakdown_that_is_not_of_versions_are_not_read(tmp_path: Path) -> None:
    field = breakdown(tmp_path, {"a": 1, "b": 2}, bands="release")

    assert (field["versions"], "bands" in field, field["keys"]) == (False, False, ["b", "a"])


def test_a_breakdown_of_versions_counts_each_base_versions_prereleases_apart_the_newest_five_bases(
    tmp_path: Path,
) -> None:
    # 2.0.0 has two prereleases and a release; 1.9.0's only prerelease is gone from the newest row; 2.1.0 is new.
    first = {"1.9.0-beta.1": 4, "2.0.0-beta.1": 5, "2.0.0": 3}
    newest = {"2.0.0-beta.1": 1, "2.0.0-beta.2": 2, "2.0.0": 6, "2.1.0-rc.1": 7, "3-beta.1": 1}

    field = breakdown(tmp_path, first, "not counts", newest)

    assert field["betas"] == {
        "keys": ["1.9.0", "2.0.0", "2.1.0", "3"],  # by version, oldest first
        "values": [[4, None, 0], [5, None, 3], [0, None, 7], [0, None, 1]],
        "other": [0, None, 0],
    }
    assert field["prerelease"] == [9, None, 11]  # still every prerelease's total


def test_the_betas_of_older_bases_beyond_the_newest_five_are_added_up_as_other(tmp_path: Path) -> None:
    row = {f"1.{n}.0-beta.1": n for n in range(1, 8)}

    betas = breakdown(tmp_path, row)["betas"]

    assert betas["keys"] == [f"1.{n}.0" for n in range(3, 8)]
    assert betas["other"] == [1 + 2]


def test_a_breakdown_of_versions_without_prereleases_has_no_beta_bands(tmp_path: Path) -> None:
    assert breakdown(tmp_path, {"1.0.0": 5})["betas"] == {"keys": [], "values": [], "other": [0]}


def test_a_breakdown_that_is_not_of_versions_has_no_betas_either(tmp_path: Path) -> None:
    assert "betas" not in breakdown(tmp_path, {"1.0.0-beta": 5, "x": 1})


@pytest.mark.parametrize(
    ("label", "title"),
    [("catalog_by_status", "Catalog by status"), ("Opened by kind", "Opened by kind"), ("by_Status", "By Status")],
    ids=["a config key", "a title", "only the first letter changes"],
)
def test_a_breakdowns_label_reads_as_a_title_underscores_as_spaces_the_first_letter_capital(
    tmp_path: Path, label: str, title: str
) -> None:
    seed(tmp_path, "b.jsonl", [{"date": "2026-09-24", "by": {"x": 1}}])

    (section,) = built(tmp_path, Section("s", None, "b.jsonl", [Shown("by", label, "breakdown")], "L"))["sections"]

    assert section["fields"][0]["label"] == title


def test_the_label_of_a_field_into_a_stack_is_made_a_title_too(tmp_path: Path) -> None:
    seed(tmp_path, "b.jsonl", [{"date": "2026-09-24", "n": 1, "by": {"x": 1}}])
    shown = [Shown("n", "config_files", into="by"), Shown("by", "By", "breakdown")]

    (section,) = built(tmp_path, Section("s", None, "b.jsonl", shown, "L"))["sections"]

    assert section["fields"][0]["label"] == "Config files"


def test_the_keys_of_a_breakdown_are_never_changed(tmp_path: Path) -> None:
    field = breakdown(tmp_path, {"awaiting_verification": 2, "new_modem": 1})

    assert field["keys"] == ["awaiting_verification", "new_modem"]


def test_only_a_breakdowns_label_is_made_a_title(tmp_path: Path) -> None:
    seed(tmp_path, "b.jsonl", [{"date": "2026-09-24", "n": 1}])

    (section,) = built(tmp_path, Section("s", None, "b.jsonl", [Shown("n", "configs_total")], "L"))["sections"]

    assert section["fields"][0]["label"] == "configs_total"


def test_a_breakdown_of_versions_without_prereleases_counts_none(tmp_path: Path) -> None:
    field = breakdown(tmp_path, {"1.0.0": 5}, "not counts")

    assert field["prerelease"] == [0, None]


def test_a_breakdown_that_is_not_of_versions_has_no_prerelease_series(tmp_path: Path) -> None:
    assert "prerelease" not in breakdown(tmp_path, {"1.0.0-beta": 5, "x": 1})


def test_a_breakdowns_neutral_keys_are_kept_after_the_top_keys_wherever_they_rank(tmp_path: Path) -> None:
    rows = [{"date": "2026-09-24", "by": {"none": 90, **{f"k{n}": n for n in range(1, 8)}}}]
    seed(tmp_path, "b.jsonl", rows)
    shown = Shown("by", "By", "breakdown", neutral=("none", "absent"))

    (section,) = built(tmp_path, Section("s", None, "b.jsonl", [shown], "Label"))["sections"]
    field = section["fields"][0]

    assert field["keys"] == ["k7", "k6", "k5", "k4", "k3", "none"]
    assert field["neutral"] == ["none"]  # a neutral key no row has is no band
    assert field["other"] == [3]  # k1 and k2


def test_a_breakdown_is_of_versions_only_when_every_key_is_one(tmp_path: Path) -> None:
    field = breakdown(tmp_path, {"1.0.0": 5, "v1.1.0": 2})

    assert (field["versions"], field["keys"]) == (False, ["1.0.0", "v1.1.0"])


def test_a_field_not_charted_alone_says_so_and_a_field_into_a_breakdown_names_it(tmp_path: Path) -> None:
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 5, "m": 1, "b": {"x": 5}}])
    shown = [
        Shown("n", "N", into="b"),
        Shown("m", "M", charted=False),
        Shown("b", "B", "breakdown"),
    ]

    (section,) = built(tmp_path, Section("s", None, "a.jsonl", shown, "Label"))["sections"]
    n, m, b = section["fields"]

    assert (n["charted"], n["into"]) == (False, "b")
    assert (m["charted"], "into" in m) == (False, False)
    assert "charted" not in b


@pytest.mark.parametrize(
    ("shown", "bug"),
    [
        ([Shown("n", "N", into="b")], "n: into: names no breakdown of the summary"),
        ([Shown("n", "N", into="m"), Shown("m", "M")], "n: into: names no breakdown of the summary"),
        ([Shown("b", "B", "breakdown", into="b")], "b: into: only a field that is not a breakdown"),
        ([Shown("n", "N", neutral=("x",))], "n: neutral: only a breakdown has keys"),
        ([Shown("n", "N", bands="release")], "n: bands: only a breakdown has them"),
        ([Shown("b", "B", "breakdown", bands="x")], "b: bands: must be one of line, release, not 'x'"),
        ([Shown("n", "N", "breakdown", by="k")], "n: by: needs a field with a list to total, like a[].n"),
        ([Shown("a[].n", "N")], "a[].n: [] in the field: needs by"),
        ([Shown("a[].n", "N", by="k")], "a[].n: by: only a breakdown takes it"),
        ([Shown("n", "N", lines=True)], "n: lines: only a breakdown takes it"),
        ([Shown("n", "N", half=True)], "n: half: only a breakdown takes it"),
        ([Shown("n", "N", trend=True)], "n: trend: only a daily count takes it"),
        ([Shown("n", "N", color="s9")], "n: color: must be one of s1, s2, s3, s4, s5, s6, not 's9'"),
        ([Shown("n", "N", since="d")], "n: since: needs by and lines"),
        ([Shown("a[].n", "N", "breakdown", by="k", since="d")], "a[].n: since: needs by and lines"),
        (
            [Shown("a[].n", "N", "breakdown", by="k", lines=True, since="d")],
            "a[].n: since: needs bands release, one line a release",
        ),
        ([Shown("n", "N", prereleases=False)], "n: prereleases: only a breakdown takes it"),
        ([Shown("n", "N", "daily", chart="C")], "n: chart: only a snapshot or change takes it"),
        ([Shown("b", "B", "breakdown", chart="C")], "b: chart: only a snapshot or change takes it"),
        ([Shown("n", "N", charted=False, chart="C")], "n: chart: not with charted=False"),
        ([Shown("n", "N", into="b", chart="C"), Shown("b", "B", "breakdown")], "n: chart: not with into"),
    ],
    ids=[
        "no such field",
        "a field that is no breakdown",
        "a breakdown into",
        "neutral",
        "bands",
        "bad bands",
        "by without a list",
        "a list without by",
        "by on a line",
        "lines on a line",
        "half on a line",
        "trend on a line",
        "a color that is no token",
        "since without a list",
        "since without lines",
        "since with line bands",
        "prereleases on a line",
        "a chart of bars",
        "a chart of a stack",
        "a chart off the page",
        "a chart into a stack",
    ],
)
def test_a_field_that_cannot_be_charted_so_is_a_bug(tmp_path: Path, shown: list[Shown], bug: str) -> None:
    ctx = context(tmp_path)

    trends.write(ctx, [Section("a", None, "a.jsonl", shown, "Label")], HEALTH)

    assert ctx.critical == [Critical("trends", None, f"crashed: ValueError: {bug}")]


def three(tmp_path: Path, projects: Any) -> list[tuple[str, Any]]:
    """The titles and projects of three sections, a, b: x and c, as trends.json lists them with `projects`."""
    sections = [
        Section("a", None, "a.jsonl", [], "Label"),
        Section("b", "x", "b.jsonl", [], "Label"),
        Section("c", None, "c.jsonl", [], "Label"),
    ]
    return [(s["title"], s["project"]) for s in built(tmp_path, *sections, projects=projects)["sections"]]


def test_without_projects_the_sections_are_in_config_order_in_no_project(tmp_path: Path) -> None:
    assert three(tmp_path, None) == [("a", None), ("b: x", None), ("c", None)]


def test_projects_order_the_sections_each_projects_in_its_lists_order_then_the_rest_in_config_order(
    tmp_path: Path,
) -> None:
    projects = [{"name": "Two", "sections": ["c", "a"]}]

    assert three(tmp_path, projects) == [("c", "Two"), ("a", "Two"), ("b: x", None)]


def test_projects_follow_their_config_order(tmp_path: Path) -> None:
    projects = [{"name": "Y", "sections": ["c"]}, {"name": "X", "sections": ["b: x", "a"]}]

    assert three(tmp_path, projects) == [("c", "Y"), ("b: x", "X"), ("a", "X")]


def headings(tmp_path: Path, projects: Any) -> list[tuple[str, str]]:
    """The title and heading of each of four sections, as trends.json lists them with `projects`: two of source s, one
    of t, and u, a source with one entry."""
    sections = [
        Section("s", "one", "s1.jsonl", [], "Things"),
        Section("s", "two", "s2.jsonl", [], "Things"),
        Section("t", "one", "t.jsonl", [], "Tees"),
        Section("u", None, "u.jsonl", [], "You"),
    ]
    return [(s["title"], s["heading"]) for s in built(tmp_path, *sections, projects=projects)["sections"]]


def test_without_projects_a_heading_is_the_label_and_the_entry_name(tmp_path: Path) -> None:
    assert headings(tmp_path, None) == [
        ("s: one", "Things · one"),
        ("s: two", "Things · two"),
        ("t: one", "Tees · one"),
        ("u", "You"),
    ]


def test_in_a_project_a_heading_is_the_label_and_the_name_only_where_the_project_has_two_of_its_source(
    tmp_path: Path,
) -> None:
    projects = [{"name": "P", "sections": ["s: one", "t: one"]}, {"name": "Q", "sections": ["u", "s: two"]}]

    assert headings(tmp_path, projects) == [
        ("s: one", "Things"),
        ("t: one", "Tees"),
        ("u", "You"),
        ("s: two", "Things"),
    ]
    assert headings(tmp_path, [{"name": "P", "sections": ["s: two", "s: one"]}]) == [
        ("s: two", "Things · two"),
        ("s: one", "Things · one"),
        ("t: one", "Tees · one"),  # in no project: its whole identity
        ("u", "You"),
    ]


def test_a_projects_section_may_give_its_own_heading(tmp_path: Path) -> None:
    projects = [{"name": "P", "sections": [{"section": "s: one", "title": "Catalog"}, "s: two"]}]

    assert headings(tmp_path, projects)[:2] == [("s: one", "Catalog"), ("s: two", "Things · two")]


TITLES = ["a", "b: x", "c"]
SECTION_FORM = '{"section": a section title, "title": its heading}'


@pytest.mark.parametrize(
    ("projects", "problems"),
    [
        (None, []),
        ([{"name": "A", "sections": ["a"]}, {"name": "B", "sections": ["b: x", "c"]}], []),
        ({"name": "A"}, [": must be a list of projects"]),
        (["A"], ['[0]: must be {"name": a display name, "sections": a list of section titles}']),
        (
            [{"name": "A", "sections": ["a"], "x": 1}],
            ['[0]: must be {"name": a display name, "sections": a list of section titles}'],
        ),
        ([{"sections": ["a"]}], ['[0]: must be {"name": a display name, "sections": a list of section titles}']),
        ([{"name": "", "sections": ["a"]}], ["[0].name: must be a non-empty string"]),
        ([{"name": "A", "sections": []}], ["[0].sections: must be a non-empty list of section titles"]),
        ([{"name": "A", "sections": "a"}], ["[0].sections: must be a non-empty list of section titles"]),
        (
            [{"name": "A", "sections": ["a"]}, {"name": "A", "sections": ["c"]}],
            ['[1].name: "A" is also projects[0]\'s'],
        ),
        (
            [{"name": "A", "sections": ["a", 5, "nope"]}],
            ["[0].sections[1]: 5 names no section", '[0].sections[2]: "nope" names no section'],
        ),
        (
            [{"name": "A", "sections": ["a", "c"]}, {"name": "B", "sections": ["c"]}],
            ['[1].sections[0]: "c" is also in projects[0]'],
        ),
        ([{"name": "A", "sections": ["a", "a"]}], ['[0].sections[1]: "a" is also in projects[0]']),
        ([{"name": "A", "sections": [{"section": "a", "title": "Alpha"}, "c"]}], []),
        (
            [{"name": "A", "sections": [{"section": "nope", "title": "N"}]}],
            ['[0].sections[0].section: "nope" names no section'],
        ),
        (
            [{"name": "A", "sections": [{"section": "a", "title": ""}, {"section": "c", "title": 5}]}],
            ["[0].sections[0].title: must be a non-empty string", "[0].sections[1].title: must be a non-empty string"],
        ),
        (
            [{"name": "A", "sections": [{"section": "a"}, {"section": "c", "title": "C", "x": 1}]}],
            [f"[0].sections[{j}]: must be a section title or {SECTION_FORM}" for j in (0, 1)],
        ),
        (
            [{"name": "A", "sections": ["a", {"section": "a", "title": "Alpha"}]}],
            ['[0].sections[1].section: "a" is also in projects[0]'],
        ),
    ],
)
def test_projects_are_validated_against_the_section_titles(projects: Any, problems: list[str]) -> None:
    assert trends.validate(projects, TITLES) == problems


PROJECTS = [{"name": n, "sections": [n.lower()]} for n in ("A", "B", "C")]
PAGE_FORM = '{"file": a *.html name, "title": its heading, "projects": a list of project names}'


def a_page(**keys: Any) -> dict[str, Any]:
    return {"file": "one.html", "title": "One", "projects": ["A"], **keys}


@pytest.mark.parametrize(
    ("pages", "problems"),
    [
        (None, []),
        ([a_page(projects=["C", "A"]), a_page(file="trends.html", title="All", projects=["B", "A"])], []),
        (a_page(), [": must be a non-empty list of pages"]),
        ([], [": must be a non-empty list of pages"]),
        (["one.html"], [f"[0]: must be {PAGE_FORM}"]),
        ([{"file": "one.html", "title": "One"}], [f"[0]: must be {PAGE_FORM}"]),
        ([a_page(x=1)], [f"[0]: must be {PAGE_FORM}"]),
        *(
            ([a_page(file=file)], [f"[0].file: must be a plain *.html name, not {json.dumps(file)}"])
            for file in ("one.htm", "a/one.html", "a\\one.html", ".html", 5)
        ),
        ([a_page(file="One.html"), a_page()], ['[1].file: "one.html" is also pages[0]\'s']),
        (
            [a_page(title=""), a_page(file="two.html", title=None)],
            [
                "[0].title: must be a non-empty string",
                "[1].title: must be a non-empty string",
            ],
        ),
        ([a_page(projects=[])], ["[0].projects: must be a non-empty list of project names"]),
        ([a_page(projects="A")], ["[0].projects: must be a non-empty list of project names"]),
        (
            [a_page(projects=["A", "Z", 5, "A"])],
            [
                '[0].projects[1]: "Z" names no project',
                "[0].projects[2]: 5 names no project",
                '[0].projects[3]: "A" is already on this page',
            ],
        ),
    ],
)
def test_pages_are_validated_against_the_projects(pages: Any, problems: list[str]) -> None:
    assert trends.validate_pages(pages, PROJECTS) == problems


def test_pages_need_projects() -> None:
    assert trends.validate_pages([a_page()], None) == [": needs projects, whose names a page lists"]


def test_the_health_is_the_newest_health_rows_findings_its_checks_at_warn_or_error(tmp_path: Path) -> None:
    failed = [{"file": "a.jsonl", "last": "2026-09-24", "status": "fetch_failed", "streak": 2, "since": "2026-09-23"}]
    stale = [{"file": "b.jsonl", "last": "2026-09-20", "age_days": 4}]
    critical = [{"source": "a", "file": "a.jsonl", "reason": "no good row for 2 nights, since 2026-09-23"}]
    checks = [
        {"source": "a", "level": "ok", "message": "fine"},
        {"source": "b", "level": "warn", "message": "odd"},
        {"source": "c", "level": "error", "message": "wrong"},
    ]
    seed(
        tmp_path,
        HEALTH,
        [
            health_row(date="2026-09-23"),
            health_row(failed_count=1, failed=failed, stale_count=1, stale=stale, critical=critical, checks=checks),
        ],
    )

    assert built(tmp_path)["health"] == {
        "date": "2026-09-24",
        "stale": stale,
        "failed": failed,
        "critical": critical,
        "checks": checks[1:],
    }


def test_the_health_is_null_with_no_health_row(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)

    assert built(tmp_path)["health"] is None


# ---- trends.html ---------------------------------------------------------------------------------


EMBEDDED = re.compile(r'<script type="application/json" id="trends-data">(.*?)</script>', re.S)


def sample_sections(tmp_path: Path) -> list[Section]:
    seed(tmp_path, HEALTH, [health_row()])
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 5, "v": "1.0", "d": 2, "b": {"x": 1}}])
    shown = [Shown("n", "N"), Shown("v", "Version", "value"), Shown("d", "D", "daily"), Shown("b", "B", "breakdown")]
    return [Section("a", "x", "a.jsonl", shown, "Aye"), Section("c", None, "c.jsonl", [Shown("n", "M")], "Sea")]


def sample(tmp_path: Path, projects: Any = None) -> dict[str, Any]:
    return built(tmp_path, *sample_sections(tmp_path), projects=projects)


def test_the_page_is_titled_trends_and_a_pages_by_its_title(tmp_path: Path) -> None:
    data = sample(tmp_path)

    default, titled = trends.page(data), trends.page(data, "Ours & theirs")

    assert ("<title>leadline trends</title>" in default, "<h1>Trends</h1>" in default) == (True, True)
    assert ("<title>Ours &amp; theirs</title>" in titled, "<h1>Ours &amp; theirs</h1>" in titled) == (True, True)


def test_the_page_embeds_exactly_the_data(tmp_path: Path) -> None:
    data = sample(tmp_path)

    (embedded,) = EMBEDDED.findall(trends.page(data))

    assert json.loads(embedded) == data


def test_the_page_loads_nothing_from_anywhere(tmp_path: Path) -> None:
    page = trends.page(sample(tmp_path, [{"name": "P", "sections": ["c"]}]))

    assert "http" not in page
    assert not re.search(r"\bsrc\s*=", page, re.I)
    assert re.findall(r"\bhref\s*=\s*\"([^\"]*)\"", page, re.I) == ["#project-0", "#project-1"]  # links within it
    assert len(re.findall(r"\bhref\s*=", page, re.I)) == 2
    assert "@import" not in page
    assert "url(" not in page


def test_the_page_has_the_range_buttons_90_days_chosen(tmp_path: Path) -> None:
    page = trends.page(sample(tmp_path))

    buttons = re.findall(r'<button type="button" data-days="(\d+)" aria-pressed="(true|false)">([^<]+)</button>', page)

    assert buttons == [("30", "false", "30d"), ("90", "true", "90d"), ("365", "false", "1y"), ("0", "false", "All")]


def test_the_page_has_a_chart_for_each_charted_field_and_shows_values_as_text(tmp_path: Path) -> None:
    page = trends.page(sample(tmp_path))

    charts = re.findall(
        r'<figure class="chart[^"]*" data-section="(\d+)" data-field="(\d+)">\s*<figcaption>([^<]*)<', page
    )

    assert charts == [("0", "3", "B"), ("0", "0", "N"), ("0", "2", "D"), ("1", "0", "M")]  # the stack first
    assert '<h2><span class="head">Aye · x</span><span class="val">Version: 1.0</span></h2>' in page
    assert '<h2><span class="head">Sea</span></h2>' in page
    assert '<span class="pill ok">All sources current</span>' in page


def stacked_page(tmp_path: Path) -> str:
    """A page of one section: a total that is into a stack, a field off the page, a line, and the stack, which is
    listed last; the line's first reading comes 10 days after the section's."""
    rows = [
        {"date": f"2026-09-{day:02d}", "total": 3, "off": 1, "line": 4 if day >= 11 else None, "by": {"x": 1, "y": 2}}
        for day in (1, 11, 12)
    ]
    seed(tmp_path, "a.jsonl", rows)
    shown = [
        Shown("total", "Total", into="by"),
        Shown("off", "Off", charted=False),
        Shown("line", "Line"),
        Shown("by", "By", "breakdown"),
    ]
    return trends.page(built(tmp_path, Section("s", None, "a.jsonl", shown, "Label")))


def test_the_page_charts_no_field_that_is_into_a_stack_or_off_it(tmp_path: Path) -> None:
    page = stacked_page(tmp_path)

    assert re.findall(r'<figure class="chart[^"]*" data-section="0" data-field="(\d+)"', page) == ["3", "2"]


def test_the_page_draws_a_stack_first_on_a_wide_card(tmp_path: Path) -> None:
    page = stacked_page(tmp_path)

    classes = re.findall(r'<figure class="(chart[^"]*)" data-section="0" data-field="\d+"', page)
    assert classes == ["chart wide", "chart wide"]  # the line stands alone beside the stack: see the next test


def test_a_chart_is_wide_when_it_is_a_stack_or_the_only_chart_in_its_section_besides_the_stacks(
    tmp_path: Path,
) -> None:
    rows = [{"date": "2026-09-24", "a": 1, "b": 2, "c": 3, "by": {"x": 1}}]
    seed(tmp_path, "a.jsonl", rows)
    shown = [Shown("a", "A"), Shown("b", "B"), Shown("c", "C", "daily"), Shown("by", "By", "breakdown")]
    more = trends.page(built(tmp_path, Section("s", None, "a.jsonl", shown, "Label")))
    alone = trends.page(built(tmp_path, Section("s", None, "a.jsonl", [shown[0], shown[3]], "Label")))
    none = trends.page(built(tmp_path, Section("s", None, "a.jsonl", shown[:2], "Label")))

    def classes(page: str) -> list[str]:
        return re.findall(r'<figure class="(chart[^"]*)" data-section="0"', page)

    assert classes(more) == ["chart wide", "chart", "chart", "chart"]
    assert classes(alone) == ["chart wide", "chart wide"]
    assert classes(none) == ["chart", "chart"]


def test_breakdowns_that_are_half_width_share_a_row_and_say_so_in_the_data(tmp_path: Path) -> None:
    rows = [{"date": "2026-09-24", "a": 1, "x": {"p": 1}, "y": {"q": 2}}]
    seed(tmp_path, "a.jsonl", rows)
    shown = [Shown("x", "X", "breakdown", half=True), Shown("y", "Y", "breakdown", half=True), Shown("a", "A")]
    data = built(tmp_path, Section("s", None, "a.jsonl", shown, "Label"))

    page = trends.page(data)

    assert [field.get("half") for field in data["sections"][0]["fields"]] == [True, True, None]
    assert re.findall(r'<figure class="(chart[^"]*)" data-section="0"', page) == ["chart", "chart", "chart"]


def test_a_field_with_a_trend_or_a_color_says_so_in_the_data(tmp_path: Path) -> None:
    rows = [{"date": "2026-09-24", "d": 1, "e": 2, "f": 3}]
    seed(tmp_path, "a.jsonl", rows)
    shown = [
        Shown("d", "Views", "daily", trend=True, color="s1"),
        Shown("e", "Clones", "daily"),
        Shown("f", "F", color="s4"),
    ]

    (section,) = built(tmp_path, Section("s", None, "a.jsonl", shown, "Label"))["sections"]

    assert [(f.get("trend"), f.get("color")) for f in section["fields"]] == [(True, "s1"), (None, None), (None, "s4")]


def two_cards(folder: Path, *sections: Section, projects: Any = None) -> dict[str, Any]:
    rows = [{"date": "2026-09-24", "n": 1}]
    for name in ("a.jsonl", "b.jsonl", "c.jsonl", "d.jsonl"):
        seed(folder, name, rows)
    return built(folder, *sections, projects=projects)


ROLLING = Card("Traffic", "Rolling 14 days", "A total of 14 days.")
BY_DAY = Card("Traffic", "By day", "One bar a day.")


def test_a_section_of_a_card_names_the_card_its_row_and_what_the_row_is_and_is_headed_by_the_card(
    tmp_path: Path,
) -> None:
    data = two_cards(
        tmp_path,
        Section("r", "x/y", "a.jsonl", [Shown("n", "N")], "Rolling", card=ROLLING),
        Section("d", "x/y", "b.jsonl", [Shown("n", "N")], "Daily", card=BY_DAY),
        Section("o", "x/y", "c.jsonl", [Shown("n", "N")], "Other"),
    )

    rolling, daily, other = data["sections"]

    assert (rolling["card"], rolling["row"], rolling["about"]) == ("Traffic", "Rolling 14 days", "A total of 14 days.")
    assert (daily["card"], daily["row"], daily["about"]) == ("Traffic", "By day", "One bar a day.")
    assert [rolling["heading"], daily["heading"], other["heading"]] == ["Traffic · x/y", "Traffic · x/y", "Other · x/y"]
    assert not {"card", "row", "about"} & set(other)  # optional keys: a section with no card has none


def test_in_a_project_a_card_section_is_alone_among_the_sections_of_its_source_that_name_the_card(
    tmp_path: Path,
) -> None:
    sections = [
        Section("s", "x/y", "a.jsonl", [Shown("n", "N")], "Things"),
        Section("s", "x/y", "b.jsonl", [Shown("n", "N")], "Things", card=ROLLING, part="more"),
        Section("t", "x/y", "c.jsonl", [Shown("n", "N")], "Tees", card=BY_DAY),
    ]
    projects = [{"name": "P", "sections": ["s: x/y", "s: x/y more", "t: x/y"]}]

    data = two_cards(tmp_path, *sections, projects=projects)

    assert [(s["title"], s["heading"]) for s in data["sections"]] == [
        ("s: x/y", "Things"),
        ("s: x/y more", "Traffic"),
        ("t: x/y", "Traffic"),
    ]
    assert len(sections_of(trends.page(data))) == 2  # the plain panel, and one Traffic card of two rows


def sections_of(page: str) -> list[str]:
    """The text of each `<section>` of a page."""
    return re.findall(r"<section>.*?</section>", page, re.S)


def test_sections_of_one_card_and_entry_are_one_panel_of_rows_the_first_row_first(tmp_path: Path) -> None:
    data = two_cards(
        tmp_path,
        Section("r", "x/y", "a.jsonl", [Shown("n", "N")], "Rolling", card=ROLLING),
        Section("r", "p/q", "b.jsonl", [Shown("n", "N")], "Rolling", card=ROLLING),
        Section("d", "x/y", "c.jsonl", [Shown("n", "N")], "Daily", card=BY_DAY),
        Section("o", "x/y", "d.jsonl", [Shown("n", "N")], "Other"),
    )

    panels = sections_of(trends.page(data))

    assert len(panels) == 3  # Traffic of x/y (two rows), Traffic of p/q (one), Other
    first = panels[0]
    assert re.findall(r'<span class="head">(.*?)</span>', first) == ["Traffic \u00b7 x/y"]
    assert re.findall(r"<h4>(.*?)</h4>", first) == ["Rolling 14 days", "By day"]
    assert re.findall(r'<p class="about">(.*?)</p>', first) == ["A total of 14 days.", "One bar a day."]
    assert re.findall(r'data-section="(\d)"', first) == ["0", "2"]
    assert re.findall(r"<h4>(.*?)</h4>", panels[1]) == ["Rolling 14 days"]
    assert "<h4>" not in panels[2]


def test_in_a_project_the_sections_of_a_card_are_one_panel_whatever_their_order_in_the_config(tmp_path: Path) -> None:
    sections = [
        Section("r", "x/y", "a.jsonl", [Shown("n", "N")], "Rolling", card=ROLLING),
        Section("o", "x/y", "b.jsonl", [Shown("n", "N")], "Other"),
        Section("d", "x/y", "c.jsonl", [Shown("n", "N")], "Daily", card=BY_DAY),
    ]
    projects = [{"name": "P", "sections": ["r: x/y", "o: x/y", "d: x/y"]}]

    panels = sections_of(trends.page(two_cards(tmp_path, *sections, projects=projects)))

    assert [re.findall(r'<span class="head">(.*?)</span>', panel) for panel in panels] == [["Traffic"], ["Other"]]
    assert re.findall(r"<h4>(.*?)</h4>", panels[0]) == ["Rolling 14 days", "By day"]


def test_the_tooltip_is_above_the_sticky_nav_so_it_is_never_hidden_under_it(tmp_path: Path) -> None:
    style = trends._STYLE

    nav = int(re.search(r"\nnav \{[^}]*z-index: (\d+)", style).group(1))  # type: ignore[union-attr]
    tip = int(re.search(r"\n\.tip \{[^}]*z-index: (\d+)", style).group(1))  # type: ignore[union-attr]

    assert tip > nav


def lined_page(tmp_path: Path, shown: list[Shown]) -> str:
    rows = [
        {"date": f"2026-09-{day:02d}", "a": 1, "b": 2, "c": 3, "d": 4 if day >= 11 else None, "e": 5}
        for day in (1, 11, 12)
    ]
    seed(tmp_path, "a.jsonl", rows)
    return trends.page(built(tmp_path, Section("s", None, "a.jsonl", shown, "Label")))


def test_the_fields_of_a_chart_are_one_figure_captioned_by_its_name_at_its_first_fields_place(tmp_path: Path) -> None:
    shown = [Shown("a", "A"), Shown("b", "B", chart="Hours"), Shown("c", "C"), Shown("e", "E", chart="Hours")]

    page = lined_page(tmp_path, shown)

    figures = re.findall(r'<figure class="chart[^"]*" data-section="0" data-field="(\d+)"><figcaption>([^<]*)', page)
    assert figures == [("0", "A"), ("1", "Hours"), ("2", "C")]


def test_a_chart_alone_in_its_section_is_wide(tmp_path: Path) -> None:
    page = lined_page(tmp_path, [Shown("a", "A", chart="Hours"), Shown("b", "B", chart="Hours")])

    assert re.findall(r'<figure class="(chart[^"]*)"', page) == ["chart wide"]


def test_a_chart_starts_late_when_all_its_fields_do(tmp_path: Path) -> None:
    both = lined_page(tmp_path, [Shown("a", "A"), Shown("d", "D", chart="X"), Shown("d", "D2", chart="X")])
    one = lined_page(tmp_path / "one", [Shown("a", "A"), Shown("a", "A2", chart="X"), Shown("d", "D", chart="X")])

    assert re.findall(r'<p class="note">([^<]*)</p>', both) == ["Counted from 2026-09-11"]
    assert re.findall(r'<p class="note">([^<]*)</p>', one) == []


def test_a_chart_that_starts_more_than_7_days_after_its_section_says_when_it_was_counted_from(tmp_path: Path) -> None:
    rows = [
        {"date": "2026-09-01", "early": 1},
        {"date": "2026-09-08", "early": 1, "week": 3},  # 7 days after the section's first
        {"date": "2026-09-09", "early": 1, "week": 3, "late": 2},  # 8 days after
        {"date": "2026-09-10", "early": 1, "week": 3, "late": 2},
    ]
    seed(tmp_path, "a.jsonl", rows)
    shown = [Shown("early", "Early"), Shown("week", "Week"), Shown("late", "Late")]

    page = trends.page(built(tmp_path, Section("s", None, "a.jsonl", shown, "Label")))

    figures = re.findall(r'<figure class="chart[^"]*" data-section="0" data-field="(\d+)">(.*?)</figure>', page, re.S)
    notes = {field: re.findall(r'<p class="note">([^<]*)</p>', body) for field, body in figures}
    assert notes == {"0": [], "1": [], "2": ["Counted from 2026-09-09"]}


def test_a_stack_that_starts_late_says_so_too(tmp_path: Path) -> None:
    rows = [{"date": "2026-09-01", "n": 1}] + [{"date": f"2026-09-{day}", "n": 1, "by": {"x": 1}} for day in (10, 11)]
    seed(tmp_path, "a.jsonl", rows)
    shown = [Shown("n", "N"), Shown("by", "By", "breakdown")]

    page = trends.page(built(tmp_path, Section("s", None, "a.jsonl", shown, "Label")))

    assert re.findall(r'<p class="note">([^<]*)</p>', page) == ["Counted from 2026-09-10"]


def test_without_projects_the_page_has_no_nav_and_no_groups(tmp_path: Path) -> None:
    page = trends.page(sample(tmp_path))

    assert "<nav" not in page
    assert 'class="project"' not in page


def test_projects_group_the_sections_under_their_names_with_a_nav_between_them_the_rest_as_other(
    tmp_path: Path,
) -> None:
    page = trends.page(sample(tmp_path, [{"name": "P & Q", "sections": ["c"]}]))

    links = '<a href="#project-0">P &amp; Q</a><a href="#project-1">Other</a>'
    nav = f'<nav class="projects" aria-label="Projects">{links}</nav>'
    assert nav in page
    groups = re.findall(r'<div class="project" id="(project-\d)">\n<h2>([^<]*)</h2>\n<section>\n<h3>(.*?)</h3>', page)
    assert groups == [
        ("project-0", "P &amp; Q", '<span class="head">Sea</span>'),
        (
            "project-1",
            "Other",
            '<span class="head">Aye · x</span><span class="val">Version: 1.0</span>',
        ),
    ]
    charts = re.findall(r'<figure class="chart[^"]*" data-section="(\d+)"', page)
    assert charts == ["0", "1", "1", "1"]  # c's, then a's: each the index of its section in the data


def test_there_is_no_other_when_every_section_is_in_a_project(tmp_path: Path) -> None:
    page = trends.page(sample(tmp_path, [{"name": "P", "sections": ["c", "a: x"]}]))

    assert "Other" not in page
    assert page.count('<div class="project"') == 1


def test_a_section_with_no_field_is_left_off_the_page_and_kept_in_the_data(tmp_path: Path) -> None:
    """Such a section's content is report lines (a latest release), which the page can't show: an
    empty panel would say nothing, so the page leaves it out, and a project with nothing else on the page with it."""
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 5}])
    seed(tmp_path, "r.jsonl", [{"date": "2026-09-24", "releases": []}])
    sections = [Section("a", "x", "a.jsonl", [Shown("n", "N")], "Aye"), Section("r", "x", "r.jsonl", [], "Arr")]
    data = built(tmp_path, *sections, projects=[{"name": "Releases", "sections": ["r: x"]}])

    page = EMBEDDED.sub("", trends.page(data))  # the page's own markup, without its copy of the data

    assert [section["title"] for section in data["sections"]] == ["r: x", "a: x"]
    assert "Arr" not in page
    assert "Releases" not in page
    assert "<nav" not in page  # no section on the page is in a project
    assert re.findall(r'<figure class="chart[^"]*" data-section="(\d+)"', page) == ["1"]  # its index in the data


def test_the_page_escapes_what_the_data_holds(tmp_path: Path) -> None:
    evil = "</script><b>&amp;</b>"
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 1, "v": evil}])
    data = built(tmp_path, Section(evil, evil, "a.jsonl", [Shown("n", evil), Shown("v", evil, "value")], evil))

    page = trends.page(data)

    assert "<b>" not in page
    assert page.count("</script>") == 2  # the data's and the viewer's, each closed once
    (embedded,) = EMBEDDED.findall(page)
    assert json.loads(embedded) == data


def test_the_page_names_each_health_finding(tmp_path: Path) -> None:
    failed = [{"file": "a.jsonl", "last": "2026-09-24", "status": "fetch_failed", "streak": 3, "since": "2026-09-22"}]
    critical = [{"source": "a", "file": "a.jsonl", "reason": "no good row"}]
    checks = [{"source": "c", "level": "warn", "message": "odd"}]
    stale = [{"file": "b.jsonl", "last": None, "age_days": None}]
    seed(tmp_path, HEALTH, [health_row(failed=failed, stale=stale, critical=critical, checks=checks)])

    page = trends.page(built(tmp_path))

    for text in (
        "Failed: a.jsonl, fetch_failed since 2026-09-22 (3 nights)",
        "Stale: b.jsonl",
        "Critical: a: no good row",
        "Check c (warn): odd",
    ):
        assert f'<span class="pill warn">{text}</span>' in page
    assert "All sources current" not in page


# ---- Writing -------------------------------------------------------------------------------------


def test_a_run_writes_trends_json_and_the_page_from_the_same_data(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 5}])
    sections = [Section("a", None, "a.jsonl", [Shown("n", "N")], "Label")]
    lines: list[str] = []

    with port.captured_log(lines):
        trends.write(ctx, sections, HEALTH)

    data = json.loads((tmp_path / "trends.json").read_text(encoding="utf-8"))
    assert data == trends.build(tmp_path, ctx.date, sections, HEALTH)
    (embedded,) = EMBEDDED.findall((tmp_path / "trends.html").read_text(encoding="utf-8"))
    assert json.loads(embedded) == data
    assert ctx.critical == []
    assert "Wrote trends.json, trends.html" in "\n".join(lines)


# Three projects, P, Q and R, and a section in none, d; two pages, the first listing its projects out of their order.
PAGED = [{"name": "P", "sections": ["c"]}, {"name": "Q", "sections": ["a: x"]}, {"name": "R", "sections": ["e"]}]
PAGES = [
    {"file": "two.html", "title": "Two", "projects": ["Q", "P"]},
    {"file": "one.html", "title": "One", "projects": ["P"]},
]


def paged(tmp_path: Path) -> list[Section]:
    return [
        *sample_sections(tmp_path),
        Section("d", None, "d.jsonl", [Shown("n", "D")], "Dee"),
        Section("e", None, "e.jsonl", [Shown("n", "E")], "Eee"),
    ]


def embedded(path: Path) -> Any:
    (text,) = EMBEDDED.findall(path.read_text(encoding="utf-8"))
    return json.loads(text)


def test_with_pages_a_run_writes_trends_json_and_each_page_and_no_trends_html(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    lines: list[str] = []

    with port.captured_log(lines):
        trends.write(ctx, paged(tmp_path), HEALTH, PAGED, PAGES)

    data = json.loads((tmp_path / "trends.json").read_text(encoding="utf-8"))
    assert [s["title"] for s in data["sections"]] == ["c", "a: x", "e", "d"]  # everything, each page's or not
    assert not (tmp_path / "trends.html").exists()
    assert ctx.critical == []
    assert "Wrote trends.json, two.html, one.html" in "\n".join(lines)


def test_each_page_embeds_only_its_projects_sections_in_its_order_and_shows_its_title(tmp_path: Path) -> None:
    ctx = context(tmp_path)

    trends.write(ctx, paged(tmp_path), HEALTH, PAGED, PAGES)

    data = json.loads((tmp_path / "trends.json").read_text(encoding="utf-8"))
    two, one = embedded(tmp_path / "two.html"), embedded(tmp_path / "one.html")
    assert two == {**data, "sections": [data["sections"][1], data["sections"][0]]}
    assert one == {**data, "sections": [data["sections"][0]]}
    page = (tmp_path / "two.html").read_text(encoding="utf-8")
    assert '<a href="#project-0">Q</a><a href="#project-1">P</a></nav>' in page
    assert re.findall(r'<figure class="chart[^"]*" data-section="(\d+)"', page) == ["0", "0", "0", "1"]  # in its data
    markup = EMBEDDED.sub("", page)
    assert "<h1>Two</h1>" in markup
    assert [text for text in ("Other", "Dee", "Eee", "One") if text in markup] == []  # no other page's, nor none's


def test_a_pages_health_keeps_only_what_concerns_it_and_every_crash_and_report_or_trends_failure(
    tmp_path: Path,
) -> None:
    """One.html shows P, whose section c reads c.jsonl; a.jsonl is Q's, on two.html only."""
    mine, theirs = {"file": "c.jsonl", "last": None}, {"file": "a.jsonl", "last": None}
    failed = [{**mine, "status": "fetch_failed", "since": "2026-09-23"}, {**theirs, "status": "fetch_failed"}]
    critical = [
        {"source": "c", "file": "c.jsonl", "reason": "mine"},
        {"source": "a", "file": "a.jsonl", "reason": "theirs"},
        {"source": "a", "file": None, "reason": "crashed"},
        {"source": "reports", "file": "report.md", "reason": "block"},
        {"source": "trends", "file": "trends.json", "reason": "write"},
    ]
    checks = [{"source": "c", "level": "warn", "message": "mine"}, {"source": "a", "level": "error", "message": "x"}]
    ctx = context(tmp_path)
    sections = paged(tmp_path)
    seed(tmp_path, HEALTH, [health_row(stale=[mine, theirs], failed=failed, critical=critical, checks=checks)])

    trends.write(ctx, sections, HEALTH, PAGED, PAGES)

    whole = json.loads((tmp_path / "trends.json").read_text(encoding="utf-8"))["health"]
    assert (len(whole["stale"]), len(whole["failed"]), len(whole["critical"]), len(whole["checks"])) == (2, 2, 5, 2)
    assert embedded(tmp_path / "one.html")["health"] == {
        "date": "2026-09-24",
        "stale": [mine],
        "failed": failed[:1],
        "critical": [critical[0], *critical[2:]],
        "checks": checks[:1],
    }


def test_a_page_whose_files_are_all_current_says_so(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    sections = paged(tmp_path)
    seed(tmp_path, HEALTH, [health_row(stale=[{"file": "a.jsonl", "last": None}])])

    trends.write(ctx, sections, HEALTH, PAGED, PAGES)

    assert '<span class="pill ok">All sources current</span>' in (tmp_path / "one.html").read_text(encoding="utf-8")
    assert "Stale: a.jsonl" in (tmp_path / "two.html").read_text(encoding="utf-8")


def test_each_file_is_written_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[str] = []

    def write_text(path: Path, text: str) -> None:
        written.append(path.name)

    monkeypatch.setattr(store, "write_text", write_text)

    trends.write(context(tmp_path), [], HEALTH)
    trends.write(context(tmp_path), paged(tmp_path), HEALTH, PAGED, PAGES)

    assert written == ["trends.json", "trends.html", "trends.json", "two.html", "one.html"]


def test_a_file_that_cannot_be_written_is_critical_and_the_other_is_still_written(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    (tmp_path / "trends.json").mkdir()  # a folder where the file goes: its write fails

    trends.write(ctx, [], HEALTH)

    assert [(c.source, c.file) for c in ctx.critical] == [("trends", "trends.json")]
    assert ctx.critical[0].reason.startswith("can't write trends.json: ")
    assert (tmp_path / "trends.html").is_file()


def test_a_page_that_cannot_be_written_is_critical_and_the_others_are_still_written(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    (tmp_path / "two.html").mkdir()

    trends.write(ctx, paged(tmp_path), HEALTH, PAGED, PAGES)

    assert [(c.source, c.file) for c in ctx.critical] == [("trends", "two.html")]
    assert ((tmp_path / "trends.json").is_file(), (tmp_path / "one.html").is_file()) == (True, True)


@pytest.mark.parametrize(
    ("shown", "bug"),
    [
        (Shown("n", "N", "total"), "n: kind must be one of snapshot, change, daily, value, breakdown, not 'total'"),
        (Shown("n", "N", "daily", complement=True), "n: complement: only snapshot, change and value take it"),
    ],
    ids=["an unknown kind", "a complement of a daily count"],
)
def test_a_bug_building_the_data_is_critical_and_never_raises(tmp_path: Path, shown: Shown, bug: str) -> None:
    ctx = context(tmp_path)
    lines: list[str] = []

    with port.captured_log(lines):
        trends.write(ctx, [Section("a", None, "a.jsonl", [shown], "Label")], HEALTH)

    assert ctx.critical == [Critical("trends", None, f"crashed: ValueError: {bug}")]
    assert "ERROR: trends crashed: ValueError" in "\n".join(lines)
    assert not (tmp_path / "trends.json").exists()


def lined_section(file: str, title: str, name: str) -> Section:
    shown = [Shown("releases[].assets[].downloads", "Downloads", "breakdown", by="tag", lines=True, bands="release")]
    return Section(name, title, file, shown, "Releases")


def test_a_section_whose_charts_have_no_rows_at_any_date_is_left_off_the_page_and_kept_in_the_data(
    tmp_path: Path,
) -> None:
    """Releases with no downloadable files count 0 on every date, so their lines are never drawn: the section
    is left out, and so is a project with nothing else on the page."""
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 5}])
    seed(tmp_path, "r.jsonl", [day(23, [release("1.0.0")]), day(24, [release("1.0.0", 0, 0)])])
    sections = [Section("a", "x", "a.jsonl", [Shown("n", "N")], "Aye"), lined_section("r.jsonl", "x", "r")]
    data = built(tmp_path, *sections, projects=[{"name": "Gone", "sections": ["r: x"]}])

    page = EMBEDDED.sub("", trends.page(data))

    assert [section["title"] for section in data["sections"]] == ["r: x", "a: x"]
    assert page.count("<section>") == 1
    assert "Gone" not in page
    assert "<nav" not in page  # no section on the page is in a project


def test_a_section_with_a_chart_that_has_rows_stays_even_when_another_of_its_charts_has_none(tmp_path: Path) -> None:
    rows = [{**day(23, [release("1.0.0", 0)]), "n": 1}, {**day(24, [release("1.0.0", 0)]), "n": 3}]
    seed(tmp_path, "r.jsonl", rows)
    shown = [
        Shown("releases[].assets[].downloads", "None", "breakdown", by="tag", lines=True, bands="release"),
        Shown("n", "N"),
    ]
    data = built(tmp_path, Section("r", "x", "r.jsonl", shown, "Releases"))

    assert EMBEDDED.sub("", trends.page(data)).count("<section>") == 1


def test_a_section_with_no_good_rows_yet_stays_to_say_so(tmp_path: Path) -> None:
    seed(tmp_path, "a.jsonl", [])
    data = built(tmp_path, Section("a", "x", "a.jsonl", [Shown("n", "N")], "Aye"))

    assert "No good rows yet." in trends.page(data)


def test_a_section_with_only_zero_counts_in_bars_stays_since_they_are_drawn(tmp_path: Path) -> None:
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "d": 0}])
    data = built(tmp_path, Section("a", "x", "a.jsonl", [Shown("d", "D", "daily")], "Aye"))

    assert EMBEDDED.sub("", trends.page(data)).count("<section>") == 1


# ---- derived fields, bands, tips and notes ------------------------------------------------------


def test_a_minus_field_holds_the_value_at_its_field_less_the_value_at_its_minus_per_row(tmp_path: Path) -> None:
    rows: list[dict[str, Any]] = [
        {"date": "2026-09-20", "n": 10, "r": 3},
        {"date": "2026-09-21", "n": 10},
        {"date": "2026-09-22", "n": None, "r": 3},
        {"date": "2026-09-23", "n": "x", "r": 3},
        {"date": "2026-09-24", "n": 12.5, "r": True},
        {"date": "2026-09-25", "n": 1.5, "r": 1},
    ]
    seed(tmp_path, "a.jsonl", rows)

    (section,) = built(tmp_path, Section("a", None, "a.jsonl", [Shown("n", "Out", minus="r")], "L"))["sections"]

    assert section["fields"] == [
        {"field": "n", "label": "Out", "kind": "snapshot", "minus": "r", "values": [7, None, None, None, None, 0.5]}
    ]


def test_the_default_summary_leaves_a_field_with_minus_out() -> None:
    plain = Section("s", None, "a.jsonl", [Shown("n", "N")], "L")
    derived = plain._replace(shown=[*plain.shown, Shown("n", "Out", minus="r", chart="C")])

    assert section_lines(derived) == section_lines(plain)


def test_a_band_tip_and_format_are_in_the_data(tmp_path: Path) -> None:
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 9, "r": 2, "p": 22.2}])
    shown = [
        Shown("n", "N", chart="C", tip=("r", "p")),
        Shown("n", "Out", chart="C", minus="r", band="n"),
        Shown("r", "Rank", charted=False),
        Shown("p", "Pct", charted=False, format=".1f", complement=True),
    ]

    (section,) = built(tmp_path, Section("a", None, "a.jsonl", shown, "L"))["sections"]

    first, second, _, pct = section["fields"]
    assert (first["tip"], second["band"], pct["format"], pct["values"]) == (["r", "p"], "n", ".1f", [77.8])
    assert "band" not in first
    assert "tip" not in second


def test_a_section_carries_its_notes_only_when_it_has_some(tmp_path: Path) -> None:
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 1}])
    plain = Section("a", None, "a.jsonl", [Shown("n", "N")], "L")
    noted = plain._replace(notes=(Note("2026-09-22", "Moved"),))

    without, with_notes = (built(tmp_path, s)["sections"][0] for s in (plain, noted))

    assert "notes" not in without
    assert with_notes["notes"] == [{"date": "2026-09-22", "text": "Moved"}]


@pytest.mark.parametrize(
    ("shown", "bug"),
    [
        ([Shown("n", "N", minus="")], None),
        ([Shown("n", "N", minus=5)], "n: minus: must be a non-empty string"),  # type: ignore[arg-type]
        ([Shown("n", "N", "daily", minus="r")], "n: minus: only a snapshot, change or value takes it"),
        ([Shown("n", "N", minus="r", complement=True)], "n: minus: not with complement"),
        ([Shown("n", "N", into="b", minus="r"), Shown("b", "B", "breakdown")], "n: minus: not with into"),
        (
            [Shown("a[].n", "N", "breakdown", by="k", minus="r")],
            "a[].n: minus: only a snapshot, change or value takes it",
        ),
        ([Shown("n", "N", band="m")], "n: band: needs chart"),
        ([Shown("n", "N", chart="C", band="m")], "n: band: names no field of the chart C"),
        ([Shown("n", "N", chart="C"), Shown("m", "M", chart="D", band="n")], "m: band: names no field of the chart D"),
        ([Shown("n", "N", chart="C", band="n")], "n: band: names its own field"),
        ([Shown("n", "N", chart="C", tip=("x",))], "n: tip: x names no field of the section"),
        ([Shown("n", "N", chart="C", tip="r")], "n: tip: must be a tuple of field paths"),  # type: ignore[arg-type]
        ([Shown("n", "N", chart="C", tip=("",))], "n: tip: must be a tuple of field paths"),
        ([Shown("n", "N", "daily", tip=("n",))], "n: tip: only a snapshot or change takes it"),
    ],
    ids=[
        "an empty minus is none",
        "minus not a string",
        "minus of a daily count",
        "minus with complement",
        "minus into a stack",
        "minus on a breakdown",
        "band without chart",
        "band names nothing",
        "band in another chart",
        "band names itself",
        "tip names nothing",
        "tip a string",
        "tip an empty path",
        "tip on a daily count",
    ],
)
def test_a_band_minus_or_tip_that_cannot_be_drawn_so_is_a_bug(
    tmp_path: Path, shown: list[Shown], bug: str | None
) -> None:
    ctx = context(tmp_path)

    trends.write(ctx, [Section("a", None, "a.jsonl", shown, "Label")], HEALTH)

    assert ctx.critical == ([] if bug is None else [Critical("trends", None, f"crashed: ValueError: {bug}")])


def test_two_fields_of_one_path_coexist_and_a_band_names_the_one_without_minus(tmp_path: Path) -> None:
    seed(tmp_path, "a.jsonl", [{"date": "2026-09-24", "n": 9, "r": 2}])
    shown = [Shown("n", "N", chart="C"), Shown("n", "Out", chart="C", minus="r", band="n"), Shown("r", "R")]

    (section,) = built(tmp_path, Section("a", None, "a.jsonl", shown, "L"))["sections"]

    assert [f["values"] for f in section["fields"]] == [[9], [7], [2]]
