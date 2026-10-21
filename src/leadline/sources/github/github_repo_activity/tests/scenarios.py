"""github_repo_activity: one public repository's activity over the week before the run.

The script's collector for it (REPO LABEL) reads the repository, every page of its commits since a
week before the run (following Link), its issues since then, its top ten contributors, its five
latest releases and its languages, and writes one row for the run's date into its file for the
label: stars, forks, open issues, code size; the week's commits, authors, fixes and redesigns (by
the first line of each message); the week's issues and those opened by others than the owner; the
top contributor's share; and the latest release (no date when it isn't published). A failed
issues, contributors, releases or languages fetch counts as none; a failed repository or commits
fetch writes nothing, and so does any fetch that answers with an empty or unparseable body, or an
issue whose user is null. A rerun replaces the row whose own date is the run's, not one whose
latest release is dated so.

Where leadline differs by a decision, the scenario is in DIVERGENCES, and
tests/test_github_repo_activity.py asserts leadline's side: a failed repository or commits fetch
writes a failure row (`fetch_failed`) for the run's date (ADR: a failure is recorded as a failure);
and an optional list that fails, answers with an empty or unparseable body, or holds an item it
can't read (an issue whose user is null) is null in a partial row naming what failed, never none
and never a lost row (ADR: a partly failed row keeps its good parts).

The scenarios name neither the collector nor its file as the script does: CALLS finds the collector
by its shape (the one whose rows carry `velocity_7d`), and its row file, named after the collector
(`<name>-<label>.jsonl`), gets this source's default name (FILE) before the run (for the seed) and
after it (for the golden).
"""

from __future__ import annotations

import random
from typing import Sequence

from scenarios import Scenario
from scenarios.builders.common import Exchange, Json, exchange, failed, failing_at

from leadline.sources.github.tests.builders import (
    GITHUB_API,
    github_commit,
    github_contributor,
    github_issue,
    github_numbered_pages,
    github_release,
    github_repo,
)

PROJECT = "example-org/example-project"
LABEL = "example"
FILE = f"github-repo-activity-{LABEL}.jsonl"
SCRIPT_FILE = f'"${{METRICS_DIR}}/${{fn#collect_}}-{LABEL}.jsonl"'  # the collector's own name for the file
NEUTRAL_FILE = f'"${{METRICS_DIR}}/{FILE}"'
CALLS = [
    'fn=$(for f in $(declare -F | cut -d" " -f3); '
    'do if [[ $(declare -f "$f") == *velocity_7d* ]]; then echo "$f"; fi; done) && [[ -n "$fn" ]] || exit 3',
    f"[[ ! -f {NEUTRAL_FILE} ]] || mv {NEUTRAL_FILE} {SCRIPT_FILE}",
    f'"$fn" "{PROJECT}" "{LABEL}"',
    f"[[ ! -f {SCRIPT_FILE} ]] || mv {SCRIPT_FILE} {NEUTRAL_FILE}",
]
SINCE = "2026-09-16T00:00:00Z"  # a week before the run
REPO_URL = f"{GITHUB_API}/repos/{PROJECT}"
ISSUES_URL = f"{REPO_URL}/issues?state=all&since={SINCE}&per_page=100"
CONTRIBUTORS_URL = f"{REPO_URL}/contributors?per_page=10"
RELEASES_URL = f"{REPO_URL}/releases?per_page=5"
LANGUAGES_URL = f"{REPO_URL}/languages"
REPOSITORY = exchange(REPO_URL, github_repo(PROJECT, stars=900, forks=40, open_issues=12))
# leadline's config for these scenarios: the script's repo and label, whose default file is FILE.
LEADLINE_CONFIG = [{"repo": PROJECT, "label": LABEL}]

# The scenarios where leadline's rows differ from the script's by a decision, and the decision.
DIVERGENCES = {
    "an_issue_with_a_null_user_writes_nothing": "ADR: a partly failed row keeps its good parts",
    "an_empty_list_body_writes_nothing": "ADR: a partly failed row keeps its good parts",
    "an_unparseable_list_body_writes_nothing": "ADR: a partly failed row keeps its good parts",
    "failed_optional_lists_count_as_none": "ADR: a partly failed row keeps its good parts",
    "a_failed_commits_page_writes_nothing": "ADR: a failure is recorded as a failure",
    "failed_repository_writes_nothing": "ADR: a failure is recorded as a failure",
}
EARLIER = '{"date": "2026-09-22", "label": "example", "repo": "example-org/example-project", "stars": 890}\n'


def commits(messages: Sequence[tuple[str, str | None]]) -> list[Exchange]:
    """Every page of the week's commits: (message, author login or None), newest first."""
    items = [
        github_commit(PROJECT, message, author=author, when=f"2026-09-{22 - i % 6:02d}T{i % 24:02d}:00:00Z")
        for i, (message, author) in enumerate(messages)
    ]
    return github_numbered_pages(PROJECT, "commits", f"since={SINCE}&per_page=100", items, per_page=100)


