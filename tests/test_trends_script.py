"""The trends page's script, run in Node on made-up data with a stub of the browser's document.

The page's drawing is JavaScript, which Python can't run, so these tests run the page's own script once per chart
and read the SVG it draws. They skip where `node` isn't installed; every value here is made up.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

import pytest
from test_trends import built, seed

from leadline import trends
from leadline.config import Note, Section, Shown

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the page's script")

DRIVER = r"""
const html = require("fs").readFileSync(0, "utf8");
const data = html.match(/<script type="application\/json" id="trends-data">(.*?)<\/script>/s)[1];
const script = html.match(/<\/script>\n<script>(.*?)<\/script>/s)[1];
const mode = process.argv[3] || "";
let svg = "";
const stub = () => ({ addEventListener() {}, setAttribute() {}, textContent: "", dataset: {}, style: {},
  replaceChildren() {}, appendChild() {} });
const now = stub();
const hit = { ...stub(), on: {}, addEventListener(name, fn) { this.on[name] = fn; } };
const tip = { ...stub(), texts: [], offsetWidth: 100, offsetHeight: 20,
  replaceChildren() { this.texts = []; }, appendChild(line) { this.texts.push(line.textContent); } };
const box = { ...stub(), getBoundingClientRect: () => ({ left: 0, width: +process.argv[2] || 700 }) };
const parts = { ".hit": hit, ".tip": tip, svg: box };
const plot = { clientWidth: +process.argv[2] || 700, set innerHTML(v) { svg = v; },
  getBoundingClientRect: () => ({ left: 0, top: 100 }), querySelector: (q) => parts[q] || stub() };
const figure = { dataset: { section: "0", field: process.argv[1] },
  querySelector: (q) => (q === ".plot" ? plot : q === ".now" ? now : stub()) };
global.window = { innerWidth: 1000, addEventListener() {} };
global.document = {
  getElementById: () => ({ textContent: data }),
  querySelector: () => ({ dataset: { days: "0" } }),
  querySelectorAll: (q) => (q === "figure.chart" ? [figure] : []),
  createElement: stub,
};
eval(script);
if (mode === "now") { console.log(now.textContent); }
else if (mode.startsWith("hover:")) {
  hit.on.pointermove({ clientX: +mode.slice(6) });
  console.log(tip.texts.join("\n"));
}
else { console.log(svg); }
"""


def run(
    tmp_path: Path,
    rows: Iterable[Mapping[str, Any]],
    shown: Shown | list[Shown],
    width: int,
    mode: str,
    notes: tuple[Note, ...] = (),
) -> str:
    seed(tmp_path, "a.jsonl", rows)
    section = Section("s", None, "a.jsonl", shown if isinstance(shown, list) else [shown], "Label", notes=notes)
    data = built(tmp_path, section)
    # Bytes both ways: text mode would translate newlines and use the locale's code page on Windows.
    page = trends.page(data).encode("utf-8")
    done = subprocess.run(["node", "-e", DRIVER, "0", str(width), mode], input=page, capture_output=True, check=True)
    return done.stdout.decode("utf-8").rstrip("\n")


def draw(tmp_path: Path, rows: Iterable[Mapping[str, Any]], shown: Shown | list[Shown], width: int = 700) -> str:
    """The SVG the page draws for the first field of `shown` (one, or the several that share a chart) of a file of
    `rows`."""
    return run(tmp_path, rows, shown, width, "svg")


def caption(tmp_path: Path, rows: Iterable[Mapping[str, Any]], shown: Shown) -> str:
    """The figure's caption figure, its `.now` text."""
    return run(tmp_path, rows, shown, 700, "now")


def hover(tmp_path: Path, rows: Iterable[Mapping[str, Any]], shown: Shown, x: int) -> list[str]:
    """The tooltip's lines with the pointer `x` pixels along the chart: the row nearest it."""
    return run(tmp_path, rows, shown, 700, f"hover:{x}").split("\n")


def days(start: str, offsets: Iterable[int], field: str, values: Iterable[Any]) -> list[dict[str, Any]]:
    first = date.fromisoformat(start)
    return [{"date": (first + timedelta(days=n)).isoformat(), field: v} for n, v in zip(offsets, values)]


