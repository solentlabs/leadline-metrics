# Trends

What every run, and every `leadline report`, writes in the output folder
after the reports: `trends.json`,
the history of each number the [default summary](REPORTS.md#the-default-summary)
shows, and pages that chart it (ADR: the trends viewer draws its own SVG): one
per entry of the config's [`pages`](CONFIGURATION.md#pages), each showing its
projects, or without `pages`, `trends.html`, showing everything. They are
written whatever the `reports` setting says.

`trends.py` builds them from the output files, through `store`, each source's
`summary()` sections, which the runner gives it
([The run](ARCHITECTURE.md#steps)), and the config's
[`projects`](CONFIGURATION.md#projects), which group the sections. Each file is
written atomically. One that can't be written, or a bug building them, is logged
and recorded critical (`trends`), so the run or report exits 1; the other
files, and everything before, are still written. A run and `leadline report`
build them with one function ([Reports without a run](ARCHITECTURE.md#reports-without-a-run)),
from the files alone: redrawing a page needs no collection.

## trends.json

One JSON object, UTF-8, on one line. A reader checks `format` first: a change
that could break a reader is a new format number; a new key is not.

| Key | Type | Holds |
| --- | ---- | ----- |
| `format` | number | the format, `3` |
| `date` | string | the run date, `YYYY-MM-DD` |
| `health` | object or null | the newest row of `health.jsonl`, below; null when it has none |
| `sections` | list | one per summary section, below: each project's, in the order `projects` lists them, then the sections in no project, in config order |

**`health`**: what the default summary's health lines show.

| Key | Type | Holds |
| --- | ---- | ----- |
| `date` | string | the health row's date |
| `stale`, `failed`, `critical` | lists | the health row's lists, each item as [health](ARCHITECTURE.md#health) writes it |
| `checks` | list | the row's check findings at `warn` or `error`, each `{"source", "level", "message"}` |

All sources are current when `stale` and `failed` are both empty.

**A section**: one source entry, as the default summary shows it.

| Key | Type | Holds |
| --- | ---- | ----- |
| `source` | string | the source's name |
| `name` | string or null | the entry's id (a repo, a package); null for a source with one entry |
| `title` | string | the summary's heading: `<source>: <name>`, or the source alone |
| `label` | string | the source's readable name for it (`Issues`) |
| `heading` | string | the page's heading for it, below |
| `project` | string or null | the name of the project that lists it; null for none, and for every section without `projects` |
| `file` | string | the row file read |
| `where` | object or null | the values a row must have to be read (a forum topic's `{"topic_id": 7}`); null for every row |
| `dates` | list of strings | the dates of the file's good rows, one a date (its last line), oldest first; empty until there is one |
| `fields` | list | one per field the summary shows, in its order, below |
| `card`, `row`, `about` | strings | only for a section that is a row of a card: the card's name, the row's title and a sentence on what the row shows; optional keys, so the `format` stays 3 |
| `notes` | list | only for a section with notes (an entry's [`notes`](CONFIGURATION.md#notes)): `{"date", "text"}` objects, in the config's order; an optional key, so the `format` stays 3 |

Only the fields the summary shows, and the `charts` its source adds for the
pages alone, are here, never whole rows: the row files hold the rest.

**`heading`**: the title its project gives it, if any. Otherwise, in a
project, its `label`, which the project's name places; where the project
has another section of its source (two packages, two forum topics), the
label and its name, `PyPI downloads · example-package`. In no project, the
label and its name, its whole identity; the label alone for a source with
one entry.

**A field**: `field` (its dotted path in a row), `label` and `kind`;
`complement`, `true`, where each value is 100 less the row's, as the default
summary shows it; `charted`, `false`, for a field the pages don't chart alone,
`into`, for one that is part of a stack, the `field` of the breakdown it
caps (the installs total, whose stack of versions adds up to it); `lines`,
`true`, for a breakdown the pages draw as a line for each band, not a stack;
`since`, `true`, `starts`, for a breakdown of lines by days since each line's
start, below; `half`, `true`, for a breakdown whose chart is half width;
`trend`, `true`, for a `daily` field whose chart has a line of 7-day averages;
`color`, one of `s1` to `s6`, for a field drawn in that theme color, not the
default; `chart`, for a `snapshot` or `change` that is a line of a
chart shared with the section's other fields of the same `chart`, the chart's
name; `minus`, the path of the field a derived field is less (its `values` are
the field's less that one's in each row, null where either is missing or not a
number; the default summary leaves such a field out); `band`, the path of a
field of the same chart the area is shaded to; `tip`, a list of paths of other
fields of the section the hover lists; `format`, the field's format spec, when
set; then its
series. Each series has a value for each of the section's `dates`, in order
(except a `since` field's, below).

| Kind | The value | Series |
| ---- | --------- | ------ |
| `snapshot` | a count as it stands that day (stars) | `values` |
| `change` | a running count (issues opened, ever), whose changes matter | `values` |
| `daily` | that day's count alone (views that day) | `values` |
| `value` | a value to read, not chart (a version) | `values` |
| `breakdown` | an object of counts (installs by version) | `versions`, `bands`, `keys`, `values`, `other`, `prerelease`, `betas`, `neutral` |

- **`values`**: the row's value at `field`, as the row holds it, or with
  `complement`, 100 less it; null where the row has none.
- **A list total** is a breakdown whose `field` runs through lists
  (`releases[].assets[].downloads`), which the summary names with a `by`, the
  key that names each item of the first list (`tag`). Each item is a key; its
  count is the numbers at the path added up, at the greatest it has had in any
  row so far, so an item that leaves the list (a list cut at its limit, a
  deleted release) or whose count drops keeps its count and no count falls.
  A leading `v` before a digit is dropped from a key (`v1.2.0` is `1.2.0`), so
  tags band as versions, below; where several tags share a band (a base
  version's betas, a release line) the band's count is the sum of its tags'.
  A source's `exclude` (`github_releases`) leaves named items out of the
  list in every row before any of this: an excluded release is no key, is in
  no band and no `other`, and adds to no count; the file is not changed.
  The summary can leave the prereleases out (`prereleases` false): their
  keys are then not drawn and not added into any band or into `other`, and
  `prerelease` is 0 and `betas` has no keys.
  An item has no count before its first listing, so a release published after
  the first row rises from 0 there and one published before it starts at its
  first reading; its `published_at` is not read, so one that is missing, or
  falls after the first sighting (a UTC date a day past the run's), changes
  nothing. An item that is no object, has no text key or no number at the path
  counts 0; a row whose list is missing or no list is null in every list and
  leaves the counts as they were. Counts an item gains while it is outside the
  list are never seen.
- **Days since a start** (`since`, on a field with `lines`): a list total
  whose horizontal axis counts days from each item's start date, not the
  calendar. The summary names the item key holding the start (`published_at`),
  whose first 10 characters are the day, and the field is `"since": true`,
  with `starts`, each key's start day. Its series are indexed by day, not by
  the section's `dates`: index n is the n-th day since each item's start (day
  0 its start), to 7 days past the newest drawn item's age on the last row, so
  the newest line has room to grow against the older ones, which are cut off
  there (days past the last row are null); so a reader that takes a field's
  series to be by `dates` must check `since` first (an optional key, so the
  `format` stays 3). A key's value on its day d is its count (above) as of its
  start plus d, the last row on or before that day standing for a day without
  a row, so a count is carried over a missing row and a release listed a day
  before its `published_at` (a UTC date past the run's) has its reading at day
  0. A key has no value (null) before its first listing and after the last
  row; it is never 0 there. Only the items started on or after the first row's
  date are drawn, since an earlier item's first days were never read; one with
  no readable start is not drawn. Of those only the newest 5 are drawn, the
  older not added into `other` (a sum of different releases' day-d counts
  would mean nothing); with `bands` `release`, as it needs. The pages ignore
  the range for it, and it has no "Counted from" note.
- **A breakdown** keeps at most 5 keys (and its neutral ones), in `keys`, in
  the order they stack,
  bottom first. `versions` is true when every key is a version (`1.2.0`,
  `2.0.0-beta.1`): then every prerelease (a version with a suffix after a
  dash) is added up apart, in `prerelease`, a list of each day's count, and
  by base version, its release part (`2.0.0` for `2.0.0-beta.1`), in `betas`:
  its `keys` are the 5 newest base versions, oldest first, and its `values`
  and `other` are laid out as the breakdown's own. The rest are counted by
  release line, a version's major.minor (`1.2`); `keys` are the 5
  newest lines, oldest first, and `other` is every older line. With the
  section's `bands` setting `release` ([`ha_integration`](../src/leadline/sources/home_assistant/ha_integration/README.md#bands)),
  `bands` is `"release"` and they are counted by exact release (`1.2.3`)
  instead: `keys` are every release any day holds, oldest first, with no
  limit, and `other` is 0 each day; the prereleases are as above. `bands` is
  there only for that setting, and a reader takes its absence as lines.
  Otherwise `keys`
  are the newest row's, the largest count first, as the default summary shows
  them, then, where it has fewer, the others by the largest share of one day's
  counts they had; and `other` is the rest. `neutral`, there only when it has
  one, lists the keys the summary marks as meaning none of the others
  (`unknown`): each is the last of `keys` whatever its count, and no
  other key crowds it out. `values` holds a list for each key, in that order:
  its count each day, 0 where the row lacks the key. `other` holds each day's
  rest, added up. A day whose row has no object of counts is null in every
  list. So each day's `values`, `other` and `prerelease` add up to the day's
  counts, and the `values` and `other` of its `betas` add up to that day's
  `prerelease`.

## The pages

Each page draws the data in the browser. A page opened from disk can't
read a file beside it, so the page holds the data itself, in a
`<script type="application/json" id="trends-data">` element, escaped so that
nothing in it closes the element: `trends.json`'s, with only the sections of
its projects, in its `projects`' order, and their sections in each project's
order, and only the health that concerns them: the stale, failed and
critical items of the files its sections read, and the checks of their
sources; and on every page, each crash (a critical item with no file) and
each critical item of the reports and trends. So a page carries its own
portfolio's numbers and none of another's, and is no larger than they make
it. `trends.html`, without `pages`, holds all of it, the whole run's
health included. A page
loads nothing: no script, style, font or image from anywhere. Double-click
it to open it.

- **Top**: the page's `title` (`Trends` for `trends.html`), the run date,
  and the health it holds as pills: all sources current (on a page of
  `pages`, its own files), or each failed, stale and critical file and
  each check warning or error, in the summary's words.
- **Projects**: with `projects`, a row of links to each project, which
  stays at the top as the page scrolls, and each project's sections
  under its name; on `trends.html`, the sections in no project last, under
  "Other", and a page of `pages` has none of them. Without `projects`,
  the sections follow one another, with no links.
- **Range**: 30 days, 90 days (the default), a year, or all, for every chart
  at once, counted back from the run date; a chart of days since a start (`since`)
  ignores it.
- **A section** per `sections` entry with a field, in their order, a panel
  headed by its `heading` (the sections of one card, below, share one panel),
  with the newest of each `value` field as text beside it, and a chart for
  each other field, captioned by its label and its newest value (a daily
  count's 7-row total and, after it, its newest whole week's, the week Monday
  to Sunday with a count on every day; a stack's, the newest of the field it
  caps; the label of a breakdown, and of a field
  that caps one, has its underscores shown as spaces and its first letter
  capitalized, so a config key reads as a title; a band's name shows its
  underscores as spaces, and its key stays as it is in the data). A field
  with `charted` false or an `into` has no chart of its own; the fields that
  share a `chart` are one chart, captioned by its name. A section's stacks
  come first, each on a card as wide as the section and taller than the other
  charts; a chart that is the only one besides the stacks, or the only one at
  all, is as wide as the section too. A breakdown that says it is `half` is
  not a stack for this: its chart is as wide as any other, so two of them
  (the downloads of a release by calendar and by days since release) share a
  row where the page is wide enough for two columns, as do a GitHub issues
  entry's "Opened by kind" and "Open by kind", and stack, one under the
  other, on a narrow screen. A chart whose first reading (of its
  fields, the earliest) comes more than 7 days after its section's first date
  has a note under it, "Counted from" and that date; one that starts with the
  section has none. One crosshair and tooltip follow the pointer; the
  tooltip is always whole on the screen: centered on the row, kept between the
  screen's sides, above the chart or, where that would leave the top of the
  screen, down inside it, and over the sticky project links:

  | Kind | Chart | On hover |
  | ---- | ----- | -------- |
  | `snapshot`, `change` | a line over a light fill through readings on consecutive days, with a dot at its newest value; two readings more than a day apart (whether or not a row with no value lies between) are joined by a dashed line over the same fill, which is not a reading; a reading with none beside it is a dot | the date and the value of the nearest row with one |
  | the fields of a `chart` | one figure captioned by the chart's name, at its first field's place, with a line for each field, each in a color of its own and with no fill, drawn as above, on one axis fitted to all of them, and a key above it naming each line with its newest value | the date, and each line's label and value on that row, for those with one |
  | the fields of a `chart`, with `band` | as the fields of a `chart`, and the area between the line of the field with `band` and the line of the field it names is shaded, behind the lines, in the chart's first color at 18% opacity, over the runs of consecutive days where both have a number (a date where either has none is not shaded); the axis runs from zero | as the fields of a `chart` |
  | the fields of a `chart`, with `tip` | as the fields of a `chart` | after the lines, each field their `tip` names, once, as its label and value in its own `format` (`.1f` is one decimal; any other spec shows the number as is), a field with `complement` as its complement; a field with no value on that row leaves its line out |
  | `daily` | a bar a day, from zero; a day with no count has no bar | the date, the value, and the total of the 7 rows through it |
  | `daily` with `trend` | the bars, and over them a faint line (ink at 55%, no markers) of each day's average: the mean of the days that have a count among the 7 ending that day, drawn where at least 4 of the 7 have one. It is unbroken through a day with no count and breaks only where fewer than 4 days have | as `daily`, and "7d avg" and the mean where the line is |
  | `breakdown` | each band's count, stacked from zero, so its top edge is the total (with `lines`, a line for each band on one axis, none added to another: each band's own count, solid over a translucent area (opacity 0.4) of the line's color from the baseline to the line, the newest band in front so older areas show through, its newest count named in a key above the chart, and with `bands` `release` only the newest 5 releases, the older ones added up as one line, "older"); named at the right edge with its newest count where it is thick enough | the date, each band with a count and its share (a band with none is left out), and the fields it caps (`into`) as the totals, or with none, the stack's total |

  **Notes** (`notes` of a section) are drawn on each chart of lines of the
  section (a chart of fields, a single line, a breakdown with `lines`; not a
  stack, bars or a chart of days since a start): a dashed vertical line at the
  note's date and its text above the plot area, never over the lines, which
  stays clear of the plot's top. A note dated outside the range the chart
  draws (the chosen range, or before its first reading or after the run date)
  is not drawn. A text near the right edge is anchored by its end so that it is
  not cut off.

  **A derived field** (`minus`) is a line like any other; `Shown` fields
  with one path (a field and the field derived from it) coexist, and a `band`
  or `tip` that names that path means the one without `minus`.

  **A card** is a panel that more than one source's sections make: a section
  with `card`, `row` and `about` is a row of it, and the sections of one entry
  (the same `heading`) that name the same `card` are one panel, headed by the
  card, at the place of the first, each a row with its `row` as a title and
  `about` under it, over its charts, in the order the project lists their
  sections (config order outside a project) ([`github_traffic`](../src/leadline/sources/github/github_traffic/README.md)
  and [`github_traffic_daily`](../src/leadline/sources/github/github_traffic_daily/README.md)
  are the rows "Rolling 14 days" and "By day" of the Traffic card, and
  [`github_repo_stats`](../src/leadline/sources/github/github_repo_stats/README.md)
  and a [`github_issues`](../src/leadline/sources/github/github_issues/README.md#stars_and_forks)
  entry with `stars_and_forks` the row "Stars and forks"). In a project, a
  section is headed by its card alone unless the project has another section
  of its source that is a row of the same card (or of none). A field's
  `color` draws its chart (its bars, or its line and fill) in that theme
  token, so one count is one color in every row of a card.

  A breakdown of versions stacks `other` at the bottom as "older", then each
  release line, the newest (`3.14.x`) in the accent color and older lines
  fading toward gray (with `bands` `release`, each exact release, `3.14.1`,
  fading the same way; a band is named at the right edge only where it is
  thick enough, so a long list names its thicker bands and the hover lists
  them all), and the prereleases on top, a band for each base version
  (`3.14.0 betas`, for `3.14.0-beta.11` and `3.14.0-beta.12`), the oldest base
  first, in the beta color fading the same way, with "older betas" below them
  for the bases beyond the newest five.
  Any other breakdown (contributors by region, issues opened by kind, a
  catalog's files by status) stacks its keys largest first in distinct
  colors, a neutral key in dark gray, and
  `other` on top in light gray. The stack is the one chart of what it adds up
  to: the installs total, the issues opened, the contributors, a catalog's
  files, are its top edge, and a field of the same summary that is part of it
  (contributors with a region) shows in its hover. The total
  is drawn as a line along the stack's top edge, with a dot at its newest
  reading; a day that has the total and no
  bands (a day's open issues no kinds could be counted for) is on the chart as
  the total alone, and the bands start on the first day that has them.

  A stack names its bands at the right edge in as much room as the longest name
  and count take (at 6.8 pixels a character), at most 40% of the chart's
  width; a name that does not fit is cut short with an ellipsis, never its
  count (the hover has the whole name), so no label runs past the card.

  Every chart's axis is fitted to its data: four grid steps (each 1, 1.5, 2,
  2.5, 3, 4, 5, 6 or 8 times a power of ten), the fewest that run from a
  multiple of the step a little below the smallest reading to a little above
  the largest (a little is 2% of their span), and never spanning less than
  5% of the largest reading, so a small wobble stays small. Bars and stacks
  include zero, so they start there; a line starts at zero only when its
  readings do, or when they all agree, and never below zero for readings
  that are never negative. So 67 contributors, stacked, top out
  at 80, and a percentile whose readings run from 24 to 73 runs from 15 to
  75.

  A section with no field (one whose content is report lines, such as a latest
  release) has nothing the page can show, so the page leaves it out, as it
  does a section whose charts have no rows on any date (a repo's releases with
  no downloadable files count 0 for every release, and a chart of lines draws
  only a band with a count); `trends.json` keeps both. A section with good
  rows whose charts are empty only in the chosen range stays, saying "No rows
  in this range.", so the range buttons keep working; one with no good row
  yet stays too, saying so. Projects group only the sections on the page, so
  a project with none there has no heading and no link, and a page with none
  has no nav.

- **Colors** follow the system's light or dark setting.

The page is the Python module's template: an inline style, the data, and one
inline script that draws each chart as SVG, escaping every text from the
data. Another viewer can replace it by reading `trends.json`.

`tests/test_trends.py` checks the data, and the page's text, structure and
embedded copy. `tests/test_trends_script.py` runs the page's script in Node on
made-up rows, with a stub of the browser's document, and reads the SVG it
draws (axes, joins, bands); it skips where `node` isn't installed.

## Known limits

- **A band's raw key.** The page titles breakdowns and shows a band's
  underscores as spaces, but the default summary and `trends.json` keep the
  key as it is in the data (`awaiting_verification`).
- **One rule, two places.** The viewer's script draws a chart of lines only
  for bands above 0. The renderer's `_has_rows` restates that rule to decide
  which sections the page leaves out, so a change to one must change the
  other (the function's docstring says so).
