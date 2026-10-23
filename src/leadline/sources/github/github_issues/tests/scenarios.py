"""github_issues: a nightly snapshot of a repository's issues and pull requests, and its modem pipeline.

The script's collect_github_issues REPO [LABEL] reads the repository, then every page of its open
issues and of all its issues (following Link), and writes one row for the run's date into
github-issues-<repo name>.jsonl: counts, flow since the previous snapshot, who contributes, ages,
engagement, labels, triage, and, given a pipeline label, that label's requests (issues only, not pull
requests). The previous snapshot, for the flow, is the last row in the file dated before the run; a
row with no date counts as one. Any failed fetch, or a body that doesn't parse or a page that isn't
a list, writes nothing.

leadline's config for a scenario (leadline_config) comes from its calls and constants: the repo, the
maintainer where the script has one, and, where the script is given the pipeline label, a queue that
reproduces the script's pipeline section (its label, keys and body pattern) with the script's issue
text rules as the region table (QUEUE, REGIONS). Where leadline differs by a decision, the scenario is
in DIVERGENCES, and tests/test_github_issues.py asserts leadline's side: a failed request writes a
`fetch_failed` failure row, and an answer it can't use a `parse_failed` one (ADR: a failure is
recorded as a failure); and a past row without the totals gets them, rebuilt from each issue's dates,
before the flow reads it (ADR: past values a source can rebuild are filled, never changed), which a
scenario of leadline's own, with reviewed rows, proves. REGIONS also reads issue forms, which the script
doesn't and no script scenario's issue has: `queue_regions_from_issue_forms` has reviewed rows (ADR: an
answer in an issue form is the strongest region signal). A `kinds` setting, which the script doesn't have, gives
`opened_by_kind_is_filled_for_every_past_day` its reviewed rows (ADR: the issues rows count what was opened by kind).
The rows also hold `closed_not_planned_total` (issues) and `prs_merged_total` (contribution), which the script
drops: the scenarios that write a row from the script's are in DIVERGENCES for them (more data is captured than the
script did), and the reviewed rows of leadline's own scenarios hold them.
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Any, Dict, Mapping, Sequence

from scenarios import NOW, Scenario
from scenarios.builders.common import Exchange, exchange, failed, failing_at

from leadline.sources.github.tests.builders import (
    GITHUB_API,
    github_issue,
    github_issue_pages,
    github_milestone,
    github_numbered_pages,
    github_profile,
    github_repo,
    made_up_issues,
)

REPO = "solentlabs/example"
FILE = "github-issues-example.jsonl"
LABEL = "new modem"  # the pipeline label the script passes for its main repository
MAINTAINER = "example-maintainer"
CONFIG = {"MAINTAINER_LOGIN": MAINTAINER}
WITH_PIPELINE = [f'collect_github_issues "{REPO}" "{LABEL}"']
WITHOUT_PIPELINE = [f'collect_github_issues "{REPO}"']
REPO_URL = f"{GITHUB_API}/repos/{REPO}"


# The script's issue text rules for the regions the scenarios' texts name, in its order, each with
# some of its keywords: the region table leadline's queue is given.
REGIONS = {
    "text": [
        {"region": "UK", "keywords": ["virgin media", "sky broadband", "bt broadband"]},
        {"region": "NL", "keywords": ["ziggo", "kpn", "t-mobile netherlands"]},
        {"region": "US", "keywords": ["comcast", "cox cable", "charter", "spectrum", "optimum"]},
        {"region": "CA", "keywords": ["rogers", "shaw", "bell canada"]},
        {"region": "AU", "keywords": ["telstra", "tpg telecom"]},
    ],
    # leadline's own: a person's answers in an issue form, their provider and their modem, which the script doesn't
    # read; no scenario of the script's has one. An exact answer, and modems sold in one country only.
    "answer": r"^ISP: *(.*)$",
    "exact": [{"region": "UK", "answers": ["ex"]}],
    "model_answer": r"^Modem: *(.*)$",
    "models": [{"region": "DE", "keywords": ["mx6444"]}, {"region": "NL", "keywords": ["mx4236"]}],
}
# The queue that reproduces the script's pipeline section: its label, keys and body pattern.
QUEUE = {
    "label": LABEL,
    "key": "modem_pipeline",
    "items_key": "modems",
    "body_match": {
        "pattern": r"\.har\b|har[- _]?capture|har file",
        "flag_key": "has_har",
        "count_key": "with_har_data",
    },
}


def leadline_config(scenario: Scenario) -> list[dict[str, Any]]:
    """leadline's config for `scenario`, from the script's calls and constants: one entry for REPO."""
    entry: dict[str, Any] = {"repo": REPO}
    maintainer = scenario.config.get("MAINTAINER_LOGIN", "")
    if maintainer:
        entry["maintainer"] = maintainer
    if list(scenario.calls) == WITH_PIPELINE or scenario.name == QUEUE_FORMS:
        entry.update(regions=REGIONS, queue=QUEUE)
    if scenario.name in BY_KIND:
        entry["kinds"] = KINDS
    return [entry]


MORE_DATA = "more data is captured than the script did"

# The scenarios where leadline's rows differ from the script's by a decision, and the decision.
DIVERGENCES = {
    # leadline's rows also hold `closed_not_planned_total` and `prs_merged_total`, which the script's lack.
    "snapshot_with_modem_pipeline": MORE_DATA,
    "pages_through_link_headers_without_a_pipeline": MORE_DATA,
    "the_previous_snapshot_is_the_last_earlier_row_in_the_file": MORE_DATA,
    "an_undated_row_is_a_snapshot_a_day_back": MORE_DATA,
    "empty_repository": MORE_DATA,
    "null_user_and_body": MORE_DATA,
    "without_a_maintainer_everyone_is_community": MORE_DATA,
    "repo_not_found_writes_nothing": "ADR: a failure is recorded as a failure",
    "failed_open_page_writes_nothing": "ADR: a failure is recorded as a failure",
    "failed_later_page_writes_nothing": "ADR: a failure is recorded as a failure",
    "unparseable_repository_writes_nothing": "ADR: a failure is recorded as a failure",
    "a_page_not_a_list_writes_nothing": "ADR: a failure is recorded as a failure",
    "flow_since_the_previous_snapshot": "ADR: past values a source can rebuild are filled, never changed",
    "an_earlier_row_without_totals_gives_no_flow": "ADR: past values a source can rebuild are filled, never changed",
}


def flow_row(day: str, opened: int, closed: int) -> str:
    """An earlier snapshot, with just its cumulative totals."""
    return f'{{"date": "{day}", "issues": {{"opened_total": {opened}, "closed_total": {closed}}}}}\n'


YESTERDAY = flow_row("2026-09-22", 1, 0)

Issue = Dict[str, Any]


def responses(items: Sequence[Issue], stars: int = 12, forks: int = 3, watchers: int = 4) -> list[Exchange]:
    """The repository, then the pages of its open items and of all its items."""
    open_items = [i for i in items if i["state"] == "open"]
    return [
        exchange(REPO_URL, github_repo(REPO, stars=stars, forks=forks, watchers=watchers)),
        *github_issue_pages(REPO, "open", open_items),
        *github_issue_pages(REPO, "all", items),
    ]


def stargazers(stamps: Sequence[str]) -> list[Exchange]:
    """The repo's stargazers with their times (`starred_at`), as GitHub pages them 100 a page, oldest first."""
    items = [{"starred_at": stamp, "user": {"login": f"user-{n}"}} for n, stamp in enumerate(stamps)]
    return github_numbered_pages(REPO, "stargazers", "per_page=100", items, per_page=100)


