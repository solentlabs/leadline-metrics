"""cloudflare_web: a site's page views and visits from Cloudflare Web Analytics, over GraphQL.

Each entry reads the 30 complete days before the run (daily page loads and visits) and the week's
top referrers, as the script's collect_cloudflare_analytics does, and writes a row for yesterday:
`ok` with yesterday's and the week's numbers, or a failure row (`fetch_failed`, `graphql_error`,
`no_account`, `parse_failed`). A good answer also heals the 30 days, from the file's first row on:
a day with no row, a failed row, or a row read under another site tag is filled, marked
`backfilled`. Every row read from the API carries the `site_tag` it was read under. Where leadline
differs, by decision:

- Yesterday's row also holds the week's top countries, devices and paths (`top_countries`, `top_devices`, `top_paths`),
  each from a request of its own, which the script never makes. A list that fails or can't be read is
  null in a `partial` row, and the list an earlier run got is kept (ADR: a partly failed row keeps its
  good parts).

- A failure never replaces a good row (docs/ARCHITECTURE_DECISIONS.md).
- A good row in the 30 days, read under the same tag, whose numbers changed gets the new ones (ADR:
  cloudflare_web re-reads and corrects its last 30 days). store records each correction, a heal over
  a good row and yesterday's rewrite included (ADR: every correction is recorded).
- An answer that can't be read is a `parse_failed` row, and `no_account` means only that the account
  filter matched nothing (ADR: a failure is recorded as a failure).
- Credentials the API rejects (401, 403) aren't retried, and are critical at once.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.cloudflare.cloudflare_web.source import ENV, LAGGING, check, collect
    from leadline.sources.cloudflare.cloudflare_web.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
