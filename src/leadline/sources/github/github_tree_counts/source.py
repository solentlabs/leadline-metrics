"""github_tree_counts's code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

import base64
import re
from collections import Counter
from datetime import date
from typing import Any, Callable, Mapping, Tuple
from urllib.parse import quote

from leadline import store
from leadline.context import Context, Finding
from leadline.http import HttpError
from leadline.sources.github import api as github
from leadline.sources.github.github_tree_counts import spec
from leadline.storage import File

ENV: tuple[str, ...] = (github.CREDENTIAL,)
LAGGING = False
Blob = Tuple[str, int]  # a file's blob SHA and the rank of the pattern that counted it, which says how it is read
TREE_BUDGET = (
    500  # the trees the history rebuild asks for in a night, so a long history leaves the day's limit to the rest
)
UNKNOWN = "unknown"  # the value of a file whose text the content pattern doesn't match, or that has no text
BLOB_BUDGET = 1000  # the blobs the history rebuild reads in a night, so a long history doesn't spend the day's limit
UNEXPECTED = "response had an unexpected shape"
MISSING = "the repo or its ref doesn't exist, or the token can't read it"  # check()'s error, as the ref can be missing


class _UnreadableError(Exception):
    """The answer parses as JSON but not as a git tree."""


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Each entry's repo, ref and pattern against the repo's tree; writes nothing, never raises for the API.

    A repo or ref that doesn't exist fails every night; a pattern that matches nothing counts 0 every
    night, which looks like an empty catalog rather than a wrong pattern.
    """
    return [_finding(ctx, entry) for entry in cfg]


def _where(entry: Any) -> str:
    return f"{entry['repo']}@{entry.get('ref', spec.DEFAULT_REF)}"


def _tree(ctx: Context, entry: Any, ref: str | None = None) -> Any:
    """The repo's whole tree at `ref` (the entry's by default), in one call, as the script reads it. Raises HttpError,
    ValueError."""
    ref = quote(ref or entry.get("ref", spec.DEFAULT_REF), safe="/")
    return ctx.http.get_json(
        f"{github.API}/repos/{entry['repo']}/git/trees/{ref}?recursive=1", headers=github.headers()
    )


def _matches(tree: Any, entry: Any) -> tuple[list[tuple[Any, Any, Blob | None]], bool]:
    """Each counted path's group, item and blob (its SHA and its pattern's rank; None for an entry that isn't a file
    with one, or that belongs to a pattern whose files `content` doesn't read), and whether the tree is truncated.
    Raises _UnreadableError.

    A path is searched, as the script does, so the pattern decides whether it is anchored. An entry
    without a path reads as "", as in the script. Each (group, item) pair is counted from the first
    pattern in the entry's list that matches it: its paths, and no other pattern's.
    """
    entries = tree.get("tree") if isinstance(tree, dict) else None
    if not isinstance(entries, list):
        raise _UnreadableError
    compiled = [(re.compile(pattern), read) for pattern, read in spec.patterns(entry)]
    found = []
    for item in entries:
        path = item.get("path", "") if isinstance(item, dict) else None
        if not isinstance(path, str):
            raise _UnreadableError
        for rank, (regex, read) in enumerate(compiled):
            match = regex.search(path)
            if match:
                sha = item.get("sha")
                blob = (sha, rank) if read and item.get("type") == "blob" and isinstance(sha, str) else None
                found.append((rank, match.group(1), match.group(2), blob))
    first: dict[tuple[Any, Any], int] = {}
    for rank, group, name, _ in found:
        first[(group, name)] = min(rank, first.get((group, name), rank))
    matches = [(group, name, blob) for rank, group, name, blob in found if rank == first[(group, name)]]
    return matches, bool(tree.get("truncated"))


def _row(
    entry: Any, day: str, matches: list[tuple[Any, Any, Blob | None]], truncated: bool, by_value: dict[str, int] | None
) -> dict[str, Any]:
    """The row for `day`: the counts under the entry's keys, whether the tree was truncated, and with `content` its
    counts by value under its key (`by_value`, None for a part that failed)."""
    items = {(group, item) for group, item, _ in matches}
    per_group = Counter(group for group, _ in items)
    counts = {
        "items": len(items),
        "files": len(matches),
        "groups": len(per_group),
        "items_per_group": dict(sorted(per_group.items())),
    }
    keys = entry.get("keys", {})
    row = {"date": day, **{keys.get(name, name): counts[name] for name in spec.COUNTS}, "tree_truncated": truncated}
    if "content" in entry:
        row[entry["content"]["key"]] = by_value
    return row