def forks(stamps: Sequence[str], owners: Sequence[str] = ()) -> list[Exchange]:
    """The repo's forks, oldest first (`sort=oldest`), each with its creation time and its owner (`forker-N` unless
    `owners` names them), 100 a page."""
    logins = [owners[n] if n < len(owners) else f"forker-{n}" for n in range(len(stamps))]
    items = [
        {"created_at": stamp, "full_name": f"{login}/example", "owner": {"login": login}}
        for stamp, login in zip(stamps, logins)
    ]
    return github_numbered_pages(REPO, "forks", "sort=oldest&per_page=100", items, per_page=100)


def profiles(locations: Mapping[str, str | None]) -> list[Exchange]:
    """Each login's profile (`users/LOGIN`), with its location where it has one."""
    return [exchange(f"{GITHUB_API}/users/{login}", github_profile(login, where)) for login, where in locations.items()]


def answering(items: Sequence[Issue], url_end: str, body: Any) -> list[Exchange]:
    """The responses for `items`, but the request whose URL ends with `url_end` answers 200 with `body`.

    The script fetches everything before it reads any of it, so every request is still made.
    """
    return [
        exchange(e["request"]["url"], body) if e["request"]["url"].endswith(url_end) else e for e in responses(items)
    ]


def issue(number: int, **fields: Any) -> Issue:
    return github_issue(REPO, number, **fields)


