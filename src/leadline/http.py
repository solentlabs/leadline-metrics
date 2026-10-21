"""HTTP over urllib: user agent, timeout, retries, Link-header pagination, GraphQL, answers reused within a run.

It also reads an answer for a source: its JSON, and its counts, with the reason a source gives one it
can't use (UnusableError).

Every request goes through a transport, `urlopen` by default. Tests pass a
fake one, so no test touches the network. `SavedResponses` answers from saved
responses, which the port tests use.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.client import HTTPException
from typing import Any, Callable, Collection, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

from leadline.config import is_count

log = logging.getLogger("leadline")

TIMEOUT_S = 60
# Before retry n (1-based), wait n * RETRY_BACKOFF_S seconds, as the script does.
RETRY_BACKOFF_S = 10
# Errors waiting can't fix: the credentials are rejected, or the thing asked for doesn't exist. They
# are never retried, and a source marks one critical at once (docs/ARCHITECTURE_DECISIONS.md, "One
# failed night recovers; consecutive failures are critical"). A 403 that is a rate limit is not one of
# them, nor a status the request names recoverable for its API: HttpError.unfixable is the test.
UNFIXABLE = frozenset({401, 403, 404})

# What a failure's log line keeps of it: the body's first characters, and these response headers.
CAUSE_CHARS = 200
IDENTIFYING_HEADERS = ("server", "cf-ray", "date", "retry-after", "content-type")

_NEXT_LINK = re.compile(r'<([^>]*)>\s*;\s*rel="next"', re.IGNORECASE)


class HttpError(Exception):
    """A request failed: `code` is the HTTP status, or 0 when no response came back.

    `reason` is the response body, or the connection error's text, in full; `headers` the response's
    headers, names lower-cased (none when no response came back). `recoverable` holds the statuses the
    request named recoverable for its API (Http.request sets it).
    """

    def __init__(self, url: str, code: int, reason: str, headers: Mapping[str, str] | None = None) -> None:
        super().__init__(f"HTTP {code} from {_logged_url(url)}: {reason[:200]}")
        self.url = url
        self.code = code
        self.reason = reason
        self.headers = {name.lower(): value for name, value in (headers or {}).items()}
        self.recoverable: frozenset[int] = frozenset()

    @property
    def rate_limited(self) -> bool:
        """A spent rate limit, which waiting fixes: a 429, or a 403 that says so.

        A 403 says so with `Retry-After`, or with `X-RateLimit-Remaining: 0`; a 403 without either is
        a rejection.
        """
        if self.code == 429:
            return True
        remaining = self.headers.get("x-ratelimit-remaining", "").strip()
        return self.code == 403 and ("retry-after" in self.headers or remaining == "0")

    @property
    def unfixable(self) -> bool:
        """An error waiting can't fix: a status in UNFIXABLE that isn't a rate limit, nor named recoverable.

        Never retried; critical.
        """
        return self.code in UNFIXABLE and not self.rate_limited and self.code not in self.recoverable

    @property
    def detail(self) -> str:
        """The failure for a log line: `HTTP <code>: <cause>`, then the response's identifying headers if any.

        The cause is the connection error's text, or the response body, on one line and cut to CAUSE_CHARS.
        Only response headers appear (IDENTIFYING_HEADERS), never a request's.
        """
        cause = " ".join(self.reason.split())
        if not cause:
            cause = "no body"
        elif len(cause) > CAUSE_CHARS:
            cause = f"{cause[:CAUSE_CHARS]}..."
        shown = ", ".join(f"{name}={self.headers[name]}" for name in IDENTIFYING_HEADERS if name in self.headers)
        return f"HTTP {self.http_code}: {cause}" + (f" [{shown}]" if shown else "")

    @property
    def http_code(self) -> str:
        """`code` as a row gives it, curl's `%{http_code}` in the script: three digits, "000" for no response."""
        return f"{self.code:03d}"


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]  # names lower-cased
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


Transport = Callable[[urllib.request.Request, float], Response]


class UnusableError(Exception):
    """An answer a source can't use; the message names its kind and what is wrong, for the row's reason."""


def read_json(kind: str, response: Response) -> Any:
    """The answer's JSON; one that isn't JSON is unusable (`views response was not JSON`)."""
    try:
        return response.json()
    except ValueError:
        raise UnusableError(f"{kind} response was not JSON") from None


