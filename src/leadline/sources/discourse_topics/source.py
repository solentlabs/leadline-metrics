"""discourse_topics's code that makes requests and writes rows: collect, check; the declarations are in spec.py."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from leadline import store
from leadline.context import Context, Finding
from leadline.http import HttpError, UnusableError, counts, read_json, unexpected
from leadline.sources.discourse_topics import spec

ENV: tuple[str, ...] = ()  # a public forum: no credentials
LAGGING = False
# The row's counts read as they are, and the field each is read from; the post count is read apart.
COUNTS = {"views": "views", "likes": "like_count"}
POSTS_CHUNK = 20  # the posts asked for by id in one request, the forum's own chunk size


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry's topics in turn; one topic's failure row doesn't stop the next."""
    for entry in cfg:
        path = ctx.out_dir / spec.file_name(entry)
        for topic in entry["topics"]:
            _collect(ctx, entry["base_url"], topic, path)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether each entry's forum answers and each of its topics exists; writes nothing, never raises for the API.

    It asks for each topic, as collection does, so a run reuses those answers. A forum that doesn't
    answer at all is one finding, and its other topics aren't asked for.
    """
    findings = []
    for entry in cfg:
        base_url = entry["base_url"]
        for topic in entry["topics"]:
            which = f"topic {topic['id']} ({topic['label']})"
            try:
                _read(ctx, base_url, topic["id"])
            except HttpError as e:
                if e.code == 0:
                    findings.append(Finding("warn", f"{base_url}: the forum did not answer: HTTP 0"))
                    break
                if e.code == 404:
                    findings.append(Finding("warn", f"{base_url}: no {which}: HTTP 404"))
                else:
                    level = "error" if e.unfixable else "warn"
                    findings.append(Finding(level, f"{base_url}: could not read {which}: HTTP {e.code}"))
            except UnusableError as e:
                findings.append(Finding("warn", f"{base_url}: could not read {which}: {e}"))
            else:
                findings.append(Finding("ok", f"{base_url}: {which} exists"))
    return findings


def _read(ctx: Context, base_url: str, topic_id: int) -> dict[str, Any]:
    """The topic's views, replies, likes and last post, as the script reads them.

    Raises HttpError for a failed request, and UnusableError for an answer it can't use.
    """
    return _counted(_topic(ctx, base_url, topic_id))


def _topic(ctx: Context, base_url: str, topic_id: int) -> Any:
    """The topic's answer, read as JSON. Raises HttpError, UnusableError."""
    return read_json("topic", ctx.http.request(f"{base_url.rstrip('/')}/t/{topic_id}.json"))


def _counted(answer: Any) -> dict[str, Any]:
    """The topic's views, replies, likes and last post from its answer. Raises UnusableError."""
    found = counts(
        "topic", answer, {**COUNTS, "posts": "posts_count"}
    )  # also refuses a non-object, names what is missing
    last = answer.get("last_posted_at")
    if last and not isinstance(last, str):
        raise unexpected("topic")
    return {
        "views": found["views"],
        "replies": found["posts"] - 1,
        "likes": found["likes"],
        "last_post": last[:10] if last else "",
    }


def _collect(ctx: Context, base_url: str, topic: dict[str, Any], path: Path) -> None:
    topic_id, label = topic["id"], topic["label"]
    row = {"date": ctx.date.isoformat(), "topic_id": topic_id, "label": label}
    ctx.log.info("Forum: fetching topic %s (%s)", topic_id, label)
    try:
        answer = _topic(ctx, base_url, topic_id)
        row.update(_counted(answer))
    except HttpError as e:
        ctx.log.info("ERROR: Failed to fetch forum topic %s: HTTP %s", topic_id, e.http_code)
        reason = f"{base_url}: HTTP {e.http_code} for topic {topic_id} ({label})"
        ctx.record_unfixable(spec.NAME, path.name, e, reason)
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": "topic request failed"}
    except UnusableError as e:
        ctx.log.info("ERROR: Failed to parse forum data for topic %s: %s", topic_id, e)
        failure = {"status": "parse_failed", "reason": str(e)}
    else:
        store.write_good(path, row, ctx, spec.NAME, store.SECOND_KEY)
        _rebuild(ctx, base_url, topic, path, answer)
        return
    store.write_failure(path, {**row, **failure}, ctx.now, store.SECOND_KEY)