PIPELINE = [
    issue(
        1,
        title="Support for the Example X1 modem on Comcast",
        author="example-user",
        created_at="2026-09-01T10:00:00Z",
        comments=6,
        labels=[LABEL],
        body="HAR file attached, captured with har-capture.",
    ),
    issue(
        2,
        title="Docs typo",
        author=MAINTAINER,
        created_at="2026-09-20T08:00:00Z",
        assignees=[MAINTAINER],
        milestone=github_milestone(REPO, 1),
        body=None,
    ),
    issue(3, title="Bump a dependency", author="dependabot[bot]", created_at="2026-09-21T08:00:00Z", pull_request=True),
    issue(
        4,
        title="Virgin Media Hub 5",
        author="another-user",
        created_at="2026-08-01T08:00:00Z",
        closed_at="2026-08-11T08:00:00Z",
        comments=2,
        labels=[LABEL],
    ),
    issue(
        5,
        title="Example Z9 modem",
        author="another-user",
        created_at="2026-09-10T12:00:00Z",
        labels=[LABEL],
        body="No capture yet.",
    ),
    issue(
        6,
        title="Example C3 cable modem",
        author="third-user",
        created_at="2026-09-15T09:30:00Z",
        comments=1,
        labels=[LABEL, "enhancement"],
        assignees=[MAINTAINER],
        body="On Ziggo; attached example.har from the status page.",
    ),
    issue(
        7,
        title="Refactor the parser",
        author=MAINTAINER,
        created_at="2026-09-02T08:00:00Z",
        closed_at="2026-09-03T08:00:00Z",
        pull_request=True,
    ),
    issue(8, title="Nightly workflow failed", author="github-actions", created_at="2026-09-22T01:00:00Z"),
    issue(
        9,
        title="Values drop to zero after a reboot",
        author="example-user",
        created_at="2026-07-04T16:00:00Z",
        comments=7,
        labels=["bug"],
    ),
    issue(
        10,
        title="Example M2 modem",
        author="example-user",
        created_at="2026-06-01T08:00:00Z",
        closed_at="2026-06-20T08:00:00Z",
        labels=[LABEL],
        body="Telstra customer; har file to follow.",
    ),
    issue(11, title="Bump a tool", author="dependabot", created_at="2026-09-21T09:00:00Z"),  # a bot without [bot]
    issue(  # a pull request with the pipeline label isn't a request
        12,
        title="Add the Example X1 modem",
        author="another-user",
        created_at="2026-09-12T08:00:00Z",
        labels=[LABEL],
        pull_request=True,
    ),
]
SMALL = [
    issue(1, author="example-user", created_at="2026-09-18T10:00:00Z", comments=2),
    issue(2, author="another-user", created_at="2026-09-19T10:00:00Z", closed_at="2026-09-22T10:00:00Z"),
    issue(3, author=MAINTAINER, created_at="2026-09-20T10:00:00Z", closed_at="2026-09-21T10:00:00Z"),
]
MANY = made_up_issues(
    random.Random(3),
    REPO,
    430,
    now=datetime.fromisoformat(NOW),
    authors=("example-maintainer", "example-user", "another-user", "third-user", "dependabot[bot]"),
)