def axis(svg: str) -> list[str]:
    """The axis labels, bottom first: the texts anchored at the end, which are the y-axis's."""
    return re.findall(r'<text x="38"[^>]*>([^<]*)</text>', svg)


def lines(svg: str) -> dict[str, int]:
    """How many solid lines, dashed joins and fills the chart draws."""
    return {
        "solid": len(re.findall(r'<polyline [^>]*stroke-width="1.8"', svg)),
        "dashed": len(re.findall(r'<polyline [^>]*stroke-dasharray="4 3"', svg)),
        "fills": len(re.findall(r'<polygon [^>]*fill="var\(--accent-soft\)"', svg)),
    }


def test_an_axis_fits_the_readings_of_a_line(tmp_path: Path) -> None:
    rows = days("2026-01-01", range(5), "n", [24, 40, 55, 70, 73])

    assert axis(draw(tmp_path, rows, Shown("n", "N"))) == ["15", "30", "45", "60", "75"]


def test_a_line_that_starts_at_zero_keeps_it_and_a_stack_or_bars_always_start_there(tmp_path: Path) -> None:
    rows = days("2026-01-01", range(4), "n", [0, 20, 40, 67])
    assert axis(draw(tmp_path, rows, Shown("n", "N"))) == ["0", "20", "40", "60", "80"]

    bars = days("2026-01-01", range(4), "n", [50, 52, 51, 53])
    assert axis(draw(tmp_path / "bars", bars, Shown("n", "N", "daily")))[0] == "0"

    stack = [{"date": "2026-01-01", "by": {"a": 50, "b": 3}}]
    assert axis(draw(tmp_path / "stack", stack, Shown("by", "By", "breakdown")))[0] == "0"


def test_a_breakdown_of_lines_draws_each_band_as_a_line_of_its_own_on_one_axis_not_stacked(tmp_path: Path) -> None:
    rows = [
        {"date": f"2026-01-0{n}", "l": [{"k": "v1.0.0", "n": a}, {"k": "v1.1.0", "n": b}]}
        for n, (a, b) in enumerate([(100, 0), (100, 10), (100, 30)], start=1)
    ]
    shown = Shown("l[].n", "Downloads", "breakdown", by="k", lines=True, bands="release")

    svg = draw(tmp_path, rows, shown)

    assert len(re.findall(r'<polyline [^>]*stroke-width="1.8"', svg)) == 2
    assert float(axis(svg)[-1]) < 130  # a stack would reach the total, 130
    assert re.findall(r'<span class="key">.*?</i>([^<]*)</span>', svg) == ["1.0.0 100", "1.1.0 30"]


def test_each_line_of_a_breakdown_has_its_own_translucent_area_from_the_baseline_the_newest_in_front(
    tmp_path: Path,
) -> None:
    rows = [
        {"date": f"2026-01-0{n}", "l": [{"k": "v1.0.0", "n": a}, {"k": "v1.1.0", "n": b}]}
        for n, (a, b) in enumerate([(100, 10), (100, 20), (100, 30)], start=1)
    ]
    shown = Shown("l[].n", "Downloads", "breakdown", by="k", lines=True, bands="release")

    svg = draw(tmp_path, rows, shown)

    areas = re.findall(r'<polygon [^>]*fill="([^"]*)" fill-opacity="0.4"', svg)
    strokes = re.findall(r'<polyline [^>]*stroke="([^"]*)"[^>]*stroke-width="1.8"', svg)
    assert len(areas) == 2
    assert areas == strokes  # each area is its line's color
    assert svg.index("<polygon") < svg.index("<polyline")
    assert areas[-1].startswith("color-mix(in srgb, var(--accent) 100%")  # the newest release is drawn last, on top
    baseline = re.findall(r'<line x1="44" x2="\d+" y1="([\d.]+)" y2="[\d.]+" stroke="var\(--grid\)"/>', svg)[0]
    assert all(points.split()[0].endswith("," + baseline) for points in re.findall(r'<polygon points="([^"]*)"', svg))


def test_the_lines_of_a_chart_of_fields_have_no_area(tmp_path: Path) -> None:
    rows = days("2026-01-01", range(3), "a", [1, 2, 3])
    for row in rows:
        row["b"] = 5

    svg = draw(tmp_path, rows, [Shown("a", "A", chart="C"), Shown("b", "B", chart="C")])

    assert "<polygon" not in svg


