"""The run's settings: a JSON file, with flags overriding its top-level scalars.

Credentials are never settings; sources read them from the environment.
"""

from __future__ import annotations

import hashlib
import json
import re
import string
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Sequence

from leadline import __version__, places

# The top-level scalar settings: key -> (flag that overrides it, help).
SETTINGS = {
    "out_dir": ("--out", "directory for every output file; relative to the config file when set there"),
    "user_agent": ("--user-agent", "User-Agent header for every request"),
}


# The sections of the top-level `shared` setting: named tables, each defined once, which an entry
# uses by naming one under the section's key (`"regions": "<name>"`).
SHARED = ("regions",)
# A region table's kinds of keyword rule: `text`, searched in issue text, profile locations and answers, and
# `location`, searched in profile locations and answers only, after the `text` rules (find_region); and `models`,
# searched in the modem a person reports only (model_rule); and `zones`, searched in the value read from a file a
# person attached only (zone_rule).
REGION_RULES = ("text", "location", "models", "zones")
# A region table's `exact` rules, each a region and the whole answers, lowercase, that mean it, compared with an
# answer only, before its other rules (answer_rule).
REGION_EXACT = "exact"
# A region table's optional `answer` and `model_answer`: regexes whose one group captures a person's answer in an
# issue body, their provider and their modem, matched ignoring case, `^` and `$` at each line (read_form).
REGION_ANSWER, REGION_MODEL = "answer", "model_answer"
# A region table's optional `attachment` and `attachment_value`: regexes whose one group captures the URL of a file
# attached in an issue body, and a value in that file's text (find_attachment, find_value). With `zones` rules they
# go together or not at all.
REGION_ATTACHMENT, REGION_VALUE, REGION_ZONES = "attachment", "attachment_value", "zones"
# A region table's optional `known`: person login -> a region code the table's rules give, for people the
# maintainer knows (known_region).
REGION_KNOWN = "known"
# What each regex of a region table captures, for its error message.
REGEX_CAPTURES = {
    REGION_ANSWER: "the answer",
    REGION_MODEL: "the answer",
    REGION_ATTACHMENT: "the file's URL",
    REGION_VALUE: "the value",
}
ANSWER_FLAGS = re.IGNORECASE | re.MULTILINE


# How much of the config file's SHA-256 `config_hash` keeps: as much as the script keeps of its own.
HASH_LENGTH = 12
HEALTH_FILE = "health.jsonl"  # the file the run's health row goes to (health.py); no source writes it


class ConfigError(Exception):
    """The config (file, flags or source names) is unusable; the run doesn't start."""


@dataclass(frozen=True)
class Config:
    out_dir: Path
    user_agent: str
    # Each enabled source's own settings, keyed by source name, in file order, with each shared
    # table an entry names in place of its name.
    sources: dict[str, Any]
    # The start of the config file's SHA-256, which health.jsonl records as `script_hash`: a synced
    # edit changes it. None for a config not read from a file.
    config_hash: str | None = None
    # The report blocks, as the file gives them, which the runner validates (leadline.reports); None when the
    # file has none, and the run writes the default summary.
    reports: Any = None
    # The trends pages' projects, as the file gives them, which the runner validates (leadline.trends); None when
    # the file has none.
    projects: Any = None
    # The trends pages, each a file, a title and its projects, as the file gives them, which the runner validates
    # (leadline.trends); None when the file has none, and the run writes one trends.html.
    pages: Any = None


def load(path: Path, flags: Mapping[str, str | None]) -> Config:
    """Read the config file at `path`; each flag that isn't None overrides its setting."""
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e.strerror}") from e
    except ValueError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a JSON object")
    unknown = sorted(set(data) - set(SETTINGS) - {"sources", "shared", "reports", "projects", "pages"})
    if unknown:
        raise ConfigError(f"unknown settings: {', '.join(unknown)}")

    settings = {key: _string(key, data[key]) for key in SETTINGS if key in data}
    if "out_dir" in settings:
        settings["out_dir"] = str(path.parent / settings["out_dir"])
    settings.update({key: _string(key, value) for key, value in flags.items() if value is not None})
    if "out_dir" not in settings:
        raise ConfigError(f"out_dir is required (in the config file or {SETTINGS['out_dir'][0]})")
    agent = settings.get("user_agent", "")
    if not (agent.isascii() and agent.isprintable()):  # a header value: urllib refuses a newline or non-ASCII
        raise ConfigError("user_agent must be printable ASCII")

    sources = data.get("sources", {})
    if not isinstance(sources, dict):
        raise ConfigError("sources must be a JSON object")
    shared = _shared(data.get("shared", {}))
    return Config(
        out_dir=Path(settings["out_dir"]),
        user_agent=settings.get("user_agent", f"leadline-metrics/{__version__}"),
        sources={
            name: [_resolved(f"{name}[{i}]", e, shared) for i, e in enumerate(cfg)]
            if isinstance(cfg, list)
            else _resolved(name, cfg, shared)
            for name, cfg in sources.items()
        },
        config_hash=hashlib.sha256(raw).hexdigest()[:HASH_LENGTH],
        reports=data.get("reports"),
        projects=data.get("projects"),
        pages=data.get("pages"),
    )


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """A JSON object, refusing a key given twice: JSON allows it and keeps the last, which would drop an entry."""
    found: dict[str, Any] = {}
    for key, value in pairs:
        if key in found:
            raise ValueError(f"duplicate key {key!r}")
        found[key] = value
    return found


