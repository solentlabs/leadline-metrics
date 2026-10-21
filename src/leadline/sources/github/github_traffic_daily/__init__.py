"""github_traffic_daily: a repo's views and clones, a row per day, backfilled across the 14-day window.

Each entry reads the repo's traffic views and clones, one call each, as the script's
collect_github_traffic_daily does, and writes a row for each day of the window the file has no good
row for, and for yesterday again, as its counts can still settle; never today, which is still
counting. The rows follow the views, in their order: a clones day the views lack writes no row, and a
views day the clones lack has no clones. A window with no days writes nothing. Traffic needs push
access to the repo. Where leadline differs, by decision:

- A failed fetch is a `fetch_failed` row, and an answer it can't use (not JSON, not the shape it
  reads, a day without a timestamp) is a `parse_failed` row, where the script writes nothing (ADR: a
  failure is recorded as a failure). Its date is yesterday, the day the newest row should have; the
  next good run backfills every day the window still holds.
- A failure never replaces a good row, and a day with only a failure row is backfilled
  (docs/ARCHITECTURE_DECISIONS.md).
- Yesterday's good row refreshed with counts that changed is a correction, which store records for
  health (ADR: every correction is recorded).
- An error waiting can't fix (401, 403 or 404 that isn't a rate limit) is critical at once.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.github.github_traffic_daily.source import ENV, LAGGING, check, collect
    from leadline.sources.github.github_traffic_daily.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
