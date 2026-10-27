"""The Cloudflare D1 backend: the same tables over D1's REST API, within its limits."""

# cspell:ignore caplog
from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from fake_d1 import FakeD1

from leadline.http import Http
from leadline.storage import StorageError, tables
from leadline.storage.d1 import D1

ROW = '{"date": "2026-09-21", "n": 1, "top_bots": [{"name": "0", "count": 14}, {"name": "1", "count": 2}]}\n'
TOKEN = "t0ken-secret-value"


def make(fake: FakeD1 | None = None, token: str = TOKEN) -> tuple[D1, FakeD1]:
    fake = fake or FakeD1("account", "database", TOKEN)
    return D1("account", "database", token, Http("agent", fake, lambda _s: None)), fake


def days(count: int) -> list[str]:
    return [
        json.dumps({"date": f"2026-{1 + n // 28:02d}-{1 + n % 28:02d}", "n": n, "tags": ["a", "b"]}) + "\n"
        for n in range(count)
    ]


def test_the_tables_are_made_on_first_access_and_d1s_own_are_left_alone() -> None:
    backend, fake = make()

    backend.read_lines("a.jsonl")

    names = {row[0] for row in fake.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"schema_version", "file", "field", "line", "value", "raw_line", "_cf_KV"} <= names
    assert fake.db.execute("SELECT version FROM schema_version").fetchall() == [(tables.VERSION,)]


def test_every_request_carries_the_token_and_it_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    backend, fake = make()
    with caplog.at_level(logging.DEBUG, logger="leadline"):
        backend.write_lines("a.jsonl", [ROW])
        backend.files()

    assert fake.requests  # it answered, so the token matched
    assert TOKEN not in caplog.text


def test_a_rejected_token_stops_the_run_and_names_the_variable_not_its_value() -> None:
    backend, _ = make(token="wrong-token-value")

    with pytest.raises(StorageError) as raised:
        backend.read_lines("a.jsonl")

    assert "CLOUDFLARE_D1_TOKEN" in str(raised.value)
    assert "wrong-token-value" not in str(raised.value)


def test_a_database_that_is_not_there_stops_the_run() -> None:
    fake = FakeD1("account", "elsewhere", TOKEN)
    backend = D1("account", "database", TOKEN, Http("agent", fake, lambda _s: None))

    with pytest.raises(StorageError, match="account"):
        backend.read_lines("a.jsonl")


def test_a_database_that_leadline_did_not_make_is_left_alone() -> None:
    backend, fake = make()
    fake.db.execute("CREATE TABLE notes (x TEXT)")

    with pytest.raises(StorageError, match="did not make"):
        backend.write_lines("a.jsonl", [ROW])


def test_a_database_made_by_a_newer_leadline_stops_the_run() -> None:
    backend, fake = make()
    backend.read_lines("a.jsonl")
    fake.db.execute("UPDATE schema_version SET version = ?", (tables.VERSION + 1,))

    with pytest.raises(StorageError, match="newer"):
        make(fake)[0].read_lines("a.jsonl")


def test_a_file_is_read_once_and_then_kept_for_the_run() -> None:
    backend, fake = make()
    backend.write_lines("a.jsonl", [ROW])
    sent = len(fake.requests)

    assert backend.read_lines("a.jsonl") == [ROW]
    assert backend.read_lines("a.jsonl") == [ROW]
    backend.write_lines("a.jsonl", [ROW, ROW.replace("09-21", "09-22")])
    assert backend.read_lines("a.jsonl") == [ROW, ROW.replace("09-21", "09-22")]

    assert not any("FROM value" in s for s in fake.statements()[sent:])  # no value was read back


def test_a_row_added_to_a_read_file_reads_no_values_and_sends_a_few_statements() -> None:
    backend, fake = make()
    backend.write_lines("a.jsonl", days(40))
    reader, fake_reader = make(fake)  # the next night: a new run, nothing kept
    reader.read_lines("a.jsonl")
    before = len(fake.requests)

    reader.write_lines("a.jsonl", [*days(40), days(41)[40]])

    sent = [s for request in fake.requests[before:] for s in request]
    assert len(sent) < 25  # the file's row, the fields it knows, and the new row's values
    assert not any("FROM value" in s["sql"] for s in sent)
    assert fake_reader is fake


def test_a_big_write_is_sent_in_requests_within_d1s_limits_and_reads_back() -> None:
    backend, fake = make()
    lines = days(120)

    backend.write_lines("a.jsonl", lines)

    assert len(fake.requests) > 3
    assert all(len(request) <= 50 for request in fake.requests)
    assert make(fake)[0].read_lines("a.jsonl") == lines


def test_a_failed_first_request_leaves_the_file_as_it_was() -> None:
    backend, fake = make()
    backend.write_lines("a.jsonl", [ROW])
    fresh, _ = make(fake)
    fresh.read_lines("a.jsonl")
    first = len(fake.requests) + 1
    fake.fail_on = dict.fromkeys(range(first, first + 3), (500, "boom"))  # every attempt

    with pytest.raises(StorageError):
        fresh.write_lines("a.jsonl", [ROW, ROW.replace("09-21", "09-22")])

    assert make(fake)[0].read_lines("a.jsonl") == [ROW]


def test_a_write_that_stopped_part_way_is_repaired_by_the_next() -> None:
    backend, fake = make()
    lines = days(120)
    fake.fail_on = dict.fromkeys((7, 8, 9), (500, "boom"))  # every attempt of the third request

    with pytest.raises(StorageError):
        backend.write_lines("a.jsonl", lines)
    again, _ = make(fake)
    again.write_lines("a.jsonl", lines)

    assert make(fake)[0].read_lines("a.jsonl") == lines


def test_a_rate_limit_is_retried() -> None:
    backend, fake = make()
    fake.fail_on = {2: (429, "slow down")}

    backend.write_lines("a.jsonl", [ROW])

    assert make(fake)[0].read_lines("a.jsonl") == [ROW]


def test_bytes_that_are_not_text_are_kept_as_text_a_json_request_can_carry() -> None:
    backend, fake = make()
    raw = '{"date":"2026-09-21","n":"\udcff"}\n'

    backend.write_lines("a.jsonl", [raw])

    assert make(fake)[0].read_lines("a.jsonl") == [raw]
    stored: Any = fake.db.execute("SELECT data FROM raw_line").fetchone()[0]
    assert isinstance(stored, str)
