"""Trends: trends.json, the series of the fields each source's summary() names, and trends.html, a viewer of them.

A run writes both in the output folder after the reports (docs/TRENDS.md). trends.json is the product: a
documented, versioned format other tools load. trends.html holds a copy of the same data, since a page opened
from disk can't read a file beside it, and draws it with its own SVG and a little JavaScript, loading nothing
(ADR: the trends viewer draws its own SVG). Like the reports, trends reads only the output files, through store,
and a file it can't write is critical but never stops the run.
"""

from __future__ import annotations

import html
import itertools
import json
from bisect import bisect_right
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from leadline import store
from leadline.config import (
    BREAKDOWN_TOP,
    VERSION,
    Section,
    Shown,
    check_shown,
    complement,
    plain_file,
    untagged,
    version_key,
)
from leadline.context import Reporter

SOURCE = "trends"  # what a failure here is recorded critical as
DATA = "trends.json"
PAGE = "trends.html"
FORMAT = 3  # trends.json's format; a change a reader must know about is a new one (docs/TRENDS.md)
RANGES = ((30, "30d"), (90, "90d"), (365, "1y"), (0, "All"))  # the viewer's ranges, in days; 0 is all
DEFAULT_RANGE = 90
SINCE_AHEAD = 7  # days a chart of days since a start runs past the age of its newest item
FINDINGS = ("warn", "error")  # the check findings trends shows


def write(ctx: Reporter, sections: Sequence[Section], health: str, projects: Any = None, pages: Any = None) -> None:
    """Write trends.json and its pages from the output files: each of `pages` (valid), or with none, trends.html; each
    atomically, each on its own. A file that can't be written, or a bug building them, is logged and critical, and
    never raised."""
    try:
        data = build(ctx.out_dir, ctx.date, sections, health, projects)
        texts = {DATA: json.dumps(data, ensure_ascii=False) + "\n"}
        if pages is None:
            texts[PAGE] = page(data)
        for listed in pages or ():
            texts[listed["file"]] = page(_portfolio(data, listed["projects"]), listed["title"])
    except Exception as e:  # a bug here never stops the run: logged, and critical
        error = f"{type(e).__name__}: {e}"
        ctx.log.exception("ERROR: trends crashed: %s", error)
        ctx.record_critical(SOURCE, None, f"crashed: {error}")
        return
    done = []
    for name, text in texts.items():
        try:
            store.write_text(ctx.out_dir / name, text)
        except OSError as e:
            ctx.record_critical(SOURCE, name, f"can't write {name}: {e}")
            continue
        done.append(name)
    if done:
        ctx.log.info("Wrote %s", ", ".join(done))


# ---- projects ------------------------------------------------------------------------------------

PROJECT_FORM = '{"name": a display name, "sections": a list of section titles}'
SECTION_FORM = '{"section": a section title, "title": its heading}'


def validate(projects: Any, titles: Sequence[str]) -> list[str]:
    """Every problem with the `projects` setting, each starting with its path (`[1].sections[2]: `), given the
    titles of the configured sections; none for no setting."""
    if projects is None:
        return []
    if not isinstance(projects, list):
        return [": must be a list of projects"]
    problems: list[str] = []
    names: dict[str, int] = {}  # each name, and the project that has it
    owners: dict[str, int] = {}  # each section listed, and the project that lists it
    for i, project in enumerate(projects):
        if not (isinstance(project, dict) and set(project) == {"name", "sections"}):
            problems.append(f"[{i}]: must be {PROJECT_FORM}")
            continue
        problems += _name_problems(i, project["name"], names)
        problems += _sections_problems(i, project["sections"], titles, owners)
    return problems


def _name_problems(i: int, name: Any, names: dict[str, int]) -> list[str]:
    """What is wrong with project `i`'s name, given the projects' `names` before it, which a good one joins."""
    if not (isinstance(name, str) and name):
        return [f"[{i}].name: must be a non-empty string"]
    if name in names:
        return [f"[{i}].name: {json.dumps(name)} is also projects[{names[name]}]'s"]
    names[name] = i
    return []


def _sections_problems(i: int, listed: Any, titles: Sequence[str], owners: dict[str, int]) -> list[str]:
    """What is wrong with project `i`'s sections, given the sections listed before (`owners`), which each good
    one joins."""
    if not (isinstance(listed, list) and listed):
        return [f"[{i}].sections: must be a non-empty list of section titles"]
    problems = []
    for j, item in enumerate(listed):
        where, title = f"[{i}].sections[{j}]", item
        if isinstance(item, dict):
            if set(item) != {"section", "title"}:
                problems.append(f"{where}: must be a section title or {SECTION_FORM}")
                continue
            if not (isinstance(item["title"], str) and item["title"]):
                problems.append(f"{where}.title: must be a non-empty string")
            where, title = f"{where}.section", item["section"]
        if not (isinstance(title, str) and title in titles):
            problems.append(f"{where}: {json.dumps(title)} names no section")
        elif title in owners:
            problems.append(f"{where}: {json.dumps(title)} is also in projects[{owners[title]}]")
        else:
            owners[title] = i
    return problems


def _listed(item: Any) -> tuple[str, str | None]:
    """A valid project's section: its title, and the heading it gives it, if any."""
    return (item["section"], item["title"]) if isinstance(item, dict) else (item, None)


PAGE_FORM = '{"file": a *.html name, "title": its heading, "projects": a list of project names}'


def validate_pages(pages: Any, projects: Any) -> list[str]:
    """Every problem with the `pages` setting, each starting with its path (`[1].projects[0]: `), given the `projects`
    setting (valid); none for no setting."""
    if pages is None:
        return []
    if projects is None:
        return [": needs projects, whose names a page lists"]
    if not (isinstance(pages, list) and pages):
        return [": must be a non-empty list of pages"]
    names = {project["name"] for project in projects}
    problems: list[str] = []
    files: dict[str, int] = {}  # each file, case folded, and the page that writes it
    for i, listed in enumerate(pages):
        if not (isinstance(listed, dict) and set(listed) == {"file", "title", "projects"}):
            problems.append(f"[{i}]: must be {PAGE_FORM}")
            continue
        file, title = listed["file"], listed["title"]
        if not plain_file(file, ".html"):
            problems.append(f"[{i}].file: must be a plain *.html name, not {json.dumps(file)}")
        elif file.casefold() in files:  # one file to OneDrive on Windows, as row files are
            problems.append(f"[{i}].file: {json.dumps(file)} is also pages[{files[file.casefold()]}]'s")
        else:
            files[file.casefold()] = i
        if not (isinstance(title, str) and title):
            problems.append(f"[{i}].title: must be a non-empty string")
        problems += _page_projects_problems(i, listed["projects"], names)
    return problems


def _page_projects_problems(i: int, listed: Any, names: set[str]) -> list[str]:
    """What is wrong with page `i`'s projects, given the `names` of the projects."""
    if not (isinstance(listed, list) and listed):
        return [f"[{i}].projects: must be a non-empty list of project names"]
    problems = []
    for j, name in enumerate(listed):
        where = f"[{i}].projects[{j}]: {json.dumps(name)}"
        if not (isinstance(name, str) and name in names):
            problems.append(f"{where} names no project")
        elif name in listed[:j]:
            problems.append(f"{where} is already on this page")
    return problems


RUN_WIDE = ("reports", "trends")  # what is critical about these concerns every page


def _portfolio(data: Mapping[str, Any], projects: Sequence[str]) -> dict[str, Any]:
    """The data a page shows: its `projects`' sections, in their order, and the health that concerns them."""
    sections = [section for name in projects for section in data["sections"] if section["project"] == name]
    return {**data, "health": _page_health(data["health"], sections), "sections": sections}


