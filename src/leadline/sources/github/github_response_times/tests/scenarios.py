"""github_response_times: how long community issues wait for the maintainer's first reply, over GraphQL.

The script's collect_response_times REPO pages through every issue, oldest first, 100 a page by
cursor, each with its first 30 comments. Of the issues opened by anyone but MAINTAINER_LOGIN (and
not by a missing author), it counts those the maintainer answered and those never answered, and
writes one row for the run's date into github-response-times-<repo name>.jsonl: the median, mean,
90th percentile and fastest hours to the first reply, and the share answered within 1, 24 and 72
hours, a reply exactly on a bound counting as within it. Only the maintainer's comments count as a
reply; a comment with a missing author is passed over, and null comments are none. A failed page, or
GraphQL errors on one, writes nothing. The script reads the pages it saved in the text order of
their file names, so page_10 comes before page_2; every value in the row is a count or comes from
the sorted hours, so the order changes none (eleven_pages_read_in_text_order_give_the_same_row).

Where leadline differs by a decision, the scenario is in DIVERGENCES, and
tests/test_github_response_times.py asserts leadline's side: a failed page or GraphQL errors write a
failure row for the run's date (ADR: a failure is recorded as a failure); and a closed community issue
is answered at its closing, where that comes before the maintainer's first reply or there is none,
where the script counts only replies (ADR: a closed community issue is answered).
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from scenarios import Scenario
from scenarios.builders.common import Exchange, Json, exchange

from leadline.sources.github.tests.builders import GITHUB_GRAPHQL, github_issue_node, github_issue_nodes, graphql_body

MAINTAINER = "example-maintainer"
CONFIG = {"MAINTAINER_LOGIN": MAINTAINER}
CALLS = ['collect_response_times "solentlabs/example"']
FILE = "github-response-times-example.jsonl"
# The script's query, sent with the owner, name and cursor (null for the first page) as variables.
QUERY = (
    "query($o:String!,$n:String!,$c:String){repository(owner:$o,name:$n){issues(first:100,after:$c,"
    "orderBy:{field:CREATED_AT,direction:ASC}){pageInfo{hasNextPage endCursor} nodes{number createdAt closedAt "
    "author{login} comments(first:30){nodes{createdAt author{login}}}}}}}"
)
EARLIER = '{"date": "2026-09-22", "issues_scanned": 7}\n'
# leadline's config for these scenarios: the script's repo and maintainer, writing the script's file.
LEADLINE_CONFIG = [{"repo": "solentlabs/example", "maintainer": MAINTAINER}]

# The scenarios where leadline's rows differ from the script's by a decision, and the decision.
DIVERGENCES = {
    "graphql_errors_write_nothing": "ADR: a failure is recorded as a failure",
    "a_failed_later_page_writes_nothing": "ADR: a failure is recorded as a failure",
    "a_closed_issue_is_answered_at_its_closing": "ADR: a closed community issue is answered",
}


def page(nodes: Sequence[Json], cursor: str | None = None, *, next_page: bool = False, status: int = 200) -> Exchange:
    """The page after `cursor` (the first for None), and its answer."""
    sent = graphql_body(QUERY, {"o": "solentlabs", "n": "example", "c": cursor})
    body: Any = github_issue_nodes(nodes, next_page=next_page) if status == 200 else ""
    return exchange(GITHUB_GRAPHQL, body, method="POST", sent=sent, status=status)


def cursor_after(earlier: Exchange) -> str:
    """The cursor the script sends for the page after `earlier`."""
    cursor: str = json.loads(earlier["response"]["body"])["data"]["repository"]["issues"]["pageInfo"]["endCursor"]
    return cursor


def issue(number: int, author: str, replies: Sequence[tuple[str, str]] = (), closed: str | None = None) -> Json:
    """An issue opened on 2026-09-01 at 00:00, with (time, login) comments, closed at `closed` (None: open)."""
    return github_issue_node(number, "2026-09-01T00:00:00Z", author=author, closed_at=closed, comments=replies)


def with_ghost_comment(node: Json) -> Json:
    """`node` with a first comment by a deleted account, before the others."""
    ghost = {"createdAt": "2026-09-01T00:01:00Z", "author": None}
    return {**node, "comments": {"nodes": [ghost, *node["comments"]["nodes"]]}}


ANSWERED = [
    issue(1, "example-user", [("2026-09-01T00:30:00Z", MAINTAINER)]),  # half an hour
    with_ghost_comment(
        issue(2, "another-user", [("2026-09-01T02:00:00Z", "example-user"), ("2026-09-01T05:00:00Z", MAINTAINER)])
    ),
    issue(3, "example-user", [("2026-09-02T06:00:00Z", MAINTAINER), ("2026-09-02T07:00:00Z", MAINTAINER)]),
    issue(4, "third-user", [("2026-09-05T04:00:00Z", MAINTAINER)]),  # a hundred hours
    issue(5, "example-user", [("2026-09-01T01:00:00Z", "another-user")]),  # never answered by the maintainer
    issue(6, MAINTAINER, [("2026-09-01T00:05:00Z", MAINTAINER)]),  # the maintainer's own: not counted
    {**issue(7, "ghost", [("2026-09-01T00:10:00Z", MAINTAINER)]), "author": None},  # a deleted account
    issue(8, "another-user"),
    {**issue(9, "third-user"), "comments": None},  # never answered
]
# Replies exactly 1, 24 and 72 hours after: each counts as within its bound, and the median of an odd
# number answered is the middle one.
ON_THE_BOUNDS = [
    issue(1, "example-user", [("2026-09-01T01:00:00Z", MAINTAINER)]),
    issue(2, "another-user", [("2026-09-02T00:00:00Z", MAINTAINER)]),
    issue(3, "third-user", [("2026-09-04T00:00:00Z", MAINTAINER)]),
]
# Community issues closed: with no reply from the maintainer, after it, before it, and with only the community's
# comments; one still open and never answered; and the maintainer's own, closed. The script counts only replies.
CLOSED = [
    issue(1, "example-user", closed="2026-09-01T02:00:00Z"),  # closed at 2 hours, no reply
    issue(2, "another-user", [("2026-09-01T05:00:00Z", MAINTAINER)], closed="2026-09-01T10:00:00Z"),  # reply first
    issue(3, "third-user", [("2026-09-02T06:00:00Z", MAINTAINER)], closed="2026-09-01T03:00:00Z"),  # closed first
    issue(4, "example-user"),  # open, never answered
    issue(5, MAINTAINER, closed="2026-09-01T01:00:00Z"),  # the maintainer's own: not counted
    issue(6, "another-user", [("2026-09-01T01:00:00Z", "example-user")], closed="2026-09-04T00:00:00Z"),  # 72 hours
]
FIRST = page(ANSWERED[:5], next_page=True)
SECOND = page(ANSWERED[5:], cursor_after(FIRST))


def pages_of_one(nodes: Sequence[Json]) -> list[Exchange]:
    """A page for each of `nodes`, each after the one before."""
    pages: list[Exchange] = []
    for n, node in enumerate(nodes):
        pages.append(page([node], cursor_after(pages[-1]) if pages else None, next_page=n < len(nodes) - 1))
    return pages


# Eleven pages of one issue each, answered after 25, 50, ... hours, the last never: read in the text
# order of their file names, page_10 and page_11 come before page_2.
ELEVEN = [issue(n, "example-user", [(f"2026-09-{1 + n:02d}T{n:02d}:00:00Z", MAINTAINER)]) for n in range(1, 11)]
ELEVEN.append(issue(11, "another-user"))

# The maintainer's own issue, then two community issues, one answered by a reply after 24 hours, one by its closing
# after 12; and a file whose rows begin on 2026-09-20, after them.
LATE_ISSUES = [
    github_issue_node(2, "2026-09-17T08:00:00Z", author=MAINTAINER),
    github_issue_node(
        1, "2026-09-18T10:00:00Z", author="example-user", comments=[("2026-09-19T10:00:00Z", MAINTAINER)]
    ),
    github_issue_node(3, "2026-09-19T12:00:00Z", author="another-user", closed_at="2026-09-20T00:00:00Z"),
]
TIMES = ("median", "mean", "p90", "fastest")


def times_row(
    day: str, scanned: int, answered: int, never: int, hours: tuple[Any, ...], within: tuple[Any, ...]
) -> str:
    """A row's text, its keys in the script's order, up to its `captured_at`."""
    timed = ", ".join(f'"{name}_hours": {value}' for name, value in zip(TIMES, hours))
    shares = ", ".join(f'"within_{bound}h_pct": {value}' for bound, value in zip((1, 24, 72), within))
    return (
        f'{{"date": "{day}", "issues_scanned": {scanned}, "community_issues": {answered + never}, '
        f'"answered": {answered}, "never_answered": {never}, {timed}, {shares}'
    )


STAMP = '"captured_at": "2026-09-23T23:31:05+00:00"}\n'
BOTH = (3, 2, 0, (18.0, 18.0, 24.0, 12.0), (0.0, 100.0, 100.0))  # both community issues answered, as of 2026-09-20
# The file's rows: 2026-09-20 written under the earlier rule, a reply only by comment, so the issue closed without
# one is never answered; 2026-09-21 as today's rules count it; 2026-09-22 a failed night.
LATE = (
    times_row("2026-09-20", 3, 1, 1, (24.0, 24.0, 24.0, 24.0), (0.0, 100.0, 100.0))
    + ', "captured_at": "2026-09-20T23:30:00+00:00"}\n'
    + times_row("2026-09-21", *BOTH)
    + ', "captured_at": "2026-09-21T23:30:00+00:00"}\n'
    + '{"date": "2026-09-22", "status": "fetch_failed", "http_code": "502", "reason": "issues request failed", '
    + '"captured_at": "2026-09-22T23:30:00+00:00"}\n'
)
# Every past day, back to the first community issue, as of the end of each, under today's rules (ADR: derived history
# follows today's rules): the days before the file's rows added, the earlier rule's row corrected in its place, the
# row that already agrees left as it is, and the failed night's row replaced.
RECOMPUTED = (
    times_row("2026-09-18", 2, 0, 1, ("null",) * 4, ("null",) * 3)
    + ', "backfilled": true, '
    + STAMP
    + times_row("2026-09-19", 3, 1, 1, (24.0, 24.0, 24.0, 24.0), (0.0, 100.0, 100.0))
    + ', "backfilled": true, '
    + STAMP
    + times_row("2026-09-20", *BOTH)
    + ", "
    + STAMP
    + LATE.splitlines(keepends=True)[1]
    + times_row("2026-09-22", *BOTH)
    + ", "
    + STAMP
)
LATE_TODAY = times_row("2026-09-23", *BOTH) + ", " + STAMP

SCENARIOS = [
    Scenario(
        "hours_to_the_maintainers_first_reply",
        CALLS,
        [page(ANSWERED)],
        config=CONFIG,
        seed={FILE: '{"date": "2026-09-23", "issues_scanned": 1}\n'},  # a rerun replaces the day's row
    ),
    Scenario("pages_through_cursors", CALLS, [FIRST, SECOND], config=CONFIG),
    Scenario("eleven_pages_read_in_text_order_give_the_same_row", CALLS, pages_of_one(ELEVEN), config=CONFIG),
    Scenario("replies_on_the_bounds_count_as_within", CALLS, [page(ON_THE_BOUNDS)], config=CONFIG),
    Scenario(
        "no_community_issues_give_no_times",
        CALLS,
        [page([issue(1, MAINTAINER), issue(2, MAINTAINER, [("2026-09-01T01:00:00Z", "example-user")])])],
        config=CONFIG,
    ),
    Scenario(
        "community_issues_none_answered_give_no_times",
        CALLS,
        [page([issue(1, "example-user"), issue(2, "another-user", [("2026-09-01T01:00:00Z", "example-user")])])],
        config=CONFIG,
    ),
    Scenario("an_empty_repository_gives_no_times", CALLS, [page([])], config=CONFIG),
    Scenario("a_closed_issue_is_answered_at_its_closing", CALLS, [page(CLOSED)], config=CONFIG),
    Scenario(
        "graphql_errors_write_nothing",
        CALLS,
        [
            exchange(
                GITHUB_GRAPHQL,
                {"data": None, "errors": [{"message": "Something went wrong"}]},
                method="POST",
                sent=FIRST["request"]["body"],
            )
        ],
        config=CONFIG,
        seed={FILE: EARLIER},
    ),
    Scenario(
        "a_failed_later_page_writes_nothing",
        CALLS,
        [FIRST, page([], cursor_after(FIRST), status=502)],
        config=CONFIG,
        seed={FILE: EARLIER},
    ),
    Scenario("skips_without_a_token", CALLS, env={}, config=CONFIG),
    # leadline's own, which the script doesn't do: reviewed rows.
    Scenario(
        "past_days_are_recomputed_back_to_the_first_community_issue",
        [],
        [page(LATE_ISSUES)],
        config=CONFIG,
        seed={FILE: LATE},
        expected={FILE: RECOMPUTED + LATE_TODAY},
    ),
]
