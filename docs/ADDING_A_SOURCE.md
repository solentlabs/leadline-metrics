# Adding a source

The steps for adding a source, in order. The contracts they follow are in
[ARCHITECTURE.md](ARCHITECTURE.md#sources) and [CORE.md](CORE.md), and
the reasons in [ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md);
this guide links to them rather than restating them. The running example
is `nuget_downloads`, a hypothetical source that reads a NuGet package's
downloads.

In short, adding a source is:

1. a folder under `src/leadline/sources/` (or inside its platform's folder,
   when the service has several sources) holding `spec.py`, `source.py`,
   `__init__.py`, a `README.md` and `tests/`;
2. `make docs`, which writes the source into the generated docs;
3. its entry in the config.

Nothing else is edited: no registry, no table, no list of credentials.
Removing a source is deleting its folder, its config entry, and running
`make docs`.

## The package

A new source is a package, `src/leadline/sources/<name>/`, that holds
everything of the source, so adding it is adding a folder and removing it is
deleting one. `pypi` (which retries and backfills) and
`home_assistant/ha_integration` are complete examples. A service with several
sources puts them in one folder, a platform group ([Platform
groups](#platform-groups)).

```text
src/leadline/sources/<name>/
  __init__.py          re-exports the contract's members; sets __all__
  spec.py              declarations only: NAME, DEFAULT_FILE, validate, files,
                       the file-name helper, SHOWN, LABEL, summary
  source.py            the code that makes requests and writes rows: collect,
                       check, retry (if any), ENV, LAGGING
  README.md            its `Reads:` and `Config entry:` bullets, credentials,
                       what it writes, and the keys of its config entry
  tests/
    __init__.py
    test_<name>.py     the port tests
    scenarios.py       SCENARIOS, LEADLINE_CONFIG, DIVERGENCES
    golden.json        the rows the script writes (or the reviewed rows)
    shapes.py          KINDS, for the oracle's shape test (once a capture has
                       the API's responses)
```

- `spec.py` imports only `leadline.config`, its siblings (never
  `source.py`) and, for a source in a group that has one, the group's
  `spec` module (`leadline.sources.github.spec`: the checks every GitHub
  source's entry shares), never `http`, `store` or `context`. `source.py`
  imports from `spec.py`, never the other way. `tests/test_boundaries.py`
  enforces both.
- `__init__.py` names every member of the
  [contract](ARCHITECTURE.md#contract) that `spec.py` and `source.py` define
  (and `retry` where there is one), and `__all__` lists them; the module
  `discover()` checks is the package.
- A platform's response builders are in its own folder:
  `tests/builders.py` of the group (several sources share them), or of the
  source package (a service with one source). A builder module imports
  `scenarios.builders.common` and its own platform's builders, nothing else.
- The `tests/` folder and the README stay out of the pyz and the wheel, at
  every depth.

## Platform groups

The sources of one service sit together, so all the code of a platform is in
one folder:

```text
src/leadline/sources/<platform>/
  __init__.py          sets GROUP = True (and re-exports the shared code, if any)
  api.py, spec.py      what its sources share, if anything: requests, tokens,
                       the pure checks of an entry (sources/github/ has both)
  README.md            what is in the group
  tests/
    __init__.py
    builders.py        the response builders its sources' tests use
    shapes.py          KINDS several of its sources read
  <name>/              each source: a package as above
```

- `GROUP = True` is how `discover()` knows to look inside; a group has no
  `NAME` and is never a source. A source's folder name is its `NAME` and its
  config key, wherever it sits, and it must be unique across all groups.
- A group imports only the core and itself. A source imports the core, its
  own modules and its group's shared modules (`from leadline.sources.github
  import api as github`, `from leadline.sources.github.spec import
  repo_problems`), never another source or another group.
- To add a source to a group, add its package inside the group's folder
  and follow the steps below; the group's builders get the new kinds of
  response.
- To add a platform with several sources, make the group's folder with its
  `__init__.py` (`GROUP = True`), `README.md` and `tests/builders.py`, then
  add each source inside it. A platform with one source is just its package,
  with `tests/builders.py` in its own `tests/`.

## 1. Name it

Name the source for the platform and the data it reads, never for a project
or a purpose: `nuget_downloads`, not `example_app_downloads` or
`similar_projects` (ADR: sources are named for what they read). The name is
the config key, the package's (or module's) name, the scenario module's
and the golden's, and the first column of its row in the generated source
table, so it is unique.

## 2. Design its config entry

Follow [Source entries](CONFIGURATION.md#source-entries) in CONFIGURATION.md:

- A list of entries, one per thing it reads. `nuget_downloads` takes
  `[{"package": "Example.Package"}]`. A single object only when the source
  works across all its items at once, as `pypi` does.
- Its default file is its name with dashes, then the entry's id
  (`config.default_file(NAME, "package")`, here
  `nuget-downloads-{package}.jsonl`); every entry may override it with
  `file`.
- Everything specific to one user (which packages, patterns, the row keys a
  domain gives its counts, report titles) is config, never code. Examples in
  the repo use placeholders.
- A table several entries use goes under `shared`, and each entry names it.
  A new kind of shared table is a new section in `config.SHARED`.

## 3. Write its scenarios

Test first. Add `src/leadline/sources/nuget_downloads/tests/scenarios.py`
(with the package's `tests/__init__.py`) holding `SCENARIOS`, a
handful of scenarios, each named for the behavior it proves, that together
cover the source's paths: normal data, pagination, retries, failure rows,
windows and backfills, odd values ([Scenarios](TESTING.md#scenarios) in
TESTING.md).

- **Builders.** Each kind of API response gets one builder in
  the platform's package, `src/leadline/sources/<platform>/tests/builders.py`
  (here `src/leadline/sources/nuget_downloads/tests/builders.py`; it imports
  `scenarios.builders.common` and its own platform's builders, no other
  platform's), returning the body
  as the API gives it, with every value made up (ADR: test data is made up in
  code). Reuse a builder when the kind already has one.
- **Goldens.** Where the script collects the same data, a scenario gives the
  script's `calls`, and `make goldens SOURCES=nuget_downloads` writes the
  rows the script writes. The script has no NuGet collector, so each
  `nuget_downloads` scenario gives no calls and carries `expected`: every
  row file the source must leave, with its text, written by hand and
  reviewed. Its golden is those rows; `make goldens` copies them and runs
  nothing.

```python
Scenario(
    "writes_the_days_total",
    calls=[],
    responses=[exchange(SEARCH_URL, nuget_search("Example.Package", downloads=1234))],
    expected={
        "nuget-downloads-Example.Package.jsonl": '{"date": "2026-09-23", "package": "Example.Package", '
        '"downloads": 1234, "captured_at": "2026-09-23T23:31:05+00:00"}\n'
    },
)
```

- **Shapes.** `tests/oracle/test_builder_shapes.py` holds each builder to
  the real responses of its kind in the private capture, one `Kind` per
  kind of response, listed as `KINDS` in the package's `tests/shapes.py`
  (the test finds it by walking); a kind several sources of one group read is
  listed in the group's `tests/shapes.py`. The capture holds only
  what the script requests, so a new API has no real response there, and a
  `Kind` for it would fail. Build its responses from the API's documentation,
  name the document in the builder's docstring, and add its `Kind` once a
  capture holds that API's responses.

## 4. Write its port tests

`tests/test_nuget_downloads.py` in the package's `tests/` folder runs the
source on its scenarios with `tests/port.py`
([Port tests](TESTING.md#port-tests) in TESTING.md):

- In the scenario module, add `LEADLINE_CONFIG`, the source's config for
  its scenarios, from the same constants as the script's calls, with the
  `file` that keeps the script's file name where it differs from the
  default; where the scenarios' calls or constants differ, a function
  `leadline_config(scenario)` builds each one's instead. Add
  `DIVERGENCES`, each scenario where a decision makes leadline's rows
  differ from the script's, with the decision.
- One parametrized test compares every other scenario with its golden:

```python
@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Scenario, tmp_path: Path) -> None:
    port.run(nuget_downloads, LEADLINE_CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
```

- Each divergence gets its own test of leadline's side, on
  `port.scenario(NAME, name)`; other behavior gets tests on scenarios made
  with `dataclasses.replace` (new responses or seed files). `port.run`
  runs the runner's collection (not the checks or health) and returns the
  exit status, the row files, the waits, the log and the context, with
  what the source reported (`corrections`, `critical`).
- `check` is tested on its own: `port.context` with `http.SavedResponses`
  and `port.credentials`, one test per finding, and API trouble as a
  finding.

## 5. Write the package

Test first: write the port tests (step 4) before the package, and see them
fail. `src/leadline/sources/nuget_downloads/` provides what the
[contract](ARCHITECTURE.md#contract) in ARCHITECTURE.md lists: `NAME`,
`DEFAULT_FILE`, `ENV`, `LAGGING`, `validate`, `files`, `summary`, `collect`
and `check`, split as [The package](#the-package) says: the declarations in
`spec.py`, the rest in `source.py`, all re-exported by `__init__.py`. It
imports only the core (`config`, `context`, `http`, `places`, `store`), its own
modules and its group's shared ones, never another source;
`tests/test_boundaries.py` enforces that.
`src/leadline/sources/home_assistant/ha_integration/` is a complete example
(`sources/github/github_traffic/`, a GitHub source, is another).

The example below is one module, to read at a glance. In the package,
`NAME`, `DEFAULT_FILE`, `validate`, `_problems`, `files`, `SHOWN`, `LABEL`,
`summary` and `_file` (named `file_name`, since `source.py` calls it) go to
`spec.py`; the rest goes to `source.py`, which calls `spec.file_name(entry)`
and passes `spec.NAME`.
A GitHub source uses `github` (`from leadline.sources.github import api as github`;
[GitHub](CORE.md#github) in CORE.md) for what every GitHub source shares:
`ENV = (github.CREDENTIAL,)`, `github.headers()` on every request to
`github.API`, `github.numbered_pages` for a list asked for page by page,
`github.graphql` for a GraphQL query (its `GraphqlError` handed to
`ctx.record_unfixable` like an `HttpError`), and `github.check_repo`,
`github.check_traffic`, `github.check_access`, `github.check_query` or
`github.failed_finding` in `check`. Its `spec.py` takes `repo_problems` (for
`validate`) and `file_fields` (for `config.file_name`) from
`leadline.sources.github.spec`, which imports no request code.
`github_releases` is the shortest example, and `github_discussions` the
shortest over GraphQL. A source that reads an answer part by part, naming the
part in its reason, uses `http`'s readers: `read_json`, `counts` and
`unexpected`, which raise `http.UnusableError` with that reason (`views
response was not JSON`); `config.is_count` (an int, never a boolean) serves
`spec.py` too.
Asking for what another source asks for costs nothing extra: the run
reuses a GET's successful answer ([Answer reuse](CORE.md#answer-reuse) in
CORE.md).

```python
"""nuget_downloads: each package's total downloads, from NuGet's search API."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from leadline import config, store
from leadline.context import Context
from leadline.http import HttpError

NAME = "nuget_downloads"
ENV: tuple[str, ...] = ()  # a public API: no credentials
LAGGING = False
DEFAULT_FILE = config.default_file(NAME, "package")  # nuget-downloads-{package}.jsonl
SEARCH = "https://azuresearch-usnc.nuget.org/query"
KEYS = ("package",)  # the keys an entry may have, besides `file`


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, _file)


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, _file)


# The fields the default summary shows of each entry's file, and what trends heads its sections with.
SHOWN = (config.Shown("downloads", "Downloads"),)
LABEL = "NuGet downloads"


def summary(cfg: Any) -> list[config.Section]:
    return config.entry_summary(NAME, cfg, "package", _file, SHOWN, LABEL)


def collect(ctx: Context, cfg: Any) -> None:
    """Every entry in turn; one's failure row doesn't stop the next."""
    for entry in cfg:
        _collect(ctx, entry)


def _collect(ctx: Context, entry: Any) -> None:
    package, file = entry["package"], ctx.file(_file(entry))
    row = {"date": ctx.date.isoformat(), "package": package}
    try:
        downloads = _downloads(ctx.http.get_json(f"{SEARCH}?q=packageid:{quote(package)}"))
    except HttpError as e:
        # Critical when waiting can't fix it: rejected, or not there.
        ctx.record_unfixable(NAME, file.name, e, f"{package}: HTTP {e.http_code} from NuGet")
        failure = {"status": "fetch_failed", "http_code": e.http_code, "reason": e.failed("search")}
    except ValueError:  # not JSON, or not a search result
        failure = {"status": "parse_failed", "reason": "response was not a search result"}
    else:
        if downloads is not None:
            store.write_good(file, {**row, "downloads": downloads}, ctx, NAME)
            return
        ctx.record_critical(NAME, file.name, f"NuGet has no package {package}")
        failure = {"status": "not_found", "reason": "no such package"}
    store.write_failure(file, {**row, **failure}, ctx.now)  # kept out where a good row holds the date


def _downloads(answer: Any) -> int | None:
    """The first hit's total downloads, or None with no hit. Raises ValueError for any other answer."""
    hits = answer.get("data") if isinstance(answer, dict) else None
    if not isinstance(hits, list) or not all(isinstance(hit, dict) for hit in hits):
        raise ValueError("not a search result")
    if not hits:
        return None
    downloads = hits[0].get("totalDownloads")
    if isinstance(downloads, int) and not isinstance(downloads, bool):
        return downloads
    raise ValueError("no totalDownloads")


def _problems(entry: dict[str, Any]) -> list[str]:
    package = entry.get("package")
    return [] if isinstance(package, str) and package else ["package: must be a non-empty string"]


def _file(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, {"package": entry["package"]})
```

- **Validate** every key the source reads, and the `file` through
  `config.file_name`. For a list of entries, `config.entry_problems` does
  the walk: it takes the keys an entry may have (`KEYS`; any other is a
  config error, `file` aside), the source's own check of an entry's keys
  (`_problems`, each problem as `key: problem`, in the source's words) and
  its `_file`, and starts each problem with its path in the config
  (`[1].package: ...`). A source configured as one object rejects an
  unknown key itself, with `config.unknown_keys`;
  the runner adds the source's name and stops the run before anything is
  written.
- **`files`** names the file each entry writes, keyed by the entry's path:
  `config.entry_files`, from the same `_file` that `collect` calls. The
  runner refuses a config where two entries, in this source or any other,
  write one file.
- **`summary`** gives each entry's section of the
  [default summary](REPORTS.md#the-default-summary) and of
  [trends](TRENDS.md), a `config.Section`: `config.entry_summary` names
  each by the entry's id, shows the fields in `SHOWN`, each a
  `config.Shown` with its kind (`snapshot`, `change`, `daily`, `value` or
  `breakdown`; `complement` for a percent that reads better the other way
  round; `charted=False` to keep a field off trends' pages; `into` for a
  total that a breakdown of the same summary adds up to, so trends draws
  the stack alone; `neutral` for a breakdown's keys that mean none of the
  others; `by` to total a list into a breakdown,
  `releases[].assets[].downloads` by `tag`, each item at its greatest so far;
  `lines` to draw a breakdown as a line for each band instead of a stack;
  `prereleases=False` to leave a breakdown's prerelease versions out; `since`,
  with `by` and `lines`, to count days from each item's start date (its key
  names it) rather than the calendar; `half` for a breakdown whose chart
  takes half the section's width, so two share a row;
  `chart` to draw several fields as the lines of one chart; `compare`, the
  spans a `snapshot` or `change` is compared over; `format`, a format spec
  for its numbers; `trend`, a `daily` count's 7-day mean drawn over its
  bars; `color`, one of the theme tokens `s1` to `s6`; `minus`, a path whose
  value is taken from the field's, per row, for a chart-only line that the
  default summary leaves out; `band`, the path of a field of the same `chart`
  to shade the area to; `tip`, paths of other fields of the section that the
  chart's hover lists), and labels it
  `LABEL`, a short readable name for what it shows, which heads its panel in
  trends (`NuGet downloads`). Pick the few numbers a user checks first
  (`Section.charts` adds fields only trends draws, which the default summary
  doesn't show); a source that reports on a platform rather
  than the user's projects returns `[]`. `Section.notes` (`notes=config.notes(entry)`,
  with `config.notes_problems` in `validate`) puts an entry's dated remarks
  on trends' line charts. Its test renders it from a golden
  scenario's rows with `port.summary`, and `tests/test_scenarios.py` checks
  that it is a valid report config, that its sections share one label, and
  that trends charts its fields from the goldens.
- **Dates and time** come from `ctx.date` and `ctx.now`, never the clock.
  Every write goes through `store`, which stamps `captured_at` from
  `ctx.now`.
- **Good rows** go through `store.write_good(file, row, ctx, NAME)` (or
  `store.write_window(file, rows, ctx, NAME)` for a source that rewrites a
  window of days), which records each correction it makes on the context: a
  good row for a day before the run's date replaced with other values (ADR:
  every correction is recorded). A source never calls `store.upsert`,
  `store.replace_window` or `ctx.record_correction` itself;
  `tests/test_boundaries.py` fails when one does.
- **Failures** follow the ADRs, as the example does: a failed request
  (`HttpError`, its `http_code` as the row gives it) or an answer the
  source can't use (`parse_failed`) writes a failure row, a `status` and a
  `reason` with no counts, through `store.write_failure`, which keeps a
  good row in its place (a failure never replaces good data). The source
  moves on to its next entry; a failure doesn't raise, and the run still
  exits 0. An exception is a crash, a bug the runner records as critical.
  An error waiting can't fix (`HttpError.unfixable`: 401, 403, 404, a
  package that doesn't exist) is never retried, and is critical too, with
  its failure row, unless the request names its status recoverable for its
  API ([CORE.md](CORE.md#requests)); a rate limit
  (`HttpError.rate_limited`: 429, or a 403 that says so) is recoverable,
  never critical. Where the API keeps history, the next run backfills the
  missed days (one failed night recovers). A ported source's failure rows
  keep the script's statuses, fields and reasons.
  `tests/test_adding_a_source.py` runs this example.
- **Partial rows.** A row built from several requests keeps its good parts
  when an optional part fails: that part is null, never 0, and the row goes
  through `store.write_partial(file, row, failed, ctx, NAME)`, where
  `failed` maps each failure, as the row's `reason` names it, to the fields
  its part left null. Store keeps the value an earlier run of the day got
  for each failed part, marks the row partial while a part is still
  missing, naming each such failure, and writes it good once every part is
  filled (ADRs: a partly failed row keeps its good parts; a run never
  erases what it didn't get). A required part that fails is a failure row.
  Draw required and optional where the script does: what it can't write a
  row without is required. A value an optional part reads is never null,
  since null in a partial row means unknown. A part that is a list of
  people, each read on their own, marks the one it couldn't read instead
  and merges them itself (`store.stored`, `store.write_merged`), as
  `github_contributor_locations` does.
- **Rebuilt history.** Where what a run fetches holds past days' values
  (each issue's opening and closing times), the source can rebuild them
  for the days `store.rebuild_days` gives and pass them to
  `store.fill(file, rows, ctx)`, which adds only what a past row lacks,
  and a row for a day with none, never changing a value
  ([Backfills](CORE.md#backfills); ADR: past values a source can rebuild
  are filled, never changed), as `github_issues` and `github_discussions`
  do. A source whose rows are computed from data each run reads in full,
  rather than read at a moment, recomputes every past day instead, for
  the days `store.recompute_days` gives, through `store.recompute`, which
  corrects a value that changed and records it
  ([Recomputed history](CORE.md#recomputed-history); ADR: derived history
  follows today's rules), as `github_response_times` and
  `github_contributor_locations` do.
- **Reporting.** Every failed request goes to
  `ctx.record_unfixable(NAME, file, error, reason)`, which records it
  critical when waiting can't fix it; anything else critical (a package
  that doesn't exist, in the example) goes to `ctx.record_critical`
  ([Failures and what sources report](ARCHITECTURE.md#failures-and-what-sources-report)).
  Corrections are recorded by `store.write_good`, as above.
- **`check`** (not shown) validates the config against the live API and
  writes no rows. It returns a list of `context.Finding`, and a failed or
  rate-limited request is a finding, never an exception. The runner calls
  it on every run, after collection, and health records its findings
  (`checks` in `health.jsonl`); an exception is a crash, critical.
- **`retry`** (not shown, and only where the API wants it, as pypistats
  does): a second pass the runner calls after every source has collected
  ([The run](ARCHITECTURE.md#steps) in ARCHITECTURE.md). It asks again
  only for requests that failed; an answer the run got would be served
  again unchanged. `pypi/source.py` is the example.

## 6. Nothing to register

`sources.discover()` finds the package by its folder's name, which must
equal its `NAME`, in a group too; there is no list to edit. A package that
fails to import, or lacks a member of the contract, is reported as failed
to load (ARCHITECTURE's [Contract](ARCHITECTURE.md#contract)), and
`tests/test_scenarios.py` fails. A single `.py` module in `sources/` is not
a source: only a package is found.

## 7. Document it

The docs that list the sources are generated from the package and its
README, so a source is documented by its README alone:

- Write the package's `README.md` in the shape of
  `home_assistant/ha_integration`'s. Its bullets are hand-written and each
  is the one home of its fact:
  - `**Reads:**` what it reads and reports, one sentence or two: the text
    that goes in the source table and the list of sources;
  - `**Requests:**` the calls it makes;
  - `**Credentials:**` `none`, or the variables it needs and what the token
    must be allowed to do (the variables are the package's `ENV`; this is the
    one place their permissions are written);
  - `**Writes:**` the rows it writes (the file is `DEFAULT_FILE`, generated);
  - `**Config entry:**` its keys, which goes in the source table. It links
    only to sections of its own README (`[see its keys](#queue)`) or to a
    URL; the generator points those at the README. A key with many values
    or rules gets a section of its own in the README (`ha_integration`'s
    `bands`).

  Each bullet may wrap onto lines indented by two spaces. `Reads` and
  `Config entry` are required, may not hold a `|`, and the generator fails
  for a source without them.
- Run `make docs`. It rewrites the generated blocks: the source table and
  the lagging sources in ARCHITECTURE.md, the list of sources and the
  credentials table in CONFIGURATION.md. A new credential variable appears
  in the table from the package's `ENV`.
  `tests/test_source_docs.py` fails until the files hold what it generates.
- The hand-written pitch in README.md, "Who it's for", names no platform and
  needs no edit; the example workflow's secrets list there is hand-written
  (`tests/test_actions_recipe.py` guards it).
- Add a line to CHANGELOG.md.

## 8. Verify

`make check` runs the port tests and the scenario, golden, discovery,
generated-docs and boundary tests; `make docs` leaves the tree unchanged.
`make oracle` holds the builders to the capture and reruns the script on
every scenario that gives its calls.
