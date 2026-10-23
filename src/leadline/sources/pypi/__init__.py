"""pypi: each package's downloads by day from pypistats, with its version from PyPI.

It reads each package as the script's collect_pypi does, in the night's order (the packages rotated
one place further along each night, on the date's ordinal, so no package is always last), `sleep_s`
apart: pypistats' daily history (`overall`, two attempts `sleep_s` apart), then PyPI's metadata. The
run's date gets the day's, week's and month's downloads (without mirrors; a day with no row counts as
none), the same with mirrors from the answer's `with_mirrors` rows (null for all three where it has none),
and the latest version and required Python, or, while pypistats hasn't published the date, a
`fetch_failed` row with `http_code` "200". Then each earlier day since the file's first row that has
no row, or a row that isn't good, is backfilled from the history, marked `backfilled`, from where a
month's window is whole in the history (a package first uploaded within it has its whole life there;
without its metadata it counts as older) up to the latest day published. A failed history request is
a `fetch_failed` row naming the last status. The retry pass (`retry`, after every source has
collected) waits `retry_wait_s`, then asks again for each package whose row for the run's date is
that failure. Where leadline differs, by decision:

- It keeps the downloads with mirrors, `downloads_with_mirrors_last_day`, `_last_week` and `_last_month`, which
  follow the month's downloads, from the same answer, no further request: more data is captured than the
  script did. A `with_mirrors` row it can't use is left out, never a failure.
- An error waiting can't fix (401, 403 or 404 that isn't a rate limit) is never retried, in the
  pass or the retry pass, and is critical at once (ADR: one failed night recovers; the script's
  retry pass asks again after a 404).
- The history is the row's required part: an answer it can't use (not JSON, not the shape it reads,
  no days at all) is a `parse_failed` row, where the script writes nothing, and its metadata isn't
  asked for (ADR: a failure is recorded as a failure). The retry pass asks again only for a request
  that failed, never for an answer the run got, which the run would only serve again (ADR: a run
  reuses its successful answers).
- The metadata is optional: a failed request, or an answer it can't use, leaves the version and
  required Python null in a partial row, `partial` where `ok` was, where the script writes "" or,
  for an answer it can't parse, nothing (ADR: a partly failed row keeps its good parts). An earlier
  run's values of the day are kept (ADR: a run never erases what it didn't get).
- A failure row never replaces a good or partial row, the run date's included, where the script's
  does; and a package with a good row for the run date isn't asked again in the retry pass.
- A line that isn't a row, or whose `date` isn't a date, is left out of the backfill, where the
  script stops on it and writes nothing (ADR: store never fails on a line it can't use).

pypistats is asked only by the scheduled run (ADR: pypistats.org is called only by the scheduled
run); `check()` asks PyPI alone.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.pypi.source import ENV, LAGGING, check, collect, retry
    from leadline.sources.pypi.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "retry", "summary", "validate"]
