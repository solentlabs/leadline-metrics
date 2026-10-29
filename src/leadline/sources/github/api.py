"""What the GitHub sources share: the API, the token, pages, GraphQL, times, and check()'s checks.

check()'s findings and checks are here too.

Nothing here belongs to one source: each source keeps its own requests, rows and messages.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any, Mapping

from leadline.context import Context, Finding
from leadline.http import HttpError, Response, UnusableError, read_json, unexpected

API = "https://api.github.com"
GRAPHQL = f"{API}/graphql"
# GraphQL error types that mean what a 404 or 403 means (the thing asked for isn't there, or the token
# may not read it): waiting can't fix them. Any other, a spent rate limit (RATE_LIMITED) or an error
# with no type (a timeout), is recoverable.
GRAPHQL_UNFIXABLE = frozenset({"NOT_FOUND", "FORBIDDEN", "INSUFFICIENT_SCOPES"})
GRAPHQL_REASONS = 3  # a GraphQL failure's reason names this many of its errors, as the script's do
CREDENTIAL = "GITHUB_TOKEN"  # the variable holding the token every GitHub source needs
# check()'s reason for an error waiting can't fix, unless the source words its own.
MISSING = "the repo doesn't exist, or the token can't read it"
NO_GRAPHQL = "the token can't query GitHub's GraphQL API"  # the reason for a query that isn't the repo's
# check()'s query of the maintainer's login, and what a finding calls it.
MAINTAINER_FIELD = "user(login:$m){login}"
MAINTAINER_KIND = "the maintainer's login"
# check_traffic()'s findings: readable, or a 403, which for traffic means no push access.
READABLE = "the token can read its traffic"
NO_PUSH_ACCESS = "the token can't read its traffic, which needs push access"


def parse_time(value: Any) -> datetime:
    """A GitHub timestamp (`2026-09-01T00:30:00Z`), as the scripts read one; raises ValueError for anything else."""
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
    except ValueError:
        when = None
    if when is None or when.tzinfo is None:
        raise ValueError(f"not a time with its offset: {value!r}")
    return when


def local_date(ctx: Context, value: Any) -> date:
    """A GitHub timestamp's date in the run's UTC offset (`ctx.now`'s), as rows are dated; ValueError as parse_time."""
    return parse_time(value).astimezone(ctx.now.tzinfo).date()


def headers() -> dict[str, str]:
    """Every GitHub request's headers: the token, and GitHub's JSON asked for."""
    return {"Authorization": f"Bearer {os.environ[CREDENTIAL]}", "Accept": "application/vnd.github+json"}


def numbered_pages(ctx: Context, url: str, *, accept: str | None = None) -> list[Response]:
    """Every page of the list at `url`, asked for by number (`url&page=N`) while the answer's Link names a next page.

    With `accept`, each request asks for that media type in place of GitHub's JSON (a list of stars with their times).

    As the script asks: the Link's own URL is never followed, and each answer is returned unread, so a
    page the source can't read still has the pages after it asked for. Raises a failed page's HttpError.
    """
    pages, page = [], 1
    asked = {**headers(), **({"Accept": accept} if accept else {})}
    while True:
        pages.append(ctx.http.request(f"{url}&page={page}", headers=asked))
        if 'rel="next"' not in pages[-1].headers.get("link", "").lower():
            return pages
        page += 1


def profile_location(ctx: Context, login: str) -> str | None:
    """The location of the profile of `login`, None where it has none (unset, empty, or no profile: a 404).

    Raises a failed request's HttpError, or UnusableError for an answer it can't read.
    """
    try:
        response = ctx.http.request(f"{API}/users/{login}", headers=headers())
    except HttpError as e:
        if e.code == 404:  # GitHub's answer that there is no such profile, so no location
            return None
        raise
    answer = read_json("profile", response)
    location = answer.get("location") if isinstance(answer, dict) else None
    if not isinstance(answer, dict) or not isinstance(location, (str, type(None))):
        raise unexpected("profile")
    return location or None


class GraphqlError(Exception):
    """GitHub answered a GraphQL query with `errors`; the message joins the first ones' messages.

    `unfixable` when one's type is in GRAPHQL_UNFIXABLE, for `ctx.record_unfixable`.
    """

    def __init__(self, errors: list[dict[str, Any]], data: Any = None) -> None:
        super().__init__("; ".join(str(e.get("message", e))[:200] for e in errors[:GRAPHQL_REASONS]))
        self.errors, self.data = errors, data
        self.unfixable = any(e.get("type") in GRAPHQL_UNFIXABLE for e in errors)

    @property
    def cause(self) -> str:
        """The errors' types, as `GraphQL NOT_FOUND`."""
        types = sorted({str(e.get("type", "error")) for e in self.errors[:GRAPHQL_REASONS]})
        return "GraphQL " + ", ".join(types)


def owner_and_name(repo: str) -> dict[str, str]:
    """A repo's owner and name as a query's variables, `o` and `n`, as the scripts send them."""
    owner, name = repo.split("/")
    return {"o": owner, "n": name}


def graphql(ctx: Context, kind: str, query: str, variables: Mapping[str, Any]) -> Any:
    """POST `query` with `variables` to GitHub's GraphQL API, once, as the script does; the answer's `data`.

    Raises the request's HttpError; GraphqlError when the answer has errors; UnusableError, naming
    `kind`, when it isn't JSON or isn't an object whose errors are a list of objects.
    """
    try:
        answer = ctx.http.graphql(GRAPHQL, query, variables, headers=headers())
    except ValueError:
        raise UnusableError(f"{kind} response was not JSON") from None
    if not isinstance(answer, dict):
        raise unexpected(kind)
    errors = answer.get("errors")
    if errors:
        if not isinstance(errors, list) or not all(isinstance(e, dict) for e in errors):
            raise unexpected(kind)
        raise GraphqlError(errors, answer.get("data"))
    return answer.get("data")


def failed_finding(
    where: str, e: HttpError, fetching: str, *, missing: str = MISSING, forbidden: str | None = None
) -> Finding:
    """check()'s finding for a failed request: an error waiting can't fix is an error, anything else a warning.

    `missing` is the error's reason, `forbidden` a 403's where the source knows a better one; `fetching`
    names what the request was for (`the repo`).
    """
    if e.unfixable:
        problem = forbidden if forbidden is not None and e.code == 403 else missing
        return Finding("error", f"{where}: HTTP {e.code}: {problem}")
    limited = ", rate limited" if e.rate_limited else ""
    return Finding("warn", f"{where}: could not fetch {fetching}: HTTP {e.code}{limited}")


def check_access(
    ctx: Context, repo: str, path: str, *, fetching: str, ok: str, forbidden: str | None = None
) -> Finding:
    """Whether the token can read `repo`'s API `path` (`""` for the repo itself): `ok`, or failed_finding's."""
    try:
        ctx.http.request(f"{API}/repos/{repo}{path}", headers=headers())
    except HttpError as e:
        return failed_finding(repo, e, fetching, forbidden=forbidden)
    return Finding("ok", f"{repo}: {ok}")


def check_repo(ctx: Context, repo: str) -> Finding:
    """Whether `repo` exists and the token can read it."""
    return check_access(ctx, repo, "", fetching="the repo", ok="the repo exists")


def check_traffic(ctx: Context, repo: str) -> Finding:
    """Whether the token can read `repo`'s traffic, which needs push access."""
    return check_access(ctx, repo, "/traffic/views", fetching="its traffic", ok=READABLE, forbidden=NO_PUSH_ACCESS)


def check_query(
    ctx: Context, repo: str, selection: str, *, fetching: str, ok: str, maintainer: str | None = None, misread: str = ""
) -> list[Finding]:
    """Whether the token can query `selection` of `repo` over GraphQL: `ok`, or what went wrong.

    A failed request is failed_finding's; GraphQL errors are an error when waiting can't fix them, else
    a warning, as is an answer without the repository. With a `maintainer`, the same query asks for
    the login, and a second finding says whether it exists (_maintainer_finding, with `misread`).
    """
    declared, variables, also = "$o:String!,$n:String!", owner_and_name(repo), ""
    if maintainer:
        declared, variables, also = f"{declared},$m:String!", {**variables, "m": maintainer}, f" {MAINTAINER_FIELD}"
    query = f"query({declared}){{repository(owner:$o,name:$n){{{selection}}}{also}}}"
    data = _queried(ctx, repo, fetching, query, variables, MISSING)
    if isinstance(data, Finding):
        return [data]
    if not isinstance(data, dict) or not isinstance(data.get("repository"), dict):
        return [Finding("warn", f"{repo}: {unexpected(fetching)}")]
    found = [Finding("ok", f"{repo}: {ok}")]
    return found + ([_maintainer_finding(repo, maintainer, data, misread)] if maintainer else [])


def check_maintainer(ctx: Context, repo: str, maintainer: str, *, misread: str) -> Finding:
    """Whether `maintainer`'s login exists, asked for alone, for a check with no GraphQL query of its own."""
    query = f"query($m:String!){{{MAINTAINER_FIELD}}}"
    data = _queried(ctx, repo, MAINTAINER_KIND, query, {"m": maintainer}, NO_GRAPHQL)
    return data if isinstance(data, Finding) else _maintainer_finding(repo, maintainer, data, misread)


def _maintainer_finding(repo: str, maintainer: str, data: Any, misread: str) -> Finding:
    """What a query's `user` says of the maintainer's login: it exists, it doesn't, or it is spelled otherwise.

    A login that isn't the user's exactly would never match an author, as rows compare logins exactly;
    `misread` says what every night would then read.
    """
    user = data.get("user", {}) if isinstance(data, dict) else {}
    login = f'the maintainer login "{maintainer}"'
    if user is None:
        return Finding("error", f"{repo}: {login} doesn't exist, so {misread}")
    if not isinstance(user, dict) or not isinstance(user.get("login"), str):
        return Finding("warn", f"{repo}: {unexpected(MAINTAINER_KIND)}")
    if user["login"] != maintainer:
        spelled = f'is "{user["login"]}" on GitHub, and logins are compared exactly'
        return Finding("error", f"{repo}: {login} {spelled}, so {misread}")
    return Finding("ok", f"{repo}: {login} exists")


def _queried(ctx: Context, repo: str, fetching: str, query: str, variables: Mapping[str, Any], missing: str) -> Any:
    """The query's data, or the finding for what went wrong (`missing`, an unfixable failed request's reason).

    GitHub answers a login it can't resolve with a null `user` and a NOT_FOUND error at that path, so
    NOT_FOUND errors only there leave the data to be judged (_maintainer_finding).
    """
    try:
        return graphql(ctx, fetching, query, variables)
    except HttpError as e:
        return failed_finding(repo, e, fetching, missing=missing)
    except GraphqlError as e:
        if all(error.get("path") == ["user"] and error.get("type") == "NOT_FOUND" for error in e.errors):
            return e.data
        return Finding("error" if e.unfixable else "warn", f"{repo}: could not query {fetching}: {e}")
    except UnusableError as e:
        return Finding("warn", f"{repo}: {e}")  # names what it couldn't read