def test_a_chart_since_counts_days_not_dates_on_its_axis_and_hover(tmp_path: Path) -> None:
    rows = [
        {"date": f"2026-01-0{n}", "l": [{"k": "v1.0.0", "p": "2026-01-02T08:00:00Z", "n": 10 * n}]} for n in range(1, 8)
    ]
    shown = Shown("l[].n", "Since", "breakdown", by="k", lines=True, since="p", bands="release")

    svg = draw(tmp_path, rows, shown)

    labels = re.findall(r'y="164" text-anchor="[a-z]+">([^<]*)</text>', svg)  # under the plot
    assert labels[0] == "day 0"
    assert all(label.startswith("day ") for label in labels)
    assert len(re.findall(r'<polyline [^>]*stroke-width="1.8"', svg)) == 1
    assert re.findall(r'<span class="key">.*?</i>([^<]*)</span>', svg) == ["1.0.0 70"]


def label_ends(svg: str, width: int) -> list[float]:
    """Where each text at the right of a stack ends, at 6.8px a character (the page's monospace at 11px)."""
    texts = re.findall(r'<text x="([\d.]+)" y="[\d.]+"(?: style="[^"]*")?>([^<]*)</text>', svg)
    return [float(x) + 6.8 * len(text) for x, text in texts if float(x) > 100]


@pytest.mark.parametrize("width", [1000, 480, 340])
def test_the_labels_at_the_right_of_a_stack_end_inside_the_chart_at_any_width(tmp_path: Path, width: int) -> None:
    rows = [
        {
            "date": f"2026-01-0{n}",
            "by": {"awaiting_verification": 21 + n, "confirmed": 29, "unsupported_in_this_run": 15},
        }
        for n in (1, 2, 3)
    ]

    svg = draw(tmp_path, rows, Shown("by", "By", "breakdown"), width)

    ends = label_ends(svg, width)
    assert len(ends) == 3
    assert max(ends) <= width
    assert "awaiting verification" in svg or "\u2026" in svg  # a name that does not fit is cut, never its value
    assert re.findall(r">[^<]* (?:22|23|24)</text>", svg)  # the value of the thickest band is kept


TOOLTIP = r"""
const html = require("fs").readFileSync(0, "utf8");
const data = html.match(/<script type="application\/json" id="trends-data">(.*?)<\/script>/s)[1];
const script = html.match(/<\/script>\n<script>(.*?)<\/script>/s)[1];
const [plotLeft, plotTop, clientX, tipWidth, tipHeight, viewport, height] = process.argv.slice(1).map(Number);
const handlers = {};
const mk = (extra) => Object.assign({ addEventListener(name, f) { handlers[name] = f; }, setAttribute() {},
  textContent: "", dataset: {}, style: {}, replaceChildren() {}, appendChild() {}, hidden: true }, extra);
const tip = mk({ offsetWidth: tipWidth, offsetHeight: tipHeight });
const rect = { left: plotLeft, top: plotTop, width: 700 };
const box = mk({ getBoundingClientRect: () => rect });
const parts = { svg: box, ".cross": mk({}), ".tip": tip, ".hit": mk({}) };
const plot = { clientWidth: 700, set innerHTML(v) {}, getBoundingClientRect: () => rect,
  querySelector: (q) => parts[q === "svg" ? "svg" : q] || mk({}) };
const figure = { dataset: { section: "0", field: "0" }, querySelector: (q) => (q === ".plot" ? plot : mk({})) };
global.window = { innerWidth: viewport, innerHeight: height, addEventListener() {} };
global.document = {
  getElementById: () => ({ textContent: data }),
  querySelector: () => ({ dataset: { days: "0" } }),
  querySelectorAll: (q) => (q === "figure.chart" ? [figure] : []),
  createElement: () => mk({}),
};
eval(script);
handlers.pointermove({ clientX });
console.log(JSON.stringify({ left: parseFloat(tip.style.left), top: parseFloat(tip.style.top) }));
"""


