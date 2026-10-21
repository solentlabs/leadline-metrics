"""tools/refresh_places.py: the vendored places data (src/leadline/places/) made from the standards' files.

Parsing and rendering run on small fixture text; no test reaches the network (`make places` does).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import refresh_places as refresh

COUNTRIES = "# a comment\n#code\tname\nAD\tAndorra\nGB\tBritain (UK)\nUS\tUnited States\nPR\tPuerto Rico\n"
ZONE_TAB = (
    "# a comment\n"
    "AD\t+4230+00131\tEurope/Andorra\n"
    "GB\t+513030-0000731\tEurope/London\n"
    "US\t+404251-0740023\tAmerica/New_York\tEastern (most areas)\n"
    "US\t+340308-1181434\tAmerica/Los_Angeles\tPacific\n"
)
BACKWARD = (
    "# links\n"
    "Link\tEurope/London\tEurope/Belfast\n"
    "Link\tAmerica/New_York\t\tUS/Eastern\t#= a note\n"
    "Link\tEurope/London\tGB\n"  # no slash: a bare word, left out
    "Link\tEtc/UTC\tEtc/Zulu\n"  # its target is in no country
)
STATES = (
    "STATE|STUSAB|STATE_NAME|STATENS\n"
    "01|AL|Alabama|01779775\n11|DC|District of Columbia|01702382\n72|PR|Puerto Rico|01779808\n"
    "78|VI|Virgin Islands|01802710\n"
)


def test_countries_are_each_code_and_its_name_without_comments() -> None:
    assert refresh.parse_countries(COUNTRIES) == {
        "AD": "Andorra",
        "GB": "Britain (UK)",
        "US": "United States",
        "PR": "Puerto Rico",
    }


def test_zones_are_each_zone_and_its_country_with_the_links_that_name_a_zone_in_a_country() -> None:
    zones = refresh.parse_zones(ZONE_TAB, BACKWARD)

    assert zones == {
        "America/Los_Angeles": "US",
        "America/New_York": "US",
        "Europe/Andorra": "AD",
        "Europe/Belfast": "GB",
        "Europe/London": "GB",
        "US/Eastern": "US",
    }


def test_states_are_the_fifty_and_the_district_not_the_territories_which_are_countries_of_their_own() -> None:
    assert refresh.parse_states(STATES) == {"AL": "Alabama", "DC": "District of Columbia"}


def test_a_malformed_line_stops_the_refresh_naming_the_file() -> None:
    with pytest.raises(refresh.RefreshError, match=r"iso3166\.tab: cannot read 'AD'"):
        refresh.parse_countries("AD\n")
    with pytest.raises(refresh.RefreshError, match=r"zone\.tab: cannot read 'AD.tone'"):
        refresh.parse_zones("AD\tone\n", "")
    with pytest.raises(refresh.RefreshError, match=r"state\.txt: cannot read 'AL'"):
        refresh.parse_states("STATE|STUSAB|STATE_NAME|STATENS\nAL\n")


def rendered(name: str, retrieved: str = "2026-10-19") -> str:
    data, release = refresh.table(name, fetched)
    return refresh.render(name, data, release, retrieved)


def test_each_file_holds_one_table_and_where_it_came_from() -> None:
    countries = json.loads(rendered("countries"))
    zones = json.loads(rendered("zones"))
    states = json.loads(rendered("states"))

    assert countries["data"]["GB"] == "Britain (UK)"
    assert zones["data"]["US/Eastern"] == "US"
    assert states["data"] == {"AL": "Alabama", "DC": "District of Columbia"}
    assert [(f["release"], f["retrieved"]) for f in (countries, zones)] == [("2026e", "2026-10-19")] * 2
    assert (states["release"], states["retrieved"]) == (None, "2026-10-19")  # the Census list has no version
    assert countries["sources"] == [refresh.COUNTRIES_URL, refresh.RELEASE_URL]
    assert zones["sources"] == [refresh.ZONES_URL, refresh.LINKS_URL, refresh.RELEASE_URL]
    assert states["sources"] == [refresh.STATES_URL]
    assert all(set(f) == {"sources", "release", "retrieved", "data"} for f in (countries, zones, states))


def test_a_file_is_two_space_indented_sorted_and_ends_in_one_newline() -> None:
    text = rendered("countries")

    assert text.endswith("}\n")
    assert not text.endswith("\n\n")
    assert '\n  "data": {\n    "AD": "Andorra",' in text
    assert list(json.loads(text)) == sorted(json.loads(text))


def test_rendering_is_the_same_whatever_order_the_table_came_in() -> None:
    data, release = refresh.table("zones", fetched)

    assert refresh.render("zones", dict(reversed(list(data.items()))), release, "2026-10-19") == rendered("zones")


def fetched(url: str) -> str:
    return {
        refresh.COUNTRIES_URL: COUNTRIES,
        refresh.ZONES_URL: ZONE_TAB,
        refresh.LINKS_URL: BACKWARD,
        refresh.STATES_URL: STATES,
        refresh.RELEASE_URL: "2026e\n",
    }[url]


def read(folder: Path, name: str) -> str:
    return (folder / f"{name}.json").read_text(encoding="utf-8")


def test_a_refresh_writes_each_file_with_the_day_it_was_retrieved(tmp_path: Path) -> None:
    done = refresh.refresh(tmp_path, "2026-10-19", fetched)

    assert done == dict.fromkeys(refresh.FILES, "rewritten")
    assert [read(tmp_path, n) for n in refresh.FILES] == [rendered(n) for n in refresh.FILES]


def test_a_refresh_that_changes_nothing_leaves_every_file_and_its_date_alone(tmp_path: Path) -> None:
    refresh.refresh(tmp_path, "2026-10-19", fetched)
    before = [read(tmp_path, n) for n in refresh.FILES]

    assert refresh.refresh(tmp_path, "2026-11-30", fetched) == dict.fromkeys(refresh.FILES, "unchanged")
    assert [read(tmp_path, n) for n in refresh.FILES] == before


def test_a_standard_that_changed_rewrites_its_file_only(tmp_path: Path) -> None:
    refresh.refresh(tmp_path, "2026-10-19", fetched)
    before = {n: read(tmp_path, n) for n in refresh.FILES}

    def newer_states(url: str) -> str:
        return fetched(url) + "02|AK|Alaska|01785533\n" if url == refresh.STATES_URL else fetched(url)

    assert refresh.refresh(tmp_path, "2027-02-01", newer_states) == {
        "countries": "unchanged",
        "zones": "unchanged",
        "states": "rewritten",
    }
    assert {n: read(tmp_path, n) for n in ("countries", "zones")} == {n: before[n] for n in ("countries", "zones")}
    assert json.loads(read(tmp_path, "states"))["retrieved"] == "2027-02-01"
    assert "AK" in json.loads(read(tmp_path, "states"))["data"]


def test_a_new_tz_release_changes_countries_and_zones_and_not_states(tmp_path: Path) -> None:
    refresh.refresh(tmp_path, "2026-10-19", fetched)

    def newer(url: str) -> str:
        return "2027a\n" if url == refresh.RELEASE_URL else fetched(url)

    done = refresh.refresh(tmp_path, "2027-02-01", newer)

    assert done == {"countries": "rewritten", "zones": "rewritten", "states": "unchanged"}
    assert json.loads(read(tmp_path, "zones"))["release"] == "2027a"


def test_a_source_that_fails_leaves_its_file_and_the_others_refresh(tmp_path: Path) -> None:
    refresh.refresh(tmp_path, "2026-10-19", fetched)
    before = {n: read(tmp_path, n) for n in refresh.FILES}

    def zones_down(url: str) -> str:
        if url == refresh.ZONES_URL:
            raise OSError("down")
        return fetched(url).replace("Andorra", "Andorra (changed)") if url == refresh.COUNTRIES_URL else fetched(url)

    done = refresh.refresh(tmp_path, "2026-10-20", zones_down)

    assert done["zones"].startswith("failed: ")
    assert (done["countries"], done["states"]) == ("rewritten", "unchanged")
    assert read(tmp_path, "zones") == before["zones"]
    assert not list(tmp_path.glob("*.tmp"))


def test_a_source_that_is_malformed_writes_nothing_of_its_file(tmp_path: Path) -> None:
    def broken(url: str) -> str:
        return "AD\n" if url == refresh.COUNTRIES_URL else fetched(url)

    done = refresh.refresh(tmp_path, "2026-10-19", broken)

    assert done["countries"].startswith("failed: iso3166.tab: cannot read")
    assert not (tmp_path / "countries.json").exists()
    assert (done["zones"], done["states"]) == ("rewritten", "rewritten")


def test_a_refresh_may_name_the_files_it_refreshes(tmp_path: Path) -> None:
    assert refresh.refresh(tmp_path, "2026-10-19", fetched, ("states",)) == {"states": "rewritten"}
    assert [p.name for p in tmp_path.iterdir()] == ["states.json"]
