"""The goldens are what the script writes: each scenario rerun through the real script gives its committed golden.

Needs the script, so it is marked oracle. `make goldens` rewrites them (tests/scenarios/__init__.py).
"""

from __future__ import annotations

import pytest
import scenarios
from scenarios import Scenario

pytestmark = pytest.mark.oracle

CASES = [(name, s) for name in scenarios.sets() for s in scenarios.scenarios(name)]


@pytest.mark.parametrize(("source", "scenario"), CASES, ids=[f"{source}/{s.name}" for source, s in CASES])
def test_the_golden_is_what_the_script_writes(source: str, scenario: Scenario) -> None:
    assert scenarios.golden([scenario]) == {scenario.name: scenarios.load_golden(source).get(scenario.name)}