def tooltip(tmp_path: Path, plot_left: int, plot_top: int, x: int, size: tuple[int, int]) -> tuple[float, float]:
    """Where the tooltip goes (its left and top in the viewport) when the pointer is at `x`, with the plot at the given
    place of a 1000 x 800 viewport and a tooltip of `size`."""
    rows = [{"date": f"2026-01-0{n}", "by": {"a": n, "b": 2}} for n in (1, 2, 3)]
    seed(tmp_path, "a.jsonl", rows)
    page = trends.page(built(tmp_path, Section("s", None, "a.jsonl", [Shown("by", "By", "breakdown")], "L"))).encode()
    args = [str(n) for n in (plot_left, plot_top, x, *size, 1000, 800)]
    done = subprocess.run(["node", "-e", TOOLTIP, *args], input=page, capture_output=True, check=True)
    placed = json.loads(done.stdout)
    return plot_left + placed["left"], plot_top + placed["top"]


def test_a_tooltip_goes_above_the_chart_when_there_is_room(tmp_path: Path) -> None:
    _, top = tooltip(tmp_path, 40, 400, 300, (200, 100))

    assert top + 100 <= 400  # its bottom is above the plot


def test_a_tooltip_that_would_leave_the_top_of_the_screen_goes_down_into_the_chart(tmp_path: Path) -> None:
    _, top = tooltip(tmp_path, 40, 100, 300, (200, 220))

    assert top >= 0


def test_a_tooltip_never_leaves_the_screen_at_the_right_or_the_left(tmp_path: Path) -> None:
    right, _ = tooltip(tmp_path, 300, 400, 690, (250, 100))
    left, _ = tooltip(tmp_path, 10, 400, 0, (250, 100))

    assert right + 250 <= 1000
    assert left >= 0


def test_a_stack_is_still_an_area(tmp_path: Path) -> None:
    rows = [{"date": f"2026-01-0{n}", "by": {"a": n, "b": 2}} for n in (1, 2, 3)]

    svg = draw(tmp_path, rows, Shown("by", "By", "breakdown"))

    assert "<polygon" in svg
    assert "key" not in svg


def test_a_line_axis_spans_at_least_5_percent_of_its_value_so_a_small_wobble_stays_small(tmp_path: Path) -> None:
    rows = days("2026-01-01", range(5), "n", [72.1, 73.6, 72.4, 73.0, 72.8])

    labels = [float(label) for label in axis(draw(tmp_path, rows, Shown("n", "N")))]

    assert labels[-1] - labels[0] >= 0.05 * 73.6
    assert labels[0] <= 72.1
    assert labels[-1] >= 73.6


def test_a_line_of_readings_that_are_never_negative_never_runs_below_zero(tmp_path: Path) -> None:
    rows = days("2026-01-01", range(4), "n", [0.2, 40, 290, 250])

    assert axis(draw(tmp_path, rows, Shown("n", "N")))[0] == "0"


def test_a_line_whose_readings_agree_runs_from_zero(tmp_path: Path) -> None:
    rows = days("2026-01-01", range(3), "n", [5, 5, 5])

    assert axis(draw(tmp_path, rows, Shown("n", "N")))[0] == "0"


def test_readings_on_consecutive_days_are_one_solid_line_over_a_fill(tmp_path: Path) -> None:
    rows = days("2026-01-01", range(4), "n", [1, 2, 3, 4])

    assert lines(draw(tmp_path, rows, Shown("n", "N"))) == {"solid": 1, "dashed": 0, "fills": 1}


def test_readings_more_than_a_day_apart_are_joined_by_a_dashed_line_with_the_same_fill(tmp_path: Path) -> None:
    # Rows on days 0 and 25 are next to each other in the file, with no row between, and still not consecutive days.
    rows = days("2026-01-01", [0, 25, 26, 27], "n", [10, 20, 22, 24])

    assert lines(draw(tmp_path, rows, Shown("n", "N"))) == {"solid": 1, "dashed": 1, "fills": 2}


def test_a_row_with_no_value_between_two_readings_makes_their_join_dashed(tmp_path: Path) -> None:
    rows = days("2026-01-01", range(4), "n", [1, 2, None, 4])

    assert lines(draw(tmp_path, rows, Shown("n", "N"))) == {"solid": 1, "dashed": 1, "fills": 2}