# Past rows the script left without the totals (it began writing them later), and the same rows with the totals
# rebuilt from SMALL's dates as of each row's day, where a good row lacks them: the run fills them before it reads
# the previous snapshot. A total a row has, null included, and a failure row, stay as they are.
UNFILLED = (
    '{"date": "2026-09-17", "repo": {"stars": 10}, "issues": {"open": 0, "open_prs": 0, "velocity_24h": 0}}\n'
    '{"date": "2026-09-18", "issues": {"opened_total": 7, "closed_total": null}}\n'
    '{"date": "2026-09-19", "status": "fetch_failed", "http_code": "502", "reason": "all issues request failed"}\n'
    '{"date": "2026-09-20", "issues": {"open": 3, "opened_total": 3}}\n'
    '{"date": "2026-09-22", "repo": {"stars": 12}}\n'
)
FILLED = (
    '{"date": "2026-09-17", "repo": {"stars": 10}, '
    '"issues": {"open": 0, "open_prs": 0, "velocity_24h": 0, "opened_total": 0, "closed_total": 0}}\n'
    '{"date": "2026-09-18", "issues": {"opened_total": 7, "closed_total": null}}\n'
    '{"date": "2026-09-19", "status": "fetch_failed", "http_code": "502", "reason": "all issues request failed"}\n'
    '{"date": "2026-09-20", "issues": {"open": 3, "opened_total": 3, "closed_total": 0}}\n'
    '{"date": "2026-09-22", "repo": {"stars": 12}, "issues": {"opened_total": 3, "closed_total": 2}}\n'
)
# SMALL's row for the run date, a day after a snapshot with its totals filled: no flow since.
SMALL_TODAY = (
    '{"date": "2026-09-23", "repo": {"stars": 12, "forks": 3, "watchers": 4}, "issues": {"open": 1, "open_prs": 0, '
    '"opened_total": 3, "closed_total": 2, "closed_not_planned_total": 0, "since_prev": {"days": 1, '
    '"opened": 0, "closed": 0}}, "contribution": '
    '{"issues": {"maintainer": 1, "community": 2, "bot": 0, "community_authors": 2}, "prs": {"maintainer": 0, '
    '"community": 0, "bot": 0, "community_authors": 0}, "prs_total": 0, "prs_merged_total": 0}, '
    '"age": {"avg_days": 5, "median_days": 5, '
    '"oldest_days": 5, "newest_days": 5}, "engagement": {"total_comments": 2, "avg_comments": 2.0, '
    '"high_engagement": 0, "top_issues": [{"number": 1, "title": "An example issue", "comments": 2}]}, "labels": {}, '
    '"triage": {"assigned": 0, "unassigned": 1, "labeled": 0, "unlabeled": 1, "milestoned": 0}, '
    '"captured_at": "2026-09-23T23:31:05+00:00"}\n'
)

# leadline's own: the queue's regions from issue forms (ADR: an answer in an issue form is the strongest region
# signal). An exact answer gives the region though the modem and the title say others; a modem sold in one country
# gives it where no rule matches the answer; with neither, the issue text's, as the script reads it.
QUEUE_FORMS = "queue_regions_from_issue_forms"
FORMS = [
    issue(
        1,
        title="Example X1 modem on Comcast",
        author="example-user",
        created_at="2026-09-18T10:00:00Z",
        labels=[LABEL],
        body="ISP: EX\nModem: Example MX4236",
    ),
    issue(
        2,
        title="Example X2 modem",
        author="another-user",
        created_at="2026-09-19T10:00:00Z",
        labels=[LABEL],
        body="ISP: Example Fibre\nModem: Example MX6444",
    ),
    issue(
        3,
        title="Example X3 modem",
        author="third-user",
        created_at="2026-09-20T10:00:00Z",
        labels=[LABEL],
        body="ISP: Exodus\nModem: Example SB1\nOn Ziggo.",
    ),
]
FORMS_TODAY = (
    '{"date": "2026-09-23", "repo": {"stars": 12, "forks": 3, "watchers": 4}, "issues": {"open": 3, "open_prs": 0, '
    '"opened_total": 3, "closed_total": 0, "closed_not_planned_total": 0, "since_prev": {"days": 0, '
    '"opened": 0, "closed": 0}}, "contribution": '
    '{"issues": {"maintainer": 0, "community": 3, "bot": 0, "community_authors": 3}, "prs": {"maintainer": 0, '
    '"community": 0, "bot": 0, "community_authors": 0}, "prs_total": 0, "prs_merged_total": 0}, '
    '"age": {"avg_days": 4, "median_days": 4, '
    '"oldest_days": 5, "newest_days": 3}, "engagement": {"total_comments": 0, "avg_comments": 0.0, '
    '"high_engagement": 0, "top_issues": [{"number": 1, "title": "Example X1 modem on Comcast", "comments": 0}, '
    '{"number": 2, "title": "Example X2 modem", "comments": 0}, {"number": 3, "title": "Example X3 modem", '
    '"comments": 0}]}, "labels": {"new modem": {"open": 3, "opened_total": 3, "closed_total": 0}}, "triage": '
    '{"assigned": 0, "unassigned": 3, "labeled": 3, "unlabeled": 0, "milestoned": 0}, "modem_pipeline": '
    '{"total": 3, "avg_age_days": 4, "with_har_data": 0, "with_comments": 0, "regions": {"UK": 1, "DE": 1, "NL": 1}, '
    '"requested_total": 3, "resolved_total": 0, "median_days_to_resolve": 0, "requesters": {"maintainer": 0, '
    '"community": 3, "bot": 0, "community_authors": 3}, "modems": [{"number": 1, "title": '
    '"Example X1 modem on Comcast", "age_days": 5, "comments": 0, "has_har": false, "labels": ["new modem"], '
    '"region": "UK"}, {"number": 2, "title": "Example X2 modem", "age_days": 4, "comments": 0, "has_har": false, '
    '"labels": ["new modem"], "region": "DE"}, {"number": 3, "title": "Example X3 modem", "age_days": 3, '
    '"comments": 0, "has_har": false, "labels": ["new modem"], "region": "NL"}]}, '
    '"captured_at": "2026-09-23T23:31:05+00:00"}\n'
)

