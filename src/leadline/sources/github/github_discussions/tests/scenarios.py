"""github_discussions: a repository's discussions, over GraphQL.

The script's collect_discussions REPO asks for the 100 most recently updated discussions and writes
one row for the run's date into github-discussions-<repo name>.jsonl: the total; how many the
fetched ones were opened by the community and by MAINTAINER_LOGIN; how many community authors, how
many answered, and their comments; the count per category; and the ten most recent. A missing
author reads as "unknown", a missing category as "uncategorised", and a category with a null name
is counted under "null"; null nodes are none, and a missing total is zero. An empty MAINTAINER_LOGIN
makes every author the community. A failed fetch, a body that doesn't parse, a result with GraphQL
errors, or a null repository writes nothing.

Where leadline differs by a decision, the scenario is in DIVERGENCES, and
tests/test_github_discussions.py asserts leadline's side: a failed request, GraphQL errors, a null
repository or a body that doesn't parse writes a failure row for the run's date (ADR: a failure is
recorded as a failure); and an empty maintainer is a config error, as an entry's maintainer is
required (docs/CONFIGURATION.md, "Source entries"). The days before the file's first row, rebuilt back
to the first discussion, are leadline's own (ADR: past values a source can rebuild are filled, never
changed): its scenarios carry reviewed rows.
"""

from __future__ import annotations

from typing import Any

from scenarios import Scenario
from scenarios.builders.common import Exchange, exchange

from leadline.sources.github.tests.builders import GITHUB_GRAPHQL, github_discussion, github_discussions, graphql_body

MAINTAINER = "example-maintainer"
CONFIG = {"MAINTAINER_LOGIN": MAINTAINER}
CALLS = ['collect_discussions "solentlabs/example"']
FILE = "github-discussions-example.jsonl"
# The script's query, sent with the owner and name as variables.
QUERY = (
    "query($o:String!,$n:String!){repository(owner:$o,name:$n){discussions(first:100,"
    "orderBy:{field:UPDATED_AT,direction:DESC}){totalCount nodes{number title createdAt updatedAt isAnswered "
    "upvoteCount comments{totalCount} author{login} category{name}}}}}"
)
SENT = graphql_body(QUERY, {"o": "solentlabs", "n": "example"})
# leadline's config for these scenarios: the script's repo and maintainer, writing the script's file.
LEADLINE_CONFIG = [{"repo": "solentlabs/example", "maintainer": MAINTAINER}]

# The scenarios where leadline's rows differ from the script's by a decision, and the decision.
DIVERGENCES = {
    "an_empty_maintainer_login_makes_everyone_community": "CONFIGURATION: an entry's maintainer is required",
    "graphql_errors_write_nothing": "ADR: a failure is recorded as a failure",
    "a_null_repository_writes_nothing": "ADR: a failure is recorded as a failure",
    "unparseable_body_writes_nothing": "ADR: a failure is recorded as a failure",
    "failed_fetch_writes_nothing": "ADR: a failure is recorded as a failure",
}


def answer(body: Any, status: int = 200) -> Exchange:
    return exchange(GITHUB_GRAPHQL, body, method="POST", sent=SENT, status=status)


AUTHORS = [MAINTAINER, "example-user", "another-user", "example-user"]
TWELVE = [
    github_discussion(
        n,
        author=AUTHORS[n % 4],
        category=("Q&A", "Ideas", "Show and tell")[n % 3],
        comments=n % 5,
        upvotes=n % 3,
        answered=(n % 2 == 0) if n % 3 == 0 else None,
        updated_at=f"2026-09-{23 - n:02d}T10:00:00Z",
    )
    for n in range(1, 13)
]
ODD = {
    "data": {
        "repository": {
            "discussions": {
                "totalCount": 4,
                "nodes": [
                    {"number": 1, "title": "A ghost's question", "author": None, "category": None, "comments": None},
                    {"number": 2, "title": "No login", "author": {"login": None}, "category": {"name": "Q&A"}},
                    {**github_discussion(3), "isAnswered": True},
                    {**github_discussion(4), "category": {"name": None}},  # counted under "null"
                ],
            }
        }
    }
}