def _day(ctx: Context, when: Any) -> date:
    """A forum timestamp's date in the run's offset, as rows are dated; ValueError for one that isn't a time."""
    moment = datetime.fromisoformat(when.replace("Z", "+00:00")) if isinstance(when, str) else None
    if moment is None or moment.tzinfo is None:
        raise ValueError(f"not a time with its offset: {when!r}")
    return moment.astimezone(ctx.now.tzinfo).date()


def _rebuild(ctx: Context, base_url: str, topic: dict[str, Any], path: Path, answer: dict[str, Any]) -> None:
    """Write a row for each day from the topic's creation to the day before its first row (store.fill): its replies
    and last post by the end of that day, from each post's creation; null for its views and likes. The posts its answer
    leaves out are asked for only when a day lacks its row; nothing where they can't all be read."""
    topic_id = topic["id"]
    try:
        days = store.rebuild_days(path, _day(ctx, answer.get("created_at")), ctx, {store.SECOND_KEY: topic_id})
    except ValueError:
        ctx.log.info("Not rebuilding past replies of topic %s: it has no creation time", topic_id)
        return
    held = store.dated_rows(path, {store.SECOND_KEY: topic_id}).good
    if all(day.isoformat() in {row["date"] for row in held} for day in days):
        return
    try:
        posted = _posted(ctx, base_url, topic_id, answer)
    except (HttpError, UnusableError, ValueError) as e:
        ctx.log.info("Not rebuilding past replies of topic %s: %s", topic_id, getattr(e, "http_code", None) or e)
        return
    rows = []
    for day in days:
        made = sorted(when for when in posted if _day(ctx, when) <= day)
        if made:
            row = {"date": day.isoformat(), store.SECOND_KEY: topic_id, "label": topic["label"], "views": None}
            rows.append({**row, "replies": len(made) - 1, "likes": None, "last_post": made[-1][:10]})
    store.fill(path, rows, ctx, store.SECOND_KEY)


def _posted(ctx: Context, base_url: str, topic_id: int, answer: dict[str, Any]) -> list[str]:
    """Every post's creation time, the posts the answer leaves out asked for by id, POSTS_CHUNK at a time.

    Raises HttpError, UnusableError, or ValueError where the stream doesn't hold every post the topic counts or a
    post has no creation time.
    """
    stream = answer.get("post_stream")
    ids, posts = (stream.get("stream"), stream.get("posts")) if isinstance(stream, dict) else (None, None)
    if not (isinstance(ids, list) and isinstance(posts, list)) or len(ids) != answer.get("posts_count"):
        raise ValueError(f"its stream holds {len(ids) if isinstance(ids, list) else 0} of its posts")
    found = {post.get("id"): post.get("created_at") for post in posts if isinstance(post, dict)}
    missing = [i for i in ids if i not in found]
    for n in range(0, len(missing), POSTS_CHUNK):
        asked = "&".join(f"post_ids[]={i}" for i in missing[n : n + POSTS_CHUNK])
        more = read_json("posts", ctx.http.request(f"{base_url.rstrip('/')}/t/{topic_id}/posts.json?{asked}"))
        listed = (more.get("post_stream") or {}).get("posts") if isinstance(more, dict) else None
        if not isinstance(listed, list):
            raise unexpected("posts")
        found.update((post.get("id"), post.get("created_at")) for post in listed if isinstance(post, dict))
    posted = [found.get(i) for i in ids]
    for when in posted:
        _day(ctx, when)  # raises ValueError for a post without its creation time
    return [str(when) for when in posted]