# leadline's own: each issue opened counted once under its kind (ADR: the issues rows count what was opened by
# kind), the kinds from config: the first kind in the list whose label the issue carries, else `other`. A pull
# request isn't an issue, whatever its labels. Past rows lacking the counts get them from each issue's creation date,
# as the totals were; a value a row has, null included, and a failure row stay, and the days before the file's first
# row are rebuilt with them. The pull request, opened on the 19th, is still open.
KINDS = [{"name": "new_modem", "label": "new modem"}, {"name": "defect", "label": "bug"}]
BY_KIND = ("opened_by_kind_is_filled_for_every_past_day",)
KIND_ITEMS = [
    issue(1, author="example-user", created_at="2026-09-18T10:00:00Z", labels=["bug", "docs"]),
    issue(
        2,
        author="another-user",
        created_at="2026-09-19T10:00:00Z",
        closed_at="2026-09-22T10:00:00Z",
        labels=["bug", LABEL],
    ),
    issue(3, author=MAINTAINER, created_at="2026-09-20T10:00:00Z", closed_at="2026-09-21T10:00:00Z", labels=["docs"]),
    issue(4, author="example-user", created_at="2026-09-19T12:00:00Z", labels=["bug"], pull_request=True),
]
KIND_SEED = (
    '{"date": "2026-09-20", "issues": {"opened_total": 3, "closed_total": 0}}\n'
    '{"date": "2026-09-21", "issues": {"opened_total": 3, "closed_total": 1, "opened_by_kind": null}}\n'
    '{"date": "2026-09-22", "status": "fetch_failed", "http_code": "502", "reason": "all issues request failed"}\n'
)
KIND_STAMP = '"backfilled": true, "captured_at": "2026-09-23T23:31:05+00:00"}\n'
KIND_ROWS = (
    '{"date": "2026-09-18", "issues": {"open": 1, "open_prs": 0, "opened_total": 1, "closed_total": 0, '
    '"opened_by_kind": {"new_modem": 0, "defect": 1, "other": 0}, '
    '"open_by_kind": {"new_modem": 0, "defect": 1, "other": 0}}, '
    + KIND_STAMP
    + '{"date": "2026-09-19", "issues": {"open": 2, "open_prs": 1, "opened_total": 2, "closed_total": 0, '
    '"opened_by_kind": {"new_modem": 1, "defect": 1, "other": 0}, '
    '"open_by_kind": {"new_modem": 1, "defect": 1, "other": 0}}, '
    + KIND_STAMP
    + '{"date": "2026-09-20", "issues": {"opened_total": 3, "closed_total": 0, '
    '"opened_by_kind": {"new_modem": 1, "defect": 1, "other": 1}}}\n'
    '{"date": "2026-09-21", "issues": {"opened_total": 3, "closed_total": 1, "opened_by_kind": null}}\n'
    '{"date": "2026-09-22", "status": "fetch_failed", "http_code": "502", "reason": "all issues request failed"}\n'
)
# The run date's row, with the day's open issues by kind: one open issue and the open pull request; the previous
# snapshot is the 21st's (3 opened, 1 closed), so two days of flow with one issue closed.
KIND_TODAY = (
    '{"date": "2026-09-23", "repo": {"stars": 12, "forks": 3, "watchers": 4}, "issues": {"open": 1, "open_prs": 1, '
    '"opened_total": 3, "closed_total": 2, "closed_not_planned_total": 0, "since_prev": {"days": 2, '
    '"opened": 0, "closed": 1}, '
    '"opened_by_kind": {"new_modem": 1, "defect": 1, "other": 1}, "open_by_kind": {"new_modem": 0, "defect": 1, '
    '"other": 0}}, "contribution": '
    '{"issues": {"maintainer": 1, "community": 2, "bot": 0, "community_authors": 2}, "prs": {"maintainer": 0, '
    '"community": 1, "bot": 0, "community_authors": 1}, "prs_total": 1, "prs_merged_total": 0}, '
    '"age": {"avg_days": 5, "median_days": 5, '
    '"oldest_days": 5, "newest_days": 5}, "engagement": {"total_comments": 0, "avg_comments": 0.0, '
    '"high_engagement": 0, "top_issues": [{"number": 1, "title": "An example issue", "comments": 0}]}, '
    '"labels": {"bug": {"open": 1, "opened_total": 2, "closed_total": 1}, "docs": {"open": 1, "opened_total": 2, '
    '"closed_total": 1}, "new modem": {"open": 0, "opened_total": 1, "closed_total": 1}}, '
    '"triage": {"assigned": 0, "unassigned": 1, "labeled": 1, "unlabeled": 0, "milestoned": 0}, '
    '"captured_at": "2026-09-23T23:31:05+00:00"}\n'
)