def unexpected(kind: str) -> UnusableError:
    """An answer that isn't the shape the source reads (`views response had an unexpected shape`)."""
    return UnusableError(f"{kind} response had an unexpected shape")


def counts(kind: str, answer: Any, fields: Mapping[str, str]) -> dict[str, int]:
    """Each of `fields` (the row's key -> the answer's) read from the object `answer`, 0 where absent.

    Raises `unexpected(kind)` unless `answer` is an object and each is a count.
    """
    if not isinstance(answer, dict):
        raise unexpected(kind)
    found = {key: answer.get(field, 0) for key, field in fields.items()}
    if not all(is_count(n) for n in found.values()):
        raise unexpected(kind)
    return found


def urlopen(request: urllib.request.Request, timeout: float) -> Response:
    """The real transport. Any failure, HTTP or connection, raises HttpError."""
    try:
        # Sources build every URL from https endpoints and the user's own config.
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            headers = {name.lower(): value for name, value in resp.headers.items()}
            return Response(resp.status, headers, resp.read())
    except urllib.error.HTTPError as e:
        headers = {name.lower(): value for name, value in e.headers.items()} if e.headers is not None else {}
        raise HttpError(request.full_url, e.code, e.read().decode("utf-8", "replace"), headers) from e
    except (OSError, HTTPException) as e:  # URLError, timeouts, resets; a body cut short, not HTTP
        raise HttpError(request.full_url, 0, str(e)) from e


Exchange = Mapping[str, Any]  # a saved response: {"request": {...}, "response": {...}}, as case folders hold them
_Key = Tuple[str, str, Optional[str]]  # a request: method, URL, body


class SavedResponses:
    """A transport answering from saved responses: matched on method, URL and body, each served once, in order.

    A request with none left fails as if offline (HttpError, code 0), is logged, and is recorded in
    `unmatched`; `never_served` names the responses no request reached.
    """

    def __init__(self, exchanges: Sequence[Exchange]) -> None:
        self._left: dict[_Key, list[Mapping[str, Any]]] = {}
        for exchange in exchanges:
            request = exchange["request"]
            key = (request["method"], request["url"], request.get("body"))
            self._left.setdefault(key, []).append(exchange["response"])
        self.unmatched: list[str] = []

    def __call__(self, request: urllib.request.Request, _timeout: float) -> Response:
        url, method = request.full_url, request.get_method()
        body = request.data.decode("utf-8") if isinstance(request.data, bytes) else None
        left = self._left.get((method, url, body))
        if not left:
            self.unmatched.append(f"{method} {url}")
            log.info("WARN: no saved response for %s %s; not sent", method, _logged_url(url))
            raise HttpError(url, 0, "no saved response (offline)")
        saved = left.pop(0)
        if "error" in saved:  # a connection that failed, whose text urllib gives as URLError's
            raise HttpError(url, 0, str(urllib.error.URLError(saved["error"])))
        status, text = saved["status"], saved["body"]
        headers = {name.lower(): value for name, value in saved.get("headers", {}).items()}
        if status >= 400:  # as urllib raises for an error status, with its headers
            raise HttpError(url, status, text, headers)
        return Response(status, headers, text.encode("utf-8"))

    def never_served(self) -> list[str]:
        return [f"{key[0]} {key[1]}" for key, left in self._left.items() for _ in left]


_CREDENTIAL_PARAM = re.compile(r"token|key|secret|pass|auth|sig|cred", re.IGNORECASE)


