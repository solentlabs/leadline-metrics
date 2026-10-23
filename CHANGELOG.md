# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/); versions
follow [SemVer](https://semver.org/).

## Unreleased

- A count a source reads is never negative: an answer with a negative where a
  count belongs is unusable (a failure row), not stored as it is.

## 0.8.3 - 2026-10-23

- `cloudflare_zone_traffic`: a mean origin response time of `-1`, which
  Cloudflare gives a day with no origin responses, is no mean for that day
  and no longer makes the whole per-request part unreadable. It left the
  newest row `partial` (visits and timings null) each night while such a day
  was in the 28-day window.

- `github_traffic`: the row holds `top_paths`, the ten most viewed paths of the
  14 days (`path`, `title`, `count`, `uniques`), from one more request. GitHub
  keeps them 14 days only. Paths that fail leave it null in a `partial` row, as
  the referrers do.
- `github_repo_activity`: the row holds `languages`, the bytes of code in each
  language, beside `codebase_bytes`, their sum.
- `github_repo_stats`: the row holds the repository's `network_count`,
  `size_kb` and `pushed_at`.
- `github_issues`: the run date's row holds `issues.closed_not_planned_total`
  (issues closed as not planned) and `contribution.prs_merged_total` (pull
  requests merged), both ever, counted from the lists it already reads.
- `pypi`: the row holds `downloads_with_mirrors_last_day`, `_last_week` and
  `_last_month`, counted from the pypistats answer it already reads, beside the
  downloads without mirrors. Null when the answer has no with-mirrors rows.
- `goatcounter`: yesterday's row holds `top_pages`, the ten pages with the most
  views in the week (`path`, `title`, `count`), from the answer it already
  reads; the other days' rows hold an empty list.
- `ha_platform`: the row holds `ha_versions` (installations by core version),
  `os_boards` and `supervisor_arch`, null where the answer lacks them.
- `discourse_topics`: a topic's row holds `participants`, `closed`, `solved`
  (whether it has an accepted answer) and `tags`; each is null where the
  answer lacks it.

- `github_traffic_daily`: a day GitHub counted clones for and no views is a row
  with zero views, not a day never written (the clones were lost; the window
  keeps 14 days only).

- Reports: `counts` takes `days: N`, adding up an object of counts over the
  rows of the newest N days (a week of events, error codes or screen views).

- `cloudflare_zone_traffic`: a part of the per-request dataset that cannot be
  read says why in the row's `reason` (`totals answer was unreadable: visits is
  not a non-negative number`), naming the field, never a value.
- `ha_integration`: the summary and trends chart the integration's rank beside
  the number of integrations in Home Assistant's feed, as one chart, in place
  of the percentile. The feed changes what it lists (4,303 entries on
  2026-10-20, 5,668 on 2026-10-22), so a rank or percentile alone moved with
  no change in installs. Rows are unchanged.

## 0.8.2 - 2026-10-22

- An answer without a count, flag or list a source reads is a failure row (or a
  partial one where only a part is missing) whose reason names what is
  missing, not a row of zeros, `false` or no pages. This covers
  `github_repo_stats`, `github_repo_activity`, `github_issues`,
  `github_traffic`, `github_traffic_daily`, `github_releases`, `goatcounter` and
  `discourse_topics`.
- A bot challenge (a 403 or 503 with Cloudflare's "Just a moment..." page or
  `cf-mitigated: challenge`) is no longer critical at once: it is a failed night
  that recovers, and critical after consecutive ones. The forum answered the
  NAS one on 2026-10-21 and the job exited 1.
- `cloudflare_analytics_engine`: `refetch_days` is at most 90, not 365. The
  dataset keeps three months, and a window reaching further back rewrote the
  days it no longer holds as rows of zeros.
- `github_tree_counts`: the history rebuild asks for at most 500 trees a night
  (it was unbounded without `content`), so a long history no longer spends the
  GitHub rate limit the other sources share; the next night goes on.
- `github_traffic`: a failed or unusable referrers answer leaves `top_referrers`
  null in a `partial` row (views and clones kept), not `[]` in a good row that
  read as no referrers; a later good run fills it.
- A redirect to another origin no longer carries the `Authorization`,
  `Cookie` or `Proxy-Authorization` header (urllib copied every header, so a
  token could reach a host the config did not name).
- `cloudflare_zone_traffic`: the per-request dataset is read for the last 28
  days, not 30. Cloudflare keeps it 31 days, and a run in the evening west of
  UTC is already the next UTC day, so a 30-day start was over the limit: the
  night of 2026-10-21 wrote yesterday's row `partial`, with every
  per-request field null.

## 0.8.1 - 2026-10-21

- `cloudflare_zone_traffic`: the first run asks for 363 days of the daily
  dataset, not 365; Cloudflare refused a start a day older than it keeps, so
  the first night wrote an error row and no history.
- Reports: a row value that is a whole line and reads like a leadline marker
  no longer makes the next run fail on a repeated marker (it is shown with
  its `<` escaped); a byte order mark at the start of a report file no longer
  hides the first block's marker (the mark is kept); a lone surrogate in a row
  no longer crashes the run (it is shown as the replacement character).
- Health: a file holding several series (the forum topics, a row a day for
  each) is no longer critical because one topic fails while the others are
  fine; which row is last on a date no longer decides it. A topic's error that
  waiting can't fix (a deleted topic's 404) is still critical on its own.

## 0.8.0 - 2026-10-20

- New source `cloudflare_zone_traffic`: a zone's HTTP traffic from Cloudflare's
  GraphQL Analytics API, one row a day. The daily dataset gives exact totals
  (requests, cached and encrypted requests and bytes, page views, threats,
  unique visitors) and the counts by country, status code, browser, content
  type, HTTP version, SSL protocol, class of address and kind of threat; the
  first run reads Cloudflare's whole year and fills a row for each day from the
  first one it has (marked `backfilled`), and every run after re-reads the last
  30 days and corrects a changed total. The per-request dataset, which
  Cloudflare keeps 31 days and samples, gives each day's visits, origin
  response times and top paths, hosts, user agents, browsers, operating
  systems, device types, data centers, cache statuses, methods, bot
  categories, security actions and sources, and request sources, one query a
  list, so a list that fails leaves only its own field null and the row
  `partial`. Only counts are kept: no visitor's address or id is ever asked
  for. The token needs the zone permission Zone > Analytics > Read.
- The config rejects more mistakes, each naming the key: a key given twice in
  any object (the last used to win, silently dropping an entry); a
  `user_agent` that is not printable ASCII (it crashed every source at its
  first request); an entry whose `file` is `health.jsonl`; an unknown key
  inside `queue`, `queue.body_match`, a discourse topic or a region-table
  rule (a rule's internal `match` key crashed a run); a
  `cloudflare_analytics_engine` dimension named `day`, `n`, `timestamp`,
  `dataset` or as a column, which would shadow it in the query; and a
  `refetch_days` over 365 (it crashed the run). A region given as `GB` is read
  as `UK`, so one country is one region in the rows.
- Rows are kept exactly as they were when a file is rewritten: a line with
  bytes that are not valid UTF-8 is written back unchanged (it lost them
  before), and a byte order mark before a row no longer hides it, which wrote
  a duplicate. Files are written through a temporary file of the run, flushed
  to disk before the rename.
- A response cut short, or a reply that is not HTTP, is retried and recorded
  as a failed request like any lost connection; it crashed the source before.

## 0.7.0 - 2026-10-19

- Core ships the places everyone shares: every country's name and code (ISO
  3166-1; `UK` is `GB`), the US states and their abbreviations (USPS) and
  every IANA time zone's country, vendored in the package as three JSON files,
  one per standard, and never fetched at run time. A region table's own
  `location` and `zones` rules come first and core's fill the gaps, so a table
  no longer lists them. Core's names match as whole words (`oman` is not in
  `romania`). `make places` (or the VS Code task) refreshes each file from its
  standard for review.
- A region in a rule or in `known` that is not an ISO 3166-1 code, `UK` or
  `XA` to `XZ` is a config error. A table that sets `attachment` and
  `attachment_value` no longer needs `zones`. A location that names a
  country no table rule names, such as Norway, now gets its region, and a
  refreshed or first-run `zones` set reads every attached file again once.

- The `replay` setting is removed (nothing used it since the switch-over); a
  config that still sets it is an `unknown settings: replay` error. An entry
  with a key its source does not read (`ha_integration` and every other
  source) is now a config error naming the key, as CONFIGURATION always said.
- The fixture capture add-on (`tools/capture/`) is removed; the capture the
  oracle tests read is unchanged.
- Output is written as UTF-8 whatever the console's code page, so a login or
  a path outside a Windows code page no longer crashes `leadline diff` or
  garbles the log.
- The README says what a run with no credentials does, and when to run for
  `pypi`; each source's README says what its token needs and lists its known
  limits.

## 0.6.8 - 2026-10-17

- The trends page no longer charts stars and forks by region; the Stars and
  forks card shows the two counts. The rows still hold the regional counts.
- A failed or reused request's log line names the request with its path. The
  value of a query parameter whose name suggests a credential (`token`,
  `key`, `secret`, `pass`, `auth`, `sig`, `cred`) is shown as `…`, in log
  lines and in an `HttpError`'s text.

## 0.6.7 - 2026-10-16

- A `github_issues` entry with `stars_and_forks` and a `regions` table counts
  stars and forks by region (`repo.stars_by_region`, `repo.forks_by_region`).
  Only counts are stored: no login is stored or logged. A run makes up to 100
  profile reads. The trends page charts them as stacked bands, with unknown
  in gray.
- The trends page leaves out a section whose charts have no rows at any date.
  `trends.json` keeps it.
- The core logs a failed request's cause once, with the host only, at the
  request's last or only attempt, so `goatcounter` and `cloudflare_web` no
  longer log it themselves.

## 0.6.6 - 2026-10-15

- A failed request keeps its cause in the log: each failed attempt's WARN
  line, and the final failure's line for `goatcounter` and `cloudflare_web`,
  shows the connection error's text, or the response body (one line, cut to
  200 characters) and its `server`, `cf-ray`, `date`, `retry-after` and
  `content-type` headers. Request headers are never logged.

## 0.6.5 - 2026-10-14

- `python -m leadline` runs the same command as the `leadline` script.
- The `github_issues` setting `stars_and_forks` (off by default) adds a
  "Stars and forks" row to the Traffic card, drawn from the repo's stars and
  forks already in the rows. `github_repo_stats` shows the same row in a
  Traffic card, and its views are no longer a duplicate chart. An entry may
  give a second section, titled `<source>: <name> <part>` (for example
  `github_issues: owner/repo stars`).
- `github_issues` fills past rows' `repo.stars` and `repo.forks` from GitHub's
  stargazer and fork dates, once. Watchers are not filled. Stars removed
  later are missed, so early counts can run slightly low.
- The leadline logger's level and propagate setting are restored after a run.
- Code and tests no longer carry decision numbers, and CONTRIBUTING notes
  `make setup` after a version bump.

## 0.6.4 - 2026-10-12

- With `comments` set on a `github_contributor_locations` entry, a contributor
  whose region is still unknown gets one from their own comments: the
  entry's `text` rules read ISP names and the like in the first 500
  characters of each comment, and the source is recorded as
  `comment_inference`. Requests are bounded: one pass over the unplaced
  people's issues, again only when a rule changes, then at most one issue a
  run. The log lists the unplaced with their issue numbers.
- The README and `docs/CONFIGURATION.md` explain how history fills in on first
  use: the first run writes today's row, the first run on a later day fills
  in the days before it.
- A region table may set `attachment`, `attachment_value` and `zones`: the
  zone named in a file attached to a person's issue gives a region
  (`timezone_inference`). A zone outranks a modem and issue text, never an
  ISP answer or a profile; it checks those, and a disagreement is logged by
  rule and regions. Files are read once, then at most one a run.
- A region table may set `known`, a login mapped to a region code, for people
  the maintainer knows. The region comes from the config every run, carries
  the source `configured`, and ranks below a profile location and above every
  inferred clue; a clue that disagrees is logged by regions, never by login.
- The trends page groups the two traffic sources into one Traffic card: rolling
  14 days and by day, each row with a one-line description, matching colors.
  Daily charts gain a faint 7-day trend line, unbroken through short gaps, and
  a "last whole week" total in the caption.

## 0.6.3 - 2026-10-11

- A listed pattern of `github_tree_counts` can carry its own `content`
  `pattern` and `map`, so files from before a move that state a value another
  way (a parser's `status = ParserStatus.VERIFIED`) count under the entry's
  key. The catalog's early rows read their status from them.
- With `kinds`, `github_issues` gives every past row `issues.open_by_kind`,
  counted from each issue's created and closed dates; a row the counts don't
  add up to is left as it is and logged.

## 0.6.2 - 2026-10-10

- Issues rows of entries with `kinds` gain `issues.open_by_kind` (open issues
  per kind), written from each run on, with no backfill. The trends page
  draws open issues as stacked bands by kind under the total, beside "Opened
  by kind"; the open pull requests chart is gone (`open_prs` stays in the
  rows).

## 0.6.1 - 2026-10-09

- "Downloads since release" ends a week past the newest release's age,
  however old the older releases are, so the newest line has room to grow
  against them.

## 0.6.0 - 2026-10-09

- The trends page charts `github_releases`: "Downloads by version" (one line
  per release over its own translucent area, newest in front, betas left
  out) and "Downloads since release" (days since each release, for releases
  published since the file began), side by side. Entry settings: `bands`
  and `exclude` (tags left out of the trends and every report that reads
  the file; the file keeps every release).
- The response times chart draws the fastest, median, mean and 90th
  percentile hours as lines of one chart.
- Trends settings for sources: `by`, `lines`, `since`, `half`, `chart`,
  `prereleases`, and `Section.charts` for fields only the page draws.
- The trends page: the tooltip stays whole on screen and above the project
  links, right-hand labels stay inside their card, and a chart of readings
  that are never negative no longer runs below zero.

## 0.5.1 - 2026-10-08

- `github_tree_counts` takes several patterns in priority order (`pattern`
  as a list, each a regex or `{"pattern": ..., "content": false}`): each
  item is counted once a day, from the first pattern that finds it, so
  counts stay continuous across a file move and the history reaches back to
  the first tree any pattern matches. Its `content` setting takes an
  optional `map` so a value written differently over time counts as one.

## 0.5.0 - 2026-10-08

- Every source is a self-contained package (`spec.py`, `source.py`, a README
  and its own tests, scenarios, golden and builders), found by discovery, so
  adding or removing a source is adding or removing a folder; sources that
  share a service sit in a platform folder (`github`, `cloudflare`,
  `home_assistant`). `make docs` writes the source table and the credentials
  table from the packages. Rows, file names and config keys are unchanged.
- `leadline report --config P [--out DIR]` rebuilds the reports, the
  default summary, `trends.json` and the trends pages from the output
  folder's files alone: no network, no credentials, no collection and no
  health row. A run builds them with the same step, after health. It
  loads each source's `spec` only, never its collection code.
- `ha_integration` takes `bands`: `line` (the default, as
  before) or `release`, where every exact release is its own band with no
  cap; `trends.json` breakdowns carry it.

## 0.4.1 - 2026-10-07

- `github_issues` counts `issues.opened_by_kind` again every run for each
  past row that has it, under the `kinds` the config lists now; a changed
  count is a recorded correction, so adding a kind moves its issues out of
  `other` in every row.
- The trends page fits a line chart's axis to its readings (at least 5% of
  the largest reading; bars and stacks still start at zero), joins readings
  more than a day apart by a dashed line over the same fill, and gives a
  chart that is alone in its card, besides stacks, the card's full width.
- A version breakdown stacks each base version's prereleases as its own
  band ("3.14.0 betas"); `trends.json` breakdowns gain `betas`.

## 0.4.0 - 2026-10-06

- `github_issues` takes `kinds`, an ordered list of a name and a label
  each: its rows then hold `issues.opened_by_kind`, the issues opened,
  each counted once under the first kind whose label it carries, else
  `other`, adding up to `opened_total`. Past rows and the days before a
  file's first row are filled from each issue's creation date.
- `github_tree_counts` takes `content`, a regex and a key: it reads each
  matching file's text and counts the files by the regex's group, under the
  key (`unknown` where it doesn't match). A file's blob is read once, so
  an ordinary night costs no extra request; past rows are filled from git
  history, at most 1000 blobs a night.
- A dimension of a `cloudflare_analytics_engine` layout may give a
  `pattern` instead of a list of values; CONFIGURATION says when to
  choose which.
- The trends page stacks every breakdown by count, so its top edge is the
  total: installs by release line with every prerelease as one "betas"
  band, issues opened by kind, contributors by region, the catalog by
  content value. A total a stack caps has no chart of its own
  (`Shown.into`), `Shown.charted` keeps a field off the pages and
  `Shown.neutral` gives a key a grey band. Discussions are labelled
  "Discussion threads" and "Comments on discussions", "Not answered" is
  off the pages, and a chart that starts more than 7 days after its
  section says "Counted from" and the date. `trends.json` is format 3.

## 0.3.3 - 2026-10-02

- `github_response_times` and `github_contributor_locations` recompute
  every past day's row on each run, under the current rules, where they
  filled only the days before a file's first row: rows written before a
  closed issue counted as answered, or before issue forms gave regions,
  get today's values, each change a correction health lists, and a day
  with no row, or a failed one, gets its row (`store.recompute`; ADR:
  derived history follows today's rules). The first run after the
  upgrade lists a correction for each such day once.
- `github_contributor_locations`' check no longer lists an answer no
  rule matches where a `models` rule matches the modem in the same form,
  which places the person.
- A trends page's line (`snapshot`, `change`) no longer breaks at a row
  with no value: two values with such rows between are joined by a dashed
  line with no fill, which is not a reading, and a value with none beside
  it is a dot, so a sparse history, such as a percentile rank read from
  the Internet Archive, shows as points over time rather than a gap.
  Consecutive values are a solid line over a fill, as before.

## 0.3.2 - 2026-10-02

- A region table may read a person's modem too: `model_answer`, a regex
  like `answer`, captures it in an issue form, and `models` rules name
  modems sold in one country only. `exact` rules match a whole answer,
  for names too short to be keywords. A form's region is its answer's,
  `exact` rules then `text` then `location`, else its modem's
  (`config.form_region`). `github_contributor_locations` uses it for a
  person with no profile location, and `github_issues`' queue for each
  listed issue, before the issue text. `check()` warns of each `models`
  rule that gives another region than the answer in the same form, by
  count, never by login (ADR: an answer in an issue form is the
  strongest region signal).

## 0.3.1 - 2026-10-02

- A region table may have an `answer`: a regex whose one group captures a
  person's answer in an issue form, such as their internet provider,
  anywhere in an issue's body. `github_contributor_locations` gives a
  person with no profile location the region of their first answer a
  rule matches, `text` rules then `location` rules, before their issue
  text; only the region is kept. Its `check()` warns of the answers no
  rule matches and the rules that disagree with a profile, by count,
  never by login (ADR: an answer in an issue form is the strongest region
  signal). `config.find_answer` and `config.find_rule` are the helpers.
- Labels: `ha_integration`'s rank reads "Percentile rank (HA custom
  integrations)", `github_response_times`' `never_answered` "Not
  answered", and `github_contributor_locations`' sections "Contributors".
- Trends: a chart of counts, the region stacks included, has an axis
  fitted to its data, the fewest of four grid steps that reach just above
  its largest value (67 contributors top out at 80, not 100).

## 0.3.0 - 2026-10-02

- A GoatCounter 404 is recoverable: the hits request retries it, and a
  404 on any of its requests writes its failure or partial row without a
  critical; `check()` still reports it as an error (ADR: one failed night
  recovers; consecutive failures are critical). `Http.request` takes
  `recoverable`, the statuses a request's API answers that waiting fixes.

- History rebuilt back to its start, for the days before a file's first
  row a night wrote, each row added marked `backfilled`, through
  `store.fill` (ADR: past values a source can rebuild are filled, never
  changed): `github_issues` back to the first issue (open issues and pull
  requests, and the totals); `github_response_times` back to the first
  community issue; `github_contributor_locations` back to the first
  person's first issue, each with today's region, on a night that reads
  every profile; `github_tree_counts` from git history, back to the first
  tree that matches, one commits listing and a tree per day with a commit,
  once, going on the next night after a failed request; `discourse_topics`
  per topic back to its creation (replies and last post; views and likes
  null), asking for the posts its answer leaves out.
  `github_discussions`' rebuilt rows get `total_comments` from a second
  query, sent only while a day lacks it. A new file gets its history the
  night after its first row.
- `store.fill` fills a null in a `backfilled` row with a value, takes a
  `second_key` (the forum's `topic_id`); `store.first_night`,
  `store.rebuild_days` and `store.lacks` give the days to rebuild and what
  a day lacks.
- `github_response_times` counts a community issue as answered at its
  closing when that comes before the maintainer's first comment, or there
  is none, so an issue fixed or closed without a comment is no longer
  "never answered"; every field of the row follows. Rows already written
  are unchanged. Its summary line reads "Median hours to an answer".
- The default summary and trends: `ha_integration` shows installs, then
  installs by version, then its rank as "Ahead of (% of integrations)",
  100 less its `percentile`, which climbs as the integration rises, in
  place of the rank. `github_discussions` shows comments
  (`total_comments`) in place of answered.
- `config.Shown` takes `complement`, which shows 100 less a percent field,
  and the reports language a piece's `complement: true`, for a field
  alone. A `trends.json` field gains `complement` where it is set.
- Trends: a new optional `pages` setting writes a trends page per
  portfolio, each a file, a title shown at its top, and the projects it
  shows, in its order; each page holds only its projects' sections, and
  only the health that concerns them, plus every crash and every
  reports or trends failure.
  Without it, one `trends.html` as before. `trends.json` still holds
  everything. It is validated before anything runs.
- Trends: each source gives its sections a short readable label
  (`Issues`, `PyPI downloads`), and a panel is headed by it, with the
  entry's name where the label alone can't tell it apart (in no project,
  or two sections of one source in a project). A project's section may be
  `{"section": <title>, "title": <heading>}` to give its own heading.
  Each `trends.json` section gains `label` and `heading`. A source's
  `summary()` sections take the label (`config.Section`,
  `config.entry_summary`).
- Trends: a breakdown that isn't of versions (contributors by region)
  stacks counts from zero, its axis and labels in counts; a breakdown of
  versions still stacks shares.

## 0.2.0 - 2026-10-01

- `github_issues` fills `issues.opened_total` and `issues.closed_total`
  into each past row that lacks them, counted as of that row's day from
  every issue's dates, before it reads the previous snapshot for the
  flow. `github_discussions` writes a row for each day from the first
  discussion to the day before its file's first row, with the total, who
  opened them and their categories, null for what a past day can't tell,
  when its query reads every discussion. Both go through the new
  `store.fill`, which adds only what is missing, never changes a value,
  keeps each row's place, marks the rows it adds `backfilled`, logs one
  line per file, and records no correction.
- `trends.html` leaves out a section with no field to chart or show
  (`github_releases`', whose content is report lines) instead of an empty
  panel; `trends.json` keeps it.
- Trends: a new optional `projects` setting groups `trends.html`'s
  sections by project, each a name and the titles of its sections, with
  a row of links between projects that stays at the top; sections in no
  project show last, under "Other". It is validated before anything runs.
  Each `trends.json` section gains `project`, and the sections come in
  the page's order.
- `trends.json` is format 2: a breakdown of versions (Home Assistant
  installs by version) is added up by release line (`3.14`, a
  prerelease counting toward its base's line), its keys the 5 newest
  lines, so the chart shows the whole history rather than folding all
  but the 5 newest versions into "older". The default summary's line is
  unchanged.

## 0.1.0 - 2026-09-30

- Trends: every run writes `trends.json` in the output folder, the history
  of each field the sources' summaries show (a series per field, dates
  once per section, good rows only) and the newest health findings, in a
  versioned format documented in `docs/TRENDS.md`; and `trends.html`, a
  page with a copy of it that charts each field in its own SVG, with
  values on hover and a 30d / 90d / 1y / All range, loading nothing.
  A trends file that can't be written is critical.
- A source's `summary()` gives `config.Section`s (the file, the title and
  the fields shown), which the default summary renders and trends charts.
  A new field kind, `breakdown`, for an object of counts (Home Assistant
  installs by version, contributor regions), shows in the summary as one
  line of the largest counts, through the new `counts` report aggregate.
  `ha_platform`, Home Assistant as a whole, has no sections.
- Project scaffold: stdlib-only package for Python 3.8+, `make check` gate
  (ruff, mypy as 3.8, pytest), CI testing on Python 3.8–3.14.
- `leadline run --config P [--only a,b] [--out DIR] [--user-agent UA]`:
  loads the JSON config, runs each enabled source in config order with
  failures isolated, logs to stdout and `leadline.log`. Exit status 1 only
  when something is critical (an error waiting can't fix, or a source
  crashing, which is recorded as critical); 0 when the run's failures are
  recoverable, failure rows included; 2 for a bad command line or config.
- Core modules: `config`, `context`, `http` (urllib with retries, Link-header
  pagination, GraphQL), `store` (upsert and window replace, byte-identical to
  `collect-metrics.sh`, and `write_failure`, which never puts a failure row
  over a good row), `runner`, and the source registry.
- `leadline diff A B`: compares the row files in two folders, ignoring
  `captured_at`, one line per difference; exit 0 when they match, 1 when not.
- Oracle harness (tests only): runs a copy of `collect-metrics.sh` against
  saved responses with `curl`, `date`, `sleep` and Python's clock and network
  shimmed, failing closed, to produce the golden rows of the test scenarios.
- Test scenarios for all seventeen sources, built in code from per-API
  response builders with made-up values; one golden file per source. A
  source the script doesn't have gets reviewed expected rows as its golden.
- Sources are named for the platform and data they read (`ha_integration`,
  `github_tree_counts`, `github_repo_activity`,
  `cloudflare_analytics_engine`, `discourse_topics`), and the traffic
  snapshot and its daily rows are two sources (`github_traffic`,
  `github_traffic_daily`). `tests/test_scenarios.py` ties ARCHITECTURE.md's
  source table to the scenarios, goldens and registry.
- Config: a top-level `shared` section of tables (`regions`) that entries
  name, put in place at load; `config.default_file` and `config.file_name`
  for each entry's output file and its `file` template; every source's
  `validate()` runs before anything else, then no two entries may write one
  file (`files()`, names compared ignoring case); a problem exits 2 with
  nothing written. `docs/ADDING_A_SOURCE.md` gives the steps for a new source.
- First source, `cloudflare_web`: a site's daily page views and visits and
  the week's top referrers from Cloudflare Web Analytics, one row a day,
  row-compatible with the script's (2026.09.25.2): each row records the
  `site_tag` it was read under, and each run re-reads the last 30 days,
  filling a missed or failed day or one read under another tag (marked
  `backfilled`), and correcting changed numbers; a failure never
  replaces a good row; an answer it can't read is a `parse_failed` row;
  rejected credentials aren't retried. `check()` tells a site tag from a
  site's beacon token.
- Sources `ha_integration` (a custom integration's installs by version and
  its rank, from Home Assistant's analytics; one entry per `integration`,
  the feed read once) and `ha_platform` (Home Assistant's platform-wide
  installations, reporting shares, install types and top countries; one
  object), row-compatible with the script's. A failed fetch is a
  `fetch_failed` row and an answer they can't use a `parse_failed` row,
  where the script writes nothing (or zeros, for a feed that lists no
  integrations or a platform answer without `current`); 401, 403 and 404
  are critical. `check()` finds an
  integration the feed doesn't list, and platform fields the answer lacks.
- Sources `github_tree_counts` (the paths in a repo's git tree that match
  a pattern: files, items, groups and items per group, under row keys the
  entry names; a truncated tree marked) and `github_releases` (a repo's
  first 100 releases, with each asset's downloads), row-compatible with the
  script's. A failed fetch is a `fetch_failed` row and an answer they can't
  use a `parse_failed` row, where the script writes nothing (or zeros, for
  a tree answer without a tree, or no releases, for an empty object); an
  error waiting can't fix is critical. `check()` finds a repo or ref that
  doesn't exist, and a pattern that matches no path.
- Sources `github_traffic` (a repo's 14-day views, clones and top five
  referrers as one snapshot, with `window_end`, the window's last day) and
  `github_traffic_daily` (a row per day of the window: missing days
  backfilled, yesterday refreshed, never today), row-compatible with the
  script's. They read each answer as JSON, so a quote or `'''` in a referrer
  no longer loses the row. A failed fetch is a `fetch_failed` row and
  an answer they can't use a `parse_failed` row, where the script writes
  nothing; the daily source dates it yesterday, and treats a day without a
  timestamp as an answer it can't use. A token without push access (403) is
  critical. `check()` finds a token that can't read the repo's traffic.
- Sources `github_repo_stats` (a repo's stars, forks, watchers and open
  issues, with its 14-day views and clones totals) and
  `github_repo_activity` (one public repo's week: commits, authors, fixes
  and redesigns by configurable patterns, issues, top contributor's share,
  latest release, code size; filed under a `label`), row-compatible with
  the script's. The repository (and the commits) are required: a failed
  fetch is a `fetch_failed` row and an answer they can't use a
  `parse_failed` row, where the script writes nothing; a quote or `'''` in
  the repository no longer loses the row. The other parts are
  optional: one that fails, or that they can't use (an issue with a null
  user included), is null in a partial row, where the script writes 0 or
  none, or loses the row. An error waiting can't fix, on any request, is
  critical. `check()` finds a repo that doesn't exist and, for the stats, a
  token that can't read its traffic.
- Source `github_issues` (a repo's issues and pull requests: counts, flow
  since the previous snapshot, who opened them, ages, engagement, labels
  and triage; optionally one label's `queue`, its keys, body pattern and
  region table from config), row-compatible with the script's. Every
  request is required: a failed one is a `fetch_failed` row and an answer
  it can't use (a page that isn't a list, `{}` included, or an issue it
  can't read) a `parse_failed` row, where the script writes nothing; an
  error waiting can't fix is critical. A failure row is never a previous
  snapshot. `check()` finds a repo that doesn't exist, a token that can't
  read its issues, and a queue label the repo doesn't have.
- Sources `github_discussions` (a repo's 100 most recently updated
  discussions: the total, who opened them, community authors, answers,
  comments, categories and the ten most recent) and
  `github_response_times` (how long community issues wait for the
  maintainer's first reply: median, mean, 90th percentile and fastest
  hours, and the share within 1, 24 and 72 hours, paging through every
  issue by cursor), over GitHub's GraphQL API, row-compatible with the
  script's; each entry's `maintainer` is required. A failed request is a
  `fetch_failed` row, GraphQL errors a `graphql_error` row and an answer
  they can't use a `parse_failed` row, where the script writes nothing; an
  error waiting can't fix (HTTP 401, 403 or 404, or a GraphQL `NOT_FOUND`,
  `FORBIDDEN` or `INSUFFICIENT_SCOPES`) is critical, a GraphQL rate limit
  is not. `check()` finds a repo that doesn't exist, a token that can't
  query its discussions or issues, and a maintainer login that doesn't
  exist or is spelled otherwise, asked for in the same query;
  `github_issues`' `check()` does the same for an entry with a maintainer,
  in one more request.
- Source `github_contributor_locations` (where the people who open a
  repo's issues and pull requests are: each person's profile location
  matched against the entry's region table, which is required, or their
  issue text where they have no location; the count per region),
  row-compatible with the script's. The issues are required: a failed page
  is a `fetch_failed` row and an answer it can't use a `parse_failed` row,
  where the script writes nothing. A profile that fails or can't be read
  is that person's alone: their entry has a null location and region
  and the source `profile_failed`, the totals count only the profiles
  read, and the row is partial, counting the people unread, where the
  script reads that person's issues as if the profile had no location. A
  same-day run's readable entry for them is kept, and the row is good once
  every profile has been read by some run. A 404 is no profile, as in the
  script. The token goes in a header, never on a command line. `check()`
  finds a repo that doesn't exist and a token that can't read its issues.
  `store.stored` and `store.write_merged` serve a source that merges
  inside a part itself; `write_partial` now ends in `write_merged`.
- Sources `goatcounter` (a site's page views by day across the week
  before the run, missing days backfilled and yesterday refreshed, never
  today; yesterday's row with the week's top five referrers of the first
  ten pages, the window's sum and GoatCounter's own total) and
  `discourse_topics` (each configured topic's views, replies, likes and
  last post on a Discourse forum at `base_url`, a row per topic a day,
  keyed by date and `topic_id`), row-compatible with the script's.
  GoatCounter's pages are required: a failed request is a `fetch_failed`
  row and an answer it can't use a `parse_failed` row for yesterday, where
  the script writes nothing; the total and the referrers are optional, and
  one that fails or can't be used is null in a partial row, where the
  script writes a null total or leaves a page's referrers out. Referrers
  tied on count come in the order their pages came back. A forum topic
  that fails writes its own failure row, where the script skips it. An
  error waiting can't fix is critical. `check()` finds a GoatCounter site
  that doesn't exist or a token that can't read it, and a forum that
  doesn't answer or a topic it doesn't have.
- Source `cloudflare_analytics_engine` (a Workers Analytics Engine
  dataset's daily totals over SQL, a row per UTC day, the last
  `refetch_days` re-read and rewritten): the config gives the dataset's
  `layouts`, oldest first, each ending at a UTC time, with its dimensions,
  their allowed values, its private columns and the row's fields (a total
  by one dimension, optionally of a `double` column's sum, or a count of
  events where a column is set). The script's layout, given first, writes
  the script's rows to its file; a day that holds a layout switch adds up
  both layouts. A private column is never selected or grouped by, no query
  totals two dimensions, and a value outside its dimension's allowed
  values is left out of the row and never written or logged, and is
  critical with a count only. A failed query writes no day and a failure
  row for yesterday; an answer or result row it can't read is a
  `parse_failed` row, where the script writes nothing; rejected
  credentials aren't retried and are critical; `refetch_days` 0 is
  refused. `check()` finds a dataset with no events in the window, and API
  trouble.
- Source `pypi` (each package's downloads by day from pypistats: the day's,
  week's and month's, with the latest version and required Python from
  PyPI; earlier days since the file's first row backfilled from the
  history, from where a month's window is whole; the packages rotated one
  place each night and `sleep_s` apart; a date not yet published marked
  `fetch_failed`), row-compatible with the script's, and its retry pass,
  which waits `retry_wait_s` and asks again for each package whose history
  request failed. An error waiting can't fix (a 404 among them) is never
  retried, in either pass, and is critical. A history it can't use is
  a `parse_failed` row, where the script writes nothing; failed or unusable
  metadata leaves the version and required Python null in a partial row,
  where the script writes "" or nothing; a failure never replaces the run
  date's good row, and a package with one isn't asked again. `check()` asks
  PyPI, never pypistats, whether each package exists.
- The runner's retry pass: a source that defines `retry` (only `pypi`) gets
  a second pass after every source has collected, isolated as collection
  is. `http` takes a source's `backoff_s` between attempts, and `wait` spaces
  a source's requests. `store.write_partial` takes a row whose good form
  carries `status: "ok"`, and marks it `partial` in place.
- `store.write_window` leaves a failure row out where a good or partial
  row holds its date, as `write_failure` does, and says so; the port
  tests' saved connection failures read as urllib's do.
- `github.graphql` sends a GitHub GraphQL query and judges its errors
  (`GraphqlError`), `github.parse_time` reads a GitHub timestamp, and
  `github.maintainer_problems`, `github.check_query` and
  `github.check_maintainer` check a required maintainer, a GraphQL query
  and a maintainer's login; `ctx.record_unfixable` records a failed
  request critical when waiting can't fix it, for every source, in place
  of the test each source repeated.
- Region tables are checked where they are defined, at load
  (`shared.regions.<name>`), and matched by `config.find_region`;
  `store.rows` reads a file's rows in order; `github.numbered_pages` asks
  for a list page by page, as the script does, for `github_issues` and
  `github_repo_activity`.
- Partial rows (`store.write_partial`): a row built from several requests
  keeps its good parts where others failed; each failed part is null,
  never 0, and the row has `status: "partial"` and a `reason` naming each
  failure. It is a failure row: it never replaces a good row, and the next
  good row replaces it.
- `http` tells a rate limit from a rejection: a 429, or a 403 with
  `Retry-After` or `X-RateLimit-Remaining: 0`, is recoverable
  (`HttpError.rate_limited`), never critical; `HttpError.unfixable` is the
  test every source uses for an error waiting can't fix.
- Every correction is recorded, by store, for every source: a good row for
  a past day replaced with other values (`store.write_good`,
  `store.write_window`, which every source writes its good rows through),
  with the values that changed and the second key; `github_traffic_daily`'s
  refresh of yesterday now records one, and `cloudflare_web` records every
  changed value of yesterday's rewrite, not only its two numbers.
- Sources report errors waiting can't fix on the context
  (`ctx.record_critical`), for health, as store reports corrections
  (`ctx.record_correction`); `check()`
  returns `context.Finding`s. `http` never retries 401, 403 or 404, and can
  count an empty success as a failure (`require_body`);
  `store.rows_by_date` reads a file's rows.
- `github`, a core module holding what the GitHub sources share: the API,
  the token's headers, the `owner/name` repo check, the `repo` and
  `repo_name` file-name fields, and `check()`'s access findings. The four
  GitHub sources use it; their rows and findings are unchanged, and their
  logs too but for the reuse lines below. Any source may import it;
  sources still never import each other.
- A run reuses its successful answers: `http` keeps each successful GET's
  answer for the run and serves it to an identical GET (same URL and
  headers), logging `Reused the answer to GET <url>`, so two sources asking
  GitHub for the same thing make one request. Failures are never kept, a
  POST is always sent, and nothing outlives the run.
- Health: each run writes one row for the run date to `health.jsonl`
  (a rerun replaces it), row-compatible with the script's
  `check_freshness` for the same files: `stale`, `failed` with its
  streak, `gaps` in the last 14 days, with the package version as
  `script_version` and the start of the config file's SHA-256 as
  `script_hash`. It adds `checks` (every source's `check()` findings; the
  runner checks each source that wasn't skipped, after collection and the
  retry pass, and a check that raises is critical), `critical` (the run's
  errors waiting can't fix, crashes, and each configured source's file
  with no good row for two or more consecutive expected dates, which makes
  the run exit 1) and `corrections`. A line that isn't a row, or whose date
  isn't a date, is skipped, where the script's check stops and writes no
  row. Health scenarios run the script's `check_freshness` as the oracle.
- Port tests (`tests/port.py`): a source runs through the runner's
  collection on its scenarios' saved responses, at their frozen time, and
  its rows are compared with the goldens; decided divergences are listed
  per source and asserted apart.
- Fixture capture add-on (dev tool, `tools/capture/`): sourced by
  `collect-metrics.sh` for one night, it records every request the script
  makes and its response as saved-response files, credentials replaced by
  placeholders, without changing what the script does.
- Reports from config: the top-level `reports` lists blocks that leadline
  writes into any Markdown file (relative to `out_dir`), between
  `<!-- leadline:start NAME -->` and `<!-- leadline:end NAME -->`, never
  changing text outside them. A new block goes at the top or bottom of its
  file, and is rewritten in place after that; a file that doesn't exist is
  created. A block is a `table` (a row a day, or a week with the newest
  weeks a row a day; columns with `last`, `change`, `range` or `versions`,
  a field from another file by date, a ratio, a format, bold) or `lines`
  (a value with its change since yesterday or N days, a series, or the
  named aggregates `peak`, `sum`, `versions`, `items`, `latest_release`
  and `release_downloads`), from good rows only. Health now runs before
  the reports, so the critical block reads the run's own `critical[]`
  from `health.jsonl`. Each block stands alone: a marker missing, repeated
  or out of order, an unreadable file, or a crash is logged and critical
  (exit 1) for that block, and the others are written. The report config
  is validated with the sources', before anything runs.
- Report lines and table cells shaped by config: a `text` template of the
  fields each kind gives (`{then}`, `{change:+}`, `{row.repo.forks}`),
  `parts` joined into one line, `when` conditions, `replace` and `shorten`
  for a value's text, and an `as_of` template; a field without a value
  leaves out what names it. Items can be matched, skipped and joined on one
  line; `versions` picks releases, the newest prereleases or the older ones
  as items with their share and an N-day trail; `latest_release` can find
  the release of a version another piece shows (`tag`); a peak gives its
  age. A table column can combine its aggregates in one cell (`**N** (+d)
  · range lo-hi` where it varies).
- `leadline --version`, and `make pyz`: `dist/leadline.pyz`, the package as
  one stdlib zipapp that `python3 leadline.pyz` runs on Python 3.8+, as the
  NAS does (`tools/build_pyz.py`; tested on every Python CI runs).
- Replay: a config's `replay` (`{"dir", "hosts"}`) answers every request to
  those hosts from the saved responses in the folder and never sends one;
  a request with none left fails as if offline and is logged. The shadow
  run replays pypistats.org from the script's own answers.
  `http.SavedResponses`, which the port tests now use too, `http.read_saved`
  and `http.replaying`.
- `leadline diff` ends by naming each file that differs, with its count.
- Release (`.github/workflows/release.yml`): a pushed tag `v<version>` runs
  CI's gate, then `tools/check_release.py`, which stops the release unless
  the tag is `v` and the package version and CHANGELOG.md has that
  version's heading, then builds the sdist and wheel and publishes them to
  PyPI by trusted publishing, with no stored token. The package metadata
  drops the license classifier, which PyPI refuses beside the license
  expression.
- README: a GitHub Actions recipe, a workflow that runs `leadline.pyz`
  each day in the user's own repo, with each credential from a
  `LEADLINE_<variable>` secret, and commits what the run wrote back to the
  repo, a failed night's rows included (`tests/test_actions_recipe.py`
  checks it against the code and runs its steps in a scratch repo).
- The default summary: a config with no `reports` gets `SUMMARY.md` in
  the output folder, the run's health (stale, failed and critical files,
  check warnings) and a section a source entry with its main numbers and
  their change over 7 and 30 days. Each source names its fields in a new
  contract member, `summary(cfg)`, built with `config.summary_section`.
  `"reports": []` writes no report. `leadline default-reports --config P`
  prints the default as a `reports` setting to paste and change. The
  README's quick start relies on it.
- Reports: `sum` gives `change`, and gives `prior` and `change` only once
  the window before is whole.
