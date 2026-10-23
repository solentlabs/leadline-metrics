"""github_issues: a repo's issues and pull requests: counts, flow, contributors, ages, engagement, labels, triage.

Each entry reads the repository, then every page of its open issues and of all its issues (asked for
by number while the answer's Link names a next page), as the script's collect_github_issues does, and
writes one row for the run's date: the repo's stars, forks and watchers; its open issues and pull
requests and the cumulative totals opened and closed, with the flow since the previous snapshot, and
`closed_not_planned_total`, the issues closed as not planned; who opened them (the entry's maintainer,
if any, the community and bots: a `[bot]` login, `dependabot` or `github-actions`); the open issues' ages from
the run's clock, comments, the five most commented and
those with five or more; each label's counts; and triage. The previous snapshot is the last row in the
file dated before the run, a row with no date counting as a day back; where it has no totals there is
no flow, as in the script.

The run date's row also holds two totals the script doesn't keep (more data is captured than the script
did), counted from the same lists: `issues.closed_not_planned_total`, the closed issues, pull requests
aside, whose `state_reason` is `not_planned`, beside `closed_total`, and `contribution.prs_merged_total`,
the pull requests whose `merged_at` is set, beside `prs_total`. Only the run date's row has them: the fill
and the rebuild of past days write the totals they always did.

Before it reads the previous snapshot, it fills the totals opened and closed into each past good row
that lacks them, counted as the run counts its own, as of the end of the row's day, from every issue's
dates (store.fill; ADR: past values a source can rebuild are filled, never changed): the script began
writing them after its rows. A total a row has, null included, stays, so the flow differs from the
script's only where a row lacked one. It also writes a row for each day from the first issue's to the
day before the file's first row a night wrote (store.rebuild_days): the open issues and pull requests at
the end of that day, and the totals.

With `kinds`, an ordered list of kinds each a name and a label, `issues` also holds `opened_by_kind`: the issues
opened, ever, each counted once under the first kind in the list whose label it carries, or `other`, so the counts
add up to `opened_total`. The same fill gives every past good row that lacks it the counts as of the end of its
day, and each day before the file's first row has them too. Every run also counts them again for each past good row
that has them, under the kinds the config lists now (store.recompute; ADR: derived history follows today's rules):
a changed count is a recorded correction (ADR: the issues rows count what was opened by kind).
Without `kinds` the field isn't written.

With `kinds`, the run date's `issues` also ends with `open_by_kind`: the open issues counted the same way, so the
counts add up to `open`. Every run also gives it to each past good row, and counts it again where a row has it
(store.recompute, a changed count a recorded correction), from the issues' dates: open on a day is created by its end
and not closed by it, each under its kind. A row whose `open` the counts don't add up to, or that has no `open`, is
left as it is and the log says how many (ADR: the issues rows count what was opened by kind).

With `stars_and_forks` and a `regions` table, `repo` also holds `stars_by_region` and `forks_by_region`: the people who
starred or forked by the end of the row's day, counted by the region their profile location gives in the table (else
the table's `known` region for their login, else `unknown`). Only counts are written or logged; the newest past row
with them holds everyone dated up to its day, so a run reads the profiles of the people since, whole days from the
oldest, at most 100 a run (regions.py; ADR: stars and forks are counted by region, never by person). Past rows that lack
the counts, and each day written for history, get them as the stars and forks were filled.

With a `queue`, the row also holds the open issues carrying its label (pull requests aside), each
listed with its age, comments and labels, their count with comments, and the label's resolved
requests and requesters; `body_match` flags and counts the listed issues whose body matches its
pattern, ignoring case, and the entry's region table gives each listed issue its region, the first
`text` rule with a keyword in its title and body, or "unknown". Keys a queue doesn't configure are
left out; the others come in the script's order. Where leadline differs, by decision:

- Where the table reads issue forms (`answer`, `model_answer`), the issue's form gives its region
  before its text, as it gives a person's in github_contributor_locations (config.form_region;
  ADR: an answer in an issue form is the strongest region signal); the script reads no forms.
- A failed request is a `fetch_failed` row, and an answer it can't use (not JSON, a page that isn't a
  list, `{}` included, which the script reads as no issues, an issue it can't read) a `parse_failed`
  row, where the script writes nothing (ADR: a failure is recorded as a failure). Every request is
  required: the script writes nothing without any of them.
- A failure row is not a previous snapshot, as no reader counts one; nor is a line that isn't a row,
  where the script can't read the file (ADR: store never fails on a line it can't use).
- A failure never replaces a good row, and an error waiting can't fix (401, 403 or 404 that isn't a
  rate limit) is critical at once.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.github.github_issues.source import ENV, LAGGING, check, collect
    from leadline.sources.github.github_issues.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
