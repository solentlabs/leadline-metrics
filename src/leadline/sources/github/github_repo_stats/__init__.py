"""github_repo_stats: a repo's stars, forks, watchers and open issues, with its 14-day traffic totals.

Each entry reads the repository, then its traffic views and clones, one call each, as the script's
collector does, and writes one row for the run's date: the repo as configured, its stars, forks,
watchers and open issues, and the views' and clones' 14-day totals and unique visitors. Traffic needs
push access to the repo. The repository is the row's required part: the script writes nothing without
it. Views and clones are optional: the script counts a failed fetch as 0. Where leadline differs, by decision:

- It reads each answer as JSON, where the script pastes them into Python code, so a quote, backslash
  escape or ''' in the repository writes the real row, not nothing.
- A count the answer lacks is unusable, not 0: the repository's makes a `parse_failed` row and a traffic
  answer's a partial row, each naming what is missing (ADR: a failure is recorded as a failure).
- A failed repository fetch is a `fetch_failed` row, and a repository it can't use (not JSON, not the
  shape it reads) a `parse_failed` row, where the script writes nothing (ADR: a failure is recorded
  as a failure).
- Views or clones that fail, or that it can't use, are null in a partial row naming what failed,
  never 0, and never a lost row (ADR: a partly failed row keeps its good parts).
- A failure never replaces a good or partial row, and a part a run fails to get keeps the value an
  earlier run of the day got, so a row is good once every part is filled (ADR: a run never erases
  what it didn't get).
- An error waiting can't fix (401, 403 or 404 that isn't a rate limit), on any of its requests, is
  critical at once.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.github.github_repo_stats.source import ENV, LAGGING, check, collect
    from leadline.sources.github.github_repo_stats.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
