"""github_response_times's code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

import statistics
from datetime import date, datetime
from typing import Any

from leadline import store
from leadline.context import Context, Finding
from leadline.http import HttpError, UnusableError, unexpected
from leadline.sources.github import api as github
from leadline.sources.github.github_response_times import spec
from leadline.storage import File

ENV: tuple[str, ...] = (github.CREDENTIAL,)
LAGGING = False
KIND = "issues"  # what a failure row's reason names
PERCENTILE = 0.9  # p90_hours
WITHIN_HOURS = (1, 24, 72)  # the within_<n>h_pct bounds, a reply exactly on one counting as within
CHECKED = "issues(first:1){totalCount}"  # what check() queries of the repo, the least that reads it
# What every night would read with a maintainer login that isn't one, as check() says.
MISREAD = "every issue would count as the community's and never answered"
# The script's query, byte for byte, sent with the owner, name and cursor (null for the first page).
QUERY = (
    "query($o:String!,$n:String!,$c:String){repository(owner:$o,name:$n){issues(first:100,after:$c,"
    "orderBy:{field:CREATED_AT,direction:ASC}){pageInfo{hasNextPage endCursor} nodes{number createdAt closedAt "
    "author{login} comments(first:30){nodes{createdAt author{login}}}}}}}"
)


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether each repo exists, the token can query its issues and the maintainer's login exists.

    It writes nothing. The repo is what other GitHub sources ask for, so a run reuses that answer; the
    query, with the login asked for in it, costs one request. A login that doesn't exist, or is spelled
    otherwise, would misread every night without a failure (MISREAD).
    """
    findings = []
    for entry in cfg:
        repo = entry["repo"]
        found = [github.check_repo(ctx, repo)]
        if found[-1].level == "ok":
            ok, maintainer = "the token can query its issues", entry["maintainer"]
            found += github.check_query(
                ctx, repo, CHECKED, fetching=f"its {KIND}", ok=ok, maintainer=maintainer, misread=MISREAD
            )
        findings += found
    return findings


def _collect(ctx: Context, entry: Any) -> None:
    repo, file, day = entry["repo"], ctx.file(spec.file_name(entry)), ctx.date.isoformat()
    ctx.log.info("Collecting issue response times for %s...", repo)
    pages: list[dict[str, Any]] = []
    try:
        cursor = None
        while True:
            pages.append(_issues(github.graphql(ctx, KIND, QUERY, {**github.owner_and_name(repo), "c": cursor})))
            cursor = _next_cursor(pages[-1])
            if not cursor:
                break
        ctx.log.info("Fetched %d page(s) of issues with comments for %s", len(pages), repo)
        nodes = _nodes(pages)
    except HttpError as e:
        ctx.log.info("ERROR: Failed to fetch issue page %d for %s: HTTP %s", len(pages) + 1, repo, e.http_code)
        ctx.record_unfixable(spec.NAME, file.name, e, f"{repo}: HTTP {e.http_code} from GitHub's GraphQL API")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": e.failed(KIND)}
    except github.GraphqlError as e:
        ctx.log.info("ERROR: Failed to page issues for %s: %s", repo, e)
        ctx.record_unfixable(spec.NAME, file.name, e, f"{repo}: GitHub's GraphQL API answered: {e}")
        failure = {"status": "graphql_error", "reason": str(e)}
    except UnusableError as e:
        ctx.log.info("ERROR: Failed to compute response times for %s: %s", repo, e)
        failure = {"status": "parse_failed", "reason": str(e)}
    else:
        _recompute(ctx, file, entry["maintainer"], nodes)
        store.write_good(file, _row(day, entry["maintainer"], nodes), ctx, spec.NAME)
        return
    store.write_failure(file, {"date": day, **failure}, ctx.now)


