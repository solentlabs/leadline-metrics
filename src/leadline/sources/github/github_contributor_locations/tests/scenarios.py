"""github_contributor_locations: where the people who open a repository's issues and pull requests are.

The script's collect_contributor_locations REPO reads every page of the repository's issues (following
Link), then, for each person who opened one (not a bot, not a missing user), their profile. A profile
location matching the ISP or place patterns gives the region (`github_profile`): the first pattern
that matches, ISPs before places; a location matching none is `profile_unparsed`. Only with no
location at all (none set, empty, or the profile not fetched) are the person's issues read, all of
them: the ISP patterns only, in each title and the first 500 characters of its body
(`issue_inference`). It writes one row for the run's date into
github-contributor-locations-<repo name>.jsonl: the counts, the regions, and each person. A failed
issues page writes nothing.

Every login and location is made up: each location names a made-up place in a region the patterns
know, or nothing they know.

leadline's config (LEADLINE_CONFIG) is one entry for REPO with REGIONS, the script's rules for the regions the
scenarios name, in its order, each with some of its keywords: its ISP patterns as `text` rules, its place
patterns as `location` rules. REGIONS also reads issue forms (`answer`, `exact`, `model_answer`, `models`),
which the script doesn't and no script scenario's issue has: `regions_from_issue_form_answers` and
`regions_from_exact_answers_and_modems` have reviewed rows (ADR: an answer in an issue form is the strongest
region signal). Where leadline differs by a decision, the scenario is in DIVERGENCES, and
tests/test_github_contributor_locations.py asserts leadline's side: a failed issues page is a `fetch_failed`
failure row (ADR: a failure is recorded as a failure), and a failed profile marks that person alone as unread
(`profile_failed`, no location or region) in a partial row, where the script reads the person's issues as if
the profile had no location (ADR: a partly failed row keeps its good parts).
"""

from __future__ import annotations

import json
import random
from datetime import datetime
from typing import Any

from scenarios import NOW, Scenario
from scenarios.builders.common import Exchange, Json, exchange, failed, failing_at

from leadline.sources.github.tests.builders import (
    GITHUB_API,
    github_issue,
    github_issue_pages,
    github_profile,
    made_up_issues,
)

REPO = "solentlabs/example"
CALLS = [f'collect_contributor_locations "{REPO}"']
FILE = "github-contributor-locations-example.jsonl"
MAINTAINER = "example-maintainer"
# An earlier night's row, which a failure keeps.
EARLIER = '{"date": "2026-09-01", "repo": "solentlabs/example", "total_unique_creators": 3}\n'
# The script's rules for the regions the scenarios name, in its order, each with some of its keywords.
REGIONS = {
    "text": [
        {"region": "UK", "keywords": ["virgin media"]},
        {"region": "NL", "keywords": ["ziggo"]},
        {"region": "US", "keywords": ["comcast"]},
        {"region": "CA", "keywords": ["rogers"]},
        {"region": "AU", "keywords": ["telstra"]},
    ],
    "location": [
        {"region": "DE", "keywords": ["germany"]},
        {"region": "UK", "keywords": ["england"]},
        {"region": "FI", "keywords": ["finland"]},
        {"region": "US", "keywords": [", il"]},
    ],
    # leadline's own: a person's answers in an issue form, their provider and their modem, which the script doesn't
    # read; no scenario of the script's has one. An exact answer, too short for a keyword, and modems sold in one
    # country only.
    "answer": r"^ISP: *(.*)$",
    "exact": [{"region": "UK", "answers": ["ex"]}],
    "model_answer": r"^Modem: *(.*)$",
    "models": [{"region": "DE", "keywords": ["mx6444"]}, {"region": "NL", "keywords": ["mx4236"]}],
}
LEADLINE_CONFIG: list[dict[str, Any]] = [{"repo": REPO, "regions": REGIONS}]


def profile_url(login: str) -> str:
    return f"{GITHUB_API}/users/{login}"


def profile(login: str, location: str | None = None) -> Exchange:
    return exchange(profile_url(login), github_profile(login, location, own=login == MAINTAINER))


def opened(
    number: int,
    author: str | None,
    title: str = "An example issue",
    body: str | None = "",
    created_at: str = "2026-09-01T10:00:00Z",
    pull_request: bool = False,
) -> Json:
    return github_issue(
        REPO, number, author=author, title=title, body=body, created_at=created_at, pull_request=pull_request
    )