def test_each_base_versions_prereleases_are_a_band_of_their_own_above_the_release_lines(tmp_path: Path) -> None:
    row = {"1.0.0": 40, "1.1.0": 30, "1.1.0-beta.1": 6, "1.1.0-beta.2": 4, "2.0.0-beta.1": 10, "2.1.0-rc.1": 9}
    rows = [{"date": "2026-01-01", "v": row}]

    svg = draw(tmp_path, rows, Shown("v", "V", "breakdown"))

    names = re.findall(r'<text x="[\d.]+" y="[\d.]+"[^>]*>([^<]*)</text>', svg)
    assert [n for n in names if n.endswith((".x 30", ".x 40")) or "betas" in n] == [
        "1.0.x 40",
        "1.1.x 30",
        "1.1.0 betas 10",
        "2.0.0 betas 10",
        "2.1.0 betas 9",
    ]


def labels(svg: str) -> list[tuple[str, float]]:
    """Each band name the stack names at its right edge, and the y of its label."""
    return [(m[1], float(m[0])) for m in re.findall(r'<text x="[\d.]+" y="([\d.]+)"[^>]*>([^<]*)</text>', svg)]


def releases(count: int) -> dict[str, int]:
    return {f"3.{n}.0": 10 + 5 * n for n in range(count)}


def test_release_bands_are_each_exact_release_named_as_it_is_oldest_at_the_bottom(tmp_path: Path) -> None:
    rows = [{"date": "2026-01-01", "v": {"3.13.0": 40, "3.13.1": 30, "3.14.0": 50, "3.14.0-beta.1": 8}}]

    svg = draw(tmp_path, rows, Shown("v", "V", "breakdown", bands="release"))

    names = [name for name, _ in labels(svg)]
    assert [n for n in names if re.fullmatch(r"3\.\d+\.\d+ \d+", n)] == ["3.13.0 40", "3.13.1 30", "3.14.0 50"]
    assert "3.14.0 betas 8" in names
    assert ".x" not in svg


def test_release_bands_fade_from_the_accent_down_to_the_grid_colour_newest_in_the_accent(tmp_path: Path) -> None:
    rows = [{"date": "2026-01-01", "v": releases(5)}]

    svg = draw(tmp_path, rows, Shown("v", "V", "breakdown", bands="release"))

    mix = r'<polygon points="[^"]*" fill="color-mix\(in srgb, var\(--accent\) (\d+)%, var\(--grid\)\)"'
    fills = [int(p) for p in re.findall(mix, svg)]
    assert len(fills) == 5
    assert fills == sorted(fills)
    assert fills[-1] == 100


def test_many_release_bands_are_all_drawn_and_their_labels_never_overlap(tmp_path: Path) -> None:
    rows = days("2026-01-01", range(3), "v", [releases(20)] * 3)
    shown = Shown("v", "V", "breakdown", bands="release")

    svg = draw(tmp_path, rows, shown)

    assert svg.count('stroke="var(--panel)"') == 20  # a polygon for each release
    ys = sorted(y for name, y in labels(svg) if re.fullmatch(r"3\.\d+\.0 \d+", name))
    assert len(ys) >= 5
    assert all(b - a >= 10 for a, b in zip(ys, ys[1:]))  # a label only where its band is thick enough


def capped(tmp_path: Path, bands_from: int) -> str:
    """Six days of a total (`open`) that caps a stack (`by`), whose bands exist from the day `bands_from` on."""
    rows = [
        {
            "date": f"2026-01-0{n + 1}",
            "open": 4 + n % 2,
            **({"by": {"a": 1 + n % 2, "b": 3}} if n >= bands_from else {}),
        }
        for n in range(6)
    ]
    shown = [Shown("by", "By", "breakdown"), Shown("open", "Open", into="by")]
    return draw(tmp_path, rows, shown)


def drawn(svg: str, tag: str, where: str) -> list[int]:
    """The number of points of each `tag` the SVG draws whose attributes match `where`."""
    return [len(points.split()) for points in re.findall(rf'<{tag} points="([^"]*)"[^>]*{where}', svg)]


def test_the_total_that_caps_a_stack_is_a_line_along_its_top_edge(tmp_path: Path) -> None:
    svg = capped(tmp_path, bands_from=0)

    assert drawn(svg, "polyline", r'stroke="var\(--ink\)"') == [6]
    assert drawn(svg, "polygon", 'stroke="var\\(--panel\\)"') == [12, 12]  # a polygon a band, each up and back


