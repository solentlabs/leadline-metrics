"""discourse_topics: views, replies and likes of topics on a Discourse forum.

Each entry names a forum (`base_url`) and its `topics`, each an id and a label. Each topic is read
from `<base_url>/t/<id>.json`, one request each, as the script's collect_forum reads its threads, and
written as one row for the run's date, keyed by the date and the topic's id (`store.SECOND_KEY`):
its views, its replies (its posts but the first), its likes and the day of its last post (empty when
it has none). A view, like or post count the answer lacks is a `parse_failed` row naming it, where
the script reads it as 0 (ADR: a failure is recorded as a failure). A row whose `topic_id` isn't a
number (a string, from a hand-edited file) is a line without a usable key: it is kept, as the script
keeps it, and never replaced (ADR: store never fails on a line it can't use).

After a topic's good row, it writes a row for each day from the topic's creation to the day before
the topic's first row a night wrote (store.rebuild_days): its replies and last post by the end of that
day, from each post's creation, and null for its views and likes then (store.fill; ADR: past values a
source can rebuild are filled, never changed). The posts the topic's answer leaves out are asked for by
id, POSTS_CHUNK at a time, only when a day lacks its row; only when the topic's stream holds every post
it counts. Otherwise the log says why nothing is rebuilt. Where leadline differs, by decision:

- A topic that fails to fetch is a `fetch_failed` row, and one it can't use (not JSON, not an object,
  a count that isn't one, a null post count included) a `parse_failed` row, where the script skips
  it (ADR: a failure is recorded as a failure). The other topics are still read.
- A failure never replaces the topic's good row for the day (docs/ARCHITECTURE_DECISIONS.md).
- An error waiting can't fix (401, 403 or 404 that isn't a rate limit: a private or deleted topic)
  is critical at once.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.discourse_topics.source import ENV, LAGGING, check, collect
    from leadline.sources.discourse_topics.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
