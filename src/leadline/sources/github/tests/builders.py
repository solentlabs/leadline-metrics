"""GitHub builders: the REST API and the GraphQL API, shared by the GitHub sources."""

from __future__ import annotations

import base64
import hashlib
import json
import random
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from scenarios.builders.common import Exchange, Json, exchange, stable_id

GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL = f"{GITHUB_API}/graphql"


def _node_id(kind: str, number: int) -> str:
    return base64.b64encode(f"{kind}{number}".encode()).decode()


def github_user(login: str) -> Json:
    """A user, as the API embeds one (an issue's author, assignee or closer)."""
    number = stable_id(login)
    api = f"{GITHUB_API}/users/{quote(login)}"
    return {
        "login": login,
        "id": number,
        "node_id": _node_id("U_", number),
        "avatar_url": f"https://avatars.githubusercontent.com/u/{number}?v=4",
        "gravatar_id": "",
        "url": api,
        "html_url": f"https://github.com/{quote(login)}",
        "followers_url": f"{api}/followers",
        "following_url": f"{api}/following{{/other_user}}",
        "gists_url": f"{api}/gists{{/gist_id}}",
        "starred_url": f"{api}/starred{{/owner}}{{/repo}}",
        "subscriptions_url": f"{api}/subscriptions",
        "organizations_url": f"{api}/orgs",
        "repos_url": f"{api}/repos",
        "events_url": f"{api}/events{{/privacy}}",
        "received_events_url": f"{api}/received_events",
        "type": "Bot" if login.endswith("[bot]") else "User",
        "user_view_type": "public",
        "site_admin": False,
    }


def github_label(full_name: str, name: str) -> Json:
    number = stable_id(f"{full_name}#{name}")
    return {
        "id": number,
        "node_id": _node_id("LA_", number),
        "url": f"{GITHUB_API}/repos/{full_name}/labels/{quote(name)}",
        "name": name,
        "color": "0e8a16",
        "default": name in ("bug", "documentation", "enhancement", "question"),
        "description": "",
        "archived_at": None,
        "archived_by": None,
    }


def github_milestone(full_name: str, number: int, title: str = "Next release") -> Json:
    """A milestone, in the documented shape (every milestone in the capture is null)."""
    api = f"{GITHUB_API}/repos/{full_name}/milestones/{number}"
    return {
        "url": api,
        "html_url": f"https://github.com/{full_name}/milestone/{number}",
        "labels_url": f"{api}/labels",
        "id": stable_id(f"{full_name}/milestone/{number}"),
        "node_id": _node_id("MI_", number),
        "number": number,
        "title": title,
        "description": "",
        "creator": github_user("example-maintainer"),
        "open_issues": 1,
        "closed_issues": 0,
        "state": "open",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "due_on": None,
        "closed_at": None,
    }


