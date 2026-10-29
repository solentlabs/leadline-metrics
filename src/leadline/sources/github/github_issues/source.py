"""github_issues's code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

import re
import statistics
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

from leadline import config, store
from leadline.config import is_count
from leadline.context import Context, Finding
from leadline.http import HttpError, Response, UnusableError, counts, read_json, unexpected
from leadline.sources.github import api as github
from leadline.sources.github.github_issues import regions, spec
from leadline.storage import File

ENV: tuple[str, ...] = (github.CREDENTIAL,)
LAGGING = False
# The row's repository counts, and the field each is read from, in the script's order.
STATS = {"stars": "stargazers_count", "forks": "forks_count", "watchers": "subscribers_count"}
# The lists that date each of the repo's counts: the path, the media type that adds the times (None: the default), the
# field holding the time, and the field holding the person who did it. Both come oldest first, so a count on a day is
# the number dated by its end.
HISTORIES = {
    "stars": ("stargazers?per_page=100", "application/vnd.github.star+json", "starred_at", "user"),
    "forks": ("forks?sort=oldest&per_page=100", None, "created_at", "owner"),
}
HISTORY_LIMIT = 5000  # the most stars or forks a fill reads dates for, 50 pages; a repo with more is left unfilled
# What every night would read with a maintainer login that isn't one, as check() says.
MISREAD = "every issue and pull request would count as the community's"
BOTS = ("dependabot", "github-actions")  # bots whose login has no `[bot]`
HIGH_ENGAGEMENT = 5  # comments that make an issue highly engaged
TOP = 5  # the most commented issues listed


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether each repo exists, the token can read its issues, its maintainer's login and its queue's label exist.

    It writes nothing. The repo and its first page of open issues are what collection asks for, so a
    run reuses those answers; a maintainer's login and a queue's label cost one request each. A login
    that doesn't exist, or is spelled otherwise, would misread every night without a failure (MISREAD).
    """
    findings = []
    for entry in cfg:
        repo = entry["repo"]
        found = [github.check_repo(ctx, repo)]
        if found[-1].level == "ok":
            path = "/issues?state=open&per_page=100&page=1"  # collection's first request for issues
            found.append(
                github.check_access(ctx, repo, path, fetching="its issues", ok="the token can read its issues")
            )
        if found[-1].level == "ok":
            if entry.get("maintainer"):
                found.append(github.check_maintainer(ctx, repo, entry["maintainer"], misread=MISREAD))
            if "queue" in entry:
                found.append(_check_label(ctx, repo, entry["queue"]["label"]))
        findings += found
    return findings


def _check_label(ctx: Context, repo: str, label: str) -> Finding:
    try:
        ctx.http.request(f"{github.API}/repos/{repo}/labels/{quote(label, safe='')}", headers=github.headers())
    except HttpError as e:
        if e.code == 404:
            return Finding("warn", f'{repo}: the repo has no label "{label}", so its queue is always empty')
        return github.failed_finding(repo, e, f'its label "{label}"')
    return Finding("ok", f'{repo}: the queue\'s label "{label}" exists')


def _collect(ctx: Context, entry: Any) -> None:
    repo, file, day = entry["repo"], ctx.file(spec.file_name(entry)), ctx.date.isoformat()
    ctx.log.info("Collecting GitHub issues snapshot for %s...", repo)
    base = f"{github.API}/repos/{repo}"
    kind = "repository"
    try:
        repository = ctx.http.request(base, headers=github.headers())
        kind = "open issues"
        open_pages = github.numbered_pages(ctx, f"{base}/issues?state=open&per_page=100")
        ctx.log.info("Fetched %d page(s) of open issues for %s", len(open_pages), repo)
        kind = "all issues"
        all_pages = github.numbered_pages(ctx, f"{base}/issues?state=all&per_page=100")
        ctx.log.info("Fetched %d page(s) of all issues for %s", len(all_pages), repo)
    except HttpError as e:
        ctx.log.info("ERROR: Failed to fetch GitHub %s for %s: HTTP %s", kind, repo, e.http_code)
        ctx.record_unfixable(spec.NAME, file.name, e, f"{repo}: HTTP {e.http_code} from GitHub's {kind} API")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": e.failed(kind)}
        store.write_failure(file, {"date": day, **failure}, ctx.now)
        return
    try:
        stats = counts("repository", read_json("repository", repository), STATS)
        open_items, all_items = _items("open issues", open_pages), _items("all issues", all_pages)
    except UnusableError as e:
        ctx.log.info("ERROR: Failed to process GitHub issues snapshot for %s: %s", repo, e)
        store.write_failure(file, {"date": day, "status": "parse_failed", "reason": str(e)}, ctx.now)
        return
    tallies = {}
    if spec.by_region(entry):
        tallies = regions.count(ctx, entry, file, stats, lambda field: _listed(ctx, base, field), HISTORY_LIMIT)
    _rebuild(ctx, file, all_items, entry.get("kinds"), base, stats, tallies)
    row = _snapshot(ctx, entry, stats, open_items, all_items, _previous(file, day))
    row["repo"] = {**stats, **regions.on(tallies, ctx.date)}
    store.write_good(file, row, ctx, spec.NAME)