# SMALL and an open pull request, and a file whose rows begin on 2026-09-21, after the first issue.
LATE_ITEMS = [*SMALL, issue(4, author="example-user", created_at="2026-09-19T12:00:00Z", pull_request=True)]
LATE = flow_row("2026-09-21", 3, 1) + flow_row("2026-09-22", 3, 2)


def rebuilt(open_prs: tuple[int, int, int]) -> str:
    """The days before a file whose rows begin on 2026-09-21, back to SMALL's first issue, rebuilt from each issue's and
    pull request's dates (ADR: past values a source can rebuild are filled, never changed): the open issues and pull
    requests (`open_prs`, each day's) at the end of each day and the issues opened and closed by then."""
    return "".join(
        f'{{"date": "2026-09-{day}", "issues": {{"open": {n}, "open_prs": {prs}, "opened_total": {n}, '
        '"closed_total": 0}, "backfilled": true, "captured_at": "2026-09-23T23:31:05+00:00"}\n'
        for day, n, prs in zip((18, 19, 20), (1, 2, 3), open_prs)
    )


REBUILT = rebuilt((0, 1, 1))
LATE_TODAY = SMALL_TODAY.replace('"open_prs": 0', '"open_prs": 1').replace(
    '"prs": {"maintainer": 0, "community": 0, "bot": 0, "community_authors": 0}, "prs_total": 0',
    '"prs": {"maintainer": 0, "community": 1, "bot": 0, "community_authors": 1}, "prs_total": 1',
)

