"""The GitHub module: what the GitHub sources share, tested apart from any one of them."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import port
import pytest
import scenarios
from scenarios.builders.common import exchange, failed, retried, thrice

from leadline import http
from leadline.context import Finding
from leadline.http import HttpError, UnusableError
from leadline.sources import github
from leadline.sources.github.tests.builders import GITHUB_API, GITHUB_GRAPHQL, github_repo, github_traffic, graphql_body

REPO = "example-org/example-app"


@pytest.mark.parametrize("repo", ["a/b", "example-org/example.app", "a-b/c_d"])
def test_an_owner_and_a_name_is_a_repo(repo: str) -> None:
    assert github.repo_problems({"repo": repo}) == []


@pytest.mark.parametrize(
    "entry",
    [{}, {"repo": 5}, {"repo": ""}, {"repo": "a"}, {"repo": "a/b/c"}, {"repo": "a /b"}, {"repo": "/b"}],
    ids=["no repo", "not a string", "empty", "no owner", "two slashes", "a space", "an empty owner"],
)
def test_anything_else_is_a_problem_named_by_its_key(entry: Any) -> None:
    assert github.repo_problems(entry) == ["repo: must be owner/name"]


def test_a_maintainer_is_a_login() -> None:
    assert github.maintainer_problems({"maintainer": "example-maintainer"}) == []


@pytest.mark.parametrize("entry", [{}, {"maintainer": ""}, {"maintainer": 5}], ids=["none", "empty", "not a string"])
def test_anything_else_is_a_maintainer_problem(entry: Any) -> None:
    assert github.maintainer_problems(entry) == ["maintainer: must be a login"]


def test_a_repos_file_fields_are_the_repo_and_its_name() -> None:
    assert github.file_fields(REPO) == {"repo": REPO, "repo_name": "example-app"}


def test_the_headers_send_the_token_and_ask_for_the_github_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(github.CREDENTIAL, "t0ken")

    assert github.headers() == {"Authorization": "Bearer t0ken", "Accept": "application/vnd.github+json"}


def test_the_credential_is_the_variable_the_sources_need() -> None:
    assert github.CREDENTIAL == "GITHUB_TOKEN"
    assert github.API == GITHUB_API


# --- check()'s findings ---


def check_access(tmp_path: Path, response: Any, forbidden: str | None = None) -> Finding:
    transport = http.SavedResponses(retried(response if isinstance(response, list) else [response]))
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, (github.CREDENTIAL,)):
        finding = github.check_access(ctx, REPO, "", fetching="the repo", ok="the repo exists", forbidden=forbidden)
    assert transport.unmatched == transport.never_served() == []
    return finding


def test_access_is_ok_when_the_token_reads_the_path(tmp_path: Path) -> None:
    found = check_access(tmp_path, exchange(f"{GITHUB_API}/repos/{REPO}", github_repo(REPO)))

    assert found == Finding("ok", f"{REPO}: the repo exists")


@pytest.mark.parametrize(
    ("response", "forbidden", "finding"),
    [
        (
            exchange(f"{GITHUB_API}/repos/{REPO}", "", status=404),
            None,
            Finding("error", f"{REPO}: HTTP 404: the repo doesn't exist, or the token can't read it"),
        ),
        (
            exchange(f"{GITHUB_API}/repos/{REPO}", "", status=403),
            None,
            Finding("error", f"{REPO}: HTTP 403: the repo doesn't exist, or the token can't read it"),
        ),
        (
            exchange(f"{GITHUB_API}/repos/{REPO}", "", status=403),
            "it needs push access",
            Finding("error", f"{REPO}: HTTP 403: it needs push access"),
        ),
        (
            exchange(f"{GITHUB_API}/repos/{REPO}", "", status=404),
            "it needs push access",
            Finding("error", f"{REPO}: HTTP 404: the repo doesn't exist, or the token can't read it"),
        ),
        (
            thrice(exchange(f"{GITHUB_API}/repos/{REPO}", "", status=403, headers={"Retry-After": "60"})),
            "it needs push access",
            Finding("warn", f"{REPO}: could not fetch the repo: HTTP 403, rate limited"),
        ),
        (
            exchange(f"{GITHUB_API}/repos/{REPO}", "", status=503),
            None,
            Finding("warn", f"{REPO}: could not fetch the repo: HTTP 503"),
        ),
        (failed(f"{GITHUB_API}/repos/{REPO}"), None, Finding("warn", f"{REPO}: could not fetch the repo: HTTP 0")),
    ],
    ids=[
        "not found",
        "refused",
        "refused, with its own reason",
        "not found, whatever the 403 reason",
        "rate limited",
        "unavailable",
        "no connection",
    ],
)
def test_access_trouble_is_a_finding(tmp_path: Path, response: Any, forbidden: str | None, finding: Finding) -> None:
    assert check_access(tmp_path, response, forbidden) == finding


def test_a_failed_request_names_what_was_missing() -> None:
    """A check that reads more than access (the tree's paths) words its own `missing`."""
    found = github.failed_finding("a/b@HEAD", HttpError("u", 404, ""), "the tree", missing="no such ref")

    assert found == Finding("error", "a/b@HEAD: HTTP 404: no such ref")


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (exchange(f"{GITHUB_API}/repos/{REPO}", github_repo(REPO)), Finding("ok", f"{REPO}: the repo exists")),
        (
            exchange(f"{GITHUB_API}/repos/{REPO}", "", status=404),
            Finding("error", f"{REPO}: HTTP 404: the repo doesn't exist, or the token can't read it"),
        ),
    ],
    ids=["exists", "not found"],
)
def test_check_repo_finds_whether_the_repo_exists(tmp_path: Path, response: Any, finding: Finding) -> None:
    assert checked(tmp_path, response, github.check_repo) == finding


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (
            exchange(f"{GITHUB_API}/repos/{REPO}/traffic/views", github_traffic("views", {})),
            Finding("ok", f"{REPO}: the token can read its traffic"),
        ),
        (
            exchange(f"{GITHUB_API}/repos/{REPO}/traffic/views", "", status=403),
            Finding("error", f"{REPO}: HTTP 403: the token can't read its traffic, which needs push access"),
        ),
        (
            exchange(f"{GITHUB_API}/repos/{REPO}/traffic/views", "", status=503),
            Finding("warn", f"{REPO}: could not fetch its traffic: HTTP 503"),
        ),
    ],
    ids=["readable", "no push access", "unavailable"],
)
def test_check_traffic_finds_whether_the_token_can_read_the_traffic(
    tmp_path: Path, response: Any, finding: Finding
) -> None:
    assert checked(tmp_path, response, github.check_traffic) == finding


