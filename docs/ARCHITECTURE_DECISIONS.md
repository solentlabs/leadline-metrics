# Architecture Decisions

The decisions behind the design, and why.

## The runtime is stdlib-only

**Decision:** The installed package has no runtime dependencies: HTTP is
`urllib`, config and output are `json`. Dev and test tools live in extras.
`tests/test_packaging.py` fails if a runtime dependency appears.

**Rationale:** The scheduled job often runs on a NAS or small server whose
Python has no pip. The collectors need only HTTP, JSON and file I/O, which the stdlib
covers, so deploying means getting the package's own files onto the box.

## Python 3.8 and a JSON config

**Decision:** `requires-python = ">=3.8"`, and the config file is JSON.

**Rationale:** A NAS or small server may run Python 3.8 with no pip. 3.8 has
no stdlib `tomllib` (added in 3.11), and YAML needs PyYAML, so either format
would need a dependency such a host can't install. JSON needs none. The
costs: 3.8 has been EOL since 2024-10, and current dev tools no longer
support it (next decision).

## mypy pinned to 1.16.1; the 3.8 tests run on ubuntu-24.04

**Decision:** The `dev` extra pins `mypy==1.16.1` with
`python_version = "3.8"`. CI's Linux tests run on `ubuntu-24.04`, not
`ubuntu-latest`.

**Rationale:**

- **mypy:** 1.17 and later can't target 3.8. They warn and then check as a
  newer Python instead, so a call to a 3.9+ stdlib API would pass. 1.16.1
  checks against 3.8's stdlib stubs and runs on the dev venv.
- **ruff:** still accepts `target-version = "py38"`, so it is not pinned.
- **Runner:** setup-python's last 3.8 build (3.8.18) exists for ubuntu-24.04
  but not 26.04, which `ubuntu-latest` becomes in late 2026. If 24.04 is
  retired, the 3.8 job moves to the `python:3.8-slim` container.

- **pytest:** its own source uses 3.10+ syntax (`match`), which mypy
  targeting 3.8 can't parse. mypy skips it, so the tests see pytest and its
  decorators as untyped (`disallow_untyped_decorators = false`). `src` uses
  only typed stdlib decorators, so it loses nothing.

The cost is a frozen type checker. Revisit when the oldest Python a host
runs moves past 3.8.

## Credentials come only from the environment

**Decision:** Tokens and account IDs are read only from environment
variables, set in the scheduler's task. There are no command-line flags for
them and no secrets files. Everything else is a setting: in the JSON config,
or a flag for top-level scalar settings, and never a credential.

**Rationale:**

- **Flags leak.** Command-line arguments are readable by every user on the
  host through `ps` and `/proc/<pid>/cmdline`; a process's environment is
  readable only by its own user and root. The script this replaces has the
  flaw (it passes `GITHUB_TOKEN` as an argument to the contributor-locations
  step).
- **Secrets files are one more thing to secure and maintain** on the host; the
  task already holds the credentials.
- **The split keeps the scheduled task static.** Settings live in a file that
  can be synced, so adding a project is an edit on any machine, and the task
  never changes.

## Sources are named for what they read; project specifics are config

