"""github_repo_activity's code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from leadline import store
from leadline.config import is_count
from leadline.context import Context, Finding
from leadline.http import HttpError, Response, UnusableError, counts, read_json, unexpected
from leadline.sources.github import api as github
from leadline.sources.github.github_repo_activity import spec

ENV: tuple[str, ...] = (github.CREDENTIAL,)
LAGGING = False
WEEK = timedelta(days=7)
# The row's repository counts, and the field each is read from, in the script's order.
STATS = {"stars": "stargazers_count", "forks": "forks_count", "open_issues": "open_issues_count"}
# The optional lists, and the row field each one fills.
LISTS = {
    "issues": "issues_7d",
    "contributors": "contributors",
    "releases": "latest_release",
    "languages": "codebase_bytes",
}


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether each entry's repo exists and the token can read it; writes nothing, never raises for the API."""
    return [github.check_repo(ctx, entry["repo"]) for entry in cfg]


def _objects(kind: str, answer: Any) -> list[dict[str, Any]]:
    """A list the script reads each item of with .get, so every item must be an object."""
    if not isinstance(answer, list) or not all(isinstance(item, dict) for item in answer):
        raise unexpected(kind)
    return answer


def _repository(answer: Any) -> tuple[dict[str, Any], Any]:
    """The row's repository fields, in the script's order, and the owner's login (None without one)."""
    owner = answer.get("owner", {}) if isinstance(answer, dict) else None
    if not isinstance(answer, dict) or not isinstance(owner, dict):
        raise unexpected("repository")
    fields = {"repo": answer.get("full_name", ""), **counts("repository", answer, STATS)}
    if not isinstance(fields["repo"], str):
        raise unexpected("repository")
    return fields, owner.get("login")


def _commits(pages: list[Any]) -> tuple[list[str], set[str]]:
    """Every commit's message, and the logins of the week's authors with a GitHub account."""
    messages, authors = [], set()
    for commit in (commit for page in pages for commit in _objects("commits", page)):
        body = commit.get("commit")
        message = body.get("message") if isinstance(body, dict) else None
        author = commit.get("author")
        if not isinstance(message, str) or (author and not isinstance(author, dict)):
            raise unexpected("commits")
        messages.append(message)
        login = author.get("login") if author else None
        if login and not isinstance(login, str):
            raise unexpected("commits")
        if login:
            authors.add(login)
    return messages, authors


def _velocity(messages: list[str], authors: set[str], entry: Any) -> dict[str, Any]:
    fix, redesign = (re.compile(entry.get(key, default), re.IGNORECASE) for key, default in spec.PATTERNS.items())
    first_lines = [message.split("\n")[0] for message in messages]
    fixes = sum(1 for line in first_lines if fix.search(line))
    return {
        "commits": len(messages),
        "authors": len(authors),
        "fixes": fixes,
        "fix_ratio": round(fixes / max(len(messages), 1) * 100, 1),
        "redesigns": sum(1 for line in first_lines if redesign.search(line)),
    }


def _issues(answer: Any, owner: Any) -> dict[str, int]:
    """The week's issues, pull requests aside, and those whose author isn't the owner."""
    users = [issue.get("user", {}) for issue in _objects("issues", answer) if "pull_request" not in issue]
    if not all(isinstance(user, dict) for user in users):  # a null user: whose issue it is can't be told
        raise unexpected("issues")
    return {"total": len(users), "external": sum(1 for user in users if user.get("login") != owner)}


def _contributors(answer: Any) -> dict[str, Any]:
    contributors = _objects("contributors", answer)
    contributions = [c.get("contributions", 0) for c in contributors]
    top = contributors[0] if contributors else {}
    login = top.get("login", "")
    if not (all(is_count(n) for n in contributions) and isinstance(login, str)):
        raise unexpected("contributors")
    share = round((contributions[0] if contributions else 0) / max(sum(contributions), 1) * 100, 1)
    return {"total": len(contributors), "top_contributor": login, "top_contributor_pct": share}


def _latest_release(answer: Any) -> dict[str, str]:
    releases = _objects("releases", answer)
    latest = releases[0] if releases else {}
    tag, published = latest.get("tag_name", ""), latest.get("published_at") or ""
    if not (isinstance(tag, str) and isinstance(published, str)):
        raise unexpected("releases")
    return {"tag": tag, "date": published[:10]}


def _codebase_bytes(answer: Any) -> int:
    if not isinstance(answer, dict) or not all(is_count(n) for n in answer.values()):
        raise unexpected("languages")
    return sum(answer.values())


