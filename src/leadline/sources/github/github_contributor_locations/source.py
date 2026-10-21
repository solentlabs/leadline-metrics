"""github_contributor_locations: the code that makes requests and writes rows; the declarations are in spec.py."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from leadline import config, store
from leadline.context import Context, Finding
from leadline.http import HttpError, Response, UnusableError, read_json, unexpected
from leadline.sources.github import api as github
from leadline.sources.github.github_contributor_locations import comments, spec, timezones

ENV: tuple[str, ...] = (github.CREDENTIAL,)
LAGGING = False
KIND = "issues"  # what a failure row's reason names
UNREAD = "profile_failed"  # the source of a person whose profile no run of the day could read


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def check(ctx: Context, cfg: Any) -> list[Finding]:
    """Whether each repo exists and the token can read its issues; the region table config.load has checked.

    It writes nothing. The repo is what other GitHub sources ask for, and the issues what collection asks for, so a
    run reuses those answers. Where the entry's table reads issue forms, it also names, by count and never by login,
    the answers no rule matches where no `models` rule matches the modem in the same form, each rule an answer
    matched that gives another region than a person's profile location in the day's row, and each `models` rule that
    gives another region than the answer in the same form.
    """
    findings = []
    for entry in cfg:
        repo = entry["repo"]
        found = [github.check_repo(ctx, repo)]
        if found[-1].level == "ok":
            path = f"/{KIND}?state=all&per_page=100&page=1"  # collection's first request
            found.append(
                github.check_access(ctx, repo, path, fetching=f"its {KIND}", ok=f"the token can read its {KIND}")
            )
        if found[-1].level == "ok" and {config.REGION_ANSWER, config.REGION_MODEL} & set(entry["regions"]):
            found += _form_findings(ctx, entry)
        findings += found
    return findings


def _form_findings(ctx: Context, entry: Any) -> list[Finding]:
    """The answers no rule of the entry's table matches whose form's modem none does either, the rules that disagree
    with a profile, and the modems that disagree with an answer, each counted."""
    repo, table = entry["repo"], entry["regions"]
    try:
        pages = github.numbered_pages(ctx, f"{github.API}/repos/{repo}/{KIND}?state=all&per_page=100")
        forms = _texts(pages, table)[2]
    except HttpError as e:
        return [github.failed_finding(repo, e, f"its {KIND}")]
    except UnusableError as e:
        return [Finding("warn", f"{repo}: {e}")]
    unmatched: Counter[str] = Counter()  # each counts people, not issues
    modelled: Counter[tuple[str, str, str]] = Counter()
    for given in forms.values():
        rules = [(form, config.answer_rule(table, form.answer) if form.answer else None) for form in given]
        unmatched.update(
            {form.answer for form, answered in rules if form.answer and answered is None and not _placed(table, form)}
        )
        modelled.update(set(filter(None, (_disagreeing(table, form, answered) for form, answered in rules))))
    findings = []
    if unmatched:
        counted = ", ".join(f"{answer} ({n})" for answer, n in _ordered(unmatched))
        findings.append(Finding("warn", f"{repo}: answers no region rule matches: {counted}"))
    for (keyword, region, profile), n in _ordered(_profile_disagreements(ctx, entry, forms)):
        message = f"{repo}: rule '{keyword}' gives {region} for an answer where a profile says {profile} ({n})"
        findings.append(Finding("warn", message))
    for (keyword, region, answer), n in _ordered(modelled):
        findings.append(
            Finding("warn", f"{repo}: model '{keyword}' gives {region} where an answer says {answer} ({n})")
        )
    return findings


def _placed(table: dict[str, Any], form: config.Form) -> bool:
    """Whether a `models` rule matches the form's modem, which then places the person its answer doesn't."""
    model = form.model
    return model is not None and config.model_rule(table, model) is not None


def _disagreeing(
    table: dict[str, Any], form: config.Form, answered: tuple[str, str] | None
) -> tuple[str, str, str] | None:
    """The `models` keyword, its region and the answer's region where the modem's rule disagrees with the answer's."""
    model = config.model_rule(table, form.model) if form.model and answered else None
    return (model[1], model[0], answered[0]) if model and answered and model[0] != answered[0] else None


