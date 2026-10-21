"""The oracle split (the repo root's conftest.py): a test without the `oracle` marker can't read what the local
settings name, and a run of the marked tests stops, naming what is missing, when a setting isn't set or names
nothing."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import conftest

REPO = Path(__file__).resolve().parents[1]
REFUSED = "is read by a test not marked oracle"


def test_the_guard_holds_every_path_the_local_settings_name(tmp_path: Path) -> None:
    """Loaded under a settings file of made-up paths, the guard holds exactly those paths."""
    names = {
        "script": tmp_path / "collect-metrics.sh",
        "capture": tmp_path / "capture",
        "forbidden_terms": tmp_path,
        "leadline_config": tmp_path / "leadline.json",
    }
    settings = tmp_path / "local.json"
    settings.write_text(json.dumps({name: str(path) for name, path in names.items()}), encoding="utf-8")
    show_held = "import conftest; print('\\n'.join(map(str, conftest.GUARD.held)))"

    done = subprocess.run(
        [sys.executable, "-c", show_held],
        cwd=REPO,
        env={
            **os.environ,
            "LEADLINE_LOCAL_SETTINGS": str(settings),
            "PYTHONPATH": os.pathsep.join(["tests/oracle"]),
        },
        capture_output=True,
        text=True,
        check=True,
    )

    assert set(done.stdout.split("\n")[:-1]) == {str(path.resolve()) for path in names.values()}


def test_a_test_without_the_marker_cannot_read_a_held_file_or_list_a_held_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script, capture = tmp_path / "collect-metrics.sh", tmp_path / "capture"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    capture.mkdir()
    monkeypatch.setattr(conftest.GUARD, "held", (script.resolve(), capture.resolve()))

    with pytest.raises(RuntimeError, match=REFUSED):
        script.read_bytes()
    with pytest.raises(RuntimeError, match=REFUSED):
        list(capture.iterdir())
    with pytest.raises(RuntimeError, match=REFUSED):
        (capture / "0001-main.json").read_bytes()


def oracle_run(settings: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "LEADLINE_LOCAL_SETTINGS": str(settings)}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "oracle", "--collect-only", "-p", "no:cacheprovider"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_oracle_run_stops_naming_each_setting_that_is_not_set(tmp_path: Path) -> None:
    settings = tmp_path / "local.json"
    settings.write_text(json.dumps({"capture": str(tmp_path)}), encoding="utf-8")

    done = oracle_run(settings)

    assert done.returncode == pytest.ExitCode.USAGE_ERROR, done.stdout + done.stderr
    for name in ("script", "forbidden_terms", "leadline_config"):
        assert f"the setting {name!r} is not set: add it to {settings}" in done.stdout
    assert "'capture'" not in done.stdout


def test_the_oracle_run_stops_naming_each_setting_that_names_nothing(tmp_path: Path) -> None:
    settings = tmp_path / "local.json"
    names = {
        "script": tmp_path / "collect-metrics.sh",
        "capture": tmp_path / "capture",
        "forbidden_terms": tmp_path,
        "leadline_config": tmp_path / "leadline.json",
    }
    settings.write_text(json.dumps({name: str(path) for name, path in names.items()}), encoding="utf-8")

    done = oracle_run(settings)

    assert done.returncode == pytest.ExitCode.USAGE_ERROR, done.stdout + done.stderr
    assert f"collect-metrics.sh is not at {names['script']} (the setting 'script')" in done.stdout
    assert f"the capture is not at {names['capture']} (the setting 'capture')" in done.stdout
    assert f"the leadline config is not at {names['leadline_config']} (the setting 'leadline_config')" in done.stdout
    assert "'forbidden_terms'" not in done.stdout