EARLIER = {FILE: '{"date": "2026-09-22", "total": 9}\n'}


def opened(number: int, created_at: str, updated_at: str, **fields: Any) -> dict[str, Any]:
    return {**github_discussion(number, updated_at=updated_at, **fields), "createdAt": created_at}


# Four discussions, the first opened on 2026-09-18 by the maintainer, most recently updated first; and a file whose
# rows begin on 2026-09-21, after them.
FOUR = [
    opened(4, "2026-09-23T07:00:00Z", "2026-09-23T08:00:00Z", category="Ideas"),
    opened(3, "2026-09-21T08:00:00Z", "2026-09-22T08:00:00Z", author="another-user"),
    opened(2, "2026-09-19T12:00:00Z", "2026-09-21T08:00:00Z", comments=2, answered=True),
    opened(1, "2026-09-18T09:00:00Z", "2026-09-20T08:00:00Z", author=MAINTAINER, category="Announcements"),
]
LATE = '{"date": "2026-09-21", "total": 3, "fetched": 3}\n{"date": "2026-09-22", "total": 3, "fetched": 3}\n'
# The query for the comments' creation, sent only when a day to rebuild lacks its comments.
COMMENTS_QUERY = (
    "query($o:String!,$n:String!){repository(owner:$o,name:$n){discussions(first:100,"
    "orderBy:{field:UPDATED_AT,direction:DESC}){totalCount nodes{number comments(first:100){totalCount "
    "nodes{createdAt}}}}}}"
)


def comments_answer(created: dict[int, list[str]], read: dict[int, int] | None = None) -> Exchange:
    """The comments query's answer: each discussion's comments' creation; `read` gives a total beyond those read."""
    nodes = [
        {
            "number": n,
            "comments": {"totalCount": (read or {}).get(n, len(times)), "nodes": [{"createdAt": t} for t in times]},
        }
        for n, times in created.items()
    ]
    body = {"data": {"repository": {"discussions": {"totalCount": len(nodes), "nodes": nodes}}}}
    return exchange(
        GITHUB_GRAPHQL, body, method="POST", sent=graphql_body(COMMENTS_QUERY, {"o": "solentlabs", "n": "example"})
    )


# FOUR's comments: discussion 2's two, on the 19th and the 20th.
FOUR_COMMENTS = {4: [], 3: [], 2: ["2026-09-19T15:00:00Z", "2026-09-20T09:00:00Z"], 1: []}


def rebuilt(comments: tuple[str, ...]) -> str:
    """The days before the file's rows, back to the first discussion, rebuilt from the discussions' creation (ADR:
    past values a source can rebuild are filled, never changed): what each day's discussions were, by whom and in
    which category, and their comments by then (`comments`, each day's); null for what they don't tell (how many a
    query fetched, answers, the recent ones)."""
    return "".join(
        f'{{"date": "{day}", "total": {total}, "fetched": null, "community_opened": {community}, '
        f'"maintainer_opened": 1, "community_authors": {community}, "answered": null, "total_comments": {comments}, '
        f'"categories": {categories}, "recent": null, "backfilled": true, '
        '"captured_at": "2026-09-23T23:31:05+00:00"}\n'
        for (day, total, community, categories), comments in zip(
            [
                ("2026-09-18", 1, 0, '{"Announcements": 1}'),
                ("2026-09-19", 2, 1, '{"Q&A": 1, "Announcements": 1}'),
                ("2026-09-20", 2, 1, '{"Q&A": 1, "Announcements": 1}'),
            ],
            comments,
        )
    )


REBUILT = rebuilt(("0", "1", "2"))
# The rows an earlier rebuild wrote, which couldn't tell the comments.
WITHOUT_COMMENTS = rebuilt(("null", "null", "null"))