def checked(tmp_path: Path, response: Any, check: Any) -> Finding:
    transport = http.SavedResponses(retried(response if isinstance(response, list) else [response]))
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, (github.CREDENTIAL,)):
        finding: Finding = check(ctx, REPO)
    assert transport.unmatched == transport.never_served() == []
    return finding


# --- a list asked for by page number ---


def pages(tmp_path: Path, *responses: Any) -> list[Any]:
    transport = http.SavedResponses(retried(responses))
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, (github.CREDENTIAL,)):
        answers = github.numbered_pages(ctx, f"{GITHUB_API}/repos/{REPO}/issues?state=all&per_page=2")
    assert transport.unmatched == transport.never_served() == []
    return [answer.json() for answer in answers]


def test_numbered_pages_asks_by_number_while_the_link_names_a_next_page(tmp_path: Path) -> None:
    url = f"{GITHUB_API}/repos/{REPO}/issues?state=all&per_page=2&page="
    elsewhere = f"{GITHUB_API}/repositories/7/issues?page="  # the Link's own URL is never asked for
    responses = [
        exchange(f"{url}1", [1, 2], headers={"Link": f'<{elsewhere}2>; REL="next", <{elsewhere}2>; rel="last"'}),
        exchange(f"{url}2", {"not": "a list"}, headers={"link": f'<{elsewhere}3>; rel="next"'}),
        exchange(f"{url}3", [5], headers={"link": f'<{elsewhere}2>; rel="prev"'}),
    ]

    assert pages(tmp_path, *responses) == [[1, 2], {"not": "a list"}, [5]]  # each answer as it came, unread


def test_numbered_pages_asks_for_the_media_type_it_is_given(tmp_path: Path) -> None:
    url = f"{GITHUB_API}/repos/{REPO}/stargazers?per_page=2"
    seen: list[str] = []
    saved = http.SavedResponses([exchange(f"{url}&page=1", [])])

    def transport(request: Any, timeout: float) -> Any:
        seen.append(request.get_header("Accept"))
        return saved(request, timeout)

    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])  # type: ignore[arg-type]
    with port.credentials(scenarios.CREDENTIALS, (github.CREDENTIAL,)):
        github.numbered_pages(ctx, url, accept="application/vnd.github.star+json")

    assert seen == ["application/vnd.github.star+json"]


