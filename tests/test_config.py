from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

import leadline
from leadline import reports
from leadline.config import (
    SETTINGS,
    Card,
    ConfigError,
    Section,
    Shown,
    answer_rule,
    complement,
    default_file,
    entry_files,
    entry_problems,
    entry_summary,
    file_name,
    find_answer,
    find_attachment,
    find_model,
    find_region,
    find_rule,
    find_value,
    fingerprint,
    form_clue,
    form_region,
    is_count,
    known_region,
    load,
    model_rule,
    read_form,
    section_lines,
    zone_rule,
)
from leadline.context import Context
from leadline.http import Http

NO_FLAGS = dict.fromkeys(SETTINGS)


def without_core(table: Mapping[str, Any]) -> dict[str, Any]:
    """A loaded region table as the config wrote it: core's rules (the ones with a `match`) taken out."""
    kept = dict(table)
    for kind in ("location", "zones"):
        if kind in kept:
            kept[kind] = [rule for rule in kept[kind] if "match" not in rule]
            if not kept[kind]:
                del kept[kind]
    return kept


def write(tmp_path: Path, data: Any) -> Path:
    path = tmp_path / "leadline.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_the_hidden_items_of_each_file_come_from_its_sections() -> None:
    hide = (leadline.config.Hide("releases", "tag", frozenset({"1.0.0"})),)
    sections = [
        leadline.config.Section("s", "a", "a.jsonl", [], "L", hide=hide),
        leadline.config.Section("s", "b", "b.jsonl", [], "L"),
    ]

    assert leadline.config.hidden(sections) == {"a.jsonl": hide}
    assert [leadline.config.untagged(tag) for tag in ("v1.2.0", "V1.2.0", "1.2.0", "vault", "v")] == ["1.2.0"] * 3 + [
        "vault",
        "v",
    ]


def test_loads_settings_and_sources_in_file_order(tmp_path: Path) -> None:
    sources = {"pypi": {"packages": ["a"]}, "example_source": {}, "github_issues": [{"repo": "o/r"}]}
    elsewhere = tmp_path.parent / "metrics"  # absolute on every OS; "/data" has no drive on Windows
    path = write(tmp_path, {"out_dir": str(elsewhere), "user_agent": "Agent/1.0", "sources": sources})

    config = load(path, NO_FLAGS)

    assert config.out_dir == elsewhere
    assert config.user_agent == "Agent/1.0"
    assert list(config.sources) == ["pypi", "example_source", "github_issues"]
    assert config.sources["github_issues"] == [{"repo": "o/r"}]


def test_relative_out_dir_is_relative_to_the_config_file(tmp_path: Path) -> None:
    config = load(write(tmp_path, {"out_dir": "metrics"}), NO_FLAGS)

    assert config.out_dir == tmp_path / "metrics"


def test_defaults(tmp_path: Path) -> None:
    config = load(write(tmp_path, {"out_dir": "m"}), NO_FLAGS)

    assert config.user_agent == f"leadline-metrics/{leadline.__version__}"
    assert config.sources == {}
    assert config.reports is None  # no reports: the run writes the default summary


def test_an_empty_reports_list_is_kept_apart_from_none(tmp_path: Path) -> None:
    config = load(write(tmp_path, {"out_dir": "m", "reports": []}), NO_FLAGS)

    assert config.reports == []


def test_reports_are_kept_as_given_for_the_runner_to_validate(tmp_path: Path) -> None:
    reports = [{"file": "r.md", "block": "b"}, "not an object"]

    config = load(write(tmp_path, {"out_dir": "m", "reports": reports}), NO_FLAGS)

    assert config.reports == reports


def test_the_config_hash_is_the_start_of_the_files_sha256(tmp_path: Path) -> None:
    """health.jsonl's `script_hash`: a sync that changes the file changes it, as the script's hash of itself."""
    path = write(tmp_path, {"out_dir": "m"})

    config = load(path, NO_FLAGS)

    assert config.config_hash == hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    assert load(write(tmp_path, {"out_dir": "m", "sources": {}}), NO_FLAGS).config_hash != config.config_hash


def test_flags_override_the_file(tmp_path: Path) -> None:
    path = write(tmp_path, {"out_dir": "m", "user_agent": "File/1.0"})

    config = load(path, {"out_dir": "shadow", "user_agent": "Flag/1.0"})

    assert config.out_dir == Path("shadow")
    assert config.user_agent == "Flag/1.0"


def test_a_flag_can_supply_a_required_setting(tmp_path: Path) -> None:
    config = load(write(tmp_path, {}), {**NO_FLAGS, "out_dir": "/x"})

    assert config.out_dir == Path("/x")


def test_every_setting_has_a_flag() -> None:
    assert {key: flag for key, (flag, _) in SETTINGS.items()} == {"out_dir": "--out", "user_agent": "--user-agent"}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "must be a JSON object"),
        ({}, "out_dir is required"),
        ({"out_dir": ""}, "out_dir must be a non-empty string"),
        ({"out_dir": 5}, "out_dir must be a non-empty string"),
        ({"out_dir": "m", "user_agent": ["x"]}, "user_agent must be a non-empty string"),
        ({"out_dir": "m", "user_agent": "x\ny"}, "user_agent must be printable ASCII"),
        ({"out_dir": "m", "user_agent": "Café/1.0"}, "user_agent must be printable ASCII"),
        ({"out_dir": "m", "sources": []}, "sources must be a JSON object"),
        ({"out_dir": "m", "github_token": "x", "outdir": "y"}, "unknown settings: github_token, outdir"),
    ],
)
def test_invalid_config_is_rejected(tmp_path: Path, data: Any, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load(write(tmp_path, data), NO_FLAGS)


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load(tmp_path / "absent.json", NO_FLAGS)


def test_bad_json_is_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ConfigError, match="not valid JSON"):
        load(path, NO_FLAGS)