def _recompute(ctx: Context, file: File, maintainer: str, nodes: list[dict[str, Any]]) -> None:
    """Write the row of each past day from the first community issue's (store.recompute), each counting what the
    issues opened by the end of that day (in the run's offset) were by then."""

    def by(when: Any, day: date) -> bool:
        return when is not None and github.local_date(ctx, when) <= day

    community = [issue for issue in nodes if _login(issue) and _login(issue) != maintainer]
    first = min((github.local_date(ctx, issue["createdAt"]) for issue in community), default=None)
    rows = []
    for day in store.recompute_days(file, first, ctx):
        then = [
            {
                **issue,
                "closedAt": issue.get("closedAt") if by(issue.get("closedAt"), day) else None,
                "comments": {
                    "nodes": [c for c in (issue.get("comments") or {}).get("nodes", []) if by(c["createdAt"], day)]
                },
            }
            for issue in nodes
            if by(issue["createdAt"], day)
        ]
        rows.append(_row(day.isoformat(), maintainer, then))
    store.recompute(file, rows, ctx, spec.NAME)


def _issues(data: Any) -> dict[str, Any]:
    """A page's issues connection, its pageInfo and nodes unread."""
    repository = data.get("repository") if isinstance(data, dict) else None
    found = repository.get("issues") if isinstance(repository, dict) else None
    if not isinstance(found, dict):
        raise unexpected(KIND)
    return found


def _next_cursor(page: dict[str, Any]) -> str:
    """The cursor of the page after `page`, or "" for the last, as the script reads its pageInfo."""
    info = page.get("pageInfo")
    if not (isinstance(info, dict) and isinstance(info.get("hasNextPage"), bool)):
        raise unexpected(KIND)
    cursor = info.get("endCursor") if info["hasNextPage"] else ""
    if not isinstance(cursor, str):
        raise unexpected(KIND)
    return cursor


def _login(item: dict[str, Any]) -> Any:
    return (item.get("author") or {}).get("login")


def _readable_author(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("author") or {}, dict)
        and isinstance(_login(item), (str, type(None)))
    )


def _readable(issue: Any) -> bool:
    """An issue with every field the script reads, of the type it reads it as, its comments too."""
    if not _readable_author(issue):
        return False
    comments = issue.get("comments") or {}
    replies = comments.get("nodes", []) if isinstance(comments, dict) else None
    if not isinstance(replies, list) or not all(_readable_author(reply) for reply in replies):
        return False
    try:
        for item in [issue, *replies]:
            github.parse_time(item.get("createdAt"))
        if issue.get("closedAt") is not None:
            github.parse_time(issue["closedAt"])
    except ValueError:
        return False
    return True


def _nodes(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every page's issues, read whole: one page or issue it can't read makes the answer unusable."""
    nodes: list[Any] = []
    for page in pages:
        if not isinstance(page.get("nodes"), list):
            raise unexpected(KIND)
        nodes += page["nodes"]
    if not all(_readable(issue) for issue in nodes):
        raise unexpected(KIND)
    return nodes


def _answered_at(issue: dict[str, Any], maintainer: str) -> datetime | None:
    """When the issue was answered: the maintainer's first comment, among those fetched, or its closing, whichever
    came first (ADR: a closed community issue is answered); None if neither."""
    times = [github.parse_time(issue["closedAt"])] if issue.get("closedAt") is not None else []
    for reply in (issue.get("comments") or {}).get("nodes", []):
        if _login(reply) == maintainer:
            times.append(github.parse_time(reply["createdAt"]))
            break
    return min(times, default=None)


def _row(day: str, maintainer: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """The script's row, its keys in its order, an issue's answer its first reply or its closing."""
    hours: list[float] = []
    never = community = 0
    for issue in nodes:
        author = _login(issue)
        if not author or author == maintainer:
            continue
        community += 1
        answered = _answered_at(issue, maintainer)
        if answered is None:
            never += 1
            continue
        hours.append((answered - github.parse_time(issue["createdAt"])).total_seconds() / 3600)
    hours.sort()
    row: dict[str, Any] = {
        "date": day,
        "issues_scanned": len(nodes),
        "community_issues": community,
        "answered": len(hours),
        "never_answered": never,
        "median_hours": round(statistics.median(hours), 1) if hours else None,
        "mean_hours": round(statistics.mean(hours), 1) if hours else None,
        "p90_hours": round(hours[min(int(len(hours) * PERCENTILE), len(hours) - 1)], 1) if hours else None,
        "fastest_hours": round(hours[0], 1) if hours else None,
    }
    for bound in WITHIN_HOURS:
        within = round(100 * sum(1 for h in hours if h <= bound) / len(hours), 1) if hours else None
        row[f"within_{bound}h_pct"] = within
    return row
