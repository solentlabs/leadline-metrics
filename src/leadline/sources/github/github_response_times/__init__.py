"""github_response_times: how long community issues wait for an answer, over GraphQL.

Each entry pages through every issue of the repo, oldest first, 100 a page by cursor, each with its
first 30 comments, as the script's collect_response_times does: each page's cursor is read as it
comes, and the issues once every page is in. Of the issues opened by anyone but the entry's
maintainer (and not by a missing author), it counts those answered and those never answered, and
writes one row for the run's date: the median, mean, 90th percentile and fastest hours to the answer,
and the share answered within 1, 24 and 72 hours, an answer exactly on a bound counting as within it.
A comment with a missing author is passed over, and null comments are none.

Before that row it recomputes the row of each past day, from the first community issue's to the day
before the run's (store.recompute_days), counted the same way as of the end of that day: the issues
opened by then, a comment or closing after it not yet made. A day with no row gets one, a failure row
is replaced, and a good row whose values differ is corrected, each correction recorded (store.recompute;
ADR: derived history follows today's rules).

The script reads its saved pages in the text order of their file names (page_10 before page_2).
leadline reads them in page order: every value in the row is a count or comes from the sorted hours
(the mean from the exact sum, which order can't change), so the rows are the same. Where leadline
differs, by decision:

- An issue is answered at the maintainer's first comment or at its closing, whichever comes first,
  where the script counts only the comment (ADR: a closed community issue is answered). The query
  already reads each issue's closing time.
- The maintainer is required (docs/CONFIGURATION.md, "Source entries").
- A failed page is a `fetch_failed` row, GraphQL errors a `graphql_error` row (the script's status
  for them in its Cloudflare collector), and an answer it can't use (not JSON, no repository, a page
  it can't page through, an issue or comment it can't read) a `parse_failed` row, where the script
  writes nothing (ADR: a failure is recorded as a failure). Every page is required.
- A failure never replaces a good row, and an error waiting can't fix (an HTTP 401, 403 or 404 that
  isn't a rate limit, or a GraphQL error of a type in `github.GRAPHQL_UNFIXABLE`) is critical at once.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.github.github_response_times.source import ENV, LAGGING, check, collect
    from leadline.sources.github.github_response_times.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
