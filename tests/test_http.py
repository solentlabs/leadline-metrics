"""http, driven through a fake transport: no test touches the network."""

from __future__ import annotations

import http.client as http_client
import io
import json
import logging
import threading
import urllib.error
import urllib.request
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from scenarios.builders.common import exchange, failed

from leadline import http
from leadline.http import Http, HttpError, Response


class FakeTransport:
    """Answers each request with the next reply, and records the requests."""

    def __init__(self, *replies: Response | HttpError) -> None:
        self.replies = list(replies)
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> Response:
        self.requests.append(request)
        self.timeouts.append(timeout)
        reply = self.replies.pop(0)
        if isinstance(reply, HttpError):
            raise reply
        return reply


def ok(body: Any, headers: dict[str, str] | None = None) -> Response:
    return Response(200, headers or {}, json.dumps(body).encode("utf-8"))


def client(transport: FakeTransport, sleeps: list[float] | None = None) -> Http:
    return Http("Agent/1.0", transport=transport, sleep=(sleeps if sleeps is not None else []).append)


def test_get_json_sends_user_agent_and_timeout() -> None:
    transport = FakeTransport(ok({"a": 1}))

    assert client(transport).get_json("https://x.test/a", headers={"Accept": "b"}) == {"a": 1}

    request = transport.requests[0]
    assert request.full_url == "https://x.test/a"
    assert request.get_method() == "GET"
    assert request.get_header("User-agent") == "Agent/1.0"
    assert request.get_header("Accept") == "b"
    assert transport.timeouts == [60]


def test_a_source_can_ask_for_one_try() -> None:
    transport = FakeTransport(HttpError("u", 503, "busy"), ok(1))

    with pytest.raises(HttpError) as raised:
        client(transport).get_json("https://x.test/u", attempts=1)

    assert (raised.value.code, raised.value.reason) == (503, "busy")
    assert len(transport.requests) == 1


def test_a_request_is_tried_three_times_by_default() -> None:
    """ADR: a request is tried up to three times unless its source says one."""
    sleeps: list[float] = []
    transport = FakeTransport(HttpError("u", 503, "a"), HttpError("u", 503, "b"), ok(1))

    assert client(transport, sleeps).get_json("https://x.test/u") == 1

    assert sleeps == [10, 20]
    assert len(transport.requests) == 3


@pytest.mark.parametrize("call", ["request", "get_pages"])
def test_every_way_to_ask_is_tried_three_times_by_default(call: str) -> None:
    sleeps: list[float] = []
    transport = FakeTransport(HttpError("u", 503, "a"), HttpError("u", 503, "b"), ok([1]))

    getattr(client(transport, sleeps), call)("https://x.test/u")

    assert (sleeps, len(transport.requests)) == ([10, 20], 3)


def test_graphql_is_tried_three_times_by_default() -> None:
    sleeps: list[float] = []
    transport = FakeTransport(HttpError("u", 503, "a"), HttpError("u", 503, "b"), ok({"data": {}}))

    client(transport, sleeps).graphql("https://x.test/g", "{ a }")

    assert (sleeps, len(transport.requests)) == ([10, 20], 3)


def test_an_error_waiting_cant_fix_is_still_tried_once_by_default() -> None:
    transport = FakeTransport(HttpError("u", 401, "no"), ok(1))

    with pytest.raises(HttpError):
        client(transport).get_json("https://x.test/u")

    assert len(transport.requests) == 1


def test_retries_with_growing_backoff_then_succeeds() -> None:
    sleeps: list[float] = []
    transport = FakeTransport(HttpError("u", 500, "a"), HttpError("u", 0, "timed out"), ok(7))

    assert client(transport, sleeps).get_json("https://x.test/u", attempts=3) == 7

    assert sleeps == [10, 20]
    assert len(transport.requests) == 3


def test_retries_exhausted_raise_the_last_failure() -> None:
    sleeps: list[float] = []
    transport = FakeTransport(HttpError("u", 500, "a"), HttpError("u", 502, "b"))

    with pytest.raises(HttpError) as raised:
        client(transport, sleeps).get_json("https://x.test/u", attempts=2)

    assert raised.value.code == 502
    assert sleeps == [10]


