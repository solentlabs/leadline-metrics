"""Scenarios (tests/scenarios/): the sources found match scenarios and goldens; the runner keeps its guarantees.

The runner tests run a small stand-in script, so they run in CI (skipped on Windows: script_side). Rerunning the real
script on every scenario needs the script, so it is marked oracle (tests/oracle/test_scenario_goldens.py).
"""

from __future__ import annotations

import dataclasses
import json
import runpy
import types
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import scenarios
from row_files import files_in
from scenarios import Scenario
from scenarios.builders.common import exchange
from scratch_package import make
from script_harness import MAIN_LINE, script_side

import leadline.sources
from leadline import reports, trends
from leadline.config import SUMMARY_KINDS
from leadline.sources.github.tests.builders import github_issue, github_issue_pages

STAND_IN = f"""#!/bin/bash
set -euo pipefail
BASE_DIR="/srv/example/community"
METRICS_DIR="${{BASE_DIR}}/metrics"
LABEL="real"
DATE=$(date +%Y-%m-%d)

collect_fake() {{
    local got
    got=$(curl -sf "https://example.test/$1") || return 1
    printf '{{"date": "%s", "label": "%s", "got": %s, "token": "%s"}}\\n' \\
        "$DATE" "$LABEL" "$got" "${{DUMMY_TOKEN:-unset}}" >> "${{METRICS_DIR}}/fake.jsonl"
}}

main() {{
    collect_fake one
}}

{MAIN_LINE}
"""

ISSUES = [github_issue("solentlabs/example", n) for n in range(1, 256)]
SEED = {"fake.jsonl": '{"date": "2026-09-22"}\n', "other.jsonl": "not a row\n"}
FAKE = Scenario(
    name="fetches_one",
    calls=["collect_fake one"],
    responses=[exchange("https://example.test/one", {"n": 1})],
    env={"DUMMY_TOKEN": "dummy"},
    config={"LABEL": "made-up"},
    seed=SEED,
)


@pytest.fixture
def stand_in(tmp_path: Path) -> Path:
    script = tmp_path / "stand-in.sh"
    script.write_text(STAND_IN, encoding="utf-8")
    return script


DISCOVERED = leadline.sources.discover()
SOURCES = list(DISCOVERED.sources.values())


def test_the_sources_found_are_the_scenarios_the_goldens_and_the_documented_sources() -> None:
    """Each source found has scenarios and a golden, and only health besides has them (the docs are generated from
    the sources found, and tests/test_source_docs.py holds them to it)."""
    found = list(DISCOVERED.sources)

    assert scenarios.sources() == sorted(found)
    assert scenarios.sets() == sorted([*found, *scenarios.NOT_SOURCES])
    assert all(scenarios.place(name).golden.is_file() for name in scenarios.sets())
    assert {p.stem for p in scenarios.GOLDEN.glob("*.json")} <= set(scenarios.sets()), "a golden with no scenarios"


def contract() -> list[str]:
    """The members every source has: those the Source protocol in the registry declares."""
    declared = [
        member
        for protocol in (leadline.sources.Spec, leadline.sources.Source)
        for member in (*protocol.__annotations__, *vars(protocol))
    ]
    return [name for name in dict.fromkeys(declared) if not name.startswith("_")]


def missing_members(source: object) -> list[str]:
    return [name for name in contract() if not hasattr(source, name)]


def test_every_source_loads_and_keeps_the_contract() -> None:
    assert DISCOVERED.failed == {}
    assert contract() == ["NAME", "DEFAULT_FILE", "validate", "files", "summary", "ENV", "LAGGING", "collect", "check"]
    assert {source.NAME: missing_members(source) for source in SOURCES} == {source.NAME: [] for source in SOURCES}


def test_a_source_without_a_summary_breaks_the_contract() -> None:
    without = types.SimpleNamespace(**{name: None for name in contract() if name != "summary"})

    assert missing_members(without) == ["summary"]