def rest(issues: Sequence[Json], contributors: Sequence[Json], releases: Sequence[Json]) -> list[Exchange]:
    """The issues, contributors, releases and languages the script asks for after the commits."""
    return [
        exchange(ISSUES_URL, list(issues)),
        *github_numbered_pages(PROJECT, "contributors", "per_page=10", contributors, per_page=10, numbered=False),
        *github_numbered_pages(PROJECT, "releases", "per_page=5", releases, per_page=5, numbered=False),
        exchange(LANGUAGES_URL, {"Python": 120000, "JavaScript": 40000, "Dockerfile": 900}),
    ]


def answering(responses: Sequence[Exchange], url: str, body: str, status: int = 200) -> list[Exchange]:
    """`responses`, but the request for `url` answers with `body`. The script reads nothing before it asks for all."""
    return [exchange(url, body, status=status) if e["request"]["url"] == url else e for e in responses]


WEEK = [
    ("Fix a crash on start", "project-dev"),
    ("fix: typo in the docs", "project-dev"),
    ("Debug output for the parser", "helper-dev"),  # "bug" anywhere counts as a fix
    ("Prefix the log lines", "project-dev"),  # "fix" only as a word
    ('Revert "Add caching"', "project-dev"),
    ("Polish the dashboard\n\nAlso fix the tests", None),  # only the first line counts; no GitHub account
    ("Refactor the poller", "helper-dev"),
    ("Add a modem", "project-dev"),
]
ISSUES = [
    github_issue(PROJECT, 101, author="example-org"),  # opened by the owner
    github_issue(PROJECT, 102, author="example-user"),
    github_issue(PROJECT, 103, author="another-user", closed_at="2026-09-20T10:00:00Z"),
    github_issue(PROJECT, 104, author="project-dev", pull_request=True),
]
CONTRIBUTORS = [github_contributor(f"contributor-{n:02d}", 400 // n) for n in range(1, 13)]
RELEASES = [github_release(PROJECT, f"v3.{9 - n}.0", published_at=f"2026-09-{20 - n:02d}T09:00:00Z") for n in range(7)]
LATER = '{"date": "2026-09-24", "label": "example", "latest_release": {"tag": "v3.9.0", "date": "2026-09-23"}}\n'
MANY = [(f"Change number {n}", random.Random(n).choice(["project-dev", "helper-dev"])) for n in range(130)]

SCENARIOS = [
    Scenario(
        "weekly_snapshot",
        CALLS,
        [REPOSITORY, *commits(WEEK), *rest(ISSUES, CONTRIBUTORS, RELEASES)],
        # A rerun replaces the day's row; a later row whose latest release is dated the run's day stays.
        seed={FILE: EARLIER + '{"date": "2026-09-23", "label": "example", "stars": 1}\n' + LATER},
    ),
    Scenario("pages_through_the_commits", CALLS, [REPOSITORY, *commits(MANY), *rest(ISSUES, CONTRIBUTORS, RELEASES)]),
    Scenario(
        "an_unpublished_latest_release_has_no_date",
        CALLS,
        [
            REPOSITORY,
            *commits(WEEK),
            *rest(ISSUES, CONTRIBUTORS, [github_release(PROJECT, "v4.0.0", published_at=None, draft=True)]),
        ],
    ),
    Scenario(
        "an_issue_with_a_null_user_writes_nothing",
        CALLS,
        [REPOSITORY, *commits(WEEK), *rest([*ISSUES, github_issue(PROJECT, 105, author=None)], CONTRIBUTORS, RELEASES)],
        seed={FILE: EARLIER},
    ),
    Scenario(
        "an_empty_list_body_writes_nothing",
        CALLS,
        [REPOSITORY, *commits(WEEK), *answering(rest(ISSUES, CONTRIBUTORS, RELEASES), ISSUES_URL, "", status=204)],
        seed={FILE: EARLIER},
    ),
    Scenario(
        "an_unparseable_list_body_writes_nothing",
        CALLS,
        [
            REPOSITORY,
            *commits(WEEK),
            *answering(rest(ISSUES, CONTRIBUTORS, RELEASES), LANGUAGES_URL, "<html>Oops</html>"),
        ],
        seed={FILE: EARLIER},
    ),
    Scenario(
        "failed_optional_lists_count_as_none",
        CALLS,
        [
            REPOSITORY,
            *commits([]),
            exchange(ISSUES_URL, "", status=500),
            exchange(CONTRIBUTORS_URL, "", status=500),
            exchange(RELEASES_URL, '{"message":"Not Found"}', status=404),
            failed(LANGUAGES_URL),
        ],
    ),
    Scenario(
        "a_failed_commits_page_writes_nothing",
        CALLS,
        [REPOSITORY, *failing_at(commits(MANY), "page=2", lambda url: exchange(url, "", status=502))],
        seed={FILE: EARLIER},
    ),
    Scenario("failed_repository_writes_nothing", CALLS, [failed(REPO_URL)], seed={FILE: EARLIER}),
    Scenario("skips_without_a_token", CALLS, env={}),
]