@pytest.mark.parametrize(
    "text",
    [
        '{"out_dir": "m", "out_dir": "n"}',
        '{"out_dir": "m", "sources": {"github_issues": [], "github_issues": []}}',
        '{"out_dir": "m", "shared": {"regions": {"eu": {"text": []}, "eu": {"text": []}}}}',
        '{"out_dir": "m", "sources": {"github_issues": [{"repo": "a/b", "repo": "c/d"}]}}',
    ],
    ids=["a setting", "a source", "a region table", "inside an entry"],
)
def test_a_key_given_twice_is_a_config_error(tmp_path: Path, text: str) -> None:
    path = tmp_path / "dup.json"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigError, match="duplicate key"):
        load(path, NO_FLAGS)


def test_a_user_agent_flag_is_checked_like_the_file_setting(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="user_agent must be printable ASCII"):
        load(write(tmp_path, {"out_dir": "m"}), {"user_agent": "x\ny"})


# --- projects: the trends page's groups of sections ---


def test_projects_are_kept_as_the_file_gives_them_for_trends_to_validate(tmp_path: Path) -> None:
    projects = [{"name": "App", "sections": ["a: x"]}]

    assert load(write(tmp_path, {"out_dir": "m", "projects": projects}), NO_FLAGS).projects == projects
    assert load(write(tmp_path, {"out_dir": "m"}), NO_FLAGS).projects is None


def test_pages_are_kept_as_the_file_gives_them_for_trends_to_validate(tmp_path: Path) -> None:
    pages = [{"file": "app.html", "title": "App", "projects": ["App"]}]

    assert load(write(tmp_path, {"out_dir": "m", "pages": pages}), NO_FLAGS).pages == pages
    assert load(write(tmp_path, {"out_dir": "m"}), NO_FLAGS).pages is None


# --- replay was a setting; it is gone ---


def test_a_config_that_still_sets_replay_is_an_unknown_setting(tmp_path: Path) -> None:
    replay = {"dir": "replay", "hosts": ["pypistats.org"]}

    with pytest.raises(ConfigError, match=r"^unknown settings: replay$"):
        load(write(tmp_path, {"out_dir": "m", "replay": replay}), NO_FLAGS)


# --- shared tables: defined once, named by the entries that use them ---

EU = {
    "text": [{"region": "XA", "keywords": ["example isp"]}],
    "location": [{"region": "XA", "keywords": ["example land"]}],
}
AMERICAS = {"text": [], "location": [{"region": "XB", "keywords": ["other land"]}]}


