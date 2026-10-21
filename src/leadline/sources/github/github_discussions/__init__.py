"""github_discussions: a repo's discussions: totals, community and maintainer, answers, categories, over GraphQL.

Each entry asks GitHub's GraphQL API, once, for the 100 most recently updated discussions, as the
script's collect_discussions does, and writes one row for the run's date: the total; how many of
those fetched the community and the entry's maintainer opened; how many community authors; how many
answered, and their comments; the count per category; and the ten most recent. As in the script, a
missing author counts as "unknown" (community), a missing category as "uncategorised", and a
category whose name is null under "null"; null nodes are none, and a missing total is zero.

Before that row, where the query read every discussion, each with its creation time, it writes a row
for each day from the first discussion's to the day before the file's first row a night wrote
(store.rebuild_days): the total, who opened them and their categories as of the end of that day, and
null for what a past day can't tell, PAST_UNKNOWN (store.fill; ADR: past values a source can rebuild
are filled, never changed). Where one of those days lacks its comments, a second query, COMMENTS_QUERY,
reads each comment's creation, and the day gets the comments made by its end, where that query read
every discussion's every comment. Otherwise the log says why nothing (or no comment) is rebuilt. Where
leadline differs, by decision:

- The maintainer is required (docs/CONFIGURATION.md, "Source entries"): the script's empty login,
  which counts everyone as community, is a config error.
- A failed request is a `fetch_failed` row, GraphQL errors a `graphql_error` row (the script's status
  for them in its Cloudflare collector), and an answer it can't use (not JSON, no repository, a
  discussion it can't read) a `parse_failed` row, where the script writes nothing (ADR: a failure is
  recorded as a failure). The one request is required.
- A failure never replaces a good row, and an error waiting can't fix (an HTTP 401, 403 or 404 that
  isn't a rate limit, or a GraphQL error of a type in `github.GRAPHQL_UNFIXABLE`) is critical at once.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.github.github_discussions.source import ENV, LAGGING, check, collect
    from leadline.sources.github.github_discussions.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
