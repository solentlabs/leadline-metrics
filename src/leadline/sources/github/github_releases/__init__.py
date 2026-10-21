"""github_releases: a repo's releases, with each asset's downloads.

Each entry reads the repo's first 100 releases in one call, as the script's collect_github_releases
does (a repo with more has its oldest left out), and writes one row for the run's date: each
release's tag, whether it is a prerelease or a draft, when it was published, and each asset's name,
downloads and size, a missing field taking the script's default. Where leadline differs, by decision:

- An entry's `exclude` hides releases from reports and trends only; the file keeps them (spec.py, docs/TRENDS.md).
- Trends charts each release's downloads from the rows, with no change to them (`spec._charts`,
  docs/TRENDS.md, "A list total").
- A failed fetch is a `fetch_failed` row, and an answer it can't use (not JSON, not a list of
  releases, a release's assets not a list of objects, an asset without a name) is a `parse_failed`
  row, where the script writes nothing, or no releases for an empty object (ADR: a failure is
  recorded as a failure).
- A failure never replaces a good row (docs/ARCHITECTURE_DECISIONS.md).
- An error waiting can't fix (401, 403 or 404 that isn't a rate limit) is critical at once.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.github.github_releases.source import ENV, LAGGING, check, collect
    from leadline.sources.github.github_releases.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