def _page_health(health: Mapping[str, Any] | None, sections: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The health findings that concern a page's `sections`: the stale, failed and critical items of the files they
    read, and the checks of their sources; and every crash (critical with no file), and every critical of the reports
    and trends."""
    if health is None:
        return None
    files, sources = {s["file"] for s in sections}, {s["source"] for s in sections}
    return {
        "date": health["date"],
        "stale": [item for item in health["stale"] if item.get("file") in files],
        "failed": [item for item in health["failed"] if item.get("file") in files],
        "critical": [
            item
            for item in health["critical"]
            if item.get("file") is None or item.get("file") in files or item.get("source") in RUN_WIDE
        ],
        "checks": [check for check in health["checks"] if check.get("source") in sources],
    }


# ---- trends.json ---------------------------------------------------------------------------------


def build(
    folder: Path,
    day: date,
    sections: Sequence[Section],
    health: str,
    projects: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """trends.json's content (docs/TRENDS.md): the format, the run date `day`, the newest row of `health` (the
    health file), and each section's series, from the files in `folder`: each of `projects` (valid) in turn, its
    sections in its list's order, then the sections in none, in config order."""
    listed = [(p["name"], *_listed(item)) for p in projects or () for item in p["sections"]]
    project = {title: name for name, title, _ in listed}
    titled = {title: heading for _, title, heading in listed if heading is not None}
    order = {title: n for n, (_, title, _) in enumerate(listed)}
    ordered = sorted(sections, key=lambda section: order.get(section.title, len(order)))  # stable: the rest keep theirs
    alike = Counter((project[s.title], *_alike(s)) for s in sections if s.title in project)
    return {
        "format": FORMAT,
        "date": day.isoformat(),
        "health": _health(folder / health),
        "sections": [
            _section(folder, section, project.get(section.title), titled.get(section.title), alike)
            for section in ordered
        ],
    }


def _alike(section: Section) -> tuple[str, str | None]:
    """What makes two sections of a project alike for their headings: their source, and the card they are rows of."""
    return section.source, section.card.name if section.card else None


def _heading(section: Section, project: str | None, alike: Counter[tuple[str, str, str | None]]) -> str:
    """A section's panel heading, given the count of each project's sections by source and card (`alike`): in a
    project, its label (its card's name, for a row of a card), and its name too where the project has another of its
    source and card; in none, both, its whole identity."""
    alone = project is not None and alike[(project, *_alike(section))] == 1
    label = section.card.name if section.card else section.label
    return label if section.name is None or alone else f"{label} · {section.name}"


def _health(path: Path) -> dict[str, Any] | None:
    """The newest health row's findings, its checks at warn or error; None with no health row."""
    rows = store.dated_rows(path).good
    if not rows:
        return None
    row = rows[-1]
    return {
        "date": row["date"],
        **{key: list(row.get(key) or []) for key in ("stale", "failed", "critical")},
        "checks": [check for check in row.get("checks") or [] if check.get("level") in FINDINGS],
    }


def _section(
    folder: Path, section: Section, project: str | None, title: str | None, alike: Counter[tuple[str, str, str | None]]
) -> dict[str, Any]:
    """The section, in `project`, headed by the project's `title` for it or its own (`_heading`): its good rows, one a
    date, oldest first, their dates once, and a series for each field shown."""
    rows = store.dated_rows(folder / section.file, section.where, section.hide).good
    shown_all = [*section.shown, *section.charts]
    stacks = {shown.field for shown in shown_all if shown.kind == "breakdown"}
    for shown in shown_all:
        if shown.into and shown.into not in stacks:
            raise ValueError(f"{shown.field}: into: names no breakdown of the summary")
    for shown in shown_all:
        check_shown(shown)
    _check_partners(shown_all)
    notes = {"notes": [{"date": note.date, "text": note.text} for note in section.notes]} if section.notes else {}
    card = {"card": section.card.name, "row": section.card.row, "about": section.card.about} if section.card else {}
    return {
        "source": section.source,
        "name": section.name,
        "title": section.title,
        "label": section.label,
        "heading": title or _heading(section, project, alike),
        "project": project,
        "file": section.file,
        "where": dict(section.where) if section.where else None,
        "dates": [row["date"] for row in rows],
        "fields": [_field(rows, shown) for shown in shown_all],
        **card,
        **notes,
    }


def _drawing(shown: Shown) -> dict[str, Any]:
    """The settings of a field that only shape how the pages draw it, those it sets."""
    settings: dict[str, Any] = {
        "chart": shown.chart,
        "half": shown.half,
        "trend": shown.trend,
        "color": shown.color,
        "minus": shown.minus,
        "band": shown.band,
        "tip": list(shown.tip),
        "format": shown.format,
    }
    return {key: value for key, value in settings.items() if value}


def _check_partners(shown_all: Sequence[Shown]) -> None:
    """Raise ValueError for a `band` that names no field of its chart, or a `tip` that names no field of the section.
    A path names the field that has it without `minus`: a derived field shares its path with the field it is made of."""
    plain = {shown.field: shown for shown in shown_all if not shown.minus}
    for shown in shown_all:
        if shown.band and (shown.band not in plain or plain[shown.band].chart != shown.chart):
            raise ValueError(f"{shown.field}: band: names no field of the chart {shown.chart}")
        for path in shown.tip:
            if path not in plain:
                raise ValueError(f"{shown.field}: tip: {path} names no field of the section")


def _field(rows: Sequence[store.Row], shown: Shown) -> dict[str, Any]:
    """A field's series: its value in each row (with `complement`, 100 less it), None where the row has none; a
    breakdown's, by key. A field not charted alone says `charted: false`, and one into a stack names it."""
    check_shown(shown)
    stacked = shown.kind == "breakdown" or bool(shown.into)  # a stack's title and its totals read as titles
    field: dict[str, Any] = {
        "field": shown.field,
        "label": _title(shown.label) if stacked else shown.label,
        "kind": shown.kind,
    }
    field.update(_drawing(shown))
    if shown.into or not shown.charted:
        field["charted"] = False
    if shown.into:
        field["into"] = shown.into
    if shown.since:
        return {**field, **_since(rows, shown)}
    if shown.kind == "breakdown":
        objects = _by_key(rows, shown) if shown.by else [store.value(row, shown.field) for row in rows]
        drawn = {"lines": True} if shown.lines else {}
        return {**field, **_breakdown(objects, shown.neutral, shown.bands, shown.lines, shown.prereleases), **drawn}
    if shown.complement:
        return {**field, "complement": True, "values": [complement(store.value(row, shown.field)) for row in rows]}
    if shown.minus:
        return {
            **field,
            "values": [_less(store.value(row, shown.field), store.value(row, shown.minus)) for row in rows],
        }
    return {**field, "values": [store.value(row, shown.field) for row in rows]}


def _less(whole: Any, part: Any) -> Any:
    """`whole` less `part`; None unless both are numbers (a boolean is none)."""
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (whole, part)):
        return None
    return whole - part


def _by_key(rows: Sequence[store.Row], shown: Shown) -> list[dict[str, float] | None]:
    """A list's counts in each row as an object of counts (`Shown.by`), for a breakdown: the numbers at the field's
    path added up for each item, named by its `by` key without a leading `v` before a digit (`v1.2.0`), each at the
    greatest it has had in any row so far, so a count never falls when an item leaves the list (a list cut at its
    limit) or drops. An item has no count in the rows before its first reading, so it rises from 0 there (its
    `published_at` is not read). A row whose field is not a list is None and leaves the counts as they were; an item
    that is no object or has no text key is not counted."""
    head, _, path = shown.field.partition("[].")
    best: dict[str, float] = {}  # each tag's greatest count so far
    out: list[dict[str, float] | None] = []
    for row in rows:
        items = store.value(row, head)
        if not isinstance(items, list):
            out.append(None)
            continue
        for item in items:
            key = item.get(shown.by) if isinstance(item, dict) else None
            if isinstance(key, str):
                best[key] = max(best.get(key, 0), _leaf_sum(item, path))
        counts: dict[str, float] = {}
        for tag, n in best.items():
            name = untagged(tag)
            counts[name] = counts.get(name, 0) + n
        out.append(counts)
    return out