def test_an_entry_gets_the_shared_table_it_names_and_nothing_else(tmp_path: Path) -> None:
    data = {
        "out_dir": "m",
        "shared": {"regions": {"eu": EU, "americas": AMERICAS}},
        "sources": {
            "github_issues": [{"repo": "o/a", "regions": "eu"}, {"repo": "o/b"}],
            "github_contributor_locations": [{"repo": "o/a", "regions": "americas"}],
            "example_source": {"regions": "eu"},
        },
    }

    config = load(write(tmp_path, data), NO_FLAGS)

    got = config.sources
    assert {name: len(entries) if isinstance(entries, list) else None for name, entries in got.items()} == {
        "github_issues": 2,
        "github_contributor_locations": 1,
        "example_source": None,
    }
    assert got["github_issues"][1] == {"repo": "o/b"}
    # The table an entry names is the config's own with core's rules after (tests/test_places.py).
    for table, own in (
        (got["github_issues"][0]["regions"], EU),
        (got["github_contributor_locations"][0]["regions"], AMERICAS),
        (got["example_source"]["regions"], EU),
    ):
        assert table["text"] == own["text"]
        assert table["location"][: len(own["location"])] == own["location"]
    assert not hasattr(config, "shared")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"shared": []}, "shared must be a JSON object"),
        ({"shared": {"regions": {}, "labels": {}}}, "unknown shared sections: labels"),
        ({"shared": {"regions": []}}, "shared.regions must be a JSON object"),
        (
            {"shared": {"regions": {"eu": EU}}, "sources": {"github_issues": [{"repo": "o/a"}, {"regions": "asia"}]}},
            r'sources.github_issues\[1\].regions: "asia" names no table in shared.regions',
        ),
        (
            {"sources": {"github_issues": [{"regions": "eu"}]}},
            r'sources.github_issues\[0\].regions: "eu" names no table in shared.regions',
        ),
        (
            {"shared": {"regions": {"eu": EU}}, "sources": {"example_source": {"regions": EU}}},
            r"sources.example_source.regions: .* names no table in shared.regions",
        ),
    ],
    ids=[
        "shared not an object",
        "an unknown section",
        "a section not an object",
        "a name with no table",
        "no shared section at all",
        "a table given in place of its name",
    ],
)
def test_bad_shared_tables_and_references_are_rejected(tmp_path: Path, data: Any, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load(write(tmp_path, {"out_dir": "m", **data}), NO_FLAGS)


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ([], r"shared.regions.eu must be a JSON object"),
        ({"text": [], "places": []}, r"shared.regions.eu: unknown keys: places"),
        ({"text": {}}, r"shared.regions.eu.text must be a list of rules"),
        ({"location": [5]}, r"shared.regions.eu.location\[0\] must be a region and its keywords"),
        ({"text": [{"keywords": ["x"]}]}, r"shared.regions.eu.text\[0\] must be a region and its keywords"),
        ({"text": [{"region": "", "keywords": ["x"]}]}, r"shared.regions.eu.text\[0\] must be a region"),
        ({"text": [{"region": "XA", "keywords": []}]}, r"shared.regions.eu.text\[0\] must be a region"),
        ({"text": [{"region": "XA", "keywords": "x"}]}, r"shared.regions.eu.text\[0\] must be a region"),
        ({"text": [{"region": "XA", "keywords": [""]}]}, r"shared.regions.eu.text\[0\] must be a region"),
        (
            {"text": [{"region": "XA", "keywords": ["Example ISP"]}]},
            r"shared.regions.eu.text\[0\].keywords: \"Example ISP\" must be lowercase, as the text it is "
            "searched in is",
        ),
        ({"answer": 5}, r"shared.regions.eu.answer must be a regex with one group, the answer"),
        ({"answer": ""}, r"shared.regions.eu.answer must be a regex with one group, the answer"),
        ({"answer": "ISP: .+"}, r"shared.regions.eu.answer must be a regex with one group, the answer"),
        ({"answer": "(ISP|Provider): (.+)"}, r"shared.regions.eu.answer must be a regex with one group, the answer"),
        ({"answer": "ISP: (.+"}, r"shared.regions.eu.answer is not a valid regex: missing \), unterminated"),
        ({"model_answer": "Model: .+"}, r"shared.regions.eu.model_answer must be a regex with one group, the answer"),
        ({"model_answer": "Model: (.+"}, r"shared.regions.eu.model_answer is not a valid regex: missing \)"),
        ({"exact": {}}, r"shared.regions.eu.exact must be a list of rules"),
        ({"exact": [{"region": "XA", "keywords": ["ee"]}]}, r"shared.regions.eu.exact\[0\] must be a region and "
         r"its answers: \{\"region\": \.\.\., \"answers\": \[\.\.\.\]\}"),
        ({"exact": [{"region": "XA", "answers": []}]}, r"shared.regions.eu.exact\[0\] must be a region and its"),
        ({"exact": [{"region": "XA", "answers": ["EE"]}]},
         r"shared.regions.eu.exact\[0\].answers: \"EE\" must be lowercase, as the answer it is compared with is"),
        ({"models": [{"region": "XA", "answers": ["x"]}]}, r"shared.regions.eu.models\[0\] must be a region and its "
         "keywords"),
        ({"models": [{"region": "XA", "keywords": ["CGA1"]}]},
         r"shared.regions.eu.models\[0\].keywords: \"CGA1\" must be lowercase, as the text it is searched in is"),
    ],
    ids=["not an object", "an unknown key", "rules not a list", "a rule not an object", "no region",
         "an empty region", "no keywords", "keywords not a list", "an empty keyword", "a keyword with capitals",
         "an answer not a string", "an empty answer", "an answer without a group", "an answer with two groups",
         "an answer that is not a regex", "a model answer without a group", "a model answer that is not a regex",
         "exact rules not a list", "an exact rule with keywords", "an exact rule with no answers",
         "an exact answer with capitals", "a model rule with answers", "a model keyword with capitals"],
)  # fmt: skip
def test_a_region_table_that_is_not_rules_is_rejected_where_it_is_defined(
    tmp_path: Path, table: Any, message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        load(write(tmp_path, {"out_dir": "m", "shared": {"regions": {"eu": table}}}), NO_FLAGS)


@pytest.mark.parametrize(
    ("kind", "rule", "extra"),
    [
        ("text", {"region": "XA", "keywords": ["x"], "match": "zzz"}, "match"),
        ("location", {"region": "XA", "keywords": ["x"], "bogus": 1}, "bogus"),
        ("exact", {"region": "XA", "answers": ["x"], "keywords": ["y"]}, "keywords"),
        ("models", {"region": "XA", "keywords": ["x"], "answers": ["y"]}, "answers"),
    ],
    ids=["an internal key", "an unknown key", "the other kind's words", "a model rule with answers"],
)
def test_a_rule_with_a_key_it_does_not_read_is_rejected(tmp_path: Path, kind: str, rule: Any, extra: str) -> None:
    with pytest.raises(ConfigError, match=rf"shared.regions.eu.{kind}\[0\]: unknown keys: {extra}"):
        load(write(tmp_path, {"out_dir": "m", "shared": {"regions": {"eu": {kind: [rule]}}}}), NO_FLAGS)


def test_a_region_table_may_leave_out_either_kind_of_rule(tmp_path: Path) -> None:
    data = {"out_dir": "m", "shared": {"regions": {"eu": {"text": EU["text"]}, "americas": {}}}}

    assert load(write(tmp_path, data), NO_FLAGS).sources == {}


TABLE = {
    "text": [
        {"region": "XA", "keywords": ["example cable", "wide net"]},
        {"region": "XB", "keywords": ["net"]},
    ],
    "location": [{"region": "XC", "keywords": ["example land"]}],
}


@pytest.mark.parametrize(
    ("text", "location", "region"),
    [
        ("On EXAMPLE Cable since May", False, "XA"),  # the text is searched lowercased
        ("a wide net", False, "XA"),  # the first rule that matches wins, though a later one matches too
        ("the internet", False, "XB"),  # a keyword is found anywhere in the text
        ("Example Land", False, None),  # location rules are not searched in issue text
        ("Example Land", True, "XC"),
        ("Example Land on example cable", True, "XA"),  # text rules come first
        ("nowhere", True, None),
    ],
)
def test_a_region_is_the_first_rule_whose_keyword_is_in_the_text(text: str, location: bool, region: str | None) -> None:
    assert find_region(TABLE, text, location=location) == region


def test_a_table_without_rules_finds_no_region() -> None:
    assert find_region({}, "example cable", location=True) is None


def test_a_rule_found_is_its_region_and_the_keyword_that_matched() -> None:
    assert find_rule(TABLE, "a wide net", location=True) == ("XA", "wide net")
    assert find_rule(TABLE, "Example Land", location=True) == ("XC", "example land")
    assert find_rule(TABLE, "nowhere", location=True) is None


def test_a_region_table_may_have_an_answer(tmp_path: Path) -> None:
    table = {**TABLE, "answer": "ISP: (.*)"}
    data = {"out_dir": "m", "shared": {"regions": {"eu": table}}, "sources": {"example_source": {"regions": "eu"}}}

    assert without_core(load(write(tmp_path, data), NO_FLAGS).sources["example_source"]["regions"]) == table


ANSWERED = {**TABLE, "answer": r"^ISP: *(.*)$"}


@pytest.mark.parametrize(
    ("table", "body", "answer"),
    [
        (ANSWERED, "Modem: X\nISP:  Example Cable \nFirmware: 1", "Example Cable"),  # stripped
        (ANSWERED, "Modem: X\nisp: example cable", "example cable"),  # matched ignoring case
        (ANSWERED, "ISP: one\nISP: two", "one"),  # the first match
        (ANSWERED, "x" * 2000 + "\nISP: far down", "far down"),  # anywhere in the body
        (ANSWERED, "ISP:   ", None),  # an empty answer is none
        (ANSWERED, "No answer here", None),
        (TABLE, "ISP: Example Cable", None),  # a table without an answer
    ],
    ids=["stripped", "ignoring case", "the first", "anywhere", "empty", "no match", "no answer regex"],
)
def test_an_answer_is_what_the_tables_regex_captures_in_a_body(table: Any, body: str, answer: str | None) -> None:
    assert find_answer(table, body) == answer


FORMS = {
    **TABLE,
    "answer": r"^ISP: *(.*)$",
    "model_answer": r"^Modem: *(.*)$",
    "exact": [{"region": "XD", "answers": ["ex", "net"]}],
    "models": [{"region": "XE", "keywords": ["mx100", "mx200"]}, {"region": "XA", "keywords": ["mx3"]}],
}


def test_a_rule_or_known_region_given_as_gb_is_the_projects_uk(tmp_path: Path) -> None:
    config_text = [{"region": "GB", "keywords": ["london"]}]
    table: dict[str, Any] = {
        "text": config_text,
        "exact": [{"region": "GB", "answers": ["bt"]}],
        "models": [{"region": "DE", "keywords": ["mx"]}],
        "known": {"someone": "GB", "other": "DE"},
    }
    data = {"out_dir": "m", "shared": {"regions": {"eu": table}}, "sources": {"example_source": {"regions": "eu"}}}

    config = load(write(tmp_path, data), NO_FLAGS)
    resolved = without_core(config.sources["example_source"]["regions"])

    assert resolved["text"] == [{"region": "UK", "keywords": ["london"]}]
    assert resolved["exact"] == [{"region": "UK", "answers": ["bt"]}]
    assert resolved["models"] == [{"region": "DE", "keywords": ["mx"]}]
    assert resolved["known"] == {"someone": "UK", "other": "DE"}
    assert config_text[0]["region"] == "GB"  # the config's own table is not changed


def test_a_region_table_may_have_exact_answers_a_model_answer_and_model_rules(tmp_path: Path) -> None:
    data = {"out_dir": "m", "shared": {"regions": {"eu": FORMS}}, "sources": {"example_source": {"regions": "eu"}}}

    assert without_core(load(write(tmp_path, data), NO_FLAGS).sources["example_source"]["regions"]) == FORMS


@pytest.mark.parametrize(
    ("body", "model"),
    [
        ("ISP: Ex\nModem:  Example MX100 \n", "Example MX100"),  # stripped
        ("MODEM: mx3", "mx3"),  # matched ignoring case
        ("ISP: Ex\nModem:   ", None),  # an empty answer is none
        ("ISP: Ex", None),
    ],
    ids=["stripped", "ignoring case", "empty", "no match"],
)
def test_a_model_is_what_the_tables_model_answer_captures_in_a_body(body: str, model: str | None) -> None:
    assert find_model(FORMS, body) == model


def test_a_table_without_a_model_answer_finds_no_model() -> None:
    assert find_model(ANSWERED, "Modem: MX100") is None


@pytest.mark.parametrize(
    ("answer", "rule"),
    [
        ("EX", ("XD", "ex")),  # a whole answer, ignoring case
        (" net ", ("XD", "net")),  # stripped; exact rules come before the text rule "net" matches
        ("example cable", ("XA", "example cable")),  # then the text rules
        ("Example Land", ("XC", "example land")),  # then the location rules
        ("Exodus", None),  # an exact answer is never a keyword
        ("nowhere", None),
    ],
)
def test_an_answers_rule_is_an_exact_answer_then_a_text_then_a_location_rule(
    answer: str, rule: tuple[str, str] | None
) -> None:
    assert answer_rule(FORMS, answer) == rule


def test_exact_answers_are_matched_against_answers_only() -> None:
    assert find_region(FORMS, "ex", location=True) is None


@pytest.mark.parametrize(
    ("model", "rule"),
    [
        ("Example MX100 v2", ("XE", "mx100")),  # a keyword anywhere in the model, lowercased
        ("mx300", ("XA", "mx3")),
        ("example cable", None),  # the text rules are not searched in a model
    ],
)
def test_a_models_rule_is_the_first_whose_keyword_is_in_the_model(model: str, rule: tuple[str, str] | None) -> None:
    assert model_rule(FORMS, model) == rule


@pytest.mark.parametrize(
    ("forms", "region"),
    [
        ([("Ex", "MX100")], "XD"),  # the answer decides, though the model says another region
        ([("Somewhere", "MX100")], "XE"),  # a model decides where no rule matches the answer
        ([(None, "MX100")], "XE"),  # or where there is no answer
        ([(None, "MX100"), ("example cable", None)], "XA"),  # any answer a rule matches comes before any model
        ([("Somewhere", "Other"), ("x", "MX200")], "XE"),  # the first model a rule matches
        ([("Somewhere", "Other")], None),
        ([], None),
    ],
    ids=["the answer first", "the model where no rule matches", "the model with no answer", "any answer first",
         "the first model", "neither", "no forms"],
)  # fmt: skip
def test_a_forms_region_is_the_first_answers_a_rule_matches_then_the_first_models(
    forms: list[tuple[str | None, str | None]], region: str | None
) -> None:
    assert form_region(FORMS, forms) == region


def test_a_form_is_its_answer_and_its_model() -> None:
    assert read_form(FORMS, "ISP: Ex\nModem: MX100") == ("Ex", "MX100")
    assert read_form(TABLE, "ISP: Ex\nModem: MX100") == (None, None)


@pytest.mark.parametrize(
    ("forms", "clue"),
    [
        ([("Ex", "MX100")], ("XD", "answer")),
        ([("Somewhere", "MX100")], ("XE", "model")),
        ([(None, "MX100"), ("example cable", None)], ("XA", "answer")),
        ([("Somewhere", "Other")], None),
    ],
    ids=["an answer", "a model", "any answer first", "neither"],
)
def test_a_forms_clue_is_its_region_and_whether_the_answer_or_the_model_gave_it(
    forms: list[tuple[str | None, str | None]], clue: tuple[str, str] | None
) -> None:
    assert form_clue(FORMS, forms) == clue


# --- files people attach: where a file is, what to read in it, and the zones that name a region ---

ATTACHED = {
    **TABLE,
    "attachment": r"(https://example\.test/files/[^\s)]+\.json)",
    "attachment_value": r'"zone": *"([^"]+)"',
    "zones": [{"region": "XA", "keywords": ["area-a/city-1"]}, {"region": "XB", "keywords": ["area-a/city-2"]}],
}


def test_a_region_table_may_name_attachments_a_value_and_zones(tmp_path: Path) -> None:
    data = {"out_dir": "m", "shared": {"regions": {"eu": ATTACHED}}, "sources": {"example_source": {"regions": "eu"}}}

    table = load(write(tmp_path, data), NO_FLAGS).sources["example_source"]["regions"]

    assert {k: v for k, v in table.items() if k not in ("location", "zones")} == {
        k: v for k, v in ATTACHED.items() if k not in ("location", "zones")
    }
    assert table["zones"][: len(ATTACHED["zones"])] == ATTACHED["zones"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"attachment": "no group"}, "shared.regions.eu.attachment must be a regex with one group, the file's URL"),
        ({"attachment": "(a)(b)"}, "shared.regions.eu.attachment must be a regex with one group, the file's URL"),
        ({"attachment_value": "("}, "shared.regions.eu.attachment_value is not a valid regex"),
        ({"attachment_value": "none"}, "shared.regions.eu.attachment_value must be a regex with one group, the value"),
        ({"zones": [{"region": "XA", "keywords": ["Area-A/City-1"]}]}, "must be lowercase"),
        ({"zones": "area"}, "shared.regions.eu.zones must be a list of rules"),
        ({"attachment_value": None}, "attachment and attachment_value go together"),
        ({"attachment": None}, "attachment and attachment_value go together"),
        ({"attachment": None, "attachment_value": None}, "zones need attachment and attachment_value"),
    ],
    ids=["no group", "two groups", "bad regex", "value without a group", "capitals in a zone", "zones not a list",
         "no value", "no attachment", "zones without either"],
)  # fmt: skip
def test_a_bad_attachment_setting_is_a_config_error_named_where_it_is_defined(
    tmp_path: Path, change: dict[str, Any], message: str
) -> None:
    table = {k: v for k, v in {**ATTACHED, **change}.items() if v is not None}
    data = {"out_dir": "m", "shared": {"regions": {"eu": table}}, "sources": {"example_source": {"regions": "eu"}}}

    with pytest.raises(ConfigError, match=re.escape(message)):
        load(write(tmp_path, data), NO_FLAGS)