def test_numbered_pages_raises_the_failed_pages_error(tmp_path: Path) -> None:
    url = f"{GITHUB_API}/repos/{REPO}/issues?state=all&per_page=2&page="
    first = exchange(f"{url}1", [1], headers={"link": f'<{url}2>; rel="next"'})

    with pytest.raises(HttpError) as raised:
        pages(tmp_path, first, exchange(f"{url}2", "", status=502))

    assert (raised.value.code, raised.value.url) == (502, f"{url}2")


# --- a GitHub timestamp ---


@pytest.mark.parametrize(
    ("value", "when"),
    [
        ("2026-09-01T00:30:00Z", datetime(2026, 9, 1, 0, 30, tzinfo=timezone.utc)),
        ("2026-09-01T02:00:00+02:00", datetime(2026, 9, 1, 2, 0, tzinfo=timezone(timedelta(hours=2)))),
    ],
)
def test_a_github_time_is_read_with_its_offset(value: str, when: datetime) -> None:
    assert github.parse_time(value) == when


@pytest.mark.parametrize("value", [None, 5, "", "yesterday", "2026-09-01T00:30:00"], ids=str)
def test_anything_else_is_not_a_github_time(value: Any) -> None:
    with pytest.raises(ValueError, match=r"^not a time with its offset: "):
        github.parse_time(value)


@pytest.mark.parametrize(
    ("value", "day"),
    [
        ("2026-09-02T06:59:59Z", "2026-09-01"),
        ("2026-09-02T07:00:00Z", "2026-09-02"),
        ("2026-09-01T23:00:00-07:00", "2026-09-01"),
    ],
    ids=["before local midnight", "at local midnight", "given in another offset"],
)
def test_a_github_times_date_is_its_day_in_the_runs_offset(value: str, day: str) -> None:
    """As a row is dated: the run's local date, here seven hours behind UTC."""
    now = datetime(2026, 9, 23, 23, 31, 5, tzinfo=timezone(timedelta(hours=-7)))
    ctx = port.context(Path("unused"), http.SavedResponses([]), now, [])

    assert github.local_date(ctx, value).isoformat() == day


# --- a GraphQL query ---

QUERY = "query($o:String!,$n:String!){repository(owner:$o,name:$n){x}}"
SENT = graphql_body(QUERY, {"o": "example-org", "n": "example-app"})


def answer(body: Any, status: int = 200) -> Any:
    return exchange(GITHUB_GRAPHQL, body, method="POST", sent=SENT, status=status)


def query(tmp_path: Path, response: Any) -> Any:
    transport = http.SavedResponses(retried(response if isinstance(response, list) else [response]))
    sent: list[Any] = []

    def recording(request: Any, timeout: float) -> Any:
        sent.append(request)
        return transport(request, timeout)

    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    ctx.http._transport = recording  # to see the request headers
    with port.credentials({github.CREDENTIAL: "t0ken"}, (github.CREDENTIAL,)):
        try:
            return github.graphql(ctx, "things", QUERY, github.owner_and_name(REPO))
        finally:
            assert transport.unmatched == transport.never_served() == []
            assert sent[0].get_header("Authorization") == "Bearer t0ken"
            assert sent[0].get_header("Content-type") == "application/json"


def test_graphql_posts_the_query_and_its_variables_as_the_script_does_and_returns_the_data(tmp_path: Path) -> None:
    assert query(tmp_path, answer({"data": {"repository": {"x": 1}}})) == {"repository": {"x": 1}}


def test_graphql_raises_a_failed_requests_error(tmp_path: Path) -> None:
    with pytest.raises(HttpError) as raised:
        query(tmp_path, answer("", status=502))

    assert raised.value.code == 502


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("<html>maintenance</html>", "things response was not JSON"),
        ("", "things response was not JSON"),
        ([], "things response had an unexpected shape"),
        ({"errors": "boom"}, "things response had an unexpected shape"),
        ({"errors": ["boom"]}, "things response had an unexpected shape"),
    ],
    ids=["not JSON", "empty", "not an object", "errors not a list", "an error not an object"],
)
def test_graphql_raises_an_answer_it_cant_use_naming_its_kind(tmp_path: Path, body: Any, reason: str) -> None:
    with pytest.raises(UnusableError, match=f"^{reason}$"):
        query(tmp_path, answer(body))


