"""The local settings (tests/oracle/local_settings.py): where the oracle tests find what stays out of the repo."""

from __future__ import annotations

import json
from pathlib import Path

import local_settings
import pytest

# cspell:ignore scrpit


def write(tmp_path: Path, settings: object) -> Path:
    file = tmp_path / "local.json"
    file.write_text(json.dumps(settings), encoding="utf-8")
    return file


def test_a_setting_is_read_from_the_file(tmp_path: Path) -> None:
    file = write(tmp_path, {"script": "/somewhere/collect-metrics.sh", "capture": "~/capture"})

    assert local_settings.path("script", file) == Path("/somewhere/collect-metrics.sh")
    assert local_settings.path("capture", file) == Path.home() / "capture"


@pytest.mark.parametrize("settings", [None, {}, {"script": ""}], ids=["no file", "not in the file", "empty"])
def test_a_missing_setting_fails_naming_it_and_where_to_set_it(tmp_path: Path, settings: object) -> None:
    file = tmp_path / "local.json" if settings is None else write(tmp_path, settings)

    with pytest.raises(local_settings.MissingSettingError) as refused:
        local_settings.path("forbidden_terms", file)

    assert str(refused.value) == (
        f"the setting 'forbidden_terms' is not set: add it to {file} (an example: .leadline-local.example.json)"
    )


@pytest.mark.parametrize(
    ("settings", "refusal"),
    [(["script"], "a JSON object"), ({"scrpit": "x"}, "unknown setting 'scrpit'"), ({"script": 3}, "not a path")],
    ids=["not an object", "unknown name", "not a string"],
)
def test_a_file_it_cannot_use_is_refused(tmp_path: Path, settings: object, refusal: str) -> None:
    with pytest.raises(ValueError, match=refusal):
        local_settings.read(write(tmp_path, settings))


def test_the_file_is_the_repos_unless_the_environment_names_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LEADLINE_LOCAL_SETTINGS", raising=False)
    assert local_settings.settings_file() == local_settings.REPO / ".leadline-local.json"

    monkeypatch.setenv("LEADLINE_LOCAL_SETTINGS", str(tmp_path / "elsewhere.json"))
    assert local_settings.settings_file() == tmp_path / "elsewhere.json"


def test_the_example_names_every_setting_with_placeholders_only() -> None:
    example = json.loads((local_settings.REPO / ".leadline-local.example.json").read_text(encoding="utf-8"))

    assert sorted(example) == sorted(local_settings.SETTINGS)
    assert all(value.startswith("/path/to/") for value in example.values())


def test_the_real_file_is_ignored_by_git() -> None:
    assert ".leadline-local.json" in (local_settings.REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
