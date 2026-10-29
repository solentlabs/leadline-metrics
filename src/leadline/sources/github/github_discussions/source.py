"""github_discussions's code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from leadline import store
from leadline.config import is_count
from leadline.context import Context, Finding
from leadline.http import HttpError, UnusableError, unexpected
from leadline.sources.github import api as github
from leadline.sources.github.github_discussions import spec
from leadline.storage import File

ENV: tuple[str, ...] = (github.CREDENTIAL,)
LAGGING = False
KIND = "discussions"  # what a failure row's reason names
RECENT = 10  # the most recently updated discussions listed
UNKNOWN_AUTHOR, NO_CATEGORY, NULL_CATEGORY = "unknown", "uncategorised", "null"
CHECKED = "discussions(first:1){totalCount}"  # what check() queries of the repo, the least that reads it
# What every night would read with a maintainer login that isn't one, as check() says.
MISREAD = "every discussion would count as the community's"
# The fields of a rebuilt day's row that what the query reads can't tell for a past day: how many a query fetched
# then, the answers and comments then, and the most recently updated then. The comments come from COMMENTS_QUERY.
PAST_UNKNOWN = {"fetched": None, "answered": None, "total_comments": None, "recent": None}
COMMENTS_KIND = "discussion comments"
# The script's query, byte for byte, sent with the owner and name as variables.
QUERY = (
    "query($o:String!,$n:String!){repository(owner:$o,name:$n){discussions(first:100,"
    "orderBy:{field:UPDATED_AT,direction:DESC}){totalCount nodes{number title createdAt updatedAt isAnswered "
    "upvoteCount comments{totalCount} author{login} category{name}}}}}"
)
# The discussions' comments' creation (a discussion's comments, as `total_comments` counts them, are its top-level
# ones), asked for only when a day to rebuild lacks its comments.
COMMENTS_QUERY = (
    "query($o:String!,$n:String!){repository(owner:$o,name:$n){discussions(first:100,"
    "orderBy:{field:UPDATED_AT,direction:DESC}){totalCount nodes{number comments(first:100){totalCount "
    "nodes{createdAt}}}}}}"
)


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether each repo exists, the token can query its discussions and the maintainer's login exists.

    It writes nothing. The repo is what other GitHub sources ask for, so a run reuses that answer; the
    query, with the login asked for in it, costs one request. A login that doesn't exist, or is spelled
    otherwise, would misread every night without a failure (MISREAD).
    """
    findings = []
    for entry in cfg:
        repo = entry["repo"]
        found = [github.check_repo(ctx, repo)]
        if found[-1].level == "ok":
            ok, maintainer = "the token can query its discussions", entry["maintainer"]
            found += github.check_query(
                ctx, repo, CHECKED, fetching=f"its {KIND}", ok=ok, maintainer=maintainer, misread=MISREAD
            )
        findings += found
    return findings


def _collect(ctx: Context, entry: Any) -> None:
    repo, file, day = entry["repo"], ctx.file(spec.file_name(entry)), ctx.date.isoformat()
    ctx.log.info("Collecting GitHub discussions for %s...", repo)
    try:
        total, nodes = _discussions(github.graphql(ctx, KIND, QUERY, github.owner_and_name(repo)))
    except HttpError as e:
        ctx.log.info("ERROR: Failed to fetch discussions for %s: HTTP %s", repo, e.http_code)
        ctx.record_unfixable(spec.NAME, file.name, e, f"{repo}: HTTP {e.http_code} from GitHub's GraphQL API")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": e.failed(KIND)}
    except github.GraphqlError as e:
        ctx.log.info("ERROR: Failed to fetch discussions for %s: %s", repo, e)
        ctx.record_unfixable(spec.NAME, file.name, e, f"{repo}: GitHub's GraphQL API answered: {e}")
        failure = {"status": "graphql_error", "reason": str(e)}
    except UnusableError as e:
        ctx.log.info("ERROR: Failed to parse discussions for %s: %s", repo, e)
        failure = {"status": "parse_failed", "reason": str(e)}
    else:
        _rebuild(ctx, file, repo, entry["maintainer"], total, nodes)
        store.write_good(file, _row(day, entry["maintainer"], total, nodes), ctx, spec.NAME)
        return
    store.write_failure(file, {"date": day, **failure}, ctx.now)


def _rebuild(ctx: Context, file: File, repo: str, maintainer: str, total: int, nodes: list[dict[str, Any]]) -> None:
    """Write a row for each day from the first discussion's to the day before the file's first row (store.fill).

    Each holds what the discussions opened by the end of that day (in the run's offset) tell: their total, who
    opened them and their categories, and the comments made by then where one of the days lacks them and
    COMMENTS_QUERY reads them all; null for the rest (PAST_UNKNOWN). Only when the query read every discussion,
    each with its creation time; otherwise nothing, and the log says why.
    """
    if len(nodes) < total:
        ctx.log.info("Not rebuilding past discussions for %s: the query read %d of %d", repo, len(nodes), total)
        return
    created = []
    for node in nodes:
        try:
            created.append(github.local_date(ctx, node.get("createdAt")))
        except ValueError:
            ctx.log.info(
                "Not rebuilding past discussions for %s: discussion #%s has no creation time", repo, node["number"]
            )
            return
    days = store.rebuild_days(file, min(created, default=None), ctx)
    stored = store.rows_by_date(file)
    commented = None
    if any(store.lacks(stored.get(day.isoformat()), "total_comments") for day in days):
        commented = _comment_days(ctx, repo, {node["number"] for node in nodes})
    rows = []
    for day in days:
        opened = [node for node, when in zip(nodes, created) if when <= day]
        row = {**_row(day.isoformat(), maintainer, len(opened), opened), **PAST_UNKNOWN}
        if commented is not None:
            row["total_comments"] = sum(1 for when in commented if when <= day)
        rows.append(row)
    store.fill(file, rows, ctx)