def _since(rows: Sequence[store.Row], shown: Shown) -> dict[str, Any]:
    """A breakdown of lines by days since each item's start (`Shown.since`, the item's key with its start date, whose
    first 10 characters are the day): one line an item, whose value at day d is its count (`_by_key`) as of the start
    day plus d, the last row on or before that day standing for a day without one; None before its first listing and
    after the last row. An item with no readable start, or started before the first row's day, is not drawn; of the
    rest only the newest BREAKDOWN_TOP are, the older not added into `other`. The series run from day 0 (each item's
    start) to SINCE_AHEAD days past the newest item's age on the last row, `starts` says each line's.
    With `prereleases` false prerelease items are left out first."""
    held = _by_key(rows, shown)
    readings = [(date.fromisoformat(row["date"]), counts) for row, counts in zip(rows, held) if counts is not None]
    if not readings:
        return {**_breakdown([], shown.neutral, shown.bands, True, shown.prereleases), "lines": True, "since": True}
    starts = _starts(rows, shown)
    first, last = readings[0][0], readings[-1][0]
    drawn = {
        name: start for name, start in starts.items() if start >= first and (shown.prereleases or not _prerelease(name))
    }
    kept = sorted(drawn, key=version_key)[-BREAKDOWN_TOP:]
    days = [then for then, _ in readings]
    objects: list[dict[str, float] | None] = []
    # The axis runs SINCE_AHEAD days past the newest item's age, however much older the others are, so the newest line
    # has room to grow against theirs.
    for offset in range((last - max(drawn[name] for name in kept)).days + SINCE_AHEAD + 1 if kept else 0):
        shown_on: dict[str, float] = {}
        for name in kept:
            day = drawn[name] + timedelta(days=offset)
            at = bisect_right(days, day) - 1
            if day <= last and at >= 0 and name in readings[at][1]:
                shown_on[name] = readings[at][1][name]
        objects.append(shown_on or None)
    series = _breakdown(objects, shown.neutral, shown.bands, True, shown.prereleases)
    live = {name: {n for n, obj in enumerate(objects) if obj and name in obj} for name in series["keys"]}
    series["values"] = [
        [value if n in live[name] else None for n, value in enumerate(values)]
        for name, values in zip(series["keys"], series["values"])
    ]
    return {
        **series,
        "lines": True,
        "since": True,
        "starts": {name: drawn[name].isoformat() for name in series["keys"]},
    }


def _starts(rows: Sequence[store.Row], shown: Shown) -> dict[str, date]:
    """Each item's start day (`Shown.since`): the first 10 characters of its start value in the first row that lists
    it, named as `_by_key` names it; an item whose start is no date is left out."""
    head = shown.field.partition("[].")[0]
    starts: dict[str, date] = {}
    for row in rows:
        items = store.value(row, head)
        for item in items if isinstance(items, list) else []:
            key, start = (item.get(shown.by), item.get(shown.since)) if isinstance(item, dict) else (None, None)
            if isinstance(key, str) and isinstance(start, str) and untagged(key) not in starts:
                try:
                    starts[untagged(key)] = date.fromisoformat(start[:10])
                except ValueError:
                    continue
    return starts


def _leaf_sum(node: Any, path: str) -> float:
    """The numbers at `path` in `node` added up, a `name[].` in the path fanning out over a list: a missing value, a
    boolean or a text counts 0."""
    head, fan, tail = path.partition("[].")
    if not fan:
        found = store.value(node, head)
        return found if isinstance(found, (int, float)) and not isinstance(found, bool) else 0
    items = store.value(node, head)
    return sum(_leaf_sum(item, tail) for item in items) if isinstance(items, list) else 0


def _title(label: str) -> str:
    """A label as a title: underscores as spaces, the first letter capitalized (`catalog_by_status`: "Catalog by
    status"), so a config key reads as one."""
    text = label.replace("_", " ")
    return text[:1].upper() + text[1:]


def _breakdown(
    objects: Sequence[Any],
    neutral: Sequence[str] = (),
    bands: str = "line",
    lines: bool = False,
    prereleases: bool = True,
) -> dict[str, Any]:
    """Objects of counts as series: BREAKDOWN_TOP keys, bottom band first, each key's count in each row (0 where
    the row lacks the key), and the rest of each row's counts added up, `other`. A row with no object of counts is
    None in every series.

    Where every key is a version (`versions`), the prereleases (`2.0.0-beta.1`) are added up apart, `prerelease`,
    and by base version (`betas`, as `_betas` gives them); the rest are counted by release line (`_release_line`):
    the keys are the newest lines, oldest first, and `other` the older ones. With `bands` `release`, they are counted
    by exact release instead (`3.14.1`), every one seen, oldest first, and `other` is empty (with `lines`, the newest
    BREAKDOWN_TOP of them, `other` the older ones); the series then says `bands`. Otherwise they are the newest
    object's keys, the largest count first, as the default summary shows them; then, where it has fewer, the
    others by the largest share of any one row's counts they had. Each of the `neutral` keys a row has is one
    more key, after those, whatever its rank, and is in `neutral`.
    """
    counts = [
        {
            key: n
            for key, n in obj.items()
            if isinstance(n, (int, float)) and not isinstance(n, bool) and (prereleases or not _prerelease(key))
        }
        if isinstance(obj, dict)
        else None
        for obj in objects
    ]
    peak: dict[str, float] = {}
    for row in counts:
        total = sum(row.values()) if row else 0
        for key, n in (row or {}).items():
            peak[key] = max(peak.get(key, 0), n / total if total else 0)
    versions = bool(peak) and all(VERSION.fullmatch(key) for key in peak)
    extra: dict[str, Any] = {}
    kept = [key for key in neutral if key in peak and not versions]
    if versions:
        extra["prerelease"] = [
            None if row is None else sum(n for key, n in row.items() if "-" in key) for row in counts
        ]
        extra["betas"] = _betas(counts, peak)
        by_release = bands == "release"
        band = str if by_release else _release_line
        counts = [
            None if row is None else _sum_by(band, {key: n for key, n in row.items() if "-" not in key})
            for row in counts
        ]
        keys = sorted({band(key) for key in peak if "-" not in key}, key=version_key)
        keys = keys if by_release and not lines else keys[-BREAKDOWN_TOP:]
        if by_release:
            extra["bands"] = bands
    else:
        newest = next((row for row in reversed(counts) if row is not None), {})
        ranked = [
            *sorted(newest, key=lambda key: -newest[key]),
            *sorted(set(peak) - set(newest), key=lambda key: -peak[key]),
        ]
        keys = [key for key in ranked if key not in kept][:BREAKDOWN_TOP] + kept
    return {
        "versions": versions,
        "keys": keys,
        "values": [[None if row is None else row.get(key, 0) for row in counts] for key in keys],
        "other": [None if row is None else sum(n for key, n in row.items() if key not in keys) for row in counts],
        **extra,
        **({"neutral": kept} if kept else {}),
    }


def _prerelease(key: str) -> bool:
    """Whether a breakdown's key is a prerelease version (`2.0.0-beta.1`)."""
    return "-" in key and bool(VERSION.fullmatch(key))


def _base(version: str) -> str:
    """A prerelease's base version: its release part (`3.14.0`, for 3.14.0-beta.11)."""
    return version.partition("-")[0]


def _betas(counts: Sequence[Mapping[str, float] | None], peak: Mapping[str, float]) -> dict[str, Any]:
    """The prereleases' counts by base version, as `_breakdown` gives a breakdown: BREAKDOWN_TOP `keys`, the newest
    bases, oldest first, each key's count in each row (0 where the row lacks it), and `other`, the older bases'."""
    keys = sorted({_base(key) for key in peak if "-" in key}, key=version_key)[-BREAKDOWN_TOP:]
    by_base = [
        None if row is None else _sum_by(_base, {key: n for key, n in row.items() if "-" in key}) for row in counts
    ]
    return {
        "keys": keys,
        "values": [[None if row is None else row.get(key, 0) for row in by_base] for key in keys],
        "other": [None if row is None else sum(n for key, n in row.items() if key not in keys) for row in by_base],
    }


def _sum_by(group: Callable[[str], str], row: Mapping[str, float]) -> dict[str, float]:
    """A row's counts by version, added up by each version's `group`."""
    groups: dict[str, float] = {}
    for version, n in row.items():
        groups[group(version)] = groups.get(group(version), 0) + n
    return groups


def _release_line(version: str) -> str:
    """A release's line: its major.minor (`3.14`, for 3.14.15)."""
    return ".".join(version.split(".")[:2])


