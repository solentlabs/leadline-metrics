"""github_repo_activity: one public repo's week: commits, authors, fixes and redesigns, issues, contributors.

Each entry reads the repository, every page of its commits since a week before the run's date (asked
for by number while the answer's Link names a next page), its issues since then, its top ten
contributors, its five latest releases and its languages, as the script's collector does, and writes
one row for the run's date under the entry's label: the repo, its stars, forks and open issues, its
code size; the week's commits, authors, fixes and redesigns (each commit's first line matched,
ignoring case, against the entry's patterns, by default the script's, under which "Debug" is a fix);
the week's issues, and those opened by others than the owner; the top contributor's share; and the
latest release, whose date is "" while it is unpublished. A missing count reads as 0, and an empty
list as none, as in the script.

The repository and the commits are the row's required parts: the script writes nothing without them.
The issues, contributors, releases and languages are optional: the script counts a failed fetch as
none. Where leadline differs, by decision:

- A failed repository or commits fetch is a `fetch_failed` row, and an answer it can't use (not JSON,
  not the shape it reads) a `parse_failed` row, where the script writes nothing (ADR: a failure is
  recorded as a failure).
- An optional list that fails, or that it can't use, is null in a partial row naming what failed,
  never none, and never a lost row where the script loses it (ADR: a partly failed row keeps its good
  parts). A list is read whole or not at all: one item it can't read, such as an issue whose user is
  null, makes the list one it can't use.
- A failure never replaces a good or partial row, and a part a run fails to get keeps the value an
  earlier run of the day got, so a row is good once every part is filled (ADR: a run never erases
  what it didn't get).
- An error waiting can't fix (401, 403 or 404 that isn't a rate limit), on any of its requests, is
  critical at once.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.github.github_repo_activity.source import ENV, LAGGING, check, collect
    from leadline.sources.github.github_repo_activity.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