def test_graphql_raises_the_answers_errors_naming_the_first_three(tmp_path: Path) -> None:
    errors = [{"message": f"problem {n}"} for n in range(1, 5)]

    with pytest.raises(github.GraphqlError, match=r"^problem 1; problem 2; problem 3$"):
        query(tmp_path, answer({"data": None, "errors": errors}))


def test_no_errors_is_an_answer(tmp_path: Path) -> None:
    assert query(tmp_path, answer({"data": {"repository": None}, "errors": []})) == {"repository": None}


@pytest.mark.parametrize("kind", ["NOT_FOUND", "FORBIDDEN", "INSUFFICIENT_SCOPES"])
def test_a_graphql_error_waiting_cant_fix_is_unfixable(kind: str) -> None:
    """What a 404 or 403 means: not there, or the token may not read it."""
    error = github.GraphqlError([{"type": "RATE_LIMITED", "message": "a"}, {"type": kind, "message": "b"}])

    assert error.unfixable


@pytest.mark.parametrize(
    "error",
    [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}, {"message": "Something went wrong"}],
    ids=["a rate limit", "no type, such as a timeout"],
)
def test_any_other_graphql_error_is_recoverable(error: dict[str, str]) -> None:
    assert not github.GraphqlError([error]).unfixable


def test_a_graphql_error_names_its_types_as_its_cause() -> None:
    error = github.GraphqlError([{"type": "RATE_LIMITED", "message": "a"}, {"type": "NOT_FOUND"}, {"message": "c"}])

    assert error.cause == "GraphQL NOT_FOUND, RATE_LIMITED, error"


def test_an_owner_and_name_are_the_query_variables() -> None:
    assert github.owner_and_name(REPO) == {"o": "example-org", "n": "example-app"}


# --- check()'s GraphQL query ---


def check_query(tmp_path: Path, response: Any, maintainer: str | None = None) -> list[Finding]:
    transport = http.SavedResponses(retried(response if isinstance(response, list) else [response]))
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    ok = "the token can query its things"
    with port.credentials(scenarios.CREDENTIALS, (github.CREDENTIAL,)):
        found = github.check_query(ctx, REPO, "x", fetching="its things", ok=ok, maintainer=maintainer, misread=MISREAD)
    assert transport.unmatched == transport.never_served() == []
    return found


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (answer({"data": {"repository": {"x": 1}}}), Finding("ok", f"{REPO}: the token can query its things")),
        (
            answer({"data": {"repository": None}, "errors": [{"type": "NOT_FOUND", "message": "Could not resolve"}]}),
            Finding("error", f"{REPO}: could not query its things: Could not resolve"),
        ),
        (
            answer({"data": None, "errors": [{"type": "RATE_LIMITED", "message": "rate limit exceeded"}]}),
            Finding("warn", f"{REPO}: could not query its things: rate limit exceeded"),
        ),
        (
            answer("", status=401),
            Finding("error", f"{REPO}: HTTP 401: the repo doesn't exist, or the token can't read it"),
        ),
        (answer("", status=502), Finding("warn", f"{REPO}: could not fetch its things: HTTP 502")),
        (answer("<html>"), Finding("warn", f"{REPO}: its things response was not JSON")),
        (
            answer({"data": {"repository": None}}),
            Finding("warn", f"{REPO}: its things response had an unexpected shape"),
        ),
    ],
    ids=["ok", "not found", "rate limited", "rejected", "unavailable", "not JSON", "no repository"],
)
def test_check_query_finds_whether_the_token_can_query_the_repo(
    tmp_path: Path, response: Any, finding: Finding
) -> None:
    assert check_query(tmp_path, response) == [finding]


# --- the maintainer's login, checked in check()'s query ---

MISREAD = "every thing would count as the community's"
MAINTAINED = graphql_body(
    "query($o:String!,$n:String!,$m:String!){repository(owner:$o,name:$n){x} user(login:$m){login}}",
    {"o": "example-org", "n": "example-app", "m": "example-maintainer"},
)
NO_SUCH_USER = {"type": "NOT_FOUND", "path": ["user"], "message": "Could not resolve to a User"}
THE_REPO = Finding("ok", f"{REPO}: the token can query its things")


def maintained(body: Any, status: int = 200) -> Any:
    return exchange(GITHUB_GRAPHQL, body, method="POST", sent=MAINTAINED, status=status)