def test_a_source_can_set_the_backoff_between_attempts() -> None:
    """Before retry n it waits n times `backoff_s`, as the script's PyPI collector spaces its attempts."""
    sleeps: list[float] = []
    transport = FakeTransport(HttpError("u", 429, "a"), HttpError("u", 429, "b"), ok(7))

    assert client(transport, sleeps).request("https://x.test/u", attempts=3, backoff_s=60).json() == 7

    assert sleeps == [60, 120]


def test_wait_pauses_between_requests_through_the_clients_sleep() -> None:
    """A source spacing its requests (PyPI) waits here, so tests record the wait instead of sleeping."""
    sleeps: list[float] = []

    client(FakeTransport(), sleeps).wait(240)

    assert sleeps == [240]


def test_get_pages_follows_link_next_and_concatenates() -> None:
    transport = FakeTransport(
        ok([1, 2], {"link": '<https://x.test/p2>; rel="next", <https://x.test/p3>; rel="last"'}),
        ok([3], {"link": '<https://x.test/p1>; rel="prev", <https://x.test/p3>; rel="next"'}),
        ok([4], {"link": '<https://x.test/p1>; rel="first"'}),
    )

    assert client(transport).get_pages("https://x.test/p1") == [1, 2, 3, 4]

    assert [r.full_url for r in transport.requests] == ["https://x.test/p1", "https://x.test/p2", "https://x.test/p3"]


def test_get_pages_rejects_a_page_that_is_not_a_list() -> None:
    with pytest.raises(ValueError, match="not a JSON array"):
        client(FakeTransport(ok({"items": []}))).get_pages("https://x.test/u")


def test_graphql_posts_query_and_variables() -> None:
    transport = FakeTransport(ok({"data": {"x": 1}}))

    reply = client(transport).graphql("https://x.test/graphql", "query { x }", {"n": 2}, headers={"Authorization": "t"})

    assert reply == {"data": {"x": 1}}
    request = transport.requests[0]
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Authorization") == "t"
    assert isinstance(request.data, bytes)
    assert json.loads(request.data) == {"query": "query { x }", "variables": {"n": 2}}


def test_graphql_without_variables_sends_only_the_query() -> None:
    transport = FakeTransport(ok({"data": {}}))

    client(transport).graphql("https://x.test/u", "{ x }")

    assert isinstance(transport.requests[0].data, bytes)
    assert json.loads(transport.requests[0].data) == {"query": "{ x }"}


# The real transport, with urllib.request.urlopen replaced.


class FakeUrlopenResponse(io.BytesIO):
    status = 200

    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        super().__init__(body)
        self.headers = Message()
        for name, value in headers.items():
            self.headers[name] = value


def test_urlopen_turns_a_body_cut_short_into_code_0(monkeypatch: pytest.MonkeyPatch) -> None:
    class Cut:
        status = 200
        headers = Message()

        def __enter__(self) -> Cut:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            raise http_client.IncompleteRead(b"par", 100)

    monkeypatch.setattr(http._OPENER, "open", lambda request, timeout: Cut())

    with pytest.raises(HttpError) as raised:
        http.urlopen(urllib.request.Request("https://x.test"), 5)

    assert raised.value.code == 0


def test_urlopen_returns_status_lowercased_headers_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float] = []

    def fake(request: urllib.request.Request, timeout: float) -> FakeUrlopenResponse:
        seen.append(timeout)
        return FakeUrlopenResponse(b"[1]", {"Link": "<n>; rel=next", "Content-Type": "application/json"})

    monkeypatch.setattr(http._OPENER, "open", fake)

    response = http.urlopen(urllib.request.Request("https://x.test"), 5)

    assert response == Response(200, {"link": "<n>; rel=next", "content-type": "application/json"}, b"[1]")
    assert response.json() == [1]
    assert seen == [5]