@pytest.mark.parametrize(
    ("body", "url"),
    [
        ("Files: [a.json](https://example.test/files/1/a.json) and https://example.test/files/2/b.json", "https://example.test/files/1/a.json"),
        ("see https://example.test/files/3/c.zip", None),
        ("nothing", None),
    ],
    ids=["the first", "another kind of file", "none"],
)  # fmt: skip
def test_an_attachment_is_the_first_url_the_tables_regex_captures_in_a_body(body: str, url: str | None) -> None:
    assert find_attachment(ATTACHED, body) == url


def test_a_table_without_an_attachment_regex_finds_none() -> None:
    assert find_attachment(TABLE, "https://example.test/files/1/a.json") is None


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ('{"x": 1, "zone": "Area-A/City-1", "y": 2}', "Area-A/City-1"),
        ('{"zone":"area-a/city-2"\n', "area-a/city-2"),  # text, not JSON
        ('{"zone": ""}', None),
        ("{}", None),
    ],
    ids=["a field", "not valid JSON", "empty", "no match"],
)
def test_a_value_is_what_the_tables_regex_captures_in_a_files_text(text: str, value: str | None) -> None:
    assert find_value(ATTACHED, text) == value


@pytest.mark.parametrize(
    ("zone", "rule"),
    [
        ("Area-A/City-1", ("XA", "area-a/city-1")),  # ignoring case
        ("area-a/city-2", ("XB", "area-a/city-2")),
        ("area-a/city-3", None),  # another country's zone is not read as this one's
        ("example cable", None),  # the text rules are not searched in a zone
    ],
)
def test_a_zones_rule_is_the_first_whose_keyword_is_in_the_zone(zone: str, rule: tuple[str, str] | None) -> None:
    assert zone_rule(ATTACHED, zone) == rule


