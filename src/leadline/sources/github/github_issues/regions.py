"""github_issues: the repo's stars and forks counted by region, from each person's profile location.

An entry with `stars_and_forks` and a `regions` table gives each row `repo.stars_by_region` and
`repo.forks_by_region`: the people who starred or forked by the end of the row's day, each counted once under the
region of their profile location (`unknown` where the table can't place them). Counts only: a login lives in memory
for the run and is never written, to a row or the log. What the run has counted is the rows' own: the people dated
up to the newest row that has the counts are already in them, so a run reads the profiles of the people since, and
once a person is counted no profile is read for them again.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from itertools import groupby
from pathlib import Path
from typing import Any, Callable, NamedTuple

from leadline import config, store
from leadline.context import Context
from leadline.http import HttpError, UnusableError
from leadline.sources.github import api as github
from leadline.sources.github.github_issues import spec

PROFILE_LIMIT = 100  # the most profiles a run reads, for stars and forks together; a day's people are read whole
NOTHING_KNOWN = date.min  # a `Counted.stop` before every day: no day's counts are known


class Listed(NamedTuple):
    """One star or fork of the repo: the day it happened (the run's offset) and who did it (None: no account)."""

    day: date
    login: str | None


class Counted(NamedTuple):
    """The regions of one field (`stars`) that this run could count, to answer `on` for any day.

    `anchor` is the newest past day whose row has the counts (None: none does), and `base` its counts: the days up to
    it are never filled. `steps` is each later day with people, with the counts through its end; `stop` is the first
    day whose people couldn't all be read (None: all were), and from it on nothing is known.
    """

    anchor: date | None
    base: dict[str, int]
    steps: list[tuple[date, dict[str, int]]]
    stop: date | None

    def on(self, day: date) -> dict[str, int] | None:
        """The counts through the end of `day`, the largest region first; None where this run can't tell them."""
        if (self.anchor is not None and day <= self.anchor) or (self.stop is not None and day >= self.stop):
            return None
        found = next((counts for when, counts in reversed(self.steps) if when <= day), self.base)
        return dict(sorted(found.items(), key=lambda region: (-region[1], region[0])))


def on(counted: dict[str, Counted], day: date) -> dict[str, dict[str, int]]:
    """The `repo` fields of `day`: each count's regions that `counted` can tell, keyed by the field's name."""
    found = {field: counts for field, c in counted.items() for counts in [c.on(day)] if counts is not None}
    return {spec.REGION_FIELDS[field]: counts for field, counts in found.items()}


def count(
    ctx: Context,
    entry: dict[str, Any],
    path: Path,
    stats: dict[str, int],
    listed: Callable[[str], list[Listed]],
    limit: int,
) -> dict[str, Counted]:
    """What this run counts for each of `spec.REGION_FIELDS` (`Counted`), reading `listed(field)` (the repo's stars or
    forks, oldest first; not for a repo with more than `limit`) and the profiles of the people not yet in the file's
    rows, at most PROFILE_LIMIT of them.

    A repo with any people has its list read each run, the profiles only of those dated after the newest counted
    row; a night with nobody new reads the list and no profile. A list or
    profile that can't be read leaves the days from there to the next night, said in the log, and costs the run's
    row nothing. A person removed since is still counted in the rows that have them.
    """
    reader = _Profiles(ctx, entry["regions"])
    found = {}
    for field in spec.REGION_FIELDS:
        anchor, base = _newest(ctx, path, spec.REGION_FIELDS[field])
        counted = Counted(anchor, base, [], None)
        if stats[field]:  # the list says who is new; a repo with none has no one to read
            counted = _read(ctx, path, field, stats[field], counted, listed, limit, reader)
        found[field] = counted
    reader.say()
    return found


def _newest(ctx: Context, path: Path, key: str) -> tuple[date | None, dict[str, int]]:
    """The newest past day whose good row has `repo.<key>` counts, and the counts; none, and none counted."""
    best: tuple[date | None, dict[str, int]] = (None, {})
    for row in store.rows(path):
        day, found = store.parse_date(row.get("date")), store.value(row, f"repo.{key}")
        if day is None or day >= ctx.date or not store.is_good(row) or not _counts(found):
            continue
        if best[0] is None or day >= best[0]:
            best = (day, dict(found))
    return best


def _counts(found: Any) -> bool:
    """Whether `found` is an object of counts by region, as a row of this source holds."""
    return isinstance(found, dict) and all(isinstance(k, str) and config.is_count(n) for k, n in found.items())


def _read(
    ctx: Context,
    path: Path,
    field: str,
    total: int,
    counted: Counted,
    listed: Callable[[str], list[Listed]],
    limit: int,
    reader: _Profiles,
) -> Counted:
    """`counted` with the people of `field` dated after its anchor read, whole days from the oldest, while the
    profile limit allows; unchanged where the list or a profile can't be read, or the repo has too many to list."""
    name = path.name
    try:
        if total > limit:
            raise UnusableError(f"{total} {field} are more than the {limit} it reads")
        people = [person for person in listed(field) if counted.anchor is None or person.day > counted.anchor]
    except HttpError as e:
        ctx.log.info("Left the %s by region of %s unfilled: HTTP %s", field, name, e.http_code)
        return counted._replace(stop=NOTHING_KNOWN)
    except UnusableError as e:
        ctx.log.info("Left the %s by region of %s unfilled: %s", field, name, e)
        return counted._replace(stop=NOTHING_KNOWN)
    totals: Counter[str] = Counter(counted.base)
    steps: list[tuple[date, dict[str, int]]] = []
    for day, group in groupby(people, key=lambda person: person.day):
        today = list(group)
        if not reader.room(person.login for person in today):
            ctx.log.info(
                "Read %d profile(s) for the %s by region of %s; the rest waits for the next run",
                reader.reads,
                field,
                name,
            )
            return counted._replace(steps=steps, stop=day)
        try:
            totals.update(reader.region(person.login) for person in today)
        except (HttpError, UnusableError) as e:
            code = f"HTTP {e.http_code}" if isinstance(e, HttpError) else str(e)
            ctx.log.info("Stopped the %s by region of %s at %s: %s", field, name, day, code)
            return counted._replace(steps=steps, stop=day)
        steps.append((day, dict(totals)))
    return counted._replace(steps=steps)


class _Profiles:
    """The profiles a run reads: each login's region once, read as `github.profile_location` gives it and placed by
    the table, and how many were requested. Logins are kept only here, for the run."""

    def __init__(self, ctx: Context, table: dict[str, Any]) -> None:
        self.ctx, self.table = ctx, table
        self.found: dict[str, str] = {}
        self.reads = 0
        self.disagreeing: Counter[tuple[str, str]] = Counter()

    def room(self, logins: Any) -> bool:
        """Whether the profiles of `logins` fit in the limit with those read: always for the first day read, so a
        run goes on though one day has more people than the limit."""
        needed = {login for login in logins if login and login not in self.found}
        return self.reads == 0 or self.reads + len(needed) <= PROFILE_LIMIT

    def region(self, login: str | None) -> str:
        """The region of `login`'s profile location, else the table's known region for them, else UNKNOWN_REGION (a
        person with no account too). Raises what `github.profile_location` raises."""
        if not login:
            return spec.UNKNOWN_REGION
        if login not in self.found:
            self.reads += 1
            location = github.profile_location(self.ctx, login)
            profile = config.find_region(self.table, location, location=True) if location else None
            known = config.known_region(self.table, login)
            if profile and known and profile != known:
                self.disagreeing[(known, profile)] += 1
            self.found[login] = profile or known or spec.UNKNOWN_REGION
        return self.found[login]

    def say(self) -> None:
        """Log each known region a profile contradicts, by the two regions and how many, never by login."""
        for (known, profile), n in sorted(self.disagreeing.items(), key=lambda item: (-item[1], item[0])):
            self.ctx.log.info("WARN: a known region says %s where a profile says %s (%d)", known, profile, n)
