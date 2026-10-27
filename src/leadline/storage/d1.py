"""The Cloudflare D1 backend: the same tables as SQLite, written and read over D1's REST API.

D1 has no connection to open: every statement is an HTTPS request with an API token (`CLOUDFLARE_D1_TOKEN`, read by
`storage.open_backend`, never kept in the config or the log). The `query` endpoint takes a batch and runs it as one
unit, within D1's limits: 100 bound values and 100 KB of SQL in a statement, 30 seconds a request. A write bigger than
a request's share is sent in requests of `STATEMENTS` in order; one that stops part-way leaves a file that the next
write repairs, as it compares what is there by digest.

A free plan counts rows read and written, so a file's lines are read once a run and kept (`cached`): the run is the
only writer of its files, as `store` assumes.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Sequence

from leadline.http import Http, HttpError
from leadline.storage import StorageError
from leadline.storage.sql import Rows, SqlBackend, Statement, schema_statements

API = "https://api.cloudflare.com/client/v4"
STATEMENTS = 50  # statements in one request
ATTEMPTS = 3  # tries for a request that fails in a way waiting can fix
CREDENTIAL = "CLOUDFLARE_D1_TOKEN"


class D1(SqlBackend):
    """The files in one D1 database."""

    cached = True

    def __init__(self, account_id: str, database_id: str, token: str, http: Http) -> None:
        super().__init__()
        self.url = f"{API}/accounts/{account_id}/d1/database/{database_id}/query"
        self._account = account_id
        self._token = token
        self._http = http
        self._ready = False

    def _to_stored(self, data: bytes) -> str:
        return base64.b64encode(data).decode("ascii")  # a request body is JSON, which cannot carry bytes

    def _from_stored(self, value: Any) -> bytes:
        return base64.b64decode(str(value))

    def _run(self, statements: Sequence[Statement]) -> list[Rows]:
        if not self._ready:
            self._ensure_tables()
            self._ready = True
        return self._send(statements)

    def _ensure_tables(self) -> None:
        (names,) = self._send([("SELECT name FROM sqlite_master WHERE type = 'table'", ())])
        found = None
        if any(name == "schema_version" for (name,) in names):
            ((found,),) = self._send([("SELECT version FROM schema_version", ())])[0] or [(None,)]
        needed = schema_statements([name for (name,) in names], found, f"the D1 database of account {self._account}")
        if needed:
            self._send(needed)

    def _send(self, statements: Sequence[Statement]) -> list[Rows]:
        """The statements in requests of at most `STATEMENTS`, in order; each one's rows."""
        results: list[Rows] = []
        for start in range(0, len(statements), STATEMENTS):
            chunk = statements[start : start + STATEMENTS]
            body = json.dumps([{"sql": sql, "params": list(params)} for sql, params in chunk]).encode("utf-8")
            answer = self._post(body)
            if len(answer) != len(chunk):
                raise StorageError(f"D1 answered {len(answer)} results to {len(chunk)} statements")
            results += [[tuple(row.values()) for row in one.get("results") or []] for one in answer]
        return results

    def _post(self, body: bytes) -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        try:
            reply = self._http.request(self.url, method="POST", headers=headers, body=body, attempts=ATTEMPTS)
            answer = reply.json()
        except HttpError as e:
            raise StorageError(self._failure(e)) from e
        except ValueError as e:
            raise StorageError(f"D1 answered something that is not JSON: {e}") from e
        if not isinstance(answer, dict) or not answer.get("success"):
            raise StorageError(f"D1 refused the request: {_messages(answer)}")
        result = answer.get("result")
        return result if isinstance(result, list) else []

    def _failure(self, e: HttpError) -> str:
        if e.code in (401, 403):
            return f"D1 rejected the token in {CREDENTIAL} for account {self._account} ({_messages(e.reason)})"
        if e.code == 404:
            return f"D1 has no such database in account {self._account} ({_messages(e.reason)})"
        return f"D1 request failed: {e.detail}"


def _messages(body: Any) -> str:
    """The error messages in a D1 answer (a parsed one, or the text of one), or what it was."""
    try:
        parsed = json.loads(body) if isinstance(body, str) else body
        errors = parsed.get("errors") or []
        found = "; ".join(str(error.get("message")) for error in errors if isinstance(error, dict))
    except (ValueError, AttributeError):
        found = ""
    return found or str(body)[:200]
