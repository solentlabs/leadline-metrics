"""Every source's scenarios, run with the rows kept in SQLite or in D1, give the goldens' rows.

The goldens are what the script wrote (or reviewed rows, for a source the script never had). The same run on the
JSONL backend matches them in each source's own tests; this runs all of them on the database backend, so the
backends are shown to keep and return the same rows, whatever a source writes. D1 is a fake of its REST API.
"""

from __future__ import annotations

from pathlib import Path

import port
import pytest
import scenarios
from fake_d1 import FakeD1

import leadline.sources
from leadline.http import Http
from leadline.storage import Backend
from leadline.storage.d1 import D1
from leadline.storage.sqlite import Sqlite


def sqlite(folder: Path) -> Backend:
    return Sqlite(folder / "leadline.db")


def d1(_folder: Path) -> Backend:
    return D1("account", "database", "t0ken", Http("agent", FakeD1("account", "database", "t0ken"), lambda _s: None))


BACKENDS = {"sqlite": sqlite, "d1": d1}
SOURCES = scenarios.sources()
CASES = [(kind, name, s.name) for kind in BACKENDS for name in SOURCES for s in port.golden_scenarios(name)]


@pytest.fixture(scope="module")
def found() -> leadline.sources.Registry[leadline.sources.Source]:
    return leadline.sources.discover()


@pytest.mark.parametrize(("kind", "name", "scenario"), CASES, ids=str)
def test_a_scenario_gives_the_goldens_rows_on_each_database_backend(
    found: leadline.sources.Registry[leadline.sources.Source], kind: str, name: str, scenario: str, tmp_path: Path
) -> None:
    module = scenarios.module(name)
    config = (
        module.leadline_config(port.scenario(name, scenario))
        if hasattr(module, "leadline_config")
        else module.LEADLINE_CONFIG
    )
    backend = BACKENDS[kind](tmp_path)

    port.run(found.sources[name], config, port.scenario(name, scenario), tmp_path / "out", backend)

    assert port.golden_differences(name, scenario, tmp_path / "out") == []
