"""Scenarios: each source's test cases, made up in code, and the rows the script writes for each.

A scenario is what one collector run needs: the script's calls, dummy credentials, overrides for the
script's constants, row files already in the metrics folder, and the responses, built by the builders of
the platform's package (sources/<platform>/tests/builders.py). Every value is made up, so no real data reaches
the repo. Each source's scenarios are the list SCENARIOS in its package's tests/scenarios.py; the rows the script
writes for them are that package's tests/golden.json:

    {"<scenario>": {"case_sha256": "<fingerprint of the scenario>", "rows": {"<file>.jsonl": "<text>"}}}

`rows` is every row file in the metrics folder after the run, seed files included, with its exact
text (`captured_at` too: the clock is frozen at the scenario's `now`, in its `tz`). `case_sha256` ties
a golden to the scenario it came from, so a test without the script can tell a golden is stale.

Health (health.py) has scenarios too, though it isn't a source: the script's freshness check run on
seeded row files, whose golden is health.jsonl as the script writes it.

What the script doesn't do has no oracle (a source it doesn't have, or a source's own behavior): such
scenarios give no calls, and carry instead the rows the source must leave (`expected`, in the same form as
`rows`), written and reviewed by hand.
Their golden is those rows, and making it runs nothing.

`make goldens` (`python -m scenarios [SOURCE ...]`) reruns the script on every scenario, or on those
of the sources named, and rewrites the golden files. Design: docs/TESTING.md, "Scenarios".
"""

from __future__ import annotations

import hashlib
import importlib
import json
import pkgutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import script_harness

import leadline
from scenarios.builders.common import Exchange

GOLDEN = Path(__file__).resolve().parent / "golden"  # the goldens of the sets here (health's)
PACKAGE = Path(leadline.__file__).resolve().parent  # where the source and API packages are walked for their sets
HELPERS = ("__main__", "builders")  # the modules here that hold no scenarios
NOT_SOURCES = ("health",)  # the scenario sets that test something other than a source
NOW = "2026-09-23T23:31:05"  # the frozen local time every scenario runs at unless it sets its own

# Dummy credentials. Every scenario gets them unless it sets `env`, so the script's requests carry them.
CREDENTIALS = {
    "GITHUB_TOKEN": "dummy-github-token-0001",
    "CLOUDFLARE_API_TOKEN": "dummy-cloudflare-token-0002",
    "GOATCOUNTER_TOKEN": "dummy-goatcounter-token-0003",
    "CLOUDFLARE_ACCOUNT_ID": "dummy0cloudflare0account0id",
}

Rows = Dict[str, str]  # a row file's name and its text
Golden = Dict[str, Dict[str, Any]]  # a scenario's name: {"case_sha256": ..., "rows": Rows}


@dataclass(frozen=True)
class Scenario:
    name: str  # what the scenario proves, as an identifier
    calls: Sequence[str]  # the script's bash lines to run, each as main runs a collector
    responses: Sequence[Exchange] = ()  # saved responses, each served once; several for one request in order
    env: Mapping[str, str] = field(default_factory=lambda: dict(CREDENTIALS))
    config: Mapping[str, str | Sequence[str]] = field(default_factory=dict)  # the script's constants
    seed: Mapping[str, str] = field(default_factory=dict)  # row files in the metrics folder before the run
    now: str = NOW
    tz: str = script_harness.TZ  # the zone `now` is local time in, as a TZ name
    expected: Rows | None = None  # for what the script doesn't do: the reviewed rows, in place of calls

    def __post_init__(self) -> None:
        if bool(self.calls) == (self.expected is not None):
            raise ValueError(f"{self.name}: give the script's calls or reviewed expected rows, one of the two")


@dataclass(frozen=True)
class Place:
    module: str  # the dotted name of the module holding the set's SCENARIOS
    golden: Path  # the set's golden file


def package_files(stem: str) -> dict[str, tuple[str, Path]]:
    """The file tests/<stem>.py of every source and platform group, found by walking sources/ to any depth: the
    folder's name, the file's dotted module name and its path."""
    found: dict[str, tuple[str, Path]] = {}
    for path in sorted((PACKAGE / "sources").rglob(f"tests/{stem}.py")):
        name = path.parent.parent.name
        if name in found:
            raise ValueError(f"{name} has tests/{stem}.py twice: {found[name][1]} and {path}")
        found[name] = (".".join(path.relative_to(PACKAGE.parent).with_suffix("").parts), path)
    return found


