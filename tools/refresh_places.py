"""Regenerate the files of src/leadline/places/, the places leadline knows everywhere, from the standards.

    python tools/refresh_places.py [countries] [zones] [states]    # all three, or those named (`make places`)

countries.json holds the ISO 3166-1 alpha-2 codes and their English names (the time zone database's iso3166.tab),
zones.json the time zones (zone.tab: one country per zone; and the database's `backward` links, old names such as
Europe/Kiev, each with its target's country) and states.json the US states (the Census Bureau's USPS list). Each
file records its sources, release and the day retrieved, and is refreshed on its own: one whose standard is
unchanged is not written, and one whose source fails or is malformed is left as it was while the others go on.
The package reads the files and never fetches (docs/CONFIGURATION.md, "Shared tables"). The output is sorted,
so a change shows as a diff to review. Stdlib only; tests/test_refresh_places.py holds it to fixture text.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parent.parent
FOLDER = ROOT / "src" / "leadline" / "places"
FILES = ("countries", "zones", "states")
TZDB = "https://data.iana.org/time-zones/tzdb/"
COUNTRIES_URL, ZONES_URL, LINKS_URL, RELEASE_URL = (
    TZDB + name for name in ("iso3166.tab", "zone.tab", "backward", "version")
)
STATES_URL = "https://www2.census.gov/geo/docs/reference/state.txt"
# The territories the Census list holds besides the states and the district: each has an ISO 3166-1 code of its own,
# so it is a country in the countries table and not a state.
TERRITORIES = frozenset({"AS", "GU", "MP", "PR", "UM", "VI"})


class RefreshError(Exception):
    """A file that is not in the form the standard gives."""


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip() and not line.startswith("#")]


def parse_countries(text: str) -> dict[str, str]:
    """iso3166.tab: country code -> English name."""
    found: dict[str, str] = {}
    for line in _lines(text):
        code, _, name = line.partition("\t")
        if not re.fullmatch(r"[A-Z]{2}", code) or not name.strip():
            raise RefreshError(f"iso3166.tab: cannot read {line!r}")
        found[code] = name.strip()
    return found


def parse_zones(zone_tab: str, backward: str) -> dict[str, str]:
    """zone.tab: zone name -> country code, and each `backward` link (an old Area/Location name) -> its target's."""
    zones: dict[str, str] = {}
    for line in _lines(zone_tab):
        fields = line.split("\t")
        if len(fields) < 3 or not re.fullmatch(r"[A-Z]{2}", fields[0]):
            raise RefreshError(f"zone.tab: cannot read {line!r}")
        zones[fields[2]] = fields[0]
    links: dict[str, str] = {}
    for line in _lines(backward):
        fields = line.split("#")[0].split()
        if len(fields) == 3 and fields[0] == "Link" and "/" in fields[2] and fields[1] in zones:
            links[fields[2]] = zones[fields[1]]
    return {**links, **zones}


def parse_states(text: str) -> dict[str, str]:
    """state.txt (STATE|STUSAB|STATE_NAME|STATENS): USPS abbreviation -> name, the states and the district."""
    found: dict[str, str] = {}
    for line in _lines(text)[1:]:
        fields = line.split("|")
        if len(fields) < 3 or not re.fullmatch(r"[A-Z]{2}", fields[1]):
            raise RefreshError(f"state.txt: cannot read {line!r}")
        if fields[1] not in TERRITORIES:
            found[fields[1]] = fields[2]
    return found


def table(name: str, get: Callable[[str], str]) -> tuple[dict[str, str], str | None]:
    """The file `name`'s table and its release (None where the standard has none), fetched with `get`."""
    if name == "countries":
        return parse_countries(get(COUNTRIES_URL)), get(RELEASE_URL).strip()
    if name == "zones":
        return parse_zones(get(ZONES_URL), get(LINKS_URL)), get(RELEASE_URL).strip()
    return parse_states(get(STATES_URL)), None  # the Census list carries no version


def render(name: str, data: dict[str, str], release: str | None, retrieved: str) -> str:
    """The file's text: its sources, release and day retrieved, then the table. Sorted by key, indented two
    spaces, one newline at the end, so the same data is always the same text."""
    sources = {
        "countries": [COUNTRIES_URL, RELEASE_URL],
        "zones": [ZONES_URL, LINKS_URL, RELEASE_URL],
        "states": [STATES_URL],
    }[name]
    document = {"sources": sources, "release": release, "retrieved": retrieved, "data": data}
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _retrieved(text: str) -> str | None:
    try:
        found = json.loads(text)["retrieved"]
    except (ValueError, KeyError, TypeError):
        return None
    return found if isinstance(found, str) else None


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https URLs above
        return str(response.read().decode("utf-8"))


def refresh(folder: Path, today: str, get: Callable[[str], str] = fetch, only: Sequence[str] = FILES) -> dict[str, str]:
    """Rewrite each of the files `only` names in `folder` from the standards: name -> "rewritten", "unchanged"
    (the file and its date are kept) or "failed: why" (the file is left as it was)."""
    done: dict[str, str] = {}
    for name in only:
        target = folder / f"{name}.json"
        try:
            data, release = table(name, get)
            text = render(name, data, release, today)
            old = target.read_text(encoding="utf-8") if target.exists() else ""
            kept = _retrieved(old)
            if kept and render(name, data, release, kept) == old:
                done[name] = "unchanged"
                continue
            scratch = target.with_suffix(".json.tmp")
            scratch.write_text(text, encoding="utf-8")
            scratch.replace(target)
            done[name] = "rewritten"
        except (OSError, ValueError, RefreshError) as e:
            done[name] = f"failed: {e}"
    return done


def main(argv: Sequence[str]) -> int:
    unknown = [name for name in argv if name not in FILES]
    if unknown:
        print(f"unknown: {', '.join(unknown)}; the files are {', '.join(FILES)}")
        return 2
    done = refresh(FOLDER, datetime.now(timezone.utc).date().isoformat(), only=tuple(argv) or FILES)
    for name, status in done.items():
        print(f"{FOLDER.relative_to(ROOT)}/{name}.json: {status}{', review the diff' if status == 'rewritten' else ''}")
    return 1 if any(status.startswith("failed") for status in done.values()) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
