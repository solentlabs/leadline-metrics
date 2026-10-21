"""github_contributor_locations: the script's rows on its scenarios, and leadline's side of each decided divergence.

The rows are compared with the goldens through tests/port.py; the scenarios where leadline differs by
a decision are listed in scenarios.py (DIVERGENCES) and asserted here
instead.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import port
import pytest
import scenarios
from scenarios.builders.common import exchange, failed

from leadline.context import Critical, Finding
from leadline.http import SavedResponses
from leadline.sources.github import github_contributor_locations as source
from leadline.sources.github.github_contributor_locations import comments, timezones
from leadline.sources.github.github_contributor_locations.tests import scenarios as gcl
from leadline.sources.github.tests.builders import GITHUB_API, github_issue_pages

NAME = "github_contributor_locations"
CONFIG = gcl.LEADLINE_CONFIG
FILE = gcl.FILE
REPO = gcl.REPO
EARLIER = gcl.EARLIER
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it
ISSUES = f"{GITHUB_API}/repos/{REPO}/issues?state=all&per_page=100&page=1"
OSCAR = {"login": "oscar-user", "location": "Zedland, Germany", "region": "DE", "source": "github_profile"}
PAPA = {"login": "papa-user", "location": None, "region": "US", "source": "issue_inference"}
# A person whose profile no run of the day could read.
PAPA_UNREAD = {"login": "papa-user", "location": None, "region": None, "source": "profile_failed"}
OSCAR_UNREAD = {**PAPA_UNREAD, "login": "oscar-user"}


def row(**fields: Any) -> str:
    """A row for the run's date as leadline writes it: the date and repo, then `fields`, then captured_at."""
    return json.dumps({"date": "2026-09-23", "repo": REPO, **fields, "captured_at": NOW}) + "\n"


def located(*contributors: dict[str, Any], summary: dict[str, int]) -> dict[str, Any]:
    """The profiles' part of a row, for `contributors`: the totals count only the profiles read."""
    read = [c for c in contributors if c["source"] != "profile_failed"]
    return {
        "with_github_location": sum(1 for c in read if c["location"]),
        "with_inferred_region": sum(1 for c in read if c["region"] != "unknown"),
        "region_summary": summary,
        "contributors": list(contributors),
    }


def partial_row(*contributors: dict[str, Any], summary: dict[str, int], reason: str) -> str:
    """A partial row: every person's entry, those no run could read marked, and the reason counting them."""
    part = located(*contributors, summary=summary)
    return row(total_unique_creators=len(contributors), **part, status="partial", reason=reason)


ONE_UNREAD: dict[str, Any] = {"summary": {"DE": 1}, "reason": "1 of 2 profiles could not be read"}


def run(tmp_path: Path, *responses: Any, seed: str = EARLIER, cfg: Any = CONFIG) -> port.PortRun:
    scenario = replace(
        port.scenario(NAME, "regions_from_profile_locations"), responses=list(responses), seed={FILE: seed}
    )
    return port.run(source, cfg, scenario, tmp_path)


def issues(*items: Any) -> list[Any]:
    return github_issue_pages(REPO, "all", list(items))


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert source.NAME == NAME
    assert source.ENV == ("GITHUB_TOKEN",)
    assert source.LAGGING is False


def test_a_valid_config_has_no_problems() -> None:
    cfg = [{"repo": "a/b", "regions": {}}, {"repo": "a/c", "regions": gcl.REGIONS, "file": "x-{repo_name}.jsonl"}]

    assert source.validate(cfg) == []


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"repo": "a/b", "regions": {}}, [": must be a list of entries"]),
        (["a/b"], ["[0]: must be an object"]),
        ([{"regions": {}}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a", "regions": {}}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a/b"}], ["[0].regions: must name a region table in shared.regions"]),
        (
            [{"repo": "a/b", "regions": {}, "file": "x-{label}.jsonl"}],
            ["[0].file: 'x-{label}.jsonl' uses {label}, which is not one of: repo, repo_name"],
        ),
    ],
    ids=["not a list", "an entry not an object", "no repo", "a repo without its owner", "no regions", "a bad file"],
)  # fmt: skip
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert source.validate(cfg) == problems


def test_files_are_the_default_or_the_template_of_each_entry() -> None:
    cfg = [{"repo": "a/one", "regions": {}}, {"repo": "a/two", "regions": {}, "file": "where-{repo_name}.jsonl"}]

    assert source.files(cfg) == {"[0]": "github-contributor-locations-one.jsonl", "[1]": "where-two.jsonl"}


def test_it_is_skipped_without_a_token(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "skips_without_a_token"), tmp_path)

    assert "SKIP: github_contributor_locations (GITHUB_TOKEN not set)" in done.log
    assert (done.status, done.rows) == (0, {})


