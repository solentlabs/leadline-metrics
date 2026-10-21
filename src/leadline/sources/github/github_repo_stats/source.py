"""github_repo_stats's code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from leadline import store
from leadline.context import Context, Finding
from leadline.http import HttpError, UnusableError, counts, read_json
from leadline.sources.github import api as github
from leadline.sources.github.github_repo_stats import spec

ENV: tuple[str, ...] = (github.CREDENTIAL,)
LAGGING = False
# The row's repository counts, and the field each is read from, in the script's order.
STATS = {
    "stars": "stargazers_count",
    "forks": "forks_count",
    "watchers": "subscribers_count",
    "open_issues": "open_issues_count",
}
TRAFFIC = ("views", "clones")


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether each entry's repo exists, and then whether the token can read its traffic; writes nothing."""
    findings = []
    for entry in cfg:
        repo = entry["repo"]
        found = github.check_repo(ctx, repo)
        findings.append(found)
        if found.level == "ok":
            findings.append(github.check_traffic(ctx, repo))
    return findings


def _traffic(ctx: Context, repo: str, path: Path, kind: str) -> tuple[dict[str, int | None], str | None]:
    """The row's fields for `kind`, and None; or, where they failed or can't be used, nulls and the reason."""
    fields = {f"{kind}_14d": "count", f"{kind}_unique_14d": "uniques"}
    try:
        answer = ctx.http.request(f"{github.API}/repos/{repo}/traffic/{kind}", headers=github.headers())
        return dict(counts(kind, read_json(kind, answer), fields)), None
    except HttpError as e:
        ctx.log.info("WARN: Failed to fetch GitHub %s for %s: HTTP %s", kind, repo, e.http_code)
        ctx.record_unfixable(spec.NAME, path.name, e, f"{repo}: HTTP {e.http_code} from GitHub's traffic API")
        reason = f"{kind} request failed (HTTP {e.http_code})"
    except UnusableError as e:
        ctx.log.info("WARN: Failed to parse GitHub %s for %s: %s", kind, repo, e)
        reason = str(e)
    return dict.fromkeys(fields), reason


def _collect(ctx: Context, entry: Any) -> None:
    repo, path, day = entry["repo"], ctx.out_dir / spec.file_name(entry), ctx.date.isoformat()
    ctx.log.info("Collecting GitHub stats for %s...", repo)
    try:
        repository = ctx.http.request(f"{github.API}/repos/{repo}", headers=github.headers())
    except HttpError as e:
        ctx.log.info("ERROR: Failed to fetch the repository %s: HTTP %s", repo, e.http_code)
        ctx.record_unfixable(spec.NAME, path.name, e, f"{repo}: HTTP {e.http_code} from GitHub's repository API")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": "repository request failed"}
        store.write_failure(path, {"date": day, "repo": repo, **failure}, ctx.now)
        return
    row: dict[str, Any] = {"date": day, "repo": repo}
    failed: dict[str, list[str]] = {}  # each failure, as the reason names it, and the fields it left null
    traffic: dict[str, int | None] = {}
    for kind in TRAFFIC:  # asked for whatever the repository holds, as the script asks
        fields, reason = _traffic(ctx, repo, path, kind)
        traffic.update(fields)
        if reason is not None:
            failed[reason] = list(fields)
    try:
        row.update(counts("repository", read_json("repository", repository), STATS))
    except UnusableError as e:
        ctx.log.info("ERROR: Failed to parse the repository %s: %s", repo, e)
        store.write_failure(path, {**row, "status": "parse_failed", "reason": str(e)}, ctx.now)
        return
    row.update(traffic)
    if failed:
        store.write_partial(path, row, failed, ctx, spec.NAME)
    else:
        store.write_good(path, row, ctx, spec.NAME)
