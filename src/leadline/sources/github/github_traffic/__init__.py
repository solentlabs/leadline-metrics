"""github_traffic: a repo's 14-day views, clones and top referrers, as one snapshot.

Each entry reads the repo's traffic views, clones and popular referrers, one call each, as the script's
collect_github_traffic does, and writes one row for the run's date: the views' and clones' totals and
unique visitors, each ratio of total to unique, `window_end`, the last day the views hold (which can
be before the run's date, as GitHub's traffic lags), and the top five referrers. Traffic needs push
access to the repo. Where leadline differs, by decision:

- It reads each answer as JSON, where the script pastes the clones and referrers into Python code, so
  a quote, backslash escape or ''' in them writes the real row, not nothing.
- A failed views or clones fetch is a `fetch_failed` row, and an answer it can't use (not JSON, not
  the shape it reads) is a `parse_failed` row, where the script writes nothing
  (ADR: a failure is recorded as a failure).
- Referrers that fail, or can't be used, leave `top_referrers` null in a `partial` row with the views and
  clones, where the script writes an empty list as if there were none; the next good run fills it, and
  a run never erases referrers an earlier run got (ADR: a partly failed row keeps its good parts).
- A failure never replaces a good row (docs/ARCHITECTURE_DECISIONS.md).
- An error waiting can't fix (401, 403 or 404 that isn't a rate limit) is critical at once.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.github.github_traffic.source import ENV, LAGGING, check, collect
    from leadline.sources.github.github_traffic.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