class _Blobs:
    """What a run knows of each blob's value, the content pattern's group 1 in its text, read once for the run.

    It starts from every blob the file's rows list. A blob a run reads is kept in `fresh` until `take`
    hands it to the row that is the first to hold it, so across the rows each blob is listed once.
    """

    def __init__(self, ctx: Context, entry: Any, file: File) -> None:
        self.ctx, self.repo = ctx, entry["repo"]
        content = entry["content"]
        self.key = content["key"] + spec.BLOBS_SUFFIX
        self.names = content.get("map", {})
        # How each pattern's files are read, by rank: the entry's pattern and map, or the pattern's own.
        self.readers = {
            rank: (re.compile(own.get("pattern", content["pattern"]), re.MULTILINE), own.get("map", self.names))
            for rank, (_, own) in enumerate(spec.patterns(entry))
            if isinstance(own, dict)
        }
        self.default = (re.compile(content["pattern"], re.MULTILINE), self.names)
        self.values = self.canonical(_seen(file, self.key))
        self.fresh: dict[str, str] = {}
        self.reads = 0

    def canonical(self, values: Mapping[str, str]) -> dict[str, str]:
        """`values`, blob to value, with each value under its mapped name: the rows may list a blob under the name
        it was written with."""
        return {sha: self.names.get(value, value) for sha, value in values.items()}

    def count(self, matches: list[tuple[Any, Any, Blob | None]]) -> dict[str, int]:
        """The files of `matches` counted by value, by key; a blob read once however many files hold it. Raises
        HttpError for a blob that can't be fetched, ValueError for an answer that isn't a blob."""
        counts = Counter(self._value(*blob) if blob else UNKNOWN for _, _, blob in matches)
        return dict(sorted(counts.items()))

    def _value(self, sha: str, rank: int) -> str:
        if sha not in self.values:
            self.reads += 1
            pattern, names = self.readers.get(rank, self.default)
            found = pattern.search(self._text(sha))
            value = (found.group(1) if found else "") or UNKNOWN
            self.values[sha] = self.fresh[sha] = names.get(value, value)
        return self.values[sha]

    def _text(self, sha: str) -> str:
        blob = self.ctx.http.get_json(f"{github.API}/repos/{self.repo}/git/blobs/{sha}", headers=github.headers())
        content = blob.get("content") if isinstance(blob, dict) else None
        if not isinstance(content, str) or blob.get("encoding") != "base64":
            raise ValueError("not a base64 blob")
        return base64.b64decode(content).decode("utf-8", errors="replace")

    def take(self, kept: Mapping[str, str] | None = None) -> dict[str, list[str]]:
        """The blobs read since the last take, with `kept`, grouped by value, the SHAs in order; none after it."""
        read = {**(kept or {}), **self.fresh}
        self.fresh = {}
        grouped: dict[str, list[str]] = {}
        for sha, value in sorted(read.items()):
            grouped.setdefault(value, []).append(sha)
        return dict(sorted(grouped.items()))


def _blobs_read(row: Any, key: str) -> dict[str, str]:
    """The blobs a row lists under `key`, `{value: [sha, ...]}`, as `{sha: value}`; what isn't that is left out."""
    listed = row.get(key) if isinstance(row, dict) else None
    if not isinstance(listed, dict):
        return {}
    return {
        sha: value for value, shas in listed.items() if isinstance(shas, list) for sha in shas if isinstance(sha, str)
    }


def _seen(file: File, key: str) -> dict[str, str]:
    """Every blob the file's rows list (partial rows too): a blob's SHA names its text, so any row's is good."""
    seen: dict[str, str] = {}
    for row in store.rows(file):
        seen.update(_blobs_read(row, key))
    return seen