def github_repo(full_name: str, *, stars: int = 0, forks: int = 0, watchers: int = 0, open_issues: int = 0) -> Json:
    """A repository, as its owner's token sees it."""
    owner, name = full_name.split("/")
    api = f"{GITHUB_API}/repos/{full_name}"
    org = github_user(owner)
    org["type"] = "Organization"
    return {
        "id": stable_id(full_name),
        "node_id": _node_id("R_", stable_id(full_name)),
        "name": name,
        "full_name": full_name,
        "private": False,
        "owner": org,
        "html_url": f"https://github.com/{full_name}",
        "description": "An example project.",
        "fork": False,
        "url": api,
        "forks_url": f"{api}/forks",
        "keys_url": f"{api}/keys{{/key_id}}",
        "collaborators_url": f"{api}/collaborators{{/collaborator}}",
        "teams_url": f"{api}/teams",
        "hooks_url": f"{api}/hooks",
        "issue_events_url": f"{api}/issues/events{{/number}}",
        "events_url": f"{api}/events",
        "assignees_url": f"{api}/assignees{{/user}}",
        "branches_url": f"{api}/branches{{/branch}}",
        "tags_url": f"{api}/tags",
        "blobs_url": f"{api}/git/blobs{{/sha}}",
        "git_tags_url": f"{api}/git/tags{{/sha}}",
        "git_refs_url": f"{api}/git/refs{{/sha}}",
        "trees_url": f"{api}/git/trees{{/sha}}",
        "statuses_url": f"{api}/statuses/{{sha}}",
        "languages_url": f"{api}/languages",
        "stargazers_url": f"{api}/stargazers",
        "contributors_url": f"{api}/contributors",
        "subscribers_url": f"{api}/subscribers",
        "subscription_url": f"{api}/subscription",
        "commits_url": f"{api}/commits{{/sha}}",
        "git_commits_url": f"{api}/git/commits{{/sha}}",
        "comments_url": f"{api}/comments{{/number}}",
        "issue_comment_url": f"{api}/issues/comments{{/number}}",
        "contents_url": f"{api}/contents/{{+path}}",
        "compare_url": f"{api}/compare/{{base}}...{{head}}",
        "merges_url": f"{api}/merges",
        "archive_url": f"{api}/{{archive_format}}{{/ref}}",
        "downloads_url": f"{api}/downloads",
        "issues_url": f"{api}/issues{{/number}}",
        "pulls_url": f"{api}/pulls{{/number}}",
        "milestones_url": f"{api}/milestones{{/number}}",
        "notifications_url": f"{api}/notifications{{?since,all,participating}}",
        "labels_url": f"{api}/labels{{/name}}",
        "releases_url": f"{api}/releases{{/id}}",
        "deployments_url": f"{api}/deployments",
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2026-09-23T00:00:00Z",
        "pushed_at": "2026-09-23T00:00:00Z",
        "git_url": f"git://github.com/{full_name}.git",
        "ssh_url": f"git@github.com:{full_name}.git",
        "clone_url": f"https://github.com/{full_name}.git",
        "svn_url": f"https://github.com/{full_name}",
        "homepage": "",
        "size": 1000,
        "stargazers_count": stars,
        "watchers_count": stars,
        "language": "Python",
        "has_issues": True,
        "has_projects": True,
        "has_downloads": True,
        "has_wiki": False,
        "has_pages": False,
        "has_discussions": True,
        "forks_count": forks,
        "mirror_url": None,
        "archived": False,
        "disabled": False,
        "open_issues_count": open_issues,
        "license": {
            "key": "mit",
            "name": "MIT License",
            "spdx_id": "MIT",
            "url": f"{GITHUB_API}/licenses/mit",
            "node_id": _node_id("L_", 13),
        },
        "allow_forking": True,
        "is_template": False,
        "web_commit_signoff_required": False,
        "has_pull_requests": True,
        "pull_request_creation_policy": "all",
        "topics": ["example"],
        "visibility": "public",
        "forks": forks,
        "open_issues": open_issues,
        "watchers": stars,
        "default_branch": "main",
        "permissions": {"admin": True, "maintain": True, "push": True, "triage": True, "pull": True},
        "temp_clone_token": "",
        "allow_squash_merge": True,
        "allow_merge_commit": True,
        "allow_rebase_merge": True,
        "allow_auto_merge": False,
        "delete_branch_on_merge": False,
        "allow_update_branch": False,
        "use_squash_pr_title_as_default": False,
        "squash_merge_commit_message": "COMMIT_MESSAGES",
        "squash_merge_commit_title": "COMMIT_OR_PR_TITLE",
        "merge_commit_message": "PR_TITLE",
        "merge_commit_title": "MERGE_MESSAGE",
        "custom_properties": {},
        "organization": org,
        "security_and_analysis": {
            check: {"status": "disabled"}
            for check in (
                "secret_scanning",
                "secret_scanning_push_protection",
                "dependabot_security_updates",
                "secret_scanning_non_provider_patterns",
                "secret_scanning_validity_checks",
            )
        },
        "network_count": forks,
        "subscribers_count": watchers,
    }