def _profile_disagreements(
    ctx: Context, entry: Any, forms: dict[str, list[config.Form]]
) -> Counter[tuple[str, str, str]]:
    """Each rule a person's answer matched that gives another region than their profile location in the day's row,
    with the two regions, counted."""
    table = entry["regions"]
    held = store.stored(ctx.out_dir / spec.file_name(entry), {"date": ctx.date.isoformat()})
    people = held.get("contributors") if held is not None else None
    disagreeing: Counter[tuple[str, str, str]] = Counter()
    for person in people if isinstance(people, list) else []:
        login, location = (person.get("login"), person.get("location")) if isinstance(person, dict) else (None, None)
        profile = config.find_region(table, location, location=True) if isinstance(location, str) else None
        rule = _answered(table, forms.get(login, [])) if profile and isinstance(login, str) else None
        if rule is not None and rule[0] != profile:
            disagreeing[(rule[1], rule[0], str(profile))] += 1
    return disagreeing


def _ordered(counted: Counter[Any]) -> list[tuple[Any, int]]:
    """`counted`'s items, the largest count first, then by key."""
    return sorted(counted.items(), key=lambda item: (-item[1], item[0]))


def _collect(ctx: Context, entry: Any) -> None:
    repo, path = entry["repo"], ctx.out_dir / spec.file_name(entry)
    row: dict[str, Any] = {"date": ctx.date.isoformat(), "repo": repo}
    ctx.log.info("Collecting contributor locations for %s...", repo)
    try:
        pages = github.numbered_pages(ctx, f"{github.API}/repos/{repo}/{KIND}?state=all&per_page=100")
    except HttpError as e:
        ctx.log.info("ERROR: Failed to fetch issues for contributor locations of %s: HTTP %s", repo, e.http_code)
        ctx.record_unfixable(spec.NAME, path.name, e, f"{repo}: HTTP {e.http_code} from GitHub's {KIND} API")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": f"{KIND} request failed"}
        store.write_failure(path, {**row, **failure}, ctx.now)
        return
    ctx.log.info("Contributor locations: fetched %d page(s) of issues for %s", len(pages), repo)
    try:
        texts, firsts, forms, issues, attachments = _texts(pages, entry["regions"])
    except UnusableError as e:
        ctx.log.info("ERROR: Failed to collect contributor locations for %s: %s", repo, e)
        store.write_failure(path, {**row, "status": "parse_failed", "reason": str(e)}, ctx.now)
        return
    locations: dict[str, str | None] = {}
    for login in sorted(texts):
        try:
            locations[login] = github.profile_location(ctx, login)
        except HttpError as e:
            ctx.log.info("WARN: Failed to fetch the profile of %s: HTTP %s", login, e.http_code)
            ctx.record_unfixable(spec.NAME, path.name, e, f"{repo}: HTTP {e.http_code} from GitHub's users API")
        except UnusableError as e:
            ctx.log.info("WARN: Could not read the profile of %s: %s", login, e)
    row["total_unique_creators"] = len(texts)
    people = [_person(entry["regions"], login, texts[login], forms[login], locations) for login in sorted(texts)]
    _log_known(ctx, entry["regions"], people, forms, locations)
    if any(person["source"] == UNREAD for person in people):
        people = _merged(ctx, path, row, people)
    remembered: dict[str, Any] = {}
    if config.REGION_ATTACHMENT in entry["regions"]:
        answered = {login for login, given in forms.items() if _decided_by_answer(entry["regions"], given)}
        eligible = {p["login"] for p in people if p["source"] != UNREAD}
        checked = {
            p["login"]
            for p in people
            if p["source"] == spec.CONFIGURED or (p["login"] in answered and not p["location"])
        }
        people, state = timezones.place(ctx, path, entry, people, attachments, eligible, checked)
        remembered[timezones.STATE] = state
    if entry.get("comments"):
        people, state = comments.place(ctx, path, entry, people, issues)
        remembered[comments.STATE] = state
    unread = sum(1 for person in people if person["source"] == UNREAD)
    _recompute(ctx, path, row, people, firsts, unread)
    missing = [f"{unread} of {len(people)} profiles could not be read"] if unread else []
    store.write_merged(path, {**row, **_totals(people), **remembered}, missing, ctx, spec.NAME)


