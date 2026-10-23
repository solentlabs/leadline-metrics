"""goatcounter: a site's page views by day and its top referrers, from GoatCounter's API.

Each entry reads the week before the run, as the script's collect_goatcounter does: every page's
visits by day (`stats/hits`, up to three attempts while the request fails or comes back empty), the
week's total (`stats/total`), then the referrers of each of the first ten pages with an id whose
referrers it can fetch (`stats/hits/<id>`; a failed request doesn't count toward the ten, an answer
it can't read does). It writes a row for each day the pages have a stat for that the file has no
good or partial row for, and for yesterday again, as its counts can still settle; never today, which
is still counting. Yesterday's row carries the week's top five referrers, the window's sum of page
views (a stat without a day counted in it), GoatCounter's own total and how many pages came back;
the other days' rows carry page views alone. No pages writes nothing. Referrers tied on count come
in the order their pages came back, and within a page in the answer's order (the script's order
depends on the file system). Where leadline differs, by decision:

- Its rows also hold `top_pages` after `top_referrers`, from the same answer: the ten pages with the most
  views in the week, by each page's `count` (ties in the order the pages came back), as `{path, title,
  count}`. Yesterday's row has them; the other days' rows hold an empty list, as they hold no referrers. A
  page without a text path or a count is left out, a title that isn't text is null: more data is captured
  than the script did.
- The pages are the row's required part: a failed request is a `fetch_failed` row, and an answer it
  can't use (not JSON, not the shape it reads) a `parse_failed` row, where the script writes nothing
  (ADR: a failure is recorded as a failure). Its date is yesterday, the day the newest row should
  have; the next good run backfills every day the week still holds.
- The total and the referrers are optional parts, as the script writes the row without them: one
  that fails, or that it can't use, is null in a partial row naming the failure, where the script
  writes null for the total and leaves a page out of the referrers (ADR: a partly failed row keeps
  its good parts). The referrers are one part, read whole: one page's failure leaves them null.
- A failure never replaces a good or partial row, a part a run fails to get keeps the value an
  earlier run of the day got (ADR: a run never erases what it didn't get), and a day with only a
  failure row is backfilled.
- Yesterday's good row refreshed with values that changed is a correction, which store records.
- A 401 or 403 that isn't a rate limit, on any request, is an error waiting can't fix: critical at
  once, and the pages' request is not retried. A 404 is recoverable, retried like any other failure
  and never critical at once, as GoatCounter answers 404 for a readable site at times; check() still
  reports it as an error, and a second failed night makes it critical (ADR: one failed night
  recovers; consecutive failures are critical).
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.goatcounter.source import ENV, LAGGING, check, collect
    from leadline.sources.goatcounter.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