def github_issue(
    full_name: str,
    number: int,
    *,
    title: str = "An example issue",
    author: str | None = "example-user",
    created_at: str = "2026-09-01T10:00:00Z",
    closed_at: str | None = None,
    comments: int = 0,
    labels: Sequence[str] = (),
    assignees: Sequence[str] = (),
    milestone: Json | None = None,
    body: str | None = "",
    pull_request: bool = False,
    merged: bool = True,
) -> Json:
    """An item of an issue list: an issue, or with `pull_request` a pull request.

    A closed pull request is merged unless `merged` is false. `author` None is an item whose user is null.
    """
    api = f"{GITHUB_API}/repos/{full_name}/issues/{number}"
    html = f"https://github.com/{full_name}/{'pull' if pull_request else 'issues'}/{number}"
    people = [github_user(login) for login in assignees]
    closer = github_user("example-maintainer") if closed_at else None
    item: Json = {
        "url": api,
        "repository_url": f"{GITHUB_API}/repos/{full_name}",
        "labels_url": f"{api}/labels{{/name}}",
        "comments_url": f"{api}/comments",
        "events_url": f"{api}/events",
        "html_url": html,
        "id": stable_id(f"{full_name}#{number}"),
        "node_id": _node_id("PR_" if pull_request else "I_", number),
        "number": number,
        "title": title,
        "user": github_user(author) if author is not None else None,
        "labels": [github_label(full_name, name) for name in labels],
        "state": "closed" if closed_at else "open",
        "locked": False,
        "assignee": people[0] if people else None,
        "assignees": people,
        "milestone": milestone,
        "comments": comments,
        "created_at": created_at,
        "updated_at": closed_at or created_at,
        "closed_at": closed_at,
        "author_association": "NONE",
        "issue_field_values": [],
        "type": None,
        "active_lock_reason": None,
    }
    if pull_request:
        item["draft"] = False
        item["pull_request"] = {
            "url": f"{GITHUB_API}/repos/{full_name}/pulls/{number}",
            "html_url": html,
            "diff_url": f"{html}.diff",
            "patch_url": f"{html}.patch",
            "merged_at": closed_at if closed_at and merged else None,
        }
    else:
        item["sub_issues_summary"] = {"total": 0, "completed": 0, "percent_completed": 0}
        item["issue_dependencies_summary"] = {
            "blocked_by": 0,
            "total_blocked_by": 0,
            "blocking": 0,
            "total_blocking": 0,
        }
    item.update(
        {
            "body": body,
            "closed_by": closer,
            "reactions": {
                "url": f"{api}/reactions",
                "total_count": 0,
                **dict.fromkeys(("+1", "-1", "laugh", "hooray", "confused", "heart", "rocket", "eyes"), 0),
            },
            "timeline_url": f"{api}/timeline",
            "performed_via_github_app": None,
            "state_reason": ("completed" if closed_at else None) if not pull_request else None,
        }
    )
    if not pull_request:
        item["pinned_comment"] = None
    return item


def _timestamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def made_up_issues(
    rng: random.Random,
    full_name: str,
    count: int,
    *,
    now: datetime,
    first: int = 1,
    authors: Sequence[str] = ("example-maintainer", "example-user", "another-user", "dependabot[bot]"),
    labels: Sequence[str] = ("bug", "enhancement", "documentation"),
) -> list[Json]:
    """`count` issues and pull requests numbered from `first`, opened in the year before `now`, many closed."""
    items = []
    for number in range(first, first + count):
        created = now - timedelta(days=rng.randint(0, 365), seconds=rng.randint(0, 86399))
        closed = created + timedelta(days=rng.randint(0, 60)) if rng.random() < 0.7 else None
        closed_at = _timestamp(closed) if closed and closed < now else None
        items.append(
            github_issue(
                full_name,
                number,
                title=f"Example item {number}",
                author=rng.choice(authors),
                created_at=_timestamp(created),
                closed_at=closed_at,
                comments=rng.randint(0, 12),
                labels=rng.sample(list(labels), rng.randint(0, min(2, len(labels)))),
                assignees=["example-maintainer"] if rng.random() < 0.3 else [],
                body="Steps to reproduce, and what happened.",
                pull_request=rng.random() < 0.4,
                merged=rng.random() < 0.85,
            )
        )
    return items


def _cursor(page: int) -> str:
    return quote(base64.b64encode(f"cursor:v2:{page}".encode()).decode(), safe="")