def test_rows_with_a_total_but_no_bands_draw_the_total_line_alone_and_the_bands_start_where_they_exist(
    tmp_path: Path,
) -> None:
    svg = capped(tmp_path, bands_from=3)

    assert drawn(svg, "polyline", r'stroke="var\(--ink\)"') == [6]  # every day has the total
    assert drawn(svg, "polygon", 'stroke="var\\(--panel\\)"') == [6, 6]  # the bands: the last 3 days, up and back


def test_a_stack_whose_total_is_off_the_page_for_every_row_still_draws_its_bands(tmp_path: Path) -> None:
    rows = [{"date": f"2026-01-0{n}", "by": {"a": n, "b": 2}} for n in (1, 2, 3)]

    svg = draw(tmp_path, rows, Shown("by", "By", "breakdown"))

    assert drawn(svg, "polyline", r'stroke="var\(--ink\)"') == []  # no capping field, no top line


def test_a_daily_caption_has_the_newest_7_rows_total_and_the_newest_whole_weeks_monday_to_sunday(
    tmp_path: Path,
) -> None:
    # 2026-09-02 is a Wednesday: 09-07..09-13 and 09-14..09-20 are whole weeks, 09-21..09-23 is the current one.
    rows = days("2026-09-02", range(22), "n", [1] * 5 + [2] * 7 + [3] * 7 + [100] * 3)

    text = caption(tmp_path, rows, Shown("n", "N", "daily"))

    assert text == "312 in 7d \u00b7 21 last whole week"


def test_a_week_with_a_day_missing_is_not_a_whole_week_and_has_no_figure(tmp_path: Path) -> None:
    rows = days("2026-09-07", [0, 1, 2, 4, 5, 6, 7], "n", [1] * 7)  # 09-10 is missing, and the next week has 1 day

    assert caption(tmp_path, rows, Shown("n", "N", "daily")) == "7 in 7d"


def test_the_newest_whole_week_is_older_when_the_current_one_is_not_over(tmp_path: Path) -> None:
    rows = days("2026-09-07", range(10), "n", [1] * 7 + [50] * 3)  # a whole week, then 3 days of the next

    assert caption(tmp_path, rows, Shown("n", "N", "daily")) == "154 in 7d \u00b7 7 last whole week"


def trend(svg: str) -> list[list[tuple[float, float]]]:
    """The points of each trend line (faint ink), as (x, y)."""
    found = re.findall(r'<polyline points="([^"]*)" fill="none" stroke="var\(--ink\)" stroke-opacity="0.55"', svg)
    return [[(float(a), float(b)) for a, b in (p.split(",") for p in points.split())] for points in found]


def test_a_trend_is_a_faint_line_of_the_mean_of_the_7_days_to_each_day_drawn_from_the_fourth_day(
    tmp_path: Path,
) -> None:
    rows = days("2026-09-01", range(10), "n", [10] * 10)

    (line,) = trend(draw(tmp_path, rows, Shown("n", "N", "daily", trend=True)))

    assert len(line) == 7  # days 4 to 10: each has 4 or more of its 7 days
    assert len({y for _, y in line}) == 1  # a mean of 10s
    xs = [x for x, _ in line]
    assert xs == sorted(xs)


def test_a_field_without_a_trend_draws_no_line(tmp_path: Path) -> None:
    rows = days("2026-09-01", range(10), "n", [10] * 10)

    svg = draw(tmp_path, rows, Shown("n", "N", "daily"))

    assert trend(svg) == []
    assert svg.count("<rect") == 11  # ten bars and the pointer's rectangle


def test_a_trend_is_unbroken_through_a_short_gap_and_the_missing_day_still_has_no_bar(tmp_path: Path) -> None:
    rows = days("2026-09-01", [n for n in range(10) if n != 5], "n", [10] * 9)

    svg = draw(tmp_path, rows, Shown("n", "N", "daily", trend=True))

    (line,) = trend(svg)
    assert len(line) == 7  # still a point on the day with no row: 4 of its 7 days have counts
    assert svg.count('fill="var(--bar)"') == 9


def test_a_trend_breaks_only_where_fewer_than_4_of_the_7_days_have_counts(tmp_path: Path) -> None:
    rows = days("2026-09-01", [*range(6), *range(12, 18)], "n", [10] * 12)  # a gap of 6 days: 6 to 11

    first, second = trend(draw(tmp_path, rows, Shown("n", "N", "daily", trend=True)))

    assert (len(first), len(second)) == (6, 3)  # days 4 to 9 (day 9 has 4 of its 7), then days 16 to 18 (4 of 7)