# ---- trends.html ---------------------------------------------------------------------------------


OTHER = "Other"  # the page's group of the sections in no project


def page(data: Mapping[str, Any], title: str | None = None) -> str:
    """The viewer: the page's text, headed by its `title` (with none, "Trends"), each section that has a field with a
    chart for each field it charts, grouped by project with a nav between them when any section on the page is in one,
    and the data for the script that draws them. Everything the data holds is escaped: as HTML in the page, and so
    that nothing in the embedded JSON can close its script."""
    embedded = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    embedded = embedded.replace("&", "\\u0026")
    buttons = "".join(
        f'<button type="button" data-days="{days}" aria-pressed="{str(days == DEFAULT_RANGE).lower()}">{label}</button>'
        for days, label in RANGES
    )
    # A section with no field has nothing the page can show (its content is report lines), nor one whose charts
    # have no rows on any date.
    sections = [(i, section) for i, section in enumerate(data["sections"]) if section["fields"] and _drawn(section)]
    nav, body = "", "\n".join(_section_html(panel, "h2") for panel in _panels(sections))
    if any(section["project"] is not None for _, section in sections):
        groups = [
            (OTHER if name is None else name, list(group))
            for name, group in itertools.groupby(sections, key=lambda pair: pair[1]["project"])
        ]
        links = "".join(f'<a href="#project-{n}">{_escaped(name)}</a>' for n, (name, _) in enumerate(groups))
        nav = f'<nav class="projects" aria-label="Projects">{links}</nav>'
        body = "\n".join(
            "\n".join(
                [
                    f'<div class="project" id="project-{n}">\n<h2>{_escaped(name)}</h2>',
                    *(_section_html(panel, "h3") for panel in _panels(group)),
                    "</div>",
                ]
            )
            for n, (name, group) in enumerate(groups)
        )
    return _PAGE.format(
        title=_escaped(title or "leadline trends"),
        heading=_escaped(title or "Trends"),
        date=_escaped(data["date"]),
        health=_health_html(data["health"]),
        buttons=buttons,
        nav=nav,
        sections=body,
        data=embedded,
        style=_STYLE,
        script=_SCRIPT,
    )


def _drawn(section: Mapping[str, Any]) -> bool:
    """Whether the page shows the section: it has no good row yet (and says so), or has no chart, or one of its
    charts has a row on some date. The viewer draws a row for a reading, but a chart of lines draws only a band that
    has a count, so a section whose releases have no downloads (every count 0) has nothing to draw."""
    charts = [f for f in section["fields"] if _charted(f) or f.get("into")]
    return not (section["dates"] and charts) or any(_has_rows(f) for f in charts)


def _has_rows(field: Mapping[str, Any]) -> bool:
    """Whether a field's series has a reading the viewer draws, over all dates: a number, and for a chart of lines
    (`lines`) one above 0. The viewer's script draws by the same rule: a change to one is a change to the other
    (docs/TRENDS.md, "Known limits")."""
    series: list[Any] = [field.get("values"), field.get("other"), *(field.get("betas") or {}).values()]
    numbers = [v for v in _flat(series) if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return any(v > 0 for v in numbers) if field.get("lines") else bool(numbers)


def _flat(value: Any) -> Iterator[Any]:
    """The leaves of nested lists."""
    if isinstance(value, list):
        for item in value:
            yield from _flat(item)
    else:
        yield value


def _escaped(value: Any) -> str:
    return html.escape(str(value))


def _health_html(health: Mapping[str, Any] | None) -> str:
    """The health row's findings as pills, in the default summary's words; or one saying all sources are current."""
    if health is None:
        return '<span class="pill warn">No health row yet</span>'
    items = []
    for failed in health["failed"]:
        nights = f" ({failed.get('streak')} nights)" if (failed.get("streak") or 0) > 1 else ""
        items.append(f"Failed: {failed.get('file')}, {failed.get('status')} since {failed.get('since')}{nights}")
    for stale in health["stale"]:
        last = f", last row {stale['last']}" if stale.get("last") else ""
        items.append(f"Stale: {stale.get('file')}{last}")
    items += [f"Critical: {c.get('source')}: {c.get('reason')}" for c in health["critical"]]
    items += [f"Check {c.get('source')} ({c.get('level')}): {c.get('message')}" for c in health["checks"]]
    if not items:
        return '<span class="pill ok">All sources current</span>'
    return "".join(f'<span class="pill warn">{_escaped(item)}</span>' for item in items)


LATE = 7  # the days a chart may start after its section's first row before the page says when it was counted from


def _panels(sections: Sequence[tuple[int, Mapping[str, Any]]]) -> list[list[tuple[int, Mapping[str, Any]]]]:
    """The `(index, section)` pairs as panels, in order: the sections that are rows of a card (`card`) with the same
    heading are one panel, at the place of the first; every other section is a panel of its own."""
    panels: list[list[tuple[int, Mapping[str, Any]]]] = []
    where: dict[tuple[str, str], int] = {}
    for pair in sections:
        section = pair[1]
        key = (section["heading"], section["card"]) if "card" in section else None
        if key is not None and key in where:
            panels[where[key]].append(pair)
        else:
            if key is not None:
                where[key] = len(panels)
            panels.append([pair])
    return panels


def _section_html(panel: Sequence[tuple[int, Mapping[str, Any]]], heading: str) -> str:
    """A panel, headed by a `heading` element: its heading, the newest of each `value` field as text, and a chart
    for each field charted alone, the stacks first, each on a wide card, as is the one chart beside them; a chart that
    starts late says from when. A panel of a card has a row for each of its sections, titled, with what it shows."""
    head = f'<span class="head">{_escaped(panel[0][1]["heading"])}</span>'
    for _, section in panel:
        for field in section["fields"]:
            if field["kind"] == "value":
                newest = next((v for v in reversed(field["values"]) if v is not None), None)
                head += (
                    "" if newest is None else f'<span class="val">{_escaped(field["label"])}: {_escaped(newest)}</span>'
                )
    rows = [_row_html(i, section) for i, section in panel]
    return "\n".join([f"<section>\n<{heading}>{head}</{heading}>", *rows, "</section>"])


def _row_html(i: int, section: Mapping[str, Any]) -> str:
    """A section's charts, as a grid, with a note where it has no good row yet; in a row, with its title and what it
    shows, for a section that is part of a card."""
    charted = [(j, field) for j, field in enumerate(section["fields"]) if _charted(field)]
    charted = _one_figure_per_chart(charted)
    charted.sort(key=lambda pair: not _stack(pair[1]))  # stable: each kind keeps its order
    alone = sum(not _stack(field) for _, field in charted) == 1  # the one chart beside the stacks
    charts = [
        f'<figure class="chart{" wide" if alone or _stack(field) else ""}" '
        f'data-section="{i}" data-field="{j}">'
        f'<figcaption>{_escaped(field.get("chart") or field["label"])}<span class="now"></span></figcaption>'
        f'<div class="plot"></div>{_note(section, field)}</figure>'
        for j, field in charted
    ]
    empty = [] if section["dates"] else ['<p class="empty">No good rows yet.</p>']
    grid = "\n".join([*empty, '<div class="grid">', *charts, "</div>"])
    if "card" not in section:
        return grid
    title = f'<h4>{_escaped(section["row"])}</h4>\n<p class="about">{_escaped(section["about"])}</p>'
    return f'<div class="row">\n<div>\n{title}\n</div>\n{grid}\n</div>'


def _stack(field: Mapping[str, Any]) -> bool:
    """Whether a field's chart takes the section's whole width: a breakdown, unless it says it is `half`."""
    return field["kind"] == "breakdown" and not field.get("half")


def _charted(field: Mapping[str, Any]) -> bool:
    """Whether the page charts the field alone: not a `value`, and not one that is off the page or into a stack."""
    return field["kind"] != "value" and bool(field.get("charted", True))


def _one_figure_per_chart(charted: Sequence[tuple[int, dict[str, Any]]]) -> list[tuple[int, dict[str, Any]]]:
    """The `charted` fields with each chart of several (`chart`) once, at its first field's place."""
    seen: set[str] = set()
    kept = []
    for j, field in charted:
        name = field.get("chart")
        if name is None or name not in seen:
            kept.append((j, field))
        seen.add(name or "")
    return kept


def _members(fields: Sequence[Mapping[str, Any]], field: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The fields drawn in `field`'s chart: those that share its `chart` name, or it alone."""
    return [f for f in fields if f.get("chart") == field["chart"]] if field.get("chart") else [field]


def _note(section: Mapping[str, Any], field: Mapping[str, Any]) -> str:
    """The note under a chart: when it was counted from; none for a chart of days since a start (`since`), which has
    no calendar to start late in."""
    return "" if field.get("since") else _late_note(section["dates"], _members(section["fields"], field))


def _late_note(dates: Sequence[str], fields: Sequence[Mapping[str, Any]]) -> str:
    """The note under a chart whose first reading (of its fields, the earliest) is more than LATE days after the
    section's first date: "Counted from <date>"; none for a chart that starts with the section."""
    firsts = [
        next(
            (n for n, value in enumerate(f["other"] if f["kind"] == "breakdown" else f["values"]) if value is not None),
            None,
        )
        for f in fields
    ]
    first = min((n for n in firsts if n is not None), default=None)
    if first is None or (date.fromisoformat(dates[first]) - date.fromisoformat(dates[0])).days <= LATE:
        return ""
    return f'<p class="note">Counted from {_escaped(dates[first])}</p>'


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{style}</style>
</head>
<body>
<div class="wrap">
<header>
<div><h1>{heading}</h1><p class="sub">Run {date}</p></div>
<div class="ranges" role="group" aria-label="Date range">{buttons}</div>
</header>
<div class="health">{health}</div>
{nav}
{sections}
</div>
<script type="application/json" id="trends-data">{data}</script>
<script>{script}</script>
</body>
</html>
"""

_STYLE = """
:root {
  --bg: #f6f7f9; --panel: #ffffff; --ink: #1b2430; --muted: #5d6b7c; --grid: #e3e7ec; --edge: #d5dbe2;
  --accent: #2563a8; --accent-soft: rgba(37, 99, 168, 0.14); --bar: #4f86c6;
  --ok: #1f7a4d; --ok-bg: #e3f3ea; --warn: #9a5b00; --warn-bg: #fbeed8;
  --s1: #2563a8; --s2: #3f9b7c; --s3: #c98a1b; --s4: #9b59b6; --s5: #c2474f; --s6: #7a8796;
  --sans: system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: ui-monospace, "Cascadia Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --bg: #11161d; --panel: #18202a; --ink: #e4e9ef; --muted: #93a1b2; --grid: #27313d; --edge: #2e3a48;
    --accent: #6ea8e8; --accent-soft: rgba(110, 168, 232, 0.18); --bar: #5b93d3;
    --ok: #6fd19f; --ok-bg: #15352a; --warn: #f0b35a; --warn-bg: #3a2a12;
    --s1: #6ea8e8; --s2: #5cc3a0; --s3: #e0a94a; --s4: #b98ad0; --s5: #e0717a; --s6: #8a97a6;
  }
}
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.5 var(--sans); padding: 24px 16px 48px; }
.wrap { max-width: 1040px; margin: 0 auto; display: flex; flex-direction: column; gap: 20px; }
header { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 12px; }
h1 { font-size: 22px; font-weight: 600; margin: 0; }
.sub { color: var(--muted); font-size: 13px; margin: 2px 0 0; }
.health { display: flex; flex-wrap: wrap; gap: 8px; font-size: 13px; }
.pill { padding: 3px 10px; border-radius: 999px; font-weight: 500; overflow-wrap: anywhere; }
.pill.ok { color: var(--ok); background: var(--ok-bg); }
.pill.warn { color: var(--warn); background: var(--warn-bg); }
.ranges { display: flex; gap: 4px; border: 1px solid var(--edge); border-radius: 8px; padding: 3px;
  background: var(--panel); }