def _collect(ctx: Context, entry: Any) -> None:
    repo, file, day = entry["repo"], ctx.file(spec.file_name(entry)), ctx.date.isoformat()
    ctx.log.info("Collecting GitHub tree counts for %s...", repo)
    try:
        matches, truncated = _matches(_tree(ctx, entry), entry)
    except HttpError as e:
        ctx.log.info("ERROR: Failed to fetch the tree of %s: HTTP %s", repo, e.http_code)
        ctx.record_unfixable(spec.NAME, file.name, e, f"{repo}: HTTP {e.http_code} from GitHub's git trees API")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": "tree request failed"}
    except ValueError:
        failure = {"status": "parse_failed", "reason": "response was not JSON"}
    except _UnreadableError:
        failure = {"status": "parse_failed", "reason": UNEXPECTED}
    else:
        if truncated:
            ctx.log.info("WARN: the tree of %s was truncated; its counts are floors, not totals", repo)
        blobs = _Blobs(ctx, entry, file) if "content" in entry else None
        _write_row(ctx, entry, file, matches, truncated, blobs)
        _rebuild(ctx, entry, file, blobs)
        return
    if failure["status"] == "parse_failed":
        ctx.log.info("ERROR: Failed to parse the tree of %s: %s", repo, failure["reason"])
    store.write_failure(file, {"date": day, **failure}, ctx.now)


def _write_row(
    ctx: Context,
    entry: Any,
    file: File,
    matches: list[tuple[Any, Any, Blob | None]],
    truncated: bool,
    blobs: _Blobs | None,
) -> None:
    """Write the run date's row. With `content`, a blob that can't be read leaves its counts null, and the row partial
    (ADR: a partly failed row keeps its good parts); the blobs it did read are kept either way."""
    day, failed = ctx.date.isoformat(), {}
    by_value = None
    if blobs is not None:
        try:
            by_value = blobs.count(matches)
        except HttpError as e:
            ctx.log.info("ERROR: Failed to fetch a blob of %s: HTTP %s", entry["repo"], e.http_code)
            reason = f"{entry['repo']}: HTTP {e.http_code} from GitHub's git blobs API"
            ctx.record_unfixable(spec.NAME, file.name, e, reason)
            failed[f"blob request failed (HTTP {e.http_code})"] = [entry["content"]["key"]]
        except ValueError:
            ctx.log.info("ERROR: Failed to parse a blob of %s: not a base64 blob", entry["repo"])
            failed["blob response was not usable"] = [entry["content"]["key"]]
    row = _row(entry, day, matches, truncated, by_value)
    if blobs is not None:
        kept = blobs.canonical(_blobs_read(store.rows_by_date(file).get(day), blobs.key))  # an earlier run of the day's
        read = blobs.take(kept)
        if read:
            row[blobs.key] = read
    if failed:
        store.write_partial(file, row, failed, ctx, spec.NAME)
    else:
        store.write_good(file, row, ctx, spec.NAME)


def _rebuild(ctx: Context, entry: Any, file: File, blobs: _Blobs | None) -> None:
    """Write a row for each day before the file's first a night wrote, each counted in the tree of the day's last
    commit, from the first day whose tree matches anything; rows an earlier rebuild added mark where it starts.
    With `content`, each day through yesterday whose good row lacks the counts by value gets them too, and a night
    reads no more than BLOB_BUDGET blobs (a tree that needs more waits for the next night). A night asks for no more
    than TREE_BUDGET trees, and goes on the next. A failed request stops it,
    keeping the days counted so far (store.fill)."""
    repo, end = entry["repo"], store.first_night(file, ctx)
    by_date = store.rows_by_date(file)
    earliest = min(filter(None, map(store.parse_date, by_date)), default=None)
    if end is None or earliest is None:
        return
    key = entry["content"]["key"] if blobs is not None else None
    days_from = store.recompute_days if key else store.rebuild_days  # with content, through yesterday

    def needed(day: date) -> bool:
        stored = by_date.get(day.isoformat())
        return stored is None or (key is not None and store.is_good(stored) and store.lacks(stored, key))

    resumed = bool(by_date[earliest.isoformat()].get(store.BACKFILLED))  # an earlier rebuild found the start
    if resumed and not any(needed(day) for day in days_from(file, earliest, ctx)):
        return
    rows: list[dict[str, Any]] = []
    try:
        last = _last_commits(ctx, github.numbered_pages(ctx, _commits_url(entry)))
        start = earliest if resumed else min(last, default=earliest)
        _count_days(ctx, entry, blobs, days_from(file, start, ctx), needed, last, resumed, rows)
    except HttpError as e:
        ctx.log.info("Stopped rebuilding past tree counts for %s: HTTP %s; the next night goes on", repo, e.http_code)
    except (ValueError, _UnreadableError):
        ctx.log.info("Stopped rebuilding past tree counts for %s: %s", repo, UNEXPECTED)
    store.fill(file, rows, ctx)