def test_it_sends_the_token_in_a_header_never_the_url(tmp_path: Path) -> None:
    """ADR: credentials come only from the environment; the script passes the token on a command line."""
    sent: list[Any] = []
    transport = SavedResponses(port.scenario(NAME, "regions_from_issues_without_a_location").responses)

    def recording(request: Any, timeout: float) -> Any:
        sent.append(request)
        return transport(request, timeout)

    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    ctx.http._transport = recording  # to see the request headers
    with port.credentials({"GITHUB_TOKEN": "t0ken"}, source.ENV):
        source.collect(ctx, CONFIG)

    assert len(sent) == 8  # the issues page, then one profile per person
    assert {r.get_header("Authorization") for r in sent} == {"Bearer t0ken"}
    assert {r.get_header("Accept") for r in sent} == {"application/vnd.github+json"}
    assert not any("t0ken" in r.full_url for r in sent)


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    done = port.run(source, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


def test_an_answer_is_never_logged(tmp_path: Path) -> None:
    """Only the region an answer gives is kept, never the answer itself."""
    done = port.run(source, CONFIG, port.scenario(NAME, ANSWERS), tmp_path)

    assert not [line for line in done.log if "Example Fibre" in line or "Telia" in line]


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == [
        "a_failed_issues_page_writes_nothing",
        "a_failed_profile_falls_back_to_issue_text",
    ]


def test_a_missing_profile_is_no_location_as_the_script_reads_it(tmp_path: Path) -> None:
    """A 404 is GitHub's answer that there is no profile, so no location: not a failure, never critical."""
    gone = exchange(gcl.profile_url("papa-user"), '{"message":"Not Found"}', status=404)

    done = run(tmp_path, *issues(*gcl.FAILED_PROFILE), gcl.profile("oscar-user", "Zedland, Germany"), gone, seed="")

    assert done.rows == {FILE: row(total_unique_creators=2, **located(OSCAR, PAPA, summary={"DE": 1, "US": 1}))}
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_plain_bot_login_counts_as_a_person_as_in_the_script(tmp_path: Path) -> None:
    """The script skips only `[bot]` logins, where github_issues also counts dependabot and github-actions."""
    done = run(tmp_path, *issues(gcl.opened(1, "dependabot")), gcl.profile("dependabot"), seed="")

    who = {"login": "dependabot", "location": None, "region": "unknown", "source": "unknown"}
    assert done.rows == {FILE: row(total_unique_creators=1, **located(who, summary={"unknown": 1}))}


# --- decided divergences: the issues are required (ADR: a failure is recorded as a failure) ---


def failure_row(status: str, reason: str, http_code: str | None = None) -> str:
    code = {"http_code": http_code} if http_code is not None else {}
    return row(status=status, **code, reason=reason)


def test_a_failed_issues_page_is_a_fetch_failed_row(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "a_failed_issues_page_writes_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "issues request failed", "000")}
    assert (done.status, done.ctx.critical) == (0, [])  # recoverable: the next run writes the day


@pytest.mark.parametrize("code", [401, 403, 404])
def test_an_issues_error_waiting_cant_fix_is_critical_at_once(tmp_path: Path, code: int) -> None:
    done = run(tmp_path, exchange(ISSUES, "", status=code))

    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", "issues request failed", str(code))}
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP {code} from GitHub's issues API")]
    assert done.status == 1


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("not json", "issues response was not JSON"),
        ({}, "issues response had an unexpected shape"),  # the script reads `{}` as no issues
        ([5], "issues response had an unexpected shape"),
        ([{"user": "someone"}], "issues response had an unexpected shape"),
        ([{"user": {"login": 5}}], "issues response had an unexpected shape"),
        ([{"user": {"login": "a"}, "title": None}], "issues response had an unexpected shape"),
        ([{"user": {"login": "a"}, "title": "t", "body": 5}], "issues response had an unexpected shape"),
    ],
    ids=["not JSON", "an object", "an issue not an object", "a user not an object", "a login not a string",
         "a null title", "a body not a string"],
)  # fmt: skip
def test_issues_it_cant_use_are_a_parse_failed_row(tmp_path: Path, body: Any, reason: str) -> None:
    done = run(tmp_path, exchange(ISSUES, body))

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", reason)}
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_failure_keeps_a_good_row(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data."""
    good = row(total_unique_creators=1)

    done = run(tmp_path, exchange(ISSUES, "", status=502), seed=EARLIER + good)

    assert done.rows == {FILE: EARLIER + good}
    assert f"WARN: kept the good row for 2026-09-23 in {FILE}; fetch_failed not recorded" in done.log


def test_a_failing_entry_does_not_stop_the_next(tmp_path: Path) -> None:
    other = f"{GITHUB_API}/repos/a/other/issues?state=all&per_page=100&page=1"
    done = run(
        tmp_path,
        exchange(ISSUES, "", status=502),
        exchange(other, []),
        cfg=[*CONFIG, {"repo": "a/other", "regions": gcl.REGIONS}],
    )

    assert done.rows[FILE] == EARLIER + failure_row("fetch_failed", "issues request failed", "502")
    assert '"repo": "a/other", "total_unique_creators": 0' in done.rows["github-contributor-locations-other.jsonl"]


# --- decided divergences: a failed profile is that person's alone (ADR: a partly failed row ...) ---


def test_a_failed_profile_is_marked_unread_in_a_partial_row_never_a_guess(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, "a_failed_profile_falls_back_to_issue_text"), tmp_path)

    assert done.rows == {FILE: EARLIER + partial_row(OSCAR, PAPA_UNREAD, **ONE_UNREAD)}
    assert "WARN: Failed to fetch the profile of papa-user: HTTP 000" in done.log
    assert (done.status, done.ctx.critical) == (0, [])  # recoverable


@pytest.mark.parametrize(
    "answer",
    [
        exchange(gcl.profile_url("papa-user"), "not json"),
        exchange(gcl.profile_url("papa-user"), []),
        exchange(gcl.profile_url("papa-user"), {"login": "papa-user", "location": 5}),
        exchange(gcl.profile_url("papa-user"), "", status=500),
        exchange(gcl.profile_url("papa-user"), "", status=403, headers={"X-RateLimit-Remaining": "0"}),
    ],
    ids=["not JSON", "a list", "a location not a string", "a server error", "rate limited"],
)
def test_a_profile_it_cant_read_is_that_persons_alone(tmp_path: Path, answer: Any) -> None:
    done = run(tmp_path, *issues(*gcl.FAILED_PROFILE), gcl.profile("oscar-user", "Zedland, Germany"), answer)

    assert done.rows == {FILE: EARLIER + partial_row(OSCAR, PAPA_UNREAD, **ONE_UNREAD)}
    assert (done.status, done.ctx.critical) == (0, [])


def test_every_profile_is_still_asked_for_and_counted(tmp_path: Path) -> None:
    done = run(
        tmp_path,
        *issues(*gcl.FAILED_PROFILE),
        failed(gcl.profile_url("oscar-user")),
        failed(gcl.profile_url("papa-user")),
    )

    unread = partial_row(OSCAR_UNREAD, PAPA_UNREAD, summary={}, reason="2 of 2 profiles could not be read")
    assert done.rows == {FILE: EARLIER + unread}


@pytest.mark.parametrize("code", [401, 403])
def test_a_rejected_profile_request_is_critical_at_once(tmp_path: Path, code: int) -> None:
    rejected = exchange(gcl.profile_url("papa-user"), "", status=code)

    done = run(tmp_path, *issues(*gcl.FAILED_PROFILE), gcl.profile("oscar-user", "Zedland, Germany"), rejected)

    assert done.rows == {FILE: EARLIER + partial_row(OSCAR, PAPA_UNREAD, **ONE_UNREAD)}
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP {code} from GitHub's users API")]
    assert done.status == 1


# --- several runs on one day (a person's stored entry is kept where this run failed to read them) ---

PAPA_NL = {**PAPA, "region": "NL"}
KEPT = f"Kept the stored profile of papa-user for 2026-09-23 in {FILE}, which this run failed to get"


def failed_profile(tmp_path: Path, seed: str) -> port.PortRun:
    scenario = replace(port.scenario(NAME, "a_failed_profile_falls_back_to_issue_text"), seed={FILE: seed})
    return port.run(source, CONFIG, scenario, tmp_path)


def good(*contributors: dict[str, Any], summary: dict[str, int]) -> str:
    return row(total_unique_creators=len(contributors), **located(*contributors, summary=summary))


def test_a_failed_profile_keeps_the_persons_entry_from_a_good_row_and_the_row_stays_good(tmp_path: Path) -> None:
    stored = {**OSCAR, "location": "Old place, Finland", "region": "FI"}

    done = failed_profile(tmp_path, good(stored, PAPA_NL, summary={"FI": 1, "NL": 1}))

    # This run's reading of oscar-user; the stored entry of papa-user; the totals from the merged entries.
    assert done.rows == {FILE: good(OSCAR, PAPA_NL, summary={"DE": 1, "NL": 1})}
    assert KEPT in done.log
    assert done.ctx.corrections == []  # the run's own date


def test_a_partial_rows_readable_entry_fills_this_runs_failure_and_the_row_becomes_good(tmp_path: Path) -> None:
    earlier = partial_row(OSCAR_UNREAD, PAPA_NL, summary={"NL": 1}, reason="1 of 2 profiles could not be read")

    done = failed_profile(tmp_path, earlier)

    assert done.rows == {FILE: good(OSCAR, PAPA_NL, summary={"DE": 1, "NL": 1})}


def test_a_person_no_run_read_stays_unread(tmp_path: Path) -> None:
    earlier = partial_row(OSCAR, PAPA_UNREAD, **ONE_UNREAD)

    done = failed_profile(tmp_path, EARLIER + earlier)

    assert done.rows == {FILE: EARLIER + partial_row(OSCAR, PAPA_UNREAD, **ONE_UNREAD)}
    assert KEPT not in done.log


def test_a_person_the_stored_row_lacks_stays_unread(tmp_path: Path) -> None:
    done = failed_profile(tmp_path, EARLIER + good(OSCAR, summary={"DE": 1}))

    assert done.rows == {FILE: EARLIER + partial_row(OSCAR, PAPA_UNREAD, **ONE_UNREAD)}


def test_a_good_row_replaces_a_partial_row(tmp_path: Path) -> None:
    partial = partial_row(OSCAR, PAPA_UNREAD, **ONE_UNREAD)
    scenario = replace(port.scenario(NAME, "regions_from_profile_locations"), seed={FILE: partial})

    port.run(source, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, "regions_from_profile_locations", tmp_path) == []


def test_a_failure_keeps_a_partial_row(tmp_path: Path) -> None:
    partial = partial_row(OSCAR, PAPA_UNREAD, **ONE_UNREAD)

    done = run(tmp_path, exchange(ISSUES, "", status=502), seed=EARLIER + partial)

    assert done.rows == {FILE: EARLIER + partial}


# --- regions from comments ---

COMMENTING = [{"repo": REPO, "regions": gcl.REGIONS, "comments": True}]
RULES = comments.fingerprint(gcl.REGIONS)
STAMP = "2026-09-01T10:00:00Z"  # the updated_at of an issue gcl.opened makes


def comments_url(number: int) -> str:
    return f"{GITHUB_API}/repos/{REPO}/issues/{number}/comments?per_page=100&page=1"


def said(number: int, *comments: tuple[str, str]) -> Any:
    """The comments of issue `number`, each (login, text), as the API answers."""
    return exchange(comments_url(number), [{"user": {"login": who}, "body": text} for who, text in comments])


def commenting(tmp_path: Path, items: list[Any], *rest: Any, seed: str = "", cfg: Any = COMMENTING) -> port.PortRun:
    """A run of `cfg` on `items` (every person has an empty profile), then `rest`: the comments it asks for."""
    people = sorted({i["user"]["login"] for i in items if i["user"]})
    return run(tmp_path, *issues(*items), *[gcl.profile(who) for who in people], *rest, seed=seed, cfg=cfg)


def entry(login: str, region: str = "unknown", source: str | None = None) -> dict[str, Any]:
    return {"login": login, "location": None, "region": region, "source": source or "unknown"}


def people(done: port.PortRun) -> dict[str, tuple[str, str]]:
    last = json.loads(done.rows[FILE].splitlines()[-1])
    return {c["login"]: (c["region"], c["source"]) for c in last["contributors"]}


def state(done: port.PortRun) -> dict[str, Any]:
    read: dict[str, Any] = json.loads(done.rows[FILE].splitlines()[-1])["comments_read"]
    return read


def stored(read: dict[str, str], *contributors: dict[str, Any], rules: str = RULES) -> str:
    """The earlier night's row: its people and which issues' comments it read."""
    part = located(*contributors, summary={})
    row_ = {"date": "2026-09-22", "repo": REPO, "total_unique_creators": len(contributors), **part}
    return json.dumps({**row_, "comments_read": {"rules": rules, "issues": read}}) + "\n"


def test_comments_is_a_boolean_entry_setting() -> None:
    assert source.validate([{"repo": "a/b", "regions": {}, "comments": True}]) == []
    assert source.validate([{"repo": "a/b", "regions": {}, "comments": "yes"}]) == [
        "[0].comments: must be true or false"
    ]


def test_the_fingerprint_follows_the_text_rules_only() -> None:
    more = {**gcl.REGIONS, "text": [*gcl.REGIONS["text"], {"region": "FR", "keywords": ["orange"]}]}
    places = {**gcl.REGIONS, "location": []}

    assert comments.fingerprint(more) != RULES
    assert comments.fingerprint(places) == RULES


def test_the_first_night_reads_the_comments_of_every_unplaced_persons_issues_and_places_them(tmp_path: Path) -> None:
    items = [
        gcl.opened(1, "alpha-user"),
        gcl.opened(2, "alpha-user"),
        gcl.opened(3, "bravo-user"),
        gcl.opened(4, "kilo-user"),
    ]
    done = commenting(
        tmp_path,
        items,
        said(1, ("alpha-user", "Which provider?"), ("example-maintainer", "Hi")),
        said(2, ("alpha-user", "I'm on Comcast here")),
        said(3, ("bravo-user", "Nothing about it")),
        said(4, ("kilo-user", "Cable")),
    )

    assert people(done) == {
        "alpha-user": ("US", "comment_inference"),
        "bravo-user": ("unknown", "unknown"),
        "kilo-user": ("unknown", "unknown"),
    }
    assert state(done) == {"rules": RULES, "issues": {"1": STAMP, "2": STAMP, "3": STAMP, "4": STAMP}}
    assert "Unplaced contributors in solentlabs/example: bravo-user (#3), kilo-user (#4)" in done.log


def test_only_a_comments_author_is_placed_by_it_and_an_unplaced_commenter_is_placed_on_another_issue(
    tmp_path: Path,
) -> None:
    items = [gcl.opened(1, "alpha-user"), gcl.opened(2, "bravo-user")]

    done = commenting(
        tmp_path,
        items,
        said(1, ("example-maintainer", "Are you on Virgin Media?"), ("bravo-user", "I have Ziggo myself")),
        said(2, ("bravo-user", "just a modem")),
    )

    assert people(done) == {"alpha-user": ("unknown", "unknown"), "bravo-user": ("NL", "comment_inference")}


def test_only_the_start_of_each_comment_is_read(tmp_path: Path) -> None:
    far, near = "x" * 500 + " Comcast", "x" * 490 + " Comcast"
    items = [gcl.opened(1, "alpha-user"), gcl.opened(2, "bravo-user")]

    done = commenting(tmp_path, items, said(1, ("alpha-user", far)), said(2, ("bravo-user", near)))

    assert people(done) == {"alpha-user": ("unknown", "unknown"), "bravo-user": ("US", "comment_inference")}


def test_a_bot_comment_is_never_read(tmp_path: Path) -> None:
    done = commenting(tmp_path, [gcl.opened(1, "alpha-user")], said(1, ("github-actions[bot]", "Comcast")))

    assert people(done) == {"alpha-user": ("unknown", "unknown")}


def test_nobody_placed_by_text_or_a_profile_has_their_comments_asked_for(tmp_path: Path) -> None:
    items = [gcl.opened(1, "alpha-user", body="On Ziggo"), gcl.opened(2, "bravo-user")]
    done = run(
        tmp_path,
        *issues(*items),
        gcl.profile("alpha-user"),
        gcl.profile("bravo-user", "Zedland, Germany"),
        cfg=COMMENTING,
    )

    assert people(done) == {"alpha-user": ("NL", "issue_inference"), "bravo-user": ("DE", "github_profile")}
    assert state(done) == {"rules": RULES, "issues": {}}


def test_a_later_night_reads_one_unread_issue_the_lowest_number_first(tmp_path: Path) -> None:
    items = [
        gcl.opened(n, who) for n, who in [(1, "alpha-user"), (2, "alpha-user"), (3, "bravo-user"), (4, "kilo-user")]
    ]
    seed = stored({"1": STAMP, "2": STAMP}, entry("alpha-user"), entry("bravo-user"), entry("kilo-user"))

    done = commenting(tmp_path, items, said(3, ("bravo-user", "Rogers")), seed=seed)

    assert people(done)["bravo-user"] == ("CA", "comment_inference")
    assert state(done)["issues"] == {"1": STAMP, "2": STAMP, "3": STAMP}
    assert "Unplaced contributors in solentlabs/example: alpha-user (#1, #2), kilo-user (#4)" in done.log


def test_an_issue_changed_since_it_was_read_is_read_again_after_the_unread_ones(tmp_path: Path) -> None:
    items = [gcl.opened(1, "alpha-user"), gcl.opened(2, "bravo-user")]
    seed = stored({"1": "2026-08-01T00:00:00Z", "2": STAMP}, entry("alpha-user"), entry("bravo-user"))

    done = commenting(tmp_path, items, said(1, ("alpha-user", "Telstra now")), seed=seed)

    assert people(done)["alpha-user"] == ("AU", "comment_inference")
    assert state(done)["issues"] == {"1": STAMP, "2": STAMP}


def test_with_every_issue_read_unchanged_no_comments_are_asked_for_and_a_placement_stays(tmp_path: Path) -> None:
    items = [gcl.opened(1, "alpha-user"), gcl.opened(2, "bravo-user")]
    seed = stored({"1": STAMP, "2": STAMP}, entry("alpha-user", "US", "comment_inference"), entry("bravo-user"))

    done = commenting(tmp_path, items, seed=seed)

    assert people(done) == {"alpha-user": ("US", "comment_inference"), "bravo-user": ("unknown", "unknown")}
    assert state(done)["issues"] == {"1": STAMP, "2": STAMP}


def test_a_person_a_profile_now_places_loses_their_comment_placement(tmp_path: Path) -> None:
    items = [gcl.opened(1, "alpha-user")]
    seed = stored({"1": STAMP}, entry("alpha-user", "US", "comment_inference"))

    done = run(tmp_path, *issues(*items), gcl.profile("alpha-user", "Zedland, Germany"), seed=seed, cfg=COMMENTING)

    assert people(done) == {"alpha-user": ("DE", "github_profile")}


def test_changed_text_rules_start_the_pass_again_and_drop_what_the_old_rules_placed(tmp_path: Path) -> None:
    items = [gcl.opened(1, "alpha-user"), gcl.opened(2, "bravo-user")]
    seed = stored(
        {"1": STAMP, "2": STAMP}, entry("alpha-user", "FR", "comment_inference"), entry("bravo-user"), rules="old"
    )

    done = commenting(tmp_path, items, said(1, ("alpha-user", "Comcast")), said(2, ("bravo-user", "Ziggo")), seed=seed)

    assert people(done) == {"alpha-user": ("US", "comment_inference"), "bravo-user": ("NL", "comment_inference")}
    assert state(done)["rules"] == RULES


def test_a_failed_comments_request_is_that_issues_alone_and_is_asked_for_again(tmp_path: Path) -> None:
    items = [gcl.opened(1, "alpha-user"), gcl.opened(2, "bravo-user")]

    done = commenting(tmp_path, items, failed(comments_url(1)), said(2, ("bravo-user", "Ziggo")))

    assert people(done) == {"alpha-user": ("unknown", "unknown"), "bravo-user": ("NL", "comment_inference")}
    assert state(done)["issues"] == {"2": STAMP}
    assert "WARN: Failed to fetch the comments of issue 1: HTTP 000" in done.log
    assert (done.status, done.ctx.critical) == (0, [])


def test_comments_it_cant_read_are_that_issues_alone(tmp_path: Path) -> None:
    done = commenting(tmp_path, [gcl.opened(1, "alpha-user")], exchange(comments_url(1), {"message": "no"}))

    assert state(done)["issues"] == {}
    assert "WARN: Could not read the comments of issue 1: comments response had an unexpected shape" in done.log


def test_a_rejected_comments_request_is_critical_at_once(tmp_path: Path) -> None:
    done = commenting(tmp_path, [gcl.opened(1, "alpha-user")], exchange(comments_url(1), "", status=403))

    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP 403 from GitHub's comments API")]


def test_a_person_whose_profile_was_not_read_is_not_asked_about(tmp_path: Path) -> None:
    done = run(tmp_path, *issues(gcl.opened(1, "alpha-user")), failed(gcl.profile_url("alpha-user")), cfg=COMMENTING)

    assert people(done)["alpha-user"][1] == "profile_failed"


def test_past_days_follow_the_comment_placements_and_carry_no_state(tmp_path: Path) -> None:
    done = commenting(tmp_path, [gcl.opened(1, "alpha-user")], said(1, ("alpha-user", "Comcast")), seed=EARLIER)

    past = [json.loads(line) for line in done.rows[FILE].splitlines()[1:-1]]
    assert past
    assert all("comments_read" not in r for r in past)
    assert all(r["region_summary"] == {"US": 1} for r in past if r["date"] > "2026-09-01")


def test_without_the_setting_no_comments_are_asked_for_and_no_state_is_written(tmp_path: Path) -> None:
    done = commenting(tmp_path, [gcl.opened(1, "alpha-user")], cfg=CONFIG)

    assert "comments_read" not in done.rows[FILE]


# --- regions from the zone in an attached file ---

ZONING_TABLE = {
    **gcl.REGIONS,
    "attachment": r"(https://files\.example\.test/[^\s)]+\.json)",
    "attachment_value": r'"zone": *"([^"]+)"',
    "zones": [
        {"region": "US", "keywords": ["area-a/city-1"]},
        {"region": "CA", "keywords": ["area-a/city-2"]},
        {"region": "DE", "keywords": ["area-b/town"]},
    ],
}
ZONING = [{"repo": REPO, "regions": ZONING_TABLE}]
ZONE_RULES = timezones.fingerprint(ZONING_TABLE)


def file_url(n: int) -> str:
    return f"https://files.example.test/{n}/diagnostics.json"


def attaching(number: int, who: str, n: int | None = None, body: str = "") -> Any:
    """An issue whose body links file `n` (default: its own number), after `body`."""
    return gcl.opened(number, who, body=f"{body} Diagnostics: [d.json]({file_url(number if n is None else n)})")


def zone_file(n: int, zone: str | None = "area-a/city-1") -> Any:
    """File `n`: text with a zone field (not valid JSON, so it is read as text), or without one."""
    return exchange(file_url(n), f'{{"x": 1, "zone": "{zone}", "y": ' if zone else '{"x": 1}')


def zoning(tmp_path: Path, items: list[Any], *rest: Any, seed: str = "", cfg: Any = ZONING) -> port.PortRun:
    """A run of `cfg` on `items` (every person has an empty profile), then `rest`: the files it asks for."""
    return commenting(tmp_path, items, *rest, seed=seed, cfg=cfg)


def zone_state(done: port.PortRun) -> dict[str, Any]:
    read: dict[str, Any] = json.loads(done.rows[FILE].splitlines()[-1])["timezones_read"]
    return read


def stored_zones(read: dict[str, str], *contributors: dict[str, Any], rules: str = ZONE_RULES) -> str:
    """The earlier night's row: its people and which issues' files it read (and the URL of each)."""
    part = located(*contributors, summary={})
    row_ = {"date": "2026-09-22", "repo": REPO, "total_unique_creators": len(contributors), **part}
    return json.dumps({**row_, "timezones_read": {"rules": rules, "issues": read}}) + "\n"


def test_the_fingerprint_follows_the_attachment_settings_and_zones_only() -> None:
    more = {**ZONING_TABLE, "zones": [*ZONING_TABLE["zones"], {"region": "DE", "keywords": ["area-b/city-3"]}]}
    other = {**ZONING_TABLE, "text": []}

    assert timezones.fingerprint(more) != ZONE_RULES
    assert timezones.fingerprint(other) == ZONE_RULES


def test_the_first_night_reads_the_file_of_each_person_a_zone_can_place(tmp_path: Path) -> None:
    items = [attaching(1, "alpha-user"), attaching(2, "bravo-user")]

    done = zoning(tmp_path, items, zone_file(1, "area-a/city-1"), zone_file(2, "AREA-A/CITY-2"))

    assert people(done) == {"alpha-user": ("US", "timezone_inference"), "bravo-user": ("CA", "timezone_inference")}
    assert zone_state(done) == {"rules": ZONE_RULES, "issues": {"1": file_url(1), "2": file_url(2)}}


def test_a_zone_outranks_the_modem_and_issue_text_and_the_disagreement_is_reported_by_rule(tmp_path: Path) -> None:
    items = [
        attaching(1, "alpha-user", body="Modem: MX6444"),
        attaching(2, "bravo-user", body="I am on Ziggo"),
        attaching(3, "kilo-user", body="Modem: MX6444"),
    ]

    done = zoning(tmp_path, items, zone_file(1), zone_file(2), zone_file(3))

    assert people(done) == dict.fromkeys(("alpha-user", "bravo-user", "kilo-user"), ("US", "timezone_inference"))
    assert "WARN: zone rule 'area-a/city-1' gives US where an issue form or text says DE (2)" in done.log
    assert "WARN: zone rule 'area-a/city-1' gives US where an issue form or text says NL (1)" in done.log


def test_a_zone_that_agrees_with_the_modem_is_not_reported(tmp_path: Path) -> None:
    done = zoning(tmp_path, [attaching(1, "alpha-user", body="Modem: MX6444")], zone_file(1, "area-b/town"))

    assert people(done) == {"alpha-user": ("DE", "issue_inference")}
    assert not [line for line in done.log if "zone rule" in line]


def test_an_isp_answer_is_only_checked_by_a_zone_that_agrees(tmp_path: Path) -> None:
    items = [attaching(1, "alpha-user", body="ISP: Comcast\nModem: MX6444")]

    done = zoning(tmp_path, items, zone_file(1, "area-a/city-1"))

    assert people(done) == {"alpha-user": ("US", "issue_inference")}
    assert zone_state(done)["issues"] == {"1": file_url(1)}
    assert not [line for line in done.log if "zone rule" in line]


def test_an_isp_answer_keeps_its_region_against_a_zone_that_conflicts_and_the_conflict_is_reported(
    tmp_path: Path,
) -> None:
    items = [attaching(1, "alpha-user", body="ISP: Ziggo"), attaching(2, "bravo-user", body="ISP: Ziggo")]

    done = zoning(tmp_path, items, zone_file(1, "area-a/city-1"), zone_file(2, "area-a/city-1"))

    assert people(done) == {"alpha-user": ("NL", "issue_inference"), "bravo-user": ("NL", "issue_inference")}
    assert "WARN: zone rule 'area-a/city-1' gives US where an ISP answer says NL (2)" in done.log
    assert not [line for line in done.log if "alpha-user" in line]


def test_an_isp_answer_with_a_file_that_gives_no_zone_changes_nothing(tmp_path: Path) -> None:
    done = zoning(tmp_path, [attaching(1, "alpha-user", body="ISP: Ziggo")], zone_file(1, None))

    assert people(done) == {"alpha-user": ("NL", "issue_inference")}
    assert zone_state(done)["issues"] == {"1": file_url(1)}
    assert not [line for line in done.log if "zone rule" in line]


def test_a_stored_zone_placement_is_not_applied_to_a_person_an_isp_answer_places(tmp_path: Path) -> None:
    seed = stored_zones({"1": file_url(1)}, entry("alpha-user", "CA", "timezone_inference"))

    done = zoning(tmp_path, [attaching(1, "alpha-user", body="ISP: Ziggo")], seed=seed)

    assert people(done) == {"alpha-user": ("NL", "issue_inference")}


def test_a_profile_stays_first_and_a_zone_that_disagrees_with_it_is_reported(tmp_path: Path) -> None:
    items = [attaching(1, "alpha-user"), attaching(2, "bravo-user")]

    done = run(
        tmp_path,
        *issues(*items),
        gcl.profile("alpha-user", "Zedland, Germany"),
        gcl.profile("bravo-user", "Springfield, IL"),
        zone_file(1),
        zone_file(2),
        cfg=ZONING,
    )

    assert people(done) == {"alpha-user": ("DE", "github_profile"), "bravo-user": ("US", "github_profile")}
    assert "WARN: zone rule 'area-a/city-1' gives US where a profile says DE (1)" in done.log
    assert zone_state(done)["issues"] == {"1": file_url(1), "2": file_url(2)}  # a placed person's file is read too


def test_a_profile_location_no_rule_reads_is_placed_by_the_zone(tmp_path: Path) -> None:
    done = run(
        tmp_path, *issues(attaching(1, "alpha-user")), gcl.profile("alpha-user", "Somewhere at sea"), zone_file(1),
        cfg=ZONING,
    )  # fmt: skip

    assert people(done) == {"alpha-user": ("US", "timezone_inference")}
    assert json.loads(done.rows[FILE].splitlines()[-1])["contributors"][0]["location"] == "Somewhere at sea"


def test_only_the_newest_issues_file_is_read_and_a_pull_requests_is_not(tmp_path: Path) -> None:
    items = [
        attaching(1, "alpha-user"),
        attaching(3, "alpha-user"),
        gcl.opened(4, "alpha-user", body=f"[d.json]({file_url(4)})", pull_request=True),
    ]

    done = zoning(tmp_path, items, zone_file(3, "area-a/city-2"))

    assert people(done) == {"alpha-user": ("CA", "timezone_inference")}
    assert zone_state(done)["issues"] == {"3": file_url(3)}


def test_a_later_night_reads_one_file_a_person_with_none_read_first_and_the_lowest_number_first(
    tmp_path: Path,
) -> None:
    items = [attaching(1, "alpha-user"), attaching(2, "bravo-user"), attaching(3, "kilo-user")]
    seed = stored_zones({"1": file_url(1)}, entry("alpha-user"), entry("bravo-user"), entry("kilo-user"))

    done = zoning(tmp_path, items, zone_file(2), seed=seed)

    assert people(done)["bravo-user"] == ("US", "timezone_inference")
    assert zone_state(done)["issues"] == {"1": file_url(1), "2": file_url(2)}


def test_a_changed_link_is_read_again_after_the_unread_ones(tmp_path: Path) -> None:
    items = [attaching(1, "alpha-user", 9), attaching(2, "bravo-user")]
    seed = stored_zones({"1": file_url(1), "2": file_url(2)}, entry("alpha-user"), entry("bravo-user"))

    done = zoning(tmp_path, items, zone_file(9, "area-a/city-2"), seed=seed)

    assert people(done)["alpha-user"] == ("CA", "timezone_inference")
    assert zone_state(done)["issues"] == {"1": file_url(9), "2": file_url(2)}


def test_with_every_file_read_unchanged_none_is_asked_for_and_a_zone_placement_stays(tmp_path: Path) -> None:
    items = [attaching(1, "alpha-user"), attaching(2, "bravo-user")]
    seed = stored_zones(
        {"1": file_url(1), "2": file_url(2)}, entry("alpha-user", "CA", "timezone_inference"), entry("bravo-user")
    )

    done = zoning(tmp_path, items, seed=seed)

    assert people(done) == {"alpha-user": ("CA", "timezone_inference"), "bravo-user": ("unknown", "unknown")}


def test_a_person_a_profile_now_places_loses_their_zone_placement(tmp_path: Path) -> None:
    seed = stored_zones({"1": file_url(1)}, entry("alpha-user", "CA", "timezone_inference"))

    done = run(
        tmp_path, *issues(attaching(1, "alpha-user")), gcl.profile("alpha-user", "Zedland, Germany"), seed=seed,
        cfg=ZONING,
    )  # fmt: skip

    assert people(done) == {"alpha-user": ("DE", "github_profile")}


def test_changed_rules_start_the_pass_again_and_drop_what_the_old_rules_placed(tmp_path: Path) -> None:
    items = [attaching(1, "alpha-user"), attaching(2, "bravo-user")]
    seed = stored_zones(
        {"1": file_url(1), "2": file_url(2)}, entry("alpha-user", "FR", "timezone_inference"), entry("bravo-user"),
        rules="old",
    )  # fmt: skip

    done = zoning(tmp_path, items, zone_file(1), zone_file(2, "area-a/city-2"), seed=seed)

    assert people(done) == {"alpha-user": ("US", "timezone_inference"), "bravo-user": ("CA", "timezone_inference")}
    assert zone_state(done)["rules"] == ZONE_RULES


def test_a_failed_request_is_that_files_alone_and_is_asked_for_again(tmp_path: Path) -> None:
    items = [attaching(1, "alpha-user"), attaching(2, "bravo-user")]

    done = zoning(tmp_path, items, failed(file_url(1)), zone_file(2))

    assert people(done) == {"alpha-user": ("unknown", "unknown"), "bravo-user": ("US", "timezone_inference")}
    assert zone_state(done)["issues"] == {"2": file_url(2)}
    assert "WARN: Failed to fetch the file of issue 1: HTTP 000" in done.log
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_failed_request_on_a_later_night_ends_that_nights_read(tmp_path: Path) -> None:
    items = [attaching(1, "alpha-user"), attaching(2, "bravo-user")]
    seed = stored_zones({}, entry("alpha-user"), entry("bravo-user"))

    done = zoning(tmp_path, items, failed(file_url(1)), seed=seed)

    assert zone_state(done)["issues"] == {}


def test_a_file_the_service_no_longer_has_is_read_once_and_is_not_critical(tmp_path: Path) -> None:
    done = zoning(tmp_path, [attaching(1, "alpha-user")], exchange(file_url(1), "", status=404))

    assert people(done) == {"alpha-user": ("unknown", "unknown")}
    assert zone_state(done)["issues"] == {"1": file_url(1)}
    assert "WARN: The file of issue 1 is gone: HTTP 404" in done.log
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_rejected_request_is_asked_for_again_and_is_not_critical(tmp_path: Path) -> None:
    done = zoning(tmp_path, [attaching(1, "alpha-user")], exchange(file_url(1), "", status=403))

    assert zone_state(done)["issues"] == {}
    assert (done.status, done.ctx.critical) == (0, [])


@pytest.mark.parametrize(
    "file", [zone_file(1, None), zone_file(1, "area-z/town"), exchange(file_url(1), "")],
    ids=["no zone field", "a zone no rule names", "an empty file"],
)  # fmt: skip
def test_a_file_that_gives_no_region_is_read_once_and_changes_nobody(tmp_path: Path, file: Any) -> None:
    done = zoning(tmp_path, [attaching(1, "alpha-user", body="Modem: MX6444")], file)

    assert people(done) == {"alpha-user": ("DE", "issue_inference")}
    assert zone_state(done)["issues"] == {"1": file_url(1)}


def test_the_file_is_asked_for_without_the_token(tmp_path: Path) -> None:
    """The file is on another host than the API, and its link is public: the token is never sent to it."""
    sent: list[Any] = []
    scenario = replace(
        port.scenario(NAME, "regions_from_profile_locations"),
        responses=[*issues(attaching(1, "alpha-user")), gcl.profile("alpha-user"), zone_file(1)],
        seed={FILE: ""},
    )
    transport = SavedResponses(scenario.responses)

    def recording(request: Any, timeout: float) -> Any:
        sent.append(request)
        return transport(request, timeout)

    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    ctx.http._transport = recording  # to see the request headers
    with port.credentials({"GITHUB_TOKEN": "t0ken"}, source.ENV):
        source.collect(ctx, ZONING)

    asked = [r for r in sent if r.full_url == file_url(1)]
    assert len(asked) == 1
    assert asked[0].get_header("Authorization") is None
    assert {r.get_header("Authorization") for r in sent if r.full_url != file_url(1)} == {"Bearer t0ken"}


def test_nothing_of_a_file_but_the_region_is_kept_or_logged(tmp_path: Path) -> None:
    done = zoning(tmp_path, [attaching(1, "alpha-user", body="Modem: MX6444")], zone_file(1, "area-a/city-1"))

    assert "area-a/city-1" not in done.rows[FILE]
    assert not [line for line in done.log if "alpha-user" in line and "zone" in line]


def test_a_person_whose_profile_was_not_read_is_not_zoned(tmp_path: Path) -> None:
    done = run(tmp_path, *issues(attaching(1, "alpha-user")), failed(gcl.profile_url("alpha-user")), cfg=ZONING)

    assert people(done)["alpha-user"][1] == "profile_failed"
    assert zone_state(done)["issues"] == {}


def test_a_zone_placed_person_is_not_asked_about_in_their_comments(tmp_path: Path) -> None:
    cfg = [{**ZONING[0], "comments": True}]

    done = zoning(tmp_path, [attaching(1, "alpha-user")], zone_file(1), cfg=cfg)  # a comments request would fail

    assert people(done) == {"alpha-user": ("US", "timezone_inference")}
    assert json.loads(done.rows[FILE].splitlines()[-1])["comments_read"]["issues"] == {}


def test_past_days_follow_the_zone_placements_and_carry_no_state(tmp_path: Path) -> None:
    done = zoning(tmp_path, [attaching(1, "alpha-user")], zone_file(1), seed=EARLIER)

    past = [json.loads(line) for line in done.rows[FILE].splitlines()[1:-1]]
    assert past
    assert all("timezones_read" not in r for r in past)
    assert all(r["region_summary"] == {"US": 1} for r in past if r["date"] > "2026-09-01")


def test_without_the_setting_no_file_is_asked_for_and_no_state_is_written(tmp_path: Path) -> None:
    done = zoning(tmp_path, [attaching(1, "alpha-user")], cfg=CONFIG)

    assert "timezones_read" not in done.rows[FILE]


# --- known regions ---

KNOWN_US = {**gcl.REGIONS, "known": {"Person-A": "US"}}
KNOWING = [{"repo": REPO, "regions": KNOWN_US}]


def known_lines(done: port.PortRun) -> list[str]:
    return [line for line in done.log if "known region" in line]


def test_a_known_region_places_a_person_with_none_by_login_ignoring_case(tmp_path: Path) -> None:
    done = commenting(tmp_path, [gcl.opened(1, "person-a"), gcl.opened(2, "bravo-user")], cfg=KNOWING)

    assert people(done) == {"person-a": ("US", "configured"), "bravo-user": ("unknown", "unknown")}


def test_a_known_region_places_a_person_whose_profile_location_no_rule_reads(tmp_path: Path) -> None:
    done = run(tmp_path, *issues(gcl.opened(1, "person-a")), gcl.profile("person-a", "Somewhere at sea"), cfg=KNOWING)

    last = json.loads(done.rows[FILE].splitlines()[-1])["contributors"][0]
    assert (last["region"], last["source"], last["location"]) == ("US", "configured", "Somewhere at sea")


def test_a_profile_location_outranks_a_known_region_and_the_disagreement_is_logged_without_a_login(
    tmp_path: Path,
) -> None:
    items = [gcl.opened(1, "person-a"), gcl.opened(2, "bravo-user")]
    cfg = [{"repo": REPO, "regions": {**gcl.REGIONS, "known": {"person-a": "US", "bravo-user": "US"}}}]

    profiles = [gcl.profile("person-a", "Zedland, Germany"), gcl.profile("bravo-user", "Springfield, IL")]

    done = run(tmp_path, *issues(*items), *profiles, cfg=cfg)

    assert people(done) == {"person-a": ("DE", "github_profile"), "bravo-user": ("US", "github_profile")}
    assert known_lines(done) == ["WARN: a known region says US where a profile says DE (1)"]
    assert not [line for line in known_lines(done) if "person-a" in line or "bravo-user" in line]


def test_a_known_region_outranks_the_answer_the_modem_and_issue_text_and_each_disagreeing_clue_is_logged(
    tmp_path: Path,
) -> None:
    items = [
        gcl.opened(1, "person-a", body="ISP: Ziggo\nModem: MX4236"),
        gcl.opened(2, "person-b", body="Modem: MX6444"),
        gcl.opened(3, "person-c", body="I am on Ziggo"),
    ]
    cfg = [{"repo": REPO, "regions": {**gcl.REGIONS, "known": {"person-a": "US", "person-b": "US", "person-c": "US"}}}]

    done = commenting(tmp_path, items, cfg=cfg)

    assert people(done) == dict.fromkeys(("person-a", "person-b", "person-c"), ("US", "configured"))
    assert known_lines(done) == [
        "WARN: a known region says US where a modem says DE (1)",
        "WARN: a known region says US where an ISP answer says NL (1)",
    ]


def test_a_known_region_that_agrees_with_every_clue_logs_nothing(tmp_path: Path) -> None:
    items = [gcl.opened(1, "person-a", body="ISP: Comcast\nModem: MX6444 Comcast")]
    cfg = [{"repo": REPO, "regions": {**gcl.REGIONS, "known": {"person-a": "US"}, "models": []}}]

    done = commenting(tmp_path, items, cfg=cfg)

    assert people(done) == {"person-a": ("US", "configured")}
    assert known_lines(done) == []


def test_a_known_region_outranks_a_zone_whose_file_is_read_only_to_check_it(tmp_path: Path) -> None:
    table = {**ZONING_TABLE, "known": {"person-a": "US", "person-b": "US"}}
    items = [attaching(1, "person-a"), attaching(2, "person-b")]

    files = [zone_file(1, "area-a/city-1"), zone_file(2, "area-a/city-2")]

    done = zoning(tmp_path, items, *files, cfg=[{"repo": REPO, "regions": table}])

    assert people(done) == {"person-a": ("US", "configured"), "person-b": ("US", "configured")}
    assert "WARN: zone rule 'area-a/city-2' gives CA where a known region says US (1)" in done.log
    assert zone_state(done)["issues"] == {"1": file_url(1), "2": file_url(2)}
    assert not [line for line in done.log if "person-" in line]


def test_a_known_person_is_not_asked_about_in_their_comments(tmp_path: Path) -> None:
    cfg = [{"repo": REPO, "regions": KNOWN_US, "comments": True}]

    done = commenting(tmp_path, [gcl.opened(1, "person-a")], cfg=cfg)  # a comments request would fail

    assert people(done) == {"person-a": ("US", "configured")}


def test_a_known_region_is_read_from_the_config_every_run_and_past_days_follow_it(tmp_path: Path) -> None:
    done = commenting(tmp_path, [gcl.opened(1, "person-a")], seed=EARLIER, cfg=KNOWING)

    past = [json.loads(line) for line in done.rows[FILE].splitlines()[1:]]
    assert past
    assert all(r["region_summary"] == {"US": 1} for r in past if r["date"] > "2026-09-01")
    assert all(c["source"] == "configured" for r in past if r["date"] > "2026-09-01" for c in r["contributors"])


def test_a_known_region_that_is_removed_from_the_config_no_longer_places_the_person(tmp_path: Path) -> None:
    first = commenting(tmp_path / "one", [gcl.opened(1, "person-a")], cfg=KNOWING)
    second = commenting(tmp_path / "two", [gcl.opened(1, "person-a")], seed=first.rows[FILE], cfg=CONFIG)

    assert people(first) == {"person-a": ("US", "configured")}
    assert people(second) == {"person-a": ("unknown", "unknown")}


def test_a_person_whose_profile_was_not_read_is_not_placed_by_a_known_region(tmp_path: Path) -> None:
    done = run(tmp_path, *issues(gcl.opened(1, "person-a")), failed(gcl.profile_url("person-a")), cfg=KNOWING)

    assert people(done)["person-a"][1] == "profile_failed"


def test_without_the_setting_nothing_is_logged_about_known_regions(tmp_path: Path) -> None:
    done = commenting(tmp_path, [gcl.opened(1, "person-a")], cfg=CONFIG)

    assert people(done) == {"person-a": ("unknown", "unknown")}
    assert known_lines(done) == []


# --- check() ---


def check(tmp_path: Path, cfg: Any, *responses: Any) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, source.ENV):
        findings = source.check(ctx, cfg)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


REPO_URL = f"{GITHUB_API}/repos/{REPO}"
REPOSITORY = exchange(REPO_URL, {"full_name": REPO})


def test_check_finds_the_repo_and_that_the_token_can_read_its_issues(tmp_path: Path) -> None:
    assert check(tmp_path, CONFIG, REPOSITORY, exchange(ISSUES, [])) == [
        Finding("ok", f"{REPO}: the repo exists"),
        Finding("ok", f"{REPO}: the token can read its issues"),
    ]


@pytest.mark.parametrize(
    ("responses", "findings"),
    [
        (
            [exchange(REPO_URL, '{"message":"Not Found"}', status=404)],
            [Finding("error", f"{REPO}: HTTP 404: the repo doesn't exist, or the token can't read it")],
        ),
        (
            [exchange(REPO_URL, "", status=403, headers={"Retry-After": "60"})],
            [Finding("warn", f"{REPO}: could not fetch the repo: HTTP 403, rate limited")],
        ),
        (
            [REPOSITORY, failed(ISSUES)],
            [Finding("ok", f"{REPO}: the repo exists"), Finding("warn", f"{REPO}: could not fetch its issues: HTTP 0")],
        ),
        (
            [REPOSITORY, exchange(ISSUES, "", status=403)],
            [
                Finding("ok", f"{REPO}: the repo exists"),
                Finding("error", f"{REPO}: HTTP 403: the repo doesn't exist, or the token can't read it"),
            ],
        ),
    ],
    ids=["no repo, and no issues asked for", "the repo rate limited", "no connection for issues", "issues rejected"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(
    tmp_path: Path, responses: list[Any], findings: list[Finding]
) -> None:
    assert check(tmp_path, CONFIG, *responses) == findings


def test_check_checks_every_entry(tmp_path: Path) -> None:
    other = f"{GITHUB_API}/repos/a/other"
    cfg = [*CONFIG, {"repo": "a/other", "regions": gcl.REGIONS}]

    findings = check(tmp_path, cfg, REPOSITORY, exchange(ISSUES, []), exchange(other, "", status=404))

    assert [f.level for f in findings] == ["ok", "ok", "error"]


ANSWERS = "regions_from_issue_form_answers"
UNMATCHED = Finding("warn", f"{REPO}: answers no region rule matches: Example Fibre (2)")


def test_check_names_the_answers_no_rule_matches_and_the_rules_a_profile_disagrees_with(tmp_path: Path) -> None:
    """From the issues collection read, which the run reuses, and the run's row: no request of its own, no login."""
    collected = port.run(source, CONFIG, port.scenario(NAME, ANSWERS), tmp_path / "collected")
    stored = collected.rows[FILE]
    (tmp_path / FILE).write_text(stored, encoding="utf-8")
    transport = SavedResponses([REPOSITORY, *issues(*gcl.ANSWERS)])
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])

    with port.credentials(scenarios.CREDENTIALS, source.ENV):
        findings = source.check(ctx, CONFIG)

    assert findings == [
        Finding("ok", f"{REPO}: the repo exists"),
        Finding("ok", f"{REPO}: the token can read its issues"),
        UNMATCHED,
        Finding("warn", f"{REPO}: rule 'ziggo' gives NL for an answer where a profile says DE (1)"),
    ]
    assert transport.unmatched == transport.never_served() == []
    assert (tmp_path / FILE).read_text(encoding="utf-8") == stored  # a check writes no rows