def test_a_fingerprint_follows_its_value_and_nothing_else() -> None:
    assert fingerprint([1, 2]) == fingerprint([1, 2])
    assert fingerprint([1, 2]) != fingerprint([2, 1])
    assert len(fingerprint({"a": 1})) == 12


# --- known regions: a person's login and the region the maintainer knows ---

KNOWN = {**ATTACHED, "models": [{"region": "XE", "keywords": ["mx100"]}], "known": {"person-a": "XA", "Person-B": "XE"}}


def test_a_region_table_may_name_known_regions(tmp_path: Path) -> None:
    data = {"out_dir": "m", "shared": {"regions": {"eu": KNOWN}}, "sources": {"example_source": {"regions": "eu"}}}

    assert without_core(load(write(tmp_path, data), NO_FLAGS).sources["example_source"]["regions"]) == KNOWN


@pytest.mark.parametrize(
    ("known", "message"),
    [
        ([], "shared.regions.eu.known must be an object of login to region"),
        ({"": "XA"}, "shared.regions.eu.known: a login must be a non-empty string"),
        ({"person-a": ""}, "shared.regions.eu.known.person-a must be a non-empty region"),
        ({"person-a": 5}, "shared.regions.eu.known.person-a must be a non-empty region"),
        ({"person-a": "ZZ"}, "shared.regions.eu.known.person-a: 'ZZ' is not a region code"),
        ({"person-a": "XA", "PERSON-A": "XB"}, "shared.regions.eu.known: person-a is given twice"),
    ],
    ids=["not an object", "empty login", "empty region", "not a string", "unknown code", "a login twice"],
)  # fmt: skip
def test_a_bad_known_setting_is_a_config_error_named_where_it_is_defined(
    tmp_path: Path, known: Any, message: str
) -> None:
    data = {
        "out_dir": "m",
        "shared": {"regions": {"eu": {**TABLE, "known": known}}},
        "sources": {"example_source": {"regions": "eu"}},
    }

    with pytest.raises(ConfigError, match=re.escape(message)):
        load(write(tmp_path, data), NO_FLAGS)


