"""cloudflare_zone_traffic: a zone's HTTP traffic from Cloudflare's GraphQL Analytics API, kept as daily counts.

Cloudflare keeps this data for a limited time (a year of daily totals, 31 days of per-request detail); a row a day
keeps it for good. Each entry reads a zone in two parts:

- **The daily dataset** (required): a day's requests, cached and encrypted requests and bytes, page views, threats and
  unique visitors, and the counts by country, status code, browser, content type, HTTP version, SSL protocol, IP class
  and threat type. The first run reads the whole year and fills a row for each day from the first one Cloudflare
  has (marked `backfilled`); every run after re-reads the last 30 days.
- **The per-request dataset** (optional, one query for each list): a day's visits and origin response times, and its
  top requests by path, host, user agent, browser, operating system, device type, data center, cache status, method,
  bot category, security action and source, and request source. A list that fails is null and the row `partial`
  (ADR: a partly failed row keeps its good parts); the next run fills it.

Only counts are kept: no visitor's address, and nothing that identifies a visitor (ADR: zone traffic is kept as
counts). A day's row is written for yesterday (the zone's UTC date) and the 30 days before it are healed:
a day with no row or a failed one is filled, and a changed daily total is corrected, each correction recorded.
A failure never replaces a good row (docs/ARCHITECTURE_DECISIONS.md).
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.cloudflare.cloudflare_zone_traffic.source import ENV, LAGGING, check, collect
    from leadline.sources.cloudflare.cloudflare_zone_traffic.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