FORMS = "regions_from_exact_answers_and_modems"


def test_check_names_each_modem_rule_that_disagrees_with_an_answer(tmp_path: Path) -> None:
    """The answer gives the region, and the disagreement is reported, by count, never by login."""
    collected = port.run(source, CONFIG, port.scenario(NAME, FORMS), tmp_path / "collected")
    (tmp_path / FILE).write_text(collected.rows[FILE], encoding="utf-8")
    transport = SavedResponses([REPOSITORY, *issues(*gcl.FORMS)])
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])

    with port.credentials(scenarios.CREDENTIALS, source.ENV):
        findings = source.check(ctx, CONFIG)

    assert findings[2:] == [
        Finding("warn", f"{REPO}: answers no region rule matches: Exodus (1)"),  # the modem places the other
        Finding("warn", f"{REPO}: rule 'ex' gives UK for an answer where a profile says DE (1)"),
        Finding("warn", f"{REPO}: model 'mx4236' gives NL where an answer says UK (1)"),
    ]


def test_check_counts_people_not_issues(tmp_path: Path) -> None:
    disagreeing, unmatched = "ISP: Ex\nModem: MX4236", "ISP: Exodus"
    again = [gcl.opened(n, "whiskey-user", body=body) for n, body in enumerate([disagreeing, unmatched] * 2, 1)]

    assert check(tmp_path, CONFIG, REPOSITORY, *issues(*again))[2:] == [
        Finding("warn", f"{REPO}: answers no region rule matches: Exodus (1)"),
        Finding("warn", f"{REPO}: model 'mx4236' gives NL where an answer says UK (1)"),
    ]