@pytest.mark.parametrize(
    ("login", "region"),
    [("person-a", "XA"), ("PERSON-A", "XA"), ("person-b", "XE"), ("person-c", None)],
    ids=["exact", "ignoring case", "the configured case", "not known"],
)
def test_a_known_region_is_found_by_login_ignoring_case(login: str, region: str | None) -> None:
    assert known_region(KNOWN, login) == region


def test_a_table_without_known_regions_knows_nobody() -> None:
    assert known_region(TABLE, "person-a") is None


# --- output files: a default per source, and an entry's `file` template ---


def test_the_default_file_is_the_source_name_with_dashes_and_the_entry_id() -> None:
    assert default_file("example_source") == "example-source.jsonl"
    assert default_file("github_repo_activity", "label") == "github-repo-activity-{label}.jsonl"


def test_an_entry_without_a_file_writes_the_default_filled_from_its_fields() -> None:
    default = default_file("github_repo_activity", "label")

    assert file_name({"repo": "o/r", "label": "one"}, default, {"label": "one"}) == "github-repo-activity-one.jsonl"


def test_the_file_template_of_an_entry_overrides_the_default() -> None:
    entry = {"repo": "o/r", "label": "one", "file": "example-{label}.jsonl"}

    assert file_name(entry, "unused-{label}.jsonl", {"label": "one"}) == "example-one.jsonl"
    assert file_name({"file": "example.jsonl"}, "unused.jsonl", {"label": "one"}) == "example.jsonl"


@pytest.mark.parametrize(
    ("template", "fields", "message"),
    [
        (5, {}, "file: must be a string, not 5"),
        ("example-{nope}.jsonl", {"label": "one"}, "file: .* uses {nope}, which is not one of: label"),
        ("example-{0}.jsonl", {"label": "one"}, r"file: .* uses \{0\}, which is not one of: label"),
        ("example-{}.jsonl", {"label": "one"}, "file: .* uses {}, which is not one of: label"),
        ("example-{label.x}.jsonl", {"label": "one"}, "file: .* uses {label.x}, which is not one of: label"),
        ("example-{label", {"label": "one"}, "file: .* is not a valid template"),
        ("example-{label:{width}}.jsonl", {"label": "one"}, "file: .* is not a valid template"),
        ("example.json", {}, r"file: .* must fill to a plain \*.jsonl name, not 'example.json'"),
        (".jsonl", {}, r"file: .* must fill to a plain \*.jsonl name, not '.jsonl'"),
        ("../example.jsonl", {}, r"not '../example.jsonl'"),
        ("sub/example.jsonl", {}, r"not 'sub/example.jsonl'"),
        ("sub\\example.jsonl", {}, r"not 'sub\\\\example.jsonl'"),
        ("example-{label}.jsonl", {"label": "a/b"}, r"not 'example-a/b.jsonl'"),
        ("example-{label}.jsonl", {}, "uses {label}, which is not one of: none"),
    ],
    ids=[
        "not a string",
        "a field the source doesn't offer",
        "a positional field",
        "an empty field",
        "an attribute of a field",
        "a malformed template",
        "a field inside a format spec",
        "not .jsonl",
        "no name before .jsonl",
        "a parent folder",
        "a subfolder",
        "a Windows subfolder",
        "a value that makes a path",
        "a source that offers no fields",
    ],
)
def test_a_file_that_is_not_a_plain_jsonl_name_is_a_config_error(template: Any, fields: Any, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        file_name({"file": template}, "default.jsonl", fields)


@pytest.mark.parametrize("name", ["health.jsonl", "Health.jsonl"])
def test_a_file_cannot_be_the_run_health_file(name: str) -> None:
    with pytest.raises(ConfigError, match=r"file: .* is the run's health file"):
        file_name({"file": name}, "default.jsonl", {})


def test_a_health_file_name_in_a_default_template_is_refused_too() -> None:
    with pytest.raises(ConfigError, match="is the run's health file"):
        file_name({}, "{label}.jsonl", {"label": "health"})


# --- a list of entries, as a source's validate() and files() give it ---


def label_problems(entry: dict[str, Any]) -> list[str]:
    """An entry's own problems, as a source words them: here, a `label` it needs."""
    label = entry.get("label")
    return [] if isinstance(label, str) and label else ["label: must be a non-empty string"]


def label_file(entry: Any) -> str:
    return file_name(entry, "example-{label}.jsonl", {"label": entry["label"]})


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ([{"label": "a"}, {"label": "b", "file": "b.jsonl"}], []),
        ({"label": "a"}, [": must be a list of entries"]),
        ([{"label": "a"}, "b"], ["[1]: must be an object"]),
        ([{}, {"label": ""}], ["[0].label: must be a non-empty string", "[1].label: must be a non-empty string"]),
        (
            [{"label": "a", "file": "x-{site}.jsonl"}],
            ["[0].file: 'x-{site}.jsonl' uses {site}, which is not one of: label"],
        ),
        ([{"file": 5}], ["[0].label: must be a non-empty string"]),
    ],
    ids=[
        "valid",
        "not a list",
        "an entry not an object",
        "each entry's own problems, by its path",
        "a file its template can't fill",
        "the file is checked only once the entry's own keys are usable",
    ],
)  # fmt: skip
def test_entry_problems_names_each_problem_by_the_path_of_its_entry(cfg: Any, problems: list[str]) -> None:
    assert entry_problems(cfg, ("label",), label_problems, label_file) == problems


def test_entry_problems_rejects_a_key_that_is_neither_the_sources_nor_file() -> None:
    cfg = [{"label": "a", "file": "a.jsonl", "extra": "b"}]

    assert entry_problems(cfg, ("label",), label_problems, label_file) == [
        "[0].extra: unknown key (the keys are file, label)"
    ]