def github_issue_pages(full_name: str, state: str, issues: Sequence[Json], *, per_page: int = 100) -> list[Exchange]:
    """The list `issues?state=...` as the API pages it: `per_page` items a page, Link to the next and previous."""
    chunks = [list(issues[i : i + per_page]) for i in range(0, len(issues), per_page)] or [[]]
    pages = []
    for page, chunk in enumerate(chunks, 1):
        link = f"{GITHUB_API}/repositories/{stable_id(full_name)}/issues?state={state}&per_page={per_page}&page="
        targets = []
        if page < len(chunks):
            targets.append(f'<{link}{page + 1}&after={_cursor(page)}>; rel="next"')
        if page > 1:
            targets.append(f'<{link}{page - 1}&before={_cursor(page)}>; rel="prev"')
        url = f"{GITHUB_API}/repos/{full_name}/issues?state={state}&per_page={per_page}&page={page}"
        pages.append(exchange(url, chunk, headers={"link": ", ".join(targets)} if targets else None))
    return pages


def _sha(text: str) -> str:
    """A made-up git SHA, the same for the same text."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()  # noqa: S324 (an ID, not a security use)


def github_profile(login: str, location: str | None = None, *, own: bool = False) -> Json:
    """A user's profile (`users/LOGIN`); `own` is the token owner's own, with its private counts."""
    profile = github_user(login)
    profile.update(
        {
            "name": None,
            "company": None,
            "blog": "",
            "location": location,
            "email": None,
            "hireable": None,
            "bio": None,
            "twitter_username": None,
            "public_repos": 3,
            "public_gists": 0,
            "followers": 2,
            "following": 1,
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2026-09-01T00:00:00Z",
        }
    )
    if own:
        profile.update(
            {
                "private_gists": 0,
                "total_private_repos": 1,
                "owned_private_repos": 1,
                "disk_usage": 1000,
                "collaborators": 0,
                "two_factor_authentication": True,
                "plan": {"name": "free", "space": 976562499, "collaborators": 0, "private_repos": 10000},
            }
        )
    return profile


def github_numbered_pages(
    full_name: str, path: str, query: str, items: Sequence[Json], *, per_page: int, numbered: bool = True
) -> list[Exchange]:
    """A list the API pages by number (`path?query`), each page with Link to the others.

    `numbered` pages are asked for with `&page=N`. Without it only the first page is asked for, as the
    script does for contributors and releases, so only it is returned.
    """
    chunks = [list(items[i : i + per_page]) for i in range(0, len(items), per_page)] or [[]]
    link = f"{GITHUB_API}/repositories/{stable_id(full_name)}/{path}?{query}&page="
    pages = []
    for page, chunk in enumerate(chunks, 1):
        rels = [("prev", page - 1)] if page > 1 else []
        if page < len(chunks):
            rels += [("next", page + 1), ("last", len(chunks))]
        if page > 1:
            rels.append(("first", 1))
        headers = {"link": ", ".join(f'<{link}{n}>; rel="{rel}"' for rel, n in rels)} if rels else None
        url = f"{GITHUB_API}/repos/{full_name}/{path}?{query}" + (f"&page={page}" if numbered else "")
        pages.append(exchange(url, chunk, headers=headers))
        if not numbered:
            break
    return pages


def github_traffic(kind: str, days: Mapping[date, tuple[int, int]]) -> Json:
    """`traffic/views` or `traffic/clones` (`kind`): each day's (count, unique visitors), and the totals."""
    items = [{"timestamp": f"{day.isoformat()}T00:00:00Z", "count": c, "uniques": u} for day, (c, u) in days.items()]
    return {"count": sum(c for c, _ in days.values()), "uniques": sum(u for _, u in days.values()), kind: items}


def github_referrer(host: str, count: int, uniques: int) -> Json:
    """An item of `traffic/popular/referrers`."""
    return {"referrer": host, "count": count, "uniques": uniques}


def github_blob_sha(text: str) -> str:
    """A file's blob SHA: made up from its text alone, so two files with the same text share one, as in git."""
    return _sha(f"blob:{text}")


