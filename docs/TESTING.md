# Testing

How leadline is tested against the script it replaces: scenarios, goldens,
port tests, the oracle harness, and the checks that keep private data out
of the repo. The commands are in
[CONTRIBUTING.md](../CONTRIBUTING.md#setup-and-checks).

## How a source is proved

Each source is written test first, in three stages:

1. **Oracle.** The real script (`collect-metrics-script.sh`; the script's
   launcher keeps the name `collect-metrics.sh`) runs
   on each of the source's [scenarios](#scenarios) (made-up responses),
   with `curl`, `date` and `sleep` replaced by shims ([Oracle
   harness](#oracle-harness)). The rows
   it writes are the expected rows, committed as one golden file per
   source.
2. **Port.** The source's tests run it on the same scenarios and compare its
   rows with the goldens using `rowdiff`, which ignores `captured_at`
   ([Port tests](#port-tests)). These need only the repo, so CI runs them.
3. **Diff.** `leadline diff` compares the row files of two metrics
   folders, ignoring `captured_at`. Run on two folders that began from the
   same rows, it lists every row leadline writes differently from the
   script ([Row diff](ARCHITECTURE.md#row-diff)).

Test data is made up in code and never holds secrets or anything real. A
real capture is a private reference for the shape of API responses, never
copied into the repo (ADR: test data is made up in code; the real capture
is a private shape reference).

## Local settings and the oracle split

What the oracle tests need, where it lives, and how the other tests are
kept from it.

Four things stay out of the repo: the real script, a capture of one night's
responses, a private list of [forbidden terms](#forbidden-terms), and the
maintainer's leadline config ([Reports against the
script](#reports-against-the-script)). Their paths are in one local
settings file, `.leadline-local.json` at the repo root, which git ignores.
`.leadline-local.example.json` shows its form:

```json
{"script": "/path/to/collect-metrics-script.sh", "capture": "/path/to/capture/YYYY-MM-DD",
 "forbidden_terms": "/path/to/forbidden-terms.txt", "leadline_config": "/path/to/leadline.json"}
```

- `LEADLINE_LOCAL_SETTINGS` names another settings file
  (`tests/oracle/local_settings.py`).
- The tests that need any of the four carry the `oracle` marker. `make
  check` and CI leave them out. `make oracle` runs only them, and stops
  before the first, naming each setting that isn't set or names nothing.
- `conftest.py` at the repo root holds the split, for the tests in `tests/`
  and in the `tests/` folder of a source or API package alike: a test
  without the marker that opens a file a setting names, or lists or opens
  anything under it, fails at that call.

## Tests in packages

A source or platform group keeps its tests in its own `tests/` folder, which
is a package (`__init__.py`), so test module names stay unique:
`test_<name>.py`, `scenarios.py`, `golden.json`, `shapes.py` and, for a
platform, `builders.py`. `pyproject.toml` sets `testpaths` to `tests` and
`src/leadline`, so `make check` and `make oracle` collect them with the
tests in `tests/`, and ruff holds them to the same ignores. The build leaves
them, and every Markdown file, out of the zipapp and the wheel
(`tools/build_pyz.py`, the wheel's `exclude`); `tests/test_pyz.py` proves it
on a scratch package, as `tests/test_boundaries.py` proves its rules on
scratch trees (`tests/scratch_package.py` makes one for the other tests).

Every source is a package, so its tests, scenarios, golden and `KINDS` are
in its `tests/` folder: `src/leadline/sources/<name>/tests/`, or
`src/leadline/sources/<platform>/<name>/tests/` when it sits in a platform
group. A platform's builders are in its own folder: `tests/builders.py` of
the group (`github`, `cloudflare` and `home_assistant`, which several
sources share) or of the source package (`pypi`, `goatcounter` and
`discourse_topics`, which have one source each). The shared GitHub code's
tests are in `src/leadline/sources/github/tests/`.

## Generated docs

The docs that list the sources are generated, so they cannot drift from the
sources (`tools/gen_source_docs.py`, `make docs`; [Adding a
source](ADDING_A_SOURCE.md#7-document-it)). Between `<!-- BEGIN GENERATED
name -->` and `<!-- END GENERATED name -->` markers they hold the source table
and the lagging sources (ARCHITECTURE.md), and the list of sources and the
credentials table (CONFIGURATION.md). Each block is made from the discovered
sources and their READMEs' `Reads` and `Config entry` bullets.
`tests/test_source_docs.py`:

- regenerates every block and compares it with the file, so a changed source
  or README fails until `make docs` runs;
- fails the generator for a source with no README, a missing or empty field,
  a `|` or a link that leaves the README in a field, or a source that failed
  to load;
- checks the generator on scratch packages: the order, the lagging sentence
  for any count, the credentials table, a block replaced in place;
- holds every link in a source's README to an existing file and heading.

## Places data

The places core knows (`src/leadline/places/*.json`, one file per standard) is
generated from the standards by `tools/refresh_places.py` (`make places`). No
test reaches the network: `tests/test_refresh_places.py` holds the parsing, the
sorted output, each file's refresh on its own (an unchanged file keeps its
date, a failing source leaves its file as it was) to small fixture text, and
`tests/test_places.py` holds the matching (whole-word names, US abbreviations,
zones, the merge order and the region-code check) to the vendored data.
`tests/test_places.py` also holds each file's shape and that the zones are read
only when a table needs them. `tests/test_pyz.py` proves the archive carries
the three files and resolves a country and a zone from them.

## Operating systems

CI runs the tests on Linux with Python 3.8 through 3.14, and on macOS and
Windows with 3.14. On Windows, two kinds skip, with the reason:

- **The script's code.** The script, its Python heredocs and the harness's
  shims run on Linux, so the tests that run them
  (the [oracle harness](#oracle-harness) tests, the scenario
  runner's, and store's comparisons with the script's writers) carry
  `script_harness.script_side`.
- **A zone other than UTC.** A port test sets its scenario's `tz` through
  `TZ` and `time.tzset`, which Windows lacks, so a scenario in a named zone
  skips there (`port.local_time`). UTC needs neither.

## Scenarios

The made-up cases every source is tested on. Each scenario is named for
the behavior it proves (`retries_until_the_request_succeeds`,
`a_failure_keeps_a_good_row`). Together a source's scenarios cover its code
paths in the script: normal data, pagination, retries, failure rows,
windows and backfills, and the odd values the script handles.

```text
tests/scenarios/
  builders/
    common.py           the shared types, `exchange` and `failed`
  health.py             health's scenarios (health is not a source)
  golden/health.json    the rows the script writes for them
  __init__.py           Scenario, the runner, the goldens; `python -m scenarios`
```

A source's scenarios are in its package: `tests/scenarios.py` and
`tests/golden.json` in its `tests/` folder (`src/leadline/sources/<name>/`,
or inside its platform group). `scenarios.place(name)` is the one lookup
(`tests/port.py` and the proof tests ask it), and `scenarios.sets()` finds
the packages' sets by walking `sources/` to any depth, with health's, the one
set that has no package. A name found in both places is an error.

`python -m scenarios` and `make goldens` write each set's golden where its
set is.

- **Builders.** A plain function per kind of API response, in one module per
  platform, in the platform's package
  (`src/leadline/sources/<platform>/tests/builders.py`;
  `tests/scenarios/builders/` holds only `common`). A
  builder module imports `common` and its own platform's builders, no other
  platform's, so removing a platform removes its builder module. A scenario
  imports from the modules it uses. The kinds: a GitHub repository, issue, release,
  commit, git tree or blob, and a page of a list with its Link header; a
  GraphQL result; a GoatCounter, Discourse, pypistats, PyPI or Home
  Assistant document; a Cloudflare GraphQL or Analytics Engine SQL result.
  Each returns the body as the API gives it. Arguments are what a scenario
  steers, with defaults for everything else. Builders that make many items
  take a seeded `random.Random`, so the data is the same on every run and
  every Python. Every value is made up: logins, locations, counts, names.
- **Scenario.** The script's calls; dummy credentials (`env`); overrides
  for the script's constants (`config`); row files already in the metrics
  folder (`seed`); the frozen local time (`now`) and its zone (`tz`, UTC
  unless set); and the responses, from the builders. A source's scenarios
  are the list `SCENARIOS` in its module.
- **Reviewed rows.** What the script doesn't do has no oracle: a source
  it doesn't have, or a source's own behavior, such as the history a
  source rebuilds or recomputes (ADRs: past values a source can rebuild
  are filled, never changed; derived history follows today's rules). Such
  scenarios give no
  calls and carry `expected` instead: every row file the
  source must leave in the metrics folder (seed files included), with its
  text, written by hand and reviewed. A scenario gives calls or
  `expected`, never both.
- **Runner.** `scenarios.run` writes a scenario as a [case
  folder](#case-folders), runs it through the oracle harness, and returns
  every row file left in the metrics folder, with its text. It refuses a
  run that exits nonzero, makes a request with no saved response left, or
  never reaches one of the saved responses. So a scenario's responses are
  exactly the requests the script makes, retries included, and a failure
  scenario stops at the failing response.
- **Goldens.** `golden/<source>.json` (or `tests/golden.json` in a
  package) holds, for each scenario, its rows (each row file's exact text,
  `captured_at` included, at the frozen time) and `case_sha256`, a
  fingerprint of the scenario's inputs. `make goldens` (`make goldens
  SOURCES=ha_integration` for some) reruns the script and rewrites them. A
  scenario with `expected` rows has them as its golden, and making it runs
  nothing; the review is the approval.
- **Proof.**
  - In CI, `tests/test_scenarios.py` checks that the sources found
    (`sources.discover()`), none failed to load, are exactly those with
    scenarios and a golden file (health aside), and that every scenario has a
    current golden (its fingerprint matches; a reviewed one is its `expected`
    rows). It also tests the runner against a stand-in script.
  - In CI, `tests/test_source_docs.py` regenerates the docs that list the
    sources ([Generated docs](#generated-docs)) and compares them with the
    files.
  - Marked `oracle`, `tests/oracle/test_scenario_goldens.py` reruns the
    script on every scenario and requires the committed golden.
  - Marked `oracle`, `tests/oracle/test_builder_shapes.py` holds each
    builder to the real responses of its kind in the capture: every built
    object has the keys of a real object there, every value a type the API
    gives for that field, every part of the real responses appears in a
    built one, and built headers (Link, in the API's own form) are headers
    the API sends. Where the capture has no example (an empty list, a field
    null in every response, a Link header), the test names the place. The
    `Kind`s are listed by the packages, as `KINDS` in a source's or API's
    `tests/shapes.py`, which the test finds by walking (a kind several
    sources of one API read is in the API's); `Kind` is in
    `tests/oracle/shape_kind.py`.

### Analytics Engine scenarios

The script names its Analytics Engine collector, and that collector's row
file, after the maintainer's app; the `cloudflare_analytics_engine`
scenarios keep the name out of the repo:

- they find the collector by its shape (`collect_<name>_usage`);
- they rename its row file to `usage-<label>.jsonl` before the run (for a
  seed) and after it (for the golden);
- the script reads no layout newer than its own, so the scenarios of newer
  layouts give no calls and carry reviewed rows.

### Health scenarios

Health has scenarios too, though it isn't a source (`scenarios.NOT_SOURCES`;
`scenarios.sets()` lists every set, `scenarios.sources()` the sources).

- `tests/scenarios/health.py` seeds row files and runs the script's
  `check_freshness`, with no requests.
- `golden/health.json` holds `health.jsonl` as the script writes it.
- `tests/test_health.py` writes leadline's row for the same files and
  compares it with the golden, apart from its own `script_version`,
  `script_hash` and added fields, which it asserts on their own
  ([Health](ARCHITECTURE.md#health)).

## Port tests

How a source's tests run it on a scenario the way the harness runs the
script. `tests/port.py` does this, and each source's `test_<source>.py` (in
`tests/`, or in its package's `tests/` folder) uses it.

- **Config.** The scenario module gives `LEADLINE_CONFIG`, the source's
  config for its scenarios, built from the same constants as the script's
  calls (its label, repo or site, and the `file` that keeps the script's
  file name). Where the script's calls or constants differ between
  scenarios, it gives `leadline_config(scenario)` instead, which builds
  each one's (`github_issues`: a queue where the script is given the
  pipeline label).
- **Run.** `port.run(source, config, scenario, folder)`:
  - writes the seed files into the folder;
  - runs the source through the real runner's collection
    (`runner.validated`, then `runner.collect`: `validate`, `files`, the
    credential skip, `collect`, the retry pass; not the checks or health,
    which have tests of their own);
  - gives it only the scenario's `env` as credentials, and its `now` in its
    `tz` as `ctx.now`;
  - serves the saved responses through `http.SavedResponses`
    ([Saved responses](CORE.md#saved-responses)) exactly as the harness
    serves them: each once, several for one request in order. A request
    with none left, or a response never reached, fails the test, so the
    source makes the script's requests, retries included. A GET answered
    once is reused for the rest of the run
    ([Answer reuse](CORE.md#answer-reuse)), so only a failed GET, or a POST,
    is served again; each source runs alone, so its requests are its own;
  - records waits between attempts instead of sleeping;
  - returns the exit status, the row files, the context (its corrections
    and critical errors) and the log.
- **Compare.** `port.golden_differences(source, scenario, folder)`
  compares the folder with the scenario's golden through `rowdiff`; a match
  is no differences.
- **Divergences.** Where a decision makes leadline's rows differ from the
  script's, the scenario is listed in its module's `DIVERGENCES`, with the
  decision. `port.golden_scenarios(source)` gives every other scenario, for
  the golden comparison, and the source's tests assert leadline's side of
  each divergence explicitly.
- **Summary.** `port.summary(source, config, scenario, folder)` renders
  the source's `summary()` from the scenario's golden rows through the
  reports engine, checking that it is a valid report config, and returns
  its lines.

## Case folders

The format the harness runs, and the format of every saved response
(scenarios and the capture). The scenario runner writes each
scenario as one, in a temporary folder:

```text
<case>/
  case.json    {"now": "2026-09-23T23:31:05", "tz": "UTC", "calls": ["collect_hacs"], "env": {...}, "config": {...}}
  responses/   one saved request and its response per *.json file
  seed/        optional: row files the metrics folder holds before the run
```

`case.json`:

| Key | Meaning |
| --- | ------- |
| `now` | the frozen local time, naive ISO, in `tz` |
| `tz` | a TZ name such as `America/Los_Angeles`; UTC when absent |
| `calls` | the script's bash lines to run: a collector (`collect_github_issues "owner/repo"`) or `main` |
| `env` | optional dummy credentials |
| `config` | optional values for the script's constants (a string, or a list for an array), in place of its own |

A saved response is
`{"request": {"method", "url", "body"}, "response": {"status", "headers", "body"}}`,
or `"response": {"error": REASON}` for a connection that fails.

- A request matches on method, URL and body exactly (`body` null or absent
  for none; `curl -d @file` bodies with CR and LF removed, as curl sends
  them).
- `headers` is optional; `body` is the response text.
- Each saved response is served once. When several match one request they
  are served in file name order, one per call, which is how a retry after
  a 429 is saved. A request whose saved responses are all served has none
  left.

## Oracle harness

How the script runs safely in a test. `tests/oracle/script_harness.py` is a
dev tool, not part of the package; CI never needs it, since the script is
private and stays out of the repo. It runs the script the `script` local
setting names.

The script is never modified or run in place. A copy runs in a temporary
folder:

- **Its paths.** The copy's `BASE_DIR` points at the temporary folder. The
  harness refuses a script without exactly one `BASE_DIR="..."` line, or
  whose code (comments aside) still names that real folder, a NAS volume
  (`/volumeN`) or a mounted drive (`/mnt/`), so the real `community/`
  folder is never written.
- **What it runs.** The final `main "$@"` line is replaced by the case's
  `calls`, each run as main runs a collector (`CALL || true`), after the
  script's setup (`DATE`, the metrics folder, the log). `["main"]` runs the
  whole script.
- **Its constants.** A case's `config` is set just above the script's
  `DATE=$(date +%Y-%m-%d)` line: after the constants' own definitions and
  before anything the script derives from them (such as `PYPI_ORDER`). The
  harness refuses a name the script doesn't define.
- **Shims.** `curl`, `date` and `sleep` come first on PATH. `curl` answers
  from the saved responses for the options the script uses, and refuses any
  other option. `date` supports `+FORMAT` at the frozen time. `sleep`
  returns at once. The harness refuses a script that calls one by absolute
  path, or starts `python3` with `-I`, `-E` or `-S` (which would skip the
  Python shim).
- **Python.** A `sitecustomize` on PYTHONPATH runs in every Python the
  script starts: `datetime.now()` returns the frozen time, `time.sleep`
  returns at once, `urllib.request.urlopen` answers from the saved
  responses, and every other socket connection or DNS lookup raises. If it
  can't install itself, the process exits rather than run with the network
  open.
- **Fail closed.** A request with no saved response left fails as if
  offline and is recorded. Nothing reaches the network, so no test can
  call pypistats.org.
- **Environment.** Built from scratch: nothing is inherited but PATH, so no
  credential reaches the script unless `env` gives a dummy one. `TZ` is the
  case's `tz`.

The result is the copy's metrics folder.
`tests/oracle/test_script_harness.py` proves the shims and the refusals
against a small stand-in script, in CI.

## The capture

The capture is a private folder of one night's real requests and responses,
one saved response per file, `NNNN-<caller>.json`, in the
[case-folder format](#case-folders). Request headers aren't recorded,
response cookies are dropped, and the values of the four
[credential variables](CONFIGURATION.md#credentials) become `<NAME>`
wherever they appear. It holds real metrics and contributors' logins and
locations, so it stays outside the repo, and the only test that reads it is
the builder shape test, in place. No tool in the repo makes one.

## Forbidden terms

How the repo is kept free of private words. A private list (the
`forbidden_terms` local setting, one term per line, `#` for comments) names
words that must never be in the repo, such as the name of the maintainer's
app.

- **Matching.** `tests/oracle/forbidden_terms.py` matches each term
  ignoring case and the separators space, `_`, `-` and `.`, and never
  prints one: a finding is a file, line and column and the term's number
  in the list, with any term in a file name starred out.
- **The working tree.** `tests/oracle/test_no_forbidden_terms.py` (marked
  `oracle`, so `make oracle` runs it) searches the name and the text of
  every file git tracks or would track (untracked, not ignored).
- **The history.** `python tests/oracle/forbidden_terms.py --history`
  counts, per term, the file contents, file names and commit messages in
  every ref's history that hold it. History keeps what a commit removes,
  so this checks a rewrite of history; it isn't part of `make oracle`.

`tests/test_forbidden_terms.py` tests the matching and both searches on a
made-up term in a scratch repository, in CI.

## Reports against the script

How the maintainer's report blocks are held to the script's own report
writers. `tests/oracle/test_reports_oracle.py` (marked `oracle`) reads the
`reports` of the `leadline_config` local setting.

- **Same input.** The script runs its freshness check and its writers,
  through the oracle harness, on a copy of the real row files beside it,
  late on their newest date. leadline's reports then run on a copy of what
  the script left, so both read the same rows, the health row included.
  The real folder is only read.
- **The lines file.** The config has one file of lines blocks, the
  counterpart of the file the script's lines writer rewrites whole
  (`../TRENDS.md`, a constant in the test); the two names may differ. A
  config with more or fewer fails the test.
- **Starting files.** Both sides start from the real report files with any
  leadline marker lines taken out, so the test holds whether or not they
  carry markers; the script's side holds the lines file under the script's
  name. For leadline, each report file then starts as its first write
  finds it. A file of table blocks is that text with markers put around
  the tables the blocks name (by their header line). The lines file starts
  empty, as the script writes it whole.
- **Match rule.** A file of table blocks, its marker lines taken out, must
  be the script's file byte for byte, so the tables match and nothing
  outside them is lost. The lines file must hold, in its blocks with
  something to show, a blank line apart, the script's lines file.
- **Allowed differences** are listed in the test, each with its reason:
  the script's stale line (it uses its own list of lagging files, where
  leadline uses [one definition](ARCHITECTURE.md#terms)); the version line
  (the page names what generated it, `- leadline <version>` where the
  script writes `- Script <version>`); and the script's section headings
  over nothing (a block with nothing to show is only its markers).