def scenario_config(name: str) -> Any:
    """The source's config for its scenarios: LEADLINE_CONFIG, or leadline_config of its first scenario."""
    module = scenarios.module(name)
    if hasattr(module, "LEADLINE_CONFIG"):
        return module.LEADLINE_CONFIG
    return module.leadline_config(scenarios.scenarios(name)[0])


@pytest.mark.parametrize("source", SOURCES, ids=lambda source: source.NAME)
def test_every_sources_summary_is_a_valid_report_config(source: leadline.sources.Source) -> None:
    cfg = scenario_config(source.NAME)
    assert source.validate(cfg) == []

    summary = source.summary(cfg)  # may be empty, for a source that reports on a platform, not the user's projects

    assert reports.validate(reports.default(summary, "health.jsonl")) == []


@pytest.mark.parametrize("source", SOURCES, ids=lambda source: source.NAME)
def test_every_sources_sections_have_its_readable_label(source: leadline.sources.Source) -> None:
    """The label trends heads a section's panel with: the same for every section of the source, never empty."""
    labels = {section.label for section in source.summary(scenario_config(source.NAME))}

    assert all(isinstance(label, str) and label for label in labels)
    assert len(labels) <= 1


@pytest.mark.parametrize("source", SOURCES, ids=lambda source: source.NAME)
def test_every_sources_sections_chart_the_fields_they_name_from_its_golden_rows(
    source: leadline.sources.Source, tmp_path: Path
) -> None:
    """Every golden's rows together, as trends reads them: each section it names charts a series for each field."""
    for golden in scenarios.load_golden(source.NAME).values():
        for file, text in golden["rows"].items():
            with (tmp_path / file).open("a", encoding="utf-8") as f:
                f.write(text)
    sections = source.summary(scenario_config(source.NAME))

    data = trends.build(files_in(tmp_path), date(2026, 9, 24), sections, "health.jsonl")

    assert all(shown.kind in SUMMARY_KINDS for section in sections for shown in section.shown)
    assert [section["title"] for section in data["sections"]] == [section.title for section in sections]
    charted = [section for section in data["sections"] if section["dates"]]
    assert bool(charted) == bool(sections), "a section with good rows"
    for section in charted:
        for field in (f for f in section["fields"] if not f.get("since")):  # a since chart counts days since a start
            assert len(field["other"] if field["kind"] == "breakdown" else field["values"]) == len(section["dates"])


@pytest.mark.parametrize("name", scenarios.sets())
def test_every_scenario_has_a_current_golden_and_every_golden_a_scenario(name: str) -> None:
    """A golden is current when it was made from the scenario as it is now: edit one, and `make goldens`."""
    defined = scenarios.scenarios(name)
    names = [s.name for s in defined]
    golden = scenarios.load_golden(name)

    assert len(set(names)) == len(names), "scenario names repeat"
    assert list(golden) == names
    stale = [s.name for s in defined if golden[s.name]["case_sha256"] != scenarios.fingerprint(s)]
    assert stale == [], "stale goldens: run `make goldens`"
    reviewed = {s.name: s.expected for s in defined if s.expected is not None}
    assert {name: golden[name]["rows"] for name in reviewed} == reviewed, "a reviewed golden is its expected rows"


REVIEWED = Scenario(
    name="reviewed_rows",
    calls=[],
    responses=FAKE.responses,
    expected={"fake.jsonl": '{"date": "2026-09-23", "got": 1}\n'},
)


def test_a_scenario_with_reviewed_rows_is_its_own_golden_without_running_the_script(tmp_path: Path) -> None:
    """A source the script doesn't have gets its golden from rows reviewed by hand; nothing runs."""
    no_script = tmp_path / "absent.sh"

    assert scenarios.golden([REVIEWED], no_script) == {
        "reviewed_rows": {"case_sha256": scenarios.fingerprint(REVIEWED), "rows": REVIEWED.expected}
    }


def test_the_fingerprint_follows_the_reviewed_rows() -> None:
    edited = dataclasses.replace(REVIEWED, expected={"fake.jsonl": '{"date": "2026-09-23", "got": 2}\n'})

    assert scenarios.fingerprint(edited) != scenarios.fingerprint(REVIEWED)


