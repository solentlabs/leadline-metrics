# Architecture

How leadline is built: its goals, modules, the run, and the contract every
source keeps. Why the larger choices were made is in
[ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md) (cited below as
"ADR: *title*").

| Topic | Document |
| ----- | -------- |
| Goals, modules, the run, sources, health, `leadline diff` | this file |
| The core modules sources build on: HTTP, GitHub, store | [CORE.md](CORE.md) |
| The config file and its credentials | [CONFIGURATION.md](CONFIGURATION.md) |
| Report blocks: the `reports` setting and its language | [REPORTS.md](REPORTS.md) |
| `trends.json`'s format and the `trends.html` viewer | [TRENDS.md](TRENDS.md) |
| Tests, scenarios and the oracle | [TESTING.md](TESTING.md) |
| The one-file build and releases | [RELEASING.md](RELEASING.md) |
| Steps for a new source | [ADDING_A_SOURCE.md](ADDING_A_SOURCE.md) |

## Goals

The outcomes every design choice serves. leadline turns nightly collection
in one shell script, `collect-metrics.sh`, into a package.

- **Stable.** The runtime is stdlib-only on a fixed Python. Each source runs
  in isolation, so one failure never stops the others. Writes are atomic.
  `check()` catches a source that fetches fine but points at the wrong
  thing.
- **Scalable.** Adding a project is a config edit. Adding a source is one
  folder, found by name, and `make docs`.
- **Testable.** Every source is tested against saved API responses, with no
  network. The script is the oracle for expected rows. A test enforces the
  module boundaries.
- **Reusable.** The code holds nothing specific to one user. Repos, sites,
  labels and reports come from config.
- **Continuous history.** The existing series never breaks. Rows stay
  compatible with the script's, so leadline goes on writing the files the
  script began; `leadline diff` shows any row that differs.
- **Trustworthy data.** A failure is recorded as a failure, never as a
  zero. Missed days are backfilled where the API allows. Health shows what
  is stale, failed or missing.
- **Easy to operate.** The scheduled task never changes: the config and the
  package are files it reads, and the credentials stay in the task.

## What it does

What one run produces, and the compatibility promise it keeps.

`leadline run`, once a day from a scheduler (cron, launchd, Task Scheduler):

1. collects every configured source once, appending its rows to the
   source's JSONL files;
2. writes a health row describing the run;
3. rebuilds the report blocks from those files: the config's, or with
   none, the default summary, `SUMMARY.md`;
4. writes `trends.json`, the history of the numbers the default summary
   shows, and pages that chart it: `trends.html`, or one per portfolio of
   projects.

It replaces `collect-metrics.sh`, so it writes **the same rows the script
writes**: same files, same keys in the same order, same values, same
serialization. The only differences allowed:

- `captured_at` (ADR: captured_at is the run's start time);
- the rows a decision changes, each listed with its scenario and the
  decision in the source's `DIVERGENCES` ([Port
  tests](TESTING.md#port-tests)): a failure never replacing a good row,
  corrections and backfills, a failure row where the script writes none.

## Terms

Each term has one meaning, used the same way in code, health, reports and
docs.

- **Run date**: the local date when a run starts. Every row a run writes is
  dated from it.
- **Row**: one JSON object on one line of a source's file, keyed by `date`,
  plus a second field where the source has one (the forum's `topic_id`).
- **Good row**: a row whose `status` is absent or `ok`.
- **Failure row**: a row with any other `status`. It records that the fetch
  for that date didn't land; it is never a zero.
- **Partial row**: a failure row with `status` `partial`. It is a row built
  from several requests where an optional part failed and no run of the day
  has got it:
  - it keeps the parts that landed, and each missing part is null;
  - its `reason` names each failure that left a part missing;
  - a later run of the day keeps its values and fills what it gets, and the
    row is good once every part is filled;
  - health counts it as a failure; a failure row never replaces it, and a
    good row does.

  (ADRs: a partly failed row keeps its good parts; a run never erases what
  it didn't get.)
- **Lagging source**: a source whose data for a day is complete only after
  the day ends, so its newest row is normally for the day before the run
  date. Each source declares this itself (`LAGGING`); nothing else keeps a
  list of them.
- **Expected date**: the date a source's newest row should have: the run
  date, or the day before for a lagging source.
- **Stale**: a file's newest row is more than one day older than its
  expected date.
- **Failed**: a file's newest row is a failure row. Its **streak** is the
  number of consecutive failure rows ending there; **since** is the date
  the streak began.
- **Gap**: a date in the last 14 days, between a file's first and newest
  rows, that has no row (missing) or a failure row (empty).
- **Critical**: something that makes the run exit 1:
  - a source with no good row for two or more consecutive expected dates in
    a file it writes, counted from the day after the file's newest good row
    (or from its first row when it has none);
  - an error waiting can't fix (credentials rejected, the thing asked for
    doesn't exist), on its first night;
  - a source that crashed, or failed to load;
  - a report block that can't be written ([REPORTS.md](REPORTS.md#each-block-on-its-own)),
    or a trends file ([TRENDS.md](TRENDS.md)).
- **Backfill**: writing a good row for a past date that had none, or had a
  failure row, from an API that keeps history; or adding to a past good
  row the fields it lacks, rebuilt from what a run fetches, never changing
  a value it has, a null a rebuild wrote aside (`store.fill`; ADR: past
  values a source can rebuild are filled, never changed). `cloudflare_web`
  also refills a row read under another site tag, and a derived source's
  recompute adds the days that had no row (`store.recompute`; ADR: derived
  history follows today's rules). A row written for a day that had none
  is marked `backfilled`.
- **Correction**: replacing a good row for a date before the run date with
  a good row whose values differ, `captured_at` aside. Store records every
  one, for every source (ADR: every correction is recorded). These are not
  corrections: a rerun's row for the run date, a filled missing or failure
  row, a backfill's added fields, and a rewrite that changes nothing.

## Layout

The modules under `src/leadline/`, and what each owns.

| Module | Owns |
| ------ | ---- |
| `cli.py` | argument parsing; calls `runner` (`run`, `report`) or `rowdiff`; prints the default summary (`default-reports`) |
| `config.py` | loading the JSON config, flag overrides, shared tables (with core's places merged in after a region table's own rules), region matching and the region-code check; output file names; a list of entries' problems and files; a source's sections of the default summary (`Section`, `Shown`), and their report lines; version order |
| `places/` | the places core knows for every region table: country names, US states, time zones and the region codes, as rules (`__init__.py`); `countries.json`, `zones.json` and `states.json` are data, each generated by `make places` from its standard, vendored, and read the first time it is needed with `pkgutil.get_data`, which works inside the zipapp (the zones only for a table that reads attachments) ([Shared tables](CONFIGURATION.md#shared-tables)) |
| `context.py` | `Context`: the run's clock and date, output folder, logger and HTTP client, and what sources report for health; `ReportContext`, the same without HTTP, for `report`; the logging of both. It loads `http` only when a run opens its context |
| `http.py` | the `urllib` wrapper: timeouts, user agent, retries, pagination, GraphQL, answer reuse, reading an answer, saved responses ([CORE.md](CORE.md#http)) |
| `store.py` | reading and writing row files; every file's atomic write ([CORE.md](CORE.md#store)) |
| `runner.py` | one run: validation, sources in order with failures isolated, the retry pass, checks, health, then `build`; and `report`, which validates and runs `build` alone |
| `health.py` | `health.jsonl`: freshness, critical sources, check findings, corrections |
| `reports.py` | the report blocks in Markdown files ([REPORTS.md](REPORTS.md)) |
| `trends.py` | `trends.json` and its pages ([TRENDS.md](TRENDS.md)); the `projects` and `pages` settings' validation |
| `rowdiff.py` | `leadline diff`: comparing two folders' row files |
| `sources/` | one package per source (`spec.py`, `source.py`, `README.md` and `tests/`; [Adding a source](ADDING_A_SOURCE.md)); the sources of one service sit together in a platform group, a package of its own that holds what they share (`sources/github/` holds the GitHub sources and the code they share, [CORE.md](CORE.md#github); `sources/cloudflare/` and `sources/home_assistant/` hold their sources and their tests' response builders); a service with one source is just its package, with its builders in its own `tests/`; `__init__.py` is the contract, `discover()` (the registry: the sources found, in groups too, and those that failed to load; `discover(specs_only=True)` loads each source's `spec` alone), and the default summary built from the sources' sections; the docs that list the sources are generated from the packages and their READMEs ([Source table](#source-table)) |

### Dependency rules

What each module may import. `tests/test_boundaries.py` enforces these by
scanning imports.

- **The core** is `config`, `context`, `http`, `places` (with its
  data file) and `store`. It imports only the core.
- **Sources** import only the core, their own modules and their own
  group's shared modules: never each other, another group, `runner`,
  `health` or `reports` (ADR: sources are modular by construction).
- **A platform group** (`sources/<platform>/`, a package that sets
  `GROUP = True`) imports only the core and itself: never one of its
  sources, another group or another source. It holds what its sources
  share and nothing one of them owns (`sources/github/` holds `api.py`
  and `spec.py`).
- **`health`** imports only the core. It reads the output files through
  `store`, and the run's context; the runner tells it each source's files
  and whether the source lags.
- **`reports`** and **`trends`** import only the core (not `http`), never a source,
  `runner`, `health` or each other. They read the output files through
  `store`, `health.jsonl` included; trends gets the sources' summary
  sections from the runner.
- **The report path** is what `leadline report` loads: `reports`, `trends`,
  `sources/__init__.py` and the sources' `spec` modules. None imports
  `http`, and no source's `source.py` or group's `api.py` is imported
  (`discover(specs_only=True)` imports the `spec` alone); a test runs
  `report` in a subprocess and checks `sys.modules`.
- **`sources/__init__.py`** also imports `health` and `reports`, to build
  the default summary from the sources' sections.
- **A package's `__init__.py`**, a source's or a group's, imports nothing
  of its own (only `leadline.sources.lazy`, which finds its public names in
  its `spec`, then its `source` or `api`, on first use), so importing the
  package to reach its `spec` loads no capture code. Imports under
  `if TYPE_CHECKING:` are for type checkers and not scanned.
- **`runner`** knows sources only through the registry, never by name.
- **A source** (`sources/<name>/`, or `sources/<platform>/<name>/`, any
  depth inside) imports the core, its own modules and its group's shared
  modules (`from leadline.sources.github import api as github`), never
  another source or group. Its `spec.py` is pure declarations: it imports
  only `leadline.config`, its sibling modules (never its `source`) and its
  group's `spec` module (`leadline.sources.github.spec`, the checks of an
  entry that every source of the group shares), never `http`, `store`,
  `context` or the rest of a group. Its `source.py` is the one place its
  rows are written, through `store`. The core, `health`, `reports`,
  `trends`, `runner` and `cli` import no source and no group by name.
- **A `tests/` folder** is not runtime code and is not scanned. A builders
  module in it imports only `tests/scenarios/builders/common.py` and its
  own platform's builders.
- **Every runtime import** is the stdlib or `leadline`.

## The run

What `leadline run` does, in order, and how it exits; and `leadline report`,
which does the last of it alone ([Reports without a run](#reports-without-a-run)).

```text
leadline run --config P [--only a,b] [--out DIR] [--user-agent UA]
```

- `--only a,b` runs a subset of the enabled sources, still in config
  order.
- `--out` and `--user-agent` override their settings
  ([CONFIGURATION.md](CONFIGURATION.md#settings)). `--out` runs on
  another folder, such as a copy of the metrics folder, without editing
  the config.

### Steps

1. **Validate the config.** Nothing runs, and the output folder isn't
   created, until all of these pass:
   - every enabled source's `validate` checks its own config (a source
     that failed to load is left out of all of this; step 3);
   - then each source's `files` names the files its entries write, and no
     two entries, in one source or two, may write the same file. Names
     that differ only in case count as the same, as OneDrive on Windows
     holds them as one file;
   - then `reports.validate` checks the reports, if the config has any;
   - then `trends.validate` checks the `projects`, if the config has any,
     against the titles of the sources' `summary` sections (skipped, with a
     warning, when a configured source failed to load: its sections are
     missing);
   - then `trends.validate_pages` checks the `pages`, if the config has
     any, against the `projects`' names.

   Each problem is one line naming the source, report or project, the
   entry and the key (`sources.github_issues[1].repo: ...`,
   `reports[0].place: ...`, `projects[1].sections[2]: ...`,
   `pages[0].projects[1]: ...`), or every
   entry that writes a shared file
   (`sources.a[0] and sources.b[2] write the same file: x.jsonl`).
2. **Read the clock once.** `ctx.now` is the local time at start, with its
   UTC offset; its date is the run date, `ctx.date`. Every "yesterday" and
   every `captured_at` comes from them, never the wall clock, so a run that
   crosses midnight stays on one date, as the script's does. The output
   folder is created and logging starts.
3. **Collect each enabled source, in config order.** A source is enabled by
   its entry under `sources` in the config.
   - One that failed to load (`discover()`: its import or its contract
     check raised) is recorded critical, `failed to load: <ExceptionType>:
     <message>`, like a crash; it is not validated, collected, checked or
     summarized, and every other source runs. A source that failed to
     load and has no entry is one `WARN:` line. An entry naming no source
     at all is still `unknown sources`, exit status 2.
   - One whose credential variables (`ENV`) are unset or empty is skipped
     with a `SKIP:` line. Skipping is not a failure.
   - A failure row a source writes is recoverable and stops nothing.
   - An exception is a bug, which waiting can't fix: it is logged with its
     traceback and recorded critical, and the next source runs.
   - A final line counts the sources that ran, crashed and were skipped.
4. **Retry pass.** Each source that ran and defines `retry` (`pypi`) gets a
   second pass, in config order, after every source has collected, so the
   other sources' work spaces its requests. A crash here is isolated and
   critical, as in collection.
5. **Checks.** Each source that ran or crashed runs its `check()`, in config
   order; one skipped for its credentials doesn't (ADR: check() runs on
   every run, after collection). Findings go to the health row. A check
   that raises is a bug, logged and critical, and the next check runs.
6. **Health.** One row for the run date in `health.jsonl`; a rerun replaces
   it ([Health](#health)).
7. **Build.** The step `leadline report` runs alone ([below](#reports-without-a-run)):
   - **Reports.** Each report block rebuilds from the output files, this
     run's health row included. A config with no `reports` gets the
     default summary, built from each configured source's `summary`
     ([The default summary](REPORTS.md#the-default-summary)). A block that
     fails is critical, and the others are still written
     ([Reports](REPORTS.md)).
   - **Trends.** `trends.json` and its pages, whatever the reports
     setting, from the output files and each configured source's
     `summary`, grouped by the config's `projects`: a page for each of the
     config's `pages`, or with none, `trends.html` ([TRENDS.md](TRENDS.md)).
     A file that can't be written is critical, and the others are still
     written.

A crash in `health.write` is not isolated: it ends the run with a traceback
and exit status 1, and the steps after it don't run that night. A crash in
a source's `summary()` ends the run the same way. Every run builds the
sections, normally while validating the config, before anything is
collected; only when a configured source failed to load are they built
after health, for the trends files. A bug there is critical anyway.

### Exit status

The runner decides it from `ctx.critical` alone
(ADR: one failed night recovers; consecutive failures are critical).

| Status | When |
| ------ | ---- |
| 0 | nothing critical; failure rows included, since one failed night recovers |
| 1 | anything [critical](#terms) |
| 2 | the command line or config is unusable: nothing runs and nothing is written |

Exit 2 covers an unreadable or invalid config file, an unknown setting or
source name, a shared table that doesn't exist, a problem any `validate`
finds, two entries writing one file, a problem in the reports, and an
`--only` name that isn't in the config.

### Reports without a run

```text
leadline report --config P [--out DIR]
```

Capture and presentation are separate duties: a page is redrawn without
collecting (ADR: reports are built from the files alone). `report` does
steps 1 and 7 of a run, on the output folder as it is:

- It validates the config as `run` does (exit status 2, nothing written),
  then writes the config's `reports` or the default summary, `trends.json`
  and the trends pages, from the row files, `health.jsonl` (its last row,
  or the page's "no health row" if there is none) and the sources'
  `summary`. `--out` builds another folder, such as a copy.
- It collects nothing, checks nothing, makes no request and needs no
  credential: a source whose `ENV` variables are unset is not skipped or
  flagged. It writes no health row, changes no row file, and leaves
  `leadline.log` as the last run wrote it (its own lines go to stdout).
  The output folder must exist.
- A configured source that failed to load is critical, as in a run: `report`
  loads each source's `spec` alone, so only a broken `spec` fails; a
  `source.py` that cannot import shows in `run`.
- It exits 1 when a report block or a trends file failed (or a source
  failed to load), as `run` would; 2 when the command line or the config is
  unusable; else 0.
- The date its pages show as today is the local date when it ran.

`run` and `report` call one function, `runner.build`, so they cannot
differ.

### Logging

- Every module logs through the `leadline` logger.
- Each line reads `[YYYY-MM-DD HH:MM:SS] message`, local time, as the
  script's `log()` writes it. Warnings and errors carry `WARN:` and
  `ERROR:` in the message, as the script's do.
- Lines go to stdout and to `leadline.log` in the output folder, which each
  run rewrites (`report` logs to stdout only).
- The log opens with the version, the run date, the output folder, the
  sources.

## Sources

The contract every source module keeps, how sources report failures, and
the list of sources.

### Contract

A source is a package under `sources/` (or inside a platform group there:
`sources/github/github_issues/`) with these members, which it re-exports
from its `__init__.py`:

| Member | What it is |
| ------ | ---------- |
| `NAME` | the config key, equal to the package's folder name, named for the platform and the data it reads, e.g. `"github_issues"` (ADR: sources are named for what they read) |
| `DEFAULT_FILE` | the template of the file it writes unless an entry's `file` says otherwise (`config.default_file`), e.g. `github-issues-{repo_name}.jsonl` |
| `ENV` | the credential variables it needs, e.g. `("GITHUB_TOKEN",)`, or `()`; it reads them from `os.environ` |
| `LAGGING` | true when its newest row is normally yesterday's; health allows it one extra day |
| `validate(cfg)` | every problem with its config, as strings; empty when usable |
| `files(cfg)` | every file a valid config writes, keyed by the path of the entry that writes it |
| `collect(ctx, cfg)` | fetches, builds rows and writes them through `store`; returns nothing |
| `check(ctx, cfg)` | checks the config against the live API, writing no rows; returns findings |
| `summary(cfg)` | its sections of the default summary and trends, each a `config.Section` with its readable label |
| `retry(ctx, cfg)` | optional: the retry pass (`pypi` only) |

- **`validate`**: each problem starts with its path inside the config:
  `[1].repo: ...` for an entry, `.packages: ...` for an object's key,
  `: ...` for the config as a whole. A source that takes a list of entries
  uses `config.entry_problems(cfg, keys, problems, file)`, which checks the
  list, that each entry is an object, that it has no key but `keys` (and `file`),
  the entry's own keys (`problems`, in the source's words), then its file
  (`file`), prefixing each problem with its entry's path.
- **`files`**: e.g. `{"[0]": "github-issues-example-app.jsonl"}`,
  `{".packages[1]": "pypi-example-lib.jsonl"}` for an object's list, or
  `{"": "ha-platform.jsonl"}` for an object that writes one file. Each name
  is `config.file_name` of the entry
  ([CONFIGURATION.md](CONFIGURATION.md#output-files)); for a list of
  entries, `config.entry_files(cfg, file)` keys them. The runner uses it to
  refuse two entries writing one file; health uses it to know each
  source's files.
- **`collect`**: `cfg` is the source's config, already validated, with each
  shared table it names in place. Dates come from `ctx.date`, and each
  write passes `ctx.now` to `store`; a source never reads the clock. A
  failed request or an unusable answer writes a failure row and moves on
  to the next entry; it never raises, since an exception is a crash.
- **`check`**: returns `context.Finding`s, each a level (`ok`, `warn` or
  `error`) and a message, such as "the configured site_tag is the site's
  beacon token; its site tag is X". A failed or rate-limited request is a
  finding, never an exception.
- **`summary`**: a section per entry, a `config.Section`: the source's
  name and the entry's id, which title it (`github_repo_stats:
  owner/repo`), the source's `label`, a short readable name that heads
  its panel in trends (`Repo stats`), the file it reads (and `where`, the
  values its rows must have), and the fields a user checks first, each a
  `config.Shown` (a dotted field, a label and a kind: `snapshot`,
  `change`, `daily`, `value` or `breakdown`; `compare`, the spans a
  `snapshot` or `change` is compared over (`7d` and `30d` unless given);
  `format`, a format spec for its numbers; and `complement`, which shows 100
  less a percent, so that a rank's percentile, lower better, reads as the
  share ranked below it). Two settings only shape trends: `trend`, which
  draws a `daily` count's 7-day mean as a faint line over its bars, and
  `color`, which draws a field in one of the theme tokens `s1` to `s6`. A
  section may add report lines of its own (`lines`,
  `github_releases`' latest release), and
  `charts`, fields only trends draws (`github_releases`' downloads), and a
  `card`, a `config.Card` (a name, a row title and what the row shows) that
  makes it a row of a panel in trends, shared by the sections of one entry
  that name the same card (`github_traffic` and `github_traffic_daily` are
  rows of one Traffic card, neither knowing the other). An entry may
  have a second section reading the same file, told apart by a `part` after
  the name in its title (`github_issues` with `stars_and_forks`: the
  "Stars and forks" row of that card; ADR: an entry may give a second
  section, for a row of a card). For a
  list of entries, `config.entry_summary(NAME, cfg, key, file, shown,
  label)` builds them; a source with another shape builds each
  `config.Section` itself. The runner
  gives them to the default summary, which renders each as report lines
  (`config.section_lines`), to `leadline default-reports`, and to trends,
  which charts the same fields. A source that reports on a platform
  rather than the user's projects gives none (`ha_platform`).
- **`retry`**: runs after every source has collected. It asks again only
  for requests that failed, never for an answer the run got, which the run
  would serve again unchanged ([CORE.md](CORE.md#http)). It writes and
  reports as `collect` does.

**The registry is found, not listed.** `sources/__init__.py` holds `Source`,
the protocol every source satisfies, and `discover()`, which imports each
package under `sources/` whose name doesn't start with `_`, and each in a
platform group (a package that sets `GROUP = True`, has no `NAME` and is
never a source), and returns a `Registry`: `sources`, the ones that kept the
contract by name, and `failed`, the others with the exception that stopped each
(`<ExceptionType>: <message>`). A source keeps the contract when it has
every member above, of its kind, and its `NAME` is its folder's name. A
name found in two groups fails both, as `ValueError: found twice`. One
that doesn't load never stops the others (ADR: source packages are found,
not listed). The runner and the command call `discover()` once; tests give
them a `Registry` instead. `tests/test_sources.py` checks discovery, and
`tests/test_scenarios.py` that every source loads. `retry` isn't in
`Source`, since most sources leave it out; the runner looks for it. The
runner uses every member, and gives health each source's `files` and
`LAGGING`, never the module itself. The docs that list the sources are
generated from what `discover()` finds (the [source table](#source-table)).
Adding a source is in [ADDING_A_SOURCE.md](ADDING_A_SOURCE.md).

### Failures and what sources report

How a source records a failed fetch, and what it reports to health through
the context. Each report is logged as it happens, and health writes it to
`health.jsonl`.

- **Failure rows.** Every source writes them through
  `store.write_failure`, dated its expected date, with no counts (ADRs: a
  failure is recorded as a failure; a failure never replaces good data):
  - it never puts one over a good or partial row;
  - the `status`, fields and `reason` are those the script writes for that
    source;
  - a failed request's `http_code` is `HttpError.http_code`, except in
    `cloudflare_analytics_engine`, which writes the status as the script
    does, `"0"` for no response.

  A source that rewrites a window of days passes its failure row to
  `store.write_window` instead, which keeps it off a good or partial row
  the same way. A failure row alone is recoverable: the run still exits 0.
- **Partial rows.** A row built from several requests whose optional parts
  failed goes through `store.write_partial`, its failed parts null; store
  keeps what an earlier run of the day got for them
  ([CORE.md](CORE.md#partial-rows)). A required part that fails gets a
  failure row.
- **Critical errors.** A source hands every failed request's error to
  `ctx.record_unfixable(source, file, error, reason)`, which records it
  critical when the error says waiting can't fix it (`error.unfixable`, on
  an `HttpError` or a `github.GraphqlError`; [CORE.md](CORE.md#http)), so
  that test lives in one place. Anything else critical goes to
  `ctx.record_critical(source, file, reason)`. Both collect in
  `ctx.critical`. An error waiting can't fix gets both its failure row and
  its critical record. The runner records a crash the same way, with no
  file.
- **Corrections** are recorded by store, not the source. Every good row
  goes through `store.write_good`, `store.write_window` or
  `store.recompute`, which call
  `ctx.record_correction(source, file, date, old, new, second_key)` for
  each correction: `old` and `new` are the values that differ, and
  `second_key` the row's key besides its date (`{"topic_id": 7}`), empty
  when it has none. They collect in `ctx.corrections`. A source never calls
  `store.upsert`, `store.replace_window` or `ctx.record_correction`
  itself, so none can skip a correction or record one twice;
  `tests/test_boundaries.py` enforces this. Past values a source rebuilds
  from what it fetched go through `store.fill`, which changes no value, so
  it records none ([CORE.md](CORE.md#backfills)); a derived source's past
  rows go through `store.recompute`, which records each correction it
  makes ([CORE.md](CORE.md#recomputed-history)).

### Source table

Every source: what it reads, the file it writes unless its entry's `file`
says otherwise, and its config entry, each from the source's package and
README (`make docs` writes this table and the sentence below it; do not
edit them). A source's name links to its README, which holds its keys, its
credentials and what it writes. Entries are explained in
[CONFIGURATION.md](CONFIGURATION.md#source-entries).
`tests/test_source_docs.py` holds the table to the sources found and fails
for a source whose README lacks its two fields, and
`tests/test_scenarios.py` holds those sources to the scenario modules and
the goldens: every source found has scenarios and a golden, and nothing else
does.

<!-- BEGIN GENERATED source-table -->
| Source | Reads | Default file | Config entry |
| ------ | ----- | ------------ | ------------ |
| [`cloudflare_analytics_engine`](../src/leadline/sources/cloudflare/cloudflare_analytics_engine/README.md) | a Workers Analytics Engine dataset's daily totals over SQL, each dimension on its own; the last `refetch_days` days are read again every run. | `cloudflare-analytics-engine-{dataset}.jsonl` | `sources.cloudflare_analytics_engine` is a list; each entry has `dataset`, `layouts` ([see its keys](../src/leadline/sources/cloudflare/cloudflare_analytics_engine/README.md#layouts)) and, optionally, `refetch_days` (default 7, at most 90). |
| [`cloudflare_web`](../src/leadline/sources/cloudflare/cloudflare_web/README.md) | a site's page views and visits from Cloudflare Web Analytics, each row with the site tag it was read under; the last 30 days healed and corrected. | `cloudflare-web-{label}.jsonl` | `sources.cloudflare_web` is a list; each entry has `label`, and `site_tag`, the site's tag (not the beacon token in its pages). |
| [`cloudflare_zone_traffic`](../src/leadline/sources/cloudflare/cloudflare_zone_traffic/README.md) | a Cloudflare zone's HTTP traffic as a row a day, kept for good where Cloudflare keeps it for a year (daily totals) or 31 days (per-request detail): requests, cached and encrypted requests and bytes, page views, threats, unique visitors and visits, and the counts by country, status code, browser, operating system, device type, data center, path, host and more. Counts only, never a visitor's address. | `cloudflare-zone-traffic-{label}.jsonl` | `sources.cloudflare_zone_traffic` is a list; each entry has `label`, `zone`, the zone's tag (its id, on the zone's Overview page in the dashboard), and, optionally, `top`, the length of each list (default 50, at most 1000). |
| [`discourse_topics`](../src/leadline/sources/discourse_topics/README.md) | views, replies and likes of topics on a Discourse forum, a row per topic a day; each topic's days before its first row rebuilt back to its creation: replies and last post. | `discourse-topics.jsonl` | `sources.discourse_topics` is a list; each entry has `base_url`, the forum's address, and `topics`, each an `id` and a `label`. |
| [`github_contributor_locations`](../src/leadline/sources/github/github_contributor_locations/README.md) | where the people who open a repo's issues and pull requests are, from their profiles, issue-form answers and issue text, and, where the entry sets `comments`, a person's own comments, with the count per region; every past day recomputed back to the first person's first issue, each person with today's region, under today's rules. | `github-contributor-locations-{repo_name}.jsonl` | `sources.github_contributor_locations` is a list; each entry has `repo` (`owner/name`) and `regions`, the name of a table in `shared.regions` that maps keywords to regions. `comments` (optional, `true`) reads comments as below. A table that sets `attachment` and `attachment_value` also reads attached files as below. |
| [`github_discussions`](../src/leadline/sources/github/github_discussions/README.md) | a repo's discussions: totals, community and maintainer, answers, categories and the most recent ten; the days before the file's first row rebuilt back to the first discussion, comments included, where the queries read them all. | `github-discussions-{repo_name}.jsonl` | `sources.github_discussions` is a list; each entry has `repo` (`owner/name`) and `maintainer`, the login counted apart from the community (required). |
| [`github_issues`](../src/leadline/sources/github/github_issues/README.md) | a repo's issues and pull requests: counts, flow, who opened them, ages, engagement, labels, triage; optionally one label's queue; the issues opened and closed, ever, filled into each past row that lacks them, as of its day, and with `kinds` the issues opened by kind, counted again under the kinds on every run; the days before the file's first row rebuilt back to the first issue: open issues and pull requests, and the totals; the repo's stars and forks, filled into the past rows before the first with them, from the dates GitHub keeps; with `stars_and_forks` and `regions`, those stars and forks counted by region. | `github-issues-{repo_name}.jsonl` | `sources.github_issues` is a list; each entry has `repo` (`owner/name`); `maintainer` (optional; empty or absent for none, when everyone who isn't a bot is community); `queue` ([see its keys](../src/leadline/sources/github/github_issues/README.md#queue)), `regions` (a table's name in `shared.regions`, for the queue and for the [stars and forks by region](../src/leadline/sources/github/github_issues/README.md#stars-and-forks-by-region)) and `kinds` ([see its keys](../src/leadline/sources/github/github_issues/README.md#kinds)), each optional; `stars_and_forks` (a boolean, off by default: [see below](../src/leadline/sources/github/github_issues/README.md#stars_and_forks)). |
| [`github_releases`](../src/leadline/sources/github/github_releases/README.md) | a repo's releases, with each asset's downloads. | `github-releases-{repo_name}.jsonl` | `sources.github_releases` is a list; each entry has `repo` (`owner/name`) and, optionally, `bands` ([see its values](../src/leadline/sources/github/github_releases/README.md#bands)) and `exclude` ([see it](../src/leadline/sources/github/github_releases/README.md#exclude)). |
| [`github_repo_activity`](../src/leadline/sources/github/github_repo_activity/README.md) | one public repo's week: commits, authors, fixes and redesigns, issues, contributors, the latest release, the code size and its split by language. | `github-repo-activity-{label}.jsonl` | `sources.github_repo_activity` is a list; each entry has `repo` (`owner/name`); `label` (default: the repo's name); `fix_pattern` and `redesign_pattern`, regexes matched ignoring case against each commit's first line (the defaults are the script's). |
| [`github_repo_stats`](../src/leadline/sources/github/github_repo_stats/README.md) | a repo's stars, forks, watchers and open issues, with its 14-day traffic totals. | `github-repo-stats-{repo_name}.jsonl` | `sources.github_repo_stats` is a list; each entry has `repo` (`owner/name`). |
| [`github_response_times`](../src/leadline/sources/github/github_response_times/README.md) | how long community issues wait for an answer: the maintainer's first reply or the issue's closing, whichever comes first; every past day recomputed back to the first community issue, under today's rules. | `github-response-times-{repo_name}.jsonl` | `sources.github_response_times` is a list; each entry has `repo` (`owner/name`) and `maintainer`, the login counted apart from the community (required). |
| [`github_traffic`](../src/leadline/sources/github/github_traffic/README.md) | a repo's 14-day views, clones, top referrers and top paths, as one snapshot. | `github-traffic-{repo_name}.jsonl` | `sources.github_traffic` is a list; each entry has `repo` (`owner/name`). |
| [`github_traffic_daily`](../src/leadline/sources/github/github_traffic_daily/README.md) | a repo's views and clones, a row per day, backfilled across the 14-day window. | `github-traffic-daily-{repo_name}.jsonl` | `sources.github_traffic_daily` is a list; each entry has `repo` (`owner/name`). |
| [`github_tree_counts`](../src/leadline/sources/github/github_tree_counts/README.md) | the paths in a repo's git tree that match a pattern: files, items, groups and items per group; optionally the files counted by a value read from each one's text, each blob read once; the days before the file's first row rebuilt from git history, back to the first tree that matches, and with the counts by value, every day through yesterday. | `github-tree-counts-{repo_name}.jsonl` | `sources.github_tree_counts` is a list; each entry has `repo` (`owner/name`); `pattern`, a regex searched in each path, so `^` anchors it (group 1 is the group, group 2 the item, other groups are ignored), or a list of them for files that have lived in several places ([see patterns across a move](../src/leadline/sources/github/github_tree_counts/README.md#patterns-across-a-move)); `ref` (default `HEAD`); `keys`, the row keys for `files`, `items`, `groups` and `items_per_group` (defaults: those names); `content` (optional, [see its keys](../src/leadline/sources/github/github_tree_counts/README.md#content)). |
| [`goatcounter`](../src/leadline/sources/goatcounter/README.md) | a site's page views by day, backfilled across the week, and its top referrers, from GoatCounter's API. | `goatcounter-{site}.jsonl` | `sources.goatcounter` is a list; each entry has `site`, the site's code, as in `<site>.goatcounter.com`. |
| [`ha_integration`](../src/leadline/sources/home_assistant/ha_integration/README.md) | a custom integration's installs by version, and its rank among all of them, from Home Assistant's analytics. | `ha-integration-{integration}.jsonl` | `sources.ha_integration` is a list; each entry has `integration`, the integration's domain, as the feed lists it (a name the feed doesn't list reads as no installs every night, and `check` says so), and, optionally, `bands` ([see its values](../src/leadline/sources/home_assistant/ha_integration/README.md#bands)). |
| [`ha_platform`](../src/leadline/sources/home_assistant/ha_platform/README.md) | Home Assistant's platform-wide analytics: active installations, how many report integrations, add-ons and statistics, installation types and the top countries. It is the denominator for `ha_integration`'s installs. | `ha-platform.jsonl` | `sources.ha_platform` is one object, not a list, and has nothing to set but `file`. |
| [`pypi`](../src/leadline/sources/pypi/README.md) | each package's downloads by day from pypistats, backfilled from its history, and its version and required Python from PyPI; a retry pass asks again for a package whose history request failed. | `pypi-{package}.jsonl` | `sources.pypi` is one object, not a list. `packages` is the PyPI names, in order (each night starts one place further along); `sleep_s` is the seconds between pypistats requests; `retry_wait_s` is the seconds the retry pass waits first. |
<!-- END GENERATED source-table -->

<!-- BEGIN GENERATED lagging-sources -->
The lagging sources are `cloudflare_analytics_engine`, `cloudflare_web`,
`cloudflare_zone_traffic`, `github_traffic_daily` and `goatcounter`.
<!-- END GENERATED lagging-sources -->

## Health

What `health.jsonl` records each run, and how. `health.py` writes one row
for the run date, after the checks, through `store.upsert`, so a rerun
replaces the day's row, as the script's does.

The row keeps every field the script's `check_freshness` writes, in its
order, then adds `checks`, `critical` and `corrections`:

```text
date, script_version, script_hash, checked, stale_count, stale, failed_count, failed,
gap_window_days, gap_count, gaps, checks, critical, corrections, captured_at
```

| Field | Holds |
| ----- | ----- |
| `script_version` | the package version |
| `script_hash` | the start of the config file's SHA-256 (`Config.config_hash`), as long as the script's hash of itself: a synced config edit shows as a new hash |
| `checked` | how many row files were read |
| `stale` | each file whose newest row is more than a day behind its expected date: `file`, `last`, `age_days` (both null for a file with no dated row) |
| `failed` | each file whose newest row is a failure row: `file`, `last`, `status`, `streak`, `since` |
| `gaps` | each file's missing and empty days in the last 14, from its first row to its newest: `file`, `window`, the counts, and the first ten of each |
| `checks` | each `check()` finding, in order, as `{"source", "level", "message"}` |
| `critical` | everything critical so far, in the order recorded, as `{"source", "file", "reason"}` (`file` null for a crash) |
| `corrections` | the run's corrections, in order, as `{"source", "file", "date", "old", "new", "second_key"}` |

- **What it reads.** Every `*.jsonl` in the output folder but
  `health.jsonl`, whoever writes it, each date's last line winning. A line
  that isn't a row, or whose `date` isn't a `YYYY-MM-DD` date, is skipped
  where the script stops and writes no row (ADR: store never fails on a
  line it can't use).
- **What the runner gives it.** Each file a configured source writes, with
  its source and whether the source lags, and the checks' findings. Health
  reads the rest from the output files and the context.
- **Critical files.** Health itself records critical each file a configured
  source writes with no good row for two or more consecutive expected dates
  ([Terms](#terms)), with the reason `no good row for N nights, since DATE`.
  A file with several series (a row a day for each topic) has a good date
  while any series does; a series that fails on its own is critical when its
  error is one waiting can't fix. It judges every configured source,
  including one left out by `--only` or skipped for its credentials, since
  its file is no fresher for it. A file no configured source writes is never
  critical, and never lagging.
- **What it misses.** A report block or trends file that fails is critical
  too, but after this row is written; the log and the exit status carry
  it.
- **A same-day rerun loses the first run's corrections.** It replaces the
  day's row, so a `corrections` list from an earlier run that day is gone
  from health; the rows themselves stay corrected.
- **Log lines** are the script's: `STALE:`, `FAILED:` and `GAP:` per file,
  then a `WARN:` count of stale and of failed files, or `All metrics
  sources current`.

A report block shows the `critical` list at the top of SUMMARY.md
([REPORTS.md](REPORTS.md#the-critical-block); ADR: critical sources
surface at the top of SUMMARY.md).

## Row diff

How `leadline diff` compares two folders. It lives in the package
(`rowdiff.py`), so it runs wherever leadline runs, and the port tests use
it.

```text
leadline diff A B
```

- **Match rule.** It compares the row files (`*.jsonl`) directly in `A` and
  `B`. Two files match when their text is identical once every
  `captured_at` value is blanked: the same rows in the same order, the same
  keys in the same order, the same values and serialization. `captured_at`
  must still be there.
- **Output.** One line per difference:
  - a file only in A or B;
  - a row only in A or B, by its key (`date`, plus `topic_id` when the row
    has one; a line with no key by its text), or a different number of
    rows for one key;
  - a field that differs, with both values;
  - key order, or the same values serialized differently;
  - when every row matches, line order and layout.
- **Summary.** A last line counts the files and the differences and names
  each file that differs with its count (`29 file(s) compared: 3
  difference(s), in forum.jsonl (1), hacs.jsonl (2)`).
- **Exit status.** 0 when the folders match, 1 when they differ, 2 when A
  or B isn't a folder.