def test_urlopen_turns_an_http_error_into_code_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(request: urllib.request.Request, timeout: float) -> None:
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", Message(), io.BytesIO(b"no such repo"))

    monkeypatch.setattr(http._OPENER, "open", fake)

    with pytest.raises(HttpError) as raised:
        http.urlopen(urllib.request.Request("https://x.test/r"), 5)

    assert (raised.value.url, raised.value.code, raised.value.reason) == ("https://x.test/r", 404, "no such repo")


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("refused"),
        TimeoutError("timed out"),
        http_client.IncompleteRead(b"ab", 5),
        http_client.BadStatusLine("not http"),
    ],
    ids=["refused", "timed out", "a body cut short", "a reply that is not HTTP"],
)
def test_urlopen_turns_a_connection_failure_into_code_0(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def fake(request: urllib.request.Request, timeout: float) -> None:
        raise error

    monkeypatch.setattr(http._OPENER, "open", fake)

    with pytest.raises(HttpError) as raised:
        http.urlopen(urllib.request.Request("https://x.test"), 5)

    assert raised.value.code == 0
    assert raised.value.reason == str(error)


@pytest.mark.parametrize("attempts", [0, -1])
def test_attempts_below_one_is_an_error_before_anything_is_sent(attempts: int) -> None:
    transport = FakeTransport()

    with pytest.raises(ValueError, match=f"attempts must be at least 1, not {attempts}"):
        client(transport).get_json("https://x.test/u", attempts=attempts)

    assert transport.requests == []


@pytest.mark.parametrize("code", sorted(http.UNFIXABLE))
def test_an_error_waiting_cant_fix_is_never_retried(code: int) -> None:
    sleeps: list[float] = []
    transport = FakeTransport(HttpError("u", code, "no"), ok(1))

    with pytest.raises(HttpError) as raised:
        client(transport, sleeps).get_json("https://x.test/u", attempts=3)

    assert raised.value.code == code
    assert len(transport.requests) == 1
    assert sleeps == []


def test_a_status_the_source_names_recoverable_is_retried_and_not_unfixable() -> None:
    """A source whose API answers a 404 that waiting fixes names it recoverable for its requests."""
    sleeps: list[float] = []
    transport = FakeTransport(HttpError("u", 404, "no"), HttpError("u", 404, "no"), HttpError("u", 404, "no"))

    with pytest.raises(HttpError) as raised:
        client(transport, sleeps).request("https://x.test/u", attempts=3, recoverable={404})

    assert (raised.value.code, raised.value.unfixable) == (404, False)
    assert sleeps == [10, 20]


@pytest.mark.parametrize("code", [401, 403])
def test_a_recoverable_status_leaves_the_others_unfixable(code: int) -> None:
    sleeps: list[float] = []
    transport = FakeTransport(HttpError("u", code, "no"), ok(1))

    with pytest.raises(HttpError) as raised:
        client(transport, sleeps).request("https://x.test/u", attempts=3, recoverable={404})

    assert (raised.value.code, raised.value.unfixable) == (code, True)
    assert (len(transport.requests), sleeps) == (1, [])


def test_unfixable_is_the_list_in_the_adr() -> None:
    assert frozenset({401, 403, 404}) == http.UNFIXABLE


@pytest.mark.parametrize("body", [b"", b"\n", b"\n\n"])
def test_require_body_retries_an_empty_success_and_fails_with_its_status(body: bytes) -> None:
    sleeps: list[float] = []
    transport = FakeTransport(Response(200, {}, body), Response(204, {}, b""), Response(200, {}, body))

    with pytest.raises(HttpError) as raised:
        client(transport, sleeps).request("https://x.test/u", attempts=3, require_body=True)

    assert (raised.value.code, raised.value.reason) == (200, "empty response body")
    assert sleeps == [10, 20]


def test_require_body_returns_the_first_answer_with_a_body() -> None:
    transport = FakeTransport(Response(200, {}, b""), Response(200, {}, b" "))

    assert client(transport).request("https://x.test/u", attempts=2, require_body=True).body == b" "


def test_an_empty_body_is_an_answer_unless_a_body_is_required() -> None:
    assert client(FakeTransport(Response(200, {}, b""))).request("https://x.test/u").body == b""


@pytest.mark.parametrize(("code", "text"), [(0, "000"), (404, "404"), (503, "503")])
def test_http_code_is_curls_three_digits(code: int, text: str) -> None:
    """The script's rows carry curl's %{http_code}: "000" when no response came back."""
    assert HttpError("https://example.invalid/", code, "").http_code == text


# A rate limit, which waiting fixes, told from a rejection, which it doesn't (ADR: one failed night
# recovers; consecutive failures are critical).


@pytest.mark.parametrize(
    ("code", "headers"),
    [
        (403, {"retry-after": "60"}),
        (403, {"x-ratelimit-remaining": "0"}),
        (403, {"Retry-After": "60", "X-RateLimit-Remaining": "12"}),
        (403, {"X-RateLimit-Remaining": "0"}),
        (429, {}),
        (429, {"retry-after": "30"}),
    ],
    ids=[
        "403 retry-after",
        "403 none remaining",
        "403 retry-after any case",
        "403 none remaining any case",
        "429",
        "429 retry-after",
    ],
)
def test_a_rate_limit_is_recoverable_never_unfixable(code: int, headers: dict[str, str]) -> None:
    error = HttpError("u", code, "limit spent", headers)

    assert (error.rate_limited, error.unfixable) == (True, False)


@pytest.mark.parametrize(
    ("code", "headers"),
    [
        (403, {}),
        (403, {"x-ratelimit-remaining": "4999"}),
        (401, {"retry-after": "60"}),
        (401, {"x-ratelimit-remaining": "0"}),
        (404, {"retry-after": "60"}),
    ],
    ids=["plain 403", "403 with budget left", "401 retry-after", "401 none remaining", "404 retry-after"],
)
def test_a_rejection_or_absence_is_unfixable_whatever_its_headers(code: int, headers: dict[str, str]) -> None:
    error = HttpError("u", code, "no", headers)

    assert (error.rate_limited, error.unfixable) == (False, True)


CHALLENGE = "<!DOCTYPE html><html><head><title>Just a moment...</title></head></html>"


@pytest.mark.parametrize(
    ("body", "headers"),
    [(CHALLENGE, {}), ("", {"CF-Mitigated": "challenge"}), (CHALLENGE, {"cf-mitigated": "challenge"})],
    ids=["the page", "the header", "both"],
)
def test_a_bot_challenge_is_recoverable_never_unfixable(body: str, headers: dict[str, str]) -> None:
    """Live 2026-10-21: the forum's host answered the NAS a 403 challenge page; no credential was wrong."""
    error = HttpError("u", 403, body, headers)

    assert (error.rate_limited, error.unfixable) == (False, False)


def test_a_403_page_that_is_not_a_challenge_is_still_unfixable() -> None:
    assert HttpError("u", 403, "<title>Forbidden</title>", {"cf-mitigated": "block"}).unfixable


@pytest.mark.parametrize("code", [0, 200, 500, 502, 503])
def test_other_failures_are_neither(code: int) -> None:
    error = HttpError("u", code, "x", {"retry-after": "5"})

    assert (error.rate_limited, error.unfixable) == (False, False)


def test_a_rate_limited_403_is_retried_like_any_recoverable_failure() -> None:
    sleeps: list[float] = []
    transport = FakeTransport(HttpError("u", 403, "limit", {"x-ratelimit-remaining": "0"}), ok(1))

    assert client(transport, sleeps).get_json("https://x.test/u", attempts=2) == 1

    assert sleeps == [10]


def test_urlopen_keeps_an_http_errors_headers_lowercased(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(request: urllib.request.Request, timeout: float) -> None:
        headers = Message()
        headers["X-RateLimit-Remaining"] = "0"
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", headers, io.BytesIO(b"rate limit exceeded"))

    monkeypatch.setattr(http._OPENER, "open", fake)

    with pytest.raises(HttpError) as raised:
        http.urlopen(urllib.request.Request("https://x.test/r"), 5)

    assert raised.value.headers == {"x-ratelimit-remaining": "0"}
    assert raised.value.rate_limited


# A run keeps each successful answer to a GET and serves it again to an identical GET (same URL and
# headers), so two sources asking for the same thing make one request (ADR: a run reuses its
# successful answers).


class Logged(logging.Handler):
    """The "leadline" logger's messages while in a `with` block."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.messages: list[str] = []
        self._log = logging.getLogger("leadline")

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())

    def __enter__(self) -> Logged:
        self._level = self._log.level
        self._log.setLevel(logging.INFO)
        self._log.addHandler(self)
        return self

    def __exit__(self, *_: object) -> None:
        self._log.removeHandler(self)
        self._log.setLevel(self._level)


def test_an_identical_get_is_answered_once_and_served_again() -> None:
    transport = FakeTransport(ok({"a": 1}))
    web = client(transport)

    first = web.get_json("https://x.test/a", headers={"Accept": "b"})
    again = web.get_json("https://x.test/a", headers={"Accept": "b"})

    assert first == again == {"a": 1}
    assert len(transport.requests) == 1


def test_a_reused_answer_is_logged_once_for_each_reuse() -> None:
    web = client(FakeTransport(ok(1)))
    with Logged() as logged:
        web.get_json("https://x.test/a")
        web.get_json("https://x.test/a")
        web.get_json("https://x.test/a")

    assert logged.messages == ["Reused the answer to GET https://x.test/a"] * 2


def test_a_failure_is_never_kept_so_the_next_request_is_sent() -> None:
    transport = FakeTransport(HttpError("u", 503, "busy"), ok(2))
    web = client(transport)

    with pytest.raises(HttpError):
        web.get_json("https://x.test/a", attempts=1)
    assert web.get_json("https://x.test/a") == 2

    assert len(transport.requests) == 2


def test_an_unfixable_failure_is_never_kept_either() -> None:
    transport = FakeTransport(HttpError("u", 404, "gone"), HttpError("u", 404, "gone"))
    web = client(transport)

    for _ in range(2):
        with pytest.raises(HttpError):
            web.get_json("https://x.test/a")

    assert len(transport.requests) == 2


def test_a_retry_after_a_failure_really_retries_and_its_success_is_kept() -> None:
    transport = FakeTransport(HttpError("u", 500, "a"), ok(3))
    web = client(transport)

    assert web.get_json("https://x.test/a", attempts=2) == 3
    assert web.get_json("https://x.test/a") == 3

    assert len(transport.requests) == 2


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ({"url": "https://x.test/a"}, {"url": "https://x.test/b"}),
        ({"url": "https://x.test/a?page=1"}, {"url": "https://x.test/a?page=2"}),
        ({"url": "https://x.test/a", "headers": {"Authorization": "one"}}, {"url": "https://x.test/a"}),
        (
            {"url": "https://x.test/a", "headers": {"Authorization": "one"}},
            {"url": "https://x.test/a", "headers": {"Authorization": "two"}},
        ),
    ],
    ids=["another url", "another query", "a header added", "another header value"],
)
def test_a_request_with_another_url_or_headers_is_sent(first: dict[str, Any], second: dict[str, Any]) -> None:
    transport = FakeTransport(ok(1), ok(2))
    web = client(transport)

    assert (web.get_json(**first), web.get_json(**second)) == (1, 2)

    assert len(transport.requests) == 2


def test_the_order_headers_are_given_in_is_not_part_of_the_request() -> None:
    transport = FakeTransport(ok(1))
    web = client(transport)

    web.get_json("https://x.test/a", headers={"A": "1", "B": "2"})
    web.get_json("https://x.test/a", headers={"B": "2", "A": "1"})

    assert len(transport.requests) == 1


def test_a_post_is_never_reused() -> None:
    """A POST's answer isn't the resource's to keep: GraphQL and SQL queries are sent every time."""
    transport = FakeTransport(ok({"data": 1}), ok({"data": 2}))
    web = client(transport)

    first = web.graphql("https://x.test/graphql", "{ a }")
    second = web.graphql("https://x.test/graphql", "{ a }")

    assert (first, second) == ({"data": 1}, {"data": 2})
    assert len(transport.requests) == 2


def test_a_get_answer_is_not_served_to_a_post_to_its_url() -> None:
    transport = FakeTransport(ok(1), ok(2))
    web = client(transport)

    web.get_json("https://x.test/a")

    assert web.request("https://x.test/a", method="POST", body=b"{}").json() == 2


def test_a_kept_empty_answer_is_not_served_where_a_body_is_required() -> None:
    transport = FakeTransport(Response(200, {}, b""), Response(200, {}, b"1"))
    web = client(transport)

    web.request("https://x.test/a")

    assert web.request("https://x.test/a", require_body=True).body == b"1"
    assert web.request("https://x.test/a").body == b"1"  # the answer with a body is the one kept
    assert len(transport.requests) == 2


def test_every_page_is_kept_so_a_second_read_sends_nothing() -> None:
    pages = [ok([1], {"link": '<https://x.test/p?page=2>; rel="next"'}), ok([2])]
    transport = FakeTransport(*pages)
    web = client(transport)

    assert web.get_pages("https://x.test/p") == web.get_pages("https://x.test/p") == [1, 2]

    assert len(transport.requests) == 2


def test_each_client_keeps_its_own_answers() -> None:
    """A run's answers live as long as its Http: the next run asks again."""
    transport = FakeTransport(ok(1), ok(2))

    assert client(transport).get_json("https://x.test/a") == 1
    assert client(transport).get_json("https://x.test/a") == 2


def test_a_get_with_a_body_is_never_reused() -> None:
    transport = FakeTransport(ok(1), ok(2))
    web = client(transport)

    web.request("https://x.test/a", body=b"{}")

    assert web.request("https://x.test/a", body=b"{}").json() == 2


# --- reading an answer a source can't use ---


def test_read_json_is_the_answers_json() -> None:
    assert http.read_json("views", Response(200, {}, b'{"count": 1}')) == {"count": 1}


@pytest.mark.parametrize("body", [b"", b"not json", b"\xff"], ids=["empty", "not json", "not utf-8"])
def test_an_answer_that_is_not_json_is_unusable_naming_its_kind(body: bytes) -> None:
    with pytest.raises(http.UnusableError, match=r"^views response was not JSON$"):
        http.read_json("views", Response(200, {}, body))


def test_an_unexpected_shape_is_unusable_naming_its_kind() -> None:
    assert str(http.unexpected("issues")) == "issues response had an unexpected shape"


def test_counts_reads_each_field_as_its_row_key() -> None:
    answer = {"count": 5, "uniques": 0, "other": "x"}

    assert http.counts("views", answer, {"views": "count", "views_unique": "uniques"}) == {
        "views": 5,
        "views_unique": 0,
    }


def test_counts_names_each_absent_field_so_the_row_says_what_to_look_for() -> None:
    """An answer without a count is not a count of zero (ADR: a failure is recorded as a failure)."""
    with pytest.raises(http.UnusableError) as raised:
        http.counts("views", {"count": 5}, {"views": "count", "views_unique": "uniques", "clones": "cloned"})

    assert str(raised.value) == "views response is missing uniques, cloned"


@pytest.mark.parametrize(
    "answer",
    [[], None, {"count": None}, {"count": "5"}, {"count": True}],
    ids=["a list", "null", "a null count", "a string count", "a boolean count"],
)
def test_counts_it_cant_read_are_unusable(answer: Any) -> None:
    with pytest.raises(http.UnusableError, match=r"^views response had an unexpected shape$"):
        http.counts("views", answer, {"views": "count"})


# --- saved responses: a transport that answers from them ---


def sent(url: str, method: str = "GET", body: bytes | None = None) -> urllib.request.Request:
    return urllib.request.Request(url, data=body, method=method)  # noqa: S310 (made-up https URLs)


def test_saved_responses_serve_each_once_in_order_matched_on_method_url_and_body() -> None:
    saved = http.SavedResponses(
        [
            exchange("https://x.test/a", "second"),
            exchange("https://x.test/a", "posted", method="POST", sent="q"),
            exchange("https://x.test/b", "other"),
            exchange("https://x.test/a", "third"),
        ]
    )

    answers = [
        saved(sent("https://x.test/a"), 60).body,
        saved(sent("https://x.test/a", "POST", b"q"), 60).body,
        saved(sent("https://x.test/a"), 60).body,
    ]

    assert answers == [b"second", b"posted", b"third"]
    assert saved.never_served() == ["GET https://x.test/b"]


def test_a_saved_error_status_raises_as_urlopen_does_with_its_headers() -> None:
    saved = http.SavedResponses([exchange("https://x.test/a", "slow down", status=429, headers={"Retry-After": "9"})])

    with pytest.raises(HttpError) as raised:
        saved(sent("https://x.test/a"), 60)

    assert (raised.value.code, raised.value.reason, raised.value.headers) == (429, "slow down", {"retry-after": "9"})


def test_a_saved_connection_failure_raises_code_0() -> None:
    saved = http.SavedResponses([failed("https://x.test/a", "timed out")])

    with pytest.raises(HttpError) as raised:
        saved(sent("https://x.test/a"), 60)

    assert (raised.value.code, raised.value.reason) == (0, "<urlopen error timed out>")


def test_a_request_with_nothing_saved_left_fails_as_offline_and_says_so() -> None:
    saved = http.SavedResponses([exchange("https://x.test/a", "only once")])
    saved(sent("https://x.test/a"), 60)

    with Logged() as logged, pytest.raises(HttpError) as raised:
        saved(sent("https://x.test/a"), 60)

    assert (raised.value.code, raised.value.reason) == (0, "no saved response (offline)")
    assert saved.unmatched == ["GET https://x.test/a"]
    assert logged.messages == ["WARN: no saved response for GET https://x.test/a; not sent"]


# A failure's cause stays in the log: the connection error's text, or the response's body and a few
# identifying headers, never a request header (docs/CORE.md, "Retries").


def test_a_connection_error_shows_its_text() -> None:
    assert HttpError("u", 0, "<urlopen error timed out>").detail == "HTTP 000: <urlopen error timed out>"


def test_a_status_shows_its_body_on_one_line_and_its_identifying_headers() -> None:
    headers = {"Server": "cloudflare", "CF-Ray": "8a1-AMS", "Retry-After": "30", "Set-Cookie": "id=secret"}

    detail = HttpError("u", 404, "<html>\n  Not   found\n</html>", headers).detail

    assert detail == "HTTP 404: <html> Not found </html> [server=cloudflare, cf-ray=8a1-AMS, retry-after=30]"


def test_a_long_body_is_cut() -> None:
    detail = HttpError("u", 500, "x" * 1000).detail

    assert detail == f"HTTP 500: {'x' * 200}..."


def test_an_empty_body_is_said_so() -> None:
    assert HttpError("u", 502, " \n").detail == "HTTP 502: no body"


def test_each_failed_attempt_is_logged_with_its_cause() -> None:
    web = client(FakeTransport(HttpError("u", 0, "timed out"), HttpError("u", 503, "gone", {"server": "nginx"}), ok(1)))

    with Logged() as logged:
        web.request("https://x.test/a", attempts=3)

    assert logged.messages == [
        "WARN: attempt 1/3 failed (HTTP 000: timed out), retrying in 10s: https://x.test/a",
        "WARN: attempt 2/3 failed (HTTP 503: gone [server=nginx]), retrying in 20s: https://x.test/a",
    ]


def test_a_request_header_never_reaches_the_log() -> None:
    web = client(FakeTransport(HttpError("u", 500, "oops"), ok(1)))

    with Logged() as logged:
        web.request("https://x.test/a", headers={"Authorization": "Bearer t0ken"}, attempts=2)

    assert "t0ken" not in "\n".join(logged.messages)


def test_a_final_failure_is_logged_once_with_its_cause() -> None:
    web = client(FakeTransport(HttpError("u", 0, "timed out"), HttpError("u", 503, "gone", {"server": "nginx"})))

    with Logged() as logged, pytest.raises(HttpError):
        web.request("https://x.test/a", attempts=2)

    assert logged.messages == [
        "WARN: attempt 1/2 failed (HTTP 000: timed out), retrying in 10s: https://x.test/a",
        "WARN: attempt 2/2 failed (HTTP 503: gone [server=nginx]), giving up: https://x.test/a",
    ]


def test_a_single_attempt_that_fails_is_logged_with_its_cause() -> None:
    web = client(FakeTransport(HttpError("u", 404, "no such site", {"cf-ray": "8a1-AMS"})))

    with Logged() as logged, pytest.raises(HttpError):
        web.request("https://x.test/a", attempts=1)

    assert logged.messages == [
        "WARN: attempt 1/1 failed (HTTP 404: no such site [cf-ray=8a1-AMS]), giving up: https://x.test/a"
    ]


def test_an_error_waiting_cant_fix_is_logged_where_it_stops_the_retries() -> None:
    web = client(FakeTransport(HttpError("u", 401, "bad token")))

    with Logged() as logged, pytest.raises(HttpError):
        web.request("https://x.test/a", attempts=3)

    assert logged.messages == ["WARN: attempt 1/3 failed (HTTP 401: bad token), giving up: https://x.test/a"]


def test_a_retry_that_succeeds_and_a_reused_answer_log_no_final_failure() -> None:
    web = client(FakeTransport(HttpError("u", 429, "slow down"), ok(1)))

    with Logged() as logged:
        web.request("https://x.test/a", attempts=2)
        web.request("https://x.test/a", attempts=2)

    assert not [m for m in logged.messages if "giving up" in m]


def test_a_final_failure_line_names_the_path_and_hides_a_credential_in_the_query() -> None:
    url = "https://x.test/users/some-login?page=2&access_token=hunter2&Api-Key=k3y"
    web = client(FakeTransport(HttpError(url, 404, "gone")))

    with Logged() as logged, pytest.raises(HttpError):
        web.request(url, attempts=1)

    assert logged.messages == [
        "WARN: attempt 1/1 failed (HTTP 404: gone), "
        "giving up: https://x.test/users/some-login?page=2&access_token=…&Api-Key=…"
    ]


def test_a_retry_and_a_reused_answer_hide_a_credential_in_the_query_too() -> None:
    url = "https://x.test/a?secret=hunter2&day=2026-10-16"
    web = client(FakeTransport(HttpError(url, 500, "oops"), ok(1)))

    with Logged() as logged:
        web.request(url, attempts=2)
        web.request(url, attempts=2)

    assert "hunter2" not in "\n".join(logged.messages)
    assert logged.messages[-1] == "Reused the answer to GET https://x.test/a?secret=…&day=2026-10-16"


def test_an_http_error_s_text_hides_a_credential_in_the_url() -> None:
    error = HttpError("https://x.test/a?token=hunter2&page=2", 500, "oops")

    assert str(error) == "HTTP 500 from https://x.test/a?token=…&page=2: oops"
    assert error.url == "https://x.test/a?token=hunter2&page=2"


def test_a_final_failure_line_never_carries_a_request_header() -> None:
    web = client(FakeTransport(HttpError("u", 403, "denied")))

    with Logged() as logged, pytest.raises(HttpError):
        web.request("https://x.test/a", headers={"Authorization": "Bearer t0ken"})

    assert "t0ken" not in "\n".join(logged.messages)


# --- redirects ---


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _redirect_run(same_origin: bool) -> list[str | None]:
    """The Authorization header the redirect's target received, from a request that carried one."""
    seen: list[str | None] = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"[]")

        def log_message(self, *args: object) -> None:
            return None

    target, target_url = _serve(Target)
    start_url = ""

    class Start(Target):
        def do_GET(self) -> None:
            if self.path == "/next":
                Target.do_GET(self)
            else:
                self.send_response(302)
                self.send_header("Location", f"{start_url if same_origin else target_url}/next")
                self.end_headers()

    start, start_url = _serve(Start)
    try:
        request = urllib.request.Request(  # noqa: S310 (a local test server)
            f"{start_url}/first", headers={"Authorization": "Bearer secret"}
        )
        assert http.urlopen(request, 5).body == b"[]"
    finally:
        start.shutdown()
        target.shutdown()
    return seen


def test_a_redirect_to_another_host_does_not_carry_the_authorization_header() -> None:
    assert _redirect_run(same_origin=False) == [None]


def test_a_redirect_within_the_same_host_keeps_the_authorization_header() -> None:
    assert _redirect_run(same_origin=True) == ["Bearer secret"]
