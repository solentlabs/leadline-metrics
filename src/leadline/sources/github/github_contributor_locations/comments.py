"""github_contributor_locations: a region from the comments on the issues of a person with none.

Where an entry sets `comments`, a person whose region is still unknown after their profile, issue forms and issue
text (their profile read) gets the first region of the entry's `text` rules found in their own comments, the first
500 characters of each, on the issues the unplaced people opened. Only a comment's author is placed by it: someone
else's comment names someone else's provider (a maintainer's link, another user's report). Their source is
`comment_inference`; `[bot]` comments are left out.

Requests are bounded by what the row remembers (`comments_read`): the `text` rules' fingerprint and, for each issue
whose comments were read, its `updated_at` then. With no such row, or one read under other rules, every unplaced
person's issues are read (the pass); after that, at most one issue a run: the unread ones first, then one that changed
since it was read, the lowest number first. What a run found stays in the people's entries, so the comments are never
kept, and a person a later run places by other means loses the placement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from leadline import config, store
from leadline.context import Context
from leadline.http import HttpError, UnusableError, read_json, unexpected
from leadline.sources.github import api as github
from leadline.sources.github.github_contributor_locations import spec

SOURCE = "comment_inference"  # a person's source where a comment gave their region
STATE = "comments_read"  # the row's field: {"rules": fingerprint, "issues": {number: updated_at}}
KIND = "comments"


def fingerprint(table: dict[str, Any]) -> str:
    """A short name for the table's `text` rules, which change when they do and not with its other rules."""
    return config.fingerprint(table.get("text", []))


def place(
    ctx: Context, path: Path, entry: Any, people: list[dict[str, Any]], issues: dict[str, list[tuple[int, str]]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`people` with the region comments give each unplaced one, and the state for the row (module docstring)."""
    table, key = entry["regions"], fingerprint(entry["regions"])
    held = held_row(path, STATE)
    nightly = held is not None and held[0] == key
    read = dict(held[1]) if held is not None and nightly else {}
    kept = {p["login"]: p for p in held[2] if p.get("source") == SOURCE} if held is not None and nightly else {}
    people = [kept[p["login"]] if _unplaced(p) and p["login"] in kept else p for p in people]
    updated = {n: when for p in people if _unplaced(p) for n, when in issues.get(p["login"], [])}
    queue = sorted(n for n in updated if str(n) not in read)
    queue += sorted(n for n in updated if str(n) in read and read[str(n)] != updated[n])
    for number in queue[:1] if nightly else queue:
        try:
            said = _comments(ctx, entry["repo"], number)
        except HttpError as e:
            ctx.log.info("WARN: Failed to fetch the comments of issue %d: HTTP %s", number, e.http_code)
            ctx.record_unfixable(
                spec.NAME, path.name, e, f"{entry['repo']}: HTTP {e.http_code} from GitHub's {KIND} API"
            )
            if nightly:
                break
            continue
        except UnusableError as e:
            ctx.log.info("WARN: Could not read the %s of issue %d: %s", KIND, number, e)
            if nightly:
                break
            continue
        read[str(number)] = updated[number]
        people = _placed(table, people, said)
    _list_unplaced(ctx, entry["repo"], people, issues)
    return people, {"rules": key, "issues": dict(sorted(read.items(), key=lambda item: int(item[0])))}


def _unplaced(person: dict[str, Any]) -> bool:
    return bool(person["region"] == spec.UNKNOWN)  # a person whose profile no run read has none


def held_row(path: Path, field: str) -> tuple[Any, dict[str, str], list[dict[str, Any]]] | None:
    """The fingerprint, issues read and people of the newest stored row that remembers its `field` (the comments'
    state or the files'), or None."""
    rows = store.rows_by_date(path)
    for date in sorted(rows, reverse=True):
        row = rows[date]
        state, people = row.get(field), row.get("contributors")
        if not (store.is_good(row) or row.get("status") == store.PARTIAL):
            continue
        if isinstance(state, dict) and isinstance(state.get("issues"), dict) and isinstance(people, list):
            return state.get("rules"), state["issues"], [p for p in people if isinstance(p, dict)]
    return None


def _comments(ctx: Context, repo: str, number: int) -> list[tuple[str, str]]:
    """Each comment on an issue, as its author's login and its text; `[bot]` authors and null users left out."""
    pages = github.numbered_pages(ctx, f"{github.API}/repos/{repo}/issues/{number}/{KIND}?per_page=100")
    found = []
    for page in pages:
        answer = read_json(KIND, page)
        if not isinstance(answer, list):
            raise unexpected(KIND)
        for item in answer:
            user, body = (item.get("user"), item.get("body") or "") if isinstance(item, dict) else (None, None)
            login = user.get("login") if isinstance(user, dict) else None
            if not (isinstance(body, str) and (user is None or isinstance(login, str))):
                raise unexpected(KIND)
            if login and not login.endswith("[bot]"):
                found.append((login, body[: spec.BODY_CHARS]))
    return found


def _placed(table: dict[str, Any], people: list[dict[str, Any]], said: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """`people`, each unplaced one given the region of their own comments on the issue."""
    return [_with_region(table, person, said) if _unplaced(person) else person for person in people]


def _with_region(table: dict[str, Any], person: dict[str, Any], said: list[tuple[str, str]]) -> dict[str, Any]:
    region = config.find_region(table, " ".join(text for login, text in said if login == person["login"]))
    return {**person, "region": region, "source": SOURCE} if region else person


def _list_unplaced(
    ctx: Context, repo: str, people: list[dict[str, Any]], issues: dict[str, list[tuple[int, str]]]
) -> None:
    """Log each person still without a region and their issues' numbers, for a person to look into."""
    left = [
        f"{p['login']} ({', '.join(f'#{n}' for n, _ in sorted(issues.get(p['login'], [])))})"
        for p in people
        if _unplaced(p)
    ]
    if left:
        ctx.log.info("Unplaced contributors in %s: %s", repo, ", ".join(left))