@pytest.mark.parametrize(
    ("body", "finding"),
    [
        (
            {"data": {"repository": {"x": 1}, "user": {"login": "example-maintainer"}}},
            Finding("ok", f'{REPO}: the maintainer login "example-maintainer" exists'),
        ),
        (
            {"data": {"repository": {"x": 1}, "user": None}, "errors": [NO_SUCH_USER]},
            Finding(
                "error",
                f'{REPO}: the maintainer login "example-maintainer" doesn\'t exist, so every thing would count as the '
                "community's",
            ),
        ),
        (
            {"data": {"repository": {"x": 1}, "user": {"login": "Example-Maintainer"}}},
            Finding(
                "error",
                f'{REPO}: the maintainer login "example-maintainer" is "Example-Maintainer" on GitHub, and logins are '
                "compared exactly, so every thing would count as the community's",
            ),
        ),
        (
            {"data": {"repository": {"x": 1}, "user": "example-maintainer"}},
            Finding("warn", f"{REPO}: the maintainer's login response had an unexpected shape"),
        ),
    ],
    ids=["exists", "no such user", "spelled otherwise", "unreadable"],
)
def test_check_query_checks_the_maintainers_login_in_the_same_query(
    tmp_path: Path, body: Any, finding: Finding
) -> None:
    assert check_query(tmp_path, maintained(body), "example-maintainer") == [THE_REPO, finding]


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (
            maintained(
                {
                    "data": {"repository": None, "user": None},
                    "errors": [{**NO_SUCH_USER, "path": ["repository"]}, NO_SUCH_USER],
                }
            ),
            Finding(
                "error", f"{REPO}: could not query its things: Could not resolve to a User; Could not resolve to a User"
            ),
        ),
        (maintained("", status=502), Finding("warn", f"{REPO}: could not fetch its things: HTTP 502")),
    ],
    ids=["no repo either", "unavailable"],
)
def test_the_maintainer_is_not_judged_when_the_repo_cant_be_queried(
    tmp_path: Path, response: Any, finding: Finding
) -> None:
    assert check_query(tmp_path, response, "example-maintainer") == [finding]


def test_a_rate_limit_at_the_login_is_not_a_missing_login(tmp_path: Path) -> None:
    limited = {"type": "RATE_LIMITED", "path": ["user"], "message": "rate limit exceeded"}
    response = maintained({"data": {"repository": {"x": 1}, "user": None}, "errors": [limited]})

    found = check_query(tmp_path, response, "example-maintainer")

    assert found == [Finding("warn", f"{REPO}: could not query its things: rate limit exceeded")]


USER_SENT = graphql_body("query($m:String!){user(login:$m){login}}", {"m": "example-maintainer"})


def check_maintainer(tmp_path: Path, response: Any) -> Finding:
    transport = http.SavedResponses(retried(response if isinstance(response, list) else [response]))
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, (github.CREDENTIAL,)):
        finding = github.check_maintainer(ctx, REPO, "example-maintainer", misread=MISREAD)
    assert transport.unmatched == transport.never_served() == []
    return finding


def user_answer(body: Any, status: int = 200) -> Any:
    return exchange(GITHUB_GRAPHQL, body, method="POST", sent=USER_SENT, status=status)


@pytest.mark.parametrize(
    ("response", "finding"),
    [
        (
            user_answer({"data": {"user": {"login": "example-maintainer"}}}),
            Finding("ok", f'{REPO}: the maintainer login "example-maintainer" exists'),
        ),
        (
            user_answer({"data": {"user": None}, "errors": [NO_SUCH_USER]}),
            Finding(
                "error",
                f'{REPO}: the maintainer login "example-maintainer" doesn\'t exist, so every thing would count as the '
                "community's",
            ),
        ),
        (
            user_answer({"data": None, "errors": [{"type": "RATE_LIMITED", "message": "rate limit exceeded"}]}),
            Finding("warn", f"{REPO}: could not query the maintainer's login: rate limit exceeded"),
        ),
        (
            user_answer("", status=401),
            Finding("error", f"{REPO}: HTTP 401: the token can't query GitHub's GraphQL API"),
        ),
        (user_answer("", status=502), Finding("warn", f"{REPO}: could not fetch the maintainer's login: HTTP 502")),
        (user_answer("<html>"), Finding("warn", f"{REPO}: the maintainer's login response was not JSON")),
    ],
    ids=["exists", "no such user", "rate limited", "rejected", "unavailable", "not JSON"],
)
def test_check_maintainer_asks_for_the_login_alone(tmp_path: Path, response: Any, finding: Finding) -> None:
    """For a source whose check has no GraphQL query of its own to add it to (github_issues)."""
    assert check_maintainer(tmp_path, response) == finding
