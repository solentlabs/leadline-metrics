# github_contributor_locations

- **Reads:** where the people who open a repo's issues and pull requests are,
  from their profiles, issue-form answers and issue text, and, where the entry
  sets `comments`, a person's own comments, with the count per region; every
  past day recomputed back to the first person's first issue, each person with
  today's region, under today's rules.
- **Requests:** every page of the repo's issues, then the profile of each person
  who opened one (and, for a person with no location, their issues' titles and
  bodies). With `comments`, the comments of the issues of each person still
  without a region: all of them the first night, then at most one issue a run.
- **Credentials:** `GITHUB_TOKEN`; the source is skipped while it is unset. The
  token needs to read the repo: any token reads a public one; a private one
  needs read access to it.
- **Writes:** one row a day per entry, to its default file unless the entry sets
  `file`: the number of people, how many have a location and a region, the count
  per region, and each person; with `comments`, also `comments_read`, which
  issues' comments were read and under which rules; with a table that reads
  attached files, `timezones_read`, which issues' files were read and under
  which rules.
- **Config entry:** `sources.github_contributor_locations` is a list; each entry
  has `repo` (`owner/name`) and `regions`, the name of a table in
  `shared.regions` that maps keywords to regions. `comments` (optional,
  `true`) reads comments as below. A table that sets `attachment` and
  `attachment_value` also reads attached files as below.

```json
{"repo": "example-org/example-app", "regions": "example-regions"}
```

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.

`comments`, off unless set: a person whose region is still unknown after their
profile, forms and issue text gets the first region of the table's `text` rules
found in the first 500 characters of their own comments on the issues of the
people still unplaced (`comment_inference`). A comment places only its author:
another person's comment names another person's provider. Those issues' comments
are read once, the first night and again whenever a `text` rule changes; after
that, one issue a run, an unread one first, then one changed since it was read
(`updated_at`). A person with no region is listed in the log with their issue
numbers (`Unplaced contributors in <repo>: login (#1, #2), ...`). A new ISP
rule is a config change: the next night reads the comments again under it
(ADR: a person's region from their own comments, bounded by what the row
remembers).

```json
{"repo": "example-org/example-app", "regions": "example-regions", "comments": true}
```

Attached files, off unless the entry's table sets `attachment` and
`attachment_value` (docs/CONFIGURATION.md): the first file
`attachment` finds in the body of a person's newest issue is fetched without the
token, `attachment_value` captures a value in its text, and the first `zones`
rule matching it gives a region, the table's own first and then core's
(`timezone_inference`). A zone outranks a modem
and issue text, not the answer to the provider question; a profile stays first
and a zone that disagrees with it, with the answer, or with what it replaces, is
logged by rule and regions (`WARN: zone rule 'x' gives A where a profile says B
(n)`). A person a profile or an answer placed is read too, only to check it.
Files are read once, the first night and again whenever the attachment settings
or the zones change (a refreshed places table changes them); after that, one
file a run, an unread one first, then one whose link changed. Only the region
is kept (ADR: a region from the zone in an attached file, bounded by the row).

Known regions, off unless the entry's table sets `known`
(docs/CONFIGURATION.md): a login mapped to a region the table gives. A person
whose profile location gives no region gets it, with the source `configured`,
read from the config every run so it holds through the rebuild; a profile region
stays first. A profile, answer, modem or zone that differs from it is logged by
the two regions and the clue, never by login (`WARN: a known region says A where
a profile says B (n)`).

## Known limits

- **A late correction is routine.** The nightly run reads at 23:30 and the
  recompute counts each day to midnight, so activity in the last half hour
  shows as a correction to yesterday's row the next night.