def _comment_days(ctx: Context, repo: str, numbers: set[Any]) -> list[date] | None:
    """The day (in the run's offset) each comment of the discussions `numbers` was made, from COMMENTS_QUERY; None,
    and the log says why, where the query fails or doesn't read every comment of every one of them."""
    try:
        data = github.graphql(ctx, COMMENTS_KIND, COMMENTS_QUERY, github.owner_and_name(repo))
        repository = data.get("repository") if isinstance(data, dict) else None
        found = repository.get("discussions") if isinstance(repository, dict) else None
        listed = found.get("nodes") if isinstance(found, dict) else None
        if not (isinstance(listed, list) and all(isinstance(node, dict) for node in listed)):
            raise unexpected(COMMENTS_KIND)
        days = []
        for node in listed:
            comments = node.get("comments")
            read = comments.get("nodes") if isinstance(comments, dict) else None
            if not (isinstance(read, list) and is_count(comments.get("totalCount"))):
                raise unexpected(COMMENTS_KIND)
            if len(read) < comments["totalCount"]:
                why = f"the query read {len(read)} of discussion #{node.get('number')}'s {comments['totalCount']}"
                ctx.log.info("Not rebuilding past comments for %s: %s", repo, why)
                return None
            days += [github.local_date(ctx, (comment or {}).get("createdAt")) for comment in read]
    except (HttpError, github.GraphqlError, UnusableError, ValueError, AttributeError) as e:
        ctx.log.info("Not rebuilding past comments for %s: %s", repo, getattr(e, "http_code", None) or e)
        return None
    if {node.get("number") for node in listed} != numbers:
        ctx.log.info("Not rebuilding past comments for %s: its discussions differ from the first query's", repo)
        return None
    return days


def _readable(node: Any) -> bool:
    """A discussion with every field the script reads, of the type it reads it as."""
    if not isinstance(node, dict):
        return False
    author, category = node.get("author") or {}, node.get("category") or {}
    comments = node.get("comments") or {}
    return (
        isinstance(author, dict)
        and isinstance(author.get("login"), (str, type(None)))
        and isinstance(category, dict)
        and isinstance(category.get("name"), (str, type(None)))
        and isinstance(comments, dict)
        and is_count(comments.get("totalCount", 0))
        and is_count(node.get("number"))
        and isinstance(node.get("title"), str)
        and is_count(node.get("upvoteCount", 0))
        and isinstance(node.get("isAnswered"), (bool, type(None)))
        and isinstance(node.get("updatedAt", ""), str)
    )


def _discussions(data: Any) -> tuple[int, list[dict[str, Any]]]:
    """The total and the fetched discussions, read whole: one it can't read makes the answer unusable."""
    repository = data.get("repository") if isinstance(data, dict) else None
    found = repository.get("discussions") if isinstance(repository, dict) else None
    if not isinstance(found, dict):
        raise unexpected(KIND)
    total, nodes = found.get("totalCount", 0), found.get("nodes")
    nodes = [] if nodes is None else nodes
    if not (is_count(total) and isinstance(nodes, list) and all(_readable(node) for node in nodes)):
        raise unexpected(KIND)
    return total, nodes


def _category(node: dict[str, Any]) -> str:
    """The key it is counted under: its category's name, NO_CATEGORY without one, and NULL_CATEGORY for a null name.

    The script counts a null name under None, which JSON writes as "null".
    """
    name: str | None = (node.get("category") or {}).get("name", NO_CATEGORY)
    return NULL_CATEGORY if name is None else name


def _row(day: str, maintainer: str, total: int, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """The script's row, its keys in its order."""
    categories: Counter[str] = Counter()
    authors: Counter[str] = Counter()
    community = answered = comments = 0
    for node in nodes:
        login = (node.get("author") or {}).get("login") or UNKNOWN_AUTHOR
        authors[login] += 1
        categories[_category(node)] += 1
        comments += (node.get("comments") or {}).get("totalCount", 0)
        community += login != maintainer
        answered += bool(node.get("isAnswered"))
    recent = [
        {
            "number": node["number"],
            "title": node["title"],
            "category": (node.get("category") or {}).get("name"),
            "author": (node.get("author") or {}).get("login"),
            "comments": (node.get("comments") or {}).get("totalCount", 0),
            "upvotes": node.get("upvoteCount", 0),
            "is_answered": node.get("isAnswered"),
            "updated_at": node.get("updatedAt", "")[:10],
        }
        for node in nodes[:RECENT]
    ]
    return {
        "date": day,
        "total": total,
        "fetched": len(nodes),
        "community_opened": community,
        "maintainer_opened": len(nodes) - community,
        "community_authors": len([a for a in authors if a != maintainer]),
        "answered": answered,
        "total_comments": comments,
        "categories": dict(categories.most_common()),
        "recent": recent,
    }