.ranges button { font: 500 13px var(--sans); color: var(--muted); background: none; border: 0; border-radius: 6px;
  padding: 5px 12px; cursor: pointer; }
.ranges button[aria-pressed="true"] { background: var(--accent-soft); color: var(--accent); }
.ranges button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
section { background: var(--panel); border: 1px solid var(--edge); border-radius: 10px; padding: 16px 18px;
  display: flex; flex-direction: column; gap: 12px; }
section h2, section h3 { font-size: 15px; font-weight: 600; margin: 0; display: flex; flex-wrap: wrap;
  gap: 4px 12px; align-items: baseline; overflow-wrap: anywhere; }
.row { display: flex; flex-direction: column; gap: 10px; }
.row + .row { border-top: 1px solid var(--edge); padding-top: 14px; }
.row h4 { font-size: 14px; font-weight: 600; margin: 0; }
.about { font-size: 12px; color: var(--muted); margin: 2px 0 0; }
section .val, .empty { font-size: 12px; font-weight: 400; color: var(--muted); margin: 0; }
nav { position: sticky; top: 0; z-index: 3; display: flex; gap: 6px; overflow-x: auto; padding: 8px 0;
  margin: -8px 0; background: var(--bg); }
nav a { font: 500 13px var(--sans); color: var(--accent); text-decoration: none; white-space: nowrap;
  padding: 4px 12px; border: 1px solid var(--edge); border-radius: 999px; background: var(--panel); }
