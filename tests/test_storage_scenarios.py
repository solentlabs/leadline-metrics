"""Every source's scenarios, run with the rows kept in a SQLite database, give the goldens' rows.

The goldens are what the script wrote (or reviewed rows, for a source the script never had). The same run on the
JSONL backend matches them in each source's own tests; this runs all of them on the database backend, so the two
backends are shown to keep and return the same rows, whatever a source writes.
"""

from __future__ import annotations

from pathlib import Path

import port
import pytest
import scenarios

import leadline.sources
from leadline.storage.sqlite import Sqlite

SOURCES = scenarios.sources()
CASES = [(name, s.name) for name in SOURCES for s in port.golden_scenarios(name)]


@pytest.fixture(scope="module")
def found() -> leadline.sources.Registry[leadline.sources.Source]:
    return leadline.sources.discover()


@pytest.mark.parametrize(("name", "scenario"), CASES, ids=str)
def test_a_scenario_gives_the_goldens_rows_on_the_database_backend(
    found: leadline.sources.Registry[leadline.sources.Source], name: str, scenario: str, tmp_path: Path
) -> None:
    module = scenarios.module(name)
    config = (
        module.leadline_config(port.scenario(name, scenario))
        if hasattr(module, "leadline_config")
        else module.LEADLINE_CONFIG
    )
    backend = Sqlite(tmp_path / "leadline.db")

    port.run(found.sources[name], config, port.scenario(name, scenario), tmp_path / "out", backend)

    assert port.golden_differences(name, scenario, tmp_path / "out") == []