def _count_days(
    ctx: Context,
    entry: Any,
    blobs: _Blobs | None,
    days: list[date],
    needed: Callable[[date], bool],
    last: dict[date, str],
    resumed: bool,
    rows: list[dict[str, Any]],
) -> None:
    """Add to `rows` the row of each of `days` that is `needed`, counted in the tree of the day's last commit (`last`,
    and the commit before it for a day with none), each tree asked for once. Nothing is added before a first match
    (`resumed` is that the start was found), nor once the night's blob reads reach BLOB_BUDGET. Raises HttpError,
    ValueError, _UnreadableError, the rows so far kept in `rows`."""
    counted: dict[str, dict[str, Any]] = {}  # each commit's row, by sha
    first_read = blobs.reads if blobs is not None else 0
    start = days[0] if days else None
    sha = next((last[day] for day in sorted(last, reverse=True) if start is not None and day < start), None)
    for day in days:
        sha = last.get(day, sha)
        if sha is None or not needed(day):
            continue
        read: dict[str, list[str]] = {}
        if sha not in counted:
            if len(counted) >= TREE_BUDGET:
                ctx.log.info(
                    "Stopped rebuilding past tree counts for %s after %d tree(s); the next night goes on",
                    entry["repo"],
                    len(counted),
                )
                break
            if blobs is not None and blobs.reads - first_read >= BLOB_BUDGET:
                spent = blobs.reads - first_read
                ctx.log.info(
                    "Stopped rebuilding past tree counts for %s after %d blob read(s); the next night goes on",
                    entry["repo"],
                    spent,
                )
                break
            counted[sha], read = _counted(ctx, entry, sha, blobs)
        if rows or resumed or counted[sha][entry.get("keys", {}).get("files", "files")]:
            rows.append({**counted[sha], "date": day.isoformat(), **({blobs.key: read} if blobs and read else {})})


def _commits_url(entry: Any) -> str:
    ref = entry.get("ref", spec.DEFAULT_REF)
    return f"{github.API}/repos/{entry['repo']}/commits?per_page=100" + (
        "" if ref == "HEAD" else f"&sha={quote(ref, safe='/')}"
    )


def _counted(ctx: Context, entry: Any, sha: str, blobs: _Blobs | None) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """The row for the tree of commit `sha`, and the blobs reading it was the first to do, grouped by value. Raises
    HttpError, ValueError, _UnreadableError."""
    matches, truncated = _matches(_tree(ctx, entry, sha), entry)
    row = _row(entry, "", matches, truncated, blobs.count(matches) if blobs is not None else None)
    return row, blobs.take() if blobs is not None else {}


def _last_commits(ctx: Context, pages: list[Any]) -> dict[date, str]:
    """The sha of each day's last commit (in the run's offset, by committer date), from the commits listing's pages.
    Raises ValueError for an answer it can't read."""
    dated = []
    for page in pages:
        commits = page.json()
        if not isinstance(commits, list):
            raise ValueError("not a list of commits")
        for commit in commits:
            if not (isinstance(commit, dict) and isinstance(commit.get("sha"), str)):
                raise ValueError("a commit without its sha")
            details = commit.get("commit")
            committer = details.get("committer") if isinstance(details, dict) else None
            when = committer.get("date") if isinstance(committer, dict) else None
            dated.append((github.parse_time(when), commit["sha"]))
    return {when.astimezone(ctx.now.tzinfo).date(): sha for when, sha in sorted(dated)}


def _finding(ctx: Context, entry: Any) -> Finding:
    where = _where(entry)
    try:
        matches, truncated = _matches(_tree(ctx, entry), entry)
    except HttpError as e:
        return github.failed_finding(where, e, "the tree", missing=MISSING)
    except ValueError:
        return Finding("warn", f"{where}: could not read the tree: not JSON")
    except _UnreadableError:
        return Finding("warn", f"{where}: could not read the tree: unexpected answer")
    verb = "the patterns match" if isinstance(entry["pattern"], list) else "the pattern matches"
    if not matches:
        return Finding("warn", f"{where}: {verb} no path in the tree, so every night counts 0")
    matched = f"{verb} {len(matches)} paths"
    if truncated:
        return Finding("warn", f"{where}: the tree is truncated, so the counts are floors; {matched} in it")
    return Finding("ok", f"{where}: {matched} in the tree")