def responses(issues: list[Json], profiles: list[Exchange]) -> list[Exchange]:
    return [*github_issue_pages(REPO, "all", issues), *profiles]


FROM_PROFILES = [
    opened(1, "alpha-user"),
    opened(2, "bravo-user"),
    opened(3, "charlie-user"),
    opened(4, "delta-user", body="On Ziggo"),  # a location is set, so the issue isn't read
    opened(5, "india-user"),
    opened(6, MAINTAINER),
    opened(7, "alpha-user", title="A second issue"),
    opened(8, "juliet-user"),
    opened(9, "kilo-user"),
]
PROFILE_LOCATIONS = [
    profile("alpha-user", "Yondershire, England"),
    profile("bravo-user", "Zedland, Germany"),
    profile("charlie-user", "Springfield, IL"),
    profile("delta-user", "Somewhere at sea"),
    profile("india-user", "A Telstra customer"),  # an ISP in a location counts too
    profile(MAINTAINER, "Zednet, Finland"),
    profile("juliet-user", "Zedland, Germany, on Ziggo"),  # an ISP before a place
    profile("kilo-user", "Zedland, Germany and Yondershire, England"),  # the first place listed
]
FROM_ISSUES = [
    opened(1, "echo-user", title="Example modem on Ziggo", body=None),
    opened(2, "foxtrot-user", body="Nothing that says where."),
    opened(3, "golf-user", body="My provider is Virgin Media."),
    opened(4, "hotel-user", body="x" * 500 + " Comcast"),  # past the first 500 characters
    opened(5, "dependabot[bot]", title="Bump example on Comcast"),
    opened(6, None, title="Example modem on Rogers"),
    opened(7, "lima-user", body="Writing from Zedland, Germany."),  # issues are read for ISPs only
    opened(8, "mike-user"),
    opened(9, "mike-user", body="Now on Telstra."),  # a later issue of the same person
    opened(10, "november-user", body="On Comcast."),
]
ISSUE_PROFILES = [
    profile("echo-user"),
    profile("foxtrot-user"),
    exchange(profile_url("golf-user"), '{"message":"Not Found"}', status=404),  # no profile: no location
    profile("hotel-user"),
    profile("lima-user"),
    profile("mike-user"),
    profile("november-user", ""),  # an empty location is none
]
# Two pages; only the second has an issue by "page-two-user".
MANY = [
    *made_up_issues(random.Random(5), REPO, 100, now=datetime.fromisoformat(NOW)),
    *made_up_issues(
        random.Random(6),
        REPO,
        50,
        now=datetime.fromisoformat(NOW),
        first=101,
        authors=("example-user", "page-two-user"),
    ),
]
MANY_PROFILES = [profile(login) for login in ("another-user", "example-maintainer", "example-user", "page-two-user")]
# One profile fails: the script reads that person's issues as if the profile had no location.
FAILED_PROFILE = [opened(1, "oscar-user", body="On Ziggo."), opened(2, "papa-user", body="On Comcast.")]

# Answers in issue forms (leadline's own): each person with no location gets the region of their first answer a rule
# matches, the text rules and then the location rules; with none, their issue text's, as the script reads it.
ANSWERS = [
    opened(1, "quebec-user", body="On Comcast for years.\n" + "x" * 600 + "\nISP: Rogers"),  # before the text, far down
    opened(2, "romeo-user", body="ISP: Telia (Zednet, Finland)"),  # a location rule
    opened(3, "sierra-user", body="ISP: Example Fibre\nOn Ziggo."),  # no rule: the issue text's
    opened(4, "tango-user", body="ISP: Ziggo"),  # a location is set, so the answer isn't used
    opened(5, "uniform-user", body="x" * 600 + "\nISP: Telstra", pull_request=True),  # a pull request's isn't read
    opened(6, "victor-user", body="ISP: Example Fibre"),
    opened(7, "victor-user", body="ISP: Virgin Media"),  # a later answer a rule matches
]
ANSWER_PROFILES = [
    profile("quebec-user"),
    profile("romeo-user"),
    profile("sierra-user"),
    profile("tango-user", "Zedland, Germany"),
    profile("uniform-user"),
    profile("victor-user"),
]
ANSWERED = [
    {"login": "quebec-user", "location": None, "region": "CA", "source": "issue_inference"},
    {"login": "romeo-user", "location": None, "region": "FI", "source": "issue_inference"},
    {"login": "sierra-user", "location": None, "region": "NL", "source": "issue_inference"},
    {"login": "tango-user", "location": "Zedland, Germany", "region": "DE", "source": "github_profile"},
    {"login": "uniform-user", "location": None, "region": "unknown", "source": "unknown"},
    {"login": "victor-user", "location": None, "region": "UK", "source": "issue_inference"},
]
ANSWERED_ROW = {
    "date": "2026-09-23",
    "repo": REPO,
    "total_unique_creators": 6,
    "with_github_location": 1,
    "with_inferred_region": 5,
    "region_summary": {"CA": 1, "FI": 1, "NL": 1, "DE": 1, "unknown": 1, "UK": 1},
    "contributors": ANSWERED,
}