def github_tree(
    full_name: str,
    files: Sequence[str],
    *,
    truncated: bool = False,
    contents: Mapping[str, str] | None = None,
) -> Json:
    """`git/trees/REF?recursive=1`: each of `files`, and each folder above one, in path order.

    A file in `contents` (path to text) has the blob SHA of its text (github_blob_sha); any other, one made up from its
    path.
    """
    contents = contents or {}
    folders = {"/".join(f.split("/")[:depth]) for f in files for depth in range(1, f.count("/") + 1)}
    entries = []
    for path in sorted(folders | set(files)):
        sha = github_blob_sha(contents[path]) if path in contents else _sha(f"{full_name}:{path}")
        entry: Json = {"path": path, "mode": "040000", "type": "tree", "sha": sha}
        if path in folders:
            entry["url"] = f"{GITHUB_API}/repos/{full_name}/git/trees/{sha}"
        else:
            entry.update({"mode": "100755" if path.endswith(".sh") else "100644", "type": "blob"})
            entry.update({"size": 100 + len(path), "url": f"{GITHUB_API}/repos/{full_name}/git/blobs/{sha}"})
        entries.append(entry)
    root = _sha(full_name)
    url = f"{GITHUB_API}/repos/{full_name}/git/trees/{root}"
    return {"sha": root, "url": url, "tree": entries, "truncated": truncated}


def github_blob(full_name: str, text: str) -> Json:
    """`git/blobs/SHA`: a file's text, base64 in 60-character lines, as GitHub gives it."""
    sha = github_blob_sha(text)
    encoded = base64.b64encode(text.encode("utf-8")).decode()
    lines = [encoded[i : i + 60] for i in range(0, len(encoded), 60)]
    return {
        "sha": sha,
        "node_id": _node_id("B_", stable_id(sha)),
        "size": len(text.encode("utf-8")),
        "url": f"{GITHUB_API}/repos/{full_name}/git/blobs/{sha}",
        "content": "\n".join(lines) + "\n",
        "encoding": "base64",
    }


def github_release_asset(full_name: str, tag: str, name: str, downloads: int, size: int = 2048) -> Json:
    number = stable_id(f"{full_name}@{tag}/{name}")
    return {
        "url": f"{GITHUB_API}/repos/{full_name}/releases/assets/{number}",
        "id": number,
        "node_id": _node_id("RA_", number),
        "name": name,
        "label": "",
        "uploader": github_user("example-maintainer"),
        "content_type": "application/zip",
        "state": "uploaded",
        "size": size,
        "digest": f"sha256:{hashlib.sha256(name.encode()).hexdigest()}",
        "download_count": downloads,
        "created_at": "2026-09-01T12:00:00Z",
        "updated_at": "2026-09-01T12:00:00Z",
        "browser_download_url": f"https://github.com/{full_name}/releases/download/{tag}/{name}",
    }


def github_release(
    full_name: str,
    tag: str,
    *,
    published_at: str | None = "2026-09-01T12:00:00Z",
    prerelease: bool = False,
    draft: bool = False,
    assets: Sequence[Json] = (),
    mentions: int | None = None,
    reactions: bool = False,
) -> Json:
    """A release. `mentions` adds how many people its notes mention, `reactions` its reactions."""
    number = stable_id(f"{full_name}@{tag}")
    api = f"{GITHUB_API}/repos/{full_name}/releases/{number}"
    made = published_at or "2026-09-01T12:00:00Z"
    release: Json = {
        "url": api,
        "assets_url": f"{api}/assets",
        "upload_url": f"https://uploads.github.com/repos/{full_name}/releases/{number}/assets{{?name,label}}",
        "html_url": f"https://github.com/{full_name}/releases/tag/{tag}",
        "id": number,
        "author": github_user("example-maintainer"),
        "node_id": _node_id("RE_", number),
        "tag_name": tag,
        "target_commitish": "main",
        "name": tag,
        "draft": draft,
        "immutable": False,
        "prerelease": prerelease,
        "created_at": made,
        "updated_at": made,
        "published_at": published_at,
        "assets": list(assets),
        "tarball_url": f"{GITHUB_API}/repos/{full_name}/tarball/{tag}",
        "zipball_url": f"{GITHUB_API}/repos/{full_name}/zipball/{tag}",
        "body": "What changed.",
    }
    if mentions is not None:
        release["mentions_count"] = mentions
    if reactions:
        counts = dict.fromkeys(("+1", "-1", "laugh", "hooray", "confused", "heart", "rocket", "eyes"), 0)
        release["reactions"] = {"url": f"{api}/reactions", "total_count": 1, **counts, "heart": 1}
    return release