def test_a_trend_is_the_mean_of_the_days_that_have_counts_not_the_week(tmp_path: Path) -> None:
    rows = days("2026-09-01", range(4), "n", [10, 10, 10, 30])

    tip = hover(tmp_path, rows, Shown("n", "N", "daily", trend=True), 10000)

    assert tip == ["2026-09-04", "30 that day \u00b7 7d avg 15"]


def test_a_tooltip_has_the_7d_total_and_average_once_a_week_is_there_and_no_average_before_4_days(
    tmp_path: Path,
) -> None:
    rows = days("2026-09-01", range(7), "n", [1, 2, 3, 4, 5, 6, 7])
    shown = Shown("n", "N", "daily", trend=True)

    assert hover(tmp_path, rows, shown, 10000) == ["2026-09-07", "7 that day \u00b7 28 in 7d \u00b7 7d avg 4"]
    assert hover(tmp_path, rows, shown, 0) == ["2026-09-01", "1 that day"]


def test_a_colour_is_a_theme_token_for_the_bars_and_the_line_and_the_default_is_unchanged(tmp_path: Path) -> None:
    rows = days("2026-09-01", range(4), "n", [1, 2, 3, 4])

    plain = draw(tmp_path, rows, Shown("n", "N", "daily"))
    green = draw(tmp_path / "green", rows, Shown("n", "N", "daily", color="s2"))
    line = draw(tmp_path / "line", rows, Shown("n", "N", color="s4"))

    assert 'fill="var(--bar)"' in plain
    assert "--s2" not in plain
    assert 'fill="var(--s2)"' in green
    assert "--bar" not in green
    assert 'stroke="var(--s4)"' in line
    assert 'fill="var(--s4)" fill-opacity="0.14"' in line


# ---- derived lines, bands, tips and notes -------------------------------------------------------

FEED = [
    Shown("n", "Tracked", chart="C", color="s3", tip=("r", "p")),
    Shown("n", "Outranked", chart="C", color="s1", minus="r", band="n"),
    Shown("r", "Rank", charted=False),
    Shown("p", "Percentile", charted=False, format=".1f", complement=True),
]


def feed(start: str = "2026-09-19", count: int = 6) -> list[dict[str, Any]]:
    """A feed that jumps by 1500 on its 4th night, our rank with it: the gap hardly changes."""
    rows = []
    for k in range(count):
        tracked = 1000 + (1500 if k >= 3 else 0)
        rank = 222 + (330 if k >= 3 else 0)
        row = days(start, [k], "n", [tracked])[0]
        rows.append({**row, "r": rank, "p": round(100 * rank / tracked, 1)})
    return rows


def found(pattern: str, text: str, group: int = 0) -> str:
    """The text a pattern matches in `text` (its `group`); the test fails if it matches none."""
    match = re.search(pattern, text)
    assert match is not None, pattern
    return match.group(group)


def polygons(svg: str) -> list[str]:
    return re.findall(r'<polygon [^>]*fill-opacity="0.18"[^>]*>', svg)


def test_a_derived_line_is_drawn_beside_the_line_it_is_made_of_in_the_colors_the_fields_name(tmp_path: Path) -> None:
    svg = draw(tmp_path, feed(), FEED)

    assert re.findall(r'<polyline [^>]*stroke="(var\(--s\d\))" stroke-width="1.8"', svg) == ["var(--s3)", "var(--s1)"]
    assert "Outranked 1,948" in svg
    assert "Tracked 2,500" in svg


def test_the_area_between_two_lines_is_a_translucent_polygon_of_the_first_fields_color_behind_the_lines(
    tmp_path: Path,
) -> None:
    svg = draw(tmp_path, feed(), FEED)

    (area,) = polygons(svg)
    assert 'fill="var(--s3)"' in area
    assert svg.index(area) < svg.index("<polyline")
    assert len(found(r'points="([^"]*)"', area, 1).split()) == 12  # 6 nights along each line


def test_a_band_is_drawn_only_between_dates_where_both_lines_have_a_number(tmp_path: Path) -> None:
    rows = feed()
    rows[2]["r"] = None  # no outranked count that night: the band stops before it and starts again after
    svg = draw(tmp_path, rows, FEED)

    areas = polygons(svg)
    assert [len(found(r'points="([^"]*)"', a, 1).split()) for a in areas] == [4, 6]