# Exact answers and modems (leadline's own): an answer an exact rule matches gives the region though the modem says
# another; a modem sold in one country gives it where no rule matches the answer; any answer a rule matches comes
# before a modem; an exact answer is never a keyword; a profile location still comes first.
FORMS = [
    opened(1, "whiskey-user", body="ISP: EX\nModem: Example MX4236"),
    opened(2, "xray-user", body="ISP: Example Fibre\nModem: Example MX6444 v2"),
    opened(3, "yankee-user", body="ISP: Exodus\nModem: Example SB1\nOn Ziggo."),  # neither: the issue text's
    opened(4, "zulu-user", body="Modem: MX6444"),
    opened(5, "zulu-user", body="ISP: Virgin Media"),  # a later answer comes before an earlier modem
    opened(6, "tango-user", body="ISP: Ex"),  # a location is set, so the form isn't used
]
FORM_PROFILES = [
    profile("tango-user", "Zedland, Germany"),
    profile("whiskey-user"),
    profile("xray-user"),
    profile("yankee-user"),
    profile("zulu-user"),
]
FORMED = [
    {"login": "tango-user", "location": "Zedland, Germany", "region": "DE", "source": "github_profile"},
    {"login": "whiskey-user", "location": None, "region": "UK", "source": "issue_inference"},
    {"login": "xray-user", "location": None, "region": "DE", "source": "issue_inference"},
    {"login": "yankee-user", "location": None, "region": "NL", "source": "issue_inference"},
    {"login": "zulu-user", "location": None, "region": "UK", "source": "issue_inference"},
]
FORMED_ROW = {
    "date": "2026-09-23",
    "repo": REPO,
    "total_unique_creators": 5,
    "with_github_location": 1,
    "with_inferred_region": 5,
    "region_summary": {"DE": 2, "UK": 2, "NL": 1},
    "contributors": FORMED,
}

# A bot, then three people a day apart, with regions from two profiles and an issue; and a file whose rows begin on
# 2026-09-20, after them.
LATE_ISSUES = [
    opened(4, "dependabot[bot]", created_at="2026-09-17T10:00:00Z"),
    opened(1, "alpha-user", created_at="2026-09-18T10:00:00Z"),
    opened(2, "bravo-user", created_at="2026-09-19T10:00:00Z"),
    opened(3, "echo-user", title="Example modem on Ziggo", body=None, created_at="2026-09-20T10:00:00Z"),
]
LATE_PROFILES = [
    profile("alpha-user", "Yondershire, England"),
    profile("bravo-user", "Zedland, Germany"),
    profile("echo-user"),
]
PEOPLE = [
    '{"login": "alpha-user", "location": "Yondershire, England", "region": "UK", "source": "github_profile"}',
    '{"login": "bravo-user", "location": "Zedland, Germany", "region": "DE", "source": "github_profile"}',
    '{"login": "echo-user", "location": null, "region": "NL", "source": "issue_inference"}',
]
SUMMARIES = ['{"UK": 1}', '{"UK": 1, "DE": 1}', '{"UK": 1, "DE": 1, "NL": 1}']


def located(day: str, n: int, located: int) -> str:
    """The row of `day` with the first `n` people, `located` of them by their profile, up to its `captured_at`."""
    return (
        f'{{"date": "{day}", "repo": "solentlabs/example", "total_unique_creators": {n}, '
        f'"with_github_location": {located}, "with_inferred_region": {n}, "region_summary": {SUMMARIES[n - 1]}, '
        f'"contributors": [{", ".join(PEOPLE[:n])}], '
    )