@pytest.mark.parametrize(
    "change",
    [{"calls": ["collect_fake one"]}, {"expected": None}],
    ids=["both the script's calls and reviewed rows", "neither"],
)
def test_a_scenario_gives_the_scripts_calls_or_reviewed_rows(change: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="reviewed_rows: give the script's calls or reviewed expected rows"):
        dataclasses.replace(REVIEWED, **change)  # type: ignore[arg-type]


@script_side
def test_run_returns_every_row_file_the_script_leaves(stand_in: Path) -> None:
    rows = scenarios.run(FAKE, stand_in)

    assert rows == {
        "fake.jsonl": '{"date": "2026-09-22"}\n'
        '{"date": "2026-09-23", "label": "made-up", "got": {"n":1}, "token": "dummy"}\n',
        "other.jsonl": "not a row\n",
    }


@script_side
@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        ({"responses": []}, "no saved response: GET https://example.test/one"),
        ({"calls": ["exit 3"], "responses": []}, "exit status 3"),
        (
            {"responses": [*FAKE.responses, exchange("https://example.test/two", {})]},
            "never served: GET https://example.test/two",
        ),
        ({"calls": ["collect_fake one", "collect_fake one"]}, "no saved response: GET https://example.test/one"),
        (
            {"responses": [*FAKE.responses, *FAKE.responses]},
            "never served: GET https://example.test/one",
        ),
    ],
    ids=[
        "a request with no saved response",
        "a failed run",
        "a response never served",
        "a request asked more often than its responses",
        "a request asked less often than its responses",
    ],
)
def test_run_refuses(stand_in: Path, change: dict[str, object], refusal: str) -> None:
    with pytest.raises(RuntimeError, match=refusal):
        scenarios.run(dataclasses.replace(FAKE, **change), stand_in)  # type: ignore[arg-type]


@script_side
def test_the_responses_for_one_request_are_served_in_order(stand_in: Path) -> None:
    url = "https://example.test/one"
    retried = dataclasses.replace(
        FAKE,
        calls=["collect_fake one || collect_fake one"],
        responses=[exchange(url, "", status=503), exchange(url, {"n": 2})],
    )

    assert '"got": {"n":2}' in scenarios.run(retried, stand_in)["fake.jsonl"]


def test_the_case_folder_gives_the_harness_the_clock_and_its_zone(tmp_path: Path) -> None:
    zoned = dataclasses.replace(FAKE, tz="Asia/Kolkata")

    spec = json.loads((scenarios.write_case(zoned, tmp_path / "case") / "case.json").read_text(encoding="utf-8"))

    assert (spec["now"], spec["tz"]) == (scenarios.NOW, "Asia/Kolkata")
    assert FAKE.tz == "UTC"


@script_side
def test_golden_holds_each_scenarios_rows_and_fingerprint(stand_in: Path) -> None:
    quiet = dataclasses.replace(FAKE, name="writes_nothing", calls=["true"], responses=[], seed={})

    assert scenarios.golden([FAKE, quiet], stand_in) == {
        "fetches_one": {"case_sha256": scenarios.fingerprint(FAKE), "rows": scenarios.run(FAKE, stand_in)},
        "writes_nothing": {"case_sha256": scenarios.fingerprint(quiet), "rows": {}},
    }


@pytest.mark.parametrize(
    "change",
    [
        {"calls": ["collect_fake two"]},
        {"responses": [exchange("https://example.test/one", {"n": 2})]},
        {"env": {}},
        {"config": {}},
        {"seed": {}},
        {"now": "2026-09-24T00:00:00"},
        {"tz": "Asia/Kolkata"},
    ],
    ids=["calls", "responses", "env", "config", "seed", "now", "tz"],
)
def test_the_fingerprint_follows_every_input_but_the_name(change: dict[str, object]) -> None:
    assert scenarios.fingerprint(dataclasses.replace(FAKE, name="renamed")) == scenarios.fingerprint(FAKE)
    assert scenarios.fingerprint(dataclasses.replace(FAKE, **change)) != scenarios.fingerprint(FAKE)  # type: ignore[arg-type]


