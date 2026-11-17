"""cloudflare_workers: each Cloudflare Worker's daily invocations from Cloudflare's GraphQL Analytics API.

Cloudflare keeps this data three months and answers a month per query; a row a day keeps it for good. Each entry names a
Worker by its script name, and every run reads its 28 days ending yesterday (UTC) in one query and rewrites them in its
file (store.write_window): a day's invocations, errors and subrequests, its invocations by how they ended (`outcomes`),
and its CPU time percentiles in microseconds (`cpu_us`). A day with no invocations is a row of zeros; a value Cloudflare
changed is corrected, and each correction recorded.

The answer is read whole or not at all (ADR: a failure is recorded as a failure): a failed request, the API's own
errors, an account the filter didn't match, or an answer of another shape writes a failure row for yesterday, which
never replaces a good row. The rows hold counts per Worker only: the dataset has no visitor data.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.cloudflare.cloudflare_workers.source import ENV, LAGGING, check, collect
    from leadline.sources.cloudflare.cloudflare_workers.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