**Decision:** A source is named for the platform and the data it reads
(`pypi`, `github_issues`, `github_repo_activity`), never for a project or a
purpose. Anything specific to one user's projects is config: which repos,
packages, sites, labels and datasets, the patterns a source matches, and
the titles reports give to its data ("Similar projects", "Market
analysis"). A new platform (NuGet, Azure DevOps, ...) is a new source
package, added by the steps in [ADDING_A_SOURCE.md](ADDING_A_SOURCE.md).

**Rationale:** leadline is for anyone's projects, not only the ones it was
first built for. Naming by data keeps a source reusable for any purpose; the
user's intent belongs in their config and reports, where it can change
without touching code.

## Sources are modular by construction

**Decision:** A source is one package, `sources/<name>/`, and holds
everything of it: `spec.py` (its declarations, pure: `NAME`, `DEFAULT_FILE`,
`validate`, `files`, `summary` and what they use), `source.py` (the code that
makes requests and writes rows: `collect`, `check`, `retry` where it asks for
a retry pass, `ENV`, `LAGGING`), `__init__.py` (the contract's members),
`README.md` and `tests/` (its port tests, scenarios, golden and the shapes
the oracle holds its builders to). Adding or removing a source is adding or
deleting that folder, its config entry, and running `make docs`; nothing else
changes. The sources of one service sit together in a platform group, a
package that sets `GROUP = True`, so all the code of a platform is in one
folder, and the group holds what its sources share and nothing one of them
owns. `sources/github` is such a group: the API, the token's headers, the
repo and maintainer checks, the file-name fields, GraphQL queries and their
errors, timestamps, the access checks and their findings. Sources never
import each other; a source imports only the core (config, context, http,
store), its own modules and its group's shared modules, and a group imports
only the core and itself. A source's `spec.py` imports only `leadline.config`,
its siblings and its group's `spec`, never `http`, `store` or `context`, so
what a source declares can be read without anything that makes a request;
`source.py` is the only place its rows are written. Reports and health read
the output files, never a source's internals. Each source provides a
`validate()` that checks its config before anything runs, `files()`, which
names the files its entries write, `summary()`, its sections of the default
summary, `collect()`, `retry()` only where it asks for a retry pass, and a
`check()` that validates its config against the live API, reported in
`health.jsonl`. `tests/test_boundaries.py` enforces the import rules by
scanning imports. Breaking this rule needs a new decision here.

**Rationale:** The shell script this replaces keeps every collector in one
long file, where one project's assumptions leak into every section. A package
that holds a source's code, tests, scenarios and documentation makes adding
it one place to write and removing it one folder to delete, with no list
elsewhere to forget or leave behind. `check()` exists because a health check
that tests only whether a fetch succeeded reports "ok" for a source whose
config points at the wrong site, with zero pageviews every night.
`validate()` runs for every source before any collects, so a config edit with
a mistake stops the run at once, naming the source, entry and key, instead of
turning into failure rows that night. The same check refuses two entries that
write one file (`files()`), which would otherwise overwrite each other's rows
every night without an error. Code several sources repeat belongs in the
core or in their platform's group folder, where one copy serves them all and
a fix reaches every source at once, not in one source the others import,
which would tie them together. A platform's code in one folder means removing
a platform removes one folder. Splitting the pure declarations from the code
that makes requests lets the docs and the checks of a config use a source
without importing what it reads with. The docs that list the sources are
generated from the packages and their READMEs (`make docs`), so what the
source table, the list of sources and the credentials table say is what the
code is, and a test fails for any source whose README lacks what the
generator needs.

## Source packages are found, not listed

**Decision:** `sources.discover()` finds the sources: each package under
`sources/` whose name doesn't start with `_`, imported by name, in name order,
and each package inside a platform group, a package that sets `GROUP = True`
(it has no `NAME` and is never a source itself). A module that is not a
package is not a source, and neither is a `tests` folder in a group. A source
is keyed by its own folder's name, so a name found in two groups fails both.
There is no list to edit. A source is accepted when it has every member of the
`Source` contract (`NAME`, `DEFAULT_FILE`, `ENV`, `LAGGING`, `validate`,
`files`, `collect`, `check`, `summary`), each of its kind, and its `NAME`
equals its folder's name. Each import and check is isolated: one that raises
any exception is recorded in the `Registry`'s `failed` as
`<ExceptionType>: <message>`, and the rest load. A group that fails to import
is recorded under its own name, and so is each source package in its folder,
so a configured source in it fails like any other. A run treats a configured
source that failed to load as a crash: critical (`failed to load: ...`), exit
status 1, and no validate, collect, check, files or summary for it, while
every other source runs. A failed source with no config entry is one warning
line. An entry naming no source is still `unknown sources`, exit status 2.
While one is missing, the `projects` and `pages` are not validated, since
their sections' titles are incomplete (a warning says so); trends builds
without the missing sections. The runner and the command call `discover()`
once, and tests pass a `Registry` in its place. The docs generator reads the
same registry, and fails for a source that failed to load.

**Rationale:** A hand-written list is a second place a source must be
registered, and a missing line fails only in a test. Finding the packages
makes adding or removing a source touch one place. The nightly run is the
only copy of the data, so one broken source (a bad edit, a syntax error) must
not stop the others, and must still turn the run red where the user looks: a
critical in `health.jsonl`, exit 1, as for a crash. Requiring the name to
equal the folder's keeps the config key, the package and the documentation in
step, and names the mistake when they differ. Only packages are sources
because a source is more than code (its README, tests and golden sit beside
it), and one shape for every source keeps discovery and the build rules
simple.

## A run reuses its successful answers

**Decision:** Within a run, the core `http` keeps each successful answer to
a GET and serves it again to an identical GET (the same URL and headers), so
two sources asking for the same thing make one request. A failure is never
kept: a retry, or another source's request, is sent again, and one source's
failure never becomes another's. An answer `http` returns is kept even when
its source can't read it, so asking again in the same run gets the same
answer. A POST (GraphQL, SQL) is always sent. The
answers live as long as the run's `Http`, one per run, and nothing persists.
Each reuse logs one line. Sources don't change or know. A retry pass
(PyPI's) asks again only for requests that failed, never for an answer the
run got, so reuse never answers it.

**Rationale:** Several sources read the same GitHub data (the traffic
snapshot and its daily rows both read views and clones; `check()` rereads
what collection read), and GitHub's rate limit is shared across all of
them. Asking once spends less of it and gives both sources the same
numbers. Keeping the reuse in `http` keeps sources independent: none knows
another exists, and each still runs alone in its tests. Failures are left
out so that reuse never turns one transient error into several. A GET is
safe to answer twice by HTTP's own rules; a POST's answer is the query's,
and reusing one would need a rule per API. One short log line per reuse
lets a reader of the log see why a source made no request. An answer a
source can't read is a parse failure, not a rate limit, so asking again in
the same run would spend the API's budget on the same answer; the retry
pass is for requests that failed.

## Shared tables are defined once and named by the entries that use them

**Decision:** Config that several entries use, such as a region table, is
defined once under the top-level `shared` setting and named by each entry
that uses it (`"regions": "<name>"`). Loading the config puts the named
table in the entry, so a source receives its own entry and nothing else.

**Rationale:** The script defines its region patterns twice, once for issue
text and once for profiles, and the copies can drift. A table named once
has one home, and several repos can share it. Resolving the name at load
keeps sources modular: a source never reads another part of the config,
and a name with no table is a config error before anything runs.

## check() runs on every run, after collection

**Decision:** Every run, scheduled or manual, calls each enabled source's
`check()` after all collection is done, and records the findings in
`health.jsonl` as `checks[]`. There is no flag to turn it on or off. A
source skipped for its credentials isn't checked, as it can't
authenticate. A check never writes rows; a check that fails or hits a rate
limit reports a finding and nothing else, and a check that raises is a
bug, critical as a crash in collection is.

**Rationale:**

- **Only a run can check.** Credentials exist only in the scheduled task, so
  a check from a workstation can't authenticate. A daytime manual run on the
  scheduler's host is how a config change gets tested, and it checks everything.
- **Data first.** API budgets are shared between collecting and checking;
  running checks last means a tight budget is spent on data.

## A failure never replaces good data

**Decision:** A failed or rate-limited fetch never overwrites or deletes an
existing `ok` row, or a partial row's good parts. It writes a failure row
(`status` other than `ok`) only where no good or partial row exists for that
key; a newer failure row replaces an older one. A later successful run
replaces the failure row, or backfills it where the API keeps history.

**Rationale:** Runs repeat: a daytime test run replaces the day's row, and
the scheduled run is the last one of the day. A test run that hits a limit
must not undo the data a good run already stored. The script's usage writer
already works this way; its Cloudflare collector overwrites a good row with a
failure, and the port doesn't keep that.

## A failure is recorded as a failure

**Decision:** An answer a source can't use (a body that isn't JSON or
isn't the shape the source reads, a missing or null part it needs) writes a
failure row for the date: a `status` other than `ok` and a `reason`, with no
counts. It never writes nothing, and never zeros: a count, flag or list the
answer lacks is not 0, false or none. The `reason` names what is missing
(`views response is missing uniques`), so a reader can tell what to look for
and the night can be recovered. A status keeps one
meaning: `cloudflare_web`'s `no_account` means only that the account filter
matched nothing, and a malformed answer, `data` null included, is
`parse_failed`. Every reader of the rows (health, reports) skips failure
rows. On such a night the port's row differs from the script's, which
writes no row for some of these answers.

The date is the one the source's newest row should have (its expected
date): the run date, or yesterday for a lagging source. A source that
writes a row for each day of a window, such as `github_traffic_daily`,
uses an answer whole or not at all: one part it can't use, such as a day
without a timestamp, makes the answer one it can't use, and it writes the
failure row for yesterday, never part of the window.

**Rationale:** A night with no row reads as a missed run, and a night of
zeros as a quiet one; neither says the API answered with something
unusable. A failure row keeps an errored day null, so zero means zero,
health counts the failure in its streak and gaps, and the next successful
run replaces or backfills the row (a failure never replaces good data).
Health judges a source by its expected date, so that is where its failure
shows. Writing the usable part of an answer would guess at what the rest
meant: a clones day that can't be placed would leave its day's clones read
as zero. The window's API keeps its days, so the next good run backfills
every one the failed night missed.

## A partly failed row keeps its good parts

**Decision:** A row built from several requests has required parts, without
which there is no row (a repository's own answer), and optional parts,
which the row can do without (its traffic, its lists of issues or
releases). A source draws the line where the script does: a part the
script can't write a row without is required; one it counts as zero or
none when it fails is optional. A required part that fails, or that the
source can't use, writes a failure row (a failure is recorded as a
failure). An optional part that fails, or that the source can't use, is
null in the row, never 0, and the row keeps every other part, the script's
fields in the script's order, with `status` `partial` and a `reason` naming
each part that failed (`store.write_partial`). Each part is read whole or
not at all: an item inside it the source can't read, such as an issue
whose user is null, makes the whole part unusable, not the item skipped.

One part is a list of people, not read whole: `github_contributor_locations`
reads a profile per person, and one it can't read is that person's alone.
Their entry keeps its login, with its profile's values null and the
source `profile_failed`; every profile read is kept, the totals count only
those, and the row is partial, its reason counting the people unread. A
person is not a guess, as an item of a list the source counts is: each
entry says whose it is and whether it was read.

A partial row is a failure row: health counts it as a failed night, no
failure row replaces it (a failure never replaces good data), and the next
good row replaces it. A later run of the same day keeps what it got (a run
never erases what it didn't get). An error waiting can't fix on an optional
part's request is critical, as on any request.

**Rationale:** The script writes a failed part as 0, which reads as a quiet
day, or drops the whole row over one list it couldn't read, losing the
parts that did land. Null keeps the difference between none and unknown,
and the parts that landed are kept for anyone reading the file. Marking the
row partial keeps health honest: the night is not a good one, so the
source's streak and gaps show it, and a later good run is free to replace
it. Reading a part whole, as the daily traffic reads a window, avoids
guessing: counting the issues without the one whose author is unknown
would give a count that is neither the true one nor marked as wrong.

## A run never erases what it didn't get

**Decision:** A run writes what it got and never erases what it didn't.
When several runs read one day, a run's good values overwrite the stored
ones; for each optional part this run failed to get, the value the stored
row holds stays; anything still missing is null, and the run's log says
why (the source's WARN line for the failure; the row gets no extra field).
The row is `partial`, its `reason` naming each failure that still leaves a
part missing, until every part is filled, by this run or an earlier one;
then it is good, with no `status` or `reason`, as the source's good rows
are. A failure row never replaces a good or partial row, and a newer
failure row replaces an older one. A good row replaces anything, and on a
day before the run date a value it changes is a correction (every
correction is recorded).

Store keeps the rule (`store.write_partial`): the source says which fields
each failure left null, and store fills them from the stored good or
partial row for the key where that row knows them (a good row's value, null
included; a partial row's non-null one), a part whole or not at all. A
source whose part is a list of people (see "A partly failed row keeps its
good parts") merges them itself, each person this run couldn't read taking
their readable entry from the stored row
(`store.stored`), and writes the merged row through `store.write_merged`,
where `write_partial` ends too: good once nothing is missing, else partial.
A merged row can hold values from different runs of the same day; its
`captured_at` is the last write's. Sources whose API keeps history re-read
past days and overwrite them with good data; a source that reports only
"now" keeps the best value it got that day.

**Rationale:** Runs repeat within a day (a daytime test run, then the
scheduled run), and each can lose a different part to a rate limit. Without
the merge, the later run's partial row would drop what the earlier one got,
and a later failure row would drop a partial row outright, so the file
would end the day knowing less than the runs learned. Keeping the stored
value for a failed part, and never over a value this run got, leaves each
value the newest one read. Naming only what is still missing keeps the
`reason` true of the row as written, and a row whose every part is filled
is as good as a row read whole. Keeping the rule in store means no source
can merge differently or forget to; and writing a filled row through
`write_good` keeps corrections recorded on that path too.

## One failed night recovers; consecutive failures are critical

**Decision:** A source that fails one night is expected to recover on the
next run: it backfills where its API keeps history, and otherwise its next
good row resumes the series. A source with no good row on two or more
consecutive nights is **critical**: health reports it as critical, apart
from ordinary warnings, so it gets fixed right away.

An error that waiting can't fix is critical on its first night and is never
retried: the API rejects the credentials (401, 403), or the thing asked for
doesn't exist (404, such as a misspelled package name). A spent rate limit
is not one of these, even where an API answers it with 403: a 429, or a 403
that carries `Retry-After` or `X-RateLimit-Remaining: 0`, is recoverable
(`HttpError.rate_limited`), and only a 401, 403 or 404 without those is an
error waiting can't fix (`HttpError.unfixable`). Over GitHub's GraphQL API,
which answers most errors with a 200, an error whose type means the same as
a 404 or 403 (`NOT_FOUND`, `FORBIDDEN`, `INSUFFICIENT_SCOPES`) is one
too; any other, a spent rate limit (`RATE_LIMITED`) or an error with no
type, is recoverable (`github.GraphqlError.unfixable`). A GoatCounter 404
is recoverable, and retried like any other failure (the request names it
`recoverable`): GoatCounter answers 404 at times for a site that exists and
the token can read, and a wrong site still shows, as its check's error that
night and as critical on the second failed night. A source that crashes on
an unexpected exception is a bug, which waiting can't fix either: it is
critical on its first night, and the next source still runs.

The exit status follows: a run exits 1 only when something in it is
critical, and 0 when its failures are recoverable, their failure rows still
written and recorded by health. (A bad command line or config exits 2, and
nothing runs.)

**Rationale:** Errors happen (rate limits, outages, a daytime test run), and
the design absorbs one bad night without anyone acting. Two bad nights in a
row means recovery isn't working, and every further night risks data that
can't be recovered at all. A rejected credential or a wrong name fails the
same way every night, so waiting only loses data, and retrying it spends
API budget for nothing. An exit status of 1 is what a scheduler reports to
someone, so it is kept for what needs someone; exiting 1 on every
recoverable failure would train them to ignore it.

## Critical sources surface at the top of SUMMARY.md

**Decision:** When anything is critical, SUMMARY.md opens with a line for
each entry of the run's `critical[]` in `health.jsonl`, naming the source
and why: for consecutive failed nights, how many and the night the streak
started. It is an ordinary report block at the top of SUMMARY.md, read from
the health row the run has just written (reports are config-driven marked
blocks). When nothing is critical, the block is only its markers.

**Rationale:** SUMMARY.md is the page read every morning, so a critical source
is seen the next morning without a new service or new credentials. A run
that fails outright doesn't rewrite SUMMARY.md at all, which shows as a stale
date on the same page. As a block it needs no code of its own, and it shows
exactly what health recorded.

## Reports are config-driven marked blocks

**Decision:** leadline writes its reports as marked blocks in any Markdown
file the config names. It owns the lines from `<!-- leadline:start NAME -->`
to `<!-- leadline:end NAME -->` and never changes text outside them. A new
block goes at the top or the bottom of its file, as the config says; after
that it is rewritten where it is. Each block is a table (a row a day or a
week, each column a field and its aggregates) or lines (a value, a
comparison, a series, or a named aggregate), from any row file. A line, and
a table cell, is shaped by config: a template of the fields its kind gives,
parts joined into one line, conditions, and cleanups of a value's text; a
field without a value leaves out what names it. What a report needs beyond
these is a named aggregate, generic in code (a version breakdown, the items
of a list, releases' downloads), never code for one project. Health runs
before the reports, so a block can read the run's own health row. Each
block is built on its own: one that fails is left as it is, logged and
critical, and the other blocks are written. A marker missing, there
twice, out of order, or with another block's marker between its pair is an
error for its block, never a guess.

**Rationale:** The script builds TRENDS.md and two tables in a project's
page with code written for one project: the tables are found by their
header text, and TRENDS.md is rewritten whole. Blocks let a user keep their
own writing around the numbers in the same file, and config makes the
numbers anyone's: which files, fields and aggregates, and where they go.
A hand-written report puts several values on one line, some only where
they mean something (`7d: 96 (+5) · 30d: 96 (+5)`, a range only where it
varies); templates and parts let config say that for any project, where
fixed forms would push each project's wording into code. The markers say
which text is leadline's; a marker that doesn't pair up
could put the user's text inside what leadline would rewrite, so it stops
instead. Running health first puts the run's own critical sources on the
morning page with no second channel. A failed block is critical because
waiting mends neither a broken marker nor a bug, and exit 1 is what reaches
someone (one failed night recovers; consecutive failures are critical);
`health.jsonl`, written before the reports, doesn't list it, so the log and
the exit status carry it.

## The default summary is config built from each source's summary()

**Decision:** A config with no `reports` gets a default: one block in
`SUMMARY.md` in the output folder, the run's health lines, then a section
for each source entry, in config order. Each source's `summary(cfg)` gives
its sections as `config.Section`s (built with `config.entry_summary`, or
each on its own), naming the fields it shows and how each shows
(`config.Shown`); the runner collects them through the registry,
`config.section_lines` renders each as report lines, and `reports.default`
puts the health lines above them. The existing engine renders it, and
`leadline default-reports` prints it as a `reports` setting to paste and
change. An absent `reports` means the default;
`"reports": []` means none; a config with its own blocks gets only those.

**Rationale:** A new user should see their numbers after the first run
without learning the report language. Only a source knows which of its
fields matter and whether a field is a running count or a day's count, so
the choice lives in the source, and a new source brings its summary with
it. Returning report config, not text, keeps one renderer, and makes the
default the starting point of a user's own reports. Sections, not report
lines, are what a source gives, so trends charts the same fields without
reading them back out of report config. The helpers live in `config`
because sources may import only the core, and they build config.
Telling absent from empty keeps an opt-out that needs no flag.

## Reports are built from the files alone

**Decision:** `leadline report` builds the reports, the default summary,
`trends.json` and the trends pages from the output folder's files and the
config alone: it makes no request, needs no credential, collects and checks
nothing and writes no health row. `leadline run` ends with the same step,
one function (`runner.build`). To keep the report path free of capture, each
source's `spec` (pure declarations) is imported without its `source`
(`discover(specs_only=True)`); a source's and a group's `__init__` import
nothing of their own and find their public names lazily.

**Rationale:** Capture and presentation are separate duties. A page changes
when the config's `reports`, `projects` or `pages` change, or when its
drawing does, and none of that needs a night's collection: with the step
only inside `run`, redrawing a page took network, credentials and a health
row, and could not be done at any hour without disturbing the APIs. The rows
and `health.jsonl` already hold everything a page shows, so reading them
alone makes a redraw safe and fast, and one shared function means a run and
a report cannot differ. Loading only the `spec` makes the separation a
property the code has, which a test checks (no `source` module and no HTTP
is imported), not a promise.

## The trends viewer draws its own SVG

**Decision:** Every run writes `trends.json`, the history of each field
the sources' summaries show, in a documented, versioned format, and
pages holding a copy of that data that draw each chart as inline SVG
with a little plain JavaScript: no charting library, nothing loaded from
anywhere. They offer values on hover and a range (30 days, 90 days, a
year, all), and follow the system's light or dark setting. The config's
`pages` give each portfolio of projects its own page, holding only its
projects' sections and the health that concerns them; without them, one
`trends.html` holds everything. A breakdown stacks counts, so its top
edge is the total: a version breakdown puts each base version's
prereleases in a band of its own, and the total it adds up to has no
line of its own. A source's summary says which fields the pages chart
alone (`charted`), which total a stack caps (`into`), and which keys of
a stack mean none of the others (`neutral`), which fields only the pages
draw (`charts`), and which list to total and by what key (`by`), how a
breakdown is drawn (`lines`) and whether it holds prereleases
(`prereleases`), and which fields are lines of one chart (`chart`), so
the pages' rules hold no source's words. A total over a list counts each
item at the greatest it has had, so an item that leaves the list never
lowers it; the totals are breakdowns, banded as any breakdown of
versions. A chart that starts later than its section says when it was
counted from.

**Rationale:** The data format is the product: other tools, a database
or a website, load `trends.json`, so it is stable and documented, and
the page is one reader of it, replaceable without touching the data.
Drawing the few chart kinds the summary's fields need (a line, daily
bars, a stack of counts) takes a few hundred lines, so the runtime stays
stdlib-only and the page opens from disk with no server, no network and
no dependency to vendor or update. A page opened from disk can't read
the file beside it, so it carries its own copy. A page per portfolio can
be shared on its own, so it carries none of another portfolio's numbers.
A stack of counts shows the total and its parts in one card, so a
total's line beside it would draw the same numbers twice, and the hover
gives each band's share. A base version's prereleases are one band,
because what matters is how many are testing each coming release, not
which build. A chart that begins weeks after the others would read as
flat history, so its note says the counting began later; the rule reads
the data, so it holds for any source. What a source's entry excludes (a
release left out of what is shown) is left out where the rows are read
for display, by file, so the default summary, every report block that
reads the file and the trends agree, and the file holds everything
GitHub gave. A release can vanish from a releases list (deleted, or past
the 100 the request reads), and a total that fell with it would chart a
loss of downloads that never happened; carrying each release at its
greatest count keeps the total a running count, at the price of not
seeing what a release outside the list gains later. A person who
downloads several versions is counted in each, so a stack of the
versions' downloads would add up to no number of downloads; each version
is a line of its own on one axis, none summed with another, and a
release has no count before it is first listed, so its line rises from 0
there, whatever its `published_at` says. Each line has a translucent
area under it, the newest release in front, so the eye reads each
version's size against the others without any being summed. A chart of
days since a release puts releases side by side at the same age, which
the calendar hides; it draws only releases that began after the data
did, because an earlier release's first days were never read, and it
does not add the older ones into one line, since a sum of different
releases at one age means nothing. A beta's downloads say little about
adoption of a release, so the release chart leaves prereleases out.
Fields of one unit and scale, such as a median, a mean and a 90th
percentile of hours, are read against each other, so they share a chart
and an axis instead of three charts with three scales. The chart fields
of a list are worked out from the stored lists, so the rows stay as the
script wrote them.

## Zone traffic is kept as counts

**Decision:** `cloudflare_zone_traffic` keeps a zone's HTTP traffic as a row a
day of counts: totals, and the number of requests by country, status code,
path, host, browser, operating system, device type, data center and the
like. It never asks for, and leadline never stores, a dimension that
identifies a visitor: a client or origin IP address, a session or user id, a
ray id. There is no setting to switch one on.

The daily dataset (a year of exact totals) is read in full on the first run
and filled back to the first day Cloudflare has, each filled day marked
`backfilled`. The per-request dataset (31 days, sampled by Cloudflare) is
read for the last 28 days (a run west of UTC is a day ahead, and Cloudflare refuses a start older than 31 days), one query for each list, so a list that fails
leaves only its own field null and the row `partial`; its values are filled
where a row lacks them and never changed, since a later sample would only
move them, while the daily totals are corrected to what Cloudflare says now,
each correction recorded.

**Rationale:** Leadline exists to keep what a platform forgets, to watch
trends and learn about sites; a log of who visited is a different tool, and
the files are plain text in a synced folder, with visitors in many
jurisdictions. Counts by what was asked for, where from and with what answer
give the traffic's shape (scanners, bots, countries, errors) without a
person in them. Reading the year once recovers history Cloudflare would drop,
and one query a list keeps one bad list from costing the rest.

The cost: a count cannot be traced back to who made the requests, by design,
and a path that carries a personal value (an email address in a URL) is kept
as it came; keeping such values out of URLs is the site's job.

## cloudflare_web re-reads and corrects its last 30 days

**Decision:** Each run re-reads the 30 complete days before the run date
(yesterday the last), in the same query as yesterday's row. Within them,
from the file's first row on, a day is healed as the script heals it: a
day with no row, a failure row, or a row read under another site tag (or
under none, from before rows recorded one) is filled from the answer and
marked `backfilled`. leadline also corrects: a good row read under the
configured tag whose numbers have changed gets the latest reading. Rows
older than the 30 days are never touched. Each correction is recorded, as
every source's is (ADR: every correction is recorded), a filled day that
replaces a good row read under another tag included.

A filled day's row holds the date, the site, the site tag, `ok`, the day's
`pageviews` and `visits`, and `backfilled: true`. A correction replaces
those two numbers and keeps the row's other fields, which describe the week
read on its own night. Days before a file's first row are not filled, since
the series hadn't begun (a gap lies between a file's first and newest
rows), and a day the answer leaves out had no page loads, as the script
reads yesterday.

**Rationale:** Cloudflare's numbers are sampled and can settle after the
fact, a failed night otherwise leaves a permanent hole although the API
keeps the history, and a site tag set wrong reads every night as zero:
recording the tag each row was read under lets the next run with the right
tag replace those rows. Filling as the script does keeps the rows the same
while both run; correcting changed numbers too, in the one window, keeps
the recent data right without anyone acting, and recording each correction
keeps drift visible. Only a successful re-read changes a row (a failure
never replaces good data): an answer Cloudflare refuses, a range too long
among them, is a `graphql_error` row for yesterday, recoverable, and the
next good run heals. History beyond the 30 days stays fixed.

## Analytics Engine datasets are read as totals, one dimension at a time

**Decision:** `cloudflare_analytics_engine` reads a Workers Analytics
Engine dataset as daily totals, a row per UTC day: `date`, `dataset`,
`status`, then each field the config names, then `captured_at`. A field is
either the day's events totalled by one dimension (an object of each
value's total, or of a `double` column's sum) or the day's count of events
where a column is set (a number). What the dataset's columns hold, the
values each dimension allows, and which columns hold a per-person id
(`private`) are config; the code knows no dataset. Whatever the config,
the code keeps three rules:

- A private column is never selected, grouped by, written or logged; a
  count only tests it for being set, and a dimension can't be one.
- No query groups by more than the day and one dimension, so no total
  crosses two dimensions.
- Every value read is checked against its dimension's allowed values (a
  closed list, or a pattern the whole value must match), and a dimension
  that may be set only where another has certain values (`only_when`) is
  checked by counting the events that set it elsewhere, without reading
  them. A value outside the layout is left out of the row and never written
  or logged; the source records it critical, with the dataset, the
  dimension and a count only.

A dataset's layouts are listed oldest first, each but the newest ending at
a UTC time, when the app writing the dataset changed what its columns
hold. Each is read only for its own time, and a day that holds a switch
adds up what each layout read of it. Every query of a read is required: one
that fails, or an answer that can't be read, writes no day and a failure
row for yesterday (a failure is recorded as a failure). Each run re-reads
the last `refetch_days` days and rewrites them, a quiet day as a row of
zeros, each changed good day recorded as a correction.

**Rationale:** The rows land in a folder that may be synced or backed up,
whose version history outlives any pruning, so only what is safe to keep
forever goes there. A total of one dimension says how many, never who;
totals of two crossed (country by browser by version) can narrow to one
person, and a per-person id is a person. Keeping the rules in code means no config
mistake can break them, and the layout check catches an app that starts
writing something it shouldn't (a city, an address) before it reaches a
file: the value is dropped, the run exits 1 so someone looks, and the
finding says only how many. Layouts in config keep the history continuous
across a switch: the script's layout stays first with its `until`, so its
file goes on with the same rows, and adding up the switch day keeps that
day's events whole. Every query is required because the script's two are,
and a row missing one dimension's totals would read as a quiet day for it;
the next night re-reads the window.

## A closed community issue is answered

**Decision:** `github_response_times` counts a community issue as
answered at the maintainer's first comment or at its closing, whoever
closes it, whichever comes first, and its response time runs from its
creation to that moment. Every field of the row follows from it: answered,
never answered, the hours, and the shares within each bound. Past rows
are recomputed under it (derived history follows today's rules). The
script counts only the comment, so where a community issue is closed its
row differs, a divergence its scenario lists.

**Rationale:** A fix that closes an issue is a response. A maintainer who
answers with code, a commit or pull request that closes the issue, or who
closes a duplicate without a word, has answered it; counting only comments
reads that as never answered, or as a longer wait than there was, and
counts short the maintainer who answers with code. The query already reads
each issue's closing time, so it costs no request.

## Every correction is recorded

**Decision:** Any write that replaces a good row for a date before the run
date with a good row whose values differ, `captured_at` aside, is a
correction, and health lists it with the source, file, date and the values
that changed, old and new. Store records it, not the source: every source
writes its good rows through `store.write_good`, `store.write_window` or
`store.recompute`, which compare what they replace and call
`ctx.record_correction`, and no source calls the raw write paths or
`ctx.record_correction` itself (`tests/test_boundaries.py`). A rerun's row
for the run date, a filled missing or failure row, a backfill's added
fields (past values a source can rebuild are filled, never changed), and
a rewrite that changes nothing are not corrections.

**Rationale:** Past days change for more than one source:
`cloudflare_web` re-reads its 30 days, `github_traffic_daily` refreshes
yesterday, a same-day rerun rewrites a lagging source's yesterday, and
`github_response_times` and `github_contributor_locations` recompute
every past day (derived history follows today's rules). Drift in any of
them is only visible if every one is recorded, and a rule
each source had to remember would be skipped by the next one. In the core
it holds for every source, today's and tomorrow's. The run date's own row is
still being counted, so a rerun replacing it is not drift.

## An answer in an issue form is the strongest region signal

**Decision:** A region table may read issue forms: `answer`, a regex
that captures a person's answer naming their internet provider, and
`model_answer`, one that captures the modem they report. A person's
answer decides first: its region is the first rule it matches, `exact`
rules (whole answers too short to be keywords), then `text`, then
`location`. A modem sold in only one country, a `models` rule, confirms
the answer or decides where no rule matches it. For a person with no
profile location, `github_contributor_locations` takes the region from
their forms before the text of their issues, and `github_issues`' queue
gives each listed issue its form's region before its text. A profile
location still comes first. Only the region is kept, never an answer.
Each run's `check()` reports, by count and never by login, the answers
no rule matches, unless a `models` rule matches the modem in the same
form, which places the person; each rule an answer matched that gives
another region than a person's profile location; and each `models` rule
that gives another region than the answer in the same form. leadline
never edits the config: the table grows when its owner adds a rule from
those findings. The script reads only the first 500 characters of each
issue and no forms, so where a form gives the region, leadline's differs
from the script's; the scenarios that show it have reviewed rows, as the
script has no oracle for them.

**Rationale:** An answer to "who is your provider?" is the person's own
statement, where a keyword in free text may name a modem's or someone
else's provider, and the form puts it past the 500 characters the script
reads. A modem an internet provider issues in one country names that
country too, but less surely than the person's own answer, so it
corroborates the answer and fills in where the answer is ambiguous (a
provider in several countries). A disagreement between the two is
reported, never resolved silently, so a wrong rule surfaces. A profile
location is the person's own statement of where they are, so it stays
first, and it is what tests the table: a rule that disagrees with one is
likely wrong. Reporting unmatched answers and disagreeing rules lets the
owner see what the table lacks, and an answer whose form's modem places
the person lacks nothing, so it isn't reported; changing the table by
itself would turn one unusual answer into a wrong rule for everyone.
Counts without logins keep the findings, which the trends page shows,
from pointing at a person.

## A person's region from their own comments, bounded by what the row remembers

**Decision:** An entry of `github_contributor_locations` that sets
`comments` gives a person still without a region one from their own
comments, under the table's `text` rules: the first 500 characters of each
comment on the issues the unplaced people opened, the same limit as an issue
body. A comment places only its author. The source is `comment_inference`.
The row remembers what was read in `comments_read`: a fingerprint of the
table's `text` rules and each issue's `updated_at` when its comments were
read. With no such row, or one read under other `text` rules, every issue of
the unplaced people is read once; after that, at most one issue a run, an
unread one first, then one changed since it was read. What a run found stays
in the people's entries, so no comment is kept. A person a later run places
by a profile, form or issue text loses the placement. Each night's log lists
the people still unplaced with their issue numbers, for a person to add
rules from.

**Rationale:**

- **A comment is its author's statement.** Another person's comment names
  another person's provider: a maintainer's link to a forum thread or a
  user's report on someone else's issue would place the issue's opener in a
  region they may not live in. Reading only the author's own words keeps the
  signal as strong as an issue body's.
- **The first 500 characters** match how issue text is read and leave out
  the logs and captures that long comments hold, whose bytes match short
  keywords by chance.
- **Requests stay bounded without a new file.** A comment can't be asked
  for in bulk, so each issue is a request. The row's own state tells a run
  what it has read, so the pass happens once and a night adds one request
  at most. The rules' fingerprint makes a new rule take effect: the comments
  are read again under it, which is the only way a rule can place someone
  whose comments were already read. The `updated_at` lets a later comment
  on a read issue be seen, one issue a night.
- **Off unless set.** The script reads no comments, so an entry without
  `comments` writes the script's rows and makes the script's requests.

The cost: `comments_read` is a field the script's rows lack, on the run
date's row only (past days carry the placements, not the state). A person
whose comments name no provider within 500 characters stays unplaced, and
one issue a night is slow to reach a new person's issue when several are
waiting.

## A region from the zone in an attached file, bounded by the row

**Decision:** A region table may read files people attach to their issues,
with three settings, all config: `attachment`, a regex with one group that
captures the URL of an attached file in an issue body (a file name pattern
such as one for a diagnostics export); `attachment_value`, a regex with one
group that captures a value in the file's text (a time zone field, matched
as text, not parsed, so a file that is not valid JSON still reads); and
`zones`, rules in the form of the other rules (a region and lowercase
keywords) that map a zone name to a region, so `region-a/city-1` gives one
country and `region-a/city-2` another and a neighboring country's zone is
never read as the first's. A zone is a clue like the others, in the order:
the answer to the provider question, then the zone, then the reported modem,
then issue text and comments. The zone names a region where the answer did
not, and outranks a `models` rule or issue text where they differ. Only the
zone's region is kept: the value is matched, never stored, and nothing else
in the file is read, kept or logged. The source is `timezone_inference`.

A person's profile location, where it gives a region, and the answer to the
provider question, where it gives one, are checked by the zone: a zone rule
that gives another region than the profile's or the answer's is reported, and
that region stays. A zone that differs from the region a modem or issue text
gave, which it replaces, is reported the same way. Each is reported in the
log by the keyword that matched, the two regions and which clue the other one
is, never by login, and never settled silently. An answer's region and a
`models` rule's are reported as before.

Files are fetched from the link in the body, without the GitHub token (the
file is on another host of the service, and the link is public for a public
repo), through the core's http client, which keeps its one-request-per-URL
reuse. Links are found in issue bodies the source already reads whole, so
finding them costs no request. Every person whose profile was read gets the
first file of their issues that matches `attachment`, newest issue first,
placed by the zone or only checked by it as above; one file per person is
enough, as a person has one zone.

**Rationale:**

- **A zone is a statement of where a device is.** Its value is the person's
  own setting, far less ambiguous than a modem sold in several countries, and
  it is present where an issue form has no provider question or a person
  answers it with a name no rule matches. A zone names a place, so a rule is
  a specific zone, not a prefix: a continent's other countries share it.
- **Config, not code.** The file's name, the field and the zone rules belong
  to the project whose files they are; the source reads any text file and a
  regex, as it reads any issue form (an answer in an issue form is the
  strongest region signal). The `zones` rules reuse the keyword rule and its
  checks (lowercase keywords, first rule wins).
- **Requests stay bounded without a new file.** A file is one request. The
  row's `timezones_read` holds the rules' fingerprint (`attachment`,
  `attachment_value` and `zones`) and the URLs read, so with no such row or
  one read under other rules every person who needs a zone is read once;
  after that, at most one file a run, an unread one first, then one whose
  link changed, the lowest issue number first. A
  failed read isn't recorded, so the next run asks again within the same
  bound, but a file the service no longer has (404 or 410) is recorded as
  read, so a removed file never holds up the others, and no failed read is
  critical. A new rule takes effect because the files are read again under it,
  the only way a rule can place someone whose file was already read. What was
  found stays in the people's entries, so no file is kept.
- **Nothing else is taken from a file.** An attachment may hold personal
  data; only the captured value is looked at, and only its region is kept.
- **Off unless set.** An entry whose table has no `attachment` makes the
  script's requests and writes the script's rows.

The cost: `timezones_read` is a field the script's rows lack, on the run
date's row only, as `comments_read` is. A file attached in a comment, not a
body, is not seen (reading it would cost a request per issue). A person whose
file has no zone, or one no rule names, stays as they were, and a link the
service no longer serves (a private repo) is logged and tried again within
the bound. A change in who is checked by the zone changes
placements on the next run, and past rows follow (derived history follows
today's rules).

## A region the maintainer knows is configured

**Decision:** A region table may set `known`, an object of a person's login
to a region code the table's rules give (a code no rule gives, an empty or
non-string one, or a login twice ignoring case is a config error). It is read
from the config on every run, so it holds through each run's rebuild and the
recompute of past days, and a login is matched ignoring case. A person whose
profile was read and has no region from it gets the known region, with the
source `configured`. Order of evidence: a profile location, then the known
region, then the answer to the provider question, the zone, the modem, issue
text and comments. A known region is never changed by a clue: a profile region,
the answer's region, a modem's region or a zone's region that differs from it
is logged, by the two regions and the clue, never by login (`WARN: a known
region says A where a profile says B (n)`; a zone's line is the zone's form,
with a known region as the clue). A known person's attached file is still read,
as an answered person's is, only to check the zone; their comments are not read,
as they are placed. A person whose profile no run could read stays unread, as
no clue outranks a profile that may place them. Off unless set.

**Rationale:**

- **Knowledge the tool can't derive.** A maintainer knows where some people
  are from outside the issues, and no rule or file gives it; it is evidence like
  any other, so it takes a place in the order, below the person's own statement
  (a profile) and above what is inferred.
- **In config, not in the data.** Who a maintainer knows is theirs and private;
  the config is where such facts live (config, not project logic), and nothing
  about a person is written to a file beyond the region their row always held.
- **Labeled as such.** The source `configured` tells a reader of the rows
  that the region is the maintainer's word, not a reading of the person's
  words, as each other source says what it read.
- **Read every run.** A region in the config needs no state: the rebuild and
  the recompute of past days place the person the same way each night, and
  removing a login from `known` takes the region away on the next run.
- **Disagreements are findings.** A known region that disagrees with a clue
  means the clue's rule, or the known entry, is wrong; reporting it by regions
  and clue, never by login, lets the owner look without a log naming a person
  (an answer in an issue form is the strongest region signal).

## Core ships the places everyone shares; the config holds what is its own

**Decision:** Knowledge that is true for everyone is in core, and what is
particular to one project is in the config. Core holds, as vendored data
(`countries.json`, `zones.json` and `states.json`: data, not code), the ISO
3166-1 alpha-2 countries and English names, the IANA time zone database's
`zone.tab` (one country per zone) and its `backward` links (old zone names),
and the USPS abbreviations of the US states and the district. `make places`
regenerates each file from its standard, sorted, with its sources, release and
the day retrieved recorded in it, so a refresh of one standard changes only its
file and shows as a diff to review; nothing fetches at run time and nothing
runs on a schedule. A region table's own rules come first and core fills the
gaps, automatically and with no flag: core adds `location` rules (country and
state names, state abbreviations) and `zones` rules (a zone's country, in a
table that reads attachments). Provider keywords, answers, models and known
people stay config-only. Core's country and state names match as whole words,
longest first; a state wins a name it shares with a country; a state
abbreviation matches only as the whole token ending a field of a location,
after every name. A table's own keywords keep their substring match. A region
in a rule or in `known` must be a code core knows: an ISO 3166-1 code, `UK`
(the project's code for `GB`, so rows already written keep their region) or
`XA` to `XZ`; any other is a config error naming where it is.

**Rationale:**

- **One copy of what everyone shares.** A country's name or a zone's country is
  not a decision of one config; every config otherwise carries, and keeps
  current, its own list of them. In core, a table lists only its own words.
- **Standards, not hand lists.** Codes, names, zones and abbreviations come from
  the bodies that maintain them, so a gap is a refresh away and not a guess.
  The data is three JSON files in the package, since the host running the
  collection has no pip and a run must not depend on a service being up.
- **Refresh is deliberate.** A standard changes rarely and a changed zone or
  name changes who is placed, so the change is read as a diff before it ships
  and the data carries the day it was retrieved.
- **Config first.** A project's own reading of a place (a name it wants to give
  another region, a zone it treats differently) must win without a switch, and
  adding core cannot change what a config already says.
- **Whole words for names.** Substring matching puts `oman` in `romania`,
  `india` in `indiana`, `niger` in `nigeria` and `mali` in `somalia`. A
  config's own keywords stay substrings, as they always were, because they
  are written for the text they are matched in.
- **Abbreviations are weak clues.** `de`, `in`, `me` and `or` are words and
  other countries' codes, so an abbreviation counts only as `, tx` ending a
  field, and a country or state name anywhere in the text outranks it.
  Standing ambiguity is settled once: a name shared with a US state goes to the
  state, and a country name that is mostly the start of a US place (`jersey`)
  is left out.
- **Code check.** Core knows every code a rule can give, so a typo (`UKK`) fails
  at load instead of writing a region no report knows.

The cost: a location that names a country no table rule named is now placed,
and a refresh of the zones makes the files people attached be read again once
(the table's fingerprint includes the zones). A source that recomputes its
history gives the people it could not place their region on every past day.

## Past values a source can rebuild are filled, never changed

**Decision:** Where what a run fetches holds a past day's values, a
source rebuilds them and fills them in, through `store.fill`. A source
that rebuilds history writes a row for each day from the first its data
can tell to the day before the file's first row a night wrote (a row not
marked `backfilled`, `store.first_night`), each as of the end of that day
in the run's UTC offset. A new file gets its history the night after its
first row. `github_response_times` and `github_contributor_locations`
recompute their past rows instead (derived history follows today's
rules). Each other source's rebuild and its limits:

- `github_issues` reads every issue each run, with its opening and
  closing times. Each past good row that lacks `issues.opened_total` or
  `issues.closed_total` gets them, counted as the run counts its own
  (issues only, pull requests aside). Each day back to the first issue's
  gets `issues.open` and `issues.open_prs`, those opened by then and not
  closed by then, and the two totals. The fill comes before the previous
  snapshot is read, so the flow counts from a filled row. With `kinds`,
  each past good row that lacks `issues.opened_by_kind` gets it, counted
  the same way, and each rebuilt day has it; a row that has it is counted
  again each run (the issues rows count what was opened by kind).
  Stars and forks too, each apart: the past rows before the first one a
  night wrote with `repo.stars` (`repo.forks`) get it, counted from GitHub's
  dates for the stars (`/stargazers`, with their times) and the forks
  (`/forks?sort=oldest`) on or before the end of the day in the run's UTC
  offset, and each day with no row before that one, back to the first issue's
  or first star's or fork's, is written with them. No `watchers`, which GitHub
  dates nowhere. The lists are asked for only while a past row lacks the count,
  100 a page, and not for a repo with none or with more than 5000 (the log says
  so); a list or request that fails leaves the count to the next run and costs
  the run's row nothing. The days of a repo that lost stars or forks since count
  low, as removed ones aren't listed.
- `github_discussions`, back to the first discussion: `total`,
  `community_opened`, `maintainer_opened`, `community_authors` and
  `categories` from the discussions opened by then, each in its category
  now, and `total_comments`, the top-level comments made by then; null for
  `fetched`, `answered` and `recent`. Only when the query read every
  discussion (no more than the 100 it reads), each with its creation time;
  otherwise nothing is rebuilt and the log says why. The comments' times
  take a second query, sent only when a day lacks its comments; where it
  fails or a discussion has more than the 100 comments it reads, the
  comments stay null and the log says why.
- `github_tree_counts`, from the first day whose tree matches any pattern:
  the counts in the tree of the last commit on the ref by the end of that
  day, by committer date. It costs one listing of the ref's commits and a
  tree for each day with a commit, once. A failed request, a rate limit
  included, stops it for the night with the days counted so far; the
  rows it added mark where it starts, so the next night asks only for the
  trees of days still missing. With `content`, each day through
  yesterday whose good row lacks the counts by value gets them from the
  same commit's tree, a day's row that has the other counts keeping them
  (a tree's files are counted by what they say).
- `discourse_topics`, per topic, back to the topic's creation: `replies`
  and `last_post` from each post's creation; null for `views` and `likes`.
  The posts the topic's answer leaves out are asked for by id, 20 a
  request, only when a day lacks its row, and only when the topic's stream
  holds every post its count holds.

A fill never changes a value: a field a row has, null included, stays,
and a failure or partial row is left as it is. The one exception is a
null in a row marked `backfilled`, which a later rebuild that can tell the
value fills. A row written for a day that had none is marked
`backfilled`, as `cloudflare_web` and `pypi` mark theirs; added fields
carry no mark. A filled row keeps its place and its `captured_at`, and an
added row goes before the first row of a later date. A fill is a
backfill, not a correction ([Terms](ARCHITECTURE.md#terms)), and one log
line per file says what it did. A later run finds nothing missing and
writes nothing.

**Rationale:** The script began writing the issue totals months after its
rows, and most files began long after what they count, though every run
fetches the dates those values come from, or can, once, at a small cost;
rebuilding them gives the trends their whole history. Filling only what
is missing keeps every value a night read as it was read, so no reading
is replaced by a reconstruction, and a run after the fill changes
nothing. A null a rebuild wrote is no reading: it says the rebuild
couldn't tell, so a rebuild that can tell fills it, and only a value
does. Recording a fill as a correction would list every past row once in
health, where corrections are for values that changed. Ending at the
first row a night wrote, not the first row, lets an interrupted rebuild
go on the next night, and waiting a night on a new file keeps the first
night's requests what they were.

The rebuilt values are what the data says now: an issue, discussion,
comment or post deleted or transferred since isn't counted, an issue
counts as closed from its `closed_at`, its last close, a discussion moved
counts in its category now, and a commit merged from a branch counts from
its committer date. A query
that reads only the 100 most recently updated discussions can't count
older days, so rather than a guess there is nothing; nor are a past
day's answers, or a topic's views and likes, which no API keeps by day.

## The issues rows count what was opened by kind

**Decision:** A `github_issues` entry may list `kinds`, in order, each a
`name` and a `label`. With them, each row's `issues` object ends with
`opened_by_kind`: the issues opened, ever (pull requests aside), each
counted once under the first kind in the list whose label it carries, or
under `other`, which no kind may be named. The object holds every kind in
the list's order, then `other`, a zero for a kind no issue has, and its
values add up to `opened_total`. An entry without `kinds` writes no field,
and its rows are what they were. Past good rows that lack the field get
it as the totals were (past values a source can rebuild are filled, never
changed): counted as of the end of the row's day in the run's offset, from
each issue's creation time, and each day back to the first issue has it
too. Every run counts the field again for each past good row that has it,
under the kinds the config lists now, through `store.recompute`: a changed
count is a recorded correction, and a null or a failure row is left as it
is. The row of the run's day also ends with `open_by_kind`: the open issues,
counted the same way, adding up to `open`. Past good rows get it too, and
have it counted again under the kinds the config lists now, through
`store.recompute` (each change a correction): an issue is open on a day when
it was created by the day's end and not closed by it (its last close), and a
row is given it only where those counts add up to the row's own `open`, since
the snapshot a night wrote can differ from what the dates say now (an issue
reopened, deleted or transferred since). A row whose `open` they don't match,
or that has none, is left as it is and the run's log counts them.

**Rationale:** The rows' per-label totals overlap, as an issue with two
labels is counted under both, so they can't be stacked or added up. What was
opened, by kind, needs each issue once, and an issue that carries several
kinds' labels needs a rule: the first in the list, which puts the user's
priority in the config. The kinds and labels are the user's, so they are
config, and no source names one. A new key at the end of `issues` changes no
existing field, and an entry that doesn't ask for it writes the script's
row. Every run reads every issue with its creation time, so the history
costs no request. An issue's kind is its labels as of the night that counts
it, and every past day is counted again then, so a kind added or a label
changed moves the issue in every row at once and the chart has no seam.

## An entry may give a second section, for a row of a card

**Decision:** A source's `summary()` gives one section per entry, and may give
an entry a second one that reads the same file, titled with a `part` after
the entry's name (`github_issues: owner/repo stars`). `github_issues` does so
for an entry with `stars_and_forks` set (a boolean, off by default): the
second section shows the row's `repo.stars` and `repo.forks` as the "Stars and
forks" row of trends' Traffic card, which the traffic sources' rows share.
`github_repo_stats` has the same row for its own file's `stars` and `forks`,
and no setting. A project lists the second section by its title, as it lists
any section. Within a project, a section's heading counts the sections of its
source that are rows of the same card (or of none) apart, so the second
section is headed by its card and joins the other rows, while the entry's
first section keeps its own heading. Without the setting, an entry's sections
and rows are what they were.

**Rationale:** A card is assembled from separate sections that name the same
`Card`, and a section is one fixed set of fields of one source, so the only way
for an entry's file to also be a row of a card is a section of its own. A
setting, not always on, because the stars and forks of every repo are the same
two numbers in several files (`github_repo_stats` writes them too), and an
entry that doesn't ask for the row shows what it did. The section's title
must differ from the first's, since a project finds a section by its title and
no two share one, and a `part` keeps the entry's name, so the heading outside a
project (the card and the name) is the same for every row of the entry. The
card's row is defined once, with the GitHub sources' shared code, so the
sources that show it don't import each other.

## Stars and forks are counted by region, never by person

**Decision:** A `github_issues` entry with `stars_and_forks` and a `regions`
table (the shared table `github_contributor_locations` reads too) gives each
row two more `repo` objects, `stars_by_region` and `forks_by_region`: the
people who starred or forked by the end of the row's day (in the run's UTC
offset, from GitHub's own `starred_at` and fork `created_at`), counted once
each under the region of their profile location, as a contributor's profile
is read (`config.find_region` with `location`), else the table's `known`
region for their login, else `unknown`. A profile with no location, or no
profile (a 404), is `unknown`. Without both settings the rows are what they
were. The counts are the only thing kept: no login is written to a row or a
file, and none is remembered between runs (this source's own log lines name
regions and counts, never a person; the HTTP client's line for a failed or
reused request names the request, a profile's path included).

What a run reads comes from the rows. The newest past row that has the counts
holds every person dated up to its day, so a run reads the profiles of the
people dated after it, oldest day first, and adds them to its counts. The
run reads the list of stargazers (forks) each run, which it already reads for
history, so who is new comes from the list's dates, not from the totals (a
star removed and another added leaves the total as it was). A night with
nobody dated after the row reads the list and no profile, and a repo with no
stars (forks) reads neither. It reads at most 100 profiles, stars and forks
together (a person on
both lists once). A day's people are read whole, so the day a run stops at is
the first it couldn't finish, and the first day a run reads is always read
whole, so a run always goes on. Each day after the newest counted row, up
to the one before where the run stopped, is given its counts: a past good
row that lacks them gets them, and a day written for history gets a row
(past values a source can rebuild are filled, never changed). An added field
carries no `backfilled` mark, and a row written for a day that had none is
marked as ever. A profile that fails,
a list that can't be read, or a repo with more than the 5000 stars (forks)
the history reads leaves the days from there to the next night, said in the
log, and costs the run's row nothing.

The trends page does not draw them: the entry's Stars and forks card charts
the two counts only, because the regional counts were too thin to read
anything from. The rows keep being written, so a later report can use them
and no day has a gap.

**Rationale:**

- **Counts are all a report needs, and a login is a person.** The counts
  show where the people are, so the count by region is the whole value; a
  list of who starred would be a copy of what GitHub holds, written to a
  file the project syncs and shares. Keeping only counts, and logging only
  counts, leaves nothing about a person to leak.
- **The rows are the memory.** A profile's location is read once, when the
  person is new, and the region it gives is added to the count of every day
  from theirs on. Because the counts are in the rows, no cache of who was
  resolved is needed, and the bound holds with no stored login: after the
  first pass a night reads the list and only the new people's profiles. A
  run that can't finish leaves its rows without the counts, and the next
  one starts from the rows that have them, so an interrupted pass loses
  nothing and repeats nothing that was counted.
- **Whole days keep the bound honest.** A day's people can't be split
  without remembering which were read, which would be a stored login. Reading
  a day whole, at least the first of a run, means one very busy day is read
  in a run whatever the limit, while every other day waits its turn, and the
  limit never stops all progress.
- **The same evidence as contributors, in the same order.** A profile
  location is the person's own statement, and a known region is the
  maintainer's word, so they rank as they do for contributors, and a known
  region a profile contradicts is logged by the two regions, never by login.
  The text of an issue, an issue form or a comment, which place a
  contributor, say nothing about someone who only starred.
- **A filled row's counts are as the lists say when read.** The days before
  the newest counted row get nothing, since a run has no one to count them
  from, and a row's counts are never recomputed under a changed table: that
  would be every profile again each night, which the bound forbids. A person
  who removed their star, or a profile whose location changed, after they
  were counted stays as counted, so the stacks can total more than the count
  they cap.

The cost: a table's rule added later places only the people counted after it;
the days already counted keep their regions, unlike contributors, whose past
days follow today's rules (derived history follows today's rules). A repo
with a long history takes several nights to count, 100 profiles a night.

## A tree's files are counted by what they say

**Decision:** A `github_tree_counts` entry may have `content`, a `pattern`
(a regex with one group, run over every line of a file's text) and a `key`.
With it each row counts the entry's matching files by the pattern's group
1 in each file's text, as an object under `key`: `unknown` for a file
whose text the pattern doesn't match or captures nothing from, and for a
tree entry that isn't a file. The counts add up to the row's file count.
The text comes from GitHub's blobs API, one request for each blob the tree
lists for a matching file, and a blob is read once, ever:

- Each row lists the blobs it was the first to read, under `<key>_blobs`,
  as `{value: [sha, ...]}`; a row that read none has no such key. A night,
  and the history rebuild, start from every blob any row of the file lists
  and read only the others. A rerun of a day keeps the blobs the day's
  stored row lists.
- An unreadable blob (the request fails, or the answer isn't a blob) is a
  failed part: the counts are null, the row is partial and its reason names
  the failure (a partly failed row keeps its good parts). The blobs read
  stay listed, a value the day already has stays (a run never erases what it
  didn't get), and the next night reads the blobs still missing. An error
  waiting can't fix is critical at once.
- The history rebuild gives every day through yesterday whose good row lacks
  the counts the counts of its last commit's tree, as the other counts are
  rebuilt (past values a source can rebuild are filled, never changed). It
  reads each blob of every tree once, and a night's rebuild stops before the
  next tree once it has asked for `TREE_BUDGET` (500) trees or read
  `BLOB_BUDGET` (1000) blobs, so a long history spreads over nights and the
  rate limit the other sources share is left to them; a failed request stops
  it for the night. A past row a night wrote keeps the counts it has; the fill adds only the counts by
  value.

**Rationale:** A file's blob SHA names its text, so the value read for a SHA
is the value for every file, tree, day and night that holds it. Listing each
blob once, where it was first read, keeps the memory in the rows, the one
store, and costs a line's width for each distinct text the files have ever
held (a 40-character SHA and its punctuation), where a list in every row
would repeat every unchanged file every night. An ordinary night asks for
the tree and no blob, and a night that edits a few files reads those few.
The list is a cache and never a source of counts: a row that loses its list
costs a re-read, not a wrong count. A history costs one request for each
distinct text its files have held, against GitHub's limit of 5000 an hour,
shared with every other source's requests in the run, so a budget of a
fifth of it leaves the rest to them; stopping before a tree, never inside
one, writes a day's row only when all its blobs were read. A blob that
can't be read leaves the counts null, never a count that leaves out a file,
since a count that doesn't add up to the files would be neither the true
one nor marked as wrong. The pattern and the key are config: the
source knows nothing of what a file says (sources are named for what they
read; project specifics are config).

An entry's `pattern` may be a list, newest location first, for files that
have lived in several places, a list item being a regex or an object
`{"pattern": regex, "content": false}`. Each day a (group, item) pair is
counted from the first pattern that matches it, and only that pattern's paths
are its files; the history rebuild and the nightly row use the same rule. A
pattern with `content` false is counted but its files' text is not read, and
they count as `unknown`, for files that carry no such line (source files whose
text may still contain it). A one-regex pattern behaves as a list of one. Files
that have moved are counted once however many places hold them, so the counts
stay continuous across a move, where one regex with the places as alternatives
would count a pair in two places twice in `files` and could not give each
place its own groups; a `content` flag on the list item keeps the choice of
what is read with the place, in the config, where the place is.

A list item's `content` may also be an object with its own `pattern` (one
group) and `map`, each defaulting to the entry's, for files that say the line
another way where the files used to live (a source file's `status =
Status.VERIFIED` against a data file's `status: confirmed`). The values count
under the entry's key as every file's do, so the history has one set of bands;
the blob cache is keyed by SHA alone, as a blob's text is the same wherever it
sits. It is valid only where the entry has `content`.

`content` may also have a `map`, an object of value to name, for a value
written differently over time: the captured value is looked up exactly, and
the counts and the `<key>_blobs` lists use the name, so the history has one
band, not two. A name is renamed once (a mapped-to name that is itself mapped
is invalid), so a map has no chain or cycle to resolve. The blobs a stored row
lists under an old name are read under the mapped one, so a new map reads no
blob again, and a past row keeps the names it was written with (past values
are filled, never changed); only a rebuild or a night writes the new names.
`unknown` is not a captured value and is not mapped.

## Derived history follows today's rules

**Decision:** `github_response_times` and `github_contributor_locations`
recompute every past day's row on each run, from the first day their data
can tell to the day before the run date, under the run's rules, through
`store.recompute`, where other sources fill (past values a source can
rebuild are filled, never changed). Each day is counted as of its end in
the run's UTC offset:

- `github_response_times`, back to the first community issue: every field,
  from the issues opened by then, a comment or closing after that day not
  yet made. No extra request.
- `github_contributor_locations`, back to the first issue or pull request
  of a person it counts: the people who had opened one by then, each with
  the location and region this run gives them, and the totals and
  `region_summary` from those. Only on a night that read every profile;
  otherwise past rows stay as they are and the log says why. No extra
  request.
- `github_issues` also recomputes one field of its past rows,
  `issues.opened_by_kind`, from the first issue: each past good row that
  has it is counted again from the issues' creation times and labels under
  the kinds the config lists; the rest of the row is kept, and a null or a
  failure row is left as it is. It does the same for `issues.open_by_kind`,
  from the creation and closing times, on a row whose `open` the counts match.

A good row whose values differ gets the recomputed ones in its place,
with the run's `captured_at`, and each is a correction (every correction
is recorded); a field the recompute doesn't give is kept, and a row whose
values agree is left as it is. A failure or partial row is replaced by
the recomputed row, as a good row replaces one, and that is no
correction. A day with no row gets one, marked `backfilled`. As with a
fill, a new file gets its history the night after its first row. A
person's region and location are where their profile, forms or issues
place them now; an issue or a person's issues deleted since aren't
counted. The script never touches a past row, so these files differ from
its rows wherever a rule changed; the scenarios that show it have
reviewed rows.

**Rationale:** These rows are not readings of a moment, like a count of
stars, but values computed from data every run reads in full: each issue
with its times and comments, each person with their issues and profile.
A rule that changes, such as a closed issue counting as answered or an
issue form giving a region, changes what that data means for every day,
so the whole history follows the current rules and the chart has no seam
where the rule changed. A past day's values change only when the data or
a rule does, so after the first run under a new rule the corrections
health lists are real changes: an issue deleted, a profile's location
changed, a rule added. Recording each keeps them visible. A field the
recompute can't give is kept, as a run never erases what it didn't get.
A night's row is read at the run's time and the recompute counts to the
end of its day, so activity after the run is counted the next night, a
correction like any other.

## leadline ships as one file, leadline.pyz

**Decision:** Besides the PyPI package, the package builds to a single stdlib
zipapp, `leadline.pyz`, which a host with Python 3.8 or later and no pip
runs as it is. A scheduled task can then stay fixed, running
`python3 leadline.pyz run --config leadline.json`, while the file and the
config it reads change. Updating means replacing the file with a build of a
tagged version, the old file backed up first
([RELEASING.md](RELEASING.md#the-one-file-build)). The archive holds the
runtime only: every `tests/` folder (a source's scenarios, golden and port
tests) and every Markdown file (its README) stay in the repository, and so
they do in the wheel. The sources in the archive are found as they are in the
repository, by `discover()`.

**Rationale:** A NAS or a small server often has Python and no pip, and
copying one file needs nothing installed. A system package adds a build
toolchain and an install step for every update, for one scheduled command. A
container would lift the Python 3.8 constraint but adds images and a registry
to operate. A tag per build makes the version each night's health row
records name one commit, so a night's rows trace to the code that wrote
them, and the backup makes undoing an update one copy. Keeping each source's
tests and README in its own folder is what makes a source one folder; leaving
them out of the archive keeps the file that runs to the code it runs, and a
test holds the archive to it (`tests/test_pyz.py`).

## The PyPI port is verified from the script's own responses

**Decision:** The PyPI source's port tests serve it the script's own
pypistats responses as saved responses (`http.SavedResponses`), and its rows
are compared with the script's. It is never run beside the script, since a
second pypistats caller spends the scheduled run's budget.

**Rationale:** It gives PyPI the same comparison as every other source,
with no extra calls to pypistats. A saved response never reaches the network,
so no mistake in a test can make leadline call pypistats.

## captured_at is the run's start time

**Decision:** Every row a run writes carries the same `captured_at`: the
run's clock, read once at start as local time with its offset (`ctx.now`).
Nothing reads the wall clock after that.

**Rationale:** One clock keeps a run's rows consistent, lets tests fix the
time without patching internals, and keeps a run that crosses midnight on
one date. The script stamps each write's own moment instead; `captured_at`
is the one field the port may write differently, and within a run the two
differ by minutes.

## Store never fails on a line it can't use

**Decision:** When store meets a line it can't use as a row, because it
doesn't parse, isn't an object (`null`, `[1]`), or has no usable key (a
`date` that isn't a string), it keeps the line as it is and carries on. A
line stays as it is down to its bytes: one that isn't valid UTF-8 is written
back unchanged, and a byte order mark before a row doesn't hide it from its
key.

**Rationale:** The script crashes on a line that parses but isn't an object,
and its usage writer on a `date` it can't sort; every later write to that
file then fails until someone edits it, so one bad line stops a source
indefinitely. Keeping the line changes nothing on a
normal night and loses nothing on a bad one. The script also drops any byte
that isn't valid UTF-8 whenever it rewrites a file, so a file saved by an
editor in a Windows code page loses its accented characters; store doesn't.

## pypistats.org is called only by the scheduled run

**Decision:** Only the scheduled collection calls pypistats.org. Tests run
against made-up responses, and the PyPI source's `check()` uses the
pypi.org JSON API instead.

**Rationale:** pypistats rate-limits by IP, and a scheduled host often
shares its network's public IP with every dev machine. An ad hoc probe
spends the scheduled run's budget and shows up as failed rows the next morning.

## Test data is made up in code; the real capture is a private shape reference

**Decision:** Every response, seed row and golden in the tests is made up
in code. Per-API builders make responses with made-up values, and each
source has a handful of named scenarios, built from them, that together
cover its code paths in the script; the rows the script writes for each
scenario are the source's goldens. A source the script doesn't have has no
oracle: its scenarios carry expected rows, written by hand and reviewed,
and those are its goldens. A real capture of the script's nightly
responses stays private and outside the repo. Tests read it for one thing:
an `oracle` test holds each builder to the shape of the real responses of
its kind.

**Rationale:**

- **No personal data can reach the repo.** No value in a test came from a
  real response, so there is nothing to scrub. Scrubbing a capture took a
  heuristic tool, a reviewed list of public values and a leak test, and real
  names still got through the list, while an unrelated doc edit could change
  a fixture.
- **A scenario says what it tests.** It is named for one behavior and holds
  only the values that steer it, so it reads as a specification. A captured
  night covers only the paths that night took, as one file per request.
- **Realistic, not real.** The shape test keeps the made-up responses in the
  APIs' real form (keys at every level, value types, the Link header), so a
  port that passes on them reads the real ones.

The cost: a shape the capture has no example of (a list that was empty that
night, a field that was always null) is built from the API's documentation,
unchecked; the shape test names each such place. An API the script never
calls isn't in the capture at all, so its builders are unchecked until a
capture holds its responses; and reviewed rows are only as right as the
review.

## A run's window fields are readings of that run, never re-read

**Decision:** A field that reads a window ending at the run (a window total,
the window's sum, the pages and referrers seen across it) is a reading of
that run, kept on its row. A later run does not re-read it for a past day,
even when the row is `partial` for lack of it. A source that
re-reads days overwrites a day's own counts with good data the next night;
it does not rebuild a past run's window. A gap that no later run can fill
stays on the row, null, and ages out of health's gap window.

**Rationale:** A window read later is not the reading the run would have
made: it covers the run date's full day, not the part that had passed, so
the figure no longer matches its neighbors. A rebuilt value would look like
the run's reading and be a different measure. Healing it would also cost a
request for each partial row, for a figure that does not add to the series.
A run still writes what it got and never erases what it did not
(a failure is recorded as a failure).

## Fields a plan withholds are not captured

**Decision:** A source captures only the fields the account's plan lets it
read. When the API refuses a field to the account (the answer names the zone
and the field), the field is left out of the source, not requested every night
to fail. Another source that already records the same fact stands in for it.

**Rationale:** Cloudflare's zone analytics withholds some dimensions,
including the referrer, by plan. A dimension in the nightly query that the
account cannot read fails its part of the query every night, and the row is
`partial` for good. Page views already record referrers through
`cloudflare_web`, so the data is not lost. If the plan changes, adding the
dimension is one entry in the source's list of dimensions.

## Storage is a backend behind one contract

**Decision:** Where rows are kept is a backend, chosen by the optional
`storage` section of the config. Without it, the backend is JSONL: each file
is a flat file of that name in `out_dir`, as before. A file is a source
entry's rows, named as the config names it
(`cloudflare-web-example.jsonl`). The backends and their contract live in
`leadline/storage/`, the one place that knows how rows are kept.
`store` keeps every rule (upsert, a failure never replacing good data,
merges, windows, backfills) and reaches a file only through the contract:
`read_lines(file)` returns the file's lines in order, `write_lines(file,
lines)` replaces them atomically, and `files()` names the files that exist.
A database backend keeps a file as rows in its tables, keyed by the file's
name. The unit is the line, because `store` keeps the lines it does not
change exactly as they are. A backend returns each line exactly as it was
written, including a line that is not an object. One conformance suite, which
every backend passes, tests that round trip on the goldens.

A database backend normalizes: reference tables for files and for field
paths, created the first time a file or a field is written, and tables for
the rows and their values. A line in the form leadline writes is split into
those tables and rebuilt, with the same serializer, into the same line. Any
other line (one the original script wrote, or one that is not an object) is
kept as it came in a table of raw lines. A new source or any config leadline
accepts needs no migration. A backend creates and migrates its own structure
on first access when its credentials allow it, records a schema version, and
stops with a message that names what is missing when it cannot. A backend's
credentials come from environment variables, never from the config or the
log.

**Rationale:** A flat file per source entry suits one box and stays readable by any
tool, so it remains the default. A shared or hosted store, such as SQLite or
Cloudflare D1, needs the data in tables, and normalized tables let a viewer
query a metric without the program. Keeping the rules in `store` means a
backend cannot change what a run writes: the same run gives the same rows on
every backend. A contract of whole files keeps a backend small, and a
file is a line a day, so a database backend writes only the lines that
changed. SQLite (`sqlite3`) and an HTTPS API (`urllib`) are in the standard
library, so the runtime stays stdlib-only. A backend that needs a driver is
an optional extra, decided when it is proposed.