def test_forms_are_never_logged(tmp_path: Path) -> None:
    done = port.run(source, CONFIG, port.scenario(NAME, FORMS), tmp_path)

    assert not [line for line in done.log if "MX4236" in line or "Exodus" in line]


def test_without_the_days_row_check_names_only_the_answers_no_rule_matches(tmp_path: Path) -> None:
    assert check(tmp_path, CONFIG, REPOSITORY, *issues(*gcl.ANSWERS))[2:] == [UNMATCHED]


def test_without_an_answer_regex_check_reads_no_answers(tmp_path: Path) -> None:
    plain = {key: rules for key, rules in gcl.REGIONS.items() if key != "answer"}
    cfg = [{"repo": REPO, "regions": plain}]

    assert len(check(tmp_path, cfg, REPOSITORY, *issues(*gcl.ANSWERS))) == 2


# --- the summary ---


def test_its_sections_are_labelled_contributors() -> None:
    assert [section.label for section in source.summary(CONFIG)] == ["Contributors"]


def test_its_totals_are_charted_only_in_the_regions_stack_with_unknown_apart() -> None:
    (section,) = source.summary(CONFIG)

    assert {shown.field: shown.into for shown in section.shown} == {
        "total_unique_creators": "region_summary",
        "with_inferred_region": "region_summary",
        "region_summary": "",
    }
    assert section.shown[2].neutral == ("unknown",)


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(source, CONFIG, "regions_from_profile_locations", tmp_path) == [
        "",
        "### github_contributor_locations: solentlabs/example",
        "",
        "- Contributors: 8",
        "- With a region: 7",
        "- Regions: DE 2 · UK 1 · US 1 · unknown 1 · FI 1 · other 2",
    ]


