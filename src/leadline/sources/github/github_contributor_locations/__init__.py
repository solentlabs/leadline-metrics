"""github_contributor_locations: where the people who open a repo's issues and pull requests are.

Each entry reads every page of the repo's issues, all states (asked for by number while the answer's
Link names a next page), then, for each person who opened one, in login order, their profile, as the
script's collect_contributor_locations does. A `[bot]` login and an issue whose user is null are
skipped. A profile's location gives the region, the first rule of the entry's region table with a
keyword in it, `text` rules before `location` rules (`github_profile`), or none (`profile_unparsed`).
Only a person with no location at all (none set, or empty, or no profile: a 404) has their issues
read: every title and the first 500 characters of every body, searched for the `text` rules only
(`issue_inference`); otherwise `unknown`. It writes one row for the run's date: the number of people,
how many have a location and how many a region, the count per region, and each person.

Where the entry sets `comments`, a person left without a region also gets one from their own comments on the issues
of the people still unplaced, under the same `text` rules, within a bound on requests (`comment_inference`;
comments.py).

Where every profile was read, it also recomputes the row of each past day, from the first person's first
issue to the day before the run's (store.recompute_days): the people who had opened an issue or pull
request by the end of that day, each with the location and region this run gives them, and the totals
from those. A day with no row gets one, a failure or partial row is replaced, and a good row whose
values differ is corrected, each correction recorded (store.recompute; ADR: derived history follows
today's rules).

Where the entry's table sets `attachment`, `attachment_value` and `zones`, the zone in the newest file a person attached
gives a region too, before comments (`timezone_inference`; timezones.py).

A region table's `known` gives a person with no profile region the region the config knows for their login
(`configured`), before the answer, zone, modem, text and comments; a clue that differs is logged by regions, never
by login, and changes nothing (ADR: a region the maintainer knows is configured).

Where leadline differs, by decision:

- A failed issues page is a `fetch_failed` row, and an answer it can't use (not JSON, a page that isn't
  a list, `{}` included, which the script reads as no issues, an issue it can't read) a `parse_failed`
  row, where the script writes nothing (ADR: a failure is recorded as a failure). The issues are
  required: the script writes nothing without them.
- A profile that fails, or that it can't read, is that person's alone (ADR: a partly failed row
  keeps its good parts): every profile read is kept, and that person's entry has a null location
  and region and the source `profile_failed`, where the script reads their issues as if the profile
  had no location. The totals count only the profiles read, and the row is partial, its reason
  counting the profiles no run could read. A person's readable entry in an earlier row of the same
  day is kept in place of this run's failure, the totals recounted, and the row is good once every
  person's profile has been read by some run (ADR: a run never erases what it didn't get). Every
  profile is still asked for, as the script asks.
- A failure never replaces a good row, and an error waiting can't fix (401, 403 or 404 that isn't a
  rate limit; for a profile, 401 or 403) is critical at once.
- Where the table reads issue forms, `answer` and `model_answer` (ADR: an answer in an issue
  form is the strongest region signal), a person with no location gets the region of their first
  answer a rule matches, `exact` rules then `text` then `location`, else of the first modem a
  `models` rule matches, in the body of any of their issues (not pull requests), before their issue
  text (`issue_inference` still; config.form_region); the script reads no forms. Only the region is
  kept, never an answer. check() names the answers no rule matches (unless a `models` rule matches the
  modem in the same form, which places the person), each rule that gives another region than a profile
  location in the day's row, and each `models` rule that gives another region than the answer in the
  same form, by count, never by login.
- A zone outranks a modem and issue text and not the answer to the provider question or a profile; where it differs
  from a profile's or an answer's region, or from one it replaces, the log says so by rule and regions, never by login
  (timezones.py).
- A comment's region is the author's alone and only its first 500 characters are read, as an issue body's are: a
  maintainer's link or another user's report names someone else's provider (comments.py).
- The token goes in a header, never on a command line (ADR: credentials come only from the environment).
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.github.github_contributor_locations.source import ENV, LAGGING, check, collect
    from leadline.sources.github.github_contributor_locations.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
