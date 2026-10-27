# Configuration

The config file (its settings, each source's entries and shared tables)
and the credentials a run reads. Its `reports` setting is described in
[REPORTS.md](REPORTS.md). Each source's own keys are in its README, listed
under [Sources](#sources).

`leadline report` reads the same file but uses only part of it
([Which settings `report` uses](#which-settings-report-uses)).

Two channels, kept apart (ADR: credentials come only from the environment):

- **Credentials** come only from environment variables.
- **Settings** come from a JSON file, and never hold credentials.

## Credentials

The environment variables sources read. They are set in the scheduler's
task. There are no token flags and no secrets files. A source whose
variables are unset or empty is skipped, not failed
([The run](ARCHITECTURE.md#steps)).

<!-- BEGIN GENERATED credentials-table -->
| Variable | Used by |
| -------- | ------- |
| `CLOUDFLARE_ACCOUNT_ID` | `cloudflare_analytics_engine`, `cloudflare_web` |
| `CLOUDFLARE_API_TOKEN` | `cloudflare_analytics_engine`, `cloudflare_web`, `cloudflare_zone_traffic` |
| `GITHUB_TOKEN` | `github_contributor_locations`, `github_discussions`, `github_issues`, `github_releases`, `github_repo_activity`, `github_repo_stats`, `github_response_times`, `github_traffic`, `github_traffic_daily`, `github_tree_counts` |
| `GOATCOUNTER_TOKEN` | `goatcounter` |
<!-- END GENERATED credentials-table -->

What a token needs (permissions, access) is in the README of the source
that uses it ([Sources](#sources)).

## Settings

The top-level keys of the JSON file named by `--config`. Each top-level
scalar setting can also be given as a flag, which overrides the file.

| Setting | Flag | Meaning |
| ------- | ---- | ------- |
| `out_dir` | `--out` | output folder, required; in the file, relative to the file's folder |
| `user_agent` | `--user-agent` | User-Agent for every request; default `leadline-metrics/<version>` |
| `sources` | none | each enabled source's config, keyed by source name, in run order ([Source entries](#source-entries)) |
| `shared` | none | tables defined once and named by entries, by section: `regions` ([Shared tables](#shared-tables)) |
| `reports` | none | the report blocks, in order ([REPORTS.md](REPORTS.md)); without it, [the default summary](REPORTS.md#the-default-summary); `[]` for none |
| `projects` | none | the trends pages' groups of sections, in order ([Projects](#projects)); without it, a page doesn't group them |
| `pages` | none | the trends pages, each a file, a title and its projects ([Pages](#pages)); without it, one `trends.html` |
| `storage` | none | where the row files are kept ([Storage](#storage)); without it, jsonl files in `out_dir` |

- `config.SETTINGS` lists the scalar settings and their flags;
  `config.SHARED` lists the sections of `shared`.
- An unknown key or section, or a source name no source under
  `sources/` has, is an error. So is a key given twice in one object (JSON
  allows it and keeps the last, which would silently drop an entry), and a
  `user_agent` that is not printable ASCII (a header value).
- A relative `out_dir` in the file resolves against the file's folder, so
  one synced config works on every machine that syncs it.
- The config file can live in a synced folder next to the outputs. Adding
  a project is an edit to that file on any machine; the scheduled task
  never changes.
- API base URLs (`api.github.com`, `pypistats.org`, Home Assistant's
  analytics) are neither credentials nor settings: they are constants in
  each source module. A URL that differs between users, such as a
  Discourse forum's `base_url`, is a key of the entry.

### Which settings `report` uses

`leadline report` ([Reports without a run](ARCHITECTURE.md#reports-without-a-run))
rebuilds the reports, the default summary and the trends from the output
folder's files, and needs no credential:

| Setting | In `report` |
| ------- | ----------- |
| `out_dir` / `--out` | used: the folder to read and write; it must exist |
| `sources`, `shared` | used to validate each entry as `run` does, to find its files and to build its sections; nothing is collected, so an unset credential changes nothing |
| `reports`, `projects`, `pages` | used |
| `storage` | used: the row files are read from it |
| `user_agent` / `--user-agent` | not used: no request is made |

## Storage

Where the row files are kept ([Storage](ARCHITECTURE.md#storage)). Without
the setting, each is a `*.jsonl` file in `out_dir`. The reports, the pages and
the log are files in `out_dir` either way.

```json
"storage": {"backend": "sqlite", "path": "leadline.db"}
```

| Key | Meaning |
| --- | ------- |
| `backend` | required: `jsonl` or `sqlite` |
| `path` | the database file, required for `sqlite` and not allowed for `jsonl`; relative to the file's folder, like `out_dir`; its folder is made when it is missing |

Keep the database out of a folder a sync service (OneDrive, Dropbox)
watches: it can copy the file while a run is writing it, and a database
copied half-written is damaged. The config and the output folder can stay
synced.

A database is opened, and its tables made, before the run starts; where it
cannot be, the run stops with exit status 2. Python's `sqlite3` module must
exist, which a `jsonl` run does not need.

`leadline import --config P [--from DIR]` copies the `*.jsonl` row files in
`DIR` (default `out_dir`) into the configured database and checks each reads
back as it was. It is how existing files move to a database; the files
themselves are left as they are. It is for a database with none of those
files yet: if the database holds a file of one of those names, the command
copies nothing and exits with status 2, because replacing it would erase the
nights the database has collected since. Import once, then switch `storage`
over; keep the old folder as a backup.

## History on first use

There is no separate backfill step: a source that rebuilds history does it
in the normal run, and a new file gets it on the first run of a later day.

- The first run on empty data writes only that day's row. A second run the
  same day replaces that row and adds nothing.
- The first run on a later day fills in every earlier day, back to the
  first day the source's data can tell: opened and open issues by kind,
  response times, contributor regions and catalog counts. The
  traffic and downloads sources backfill the window their service gives on
  the first run.
- A long catalog history (`github_tree_counts`) asks for at most 500 trees a
  night, and with `content` reads at most 1000 blobs, so it can take a few
  nights; each night goes on where the last stopped.
- Contributor comments (`github_contributor_locations` with `comments`) are
  read one issue a run after the first night's pass, so a region from
  comments arrives slowly.

Why a night's wait and the bounds: [ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md#derived-history-follows-todays-rules).

## Source entries

The shape of a source's value under `sources`.

- **A list of entries**, one per thing it reads (a repo, a site, a dataset,
  a forum). Two sources take **one object** instead: `ha_platform`, which
  has one thing to read, and `pypi`, whose `packages` share one nightly
  rotation, pause and retry pass.
- **GitHub entries** take `repo` (`owner/name`). `maintainer`, the login
  counted apart from the community, belongs to each entry that needs it:
  optional for `github_issues`, required for `github_discussions` and
  `github_response_times`.
- **Only the keys a source reads.** An entry (or a source's object) with
  any other key is a config error naming it and the keys the source has,
  `file` among them; each source's README lists its keys. The same holds
  inside an entry's nested objects (`queue`, `queue.body_match`, a
  discourse topic, `kinds`, `layouts`, `github_tree_counts`' objects) and
  in each rule of a region table: a key they don't read is an error.
- **Everything specific to one user is config**: which repos, packages,
  sites and datasets; the patterns a source matches (region keywords, the
  tree pattern, commit patterns, the queue's body pattern); the row keys a
  domain gives its counts (the tree counts', the queue's); and report
  titles (ADR: sources are named for what they read; project specifics are
  config). The repo ships only placeholder examples; a user's own values
  live only in their private config.

### Output files

Which file an entry writes. Each file has one writer.

- **`file`**, optional in every entry and in the two objects: a template for
  the file it writes, a plain `*.jsonl` name filled from the entry's fields
  (`"file": "example-{label}.jsonl"`, or a fixed `"example.jsonl"`).
- **The default file**, without `file`, is the source's name with dashes,
  then the entry's id (`config.default_file`), as the
  [source table](ARCHITECTURE.md#source-table) lists. GitHub entries'
  templates may also use `{repo_name}`, the part of `repo` after the slash.
- **Keeping history.** A config keeps an older file's name, so its history
  continues, by setting `file` to it.
- **`config.file_name(entry, default, fields)`** fills either, and refuses a
  field the source doesn't offer or anything but a plain `*.jsonl` name,
  and `health.jsonl` (ignoring case), where the run writes its health row. A
  source calls it in `validate`, `files` and `collect`.
- **One writer.** Two entries that fill to the same name, in one source or
  two, are a config error ([The run](ARCHITECTURE.md#steps)), and names that
  differ only in case count as the same file, so two
  entries whose defaults would collide (two `discourse_topics` forums)
  each set a `file`.

### Notes

**`notes`**, optional in an entry of a source that passes it on (today
`ha_integration`): a list of at most 10 `{"date": "YYYY-MM-DD", "text": "..."}`
objects, each a remark on a date, its text 1 to 60 characters
(`{"date": "2026-10-22", "text": "HA changed the feed"}`). [Trends](TRENDS.md)
marks each on the entry's line charts with a dashed vertical line at the date
and the text above the plot, when the date is inside the range drawn. Rows and
reports are unchanged. A bad date, a long or empty text, an unknown key in a
note or more than 10 notes is a config error.

## Sources

Every source, with what it reads. Each links to its README, which holds
its config entry and every key, its credentials and what it writes.
Generated from the source packages and their READMEs by `make docs`
([Adding a source](ADDING_A_SOURCE.md#7-document-it)).

<!-- BEGIN GENERATED source-list -->
| Source | Reads |
| ------ | ----- |
| [`cloudflare_analytics_engine`](../src/leadline/sources/cloudflare/cloudflare_analytics_engine/README.md) | a Workers Analytics Engine dataset's daily totals over SQL, each dimension on its own; the last `refetch_days` days are read again every run. |
| [`cloudflare_web`](../src/leadline/sources/cloudflare/cloudflare_web/README.md) | a site's page views and visits from Cloudflare Web Analytics, each row with the site tag it was read under; the last 30 days healed and corrected. Yesterday's row also holds the week's `top_referrers`, `top_countries`, `top_devices`, `top_paths`, `top_browsers`, `top_systems` and `top_bots` (`name`, `count`; ten at most). These count visitors whose browsers run Cloudflare's beacon, so unlike the zone's request counts they leave out scanners and other bots. |
| [`cloudflare_zone_traffic`](../src/leadline/sources/cloudflare/cloudflare_zone_traffic/README.md) | a Cloudflare zone's HTTP traffic as a row a day, kept for good where Cloudflare keeps it for a year (daily totals) or 31 days (per-request detail): requests, cached and encrypted requests and bytes, page views, threats, unique visitors and visits, and the counts by country, status code, browser, operating system, device type, data center, path, host and more. Counts only, never a visitor's address. |
| [`discourse_forum`](../src/leadline/sources/discourse_forum/README.md) | a Discourse forum's own site statistics (members, topics, posts, likes, active members, and the forum's chat counts where it has chat), a row a day per forum. |
| [`discourse_topics`](../src/leadline/sources/discourse_topics/README.md) | views, replies and likes of topics on a Discourse forum, a row per topic a day; each topic's days before its first row rebuilt back to its creation: replies and last post. |
| [`github_contributor_locations`](../src/leadline/sources/github/github_contributor_locations/README.md) | where the people who open a repo's issues and pull requests are, from their profiles, issue-form answers and issue text, and, where the entry sets `comments`, a person's own comments, with the count per region; every past day recomputed back to the first person's first issue, each person with today's region, under today's rules. |
| [`github_discussions`](../src/leadline/sources/github/github_discussions/README.md) | a repo's discussions: totals, community and maintainer, answers, categories and the most recent ten; the days before the file's first row rebuilt back to the first discussion, comments included, where the queries read them all. |
| [`github_issues`](../src/leadline/sources/github/github_issues/README.md) | a repo's issues and pull requests: counts, flow, who opened them, ages, engagement, labels, triage; optionally one label's queue; the issues opened and closed, ever, filled into each past row that lacks them, as of its day, and with `kinds` the issues opened by kind, counted again under the kinds on every run; the days before the file's first row rebuilt back to the first issue: open issues and pull requests, and the totals; the repo's stars and forks, filled into the past rows before the first with them, from the dates GitHub keeps; with `stars_and_forks` and `regions`, those stars and forks counted by region. |
| [`github_releases`](../src/leadline/sources/github/github_releases/README.md) | a repo's releases, with each asset's downloads. |
| [`github_repo_activity`](../src/leadline/sources/github/github_repo_activity/README.md) | one public repo's week: commits, authors, fixes and redesigns, issues, contributors, the latest release, the code size and its split by language. |
| [`github_repo_stats`](../src/leadline/sources/github/github_repo_stats/README.md) | a repo's stars, forks, watchers and open issues, with its 14-day traffic totals. |
| [`github_response_times`](../src/leadline/sources/github/github_response_times/README.md) | how long community issues wait for an answer: the maintainer's first reply or the issue's closing, whichever comes first; every past day recomputed back to the first community issue, under today's rules. |
| [`github_traffic`](../src/leadline/sources/github/github_traffic/README.md) | a repo's 14-day views, clones, top referrers and top paths, as one snapshot. |
| [`github_traffic_daily`](../src/leadline/sources/github/github_traffic_daily/README.md) | a repo's views and clones, a row per day, backfilled across the 14-day window. |
| [`github_tree_counts`](../src/leadline/sources/github/github_tree_counts/README.md) | the paths in a repo's git tree that match a pattern: files, items, groups and items per group; optionally the files counted by a value read from each one's text, each blob read once; the days before the file's first row rebuilt from git history, back to the first tree that matches, and with the counts by value, every day through yesterday. |
| [`goatcounter`](../src/leadline/sources/goatcounter/README.md) | a site's page views by day, backfilled across the week, and its top referrers, from GoatCounter's API. |
| [`ha_integration`](../src/leadline/sources/home_assistant/ha_integration/README.md) | a custom integration's installs by version, and its rank among all of them, from Home Assistant's analytics. |
| [`ha_platform`](../src/leadline/sources/home_assistant/ha_platform/README.md) | Home Assistant's platform-wide analytics: active installations, how many report integrations, add-ons and statistics, installation types and the top countries. It is the denominator for `ha_integration`'s installs. |
| [`pypi`](../src/leadline/sources/pypi/README.md) | each package's downloads by day from pypistats, backfilled from its history, and its version and required Python from PyPI; a retry pass asks again for a package whose history request failed. |
<!-- END GENERATED source-list -->

## Shared tables

Tables defined once under `shared` and named by each entry that uses them
(ADR: shared tables are defined once and named by the entries that use
them).

- **`shared.regions`** holds named region tables. Each has `text` rules,
  searched in issue text, profile locations and answers, and `location`
  rules, searched in profile locations and answers only, after the `text`
  rules. Either may be left out.
- **A rule** is a region and the lowercase keywords that mark it
  (`{"region": "XA", "keywords": ["example isp"]}`). A keyword matches
  anywhere in the lowercased text, and the first rule that matches wins
  (`config.find_region`).
- **What core supplies.** Knowledge that is true for everyone ships in core
  (`leadline.places`), so a table holds only what is its own. Core adds
  `location` rules (every country's name, the US states' names and their
  USPS abbreviations, a few common names such as `united kingdom` and
  `usa`, and the ten biggest US cities) and
  `zones` rules (every IANA time zone's country). Core adds no `text`,
  `answer`, `exact`, `models` or `known`: those stay in the config. Core's
  `zones` count only in a table that sets `attachment` and
  `attachment_value`, as a table's own `zones` do, and such a table needs no
  `zones` of its own.
- **The merge order.** A table's own rules come first and core fills the
  gaps, with no flag: a table's `location` rules, after its `text` rules,
  then core's; a table's `zones`, then core's. A table that gives `germany`
  a region of its own gets that one. `config.load` puts core's rules after
  the table's own in the table an entry names; the file is not changed.
- **How core matches.** A keyword of a table matches anywhere in the text, as
  above. A core country name or state name matches only as a whole word, so
  `oman` is not found in `romania`, `india` in `indiana`, `niger` in
  `nigeria` or `mali` in `somalia`; the longer name is tried first (`papua new
  guinea` before `guinea`, `new mexico` before `mexico`); where a US state and
  a country share a name, the state wins (`georgia`, `washington`). A US
  abbreviation (`, tx`) matches only when it is the whole token ending a field
  of the location: followed by the end, a comma, a period, a bracket or a
  number, so `austin, tx`, `austin, tx 78701` and `portland, or.` give the
  US and `berlin, in germany` or `maybe, maine` does not read `in` as
  Indiana or `ma` as Massachusetts. A name tried first wins over an
  abbreviation, so `berlin, de, germany` is Germany. `jersey` alone is left
  out, as most often the start of a US place. A zone matches only as a whole
  name, so `america/bahia` is not found in `america/bahia_banderas`, and the
  old names in the time zone database (`europe/kiev`) give their zone's
  country. Core's codes are the project's: Great Britain is `UK`.
- **Region codes.** A region in a rule or in `known` must be an ISO 3166-1
  alpha-2 code, `UK` (the project's code for `GB`), or `XA` to `XZ`, the
  placeholders these docs use; anything else, such as `UKK`, is a config
  error naming where it is (`shared.regions.<name>.location[3]`). `GB` is
  accepted and read as `UK`, so one country is one region in the rows.
- **The data.** `src/leadline/places/` holds three vendored JSON files, each
  with its own source URLs, release and day retrieved: `countries.json` (the
  ISO 3166-1 codes and English names), `zones.json` (the IANA `zone.tab`, one
  country per zone, and the time zone database's old names, `backward`) and
  `states.json` (the USPS abbreviations from the Census Bureau's list). They
  are never fetched at run time and ship inside `leadline.pyz`; each is read
  the first time it is needed with `pkgutil.get_data`, and `zones.json` only
  for a table that reads attachments. `make places` (also the VS Code task
  `make places`) fetches the standards and rewrites each file on its own,
  sorted, so a change shows as a diff to review: a file whose standard is
  unchanged is not written, and one whose source fails is left as it was
  (`python tools/refresh_places.py zones` refreshes one). There is no schedule
  (ADR: core ships the places everyone shares).
- **Issue forms** (`answer` and `model_answer`, each optional): regexes
  with exactly one group, which capture a person's answers to two
  questions in an issue form, their internet provider and their modem,
  anywhere in the body of an issue (not a pull request), matched ignoring
  case, with `^` and `$` at each line (`config.read_form`). Each answer is
  stripped.
- **The answer** is matched against the `exact` rules, then the `text`
  rules, then the `location` rules, as a profile's location is, so
  "Example ISP (Example Country)" resolves by its country
  (`config.answer_rule`). An `exact` rule is a region and the whole
  answers, lowercase, that mean it
  (`{"region": "XA", "answers": ["ex"]}`): an answer, lowercased, that is
  one of them, for a name too short to be a keyword, which would match
  inside other words. `exact` rules match answers only, never issue text
  or a profile location.
- **The modem** is matched against the `models` rules, keyword rules
  searched in the modem only (`config.model_rule`). A `models` rule names
  a modem sold in one country only, such as one an internet provider
  issues there; a modem sold in shops in several countries has none.
- **A form's region** is the answer's, else the modem's
  (`config.form_region`). `github_contributor_locations` reads it: for a
  person with no profile location, their first answer a rule matches
  gives the region, else their first modem a rule matches, before their
  issue text; with neither, their issue text gives it, as without forms.
  `github_issues`' queue gives each listed issue its form's region before
  its text. Only the region is kept: an answer is never written or logged
  per person.
- **Stars and forks by region.** A `github_issues` entry with
  `stars_and_forks` that names a table (`"regions": "<name>"`) counts its
  stars and forks by the region the table gives each person's profile
  location, else their `known` region, else `unknown`
  ([its README](../src/leadline/sources/github/github_issues/README.md#stars-and-forks-by-region);
  ADR: stars and forks are counted by region, never by person). The table's
  `text` and `location` rules and `known` are the rules it uses, and counts
  are all that is written. The trends page does not chart them. A person is
  counted once, when new, and a rule added later does not change the days
  already counted.
- **Comments.** `github_contributor_locations`' entry may set `"comments":
  true`: a person still without a region gets the first `text` rule found in
  their own comments (the first 500 characters of each) on the issues of
  the people still unplaced, `comment_inference`. Their issues' comments are
  all read the first night and again after a `text` rule changes, then one
  issue a run; the row's `comments_read` holds what was read. The people
  still unplaced are listed in the log with their issue numbers, for adding
  rules (ADR: a person's region from their own comments, bounded by what the
  row remembers).
- **Known regions.** `known`, optional, is an object of a person's login
  to a region code (above; the table needs no rule for it), for people the maintainer
  knows (`config.known_region`; ADR: a region the maintainer knows is
  configured). A code that is not a region code (above), an empty or non-string
  one, or a login twice ignoring case is a config error. `github_contributor_locations`
  reads it every run: a person whose profile location gives no region gets
  it, with the source `configured`. The order of evidence is a profile
  location, the known region, the answer to the provider question, the
  zone, the modem, issue text, comments. A clue that differs from a known
  region is logged by the two regions and the clue, never by login, and
  changes nothing.
- **Attached files.** Two settings, which go together, let a table read the
  zone in a file people attach to their issues (ADR: a region from the zone in
  an attached file, bounded by the row). `attachment` is a
  regex with one group that captures a file's URL in an issue body (not a pull
  request), matched like `answer` (`config.find_attachment`). `attachment_value`
  is a regex with one group that captures a value in the file's text, which is
  matched as text, not parsed (`config.find_value`). `zones`, optional, are
  rules like `models`, a region and lowercase keywords searched in that value
  only, so a keyword names a specific zone (`config.zone_rule`); they come
  before core's and need the two settings. A zone outranks a modem and issue
  text, not the answer to the provider question nor a profile: where
  it differs from a profile's or an answer's region, or from the modem's or
  text's it replaces, the log says so by rule and regions, never by login. The
  file of a person a profile or an answer placed is read too, only to check it.
  The file is fetched without the token. Only the zone's region is kept.
  `github_contributor_locations`' row remembers which files were read in
  `timezones_read`; they are all read the first night and again after the
  settings or the zones change (core's zones are among them, so a refreshed
  places table reads the files again), then at most one file a run.
- **Findings.** `github_contributor_locations`' `check()` names, by count
  and never by login, the answers no rule matches (unless a `models` rule
  matches the modem in the same form, which places the person), each rule
  an answer matched that gives another region than a person's profile
  location, and
  each `models` rule that gives another region than the answer in the
  same form, which wins, so the table grows from evidence and a wrong rule
  surfaces (ADR: an answer in an issue form is the strongest region
  signal).

```json
"shared": {"regions": {"example-regions": {
  "answer": "^ISP: *(.*)$",
  "model_answer": "^Modem: *(.*)$",
  "exact": [{"region": "XA", "answers": ["ex"]}],
  "text": [{"region": "XA", "keywords": ["example isp"]}],
  "location": [{"region": "XA", "keywords": ["example country"]}],
  "models": [{"region": "XB", "keywords": ["mx100"]}],
  "attachment": "(https://files\\.example\\.test/\\S+\\.json)",
  "attachment_value": "\"zone\": *\"([^\"]+)\"",
  "zones": [{"region": "XA", "keywords": ["area-a/city-1"]}],
  "known": {"person-a": "XA"}
}}}
```

- **Naming a table.** An entry names it (`"regions": "<name>"`), and
  `config.load` puts the table, with core's rules after its own, in the entry
  in place of the name, so a
  source sees only its own entry, never the whole `shared` section.
- **Errors.** A name with no table, a table that isn't rules (such as a
  keyword or exact answer with capitals, which would never match, or a
  region code core does not know), or an
  `answer` or `model_answer` that isn't a regex with one group, is a
  config error, named where the table is defined
  (`shared.regions.<name>.exact[0]`).

## Projects

`projects`, optional: groups the sections of the
[trends pages](TRENDS.md#the-pages) by project, with links between
them, for a config that reads several projects. The default summary
isn't grouped.

```json
"projects": [
  {"name": "Example App", "sections": ["github_issues: example-org/example-app",
                                       {"section": "pypi: example-package", "title": "Package downloads"}]},
  {"name": "Example Lib", "sections": ["github_issues: example-org/example-lib"]}
]
```

- **A project** is its `name`, the heading the page shows, and its
  `sections`, each named by its title in the summary: `<source>: <name>`
  (`pypi: example-package`), or the source alone for a source with one
  entry.
- **A section's heading** on the page is the source's label (`Issues`),
  with the entry's name where the project has two sections of that source
  ([`heading`](TRENDS.md#trendsjson)). To give it another, list it as
  `{"section": <its title>, "title": <the heading>}` instead of the title.
- **A second section of an entry**, where a source gives one
  (`github_issues` with `stars_and_forks`), has the entry's title and its
  part after it: `github_issues: example-org/example-app stars`.
- **Order.** Projects show in the list's order, and each project's
  sections in its `sections`' order. On `trends.html`, sections in no
  project show last, under "Other", in config order.
- **Errors**, each a config error before anything runs, named by its
  place (`projects[1].sections[2]: ...`): a name missing, empty or used
  twice; a title that names no section of the configured sources; a
  section listed twice; a section's object with other keys, or a title
  missing or empty.

## Pages

`pages`, optional: a trends page for each portfolio of projects, instead
of `trends.html`, which shows them all. Each is written in the output
folder and shows only its projects, with what it shows of the data and
nothing else ([The pages](TRENDS.md#the-pages)). Without `pages`, a run
writes `trends.html`. `trends.json` holds every section either way, those
on no page too.

```json
"pages": [
  {"file": "app.html", "title": "Example App", "projects": ["Example App", "Example Lib"]},
  {"file": "other.html", "title": "Other Work", "projects": ["Other App"]}
]
```

- **A page** is its `file`, a plain `*.html` name in the output folder;
  its `title`, the heading at its top; and its `projects`, each named by
  its `name` in `projects`, in the order the page shows them. A project
  may be on more than one page; a section in no project the pages list is
  on none.
- **Errors**, each a config error before anything runs, named by its
  place (`pages[1].projects[0]: ...`): `pages` without `projects`; a file
  that isn't a plain `*.html` name, or another page's (names that differ
  only in case count as the same, as for [output files](#output-files));
  a title missing or empty; a project that names none of `projects`, or
  listed twice on one page.

## Example

A sketch with placeholders: a shared region table and the two entries that
name it, a `file` that keeps an older file name, `pypi`'s object, two
report blocks, three projects, one section with its own heading, and two
pages.

```json
{
  "out_dir": "/path/to/metrics",
  "user_agent": "example-metrics/1.0",
  "shared": {
    "regions": {
      "example-regions": {
        "text": [{"region": "XA", "keywords": ["example cable", "example isp"]}],
        "location": [{"region": "XA", "keywords": ["example country", "example city"]}],
        "answer": "^(?:### Internet Service Provider \\(ISP\\)\\s*\\n\\s*|ISP: *)(\\S.*)$"
      }
    }
  },
  "sources": {
    "github_issues": [
      {
        "repo": "example-org/example-app",
        "maintainer": "example-maintainer",
        "regions": "example-regions",
        "queue": {
          "label": "example-label",
          "key": "example_queue",
          "items_key": "examples",
          "body_match": {"pattern": "example-pattern", "flag_key": "has_example", "count_key": "with_example"}
        }
      },
      {"repo": "example-org/example-lib"}
    ],
    "github_contributor_locations": [{"repo": "example-org/example-app", "regions": "example-regions"}],
    "github_repo_activity": [{"repo": "other-org/other-app", "file": "example-{label}.jsonl"}],
    "discourse_topics": [{"base_url": "https://forum.example.com", "topics": [{"id": 1001, "label": "example-topic"}]}],
    "pypi": {"packages": ["example-package", "example-lib"], "sleep_s": 60, "retry_wait_s": 240}
  },
  "reports": [
    {"file": "../example-app.md", "block": "support-load", "place": "bottom", "heading": "Support load",
     "table": {"from": "github-issues-example-app.jsonl", "group": "week", "recent_weeks": 2,
       "columns": [{"title": "Date", "field": "date"},
                   {"title": "Open issues", "field": "issues.open", "bold": true},
                   {"title": "Range", "field": "issues.open", "aggregate": "range"}]}},
    {"file": "../SUMMARY.md", "block": "stars", "place": "top", "heading": "Stars",
     "lines": [{"label": "Now", "from": "github-issues-example-app.jsonl", "field": "repo.stars", "compare": "7d"},
               {"label": "Last 14 days", "from": "github-issues-example-app.jsonl", "field": "repo.stars", "series": 14}]}
  ],
  "projects": [
    {"name": "Example App", "sections": ["github_issues: example-org/example-app",
                                         "github_contributor_locations: example-org/example-app",
                                         {"section": "pypi: example-package", "title": "Package downloads"}]},
    {"name": "Example Lib", "sections": ["github_issues: example-org/example-lib", "pypi: example-lib"]},
    {"name": "Other App", "sections": ["github_repo_activity: other-org/other-app"]}
  ],
  "pages": [
    {"file": "example.html", "title": "Example", "projects": ["Example App", "Example Lib"]},
    {"file": "other.html", "title": "Other Work", "projects": ["Other App"]}
  ]
}
```