def _string(key: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} must be a non-empty string")
    return value


def _shared(shared: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(shared, dict):
        raise ConfigError("shared must be a JSON object")
    unknown = sorted(set(shared) - set(SHARED))
    if unknown:
        raise ConfigError(f"unknown shared sections: {', '.join(unknown)}")
    for section, tables in shared.items():
        if not isinstance(tables, dict):
            raise ConfigError(f"shared.{section} must be a JSON object")
    for name, table in shared.get("regions", {}).items():
        _check_regions(f"shared.regions.{name}", table)
    return shared


def _check_regions(where: str, table: Any) -> None:
    """A region table: its rules, each a region and its lowercase keywords (or exact answers), and its regexes."""
    if not isinstance(table, dict):
        raise ConfigError(f"{where} must be a JSON object")
    unknown = sorted(set(table) - {*REGION_RULES, REGION_EXACT, REGION_KNOWN, *REGEX_CAPTURES})
    if unknown:
        raise ConfigError(f"{where}: unknown keys: {', '.join(unknown)}")
    for kind, value in table.items():
        if kind == REGION_KNOWN:
            continue  # checked below, against the regions the rules give
        if kind in REGEX_CAPTURES:
            _check_answer(f"{where}.{kind}", value, REGEX_CAPTURES[kind])
            continue
        if not isinstance(value, list):
            raise ConfigError(f"{where}.{kind} must be a list of rules")
        _check_rules(f"{where}.{kind}", kind, value)
    _check_settings(where, table)


def _check_rules(where: str, kind: str, rules: list[Any]) -> None:
    """The rules of one kind: each a region code and a non-empty list of lowercase keywords (or exact answers)."""
    key, compared = (
        ("answers", "the answer it is compared with")
        if kind == REGION_EXACT
        else ("keywords", "the text it is searched in")
    )
    for i, rule in enumerate(rules):
        if not _is_rule(rule, key):
            raise ConfigError(f'{where}[{i}] must be a region and its {key}: {{"region": ..., "{key}": [...]}}')
        if unknown := sorted(set(rule) - {"region", key}):
            raise ConfigError(f"{where}[{i}]: unknown keys: {', '.join(unknown)}")
        if not places.is_code(rule["region"]):
            raise ConfigError(f"{where}[{i}]: {_not_a_code(rule['region'])}")
        capitals = [word for word in rule[key] if word != word.lower()]
        if capitals:
            raise ConfigError(f"{where}[{i}].{key}: {json.dumps(capitals[0])} must be lowercase, as {compared} is")


def _not_a_code(region: str) -> str:
    return f"{region!r} is not a region code: use {places.CODE_HELP}"


def _check_settings(where: str, table: Mapping[str, Any]) -> None:
    """A region table's settings: `known`, and `attachment` and `attachment_value`, which go together, with the
    `zones` that read the value (core's zones fill the gaps, so a table needs none of its own)."""
    if REGION_KNOWN in table:
        _check_known(f"{where}.{REGION_KNOWN}", table[REGION_KNOWN])
    if (REGION_ATTACHMENT in table) != (REGION_VALUE in table):
        raise ConfigError(f"{where}: {REGION_ATTACHMENT} and {REGION_VALUE} go together")
    if REGION_ZONES in table and REGION_ATTACHMENT not in table:
        raise ConfigError(f"{where}: {REGION_ZONES} need {REGION_ATTACHMENT} and {REGION_VALUE}")


def _check_known(where: str, known: Any) -> None:
    """A region table's `known`: logins, each once ignoring case, and the region each is known to be in, a region
    code (core gives every country's rules, so a table needs no rule of its own for it)."""
    if not isinstance(known, dict):
        raise ConfigError(f"{where} must be an object of login to region")
    seen: set[str] = set()
    for login, region in known.items():
        if not login:
            raise ConfigError(f"{where}: a login must be a non-empty string")
        if login.lower() in seen:
            raise ConfigError(f"{where}: {login.lower()} is given twice")
        seen.add(login.lower())
        if not isinstance(region, str) or not region:
            raise ConfigError(f"{where}.{login} must be a non-empty region")
        if not places.is_code(region):
            raise ConfigError(f"{where}.{login}: {_not_a_code(region)}")


def _check_answer(where: str, answer: Any, captures: str) -> None:
    """A region table's regex: one with exactly one group, `captures` what it captures."""
    form = f"{where} must be a regex with one group, {captures}"
    if not isinstance(answer, str) or not answer:
        raise ConfigError(form)
    try:
        groups = re.compile(answer, ANSWER_FLAGS).groups
    except re.error as e:
        raise ConfigError(f"{where} is not a valid regex: {e}") from e
    if groups != 1:
        raise ConfigError(form)


def _is_rule(rule: Any, key: str) -> bool:
    """A region table's rule: a non-empty region, and a non-empty list of non-empty strings under `key`."""
    if not isinstance(rule, dict):
        return False
    region, words = rule.get("region"), rule.get(key)
    return (
        isinstance(region, str)
        and bool(region)
        and isinstance(words, list)
        and bool(words)
        and all(isinstance(word, str) and word for word in words)
    )


def find_region(table: Mapping[str, Any], text: str, *, location: bool = False) -> str | None:
    """The region of the first rule in `table` with a keyword in `text` (lowercased), or None.

    The `text` rules are searched, then, for a profile's `location`, the `location` rules.
    """
    found = find_rule(table, text, location=location)
    return found[0] if found else None


def find_rule(table: Mapping[str, Any], text: str, *, location: bool = False) -> tuple[str, str] | None:
    """The region of the first rule in `table` with a keyword in `text`, and that keyword, or None (find_region)."""
    return _keyword_rule([*table.get("text", []), *(table.get("location", []) if location else [])], text)


def model_rule(table: Mapping[str, Any], model: str) -> tuple[str, str] | None:
    """The region of the first `models` rule in `table` with a keyword in `model`, and that keyword, or None."""
    return _keyword_rule(table.get("models", []), model)


def known_region(table: Mapping[str, Any], login: str) -> str | None:
    """The region `table`'s `known` gives a person's login, ignoring case, or None."""
    known = table.get(REGION_KNOWN, {})
    return next((region for name, region in known.items() if name.lower() == login.lower()), None)


def zone_rule(table: Mapping[str, Any], zone: str) -> tuple[str, str] | None:
    """The region of the first `zones` rule in `table` with a keyword in `zone`, and that keyword, or None."""
    return _keyword_rule(table.get(REGION_ZONES, []), zone)


def _keyword_rule(rules: Sequence[Mapping[str, Any]], text: str) -> tuple[str, str] | None:
    lowered = text.lower()
    for rule in rules:
        keyword = next((k for k in rule["keywords"] if places.in_text(k, rule.get("match"), lowered)), None)
        if keyword is not None:
            return rule["region"], keyword
    return None


def answer_rule(table: Mapping[str, Any], answer: str) -> tuple[str, str] | None:
    """The region an answer gives, and the exact answer or keyword that matched it, or None.

    The `exact` rules come first, each comparing the whole answer, stripped and lowercased; then the answer is
    matched as a profile's location is (find_rule with `location`), so "Example ISP (Example Country)" resolves by
    its country.
    """
    whole = answer.strip().lower()
    exact = next((rule for rule in table.get(REGION_EXACT, []) if whole in rule["answers"]), None)
    return (exact["region"], whole) if exact else find_rule(table, answer, location=True)


def find_answer(table: Mapping[str, Any], body: str) -> str | None:
    """What `table`'s `answer` regex captures first in `body`, stripped; None without one, with no match, or empty."""
    return _captured(table.get(REGION_ANSWER), body)


def find_model(table: Mapping[str, Any], body: str) -> str | None:
    """What `table`'s `model_answer` regex captures first in `body`, as find_answer."""
    return _captured(table.get(REGION_MODEL), body)


def find_attachment(table: Mapping[str, Any], body: str) -> str | None:
    """What `table`'s `attachment` regex captures first in `body`, a file's URL; None without one or with no match."""
    return _captured(table.get(REGION_ATTACHMENT), body)


def find_value(table: Mapping[str, Any], text: str) -> str | None:
    """What `table`'s `attachment_value` regex captures first in a file's `text`, as find_answer."""
    return _captured(table.get(REGION_VALUE), text)


def fingerprint(value: Any) -> str:
    """A short name for `value` (JSON data), which changes when it does: how a row remembers the rules it read under."""
    return hashlib.sha1(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()[:12]  # noqa: S324 (a name)


def _captured(regex: str | None, body: str) -> str | None:
    match = re.search(regex, body, ANSWER_FLAGS) if regex else None
    captured = (match.group(1) or "").strip() if match else ""
    return captured or None


class Form(NamedTuple):
    """A person's answers in an issue form: their provider (find_answer) and their modem (find_model), each None
    where the body has none. Only the region they give is ever kept, never the answers."""

    answer: str | None
    model: str | None


def read_form(table: Mapping[str, Any], body: str) -> Form:
    """The answers `table`'s regexes capture in an issue's `body`."""
    return Form(find_answer(table, body), find_model(table, body))


CLUE_ANSWER, CLUE_MODEL = "answer", "model"  # which clue of a form decided its region (form_clue)


def form_clue(table: Mapping[str, Any], forms: Iterable[tuple[str | None, str | None]]) -> tuple[str, str] | None:
    """The region of a person's forms (ADR: an answer in an issue form is the strongest region signal) and the clue
    that gave it: the first answer a rule matches (answer_rule), else the first modem a `models` rule matches
    (model_rule), else None."""
    forms = list(forms)
    answers = [answer_rule(table, answer) for answer, _ in forms if answer]
    models = [model_rule(table, model) for _, model in forms if model]
    found = [
        (rule[0], clue) for clue, rules in ((CLUE_ANSWER, answers), (CLUE_MODEL, models)) for rule in rules if rule
    ]
    return found[0] if found else None


def form_region(table: Mapping[str, Any], forms: Iterable[tuple[str | None, str | None]]) -> str | None:
    """The region of a person's forms, as form_clue gives it, without the clue."""
    found = form_clue(table, forms)
    return found[0] if found else None


def _resolved(where: str, entry: Any, shared: Mapping[str, Mapping[str, Any]]) -> Any:
    """`entry` with each shared table it names in place of the name; a source sees only its own entry."""
    if not isinstance(entry, dict):
        return entry  # the source's validate() says what's wrong with it
    resolved = dict(entry)
    for section in SHARED:
        if section in entry:
            name, tables = entry[section], shared.get(section, {})
            if not isinstance(name, str) or name not in tables:
                raise ConfigError(f"sources.{where}.{section}: {json.dumps(name)} names no table in shared.{section}")
            resolved[section] = _with_core(tables[name]) if section == "regions" else tables[name]
    return resolved


def _with_core(table: Mapping[str, Any]) -> dict[str, Any]:
    """A region table with core's rules after its own (leadline.places): `location` rules, and `zones` where the
    table reads attachments. Its regions are the project's codes (GB is UK), so one country is one region in the
    rows. The table in the config is not changed."""
    merged = {**table, **_canonical(table)}
    merged["location"] = [*merged.get("location", []), *places.location_rules()]
    if REGION_ATTACHMENT in table:
        merged[REGION_ZONES] = [*merged.get(REGION_ZONES, []), *places.zone_rules()]
    return merged


def _canonical(table: Mapping[str, Any]) -> dict[str, Any]:
    """The table's rules and `known` regions with each region as places.canonical gives it."""
    found: dict[str, Any] = {
        kind: [{**rule, "region": places.canonical(rule["region"])} for rule in table[kind]]
        for kind in (*REGION_RULES, REGION_EXACT)
        if kind in table
    }
    if REGION_KNOWN in table:
        found[REGION_KNOWN] = {login: places.canonical(region) for login, region in table[REGION_KNOWN].items()}
    return found


# The mark of a row written for a past day that had none, from history: `fill`'s, cloudflare_web's and pypi's.
# It is here, not in store, for a spec that names the keys a row has besides its counts.
BACKFILLED = "backfilled"


def is_count(value: Any) -> bool:
    """A count: a whole number of nothing or more, never a boolean (which Python counts as one)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def default_file(source: str, key: str | None = None) -> str:
    """A source's default file template: its name with dashes, then `{key}`, the entry's id field, if any."""
    stem = source.replace("_", "-")
    return f"{stem}-{{{key}}}.jsonl" if key else f"{stem}.jsonl"


def file_name(entry: Mapping[str, Any], default: str, fields: Mapping[str, Any]) -> str:
    """The file an entry writes: its `file` template, or the source's `default`, filled from `fields`.

    `fields` are the entry's values a template may use. Raises ConfigError, its message starting
    `file:`, unless the template uses only those and fills to a plain `*.jsonl` name.
    """
    template = entry.get("file", default)
    if not isinstance(template, str):
        raise ConfigError(f"file: must be a string, not {json.dumps(template)}")
    try:
        used = [field for _, field, _, _ in string.Formatter().parse(template) if field is not None]
        unknown = [field for field in used if field not in fields]
        if unknown:
            known = ", ".join(sorted(fields)) or "none"
            raise ConfigError(f"file: {template!r} uses {{{unknown[0]}}}, which is not one of: {known}")
        name = template.format(**fields)
    except (ValueError, LookupError) as e:  # malformed, or a field hidden in a format spec
        raise ConfigError(f"file: {template!r} is not a valid template: {e}") from e
    if not plain_jsonl(name):
        raise ConfigError(f"file: {template!r} must fill to a plain *.jsonl name, not {name!r}")
    if name.casefold() == HEALTH_FILE.casefold():
        raise ConfigError(f"file: {name!r} is the run's health file, which a source's rows would be mixed into")
    return name


def plain_jsonl(name: Any) -> bool:
    """Whether `name` is a plain `*.jsonl` file name: a row file in the output folder, not a path."""
    return plain_file(name, ".jsonl")


def plain_file(name: Any, suffix: str) -> bool:
    """Whether `name` is a plain file name ending in `suffix`: a file in the output folder, not a path."""
    return isinstance(name, str) and name.endswith(suffix) and name != suffix and not {"/", "\\"} & set(name)


def unknown_keys(entry: Mapping[str, Any], keys: Iterable[str]) -> list[str]:
    """A problem, `key: unknown key (...)`, for each key of `entry` that is not one of `keys` or `file`, which
    every entry may have."""
    allowed = sorted({*keys, "file"})
    return [f"{key}: unknown key (the keys are {', '.join(allowed)})" for key in entry if key not in allowed]


def unknown_nested(where: str, nested: Mapping[str, Any], keys: Iterable[str]) -> list[str]:
    """A problem, `where.key: not one of: ...`, for each key of the object `nested` that is not one of `keys`."""
    allowed = tuple(keys)
    return [f"{where}.{key}: not one of: {', '.join(allowed)}" for key in nested if key not in allowed]


def entry_problems(
    cfg: Any,
    keys: Iterable[str],
    problems: Callable[[dict[str, Any]], list[str]],
    file: Callable[[dict[str, Any]], str],
) -> list[str]:
    """Every problem with a source's list of entries, each starting with its path (`[1].repo: `), for `validate`.

    `keys` are the entry keys the source reads, besides `file`; any other is a problem. `problems` gives what
    is wrong with an entry's own keys, each as `key: problem`, in the source's words. An entry with none has
    its file checked too: `file`, the source's `file_name` call, raising ConfigError.
    """
    if not isinstance(cfg, list):
        return [": must be a list of entries"]
    found = []
    for i, entry in enumerate(cfg):
        if not isinstance(entry, dict):
            found.append(f"[{i}]: must be an object")
            continue
        own = unknown_keys(entry, keys) + problems(entry)
        if not own:
            try:
                file(entry)
            except ConfigError as e:
                own = [str(e)]
        found += [f"[{i}].{problem}" for problem in own]
    return found


def entry_files(cfg: list[dict[str, Any]], file: Callable[[dict[str, Any]], str]) -> dict[str, str]:
    """Each entry's file, keyed by the entry's path (`[1]`), as `entry_problems` names it, for `files`."""
    return {f"[{i}]": file(entry) for i, entry in enumerate(cfg)}


# ---- The default summary -------------------------------------------------------------------------
# Each source's summary() names its sections of the default summary (docs/REPORTS.md, "The default summary"):
# each entry's file, and the fields it shows, each with its label and kind. `section_lines` renders a section
# as report lines; trends (docs/TRENDS.md) charts the same fields, and the section's `charts` besides.

SUMMARY_KINDS = ("snapshot", "change", "daily", "value", "breakdown")
DAILY_WINDOWS = (7, 30)  # the rows a daily count is totalled over
BREAKDOWN_TOP = 5  # the keys a breakdown shows; the rest are added up as one
CHART_KINDS = ("snapshot", "change")  # the kinds that may share a chart as lines with `chart`
COMPLEMENT_KINDS = ("snapshot", "change", "value")  # the kinds a summary may show as their complement
BANDS = ("line", "release")  # how a breakdown of versions bands them: by release line, or every exact release
MINUS_KINDS = ("snapshot", "change", "value")  # the kinds a chart-only difference (`minus`) may be of
NOTES_MAX = 10  # the notes an entry may carry
NOTE_TEXT_MAX = 60  # the characters of a note's text
COLORS = ("s1", "s2", "s3", "s4", "s5", "s6")  # the theme tokens (--s1 ...) a field may be drawn in
VERSION = re.compile(r"\d+(\.\d+)*(-[0-9A-Za-z.-]+)?")  # a version, as a breakdown's keys may be: 1.2.0, 2.0.0-beta.1


class Shown(NamedTuple):
    """A field a summary shows: its dotted path in a row, its label, and its kind:

    - `snapshot`, a count as it stands: its value, and its change since each of `compare` back;
    - `change`: only those changes;
    - `daily`, a count a row a day: its total over the newest 7 and 30 rows, each with its change since the
      rows before, once they are all there;
    - `value`: the value alone;
    - `breakdown`, an object of counts (`{"DE": 40}`): the newest counts, the largest BREAKDOWN_TOP first, then
      the rest added up.

    `format` is a format spec for its numbers (`.1f`). `complement` shows 100 less the value (`complement()`), a
    percent read the other way round: a rank's percentile, lower better, as the share ranked below it. Only the
    COMPLEMENT_KINDS take it.

    Fifteen settings shape trends' charts only; the summary and the rows are the same. `charted=False` leaves a field
    off the pages. `into` names a `breakdown` of the same summary whose counts add up to this field (or a part of
    them): the field is not charted alone, but beside that stack, which it caps. `neutral` lists a breakdown's keys
    that mean none of the others (`unknown`): each is a band of its own, kept whatever its rank.
    `bands` (one of BANDS) says how a breakdown of versions is banded: `line`, the default,
    by release line (3.14), the newest BREAKDOWN_TOP of them; `release`, every exact release (3.14.1).
    `by` totals a list into a `breakdown`: `field` is then a path through lists (`releases[].assets[].downloads`),
    and `by` the key that names each item of the first (`tag`); a key's count is the sum of the numbers at the path
    in its item, at its greatest so far, so an item that leaves the list or drops keeps its count (trends.py
    `_by_key`). A leading `v` before a digit is dropped from the key (`v1.2.0` is `1.2.0`), so tags band as versions.
    `lines` draws a breakdown as a line for each band on one axis, not stacked (no sum across bands): with
    versions banded by `release` only the newest BREAKDOWN_TOP releases are lines, the older ones added up as one;
    each line has a translucent area of its own under it, the newest in front. `since` (with `by` and `lines`, bands
    `release`) names the key of each item holding its start date (`published_at`): the chart's horizontal axis then
    counts days from each item's start, not the calendar, one line an item, and draws only items started on or after
    the file's first row (trends.py `_since`). `half` makes a breakdown's chart half the section's width, as a chart
    that is not a breakdown is, so two of them share a row where the page is wide enough and stack where it is not.
    `prereleases=False` leaves out the keys that are prereleases (`2.0.0-beta.1`) of a breakdown of versions: they
    are not drawn or added up.
    `chart` names a chart that draws every field of the section with that name as a line of its own, on one axis
    (a `snapshot` or `change` that is charted alone), instead of a chart for each. `trend` (a `daily` count only)
    draws the mean of the days that have a count in each 7 as a faint line over the bars, where at least 4 of the
    7 days have one. `color` (one of COLORS) draws the field's chart in that theme token, not the default.
    `minus` (a snapshot, change or value) makes the field a derived one, for the chart alone: its series is the value
    at `field` less the value at `minus` (a dotted path) in each row, None where either is missing or not a number.
    The default summary leaves such a field out. `band` (a field of the same `chart`, named by its path; of two fields
    with one path it is the one without `minus`) shades the area between this field's line and that field's line, on
    the dates where both have a number. `tip` (a snapshot or change) lists the paths of other fields of the section
    whose value on each night is added to the chart's hover, as its label and value, a field with `complement` as its
    complement and every one in its own `format`; a night where it has none leaves its line out.
    """

    field: str
    label: str
    kind: str = "snapshot"
    compare: tuple[str, ...] = ("7d", "30d")
    format: str = ""
    complement: bool = False
    charted: bool = True
    into: str = ""
    neutral: tuple[str, ...] = ()
    bands: str = "line"
    by: str = ""
    lines: bool = False
    prereleases: bool = True
    since: str = ""
    half: bool = False
    chart: str = ""
    trend: bool = False
    color: str = ""
    minus: str = ""
    band: str = ""
    tip: tuple[str, ...] = ()


def complement(value: Any) -> Any:
    """100 less `value`, to its own decimals (100 less 99.9 is 0.1, never float noise); None for what isn't a
    number. A percent's complement, as `Shown.complement` and the reports' `complement` read a field."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return 100 - value if isinstance(value, int) else float(Decimal(100) - Decimal(repr(value)))


class Hide(NamedTuple):
    """Items to leave out of a list in each row of a file when it is read for display (a source's `exclude`): the
    list at the dotted `field`, whose items are objects with a text `key` (`tag`); those whose key, without a
    leading `v` before a digit (`untagged`), is in `values` (already without it) are left out. The file is not
    changed."""

    field: str
    key: str
    values: frozenset[str]


def untagged(tag: str) -> str:
    """A tag as a version: without the `v` before its first digit (`v1.2.0` is `1.2.0`)."""
    return tag[1:] if tag[:1] in "vV" and tag[1:2].isdigit() else tag


class Card(NamedTuple):
    """The card a section is a row of in trends: the sections of one entry that name the same card `name` are one
    panel headed by it, each a row titled `row` with `about` under the title, saying what its charts show. The
    default summary ignores it."""

    name: str
    row: str
    about: str


class Note(NamedTuple):
    """A dated remark on a section's line charts (an entry's `notes`): trends marks `date` with a dashed vertical line
    and `text` above the plot, where the date falls inside the range drawn."""

    date: str
    text: str


def notes_problems(notes: Any) -> list[str]:
    """What is wrong with an entry's `notes` setting (`[{"date": "2026-10-22", "text": "HA changed the feed"}]`), each
    as `notes: problem`, for a source's own `validate`; pass `entry.get("notes", [])`."""
    if not isinstance(notes, list):
        return ["notes: must be a list of {date, text} objects"]
    if len(notes) > NOTES_MAX:
        return [f"notes: at most {NOTES_MAX} notes, not {len(notes)}"]
    found = []
    for i, note in enumerate(notes):
        if not isinstance(note, dict) or set(note) != {"date", "text"}:
            found.append(f"notes[{i}]: must be an object with date and text")
            continue
        if not _is_date(note["date"]):
            found.append(f"notes[{i}].date: must be a date, YYYY-MM-DD")
        text = note["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > NOTE_TEXT_MAX:
            found.append(f"notes[{i}].text: must be 1 to {NOTE_TEXT_MAX} characters")
    return found


def notes(entry: Mapping[str, Any]) -> tuple[Note, ...]:
    """An entry's valid `notes` as Notes, for a source's `summary` (`notes=config.notes(entry)`)."""
    return tuple(Note(note["date"], note["text"]) for note in entry.get("notes", ()))


def _is_date(value: Any) -> bool:
    try:
        return isinstance(value, str) and len(value) == 10 and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


class Section(NamedTuple):
    """A section of the default summary: the rows of `file` that `where` keeps, titled by the source and the
    entry's `name`, showing each of `shown`, then `lines`, report lines of the source's own. `label` is the source's
    readable name for it (`Issues`), which heads its panel in trends. `charts` are fields only trends draws, after
    those of `shown`: the default summary shows none of them. `hide` are the items left out of the file's lists
    wherever the section's file is read for display: its trends and every report that reads it (`hidden`). `card`
    makes it a row of a card in trends. `part` names a second section of one entry (`stars`), after the name in the
    title, so the entry's sections have titles of their own. `notes` are dated remarks trends marks on the section's
    line charts (`Note`); the default summary ignores them."""

    source: str
    name: str | None
    file: str
    shown: Sequence[Shown]
    label: str
    where: Mapping[str, Any] | None = None
    lines: Sequence[Mapping[str, Any]] = ()
    charts: Sequence[Shown] = ()
    hide: Sequence[Hide] = ()
    card: Card | None = None
    part: str = ""
    notes: tuple[Note, ...] = ()

    @property
    def title(self) -> str:
        """`<source>: <name>`, or the source alone for a section with no name, then a space and the `part`, if any."""
        base = self.source if self.name is None else f"{self.source}: {self.name}"
        return f"{base} {self.part}" if self.part else base


def hidden(sections: Sequence[Section]) -> dict[str, Sequence[Hide]]:
    """What to leave out of each file read for display: the `hide` of the sections that have any, by their file."""
    return {section.file: section.hide for section in sections if section.hide}


def entry_summary(
    source: str,
    cfg: list[dict[str, Any]],
    key: str,
    file: Callable[[dict[str, Any]], str],
    shown: Sequence[Shown],
    label: str,
    charts: Sequence[Shown] = (),
    card: Card | None = None,
) -> list[Section]:
    """Each entry's section, named by its `key`, showing `shown` of its `file` and labeled `label`, with the `charts`
    trends alone draws, and the `card` it is a row of there, for `summary`."""
    return [Section(source, entry[key], file(entry), shown, label, charts=charts, card=card) for entry in cfg]


def section_lines(section: Section) -> list[dict[str, Any]]:
    """The section as report lines: `### <title>` between blank lines, then a line for each field it shows, then its
    own lines. Each reads its file (the rows `where` keeps), so the section shows once the file has a good row; where
    its newer rows are failure rows, the heading ends with the date of the row shown."""
    reads: dict[str, Any] = {"from": section.file, **({"where": dict(section.where)} if section.where else {})}
    heading = {**reads, "field": "date", "text": "### " + _literal(section.title)}
    blank = {**reads, "field": "date", "text": "", "as_of": ""}
    fields = [_summary_line(reads, field) for field in section.shown if not field.minus]
    return [blank, heading, dict(blank), *fields, *(dict(line) for line in section.lines)]


def _summary_line(reads: Mapping[str, Any], shown: Shown) -> dict[str, Any]:
    """A line of `- <label>: ` and its parts joined with ` · `. Each part reads the file itself, so none takes a
    field it lacks (a change with no row that far back) from the line."""
    check_shown(shown)
    spec, field = shown.format, {**reads, "field": shown.field, "as_of": ""}
    if shown.complement:
        field["complement"] = True
    label = "- " + _literal(shown.label) + ": "
    if shown.kind == "breakdown":
        return {
            **field,
            "aggregate": "counts",
            "count": BREAKDOWN_TOP,
            "item": f"{{key}} {{count:{spec}}}",
            "join": " · ",
            "parts": [{"text": label + "{items}"}, {"text": f"other {{rest:{spec}}}", "when": [["rest", ">", 0]]}],
        }
    if shown.kind == "daily":
        parts = [
            {
                **field,
                "aggregate": "sum",
                "count": n,
                "parts": [{"text": f"{{total:{spec}}} in {n}d"}, {"text": f" ({{change:+{spec}}})"}],
            }
            for n in DAILY_WINDOWS
        ]
    else:
        parts = [{**field, "text": f"{{value:{spec}}}"}] if shown.kind in ("snapshot", "value") else []
        if shown.kind != "value":
            parts += [{**field, "compare": then, "text": f"{{change:+{spec}}} in {then}"} for then in shown.compare]
    return {"text": label + "{parts}", "join": " · ", "parts": parts}


def check_shown(shown: Shown) -> None:
    """Raise ValueError for a field no summary can show: a kind not in SUMMARY_KINDS, a complement of a kind
    not in COMPLEMENT_KINDS, or an `into`, `neutral`, `bands`, `by`, `chart` or `trend` on the wrong kind or with a bad
    value. A source's summary() is code, so each is a bug."""
    if shown.kind not in SUMMARY_KINDS:
        raise ValueError(f"{shown.field}: kind must be one of {', '.join(SUMMARY_KINDS)}, not {shown.kind!r}")
    if shown.complement and shown.kind not in COMPLEMENT_KINDS:
        kinds = f"{', '.join(COMPLEMENT_KINDS[:-1])} and {COMPLEMENT_KINDS[-1]}"
        raise ValueError(f"{shown.field}: complement: only {kinds} take it")
    if shown.kind == "breakdown" and shown.into:
        raise ValueError(f"{shown.field}: into: only a field that is not a breakdown")
    if shown.neutral and shown.kind != "breakdown":
        raise ValueError(f"{shown.field}: neutral: only a breakdown has keys")
    if bands_problem(shown.bands):
        raise ValueError(f"{shown.field}: bands: {bands_problem(shown.bands)}")
    if shown.bands != BANDS[0] and shown.kind != "breakdown":
        raise ValueError(f"{shown.field}: bands: only a breakdown has them")
    _check_by(shown)
    _check_lines(shown)
    _check_chart(shown)
    _check_derived(shown)


def _check_derived(shown: Shown) -> None:
    """Raise ValueError for a `minus`, `band` or `tip` that cannot be drawn: a minus that is no text, or on a kind
    not in MINUS_KINDS, or with `complement`, `into` or `by`; a band with no `chart` or naming its own field; a tip
    that is not a tuple of field paths or is on a kind that is not a chart's line."""
    _check_minus(shown)
    if shown.band:
        if not shown.chart:
            raise ValueError(f"{shown.field}: band: needs chart")
        if shown.band == shown.field and not shown.minus:
            raise ValueError(f"{shown.field}: band: names its own field")
    if not isinstance(shown.tip, (tuple, list)) or not all(isinstance(p, str) and p for p in shown.tip):
        raise ValueError(f"{shown.field}: tip: must be a tuple of field paths")
    if shown.tip and shown.kind not in CHART_KINDS:
        raise ValueError(f"{shown.field}: tip: only a snapshot or change takes it")


def _check_minus(shown: Shown) -> None:
    """Raise ValueError for a `minus` that is no text, or on a kind not in MINUS_KINDS, or with `complement`, `into`
    or `by`."""
    if not isinstance(shown.minus, str):
        raise ValueError(f"{shown.field}: minus: must be a non-empty string")
    if not shown.minus:
        return
    if shown.kind not in MINUS_KINDS:
        raise ValueError(f"{shown.field}: minus: only a snapshot, change or value takes it")
    for name, used in (("complement", shown.complement), ("into", shown.into), ("by", shown.by)):
        if used:
            raise ValueError(f"{shown.field}: minus: not with {name}")


def _check_lines(shown: Shown) -> None:
    """Raise ValueError for `lines`, `prereleases` or `since` on a kind that is not a breakdown, or a `since` without
    `by`, `lines` and `bands` release."""
    if shown.lines and shown.kind != "breakdown":
        raise ValueError(f"{shown.field}: lines: only a breakdown takes it")
    if shown.since and not (shown.by and shown.lines):
        raise ValueError(f"{shown.field}: since: needs by and lines")
    if shown.since and shown.bands != "release":
        raise ValueError(f"{shown.field}: since: needs bands release, one line a release")
    if shown.trend and shown.kind != "daily":
        raise ValueError(f"{shown.field}: trend: only a daily count takes it")
    if shown.color and shown.color not in COLORS:
        raise ValueError(f"{shown.field}: color: must be one of {', '.join(COLORS)}, not {shown.color!r}")
    if shown.half and shown.kind != "breakdown":
        raise ValueError(f"{shown.field}: half: only a breakdown takes it")
    if not shown.prereleases and shown.kind != "breakdown":
        raise ValueError(f"{shown.field}: prereleases: only a breakdown takes it")


def _check_chart(shown: Shown) -> None:
    """Raise ValueError for a `chart` on a kind that is not a line, or on a field the pages don't chart alone."""
    if not shown.chart:
        return
    if shown.kind not in CHART_KINDS:
        raise ValueError(f"{shown.field}: chart: only a snapshot or change takes it")
    if not shown.charted:
        raise ValueError(f"{shown.field}: chart: not with charted=False")
    if shown.into:
        raise ValueError(f"{shown.field}: chart: not with into")


def _check_by(shown: Shown) -> None:
    """Raise ValueError for a `by` that does not total a list into a breakdown: with no `[].` in the field or on
    another kind; or a field with `[].` and no `by`."""
    listed = "[]." in shown.field
    if listed and not shown.by:
        raise ValueError(f"{shown.field}: [] in the field: needs by")
    if not shown.by:
        return
    if not listed:
        raise ValueError(f"{shown.field}: by: needs a field with a list to total, like a[].n")
    if shown.kind != "breakdown":
        raise ValueError(f"{shown.field}: by: only a breakdown takes it")


def bands_problem(bands: Any) -> str | None:
    """What is wrong with a `bands` setting (`must be one of line, release, not 'x'`), or None."""
    if bands in BANDS:
        return None
    return f"must be one of {', '.join(BANDS)}, not {bands!r}"


def _literal(text: str) -> str:
    """`text` as a report template shows it: its braces text, not fields."""
    return text.replace("{", "{{").replace("}", "}}")


def version_key(version: str) -> tuple[Any, ...]:
    """A version's order: numbers as numbers, and a prerelease before its release (1.2.0-beta.10 < 1.2.0)."""
    base, _, tag = version.partition("-")
    return (_version_parts(base), 0 if tag else 1, _version_parts(tag))


def _version_parts(text: str) -> tuple[tuple[int, Any], ...]:
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in text.split(".") if part)
