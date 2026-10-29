"""What every API's builders share: the response types and the harness's saved-response format.

`exchange` and `failed` wrap a request and its response in that format (docs/TESTING.md, "Case folders").
A builder module imports this one and no other builder module.
"""

from __future__ import annotations

import json
import zlib
from typing import Any, Callable, Dict, Mapping, Sequence

Exchange = Dict[str, Any]
Json = Dict[str, Any]


def exchange(
    url: str,
    body: Any,
    *,
    method: str = "GET",
    sent: str | None = None,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
) -> Exchange:
    """A request and its response. `body` is the response text, or a value sent as compact JSON."""
    text = body if isinstance(body, str) else json.dumps(body, separators=(",", ":"))
    response: dict[str, Any] = {"status": status, "body": text}
    if headers:
        response["headers"] = dict(headers)
    return {"request": {"method": method, "url": url, "body": sent}, "response": response}


def failed(url: str, reason: str = "connection refused", *, method: str = "GET", sent: str | None = None) -> Exchange:
    """A request whose connection fails: no status, no body."""
    return {"request": {"method": method, "url": url, "body": sent}, "response": {"error": reason}}


def failing_at(responses: Sequence[Exchange], url_end: str, failure: Callable[[str], Exchange]) -> list[Exchange]:
    """`responses` up to the one whose URL ends with `url_end`, which fails; the script asks for nothing after it."""
    at = next(i for i, e in enumerate(responses) if e["request"]["url"].endswith(url_end))
    return [*responses[:at], failure(responses[at]["request"]["url"])]


def thrice(response: Exchange) -> list[Exchange]:
    """`response` once for each of the three tries a request that waiting can fix gets (ADR: a request is tried up to
    three times)."""
    return [response] * 3


def retried(responses: Sequence[Exchange]) -> list[Exchange]:
    """`responses` with each answer waiting can fix (a connection failure, a 429 or a 5xx) twice more: the script asks
    once, leadline up to three times."""
    again = [r for r in responses if "error" in r["response"] or r["response"].get("status", 200) in RETRIED]
    return [*responses, *again, *again]


RETRIED = frozenset({429, *range(500, 600)})


def stable_id(text: str) -> int:
    """A made-up numeric ID, the same for the same text on every run."""
    return zlib.crc32(text.encode("utf-8")) % 10**9
