"""github_releases's code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

from typing import Any

from leadline import store
from leadline.context import Context, Finding
from leadline.http import HttpError
from leadline.sources.github import api as github
from leadline.sources.github.github_releases import spec

ENV: tuple[str, ...] = (github.CREDENTIAL,)
LAGGING = False
UNEXPECTED = "response had an unexpected shape"


class _UnreadableError(Exception):
    """The answer parses as JSON but not as a list of releases."""


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether each entry's repo exists and the token can read it; writes nothing, never raises for the API."""
    return [github.check_repo(ctx, entry["repo"]) for entry in cfg]


def _list(value: Any) -> list[dict[str, Any]]:
    """A list the script iterates, whose items it reads with .get, so they must be objects."""
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise _UnreadableError
    return value


def _asset(asset: dict[str, Any]) -> dict[str, Any]:
    if "name" not in asset:  # the script reads a['name']: its row is lost without one
        raise _UnreadableError
    return {"name": asset["name"], "downloads": asset.get("download_count", 0), "size": asset.get("size", 0)}


def _row(releases: Any, repo: str, day: str) -> dict[str, Any]:
    """The script's row. An empty object, which the script reads as no releases, is unreadable too."""
    out = [
        {
            "tag": release.get("tag_name"),
            "prerelease": release.get("prerelease", False),
            "draft": release.get("draft", False),
            "published_at": release.get("published_at"),
            "assets": [_asset(asset) for asset in _list(release.get("assets", []))],
        }
        for release in _list(releases)
    ]
    return {"date": day, "repo": repo, "releases": out}


def _collect(ctx: Context, entry: Any) -> None:
    repo, path, day = entry["repo"], ctx.out_dir / spec.file_name(entry), ctx.date.isoformat()
    ctx.log.info("Collecting GitHub release asset downloads for %s...", repo)
    try:
        row = _row(
            ctx.http.get_json(f"{github.API}/repos/{repo}/releases?per_page=100", headers=github.headers()), repo, day
        )
    except HttpError as e:
        ctx.log.info("ERROR: Failed to fetch releases for %s: HTTP %s", repo, e.http_code)
        ctx.record_unfixable(spec.NAME, path.name, e, f"{repo}: HTTP {e.http_code} from GitHub's releases API")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": "releases request failed"}
    except ValueError:
        failure = {"status": "parse_failed", "reason": "response was not JSON"}
    except _UnreadableError:
        failure = {"status": "parse_failed", "reason": UNEXPECTED}
    else:
        store.write_good(path, row, ctx, spec.NAME)
        return
    if failure["status"] == "parse_failed":
        ctx.log.info("ERROR: Failed to parse releases for %s: %s", repo, failure["reason"])
    store.write_failure(path, {"date": day, "repo": repo, **failure}, ctx.now)