# --- recomputed history (ADR: derived history follows today's rules) ---

LATE_SCENARIO = "past_days_are_recomputed_back_to_the_first_persons_first_issue"


def test_every_past_day_is_recomputed_each_change_a_correction_and_a_second_run_changes_nothing(tmp_path: Path) -> None:
    """The scenario's reviewed rows hold the values: the row written under earlier rules is corrected, recorded for
    health, and a run after it finds nothing to change."""
    scenario = port.scenario(NAME, LATE_SCENARIO)

    first = port.run(source, CONFIG, scenario, tmp_path / "first")
    again = port.run(source, CONFIG, replace(scenario, seed=first.rows), tmp_path / "again")

    assert f"Recomputed {FILE}: 1 corrected, 2 added, 1 failure row(s) replaced" in first.log
    [corrected] = first.ctx.corrections
    assert (corrected.date, list(corrected.old), list(corrected.new)) == (
        "2026-09-20",
        ["with_inferred_region", "region_summary", "contributors"],
        ["with_inferred_region", "region_summary", "contributors"],
    )
    assert (corrected.old["with_inferred_region"], corrected.new["with_inferred_region"]) == (2, 3)
    assert again.rows == first.rows
    assert again.ctx.corrections == []
    assert not [line for line in again.log if line.startswith("Recomputed")]


def test_with_a_profile_unread_no_past_day_is_recomputed(tmp_path: Path) -> None:
    """A past day's totals would count short; a later night that reads every profile recomputes them."""
    scenario = port.scenario(NAME, LATE_SCENARIO)
    responses = [*scenario.responses[:-1], failed(gcl.profile_url("echo-user"))]

    done = port.run(source, CONFIG, replace(scenario, responses=responses), tmp_path)

    assert done.rows[FILE].startswith(gcl.LATE)
    assert done.ctx.corrections == []
    assert f"Not recomputing past contributors for {REPO}: 1 profile(s) unread" in done.log