SCENARIOS = [
    Scenario("snapshot_with_modem_pipeline", WITH_PIPELINE, responses(PIPELINE), config=CONFIG),
    Scenario("pages_through_link_headers_without_a_pipeline", WITHOUT_PIPELINE, responses(MANY), config=CONFIG),
    Scenario(
        "flow_since_the_previous_snapshot",
        WITHOUT_PIPELINE,
        responses(SMALL),
        config=CONFIG,
        seed={
            FILE: "not json\n"
            '{"date": "2026-09-21", "issues": {"opened_total": 1}}\n'
            '{"date": "2026-09-23", "issues": {"opened_total": 99, "closed_total": 99}}\n'
        },
    ),
    Scenario(
        # The previous snapshot is the last row in the file dated before the run, not the latest date.
        "the_previous_snapshot_is_the_last_earlier_row_in_the_file",
        WITHOUT_PIPELINE,
        responses(SMALL),
        config=CONFIG,
        seed={FILE: flow_row("2026-09-21", 2, 1) + flow_row("2026-09-18", 0, 0)},
    ),
    Scenario(
        # The last earlier row has no totals, so there is no flow, though the row before it has them.
        "an_earlier_row_without_totals_gives_no_flow",
        WITHOUT_PIPELINE,
        responses(SMALL),
        config=CONFIG,
        seed={FILE: flow_row("2026-09-21", 1, 0) + '{"date": "2026-09-22", "repo": {"stars": 12}}\n'},
    ),
    Scenario(
        # A row with no date sorts before every date, so it is an earlier row, a day back.
        "an_undated_row_is_a_snapshot_a_day_back",
        WITHOUT_PIPELINE,
        responses(SMALL),
        config=CONFIG,
        seed={FILE: flow_row("2026-09-18", 1, 0) + '{"issues": {"opened_total": 2, "closed_total": 1}}\n'},
    ),
    Scenario("empty_repository", WITH_PIPELINE, responses([], stars=0, forks=0, watchers=0), config=CONFIG),
    Scenario(
        "null_user_and_body",
        WITH_PIPELINE,
        responses(
            [
                issue(1, author=None, labels=[LABEL], body=None, title="Example modem on Rogers"),
                issue(2, author=None, pull_request=True, closed_at="2026-09-05T00:00:00Z"),
            ]
        ),
        config=CONFIG,
    ),
    Scenario(
        "without_a_maintainer_everyone_is_community",
        WITHOUT_PIPELINE,
        responses(SMALL),
        config={"MAINTAINER_LOGIN": ""},
    ),
    Scenario("skips_without_a_token", WITH_PIPELINE, [], env={}, config=CONFIG),
    Scenario(
        "repo_not_found_writes_nothing",
        WITH_PIPELINE,
        [exchange(REPO_URL, '{"message":"Not Found","status":"404"}', status=404)],
        config=CONFIG,
        seed={FILE: YESTERDAY},
    ),
    Scenario(
        "failed_open_page_writes_nothing",
        WITH_PIPELINE,
        failing_at(responses(SMALL), "state=open&per_page=100&page=1", lambda url: exchange(url, "", status=500)),
        config=CONFIG,
        seed={FILE: YESTERDAY},
    ),
    Scenario(
        "failed_later_page_writes_nothing",
        WITHOUT_PIPELINE,
        failing_at(responses(MANY), "state=all&per_page=100&page=3", failed),
        config=CONFIG,
        seed={FILE: YESTERDAY},
    ),
    Scenario(
        "unparseable_repository_writes_nothing",
        WITH_PIPELINE,
        answering(SMALL, REPO_URL, "<html>maintenance</html>"),
        config=CONFIG,
        seed={FILE: YESTERDAY},
    ),
    Scenario(
        "a_page_not_a_list_writes_nothing",
        WITH_PIPELINE,
        answering(SMALL, "state=open&per_page=100&page=1", {"message": "Not a list"}),
        config=CONFIG,
        seed={FILE: YESTERDAY},
    ),
    # leadline's own, which the script doesn't do: reviewed rows (ADR: past values a source can rebuild are filled,
    # never changed).
    Scenario(
        "past_rows_without_totals_are_filled_from_each_issues_dates",
        [],
        responses(SMALL),
        config=CONFIG,
        seed={FILE: UNFILLED},
        expected={FILE: FILLED + SMALL_TODAY},
    ),
    Scenario(
        QUEUE_FORMS,
        [],
        responses(FORMS),
        config=CONFIG,
        expected={FILE: FORMS_TODAY},
    ),
    Scenario(
        BY_KIND[0],
        [],
        responses(KIND_ITEMS),
        config=CONFIG,
        seed={FILE: KIND_SEED},
        expected={FILE: KIND_ROWS + KIND_TODAY},
    ),
    Scenario(
        "a_file_starting_late_gets_rows_back_to_the_first_issue",
        [],
        responses(LATE_ITEMS),
        config=CONFIG,
        seed={FILE: LATE},
        expected={FILE: REBUILT + LATE + LATE_TODAY},
    ),
]
