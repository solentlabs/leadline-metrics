"""The `storage` setting: which backend a run keeps its row files in, and `leadline import`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fake_d1 import FakeD1

from leadline import cli, storage
from leadline.config import ConfigError, Storage, load
from leadline.context import open_context, open_report_context
from leadline.http import Http
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


D1 = {"backend": "d1", "account_id": "account", "database_id": "database"}


def test_a_d1_database_is_named_by_its_account_and_id(tmp_path: Path) -> None:
    config = load(write_config(tmp_path, storage=D1), {})

    assert config.storage == Storage("d1", None, "account", "database")


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
        ({"backend": "d1", "database_id": "d"}, "storage.account_id is required for d1"),
        ({"backend": "d1", "account_id": "a"}, "storage.database_id is required for d1"),
        ({**D1, "account_id": ""}, "storage.account_id must be a non-empty string"),
        ({**D1, "path": "x.db"}, "storage.path: d1 keeps its files in D1"),
        ({"backend": "sqlite", "path": "x.db", "account_id": "a"}, "storage.account_id: only d1 takes it"),
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
    assert "3 line(s) in 2 file(s)" in capsys.readouterr().out


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


def test_import_refuses_a_file_with_a_night_the_folder_lacks_and_copies_nothing(
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


@pytest.fixture
def d1(monkeypatch: pytest.MonkeyPatch) -> FakeD1:
    """A D1 database that answers the backend, and the token in the environment."""
    fake = FakeD1("account", "database", "t0ken")
    monkeypatch.setenv("CLOUDFLARE_D1_TOKEN", "t0ken")
    monkeypatch.setattr(storage, "d1_http", lambda: Http("agent", fake, lambda _s: None))
    return fake


def test_a_run_keeps_its_row_files_in_d1(tmp_path: Path, d1: FakeD1) -> None:
    config = load(write_config(tmp_path, storage=D1), {})

    with open_context(config) as ctx:
        ctx.file("a.jsonl").write_lines([ROW])

    assert d1.db.execute("SELECT name FROM file").fetchall() == [("a.jsonl",)]
    assert not (tmp_path / "out" / "a.jsonl").exists()


def test_a_d1_run_without_its_token_stops_before_it_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_D1_TOKEN", raising=False)
    config = load(write_config(tmp_path, storage=D1), {})

    with pytest.raises(ConfigError, match="CLOUDFLARE_D1_TOKEN"), open_context(config):
        pass


def test_a_d1_token_the_api_rejects_stops_the_run_before_it_starts(
    tmp_path: Path, d1: FakeD1, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLOUDFLARE_D1_TOKEN", "someone-elses")
    config = load(write_config(tmp_path, storage=D1), {})

    with pytest.raises(ConfigError, match="rejected"), open_context(config):
        pass


def test_export_copies_the_configured_backend_into_jsonl_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_config(tmp_path, storage={"backend": "sqlite", "path": "../leadline.db"})
    Sqlite(tmp_path / "leadline.db").write_lines("a.jsonl", [ROW, "not json\n"])
    Sqlite(tmp_path / "leadline.db").write_lines("b.jsonl", [ROW])

    assert cli.main(["export", "--config", str(path), "--to", str(tmp_path / "copy")]) == 0

    assert Jsonl(tmp_path / "copy").read_lines("a.jsonl") == [ROW, "not json\n"]
    assert Jsonl(tmp_path / "copy").files() == ["a.jsonl", "b.jsonl"]
    assert "2 file(s), 3 line(s)" in capsys.readouterr().out


def test_export_from_d1(tmp_path: Path, d1: FakeD1) -> None:
    path = write_config(tmp_path, storage=D1)
    config = load(path, {})
    with open_context(config) as ctx:
        ctx.file("a.jsonl").write_lines([ROW])

    assert cli.main(["export", "--config", str(path), "--to", str(tmp_path / "copy")]) == 0

    assert Jsonl(tmp_path / "copy").read_lines("a.jsonl") == [ROW]


def test_export_refuses_a_folder_that_already_holds_a_file_of_that_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_config(tmp_path, storage={"backend": "sqlite", "path": "../leadline.db"})
    Sqlite(tmp_path / "leadline.db").write_lines("a.jsonl", [ROW])
    Jsonl(tmp_path / "copy").write_lines("a.jsonl", [ROW, ROW.replace("09-21", "09-22")])

    assert cli.main(["export", "--config", str(path), "--to", str(tmp_path / "copy")]) == 2

    assert "a.jsonl" in capsys.readouterr().err
    assert len(Jsonl(tmp_path / "copy").read_lines("a.jsonl")) == 2


def test_export_from_jsonl_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["export", "--config", str(write_config(tmp_path)), "--to", str(tmp_path / "copy")]) == 2

    assert "storage" in capsys.readouterr().err


# --- a resumable import: it continues where it stopped, within a limit, and never erases ---

SIX = [f'{{"date": "2026-09-{day:02d}", "n": {day}, "tags": ["a", "b"]}}\n' for day in range(1, 7)]  # 5 values a line


def sqlite_config(tmp_path: Path) -> Path:
    return write_config(tmp_path, storage={"backend": "sqlite", "path": "../leadline.db"})


def test_import_within_a_limit_takes_what_fits_and_the_next_run_continues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "old"
    Jsonl(folder).write_lines("a.jsonl", SIX)
    path = sqlite_config(tmp_path)

    assert cli.main(["import", "--config", str(path), "--from", str(folder), "--max-values", "12"]) == 0

    assert Sqlite(tmp_path / "leadline.db").read_lines("a.jsonl") == SIX[:2]  # 5 + 5, a third line would pass 12
    assert "4 line(s) remain" in capsys.readouterr().out
    assert cli.main(["import", "--config", str(path), "--from", str(folder), "--max-values", "12"]) == 0
    assert Sqlite(tmp_path / "leadline.db").read_lines("a.jsonl") == SIX[:4]
    assert cli.main(["import", "--config", str(path), "--from", str(folder)]) == 0
    assert Sqlite(tmp_path / "leadline.db").read_lines("a.jsonl") == SIX
    assert cli.main(["import", "--config", str(path), "--from", str(folder)]) == 0
    assert "everything is imported" in capsys.readouterr().out


def test_import_always_takes_a_line_that_alone_is_over_the_limit(tmp_path: Path) -> None:
    folder = tmp_path / "old"
    Jsonl(folder).write_lines("a.jsonl", SIX)

    assert (
        cli.main(["import", "--config", str(sqlite_config(tmp_path)), "--from", str(folder), "--max-values", "1"]) == 0
    )

    assert Sqlite(tmp_path / "leadline.db").read_lines("a.jsonl") == SIX[:1]  # a line cannot be split; progress is made


def test_import_leaves_health_jsonl_out(tmp_path: Path) -> None:
    folder = tmp_path / "old"
    Jsonl(folder).write_lines("a.jsonl", [ROW])
    Jsonl(folder).write_lines("health.jsonl", [ROW])

    assert cli.main(["import", "--config", str(sqlite_config(tmp_path)), "--from", str(folder)]) == 0

    assert Sqlite(tmp_path / "leadline.db").files() == ["a.jsonl"]


def test_import_refuses_a_file_the_database_has_gone_on_from_and_copies_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "old"
    Jsonl(folder).write_lines("a.jsonl", SIX[:3])
    Jsonl(folder).write_lines("b.jsonl", SIX[:3])
    database = Sqlite(tmp_path / "leadline.db")
    database.write_lines("b.jsonl", [*SIX[:3], SIX[3]])  # a night the folder lacks

    assert cli.main(["import", "--config", str(sqlite_config(tmp_path)), "--from", str(folder)]) == 2

    assert "b.jsonl" in capsys.readouterr().err
    assert database.files() == ["b.jsonl"]  # a.jsonl was not copied either
    assert database.read_lines("b.jsonl") == SIX[:4]


def test_import_refuses_a_file_whose_start_differs_from_the_database(tmp_path: Path) -> None:
    folder = tmp_path / "old"
    Jsonl(folder).write_lines("a.jsonl", SIX[:3])
    database = Sqlite(tmp_path / "leadline.db")
    database.write_lines("a.jsonl", [SIX[0], SIX[2].replace('"n": 3', '"n": 99')])

    assert cli.main(["import", "--config", str(sqlite_config(tmp_path)), "--from", str(folder)]) == 2

    assert len(database.read_lines("a.jsonl")) == 2