def test_entry_files_keys_each_file_by_the_path_of_its_entry() -> None:
    cfg = [{"label": "a"}, {"label": "b", "file": "b.jsonl"}]

    assert entry_files(cfg, label_file) == {"[0]": "example-a.jsonl", "[1]": "b.jsonl"}


# ---- The default summary's sections ------------------------------------------------------------


def dated(field: str, values: Mapping[str, Any], **more: Any) -> list[dict[str, Any]]:
    return [{"date": day, field: value, **more} for day, value in values.items()]


def days(count: int, value: Any, last: str = "2026-09-24") -> dict[str, Any]:
    """`count` days ending on `last`, each with `value`."""
    end = date.fromisoformat(last)
    return {(end - timedelta(days=n)).isoformat(): value for n in range(count - 1, -1, -1)}


def rendered(tmp_path: Path, rows: Sequence[Mapping[str, Any]], *sections: Section) -> list[str]:
    """What the sections show, from `rows` in t.jsonl, rendered by the reports engine."""
    folder = tmp_path / "out"
    folder.mkdir(exist_ok=True)
    (folder / "t.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    lines = [line for section in sections for line in section_lines(section)]
    block = {"file": "s.md", "block": "s", "place": "top", "lines": lines}
    assert reports.validate([block]) == []
    now = datetime(2026, 9, 24, 23, 30, tzinfo=timezone.utc)
    ctx = Context(now=now, out_dir=folder, log=logging.getLogger("leadline"), http=Http("test/1.0"))
    reports.write(ctx, [block])
    assert ctx.critical == []
    return (folder / "s.md").read_text(encoding="utf-8").splitlines()[1:-1]


def test_a_section_is_its_heading_then_a_line_a_field(tmp_path: Path) -> None:
    rows = dated("n", {"2026-09-24": 5}, version="1.0")

    lines = rendered(
        tmp_path, rows, Section("src", "a/b", "t.jsonl", [Shown("n", "N"), Shown("version", "V", "value")], "Label")
    )

    assert lines == ["", "### src: a/b", "", "- N: 5", "- V: 1.0"]


def test_a_section_shows_nothing_until_its_file_has_a_good_row(tmp_path: Path) -> None:
    failed = [{"date": "2026-09-24", "status": "fetch_failed"}]

    assert rendered(tmp_path, failed, Section("src", "a/b", "t.jsonl", [Shown("n", "N")], "Label")) == []


def test_a_sections_heading_says_how_old_its_rows_are_when_newer_ones_failed(tmp_path: Path) -> None:
    rows: list[dict[str, Any]] = [{"date": "2026-09-23", "n": 5}, {"date": "2026-09-24", "status": "fetch_failed"}]

    lines = rendered(tmp_path, rows, Section("src", None, "t.jsonl", [Shown("n", "N")], "Label"))

    assert lines == ["", "### src (as of 2026-09-23)", "", "- N: 5"]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"2026-09-24": 41}, ["- Stars: 41"]),
        ({"2026-09-17": 36, "2026-09-24": 41}, ["- Stars: 41 · +5 in 7d"]),
        ({"2026-08-20": 30, "2026-09-17": 36, "2026-09-24": 41}, ["- Stars: 41 · +5 in 7d · +11 in 30d"]),
    ],
    ids=["no history", "a week", "a month"],
)
def test_a_snapshot_shows_its_value_and_its_change_where_the_history_reaches(
    tmp_path: Path, values: dict[str, int], expected: list[str]
) -> None:
    lines = rendered(
        tmp_path, dated("stars", values), Section("s", None, "t.jsonl", [Shown("stars", "Stars")], "Label")
    )

    assert lines[3:] == expected


def test_a_snapshot_compares_only_where_it_says(tmp_path: Path) -> None:
    rows = dated("week", {"2026-08-20": 30, "2026-09-17": 36, "2026-09-24": 41})

    lines = rendered(tmp_path, rows, Section("s", None, "t.jsonl", [Shown("week", "Week", compare=("7d",))], "Label"))

    assert lines[3:] == ["- Week: 41 · +5 in 7d"]


def test_a_change_shows_only_the_change_and_nothing_without_history(tmp_path: Path) -> None:
    shown = [Shown("opened", "Opened", "change")]

    history = dated("opened", {"2026-08-20": 30, "2026-09-17": 36, "2026-09-24": 41})
    assert rendered(tmp_path, history, Section("s", None, "t.jsonl", shown, "Label"))[3:] == [
        "- Opened: +5 in 7d · +11 in 30d"
    ]
    assert rendered(tmp_path, history[-1:], Section("s", None, "t.jsonl", shown, "Label"))[3:] == []


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (days(6, 1), []),
        (days(7, 1), ["- Views: 7 in 7d"]),
        (days(14, 1), ["- Views: 7 in 7d (+0)"]),
        ({**days(60, 1), **days(9, 2)}, ["- Views: 14 in 7d (+5) · 39 in 30d (+9)"]),
    ],
    ids=["under a week", "a week", "two weeks", "two months"],
)
def test_a_daily_count_shows_its_totals_over_7_and_30_rows_and_each_change_once_the_window_before_is_whole(
    tmp_path: Path, values: dict[str, int], expected: list[str]
) -> None:
    lines = rendered(
        tmp_path, dated("views", values), Section("s", None, "t.jsonl", [Shown("views", "Views", "daily")], "Label")
    )

    assert lines[3:] == expected


def test_a_format_applies_to_the_value_and_its_change(tmp_path: Path) -> None:
    rows = dated("hours", {"2026-09-17": 15.25, "2026-09-24": 17.5})

    lines = rendered(tmp_path, rows, Section("s", None, "t.jsonl", [Shown("hours", "Hours", format=".1f")], "Label"))

    assert lines[3:] == ["- Hours: 17.5 · +2.2 in 7d"]