def test_pages_follow_the_apis_links() -> None:
    """A list longer than a page is split as the API splits it: next on every page but the last."""
    pages = github_issue_pages("solentlabs/example", "open", ISSUES)

    assert [len(json.loads(p["response"]["body"])) for p in pages] == [100, 100, 55]
    assert [p["request"]["url"].rsplit("page=", 1)[1] for p in pages] == ["1", "2", "3"]
    assert ['rel="next"' in p["response"].get("headers", {}).get("link", "") for p in pages] == [True, True, False]
    assert github_issue_pages("solentlabs/example", "open", [])[0]["response"] == {"status": 200, "body": "[]"}


# A set of scenarios as a source's or API's package holds it: tests/scenarios.py and tests/golden.json, found with
# the flat sets (tests/scenarios/<name>.py, tests/scenarios/golden/<name>.json) by one lookup.

PACKAGED = (
    "from scenarios import Scenario\n"
    "SCENARIOS = [Scenario(name='one', calls=[], expected={'x.jsonl': '{}\\n'})]\n"
    "DIVERGENCES = {'one': 'a decision'}\n"
)


def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **more: str) -> Path:
    files = {
        "sources/x/tests/scenarios.py": PACKAGED,
        "sources/g/gx/tests/scenarios.py": PACKAGED,
        "sources/y/tests/shapes.py": "KINDS = []\n",
        "sources/z/tests/test_z.py": "",
        **more,
    }
    return make(tmp_path, monkeypatch, files)


def test_the_sets_are_the_flat_modules_and_the_packages_scenarios_found_by_walking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flat = [name for name, place in scenarios.places().items() if place.golden.parent == scenarios.GOLDEN]
    scratch(tmp_path, monkeypatch)

    assert scenarios.sets() == sorted([*flat, "gx", "x"])
    assert scenarios.sources() == sorted([*(n for n in flat if n not in scenarios.NOT_SOURCES), "gx", "x"])


def test_a_package_set_has_its_module_and_its_golden_beside_its_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = scratch(tmp_path, monkeypatch)

    assert scenarios.place("x").golden == package / "sources" / "x" / "tests" / "golden.json"
    assert scenarios.place("gx").golden == package / "sources" / "g" / "gx" / "tests" / "golden.json"
    assert scenarios.place("health").module == "scenarios.health"
    assert scenarios.place("health").golden == scenarios.GOLDEN / "health.json"
    assert scenarios.module("x").__name__ == f"{package.name}.sources.x.tests.scenarios"
    assert [s.name for s in scenarios.scenarios("x")] == ["one"]


def test_a_package_set_writes_its_golden_beside_its_tests_and_loads_it_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch(tmp_path, monkeypatch)

    written = scenarios.write_golden("x")

    assert written == scenarios.place("x").golden
    assert scenarios.load_golden("x") == scenarios.golden(scenarios.scenarios("x"))
    assert scenarios.load_golden("x")["one"]["rows"] == {"x.jsonl": "{}\n"}


def test_a_set_found_in_both_places_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scratch(tmp_path, monkeypatch, **{"sources/health/tests/scenarios.py": PACKAGED})

    with pytest.raises(ValueError, match="health"):
        scenarios.sets()


def test_the_files_a_package_holds_by_name_are_found_by_walking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = scratch(tmp_path, monkeypatch)

    found = scenarios.package_files("shapes")

    assert found == {"y": (f"{package.name}.sources.y.tests.shapes", package / "sources/y/tests/shapes.py")}


def test_python_m_scenarios_finds_the_sets_in_both_places(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scratch(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.argv", ["scenarios", "x"])

    runpy.run_module("scenarios", run_name="__main__")

    assert scenarios.load_golden("x")["one"]["rows"] == {"x.jsonl": "{}\n"}