def _recompute(
    ctx: Context,
    path: Path,
    row: dict[str, Any],
    people: list[dict[str, Any]],
    firsts: dict[str, str],
    unread: int,
) -> None:
    """Write the row of each past day from the first person's first issue (store.recompute): the people who had opened
    one by the end of that day (in the run's offset), with this run's locations and regions. Only where every profile
    was read and every issue has its creation time; otherwise nothing, and the log says why."""
    repo = row["repo"]
    if unread:
        ctx.log.info("Not recomputing past contributors for %s: %d profile(s) unread", repo, unread)
        return
    try:
        first = {login: github.local_date(ctx, when) for login, when in firsts.items()}
    except ValueError:
        ctx.log.info("Not recomputing past contributors for %s: an issue has no creation time", repo)
        return
    rows = []
    for day in store.recompute_days(path, min(first.values(), default=None), ctx):
        then = [person for person in people if first[person["login"]] <= day]
        rows.append({**row, "date": day.isoformat(), "total_unique_creators": len(then), **_totals(then)})
    store.recompute(path, rows, ctx, spec.NAME)


def _decided_by_answer(table: dict[str, Any], forms: list[config.Form]) -> bool:
    """Whether the answer to the provider question, not the modem, gives the region of a person's forms."""
    clue = config.form_clue(table, forms)
    return clue is not None and clue[1] == config.CLUE_ANSWER


def _texts(
    pages: list[Response], table: dict[str, Any]
) -> tuple[
    dict[str, list[str]],
    dict[str, str],
    dict[str, list[config.Form]],
    dict[str, list[tuple[int, str]]],
    dict[str, list[tuple[int, str]]],
]:
    """Each person's issue texts, the creation time of their first issue, their issue forms, their issues' numbers
    with the time each was last updated, and the number and URL of each file the table's `attachment` finds in the
    body of their issues (not pull requests), by login, read whole: one page or issue it can't read makes them
    unusable.

    A text is the issue's title and the start of its body; `[bot]` logins and null users are left out. A creation
    time is the API's text, compared as text (GitHub gives every one in UTC, `Z`), and "" where an issue has none.
    A form is what the table's `answer` and `model_answer` capture in the body of an issue, not a pull request
    (config.read_form); a body with neither has none.
    """
    texts: dict[str, list[str]] = defaultdict(list)
    firsts: dict[str, str] = {}
    forms: dict[str, list[config.Form]] = defaultdict(list)
    numbers: dict[str, list[tuple[int, str]]] = defaultdict(list)
    attachments: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for page in pages:
        answer = read_json(KIND, page)
        if not isinstance(answer, list):  # `{}` too, which the script reads as no issues
            raise unexpected(KIND)
        for item in answer:
            if not isinstance(item, dict):
                raise unexpected(KIND)
            user, title, body = item.get("user") or {}, item.get("title", ""), item.get("body") or ""
            login = user.get("login", "") if isinstance(user, dict) else None
            if not (isinstance(login, str) and isinstance(title, str) and isinstance(body, str)):
                raise unexpected(KIND)
            if login and not login.endswith("[bot]"):
                texts[login].append(f"{title} {body[: spec.BODY_CHARS]}")
                created = item.get("created_at")
                created = created if isinstance(created, str) else ""
                firsts[login] = min(firsts.get(login, created), created)
                number, updated = item.get("number"), item.get("updated_at")
                if type(number) is int:
                    numbers[login].append((number, updated if isinstance(updated, str) else ""))
                form = None if "pull_request" in item else config.read_form(table, body)
                if form is not None and any(form):
                    forms[login].append(form)
                url = None if "pull_request" in item else config.find_attachment(table, body)
                if url is not None and type(number) is int:
                    attachments[login].append((number, url))
    return texts, firsts, forms, numbers, attachments


