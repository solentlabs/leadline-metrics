"""cloudflare_analytics_engine: a Workers Analytics Engine dataset's daily totals, one dimension at a time, over SQL.

Each entry reads the `refetch_days` complete UTC days before the run and rewrites them in its file, a
row per day (a window replace, as the script's usage collector writes it): the day's total of events
by each dimension its layout names, each summed on its own, and the counts of events where a column is
set. A quiet day is a row of zeros; every query is required, so one that fails, or an answer it can't
read, writes no day and records a failure row for yesterday, never over a good row (store.write_window).

A dataset's columns change meaning when the app writing it changes its layout, so the entry gives its
layouts, oldest first, each but the newest ending at a UTC time (`until`): each layout is read only for
the events of its own time, and a day that holds a switch adds up what each layout read of it. The
script's own usage collector reads one layout: its config, as the first layout, gives the script's two
queries and rows.

Privacy is kept here, whatever the config (docs/ARCHITECTURE_DECISIONS.md, "Analytics Engine datasets
are read as totals, one dimension at a time"):

- A column a layout marks `private` (a per-person id) is never selected, grouped by or written: a
  count may only test it for being set (`!= ''`), and validate refuses a dimension on it.
- No query groups by more than the day and one dimension, so no total crosses two dimensions.
- Every value read is checked against its dimension's layout (a closed list of values, or a pattern the
  whole value must match): one outside it is never written or logged, and is left out of the row. So
  is a dimension set on an event whose other dimension doesn't allow it (`only_when`), which is counted,
  never read. Either is critical, recorded with the dataset, the dimension and a count only.

Where leadline differs from the script, by decision: an answer or a result row it can't read is a
`parse_failed` row (ADR: a failure is recorded as a failure); credentials the API rejects (401, 403)
aren't retried, and are critical at once; a config that reads no day (`refetch_days` 0) is refused.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.cloudflare.cloudflare_analytics_engine.source import ENV, LAGGING, check, collect
    from leadline.sources.cloudflare.cloudflare_analytics_engine.spec import (
        DEFAULT_FILE,
        NAME,
        files,
        summary,
        validate,
    )

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