nav a:hover { background: var(--accent-soft); }
nav a:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.project { display: flex; flex-direction: column; gap: 12px; scroll-margin-top: 52px; }
.project > h2 { font-size: 18px; font-weight: 600; margin: 8px 0 0; overflow-wrap: anywhere; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
figure { margin: 0; min-width: 0; }
figcaption { font-size: 12px; font-weight: 500; color: var(--muted); margin: 0 0 4px; display: flex;
  justify-content: space-between; gap: 8px; }
figure.wide { grid-column: 1 / -1; }
.note { font-size: 12px; color: var(--muted); margin: 4px 0 0; }
figcaption .now { color: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums; }
.plot { position: relative; font-size: 12px; color: var(--muted); }
.legend { display: flex; flex-wrap: wrap; gap: 2px 14px; margin: 0 0 4px; color: var(--ink); }
.legend .key { display: inline-flex; align-items: center; gap: 5px; font-variant-numeric: tabular-nums; }
.legend i { width: 14px; height: 3px; border-radius: 2px; flex: none; }
svg { display: block; max-width: 100%; overflow: visible; touch-action: pan-y; }
svg text { font: 11px var(--mono); fill: var(--muted); }
.tip { position: absolute; top: 0; pointer-events: none; background: var(--ink); color: var(--bg);
  font: 12px/1.4 var(--mono); padding: 6px 8px; border-radius: 6px; white-space: nowrap;
  z-index: 5; }  /* above the sticky nav (3), which would hide a tooltip that reaches the top of the screen */
"""

# Draws each chart as SVG markup: numbers, fixed names, and the data's texts escaped (esc); the tooltip's texts go
# in through textContent.
_SCRIPT = """
(function () {
  "use strict";
  var data = JSON.parse(document.getElementById("trends-data").textContent);
  var DAY = 864e5, HEIGHT = 170, STACK = 240, L = 44, TOP = 8, B = 22, LABELS = 96;
  var NOTE = 16;  // the room above a plot for its notes' text
  var TREND_DAYS = 4;  // the days of 7 that must have a count for the trend to have a mean there
  var CHAR = 6.8;  // the width of a character of the page's 11px monospace
  var end = time(data.date), days = +document.querySelector('.ranges [aria-pressed="true"]').dataset.days;
  var exact = new Intl.NumberFormat("en", { maximumFractionDigits: 2 });
  var compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
  var PALETTE = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)"];

  function time(d) { return Date.parse(d + "T00:00:00Z"); }
  function num(v) { return typeof v === "number" && isFinite(v); }
  function show(v) { return num(v) ? exact.format(v) : String(v); }
  // A number in its field's `format`: a fixed count of decimals (".1f") as that, any other spec as a plain number.
  function formatted(v, spec) {
    var m = /^\\.(\\d)f$/.exec(spec || "");
    return m ? new Intl.NumberFormat("en", { minimumFractionDigits: +m[1], maximumFractionDigits: +m[1] }).format(v)
      : show(v);
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }); }
  // An axis that fits its readings: the fewest of 4 grid steps, each 1, 1.5, 2, 2.5, 3, 4, 5, 6 or 8 times a power
  // of ten, that run from a multiple of the step a little below the smallest reading to a little above the largest
  // (a little is 2% of their span); zero is the bottom when nothing is below it, and the axis never runs below zero
  // for readings that are never negative. Readings that agree span from
  // zero to them, and none span less than 5% of the largest, so a wobble stays small. Returns [bottom, top]:
  // 67 from zero tops at 80, readings from 24 to 73 run from 15 to 75.
  function fit(min, max) {
    if (min === max) { min = Math.min(min, 0); max = Math.max(max, 0); }
    if (!(max > min)) { return [0, 1]; }
    var least = 0.05 * Math.max(Math.abs(min), Math.abs(max));
    if (max - min < least) { var mid = (min + max) / 2; min = mid - least / 2; max = mid + least / 2; }
    var pad = (max - min) * 0.02, p = Math.pow(10, Math.floor(Math.log10((max - min) / 4)) - 1);
    for (var e = 0; e < 3; e++, p *= 10) {
      for (var f of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8]) {
        var step = f * p, lo = min === 0 ? 0 : Math.floor((min - pad) / step) * step;
        if (min >= 0 && lo < 0) { lo = 0; }  // readings that are never negative never run below zero
        if (lo + 4 * step >= max + pad) { return [+lo.toPrecision(12), +(lo + 4 * step).toPrecision(12)]; }
      }
    }
  }
  // The runs of readings on consecutive days: each a list of positions in `stamps`, the readings' times in order.
  // Two readings with more than a day between them, a row with none among them or not, are in different runs.
  function runs(stamps) {
    var found = [[]];
    stamps.forEach(function (t, n) {
      if (n && t - stamps[n - 1] > DAY) { found.push([]); }
      found[found.length - 1].push(n);
    });
    return found;
  }
  function day(t, long) {
    var d = new Date(t), m = d.toLocaleString("en", { month: "short", timeZone: "UTC" });
    return long ? m + " \\u2019" + String(d.getUTCFullYear()).slice(2) : m + " " + d.getUTCDate();
  }
  function sum(list) { return list.reduce(function (a, b) { return a + b; }, 0); }

  // The bands of a breakdown, bottom first. Release lines (3.14.x) or, with `bands` release, every exact release
  // (3.14.1): the older ones at the bottom and the newest in the accent, fading toward the grid color, then each
  // base version's prereleases (3.14.0 betas), oldest first and fading the same way in the beta color, on top.
  // Any other keys: largest first in the palette, a neutral key (unknown) in gray, and the rest on top in a lighter
  // gray.
  function fade(bands, colour) {
    bands.forEach(function (band, k) {
      var p = bands.length === 1 ? 100 : Math.round(15 + 85 * k / (bands.length - 1));
      band.color = "color-mix(in srgb, " + colour + " " + p + "%, var(--grid))";
    });
    return bands;
  }
  function bands(field) {
    var keys = field.keys.map(function (key, k) {
      var name = !field.versions ? key.replace(/_/g, " ") : field.bands === "release" ? key : key + ".x";
      return { name: name, values: field.values[k] };
    });
    if (field.versions) {
      var all = fade([{ name: "older", values: field.other }].concat(keys), "var(--accent)");
      all[all.length - 1].strong = true;
      var betas = field.betas.keys.map(function (key, k) {
        return { name: key + " betas", values: field.betas.values[k] };
      });
      return all.concat(fade([{ name: "older betas", values: field.betas.other }].concat(betas), "var(--s3)"));
    }
    var neutral = field.neutral || [];
    keys.forEach(function (band, k) {
      band.color = neutral.indexOf(field.keys[k]) < 0 ? PALETTE[k] : "var(--muted)";
    });
    return keys.concat([{ name: "other", values: field.other, color: "var(--s6)" }]);
  }

  // The field a path names: of two with one path (a field and one derived from it with `minus`), the one without.
  function named(section, path) {
    return section.fields.filter(function (f) { return f.field === path && !f.minus; })[0];
  }

  // The lines of a chart of several fields (`chart`): each field of the section with that name, in its color if it
  // names a theme token, else the palette's. A field with a `band` has the values of the field it shades to, and its
  // `tip` fields are the others its hover lists.
  function lineLayers(section, field) {
    return section.fields.filter(function (f) { return f.chart === field.chart; }).map(function (f, k) {
      var partner = f.band ? named(section, f.band) : null;
      return {
        name: f.label, values: f.values, tip: f.tip || [], band: partner ? partner.values : null,
        color: /^s[1-6]$/.test(f.color) ? "var(--" + f.color + ")" : PALETTE[k % PALETTE.length]
      };
    });
  }

  // A band's label at the right edge: its name and newest count.
  function tag(band, count) { return band.name + " " + compact.format(count); }
  // The label cut to fit `room` pixels: a name that does not fit is cut short with an ellipsis, never its count.
  function fitted(band, count, room) {
    var text = tag(band, count), fit = Math.floor(room / CHAR);
    if (text.length <= fit) { return text; }
    var value = " " + compact.format(count);
    return band.name.slice(0, Math.max(1, fit - value.length - 1)) + "\u2026" + value;
  }

  // The total of a daily count's newest whole week, Monday to Sunday, every day of which has a count; null for none.
  function lastWeek(dates, values) {
    var by = {}, order = [];
    dates.forEach(function (d, i) {
      if (!num(values[i])) { return; }
      var t = time(d), monday = t - ((new Date(t).getUTCDay() + 6) % 7) * DAY;
      if (!by[monday]) { by[monday] = []; order.push(monday); }
      by[monday].push(values[i]);
    });
    var whole = order.filter(function (m) { return by[m].length === 7; });
    return whole.length ? sum(by[whole[whole.length - 1]]) : null;
  }

  // The mean of the days that have a count among the 7 ending at each day, kept where at least TREND_DAYS have one:
  // a map from each day's time to its mean.
  function trendMeans(dates, values) {
    var counted = {};
    dates.forEach(function (d, i) { if (num(values[i])) { counted[time(d)] = values[i]; } });
    return function (t) {
      var n = 0, total = 0;
      for (var k = 0; k < 7; k++) {
        if (counted[t - k * DAY] !== undefined) { n++; total += counted[t - k * DAY]; }
      }
      return n >= TREND_DAYS ? total / n : null;
    };
  }

  function caption(section, field) {
    if (field.chart) { return ""; }  // its legend gives each line's newest value
    var values = field.values.filter(num);
    if (field.kind === "daily") {
      var week = field.values.slice(-7), whole = lastWeek(section.dates, field.values);
      var parts = week.length === 7 && week.every(num) ? [show(sum(week)) + " in 7d"] : [];
      if (whole !== null) { parts.push(show(whole) + " last whole week"); }
      return parts.join(" \\u00b7 ");
    }
    if (field.kind === "breakdown") {  // the newest total of the fields it caps
      var capped = section.fields.filter(function (f) { return f.into === field.field; })[0];
      var newest = capped ? capped.values.filter(num) : [];
      return newest.length ? show(newest[newest.length - 1]) : "";
    }
    return values.length ? show(values[values.length - 1]) : "";
  }

  function chart(figure) {
    var section = data.sections[+figure.dataset.section], field = section.fields[+figure.dataset.field];
    var plot = figure.querySelector(".plot"), dates = section.dates;
    var breakdown = field.kind === "breakdown", daily = field.kind === "daily";
    var since = breakdown && !!field.since;  // days since each line's start, not the calendar: day n is the n-th
    if (since) { dates = field.other.map(function (_, n) { return new Date(n * DAY).toISOString().slice(0, 10); }); }
    var stop = since && dates.length ? time(dates[dates.length - 1]) : end;
    var lined = breakdown && !!field.lines, stack = breakdown && !lined;  // a breakdown is a stack, or lines
    var H = stack ? STACK : HEIGHT;
    var T = TOP;
    figure.querySelector(".now").textContent = caption(section, field);
    var from = days && !since ? end - (days - 1) * DAY : (dates.length ? time(dates[0]) : end);
    var tint = /^s[1-6]$/.test(field.color) ? "var(--" + field.color + ")" : null;  // a theme token, or the default
    var means = field.trend ? trendMeans(dates, field.values) : null;
    var group = !!field.chart, multi = group || lined;  // several lines, each with a color and a key
    var layers = breakdown ? bands(field) : group ? lineLayers(section, field) : [{ values: field.values }];
    if (lined) {  // a band that never has a count is no line
      layers = layers.filter(function (b) { return b.values.some(function (v) { return v > 0; }); });
    }
    // The total a stack adds up to (the field it caps): a row with it and no bands, the days before the bands
    // existed, is on the chart as the total alone.
    var cap = stack ? section.fields.filter(function (f) { return f.into === field.field; })[0] : null;
    var total = function (i) { return cap && num(cap.values[i]); };
    var present = [];
    dates.forEach(function (d, i) {
      if (time(d) >= from && (total(i) || layers.some(function (b) { return num(b.values[i]); }))) { present.push(i); }
    });
    if (!present.length) {
      plot.textContent = "No rows in this range.";
      return;
    }
    var left = Math.min(from, time(dates[present[0]])), span = Math.max(stop - left, DAY);
    // The notes on a chart of lines that fall inside what it draws; they take room above the plot.
    var marks = (daily || stack || since ? [] : section.notes || []).filter(function (note) {
      var t = time(note.date);
      return t >= left && t <= stop;
    });
    if (marks.length) { T = TOP + NOTE; }
    var W = Math.max(plot.clientWidth, 240), R = 10, pw = W - L - R, ph = H - T - B;
    function xAt(t) { return L + (t - left) / span * pw; }
    function x(i) { return xAt(time(dates[i])); }
    var totals = {};
    function banded(i) { return sum(layers.map(function (b) { return num(b.values[i]) ? b.values[i] : 0; })); }
    present.forEach(function (i) {
      totals[i] = total(i) ? Math.max(cap.values[i], banded(i)) : banded(i);
    });
    var vals = stack ? present.map(function (i) { return totals[i]; }) : [];
    if (!stack) {
      layers.forEach(function (layer) {
        present.forEach(function (i) { if (num(layer.values[i])) { vals.push(layer.values[i]); } });
      });
    }
    var zero = daily || stack || layers.some(function (layer) { return layer.band; });  // a band is read from zero
    var axis = fit(zero ? Math.min(0, Math.min.apply(null, vals)) : Math.min.apply(null, vals),
      zero ? Math.max(0, Math.max.apply(null, vals)) : Math.max.apply(null, vals));
    var lo = axis[0], hi = axis[1];
    function y(v) { return T + ph - (v - lo) / (hi - lo) * ph; }
    if (stack) {  // the band names at the right edge need room: as much as the longest takes, at most 40% of the width
      var newest = present[present.length - 1], used = 0, longest = 0;
      layers.forEach(function (band) {
        var v = num(band.values[newest]) ? band.values[newest] : 0;
        if (v > 0 && y(used) - y(used + v) >= 10) { longest = Math.max(longest, tag(band, v).length); }
        used += v;
      });
      R = Math.min(Math.max(LABELS, Math.ceil(longest * CHAR) + 12), Math.round(0.4 * W));
      pw = W - L - R;
    }
    var svg = [];
    for (var k = 0; k <= 4; k++) {
      var g = lo + (hi - lo) * k / 4;
      svg.push('<line x1="' + L + '" x2="' + (W - R) + '" y1="' + y(g) + '" y2="' + y(g) + '" stroke="var(--grid)"/>');
      svg.push('<text x="' + (L - 6) + '" y="' + (y(g) + 4) + '" text-anchor="end">' +
        compact.format(g) + "</text>");
    }
    var steps = Math.max(1, Math.min(4, Math.floor(pw / 80)));  // a date label about every 80px
    for (k = 0; k <= steps; k++) {
      var anchor = k === 0 ? "start" : k === steps ? "end" : "middle";
      svg.push('<text x="' + (L + pw * k / steps) + '" y="' + (H - 6) + '" text-anchor="' + anchor + '">' +
        (since ? "day " + Math.round((left + span * k / steps) / DAY)
          : day(left + span * k / steps, span > 200 * DAY)) + "</text>");
    }
    if (stack) {
      var rows = present.filter(function (i) { return layers.some(function (b) { return num(b.values[i]); }); });
      var below = rows.map(function () { return 0; });
      var drawn = layers.filter(function (b) { return rows.some(function (i) { return b.values[i] > 0; }); });
      drawn.forEach(function (band) {
        var top = rows.map(function (i, r) {
          var v = num(band.values[i]) ? band.values[i] : 0;
          return below[r] + v;
        });
        var up = rows.map(function (i, r) { return x(i) + "," + y(top[r]); });
        var down = rows.map(function (i, r) { return x(i) + "," + y(below[r]); }).reverse();
        if (rows.length === 1) {
          up.push((x(rows[0]) + 3) + "," + y(top[0]));
          down.unshift((x(rows[0]) + 3) + "," + y(below[0]));
        }
        svg.push('<polygon points="' + up.concat(down).join(" ") + '" fill="' + band.color +
          '" stroke="var(--panel)" stroke-width="0.8" stroke-linejoin="round"/>');
        var last = rows.length - 1, part = top[last] - below[last];
        if (y(below[last]) - y(top[last]) >= 10) {  // thick enough to name
          var strong = band.strong ? ' style="fill:var(--ink);font-weight:500"' : "";
          svg.push('<text x="' + (W - R + 6) + '" y="' + (y((top[last] + below[last]) / 2) + 4) + '"' + strong + ">" +
            esc(fitted(band, part, R - 8)) + "</text>");
        }
        below = top;
      });
      var outline = present.filter(total);  // the total is the stack's top edge, a line over the bands
      if (outline.length) {
        var last = outline[outline.length - 1];
        svg.push('<polyline points="' + outline.map(function (i) { return x(i) + "," + y(cap.values[i]); }).join(" ") +
          '" fill="none" stroke="var(--ink)" stroke-width="1.6" stroke-linejoin="round"/>');
        svg.push('<circle cx="' + x(last) + '" cy="' + y(cap.values[last]) + '" r="3" fill="var(--ink)"/>');
      }
    } else if (daily) {
      var bw = Math.max(1, pw * DAY / span - 1);
      present.forEach(function (i) {
        svg.push('<rect x="' + (x(i) - bw / 2) + '" y="' + y(Math.max(field.values[i], 0)) + '" width="' + bw +
          '" height="' + Math.abs(y(0) - y(field.values[i])) + '" fill="' + (tint || "var(--bar)") + '"/>');
      });
      if (means) {  // the trend: a faint line over the days, unbroken through those with no count, where its mean is
        var first = time(dates[present[0]]), final = time(dates[present[present.length - 1]]), run = [];
        var flush = function () {
          if (run.length > 1) {
            svg.push('<polyline points="' + run.join(" ") + '" fill="none" stroke="var(--ink)" stroke-opacity="0.55" ' +
              'stroke-width="1.8" stroke-linejoin="round"/>');
          }
          run = [];
        };
        for (var t = first; t <= final; t += DAY) {
          var mean = means(t);
          if (mean === null) { flush(); } else { run.push(xAt(t) + "," + y(mean)); }
        }
        flush();
      }
    } else {
      // Readings on consecutive days are a solid line over a fill; a run of one, a dot. Two runs with more than a
      // day between them are joined by a dashed line over the same fill, which is not a reading. The lines of a
      // chart each have a color and no fill. A band is the area between a line and the line it names, in the chart's
      // first color, behind the lines, over the runs of consecutive days where both have a number.
      layers.forEach(function (layer) {
        if (!layer.band) { return; }
        var both = present.filter(function (i) { return num(layer.values[i]) && num(layer.band[i]); });
        runs(both.map(function (i) { return time(dates[i]); })).forEach(function (run) {
          if (run.length < 2) { return; }
          var rows = run.map(function (n) { return both[n]; });
          var upper = rows.map(function (i) { return x(i) + "," + y(layer.band[i]); });
          var lower = rows.map(function (i) { return x(i) + "," + y(layer.values[i]); }).reverse();
          svg.push('<polygon points="' + upper.concat(lower).join(" ") + '" fill="' + layers[0].color +
            '" fill-opacity="0.18"/>');
        });
      });
      layers.forEach(function (layer) {
        var color = multi ? layer.color : (tint || "var(--accent)");
        var fill = function (points) {  // a breakdown's lines have areas, the newest in front; a chart's lines none
          if (lined) {
            svg.push('<polygon points="' + points + '" fill="' + color + '" fill-opacity="0.4"/>');
          } else if (!multi) {
            svg.push('<polygon points="' + points + '" ' +
              (tint ? 'fill="' + tint + '" fill-opacity="0.14"' : 'fill="var(--accent-soft)"') + "/>");
          }
        };
        var own = present.filter(function (i) { return num(layer.values[i]); });
        if (!own.length) { return; }
        var segments = runs(own.map(function (i) { return time(dates[i]); })).map(function (run) {
          return run.map(function (n) { return own[n]; });
        });
        var point = function (i) { return x(i) + "," + y(layer.values[i]); };
        var dot = function (i, r) {
          svg.push('<circle cx="' + x(i) + '" cy="' + y(layer.values[i]) + '" r="' + r + '" fill="' + color + '"/>');
        };
        segments.forEach(function (seg, n) {
          if (n) {
            var prev = segments[n - 1][segments[n - 1].length - 1];
            fill(x(prev) + "," + y(lo) + " " + point(prev) + " " + point(seg[0]) + " " + x(seg[0]) + "," + y(lo));
            svg.push('<polyline points="' + point(prev) + " " + point(seg[0]) + '" fill="none" stroke="' + color +
              '" stroke-width="1.4" stroke-dasharray="4 3" stroke-opacity="0.6"/>');
          }
          if (seg.length === 1) {
            dot(seg[0], 2.2);
            return;
          }
          var pts = seg.map(point);
          fill(x(seg[0]) + "," + y(lo) + " " + pts.join(" ") + " " + x(seg[seg.length - 1]) + "," + y(lo));
          svg.push('<polyline points="' + pts.join(" ") + '" fill="none" stroke="' + color + '" stroke-width="1.8" ' +
            'stroke-linejoin="round"/>');
        });
        dot(own[own.length - 1], 3);
      });
    }
    marks.forEach(function (note) {  // a dashed line at the date, its text above the plot, at the line's side that fits
      var at = xAt(time(note.date)), flip = at + 3 + note.text.length * CHAR > W - R + 4;
      svg.push('<line class="note" x1="' + at + '" x2="' + at + '" y1="' + T + '" y2="' + (T + ph) +
        '" stroke="var(--muted)" stroke-dasharray="2 3"/>');
      svg.push('<text class="note" x="' + (flip ? at - 3 : at + 3) + '" y="' + (T - 5) + '" text-anchor="' +
        (flip ? "end" : "start") + '" style="fill:var(--muted)">' + esc(note.text) + "</text>");
    });
    svg.push('<line class="cross" y1="' + T + '" y2="' + (T + ph) + '" stroke="var(--muted)" ' +
      'stroke-dasharray="3 3" visibility="hidden"/>');
    svg.push('<rect class="hit" x="' + L + '" y="' + T + '" width="' + pw + '" height="' + ph +
      '" fill="transparent"/>');
    var legend = !multi ? "" : '<div class="legend">' + layers.map(function (layer) {
      var seen = present.map(function (i) { return layer.values[i]; }).filter(num);
      return '<span class="key"><i style="background:' + layer.color + '"></i>' + esc(layer.name) +
        (seen.length ? " " + show(seen[seen.length - 1]) : "") + "</span>";
    }).join("") + "</div>";
    plot.innerHTML = legend + '<svg viewBox="0 0 ' + W + " " + H + '" width="' + W + '" height="' + H +
      '" role="img" aria-label="' + esc(field.chart || field.label) + '">' +
      svg.join("") + '</svg><div class="tip" hidden></div>';

    function name(i) { return since ? "Day " + i : dates[i]; }
    var box = plot.querySelector("svg"), cross = plot.querySelector(".cross"), tip = plot.querySelector(".tip");
    function lines(i) {
      if (stack) {
        // a band with none is not listed
        var listed = layers.slice().reverse().filter(function (b) { return b.values[i] > 0; });
        var capped = section.fields.filter(function (f) { return f.into === field.field && num(f.values[i]); });
        var counts = capped.map(function (f) { return f.label + "  " + show(f.values[i]); });
        return [name(i)].concat(listed.map(function (b) {
          var share = totals[i] ? " (" + Math.round(100 * b.values[i] / totals[i]) + "%)" : "";
          return b.name + "  " + show(b.values[i]) + share;
        }), counts.length ? counts : ["Total  " + show(totals[i])]);
      }
      // The other fields a chart's fields list on hover, once each, those that have a number that night.
      function tipped(paths) {
        return paths.filter(function (path, n) { return paths.indexOf(path) === n; }).map(function (path) {
          return named(section, path);
        }).filter(function (f) { return f && num(f.values[i]); }).map(function (f) {
          return f.label + "  " + formatted(f.values[i], f.format);
        });
      }
      if (multi) {
        var listed = layers.filter(function (l) { return num(l.values[i]); }).map(function (l) {
          return l.name + "  " + show(l.values[i]);
        });
        return [name(i)].concat(listed, tipped(layers.reduce(function (a, l) { return a.concat(l.tip || []); }, [])));
      }
      var text = show(field.values[i]);
      if (daily) {
        var week = field.values.slice(Math.max(0, i - 6), i + 1), mean = field.trend ? means(time(dates[i])) : null;
        text += " that day" + (week.length === 7 && week.every(num) ? " \\u00b7 " + show(sum(week)) + " in 7d" : "") +
          (mean === null ? "" : " \\u00b7 7d avg " + show(Math.round(mean * 10) / 10));
      }
      return [name(i), text].concat(tipped(field.tip || []));
    }
    function hover(event) {
      var r = box.getBoundingClientRect(), px = (event.clientX - r.left) * W / r.width;
      var i = present.reduce(function (a, b) { return Math.abs(x(b) - px) < Math.abs(x(a) - px) ? b : a; });
      cross.setAttribute("x1", x(i));
      cross.setAttribute("x2", x(i));
      cross.setAttribute("visibility", "visible");
      tip.replaceChildren();
      lines(i).forEach(function (text) {
        var line = document.createElement("div");
        line.textContent = text;
        tip.appendChild(line);
      });
      tip.hidden = false;
      // Always whole on the screen: centered on the row, kept between the screen's sides; above the chart, or, where
      // that would leave the top of the screen, down inside it (the screen's top edge is the chart's top, less pr.top).
      var pr = plot.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight, edge = 4;
      var mid = x(i) / W * r.width;
      tip.style.left = Math.max(edge - pr.left, Math.min(window.innerWidth - pr.left - w - edge, mid - w / 2)) + "px";
      tip.style.top = (pr.top - h - edge < edge ? Math.max(edge, edge - pr.top) : -h - edge) + "px";
    }
    var hit = plot.querySelector(".hit");
    hit.addEventListener("pointermove", hover);
    hit.addEventListener("pointerdown", hover);
    hit.addEventListener("pointerleave", function () {
      cross.setAttribute("visibility", "hidden");
      tip.hidden = true;
    });
  }

  function draw() { document.querySelectorAll("figure.chart").forEach(chart); }
  var width = window.innerWidth;
  window.addEventListener("resize", function () {
    if (window.innerWidth !== width) {  // a phone's scrolling changes only the height
      width = window.innerWidth;
      draw();
    }
  });

  document.querySelectorAll(".ranges button").forEach(function (button) {
    button.addEventListener("click", function () {
      days = +button.dataset.days;
      document.querySelectorAll(".ranges button").forEach(function (b) {
        b.setAttribute("aria-pressed", String(b === button));
      });
      draw();
    });
  });
  draw();
})();
"""
