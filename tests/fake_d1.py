"""A stand-in for Cloudflare's D1 REST API: the real SQL on an in-memory SQLite behind D1's requests and answers.

It speaks the `query` endpoint as the documented API does (a statement `{sql, params}` or a batch of them, run as one
unit, answered with `result[].results` as objects) and enforces the limits the backend must live within: 100 bound
values and 100 KB of SQL in a statement, bound values of JSON types only (no bytes), a token on every request, and
foreign keys always on. `requests` records every request body; `fail_on` makes a given request fail.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from typing import Any

from leadline.http import HttpError, Response

BASE = "https://api.cloudflare.com/client/v4"
MAX_PARAMS = 100
MAX_SQL_BYTES = 100_000


class FakeD1:
    """A transport (`Http(..., transport=FakeD1(...))`) for one account's one database."""

    def __init__(self, account: str, database: str, token: str) -> None:
        self.url = f"{BASE}/accounts/{account}/d1/database/{database}/query"
        self.token = token
        self.requests: list[list[dict[str, Any]]] = []  # each request's statements
        self.fail_on: dict[int, tuple[int, str]] = {}  # request number (from 1) -> (status, body) it answers instead
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute(
            "CREATE TABLE _cf_KV (key TEXT PRIMARY KEY, value BLOB)"
        )  # D1's own table, there from the start

    def __call__(self, request: urllib.request.Request, _timeout: float) -> Response:
        url = request.full_url
        if request.get_header("Authorization") != f"Bearer {self.token}":
            raise HttpError(
                url, 403, json.dumps({"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]})
            )
        if url != self.url or request.get_method() != "POST":
            raise HttpError(
                url, 404, json.dumps({"success": False, "errors": [{"code": 7404, "message": "no such database"}]})
            )
        data = request.data
        assert isinstance(data, bytes)
        body = json.loads(data.decode("utf-8"))
        statements = body if isinstance(body, list) else [body]
        self.requests.append(statements)
        failed = self.fail_on.get(len(self.requests))
        if failed is not None:
            raise HttpError(url, failed[0], failed[1])
        for statement in statements:
            self._check(url, statement)
        results = self._run(url, statements)
        return Response(
            200, {}, json.dumps({"success": True, "errors": [], "messages": [], "result": results}).encode()
        )

    def _check(self, url: str, statement: dict[str, Any]) -> None:
        params = statement.get("params", [])
        if len(params) > MAX_PARAMS:
            raise HttpError(
                url, 400, self._error(7500, f"too many SQL variables: {len(params)} bound, the limit is {MAX_PARAMS}")
            )
        if len(statement["sql"].encode("utf-8")) > MAX_SQL_BYTES:
            raise HttpError(url, 400, self._error(7500, "statement too long"))
        if not all(value is None or isinstance(value, (str, int, float, bool)) for value in params):
            raise HttpError(url, 400, self._error(7400, "a bound value of a type D1 cannot take"))

    def _run(self, url: str, statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results = []
        self.db.execute("BEGIN")
        try:
            for statement in statements:
                cursor = self.db.execute(statement["sql"], statement.get("params", []))
                names = [d[0] for d in cursor.description or ()]
                rows = [dict(zip(names, row)) for row in cursor.fetchall()]
                results.append({"success": True, "results": rows, "meta": {"changes": cursor.rowcount}})
        except sqlite3.Error as e:
            self.db.execute("ROLLBACK")
            raise HttpError(url, 400, self._error(7500, str(e))) from e
        self.db.execute("COMMIT")
        return results

    @staticmethod
    def _error(code: int, message: str) -> str:
        return json.dumps({"success": False, "errors": [{"code": code, "message": message}], "result": []})

    def statements(self) -> list[str]:
        """The SQL of every statement received, in order."""
        return [s["sql"] for request in self.requests for s in request]
