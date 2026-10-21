# github_issues

- **Reads:** a repo's issues and pull requests: counts, flow, who opened them,
  ages, engagement, labels, triage; optionally one label's queue; the issues
  opened and closed, ever, filled into each past row that lacks them, as of its
  day, and with `kinds` the issues opened by kind, counted again under the kinds
  on every run; the days before the file's first row rebuilt back to the first
  issue: open issues and pull requests, and the totals; the repo's stars and
  forks, filled into the past rows before the first with them, from the dates
  GitHub keeps; with `stars_and_forks` and `regions`, those stars and forks
  counted by region.
- **Requests:** the repository, then every page of its open issues and of all
  its issues (and, for a queue, their bodies); and, only while a past row lacks
  them, every page of its stargazers and forks; and, with `regions` on
  `stars_and_forks`, every page of them each run and the profile of each new
  stargazer and forker, at most 100 a run.
- **Credentials:** `GITHUB_TOKEN`; the source is skipped while it is unset. The
  token needs to read the repo: any token reads a public one; a private one
  needs read access to it.
- **Writes:** one row a day per entry, to its default file unless the entry sets
  `file`: the counts and the flow since the previous snapshot.
- **Config entry:** `sources.github_issues` is a list; each entry has `repo`
  (`owner/name`); `maintainer` (optional; empty or absent for none, when
  everyone who isn't a bot is community); `queue` ([see its keys](#queue)),
  `regions` (a table's name in `shared.regions`, for the queue and for the
  [stars and forks by region](#stars-and-forks-by-region)) and `kinds`
  ([see its keys](#kinds)), each optional; `stars_and_forks` (a boolean, off
  by default: [see below](#stars_and_forks)).

```json
{"repo": "example-org/example-app", "maintainer": "example-maintainer"}
```

## `queue`

`queue`, optional: the open issues carrying one label, pull requests aside.

| Key | Meaning |
| --- | ------- |
| `label` | the label |
| `key` | the row key it is written under; default `label_queue` |
| `items_key` | the key each issue is listed under; default `issues` |
| `body_match` | optional: `pattern`, a regex matched ignoring case against each listed issue's body; `flag_key` flags each issue that matches, and `count_key` counts them |

- Each listed issue has its region when the entry names a `regions` table:
  its [issue form's](../../../../../docs/CONFIGURATION.md#shared-tables)
  region, where the table reads forms, else the first `text` rule matching
  its title and body, or `unknown`.
- The queue also resolves the label's requests and their requesters.
- The keys come in the script's order. A key the queue doesn't configure
  (`body_match`'s, or the regions' without a table) is left out.
- A key the config names must not be one the row, the queue or a listed
  issue already has.
- `regions` with neither a queue nor `stars_and_forks` is a config error, as
  nothing would read it.

## `kinds`

`kinds`, optional: a list of kinds, each an object with a `name` and a
`label`, which gives each row's `issues` an `opened_by_kind` object (ADR:
the issues rows count what was opened by kind).

```json
"kinds": [{"name": "request", "label": "new request"}, {"name": "defect", "label": "bug"}]
```

- Each issue opened (pull requests aside) counts once, under the first kind
  in the list whose label it carries, or under `other`. The object holds
  every kind in the list's order, then `other`, and its values add up to
  `opened_total`.
- Past rows that lack the field are filled from each issue's creation date.
  Every run counts it again for each past row that has it, under the
  kinds the config has now, so adding, removing or relabelling a kind
  changes every day's counts at once, each a recorded correction (ADR:
  derived history follows today's rules). A null stays.
- The list is not the queue's label, though a kind may name the same one:
  the queue lists the open issues that carry its label, a kind counts every
  issue opened.
- With `kinds`, every row also has `open_by_kind` (last in `issues`): the
  issues open that day counted the same way, so its values add up to `open`.
  For a past day an issue is open when it was created by the day's end and
  not closed by it (its last close), which the row's own `open` is checked
  against: a past row whose `open` the counts don't add up to, or that has no
  `open` (an issue reopened, deleted or transferred since, say), is left
  without it and the log counts those rows. Every run gives it to past rows
  that lack it and counts it again for those that have it, under the kinds
  the config has now, each change a recorded correction.
- A kind's name and label are each non-empty and used once, and no name is
  `other`. Without `kinds`, rows have neither `opened_by_kind` nor `open_by_kind`.

## `stars_and_forks`

`stars_and_forks`, optional, `true` or `false` (the default): the entry has a
second section in the summary and on the trends page, titled
`github_issues: <repo> stars` (list it under that title in a project), that
charts the row's `repo.stars` and `repo.forks` as the "Stars and forks" row
of the Traffic card, placed among the card's rows by the order the project
lists the sections (ADR: an entry may give a second section, for a row of a
card). It reads the file the entry already
writes, so it adds no request and no row key (but see [by
region](#stars-and-forks-by-region)). Without it, the entry has one section.

## Stars and forks history

Rows from before a file's rows carried `repo.stars` and `repo.forks` get them
from the dates GitHub keeps, with no setting: the source is already writing
`repo`, and fills what is missing (ADR: past values a source can rebuild are
filled, never changed).

- A past good row before the file's first row with a count of `stars` (or of
  `forks`) that a night wrote, and a day with no row before the file's first,
  back to the first issue's or the first star's or fork's, gets `repo.stars`
  (and `repo.forks`): the stars (forks) dated on or before the end of that
  day in the run's UTC offset, as every row's date is. A row's added keys carry
  no mark; a row written for a day that had none is marked `backfilled`. No
  `watchers`: GitHub keeps no dates for them.
- A count a row has, and every row from the first with one on, is never
  changed. The stars are asked for oldest first with their times
  (`/stargazers`, the `star+json` media type), and the forks oldest first
  (`/forks?sort=oldest`), 100 a page, only while a past row lacks the count;
  a later run asks for neither. A repo with no stars (forks) asks for no list,
  and one with more than 5000 is left unfilled, said in the log.
- A list that can't be read, or a request that fails, leaves that count unfilled
  and the log says so; the run's own row is written as ever and the next run
  tries again. The stars and the forks are filled apart.
- A star or fork removed since is no longer listed, so a past day's count can
  be low, most of all for the earliest days of a repo that lost some.

## Stars and forks by region

An entry with `stars_and_forks` and `regions` (the table's name in
`shared.regions`, [Shared tables](../../../../../docs/CONFIGURATION.md#shared-tables))
gives each row `repo.stars_by_region` and `repo.forks_by_region`: the people
who starred or forked by the end of the row's day, counted by region, the
largest first (ADR: stars and forks are counted by region, never by person).
Without both settings the rows have neither. The trends page does not chart
them (the Stars and forks card shows the two counts); the rows keep them.

```json
"repo": {"stars": 55, "forks": 6, "watchers": 4, "stars_by_region": {"unknown": 31, "US": 20, "DE": 4}, "forks_by_region": {"US": 4, "DE": 2}}
```

- A person's region is the one the table gives their profile location (as
  `github_contributor_locations` reads it), else the table's `known` region
  for their login, else `unknown`: a profile with no location or none at
  all, or a location no rule reads. A `known` region a profile contradicts
  is logged by the two regions, never by login.
- Counts only. No login is written to a row or a file, and none is
  kept between runs: the newest past row with the counts holds everyone dated
  up to its day, so a run reads the profiles of the people dated after it and
  adds them. A night with nobody new reads the list of stargazers (forks) and
  no profile; a repo with none reads neither.
- A run reads at most 100 profiles, stars and forks together, a person on
  both once, and reads a day's people whole: it stops before a day that
  wouldn't fit, except the first it reads, so a busy day is read in one run
  and every run goes on. A repo with many people takes several nights; the
  days not yet counted have no field and the next run fills them, each past
  row that lacks them and each day written for history, with no
  `backfilled` mark on a row that already existed.
- A list or profile that can't be read, or a repo with more than 5000 stars
  (forks), leaves the counts to the next night and says so in the log; the
  run's row is written as ever.
- The counts are not recomputed: a person is counted once, as their profile
  read that night. A rule added to the table places only people counted after
  it, and a star removed later stays counted, so a stack can add up to more
  than `repo.stars`. The days before the newest counted row are never filled.

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
