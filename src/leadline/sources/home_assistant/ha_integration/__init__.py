"""ha_integration: a custom integration's installs by version, and its rank, from Home Assistant's analytics.

The feed, custom_integrations.json, holds every custom integration's installs by version. It is read
once a run, and each entry writes one row for the run's date, as the script's collect_hacs does: the
integration's total, its versions in the script's order (numeric parts as numbers, a pre-release after
its release), and its rank among the feed's integrations (ties by name, descending). An integration
the feed doesn't list reads as no installs and no rank, as in the script. Where leadline differs, by
decision:

- A failed fetch is a `fetch_failed` row, and an answer the script's code stops on (not JSON, not an
  object, the integration's entry or versions not an object, a total the ranking can't compare) is a
  `parse_failed` row, where the script writes nothing; so is a feed that lists no integrations at all,
  where the script writes no installs (ADR: a failure is recorded as a failure).
- A failure never replaces a good row (docs/ARCHITECTURE_DECISIONS.md).
- An error waiting can't fix (401, 403, 404) is critical at once.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.home_assistant.ha_integration.source import ENV, LAGGING, check, collect
    from leadline.sources.home_assistant.ha_integration.spec import DEFAULT_FILE, NAME, SHOWN, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "SHOWN", "check", "collect", "files", "summary", "validate"]
