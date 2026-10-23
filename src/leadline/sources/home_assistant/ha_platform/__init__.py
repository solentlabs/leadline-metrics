"""ha_platform: Home Assistant's platform-wide analytics: installations, countries, install types.

It reads data.json once a run and writes one row for the run's date, as the script's
collect_ha_platform does: active installations; how many report integrations, add-ons and statistics;
the share whose integrations are visible; installation types; average integrations; the top fifteen
countries (ties in the feed's order, at the cut too) and how many report. A null or missing
installation or reporting count reads as 0, as in the script. Where leadline differs, by decision:

- Its rows end with `ha_versions` (`current.versions`, installations by core version), `os_boards`
  (`current.operating_system.boards`) and `supervisor_arch` (`current.supervisor.arch`): more data is
  captured than the script did. Each is null where the answer lacks it or gives it as something but an
  object, and the row is otherwise unchanged.
- A failed fetch is a `fetch_failed` row, and an answer it can't use (not JSON, no `current` object, a
  count it divides by or sorts on that isn't a number) is a `parse_failed` row, where the script
  writes nothing, or zeros for a body without `current` (ADR: a failure is recorded as a failure).
- A failure never replaces a good row (docs/ARCHITECTURE_DECISIONS.md).
- An error waiting can't fix (401, 403, 404) is critical at once.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.home_assistant.ha_platform.source import ENV, LAGGING, check, collect
    from leadline.sources.home_assistant.ha_platform.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