STAMP = '"captured_at": "2026-09-23T23:31:05+00:00"}\n'
# The file's rows: 2026-09-20 written under earlier rules, which placed echo-user nowhere; 2026-09-21 as today's
# rules place everyone; 2026-09-22 a night that couldn't read echo-user's profile.
LATE = (
    '{"date": "2026-09-20", "repo": "solentlabs/example", "total_unique_creators": 3, "with_github_location": 2, '
    '"with_inferred_region": 2, "region_summary": {"UK": 1, "DE": 1, "unknown": 1}, '
    f'"contributors": [{PEOPLE[0]}, {PEOPLE[1]}, '
    '{"login": "echo-user", "location": null, "region": "unknown", "source": "unknown"}], '
    '"captured_at": "2026-09-20T23:30:00+00:00"}\n'
    + located("2026-09-21", 3, 2)
    + '"captured_at": "2026-09-21T23:30:00+00:00"}\n'
    + '{"date": "2026-09-22", "repo": "solentlabs/example", "total_unique_creators": 3, "with_github_location": 2, '
    + '"with_inferred_region": 2, "region_summary": {"UK": 1, "DE": 1}, '
    + f'"contributors": [{PEOPLE[0]}, {PEOPLE[1]}, '
    + '{"login": "echo-user", "location": null, "region": null, "source": "profile_failed"}], '
    + '"status": "partial", "reason": "1 of 3 profiles could not be read", '
    + '"captured_at": "2026-09-22T23:30:00+00:00"}\n'
)
# Every past day, back to the first person's first issue (ADR: derived history follows today's rules): the people
# who had opened one by the end of each day, each with the region the run gives; the days before the file's rows
# added, the earlier rules' row corrected in its place, the row that agrees left as it is, the partial row replaced.
RECOMPUTED = (
    "".join(located(day, n, n) + '"backfilled": true, ' + STAMP for day, n in (("2026-09-18", 1), ("2026-09-19", 2)))
    + located("2026-09-20", 3, 2)
    + STAMP
    + LATE.splitlines(keepends=True)[1]
    + located("2026-09-22", 3, 2)
    + STAMP
)
LATE_TODAY = located("2026-09-23", 3, 2) + STAMP

SCENARIOS = [
    Scenario(
        "regions_from_profile_locations",
        CALLS,
        responses(FROM_PROFILES, PROFILE_LOCATIONS),
        seed={FILE: '{"date": "2026-09-23", "total_unique_creators": 1}\n'},  # a rerun replaces it
    ),
    Scenario("regions_from_issues_without_a_location", CALLS, responses(FROM_ISSUES, ISSUE_PROFILES)),
    Scenario("pages_through_link_headers", CALLS, responses(MANY, MANY_PROFILES)),
    Scenario("a_repository_without_issues", CALLS, responses([], [])),
    Scenario(
        "a_failed_issues_page_writes_nothing",
        CALLS,
        failing_at(github_issue_pages(REPO, "all", MANY), "state=all&per_page=100&page=2", failed),
        seed={FILE: EARLIER},
    ),
    Scenario(
        "a_failed_profile_falls_back_to_issue_text",
        CALLS,
        responses(FAILED_PROFILE, [profile("oscar-user", "Zedland, Germany"), failed(profile_url("papa-user"))]),
        seed={FILE: EARLIER},
    ),
    Scenario("skips_without_a_token", CALLS, env={}),
    # leadline's own, which the script doesn't do: reviewed rows.
    Scenario(
        "regions_from_issue_form_answers",
        [],
        responses(ANSWERS, ANSWER_PROFILES),
        expected={FILE: json.dumps(ANSWERED_ROW)[:-1] + ", " + STAMP},
    ),
    Scenario(
        "regions_from_exact_answers_and_modems",
        [],
        responses(FORMS, FORM_PROFILES),
        expected={FILE: json.dumps(FORMED_ROW)[:-1] + ", " + STAMP},
    ),
    Scenario(
        "past_days_are_recomputed_back_to_the_first_persons_first_issue",
        [],
        responses(LATE_ISSUES, LATE_PROFILES),
        seed={FILE: LATE},
        expected={FILE: RECOMPUTED + LATE_TODAY},
    ),
]

# The scenarios where leadline's rows differ from the script's by a decision, and the decision.
DIVERGENCES = {
    "a_failed_issues_page_writes_nothing": "ADR: a failure is recorded as a failure",
    "a_failed_profile_falls_back_to_issue_text": "ADR: a partly failed row keeps its good parts",
}