def four_today(total: int) -> str:
    """FOUR's row for the run date, `total` discussions in all."""
    recent = [
        (4, "Ideas", "example-user", 0, "null", "2026-09-23"),
        (3, "Q&A", "another-user", 0, "null", "2026-09-22"),
        (2, "Q&A", "example-user", 2, "true", "2026-09-21"),
        (1, "Announcements", MAINTAINER, 0, "null", "2026-09-20"),
    ]
    listed = ", ".join(
        f'{{"number": {n}, "title": "Example discussion {n}", "category": "{category}", "author": "{author}", '
        f'"comments": {comments}, "upvotes": 0, "is_answered": {answered}, "updated_at": "{updated}"}}'
        for n, category, author, comments, answered, updated in recent
    )
    return (
        f'{{"date": "2026-09-23", "total": {total}, "fetched": 4, "community_opened": 3, "maintainer_opened": 1, '
        '"community_authors": 2, "answered": 1, "total_comments": 2, '
        f'"categories": {{"Q&A": 2, "Ideas": 1, "Announcements": 1}}, "recent": [{listed}], '
        '"captured_at": "2026-09-23T23:31:05+00:00"}\n'
    )


SCENARIOS = [
    Scenario("who_opened_what_and_what_was_answered", CALLS, [answer(github_discussions(TWELVE, 30))], config=CONFIG),
    Scenario(
        "an_empty_maintainer_login_makes_everyone_community",
        CALLS,
        [answer(github_discussions(TWELVE, 30))],
        config={"MAINTAINER_LOGIN": ""},
    ),
    Scenario("missing_authors_categories_and_counts", CALLS, [answer(ODD)], config=CONFIG),
    Scenario("no_discussions", CALLS, [answer(github_discussions([]))], config=CONFIG),
    Scenario(
        "null_nodes_and_a_missing_total_count_as_zero",
        CALLS,
        [answer({"data": {"repository": {"discussions": {"nodes": None}}}})],
        config=CONFIG,
    ),
    Scenario(
        "graphql_errors_write_nothing",
        CALLS,
        [answer({"data": None, "errors": [{"type": "NOT_FOUND", "message": "Could not resolve to a Repository"}]})],
        config=CONFIG,
        seed=EARLIER,
    ),
    Scenario(
        "a_null_repository_writes_nothing",
        CALLS,
        [answer({"data": {"repository": None}})],
        config=CONFIG,
        seed=EARLIER,
    ),
    Scenario(
        "unparseable_body_writes_nothing", CALLS, [answer("<html>maintenance</html>")], config=CONFIG, seed=EARLIER
    ),
    Scenario("failed_fetch_writes_nothing", CALLS, [answer("", status=502)], config=CONFIG, seed=EARLIER),
    Scenario("skips_without_a_token", CALLS, env={}, config=CONFIG),
    # leadline's own, which the script doesn't do: reviewed rows.
    Scenario(
        "a_file_starting_late_gets_rows_back_to_the_first_discussion",
        [],
        [answer(github_discussions(FOUR)), comments_answer(FOUR_COMMENTS)],
        config=CONFIG,
        seed={FILE: LATE},
        expected={FILE: REBUILT + LATE + four_today(4)},
    ),
    Scenario(
        # The query reads the 100 most recently updated: with more, the older ones' days can't be counted.
        "with_more_discussions_than_the_query_reads_nothing_is_rebuilt",
        [],
        [answer(github_discussions(FOUR, 150))],
        config=CONFIG,
        seed={FILE: LATE},
        expected={FILE: LATE + four_today(150)},
    ),
    Scenario(
        # An earlier rebuild's rows, null where it couldn't tell the comments, get them once the comments are read.
        "rebuilt_rows_get_the_comments_they_lacked",
        [],
        [answer(github_discussions(FOUR)), comments_answer(FOUR_COMMENTS)],
        config=CONFIG,
        seed={FILE: WITHOUT_COMMENTS + LATE},
        expected={FILE: REBUILT + LATE + four_today(4)},
    ),
]
