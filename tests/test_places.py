"""The places core knows (leadline.places): country names, US states, time zones and the region codes.

A region table's own rules come first and the core fills the gaps (docs/CONFIGURATION.md, "Shared tables").
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from leadline import places
from leadline.config import SETTINGS, ConfigError, find_region, find_rule, load, zone_rule
from leadline.places import STATES

NO_FLAGS = dict.fromkeys(SETTINGS)
ATTACHED = {
    "attachment": r"(https://example\.test/files/[^\s)]+\.json)",
    "attachment_value": r'"zone": *"([^"]+)"',
}


def resolved(table: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    """The table as a source sees it: `table` defined under shared.regions and named by an entry."""
    path = tmp_path / "leadline.json"
    data = {"out_dir": "m", "shared": {"regions": {"eu": table}}, "sources": {"example_source": {"regions": "eu"}}}
    path.write_text(json.dumps(data), encoding="utf-8")
    table_seen: dict[str, Any] = load(path, NO_FLAGS).sources["example_source"]["regions"]
    return table_seen


CORE = {"location": places.location_rules()}


def region(text: str, table: dict[str, Any] = CORE, tmp_path: Path | None = None) -> str | None:
    """The region of a location: the core's alone, or a table's as a source sees it (with `tmp_path`)."""
    seen = resolved(table, tmp_path) if tmp_path else table
    return find_region(seen, text, location=True)


# --- region codes ---


@pytest.mark.parametrize("code", ["DE", "US", "GB", "UK", "UA", "XA", "XK", "XZ"])
def test_a_region_code_is_an_iso_code_the_uk_alias_or_a_placeholder(code: str) -> None:
    assert places.is_code(code)


@pytest.mark.parametrize("code", ["UKK", "ZZ", "de", "", "X", "XAA", "EU", "unknown"])
def test_anything_else_is_not_a_region_code(code: str) -> None:
    assert not places.is_code(code)


def test_the_uk_is_the_country_of_gb() -> None:
    assert places.country("UK") == places.country("GB") == "Britain (UK)"
    assert places.country("DE") == "Germany"
    assert places.country("XA") is None


# --- country names: whole words, longest first ---


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("berlin, germany", "DE"),
        ("united kingdom", "UK"),  # the project's code for GB
        ("manchester, england", "UK"),
        ("kyiv, ukraine", "UA"),
        ("oman", "OM"),
        ("india", "IN"),
        ("niger", "NE"),
        ("mali", "ML"),
        ("republic of korea / south korea", "KR"),
        ("papua new guinea", "PG"),  # the longer name, not guinea
        ("equatorial guinea", "GQ"),
        ("guinea", "GN"),
        ("guinea-bissau", "GW"),
        ("turkey", "TR"),
        ("españa", "ES"),
        ("czechia", "CZ"),
    ],
)
def test_a_country_name_in_a_location_gives_its_code(text: str, code: str) -> None:
    assert region(text) == code


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("romania", "RO"),  # not oman
        ("indiana, usa", "US"),  # not india
        ("nigeria", "NG"),  # not niger
        ("somalia", "SO"),  # not mali
        ("new mexico", "US"),  # not mexico
        ("west virginia", "US"),
        ("dominican republic", "DO"),  # not dominica
        ("south africa", "ZA"),  # not africa-anything
    ],
)
def test_a_country_name_inside_another_word_or_name_is_not_found(text: str, code: str) -> None:
    assert region(text) == code


@pytest.mark.parametrize("text", ["romanian", "omani", "indianapolis", "the nigerian coast", "mali3", ""])
def test_a_name_that_is_only_part_of_a_word_gives_nothing(text: str) -> None:
    assert region(text) is None


# --- US states: names, abbreviations at the end of a field, ambiguity ---


@pytest.mark.parametrize(
    "text",
    [
        "austin, texas",
        "austin, tx",
        "austin, tx, usa",
        "austin, tx 78701",
        "Austin, TX".lower(),
        "washington, dc",
        "washington state",
        "new york, ny",
        "new jersey",
        "jersey city, nj",
        "atlanta, georgia",
        "portland, or",
        "portland, or.",
        "indianapolis, in",
        "portland, maine",
        "portland, me",
        "wilmington, de",
        "san jose",
        "u.s.a",
        "the u.s.",
        "usa",
        "united states",
    ],
)
def test_a_us_state_or_place_in_a_location_gives_the_us(text: str) -> None:
    assert region(text) == "US"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("berlin, in germany", "DE"),  # ", in" is a word here, not Indiana
        ("lagos, or nearby", None),  # ", or" is a word here, not Oregon
        ("call me, maybe", None),  # ", ma" starts "maybe": an abbreviation is the whole token
        ("tbilisi, georgia, georgia", "US"),  # a bare "georgia" is the state, as a United States location would say
        ("sydney, nsw, australia", "AU"),
        ("berlin, de, germany", "DE"),  # a country's name outranks an abbreviation
        ("paris, texas, france", "FR"),  # ... and so does a longer name outrank a shorter
        ("toronto, ca, canada", "CA"),
    ],
)
def test_an_abbreviation_is_the_whole_token_ending_a_field_and_a_country_name_outranks_it(
    text: str, code: str | None
) -> None:
    assert region(text) == code


def test_jersey_alone_is_left_out_because_it_is_most_often_a_us_place() -> None:
    assert region("jersey") is None
    assert region("jersey city") is None


def test_a_longer_name_is_tried_before_a_shorter_one() -> None:
    rule = find_rule(CORE, "new guinea, guinea, papua new guinea", location=True)

    assert rule == ("PG", "papua new guinea")


def test_the_us_states_and_the_district_are_all_there() -> None:
    names = {rule["keywords"][0] for rule in places.location_rules() if rule["region"] == "US"}

    assert len(STATES) == 51
    assert {state.lower() for state in STATES.values()} <= names
    assert {f"{abbreviation.lower()}" for abbreviation in STATES} <= names


def test_puerto_rico_is_its_own_region_not_a_us_state() -> None:
    assert region("san juan, puerto rico") == "PR"
    assert region("san juan, pr") is None


# --- the merge: a table's own rules first, the core fills the gaps ---


def test_a_tables_own_location_rules_come_before_the_core(tmp_path: Path) -> None:
    table = {"location": [{"region": "XA", "keywords": ["germany", "indiana"]}]}

    assert region("hamburg, germany", table, tmp_path) == "XA"  # the table says XA where the core says DE
    assert region("indiana", table, tmp_path) == "XA"
    assert region("france", table, tmp_path) == "FR"  # a gap the core fills


def test_the_text_rules_come_before_the_core_locations_and_the_core_has_no_text_rules(tmp_path: Path) -> None:
    table = {"text": [{"region": "XB", "keywords": ["acme"]}]}
    seen = resolved(table, tmp_path)

    assert find_region(seen, "acme, france", location=True) == "XB"
    assert find_region(seen, "acme, france") == "XB"
    assert find_region(seen, "france") is None  # issue text is searched by the table's own text rules only
    assert seen["text"] == table["text"]


def test_a_keyword_the_table_gives_still_matches_inside_a_word(tmp_path: Path) -> None:
    table = {"location": [{"region": "XA", "keywords": ["oman"]}]}

    assert region("romania", table, tmp_path) == "XA"  # a config keyword is a substring, as it always was


def test_the_core_adds_nothing_the_table_did_not_ask_for(tmp_path: Path) -> None:
    seen = resolved({}, tmp_path)

    assert set(seen) == {"location"}
    assert seen["location"] == places.location_rules()


def test_the_table_in_the_config_is_not_changed_by_the_merge(tmp_path: Path) -> None:
    table = {"location": [{"region": "XA", "keywords": ["x"]}]}
    resolved(table, tmp_path)

    assert table == {"location": [{"region": "XA", "keywords": ["x"]}]}


# --- zones: whole zone names, only for a table that reads attachments ---


def test_a_table_that_reads_attachments_gets_the_core_zones_after_its_own(tmp_path: Path) -> None:
    table = {**ATTACHED, "zones": [{"region": "XA", "keywords": ["europe/berlin"]}]}
    seen = resolved(table, tmp_path)

    assert zone_rule(seen, "Europe/Berlin") == ("XA", "europe/berlin")  # the table's own first
    assert zone_rule(seen, "Europe/Paris") == ("FR", "europe/paris")
    assert seen["zones"][0] == table["zones"][0]


def test_a_table_that_reads_attachments_needs_no_zones_of_its_own(tmp_path: Path) -> None:
    seen = resolved(ATTACHED, tmp_path)

    assert zone_rule(seen, "America/New_York") == ("US", "america/new_york")
    assert zone_rule(seen, "Europe/London") == ("UK", "europe/london")


def test_a_table_that_reads_no_attachments_gets_no_zones(tmp_path: Path) -> None:
    assert "zones" not in resolved({}, tmp_path)


@pytest.mark.parametrize(
    ("zone", "code"),
    [
        ("America/Indiana/Indianapolis", "US"),
        ("America/Kentucky/Louisville", "US"),
        ("Australia/Sydney", "AU"),
        ("Europe/Kiev", "UA"),  # an old name for Europe/Kyiv
        ("America/Montreal", "CA"),
        ("US/Eastern", "US"),
        ('"timezone": "Asia/Kolkata"', "IN"),
        ("america/dawson_creek", "CA"),  # not America/Dawson, a zone whose name begins it
        ("America/Dawson", "CA"),
        ("America/Bahia_Banderas", "MX"),  # not America/Bahia (BR)
        ("America/Bahia", "BR"),
        ("Not/AZone", None),
        ("europe", None),
    ],
)
def test_a_zone_is_found_by_its_whole_name_not_by_a_name_it_contains(
    zone: str, code: str | None, tmp_path: Path
) -> None:
    found = zone_rule(resolved(ATTACHED, tmp_path), zone)

    assert (found[0] if found else None) == code


def test_the_zone_that_begins_another_zones_name_gives_its_own_country(tmp_path: Path) -> None:
    seen = resolved(ATTACHED, tmp_path)

    assert zone_rule(seen, "America/Bahia_Banderas") == ("MX", "america/bahia_banderas")
    assert zone_rule(seen, "America/Bahia") == ("BR", "america/bahia")


# --- the code check ---


def config_error(table: dict[str, Any], tmp_path: Path) -> str:
    with pytest.raises(ConfigError) as e:
        resolved(table, tmp_path)
    return str(e.value)


@pytest.mark.parametrize("kind", ["text", "location", "models", "zones"])
def test_a_rule_that_gives_a_code_core_does_not_know_is_a_config_error_naming_where(kind: str, tmp_path: Path) -> None:
    table = {**ATTACHED, kind: [{"region": "XA", "keywords": ["ok"]}, {"region": "UKK", "keywords": ["x"]}]}

    assert f"shared.regions.eu.{kind}[1]: 'UKK' is not a region code" in config_error(table, tmp_path)


def test_an_exact_rule_is_checked_too(tmp_path: Path) -> None:
    table = {"exact": [{"region": "ZZ", "answers": ["x"]}]}

    assert "shared.regions.eu.exact[0]: 'ZZ' is not a region code" in config_error(table, tmp_path)


@pytest.mark.parametrize("code", ["UK", "GB", "XA", "XZ", "DE"])
def test_uk_the_placeholders_and_the_iso_codes_are_accepted(code: str, tmp_path: Path) -> None:
    table = {"location": [{"region": code, "keywords": ["somewhere"]}], "known": {"person": code}}

    assert resolved(table, tmp_path)["known"] == {"person": "UK" if code == "GB" else code}  # GB is the project's UK


def test_a_known_region_may_be_one_only_core_gives(tmp_path: Path) -> None:
    assert resolved({"known": {"person": "FR"}}, tmp_path)["known"] == {"person": "FR"}


def test_a_known_region_core_does_not_know_is_a_config_error(tmp_path: Path) -> None:
    message = config_error({"known": {"person": "UKK"}}, tmp_path)

    assert "shared.regions.eu.known.person: 'UKK' is not a region code" in message


def test_the_message_says_what_a_code_is() -> None:
    for word in ("ISO 3166-1", "UK", "XA"):
        assert word in places.CODE_HELP


FOLDER = Path(places.__file__).parent


@pytest.mark.parametrize(("name", "least"), [("countries", 200), ("zones", 300), ("states", 51)])
def test_each_vendored_file_records_its_sources_release_and_day(name: str, least: int) -> None:
    data = json.loads((FOLDER / f"{name}.json").read_text(encoding="utf-8"))

    assert set(data) == {"data", "release", "retrieved", "sources"}
    assert re.fullmatch(r"\d{4}-\d\d-\d\d", data["retrieved"])
    assert data["release"] or name == "states"
    assert data["sources"]
    assert all(url.startswith("https://") for url in data["sources"])
    assert len(data["data"]) >= least


def test_the_zones_are_read_only_when_a_table_needs_them(tmp_path: Path) -> None:
    code = (
        "import sys; from leadline import places, config;"
        "places.location_rules(); places.is_code('DE');"
        "print('zones' in places._TABLES);"
        "places.zone_rules(); print('zones' in places._TABLES)"
    )
    found = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, check=True)

    assert found.stdout.split() == ["False", "True"]