def test_where_keeps_a_sections_rows(tmp_path: Path) -> None:
    rows = [{"date": "2026-09-24", "topic_id": 1, "views": 5}, {"date": "2026-09-24", "topic_id": 2, "views": 9}]

    lines = rendered(
        tmp_path, rows, Section("t", None, "t.jsonl", [Shown("views", "Views")], "Label", where={"topic_id": 2})
    )
    missing = rendered(
        tmp_path, rows, Section("t", None, "t.jsonl", [Shown("views", "V")], "Label", where={"topic_id": 3})
    )

    assert (lines, missing) == (["", "### t", "", "- Views: 9"], [])


def test_braces_in_a_title_or_label_are_text(tmp_path: Path) -> None:
    rows = dated("n", {"2026-09-24": 5})

    lines = rendered(tmp_path, rows, Section("a", "{b}", "t.jsonl", [Shown("n", "{n}")], "Label"))

    assert lines == ["", "### a: {b}", "", "- {n}: 5"]


def test_entry_summary_gives_a_section_an_entry_titled_by_its_id(tmp_path: Path) -> None:
    cfg = [{"repo": "a/b"}, {"repo": "c/d", "file": "t.jsonl"}]
    rows = dated("n", {"2026-09-24": 5})

    lines = rendered(
        tmp_path, rows, *entry_summary("src", cfg, "repo", lambda entry: "t.jsonl", [Shown("n", "N")], "Things")
    )

    assert lines == ["", "### src: a/b", "", "- N: 5", "", "### src: c/d", "", "- N: 5"]


def test_entry_summary_gives_each_section_the_sources_label() -> None:
    sections = entry_summary("src", [{"repo": "a/b"}, {"repo": "c/d"}], "repo", lambda entry: "t.jsonl", [], "Things")

    assert [(section.title, section.label) for section in sections] == [("src: a/b", "Things"), ("src: c/d", "Things")]


def test_a_breakdown_shows_the_newest_counts_the_largest_first_then_the_rest_past_the_top(tmp_path: Path) -> None:
    counts = {f"k{n}": n for n in range(1, 11)}  # ten keys: the top five, and k1 to k5 as the rest
    rows = [{"date": "2026-09-23", "by": {"old": 99}}, {"date": "2026-09-24", "by": counts}]

    lines = rendered(tmp_path, rows, Section("s", None, "t.jsonl", [Shown("by", "By kind", "breakdown")], "Label"))

    assert lines[3:] == ["- By kind: k10 10 · k9 9 · k8 8 · k7 7 · k6 6 · other 15"]


def test_a_breakdown_with_no_more_than_the_top_has_no_rest(tmp_path: Path) -> None:
    rows = [{"date": "2026-09-24", "by": {"os": 3, "core": 5}}]

    lines = rendered(tmp_path, rows, Section("s", None, "t.jsonl", [Shown("by", "By", "breakdown")], "Label"))

    assert lines[3:] == ["- By: core 5 · os 3"]


def test_a_sections_own_lines_follow_its_fields(tmp_path: Path) -> None:
    rows = dated("n", {"2026-09-24": 5})
    extra = {"from": "t.jsonl", "field": "n", "text": "- Plain {value}", "as_of": ""}

    lines = rendered(tmp_path, rows, Section("s", None, "t.jsonl", [Shown("n", "N")], "Label", lines=[extra]))

    assert lines == ["", "### s", "", "- N: 5", "- Plain 5"]


def test_a_sections_title_is_its_source_and_name() -> None:
    assert (Section("s", "a/b", "t.jsonl", (), "Label").title, Section("s", None, "t.jsonl", (), "Label").title) == (
        "s: a/b",
        "s",
    )


def test_a_second_section_of_an_entry_has_its_part_after_the_name_in_its_title() -> None:
    assert Section("s", "a/b", "t.jsonl", (), "Label", part="stars").title == "s: a/b stars"
    assert Section("s", None, "t.jsonl", (), "Label", part="stars").title == "s stars"


def test_a_shown_of_an_unknown_kind_is_a_bug() -> None:
    with pytest.raises(ValueError, match="kind must be one of snapshot, change, daily, value, breakdown"):
        section_lines(Section("s", None, "t.jsonl", [Shown("n", "N", "total")], "Label"))


def test_a_complement_shows_100_less_the_value_and_its_change(tmp_path: Path) -> None:
    """A rank's percentile (lower is better) read as the share ranked below it, which climbs as it rises."""
    rows = dated("percentile", {"2026-09-17": 27.1, "2026-09-24": 26.6})
    shown = [Shown("percentile", "Ahead", format=".1f", complement=True)]

    lines = rendered(tmp_path, rows, Section("s", None, "t.jsonl", shown, "Label"))

    assert lines[3:] == ["- Ahead: 73.4 · +0.5 in 7d"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(58.8, 41.2), (99.9, 0.1), (30, 70), (100.0, 0.0), (None, None), ("26.6", None), (True, None)],
    ids=["a percent", "no float noise", "a whole number", "the whole", "null", "text", "a boolean"],
)
def test_complement_is_100_less_a_number_to_its_own_decimals(value: Any, expected: Any) -> None:
    found = complement(value)

    assert (found, type(found)) == (expected, type(expected))


@pytest.mark.parametrize("kind", ["daily", "breakdown"])
def test_a_complement_of_a_daily_count_or_a_breakdown_is_a_bug(kind: str) -> None:
    with pytest.raises(ValueError, match="complement: only snapshot, change and value take it"):
        section_lines(Section("s", None, "t.jsonl", [Shown("n", "N", kind, complement=True)], "Label"))


@pytest.mark.parametrize(
    ("value", "count"),
    [(0, True), (7, True), (True, False), (1.0, False), ("1", False), (None, False)],
    ids=["zero", "a number", "a boolean", "a float", "a string", "null"],
)
def test_a_count_is_an_int_and_never_a_boolean(value: Any, count: bool) -> None:
    assert is_count(value) is count


def test_a_summary_may_name_the_card_its_section_is_a_row_of() -> None:
    card = Card("Traffic", "By day", "One bar a day.")

    (section,) = entry_summary("s", [{"repo": "a/b"}], "repo", lambda entry: "f.jsonl", [], "Label", card=card)

    assert section.card == card
    assert entry_summary("s", [{"repo": "a/b"}], "repo", lambda entry: "f.jsonl", [], "Label")[0].card is None
