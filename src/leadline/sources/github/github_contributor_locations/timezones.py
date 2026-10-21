"""github_contributor_locations: a region from the zone in a file a person attached to an issue.

Where an entry's region table sets `attachment`, `attachment_value` and `zones` (config.py), the first file the
`attachment` regex finds in the body of a person's newest issue (not a pull request) is fetched, the first group
of `attachment_value` is read in its text, and the first `zones` rule with a keyword in that value gives a region
(`timezone_inference`). A zone outranks a modem and issue text, not the answer to the provider question, and not
a profile: where a zone's region differs from a profile's, from the answer's, or from the modem's or text's it
replaces, the disagreement is logged by rule and regions, never by login. A person a profile or an answer placed is
read too, only to check it.
Only the region is kept: the value is matched, never stored, and nothing else in the file is read.

Requests are bounded by what the row remembers (`timezones_read`): the settings' fingerprint and, for each issue
whose file was read, the URL then. With no such row, or one read under other settings, every person's file is read
(the pass); after that, at most one file a run: an unread one first, then one whose link changed, the lowest issue
number first. A file the service no longer has (404 or 410) counts as read; any other failure is asked for again.
The file is public, on another host than the API, so no token is sent to it.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from leadline import config
from leadline.context import Context
from leadline.http import HttpError
from leadline.sources.github.github_contributor_locations import comments, spec

SOURCE = "timezone_inference"  # a person's source where a zone gave their region
STATE = "timezones_read"  # the row's field: {"rules": fingerprint, "issues": {number: URL of the file read}}
PROFILE = "github_profile"  # a person's source where their profile gave their region
GONE = (404, 410)  # statuses for a file the service no longer has: nothing to read again


def fingerprint(table: dict[str, Any]) -> str:
    """A short name for the table's attachment settings and zones, which change when they do and not with its rest."""
    return config.fingerprint(
        [table.get(key) for key in (config.REGION_ATTACHMENT, config.REGION_VALUE, config.REGION_ZONES)]
    )


def place(
    ctx: Context,
    path: Path,
    entry: Any,
    people: list[dict[str, Any]],
    attachments: dict[str, list[tuple[int, str]]],
    eligible: set[str],
    checked: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`people` with the region the zone in each eligible person's newest file gives, and the state for the row.

    The zone only checks a person in `checked` (placed by the answer to the provider question) or placed by a profile.
    """
    table, key = entry["regions"], fingerprint(entry["regions"])
    held = comments.held_row(path, STATE)
    nightly = held is not None and held[0] == key
    read = dict(held[1]) if held is not None and nightly else {}
    kept = {p["login"]: p for p in held[2] if p.get("source") == SOURCE} if held is not None and nightly else {}
    people = [kept[p["login"]] if p["login"] in kept and _open(p, eligible, checked) else p for p in people]
    newest = {login: max(attachments[login]) for login in eligible if attachments.get(login)}
    wanted = sorted((number, login, url) for login, (number, url) in newest.items() if str(number) not in read)
    wanted += sorted(
        (number, login, url)
        for login, (number, url) in newest.items()
        if str(number) in read and read[str(number)] != url
    )
    disagreeing: Counter[tuple[str, str, str, str]] = Counter()
    for number, login, url in wanted[:1] if nightly else wanted:
        try:
            text = ctx.http.request(url).body.decode("utf-8", "replace")  # no token: another host, a public link
        except HttpError as e:
            if e.code not in GONE:
                ctx.log.info("WARN: Failed to fetch the file of issue %d: HTTP %s", number, e.http_code)
                if nightly:
                    break
                continue
            ctx.log.info("WARN: The file of issue %d is gone: HTTP %s", number, e.http_code)
            text = ""
        read[str(number)] = url
        value = config.find_value(table, text)
        rule = config.zone_rule(table, value) if value else None
        if rule is not None:
            people = [
                _with_zone(p, rule, disagreeing, p["login"] in checked) if p["login"] == login else p for p in people
            ]
    for (keyword, region, found, clue), n in sorted(disagreeing.items(), key=lambda item: (-item[1], item[0])):
        ctx.log.info("WARN: zone rule '%s' gives %s where %s says %s (%d)", keyword, region, clue, found, n)
    return people, {"rules": key, "issues": dict(sorted(read.items(), key=lambda item: int(item[0])))}


def _open(person: dict[str, Any], eligible: set[str], checked: set[str]) -> bool:
    """Whether a zone may still place the person: eligible, with neither a profile nor an answer placing them."""
    return person["login"] in eligible and person["login"] not in checked and person["source"] != PROFILE


def _with_zone(
    person: dict[str, Any], rule: tuple[str, str], disagreeing: Counter[tuple[str, str, str, str]], answered: bool
) -> dict[str, Any]:
    """`person` placed by the zone's `rule` (its region and keyword) unless a profile or the answer to the provider
    question (`answered`) placed them, or the zone's region is theirs already; a differing region another clue gave
    is counted in `disagreeing`."""
    region, keyword = rule
    if person["region"] == region:
        return person
    if person["region"] != spec.UNKNOWN:
        clue = {PROFILE: "a profile", spec.CONFIGURED: "a known region"}.get(person["source"], "")
        clue = clue or ("an ISP answer" if answered else "an issue form or text")
        disagreeing[(keyword, region, person["region"], clue)] += 1
    return (
        person
        if person["source"] in (PROFILE, spec.CONFIGURED) or answered
        else {**person, "region": region, "source": SOURCE}
    )
