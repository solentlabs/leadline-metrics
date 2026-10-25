"""discourse_forum: a Discourse forum's own site statistics, once a day.

Each entry names a forum (`base_url`). The forum is read from `<base_url>/about.json`, one request, and
written as one row for the run's date: its `base_url` as configured, then every integer of the answer's
`about.stats` as a field of its own under the forum's own name (`users_count`, `topics_count`, `posts_count`,
`active_users_7_days`, `posts_last_day`, `likes_30_days`, and `chat_messages_count` where the forum has chat).
Anything that isn't a count (a boolean, text, null) is left out, and a forum with fewer statistics gives a row
with fewer fields. The row is keyed by the date alone, so a forum has a file of its own (two forums in one config
each set a `file`, which entries may not share anyway).

- A forum that fails to fetch is a `fetch_failed` row, and an answer it can't use (not JSON, not an object, no
  `about` object, no `stats` object, or a `stats` with no integer in it, as when the forum hides its statistics
  from visitors) a `parse_failed` row naming the missing part (ADR: a failure is recorded as a failure).
- A failure never replaces the forum's good row for the day (docs/ARCHITECTURE_DECISIONS.md).
- An error waiting can't fix (401, 403 or 404 that isn't a rate limit) is critical at once.

The script has no collector for it, so the source's scenarios carry the reviewed rows.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.discourse_forum.source import ENV, LAGGING, check, collect
    from leadline.sources.discourse_forum.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