def _logged_url(url: str) -> str:
    """A URL as a log line names it: whole, except the value of a query parameter that looks like a credential
    (its name holds token, key, secret, pass, auth, sig or cred), which becomes an ellipsis."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    pairs = []
    for pair in parts.query.split("&"):
        name, sep, _ = pair.partition("=")
        pairs.append(f"{name}=…" if sep and _CREDENTIAL_PARAM.search(name) else pair)
    return urlunsplit(parts._replace(query="&".join(pairs)))


def _empty(response: Response) -> bool:
    """An answer with no body, or only newlines: a failure where a body is required."""
    return not response.body.strip(b"\n")


class Http:
    """A run's HTTP client, one per run (the context's): it keeps each successful answer to a GET.

    An identical GET later in the run (same URL and headers, names as given) is answered from what was
    kept, so two sources asking for the same thing make one request. A failure (an HttpError) is never
    kept, so a retry, or the next source's request, is sent; any answer the transport returns is kept,
    one its source can't read included. A POST, or a GET with a body, is always sent. Nothing outlives
    the client.
    """

    def __init__(
        self,
        user_agent: str,
        transport: Transport = urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.user_agent = user_agent
        self._transport = transport
        self._sleep = sleep
        self._kept: dict[tuple[str, tuple[tuple[str, str], ...]], Response] = {}

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        attempts: int = 1,
        require_body: bool = False,
        backoff_s: float = RETRY_BACKOFF_S,
        recoverable: Collection[int] = (),
    ) -> Response:
        """Send one request, trying up to `attempts` times. Raises the last HttpError.

        A failure that ends the request, its last or only attempt or an error waiting can't fix, is
        logged once with its cause (HttpError.detail) and the URL's host, then raised.

        Before retry n it waits n times `backoff_s`. An error waiting can't fix (HttpError.unfixable)
        is never retried; a rate limit is retried like any other failure, and so is a status in
        `recoverable`, which the source's API answers at times for a request waiting fixes: its error
        isn't unfixable. With `require_body`, an answer whose body is empty (or only newlines) is a
        failure too, raised as HttpError with its status, as the script counts one.
        A GET answered earlier in the run is answered again from what was kept, and logged as reused.
        """
        if attempts < 1:
            raise ValueError(f"attempts must be at least 1, not {attempts}")
        all_headers: dict[str, str] = {"User-Agent": self.user_agent, **(headers or {})}
        key = (url, tuple(sorted(all_headers.items()))) if method == "GET" and body is None else None
        kept = self._kept.get(key) if key is not None else None
        if kept is not None and not (require_body and _empty(kept)):
            log.info("Reused the answer to GET %s", _logged_url(url))
            return kept
        attempt = 1
        while True:
            request = urllib.request.Request(url, data=body, headers=all_headers, method=method)  # noqa: S310 (see urlopen)
            try:
                response = self._transport(request, TIMEOUT_S)
                if require_body and _empty(response):
                    raise HttpError(url, response.status, "empty response body")
                if key is not None:
                    self._kept[key] = response
                return response
            except HttpError as e:
                e.recoverable = frozenset(recoverable)
                if attempt == attempts or e.unfixable:
                    log.info(
                        "WARN: attempt %d/%d failed (%s), giving up: %s", attempt, attempts, e.detail, _logged_url(url)
                    )
                    raise
                wait = attempt * backoff_s
                log.info(
                    "WARN: attempt %d/%d failed (%s), retrying in %ds: %s",
                    attempt,
                    attempts,
                    e.detail,
                    wait,
                    _logged_url(url),
                )
                self._sleep(wait)
            attempt += 1

    def wait(self, seconds: float) -> None:
        """Pause between requests, for an API that wants them spaced (pypistats); tests record it, not sleep."""
        self._sleep(seconds)

    def get_json(self, url: str, *, headers: Mapping[str, str] | None = None, attempts: int = 1) -> Any:
        return self.request(url, headers=headers, attempts=attempts).json()

    def get_pages(self, url: str, *, headers: Mapping[str, str] | None = None, attempts: int = 1) -> list[Any]:
        """GET a JSON array and every page after it (Link: rel="next"), concatenated."""
        items: list[Any] = []
        next_url: str | None = url
        while next_url is not None:
            response = self.request(next_url, headers=headers, attempts=attempts)
            page = response.json()
            if not isinstance(page, list):
                raise ValueError(f"page {next_url} is not a JSON array")
            items.extend(page)
            match = _NEXT_LINK.search(response.headers.get("link", ""))
            next_url = match.group(1) if match else None
        return items

    def graphql(
        self,
        url: str,
        query: str,
        variables: Mapping[str, Any] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        attempts: int = 1,
    ) -> Any:
        """POST a GraphQL query; returns the whole reply, `errors` included, for the caller to judge."""
        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = dict(variables)
        body = json.dumps(payload).encode("utf-8")
        all_headers = {"Content-Type": "application/json", **(headers or {})}
        return self.request(url, method="POST", headers=all_headers, body=body, attempts=attempts).json()