def _read(kind: str, answer: Response | str, read: Callable[[Any], Any]) -> tuple[Any, str | None]:
    """An optional list's value and None; or, where it failed or can't be used, None (unknown, never none) and why."""
    if isinstance(answer, str):
        return None, answer
    try:
        return read(read_json(kind, answer)), None
    except UnusableError as e:
        return None, str(e)


def _fetch(ctx: Context, repo: str, path: Path, kind: str, url: str) -> Response:
    """GET `url`; an error waiting can't fix is reported critical before the HttpError goes on."""
    try:
        return ctx.http.request(url, headers=github.headers())
    except HttpError as e:
        _critical(ctx, repo, path, kind, e)
        raise


def _critical(ctx: Context, repo: str, path: Path, kind: str, e: HttpError) -> None:
    """An error waiting can't fix is critical, named by its kind of request."""
    ctx.record_unfixable(spec.NAME, path.name, e, f"{repo}: HTTP {e.http_code} from GitHub's {kind} API")


def _commit_pages(ctx: Context, repo: str, path: Path, since: str) -> list[Response]:
    """Every page of the week's commits; an error waiting can't fix is reported critical before it goes on."""
    try:
        return github.numbered_pages(ctx, f"{github.API}/repos/{repo}/commits?since={since}&per_page=100")
    except HttpError as e:
        _critical(ctx, repo, path, "commits", e)
        raise


def _collect(ctx: Context, entry: Any) -> None:
    repo, label, path, day = entry["repo"], spec.label(entry), ctx.out_dir / spec.file_name(entry), ctx.date.isoformat()
    since = f"{(ctx.date - WEEK).isoformat()}T00:00:00Z"
    base = f"{github.API}/repos/{repo}"
    ctx.log.info("Collecting repo activity for %s (%s)...", repo, label)
    failure_row = {"date": day, "label": label, "repo": repo}
    kind = "repository"
    try:
        repository = _fetch(ctx, repo, path, kind, base)
        kind = "commits"
        commit_pages = _commit_pages(ctx, repo, path, since)
    except HttpError as e:
        ctx.log.info("ERROR: Failed to fetch GitHub %s for %s: HTTP %s", kind, repo, e.http_code)
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": f"{kind} request failed"}
        store.write_failure(path, {**failure_row, **failure}, ctx.now)
        return
    lists = {
        "issues": f"{base}/issues?state=all&since={since}&per_page=100",
        "contributors": f"{base}/contributors?per_page=10",
        "releases": f"{base}/releases?per_page=5",
        "languages": f"{base}/languages",
    }
    answers: dict[str, Response | str] = {}  # each list's answer, or why it failed
    for kind, url in lists.items():
        try:
            answers[kind] = _fetch(ctx, repo, path, kind, url)
        except HttpError as e:
            ctx.log.info("WARN: Failed to fetch GitHub %s for %s: HTTP %s", kind, repo, e.http_code)
            answers[kind] = f"{kind} request failed (HTTP {e.http_code})"
    try:
        fields, owner = _repository(read_json("repository", repository))
        messages, authors = _commits([read_json("commits", page) for page in commit_pages])
    except UnusableError as e:
        ctx.log.info("ERROR: Failed to parse repo activity for %s: %s", repo, e)
        store.write_failure(path, {**failure_row, "status": "parse_failed", "reason": str(e)}, ctx.now)
        return
    readers: dict[str, Callable[[Any], Any]] = {
        "issues": lambda answer: _issues(answer, owner),
        "contributors": _contributors,
        "releases": _latest_release,
        "languages": _codebase_bytes,
    }
    values: dict[str, Any] = {}
    failed: dict[str, list[str]] = {}  # each failure, as the reason names it, and the field it left null
    for kind, answer in answers.items():
        values[kind], reason = _read(kind, answer, readers[kind])
        if reason is not None:
            failed[reason] = [LISTS[kind]]
    row = {
        "date": day,
        "label": label,
        **fields,
        "codebase_bytes": values["languages"],
        "velocity_7d": _velocity(messages, authors, entry),
        "issues_7d": values["issues"],
        "contributors": values["contributors"],
        "latest_release": values["releases"],
    }
    if failed:
        ctx.log.info("WARN: Partial repo activity for %s: %s", repo, "; ".join(failed))
        store.write_partial(path, row, failed, ctx, spec.NAME)
    else:
        store.write_good(path, row, ctx, spec.NAME)