def github_commit(full_name: str, message: str, *, when: str, author: str | None = "example-user") -> Json:
    """An item of `commits?since=...`. `author` None is a commit whose author has no GitHub account."""
    sha = _sha(f"{full_name}:{message}:{when}")
    parent, tree = _sha(f"parent of {sha}"), _sha(f"tree of {sha}")
    person = {"name": "Example Person", "email": "person@example.invalid", "date": when}
    user = github_user(author) if author is not None else None
    return {
        "sha": sha,
        "node_id": _node_id("C_", stable_id(sha)),
        "commit": {
            "author": person,
            "committer": dict(person),
            "message": message,
            "tree": {"sha": tree, "url": f"{GITHUB_API}/repos/{full_name}/git/trees/{tree}"},
            "url": f"{GITHUB_API}/repos/{full_name}/git/commits/{sha}",
            "comment_count": 0,
            "verification": {
                "verified": True,
                "reason": "valid",
                "signature": "-----BEGIN PGP SIGNATURE-----\nmade up\n-----END PGP SIGNATURE-----",
                "payload": f"tree {tree}\nparent {parent}\n\n{message}",
                "verified_at": when,
            },
        },
        "url": f"{GITHUB_API}/repos/{full_name}/commits/{sha}",
        "html_url": f"https://github.com/{full_name}/commit/{sha}",
        "comments_url": f"{GITHUB_API}/repos/{full_name}/commits/{sha}/comments",
        "author": user,
        "committer": user,
        "parents": [
            {
                "sha": parent,
                "url": f"{GITHUB_API}/repos/{full_name}/commits/{parent}",
                "html_url": f"https://github.com/{full_name}/commit/{parent}",
            }
        ],
    }


def github_contributor(login: str, contributions: int) -> Json:
    """An item of `contributors`."""
    return {**github_user(login), "contributions": contributions}


# --- GitHub GraphQL API ---


def graphql_body(query: str, variables: Mapping[str, Any]) -> str:
    """A GraphQL request's body, as the script's Python writes it."""
    return json.dumps({"query": query, "variables": dict(variables)})


def github_discussion(
    number: int,
    *,
    author: str = "example-user",
    category: str = "Q&A",
    comments: int = 0,
    upvotes: int = 0,
    answered: bool | None = None,
    updated_at: str = "2026-09-20T10:00:00Z",
) -> Json:
    """A node of the discussions query. `answered` is None in a category that takes no answers."""
    return {
        "number": number,
        "title": f"Example discussion {number}",
        "createdAt": "2026-08-01T10:00:00Z",
        "updatedAt": updated_at,
        "isAnswered": answered,
        "upvoteCount": upvotes,
        "comments": {"totalCount": comments},
        "author": {"login": author},
        "category": {"name": category},
    }


def github_discussions(nodes: Sequence[Json], total: int | None = None) -> Json:
    """The discussions query's result: `total` discussions in all, `nodes` the ones fetched."""
    found = {"totalCount": len(nodes) if total is None else total, "nodes": list(nodes)}
    return {"data": {"repository": {"discussions": found}}}


def github_issue_node(
    number: int,
    created_at: str,
    *,
    author: str,
    closed_at: str | None = None,
    comments: Sequence[tuple[str, str]] = (),
) -> Json:
    """A node of the issues-with-comments query; `comments` are (when, login), oldest first."""
    return {
        "number": number,
        "createdAt": created_at,
        "closedAt": closed_at,
        "author": {"login": author},
        "comments": {"nodes": [{"createdAt": when, "author": {"login": login}} for when, login in comments]},
    }


def github_issue_nodes(nodes: Sequence[Json], *, next_page: bool = False) -> Json:
    """One page of the issues-with-comments query; `next_page` says another follows."""
    page_info = {"hasNextPage": next_page, "endCursor": _cursor(nodes[-1]["number"]) if nodes else None}
    return {"data": {"repository": {"issues": {"pageInfo": page_info, "nodes": list(nodes)}}}}