def test_a_band_whose_partner_has_no_number_draws_nothing_and_does_not_throw(tmp_path: Path) -> None:
    rows = [{k: v for k, v in row.items() if k != "r"} for row in feed()]

    svg = draw(tmp_path, rows, FEED)

    assert polygons(svg) == []
    assert "<polyline" in svg  # the tracked line is still drawn


def test_a_chart_with_a_band_runs_its_axis_from_zero(tmp_path: Path) -> None:
    with_band = axis(draw(tmp_path / "a", feed(), FEED))
    without = axis(draw(tmp_path / "b", feed(), [FEED[0]._replace(tip=()), FEED[1]._replace(band="")]))

    assert with_band[0] == "0"
    assert without[0] != "0"


def test_the_hover_adds_the_tip_fields_each_in_its_format_a_complement_as_its_complement(tmp_path: Path) -> None:
    lines = run(tmp_path, feed(), FEED, 700, "hover:700").split("\n")

    assert lines == [
        "2026-09-24",
        "Tracked  2,500",
        "Outranked  1,948",
        "Rank  552",
        "Percentile  77.9",
    ]


def test_a_night_without_a_tip_value_leaves_its_line_out(tmp_path: Path) -> None:
    rows = feed()
    rows[-1]["p"] = None

    lines = run(tmp_path, rows, FEED, 700, "hover:700").split("\n")

    assert lines == ["2026-09-24", "Tracked  2,500", "Outranked  1,948", "Rank  552"]


NOTE = (Note("2026-09-22", "HA changed the feed"),)


def noted(tmp_path: Path, notes: tuple[Note, ...] = NOTE, width: int = 700, rows: Any = None) -> str:
    seed(tmp_path, "a.jsonl", rows or feed())
    return run(tmp_path, rows or feed(), FEED, width, "svg", notes)


def test_a_note_in_the_range_is_a_dashed_vertical_line_with_its_text_above_the_plot(tmp_path: Path) -> None:
    svg = noted(tmp_path)

    (line,) = re.findall(r'<line class="note" x1="([\d.]+)" x2="([\d.]+)" y1="([\d.]+)" y2="([\d.]+)"[^>]*>', svg)
    assert line[0] == line[1]
    assert float(line[2]) < float(line[3])
    assert 'stroke-dasharray="' in found(r'<line class="note"[^>]*>', svg, 0)
    (label,) = re.findall(r'<text class="note"[^>]* y="([\d.]+)"[^>]*>HA changed the feed</text>', svg)
    assert float(label) < float(line[2])  # above where the plot (and its lines) start


def test_a_note_outside_the_range_draws_nothing(tmp_path: Path) -> None:
    svg = noted(tmp_path, (Note("2025-01-01", "Old"), Note("2027-01-01", "Future")))

    assert 'class="note"' not in svg


def test_a_note_near_the_right_edge_is_anchored_to_end_so_it_does_not_clip(tmp_path: Path) -> None:
    near, far = noted(tmp_path / "a", (Note("2026-09-24", "HA changed the feed"),)), noted(tmp_path / "b")

    assert 'text-anchor="end"' in found(r'<text class="note"[^>]*>', near, 0)
    assert 'text-anchor="start"' in found(r'<text class="note"[^>]*>', far, 0)


def test_a_chart_with_a_note_keeps_its_top_clear_for_the_text_and_one_without_is_unchanged(tmp_path: Path) -> None:
    plain = draw(tmp_path / "a", feed(), FEED)
    marked = noted(tmp_path / "b")

    def top(svg: str) -> float:
        return float(found(r'<rect class="hit" x="\d+" y="([\d.]+)"', svg, 1))

    assert top(plain) == 8
    assert top(marked) > 8


def test_a_stack_or_bars_draw_no_note(tmp_path: Path) -> None:
    rows = [{"date": f"2026-09-{19 + k}", "v": {"1.0": 5 + k}, "d": k + 1} for k in range(6)]
    stack, bars = Shown("v", "V", "breakdown"), Shown("d", "D", "daily")

    assert 'class="note"' not in run(tmp_path / "a", rows, stack, 700, "svg", NOTE)
    assert 'class="note"' not in run(tmp_path / "b", rows, bars, 700, "svg", NOTE)