def places() -> dict[str, Place]:
    """Every set of scenarios, with where its module and golden are: a module here with golden/<name>.json (health's),
    or a package's tests/scenarios.py with its tests/golden.json. A set found in both places is refused."""
    flat = {
        m.name: Place(f"{__name__}.{m.name}", GOLDEN / f"{m.name}.json")
        for m in pkgutil.iter_modules(__path__)
        if m.name not in HELPERS
    }
    packaged = {
        name: Place(module, path.with_name("golden.json"))
        for name, (module, path) in package_files("scenarios").items()
    }
    twice = sorted(flat.keys() & packaged.keys())
    if twice:
        raise ValueError(f"scenarios in both places: {', '.join(twice)}")
    return {**flat, **packaged}


def place(name: str) -> Place:
    return places()[name]


def sets() -> list[str]:
    """Every set of scenarios, each with its golden: the modules in this package, apart from HELPERS, and the
    packages' tests/scenarios.py."""
    return sorted(places())


def sources() -> list[str]:
    """Every source with scenarios: the sets, apart from NOT_SOURCES."""
    return [name for name in sets() if name not in NOT_SOURCES]


def module(name: str) -> Any:
    """The module holding the set `name`: its SCENARIOS and whatever else the set defines."""
    return importlib.import_module(place(name).module)


def scenarios(source: str) -> list[Scenario]:
    found: list[Scenario] = module(source).SCENARIOS
    return found


def load_golden(source: str) -> Golden:
    loaded: Golden = json.loads(place(source).golden.read_text(encoding="utf-8"))
    return loaded


def _case(scenario: Scenario) -> dict[str, Any]:
    """The scenario's inputs: everything but its name, and `expected` only where it is given."""
    case = asdict(scenario)
    del case["name"]
    if case["expected"] is None:
        del case["expected"]  # so the fingerprints of the script's scenarios don't change with the field
    return case


def fingerprint(scenario: Scenario) -> str:
    return hashlib.sha256(json.dumps(_case(scenario), sort_keys=True).encode("utf-8")).hexdigest()


def write_case(scenario: Scenario, folder: Path) -> Path:
    """The scenario as a case folder the harness runs (docs/TESTING.md, "Case folders")."""
    case = _case(scenario)
    (folder / "responses").mkdir(parents=True)
    spec = {key: case[key] for key in ("now", "tz", "calls", "env", "config")}
    (folder / "case.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    for i, exchange in enumerate(case["responses"]):
        (folder / "responses" / f"{i:04d}.json").write_text(json.dumps(exchange, indent=2) + "\n", encoding="utf-8")
    if case["seed"]:
        (folder / "seed").mkdir()
        for name, text in case["seed"].items():
            (folder / "seed" / name).write_text(text, encoding="utf-8")
    return folder


def row_files(folder: Path) -> Rows:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.jsonl"))}


def _never_served(scenario: Scenario, state: Path) -> list[str]:
    """The saved responses the run never reached: for each request, those past the number it was served."""
    served_file = state / "served.json"
    served: dict[str, int] = json.loads(served_file.read_text(encoding="utf-8")) if served_file.exists() else {}
    seen: dict[str, int] = {}
    missed = []
    for exchange in scenario.responses:
        request = exchange["request"]
        key = json.dumps([request["method"], request["url"], request.get("body")])
        seen[key] = seen.get(key, 0) + 1
        if served.get(key, 0) < seen[key]:
            missed.append(f"{request['method']} {request['url']}")
    return missed


def run(scenario: Scenario, script: Path | None = None) -> Rows:
    """The row files the script leaves after running `scenario`, through the oracle harness.

    `script` is the real one (the `script` local setting) unless given. Refuses a run that exits
    nonzero, makes a request with no saved response, or never reaches one of the saved responses, so
    every response a scenario gives is one the script used.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        done = script_harness.run_script(write_case(scenario, work / "case"), work, script)
        problems = [f"no saved response: {request}" for request in done.unmatched]
        problems += [f"never served: {request}" for request in _never_served(scenario, work / "state")]
        if done.returncode != 0:
            problems.append(f"exit status {done.returncode}")
        if problems:
            raise RuntimeError(f"{scenario.name}: " + "; ".join(problems) + f"\n{done.output}")
        return row_files(done.metrics)


def golden(defined: Sequence[Scenario], script: Path | None = None) -> Golden:
    """Each scenario's golden: its fingerprint and the rows the script writes for it, or its reviewed rows."""
    return {
        s.name: {"case_sha256": fingerprint(s), "rows": dict(s.expected) if s.expected is not None else run(s, script)}
        for s in defined
    }


def write_golden(source: str, script: Path | None = None) -> Path:
    path = place(source).golden
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(golden(scenarios(source), script), indent=2) + "\n", encoding="utf-8")
    return path
