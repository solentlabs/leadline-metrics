"""The `storage` setting: which backend a run keeps its row files in, and `leadline import`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from leadline import cli
from leadline.config import ConfigError, Storage, load
from leadline.context import open_context, open_report_context
from leadline.storage.jsonl import Jsonl
from leadline.storage.sqlite import Sqlite

ROW = '{"date": "2026-09-21", "n": 1}\n'


def write_config(tmp_path: Path, **settings: Any) -> Path:
    path = tmp_path / "conf" / "leadline.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"out_dir": "../out", **settings}), encoding="utf-8")
    return path


def test_without_a_storage_setting_the_row_files_are_jsonl_files_in_out_dir(tmp_path: Path) -> None:
    assert load(write_config(tmp_path), {}).storage == Storage("jsonl", None)


def test_a_sqlite_database_is_relative_to_the_config_file(tmp_path: Path) -> None:
    config = load(write_config(tmp_path, storage={"backend": "sqlite", "path": "../data/leadline.db"}), {})

    assert config.storage == Storage("sqlite", tmp_path / "conf" / "../data/leadline.db")


@pytest.mark.parametrize(
    ("storage", "message"),
    [
        ("sqlite", "storage must be a JSON object"),
        ({}, "storage.backend is required"),
        ({"backend": "oracle"}, "storage.backend: 'oracle' is not one of jsonl, sqlite"),
        ({"backend": "sqlite"}, "storage.path is required for sqlite"),
        ({"backend": "sqlite", "path": ""}, "storage.path must be a non-empty string"),
        ({"backend": "sqlite", "path": 3}, "storage.path must be a non-empty string"),
        ({"backend": "jsonl", "path": "x.db"}, "storage.path: jsonl keeps its files in out_dir"),
        ({"backend": "sqlite", "path": "x.db", "extra": 1}, "storage.extra: unknown key"),
    ],
)
def test_a_storage_setting_that_cannot_be_used_stops_the_run(tmp_path: Path, storage: Any, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load(write_config(tmp_path, storage=storage), {})


def test_a_run_keeps_its_row_files_in_the_configured_database(tmp_path: Path) -> None:
    config = load(write_config(tmp_path, storage={"backend": "sqlite", "path": "../leadline.db"}), {})

    with open_context(config) as ctx:
        ctx.file("a.jsonl").write_lines([ROW])

    assert Sqlite(tmp_path / "leadline.db").read_lines("a.jsonl") == [ROW]
    assert not (tmp_path / "out" / "a.jsonl").exists()
    assert (tmp_path / "out" / "leadline.log").exists()  # the log is still a file in out_dir


def test_a_report_reads_the_configured_database(tmp_path: Path) -> None:
    config = load(write_config(tmp_path, storage={"backend": "sqlite", "path": "../leadline.db"}), {})
    Sqlite(tmp_path / "leadline.db").write_lines("a.jsonl", [ROW])
    (tmp_path / "out").mkdir()

    with open_report_context(config) as ctx:
        assert ctx.file("a.jsonl").read_lines() == [ROW]


def test_a_database_that_cannot_be_opened_stops_the_run_before_it_starts(tmp_path: Path) -> None:
    (tmp_path / "taken").mkdir()
    config = load(write_config(tmp_path, storage={"backend": "sqlite", "path": "../taken"}), {})

    with pytest.raises(ConfigError, match=r"storage: .*taken"), open_context(config):
        pass


def test_a_run_writes_its_health_row_to_the_database(tmp_path: Path) -> None:
    path = write_config(tmp_path, storage={"backend": "sqlite", "path": "../leadline.db"})

    assert cli.main(["run", "--config", str(path)]) == 0

    assert Sqlite(tmp_path / "leadline.db").files() == ["health.jsonl"]
    assert not (tmp_path / "out" / "health.jsonl").exists()


def test_import_copies_the_jsonl_files_of_a_folder_into_the_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "old"
    Jsonl(folder).write_lines("a.jsonl", [ROW, "not json\n"])
    Jsonl(folder).write_lines("b.jsonl", [ROW])
    path = write_config(tmp_path, storage={"backend": "sqlite", "path": "../leadline.db"})

    assert cli.main(["import", "--config", str(path), "--from", str(folder)]) == 0

    database = Sqlite(tmp_path / "leadline.db")
    assert database.files() == ["a.jsonl", "b.jsonl"]
    assert database.read_lines("a.jsonl") == [ROW, "not json\n"]
    assert "2 file(s), 3 line(s)" in capsys.readouterr().out


def test_import_without_a_folder_reads_out_dir(tmp_path: Path) -> None:
    Jsonl(tmp_path / "out").write_lines("a.jsonl", [ROW])
    path = write_config(tmp_path, storage={"backend": "sqlite", "path": "../leadline.db"})

    assert cli.main(["import", "--config", str(path)]) == 0

    assert Sqlite(tmp_path / "leadline.db").read_lines("a.jsonl") == [ROW]


def test_import_into_jsonl_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["import", "--config", str(write_config(tmp_path))]) == 2

    assert "storage" in capsys.readouterr().err


def test_import_stops_when_the_folder_is_not_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_config(tmp_path, storage={"backend": "sqlite", "path": "../leadline.db"})

    assert cli.main(["import", "--config", str(path), "--from", str(tmp_path / "missing")]) == 2

    assert "not a folder" in capsys.readouterr().err


def test_import_refuses_a_file_the_database_already_holds_and_copies_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    newer = '{"date": "2026-09-22", "n": 2}\n'
    folder = tmp_path / "old"
    Jsonl(folder).write_lines("a.jsonl", [ROW])
    Jsonl(folder).write_lines("b.jsonl", [ROW])
    database = Sqlite(tmp_path / "leadline.db")
    database.write_lines("b.jsonl", [ROW, newer])  # a night the folder lacks
    path = write_config(tmp_path, storage={"backend": "sqlite", "path": "../leadline.db"})

    assert cli.main(["import", "--config", str(path), "--from", str(folder)]) == 2

    assert "b.jsonl" in capsys.readouterr().err
    assert database.files() == ["b.jsonl"]  # a.jsonl was not copied either
    assert database.read_lines("b.jsonl") == [ROW, newer]


def test_import_run_twice_stops_the_second_time(tmp_path: Path) -> None:
    Jsonl(tmp_path / "out").write_lines("a.jsonl", [ROW])
    path = write_config(tmp_path, storage={"backend": "sqlite", "path": "../leadline.db"})
    assert cli.main(["import", "--config", str(path)]) == 0
    Sqlite(tmp_path / "leadline.db").write_lines("a.jsonl", [ROW, '{"date": "2026-09-22", "n": 2}\n'])

    assert cli.main(["import", "--config", str(path)]) == 2

    assert len(Sqlite(tmp_path / "leadline.db").read_lines("a.jsonl")) == 2