def _person(
    table: dict[str, Any], login: str, texts: list[str], forms: list[config.Form], locations: dict[str, str | None]
) -> dict[str, Any]:
    """A person's entry, as the script's: their region and where it came from; marked UNREAD with no profile read.

    A location a rule reads gives the region, before the table's known region for the login (`configured`); with
    neither, their issue forms give it (config.form_region), before their issue text.
    """
    if login not in locations:
        return {"login": login, "location": None, "region": None, "source": UNREAD}
    location = locations[login]
    region = config.find_region(table, location, location=True) if location else None
    known = config.known_region(table, login)
    if region:
        source = "github_profile"
    elif known:
        region, source = known, spec.CONFIGURED
    elif location:
        source = "profile_unparsed"
    else:
        region = config.form_region(table, forms) or config.find_region(table, " ".join(texts))
        source = "issue_inference" if region else spec.UNKNOWN
    return {"login": login, "location": location, "region": region or spec.UNKNOWN, "source": source}


def _log_known(
    ctx: Context,
    table: dict[str, Any],
    people: list[dict[str, Any]],
    forms: dict[str, list[config.Form]],
    locations: dict[str, str | None],
) -> None:
    """Log each region a profile, the answer to the provider question or a modem gives that differs from the known
    region of a person whose profile was read, by the two regions and the clue, counted, never by login."""
    disagreeing: Counter[tuple[str, str, str]] = Counter()
    for person in people:
        login = person["login"]
        known = config.known_region(table, login) if person["source"] != UNREAD else None
        if known is None:
            continue
        location = locations.get(login)
        profile = config.find_region(table, location, location=True) if location else None
        clue = config.form_clue(table, forms[login])
        found = [(profile, "a profile")] if profile else []
        found += [(clue[0], "an ISP answer" if clue[1] == config.CLUE_ANSWER else "a modem")] if clue else []
        disagreeing.update((known, region, name) for region, name in found if region != known)
    for (known, region, name), n in _ordered(disagreeing):
        ctx.log.info("WARN: a known region says %s where %s says %s (%d)", known, name, region, n)


def _answered(table: dict[str, Any], forms: list[config.Form]) -> tuple[str, str] | None:
    """The region and the exact answer or keyword of the first answer in `forms` a rule matches, or None."""
    return next(filter(None, (config.answer_rule(table, form.answer) for form in forms if form.answer)), None)


def _merged(ctx: Context, path: Path, row: dict[str, Any], people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`people`, each one this run couldn't read replaced by their readable entry in the day's stored row."""
    held = store.stored(path, row)
    earlier = held.get("contributors") if held is not None else None
    readable = {
        person["login"]: person
        for person in (earlier if isinstance(earlier, list) else [])
        if isinstance(person, dict) and isinstance(person.get("login"), str) and person.get("source") != UNREAD
    }
    kept = [person["login"] for person in people if person["source"] == UNREAD and person["login"] in readable]
    for login in kept:
        ctx.log.info(
            "Kept the stored profile of %s, which this run failed to get", f"{login} for {row['date']} in {path.name}"
        )
    return [readable[person["login"]] if person["login"] in kept else person for person in people]


def _totals(people: list[dict[str, Any]]) -> dict[str, Any]:
    """The profiles' part of the row, as the script's; the totals count only the people whose profile was read."""
    read = [person for person in people if person["source"] != UNREAD]
    counted: dict[str, int] = defaultdict(int)
    for person in read:
        counted[person["region"]] += 1
    return {
        "with_github_location": sum(1 for person in read if person["location"]),
        "with_inferred_region": sum(1 for person in read if person["region"] != spec.UNKNOWN),
        "region_summary": dict(sorted(counted.items(), key=lambda region: -region[1])),
        "contributors": people,
    }