def _issues(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The issues among `items`, pull requests aside."""
    return [i for i in items if "pull_request" not in i]


def _by(ctx: Context, when: Any, through: date | None) -> bool:
    """Whether the time `when` is set and falls on or before the end of the day `through` (in the run's offset); any
    time is, with no `through`."""
    return bool(when) and (through is None or github.local_date(ctx, when) <= through)


def _totals(ctx: Context, issues_ever: list[dict[str, Any]], through: date | None = None) -> dict[str, int]:
    """The issues opened and closed, ever, or through the end of the day `through` (in the run's offset)."""
    return {
        "opened_total": sum(1 for i in issues_ever if _by(ctx, i["created_at"], through)),
        "closed_total": sum(1 for i in issues_ever if _by(ctx, i.get("closed_at"), through)),
    }


def _closed_not_planned(issues_ever: list[dict[str, Any]]) -> int:
    """The issues closed as not planned, ever (an issue reopened is open, whatever it was closed as)."""
    return sum(1 for i in issues_ever if i.get("state") == "closed" and i.get("state_reason") == "not_planned")


def _merged(prs_ever: list[dict[str, Any]]) -> int:
    """The pull requests merged, ever: those whose `pull_request` says when."""
    return sum(1 for i in prs_ever if isinstance(i["pull_request"], dict) and i["pull_request"].get("merged_at"))


def _kind(kinds: list[dict[str, str]], item: dict[str, Any]) -> str:
    """The first kind in `kinds` whose label `item` carries, else OTHER_KIND."""
    labels = _labels(item)
    return next((kind["name"] for kind in kinds if kind["label"] in labels), spec.OTHER_KIND)


def _opened_by_kind(
    ctx: Context, issues_ever: list[dict[str, Any]], kinds: list[dict[str, str]], through: date | None = None
) -> dict[str, int]:
    """The issues opened, ever or through the end of the day `through`, each counted once under its kind: the
    kinds in order, then OTHER_KIND, with a zero for a kind none has."""
    counts = dict.fromkeys([*(kind["name"] for kind in kinds), spec.OTHER_KIND], 0)
    for i in issues_ever:
        if _by(ctx, i["created_at"], through):
            counts[_kind(kinds, i)] += 1
    return counts


def _open_by_kind(
    ctx: Context, issues_ever: list[dict[str, Any]], kinds: list[dict[str, str]], through: date | None = None
) -> dict[str, int]:
    """The issues open, now or at the end of the day `through`: opened by then and not closed by then, each counted
    once under its kind as `_opened_by_kind` counts the opened."""
    counts = dict.fromkeys([*(kind["name"] for kind in kinds), spec.OTHER_KIND], 0)
    for i in issues_ever:
        if _by(ctx, i["created_at"], through) and not _by(ctx, i.get("closed_at"), through):
            counts[_kind(kinds, i)] += 1
    return counts


def _rebuild(
    ctx: Context,
    file: File,
    items: list[dict[str, Any]],
    kinds: list[dict[str, str]] | None,
    base: str,
    stats: dict[str, int],
    tallies: dict[str, regions.Counted],
) -> None:
    """Fill each past row's totals where it lacks them, as of its day, from every issue's dates; write a row for each
    day before the file's first, back to the first issue's, with the open issues and pull requests (store.fill); and
    with `kinds`, count every past row's issues opened by kind again (`_recompute_kinds`).

    The script began writing the totals later than its rows; every issue's and pull request's opening and closing time
    comes with each run, so the counts of every earlier day can be counted again. The same fill gives the days before
    the first row with the repo's stars or forks those counts (`_history`), back to the first star or fork if earlier.
    With `tallies` (the stars and forks by region, `regions.count`), each of those rows and days also gets the counts
    by region this run can tell.
    """
    issues_ever = _issues(items)
    prs_ever = [i for i in items if "pull_request" in i]
    days = [day for day in map(store.parse_date, store.rows_by_date(file)) if day is not None and day < ctx.date]

    def counted(day: date) -> dict[str, Any]:
        """The day's totals, and its issues opened by kind with `kinds`."""
        by_kind = {"opened_by_kind": _opened_by_kind(ctx, issues_ever, kinds, day)} if kinds else {}
        return {**_totals(ctx, issues_ever, day), **by_kind}

    if kinds:  # before the fill, which would add a kind a stored row lacks inside its counts
        _recompute_kinds(ctx, file, issues_ever, kinds)
    rebuilt = {day: {"date": day.isoformat(), "issues": counted(day)} for day in days}
    first = min((github.local_date(ctx, i["created_at"]) for i in issues_ever), default=None)
    history = _history(ctx, file, base, stats, store.rebuild_days(file, first, ctx))
    first = min([day for day in [first, *(dates[0] for _, dates in history.values() if dates)] if day], default=None)
    for day in store.rebuild_days(file, first, ctx):
        totals = counted(day)
        prs = _totals(ctx, prs_ever, day)
        opened = {
            "open": totals["opened_total"] - totals["closed_total"],
            "open_prs": prs["opened_total"] - prs["closed_total"],
        }
        by_kind = {"open_by_kind": _open_by_kind(ctx, issues_ever, kinds, day)} if kinds else {}
        rebuilt[day] = {"date": day.isoformat(), "issues": {**opened, **totals, **by_kind}}
    for day, row in rebuilt.items():
        counted_repo = {**_repo_on(history, day), **regions.on(tallies, day)}
        if counted_repo:
            rebuilt[day] = {"date": row["date"], "repo": counted_repo, "issues": row["issues"]}
    store.fill(file, [rebuilt[day] for day in sorted(rebuilt)], ctx)
    if kinds:  # after the fill, which gives a row its opened_by_kind, so open_by_kind comes after it
        _recompute_open_kinds(ctx, file, issues_ever, kinds)


def _anchor(file: File, ctx: Context, field: str) -> date | None:
    """The date of the file's first past row a night wrote (not marked backfilled) with a real count of `field`
    (`repo.stars`): the days before it are the ones a fill gives it. None without one."""
    found = [
        day
        for row in store.rows(file)
        if store.is_good(row) and not row.get(store.BACKFILLED) and is_count(store.value(row, f"repo.{field}"))
        for day in [store.parse_date(row.get("date"))]
        if day is not None and day < ctx.date
    ]
    return min(found, default=None)


def _lacks(row: dict[str, Any] | None, field: str) -> bool:
    """Whether a fill would give the day's stored `row` (none for a day without one) the repo's count of `field`."""
    if row is None:
        return True
    repo = row.get("repo", {})
    return store.is_good(row) and isinstance(repo, dict) and field not in repo


def _listed(ctx: Context, base: str, field: str) -> list[regions.Listed]:
    """Every star or fork of the repo, oldest first, from GitHub's list of them: the day in the run's offset, and the
    login of the person who did it (None where the list gives none).

    Raises HttpError for a page that fails, and UnusableError for one that isn't a list of objects each with a time.
    """
    path, accept, stamp, who = HISTORIES[field]
    found = []
    for page in github.numbered_pages(ctx, f"{base}/{path}", accept=accept):
        answer = read_json(field, page)
        if not isinstance(answer, list):
            raise unexpected(field)
        for item in answer:
            try:
                day = github.local_date(ctx, item.get(stamp) if isinstance(item, dict) else None)
            except ValueError:
                raise unexpected(field) from None
            person = item.get(who)
            login = person.get("login") if isinstance(person, dict) else None
            found.append(regions.Listed(day, login if isinstance(login, str) else None))
    return sorted(found, key=lambda listed: listed.day)


def _history(
    ctx: Context, file: File, base: str, stats: dict[str, int], rebuilt_days: list[date]
) -> dict[str, tuple[date, list[date]]]:
    """For each of `HISTORIES` the fill is due for: the day its count first has a real one in the file, and the dates
    the repo's stars or forks happened, each listed once and only when a past row lacks the count.

    A past good row before the real one, or a day to write (`rebuilt_days`), lacks it; a count with nothing to fill is
    not asked for, nor one of a repo with none (every day's is 0) or with more than HISTORY_LIMIT (said in the log).
    A list that can't be read is left unfilled the same way, said in the log, and tried again the next night: a
    failed fill costs the run's row nothing. A star or fork since removed is no longer listed, so the early counts of
    a repo that lost some can be low.
    """
    stored, found = store.rows_by_date(file), {}
    for field in HISTORIES:
        anchor = _anchor(file, ctx, field)
        days = {day for day in map(store.parse_date, stored) if day is not None} | set(rebuilt_days)
        if anchor is None or not any(day < anchor and _lacks(stored.get(day.isoformat()), field) for day in days):
            continue
        try:
            if stats[field] > HISTORY_LIMIT:
                raise UnusableError(f"{stats[field]} {field} are more than the {HISTORY_LIMIT} it reads")
            found[field] = (anchor, [listed.day for listed in _listed(ctx, base, field)] if stats[field] else [])
        except HttpError as e:
            ctx.log.info("Left the %s of %s unfilled: HTTP %s", field, file.name, e.http_code)
        except UnusableError as e:
            ctx.log.info("Left the %s of %s unfilled: %s", field, file.name, e)
    return found


def _repo_on(history: dict[str, tuple[date, list[date]]], day: date) -> dict[str, int]:
    """The repo's counts at the end of `day` that `history` fills: the stars or forks dated by then, for each count
    whose first real one comes after `day`; no `watchers`, which GitHub keeps no dates for."""
    return {field: bisect_right(dates, day) for field, (anchor, dates) in history.items() if day < anchor}


def _recompute_kinds(ctx: Context, file: File, issues_ever: list[dict[str, Any]], kinds: list[dict[str, str]]) -> None:
    """Count again, under `kinds`, the issues opened by kind of each past good row that has them (store.recompute;
    ADR: derived history follows today's rules): the rest of its `issues` is kept, and a changed count is a
    recorded correction. A row without the counts, or with a null, is left to the fill."""
    rows = []
    for stamp, row in store.rows_by_date(file).items():
        day, issues = store.parse_date(stamp), row.get("issues")
        if day is None or day >= ctx.date or not store.is_good(row) or not isinstance(issues, dict):
            continue
        if not isinstance(issues.get("opened_by_kind"), dict):
            continue
        rows.append(
            {"date": stamp, "issues": {**issues, "opened_by_kind": _opened_by_kind(ctx, issues_ever, kinds, day)}}
        )
    store.recompute(file, rows, ctx, spec.NAME)


def _recompute_open_kinds(
    ctx: Context, file: File, issues_ever: list[dict[str, Any]], kinds: list[dict[str, str]]
) -> None:
    """Give each past good row its open issues by kind, counted from the issues' dates as of its day, and count again
    the ones that have them (store.recompute; ADR: derived history follows today's rules); a changed count is a
    recorded correction. A row whose `open` the counts don't add up to, or that has none, is left as it is, and said
    so: the dates can't tell what that night's snapshot saw (an issue reopened, deleted or transferred since)."""
    rows, unmatched = [], []
    for stamp, row in store.rows_by_date(file).items():
        day, issues = store.parse_date(stamp), row.get("issues")
        if day is None or day >= ctx.date or not store.is_good(row) or not isinstance(issues, dict):
            continue
        counts = _open_by_kind(ctx, issues_ever, kinds, day)
        if not (is_count(issues.get("open")) and sum(counts.values()) == issues["open"]):
            if "open_by_kind" not in issues:
                unmatched.append(stamp)
            continue
        rows.append({"date": stamp, "issues": {**issues, "open_by_kind": counts}})
    store.recompute(file, rows, ctx, spec.NAME)
    if unmatched:
        ctx.log.info(
            "Left %d past row(s) of %s without open_by_kind: their open doesn't match the issues' dates (%s to %s)",
            len(unmatched),
            file.name,
            min(unmatched),
            max(unmatched),
        )


def _readable(item: Any) -> bool:
    """An issue with every field the script reads, of the type it reads it as."""
    if not isinstance(item, dict):
        return False
    user, labels = item.get("user") or {}, item.get("labels", [])
    try:
        github.parse_time(item.get("created_at"))
        if item.get("closed_at"):
            github.parse_time(item["closed_at"])
    except ValueError:
        return False
    return (
        isinstance(user, dict)
        and isinstance(user.get("login", ""), str)
        and isinstance(labels, list)
        and all(isinstance(label, dict) and isinstance(label.get("name"), str) for label in labels)
        and is_count(item.get("number"))
        and isinstance(item.get("title"), str)
        and is_count(item.get("comments", 0))
        and isinstance(item.get("body") or "", str)
    )


def _items(kind: str, pages: list[Response]) -> list[dict[str, Any]]:
    """Every page's issues, read whole: one page or issue it can't read makes the list unusable."""
    items: list[Any] = []
    for page in pages:
        answer = read_json(kind, page)
        if not isinstance(answer, list):  # `{}` too, which the script reads as no issues
            raise unexpected(kind)
        items += answer
    if not all(_readable(item) for item in items):
        raise unexpected(kind)
    return items


def _earlier(row: dict[str, Any], day: str) -> bool:
    """A good row dated before `day`, or undated, as the script finds the previous snapshot."""
    dated = row.get("date", "")
    if not (store.is_good(row) and isinstance(dated, str) and dated < day):
        return False
    try:
        if dated:
            datetime.fromisoformat(dated)
    except ValueError:  # a date the flow can't count days from
        return False
    return True


def _previous(file: File, day: str) -> dict[str, Any] | None:
    """The previous snapshot: the last earlier good row in the file, in file order."""
    earlier = [row for row in store.rows(file) if _earlier(row, day)]
    return earlier[-1] if earlier else None


def _flow(previous: dict[str, Any] | None, day: str, opened: int, closed: int) -> dict[str, int]:
    """The flow since the previous snapshot; none where it has no totals."""
    totals = previous.get("issues") if previous is not None else None
    if not (
        isinstance(totals, dict) and is_count(totals.get("opened_total")) and is_count(totals.get("closed_total", 0))
    ):
        return {"days": 0, "opened": 0, "closed": 0}
    dated = previous.get("date") if previous is not None else None
    days = max(1, (datetime.fromisoformat(day) - datetime.fromisoformat(dated)).days) if dated else 1
    return {"days": days, "opened": opened - totals["opened_total"], "closed": closed - totals.get("closed_total", 0)}


def _login(item: dict[str, Any]) -> str:
    login: str = (item.get("user") or {}).get("login", "")
    return login


def _authors(items: list[dict[str, Any]], maintainer: str) -> dict[str, int]:
    """Who opened `items`: the maintainer (none when empty), the community and bots."""
    owner = others = bots = 0
    logins = set()
    for item in items:
        login = _login(item)
        if login.endswith("[bot]") or login in BOTS:
            bots += 1
        elif maintainer and login == maintainer:
            owner += 1
        else:
            others += 1
            logins.add(login)
    return {"maintainer": owner, "community": others, "bot": bots, "community_authors": len(logins)}


def _labels(item: dict[str, Any]) -> list[str]:
    return [label["name"] for label in item.get("labels", [])]


def _snapshot(
    ctx: Context,
    entry: Any,
    stats: dict[str, int],
    open_items: list[dict[str, Any]],
    all_items: list[dict[str, Any]],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """The script's row, its keys in its order."""
    maintainer, day = entry.get("maintainer", ""), ctx.date.isoformat()
    issues = _issues(open_items)
    prs = [i for i in open_items if "pull_request" in i]
    ages = [(ctx.now - github.parse_time(i["created_at"])).days for i in issues]
    comments = [i.get("comments", 0) for i in issues]
    top = sorted(issues, key=lambda i: i.get("comments", 0), reverse=True)[:TOP]
    label_open: dict[str, int] = defaultdict(int)
    for i in issues:
        for name in _labels(i):
            label_open[name] += 1
    issues_ever = _issues(all_items)
    prs_ever = [i for i in all_items if "pull_request" in i]
    totals = _totals(ctx, issues_ever)
    opened_total, closed_total = totals["opened_total"], totals["closed_total"]
    label_opened: dict[str, int] = defaultdict(int)
    label_closed: dict[str, int] = defaultdict(int)
    for i in issues_ever:
        for name in _labels(i):
            label_opened[name] += 1
            if i.get("closed_at"):
                label_closed[name] += 1
    assigned = sum(1 for i in issues if i.get("assignees"))
    labeled = sum(1 for i in issues if i.get("labels"))
    row: dict[str, Any] = {
        "date": day,
        "repo": stats,
        "issues": {
            "open": len(issues),
            "open_prs": len(prs),
            "opened_total": opened_total,
            "closed_total": closed_total,
            "closed_not_planned_total": _closed_not_planned(issues_ever),
            "since_prev": _flow(previous, day, opened_total, closed_total),
        },
        "contribution": {
            "issues": _authors(issues_ever, maintainer),
            "prs": _authors(prs_ever, maintainer),
            "prs_total": len(prs_ever),
            "prs_merged_total": _merged(prs_ever),
        },
        "age": {
            "avg_days": round(statistics.mean(ages)) if ages else 0,
            "median_days": round(statistics.median(ages)) if ages else 0,
            "oldest_days": max(ages, default=0),
            "newest_days": min(ages, default=0),
        },
        "engagement": {
            "total_comments": sum(comments),
            "avg_comments": round(sum(comments) / len(issues), 1) if issues else 0,
            "high_engagement": sum(1 for n in comments if n >= HIGH_ENGAGEMENT),
            "top_issues": [{"number": i["number"], "title": i["title"], "comments": i.get("comments", 0)} for i in top],
        },
        "labels": {
            name: {"open": label_open[name], "opened_total": label_opened[name], "closed_total": label_closed[name]}
            for name in sorted(set(label_open) | set(label_opened))
        },
        "triage": {
            "assigned": assigned,
            "unassigned": len(issues) - assigned,
            "labeled": labeled,
            "unlabeled": len(issues) - labeled,
            "milestoned": sum(1 for i in issues if i.get("milestone")),
        },
    }
    if "kinds" in entry:
        row["issues"]["opened_by_kind"] = _opened_by_kind(ctx, issues_ever, entry["kinds"])
        row["issues"]["open_by_kind"] = _open_by_kind(ctx, issues_ever, entry["kinds"])
    if "queue" in entry:
        queue = entry["queue"]
        key = queue.get("key", spec.QUEUE_KEY)
        row[key] = _queue(ctx, queue, entry.get("regions"), issues, issues_ever, maintainer)
    return row


def _queue(
    ctx: Context,
    queue: dict[str, Any],
    table: dict[str, Any] | None,
    issues: list[dict[str, Any]],
    issues_ever: list[dict[str, Any]],
    maintainer: str,
) -> dict[str, Any]:
    """The open issues with the queue's label, and its requests resolved; keys the config leaves out stay out."""
    label = queue["label"]
    match = queue.get("body_match")
    pattern = re.compile(match["pattern"], re.IGNORECASE) if match else None
    waiting = sorted((i for i in issues if label in _labels(i)), key=lambda i: i["created_at"])
    items: list[dict[str, Any]] = []
    ages: list[int] = []
    matched = 0
    by_region: dict[str, int] = defaultdict(int)
    for i in waiting:
        ages.append((ctx.now - github.parse_time(i["created_at"])).days)
        body = i.get("body") or ""
        item: dict[str, Any] = {"number": i["number"], "title": i["title"], "age_days": ages[-1]}
        item["comments"] = i.get("comments", 0)
        if match and pattern is not None:
            found = bool(pattern.search(body))
            item[match["flag_key"]] = found
            matched += found
        item["labels"] = _labels(i)
        if table is not None:
            formed = config.form_region(table, [config.read_form(table, body)])
            item["region"] = formed or config.find_region(table, f"{i['title']} {body}") or spec.UNKNOWN_REGION
            by_region[item["region"]] += 1
        items.append(item)
    requested = [i for i in issues_ever if label in _labels(i)]
    resolved = [
        (github.parse_time(i["closed_at"]) - github.parse_time(i["created_at"])).days
        for i in requested
        if i.get("closed_at")
    ]
    section: dict[str, Any] = {"total": len(waiting), "avg_age_days": round(statistics.mean(ages)) if ages else 0}
    if match:
        section[match["count_key"]] = matched
    section["with_comments"] = sum(1 for i in waiting if i.get("comments", 0) > 0)
    if table is not None:
        section["regions"] = dict(sorted(by_region.items(), key=lambda counted: -counted[1]))
    section["requested_total"] = len(requested)
    section["resolved_total"] = len(resolved)
    section["median_days_to_resolve"] = round(statistics.median(resolved)) if resolved else 0
    section["requesters"] = _authors(requested, maintainer)
    section[queue.get("items_key", spec.ITEMS_KEY)] = items
    return section
