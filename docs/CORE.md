# Core modules

The APIs sources build on: `http`, `store` and, for the GitHub sources,
`github`. The rest of the core (`config`, `context`) and how the modules
fit together are in [ARCHITECTURE.md](ARCHITECTURE.md); the reasons are in
[ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md) (cited as "ADR:
*title*").

## HTTP

`ctx.http` (`http.Http`), the run's one HTTP client. Every request carries
the configured user agent and a 60-second timeout.

### Requests

| Call | Does |
| ---- | ---- |
| `request(url, method=, headers=, body=, attempts=1, require_body=False, backoff_s=10, recoverable=())` | the general form; returns a `Response` (`status`, `headers` with lower-cased names, `body`) |
| `get_json(url, headers=, attempts=1)` | a GET's JSON |
| `get_pages(url, ...)` | a JSON array and every page after it (`Link: rel="next"`), concatenated |
| `graphql(url, query, variables=None, ...)` | POSTs the query as JSON and returns the whole reply, `errors` included, for the source to judge |
| `wait(seconds)` | pauses between requests, for an API that wants them spaced (`pypi`); tests record it instead of sleeping |

- **Errors.** Any failure raises `HttpError`:
  - `code`: the HTTP status, or 0 when no response came back (a refused or
    timed-out connection, or an answer cut short or not HTTP at all);
  - `reason`: the body or the error text, in full (sources truncate it for
    rows);
  - `headers`: the response's headers, names lower-cased;
  - `http_code`: `code` as a failure row gives it, curl's three digits in
    the script: `"503"`, or `"000"` for no response.
- **Retries.** One attempt unless the source asks for more. Before retry
  n it waits n × 10 seconds, as the script does, or n × `backoff_s` where
  the source gives one (`pypi`). Tests record these waits. An error waiting
  can't fix is never retried.
- **Failure causes.** Each failed attempt is logged with its cause,
  `WARN: attempt 1/3 failed (HTTP 404: <body> [server=..., cf-ray=...]),
  retrying in 10s: <url>`, from `HttpError.detail`: the connection error's
  text (`HTTP 000: <urlopen error timed out>`) or the response body, on one
  line and cut to 200 characters, then the response's `server`, `cf-ray`,
  `date`, `retry-after` and `content-type` headers when present. A failure
  that ends the request (its last or only attempt, or an error waiting can't
  fix) is logged the same way, with `giving up` in place of `retrying in`:
  `WARN: attempt 3/3 failed (HTTP 000: <cause>), giving up: <url>`. Each
  line names the request, path and query included, except that the value of
  a query parameter whose name holds
  `token`, `key`, `secret`, `pass`, `auth`, `sig` or `cred` is shown as `…`
  (an `HttpError`'s text and the `Reused` line do the same). So every source's failure
  has its cause in the log, and a source adds no line for it. A request that
  succeeds on its first attempt, or is answered from what was kept, logs
  no such line.
  Request headers, tokens included, are never logged.
- **Rate limits and rejections** (ADR: one failed night recovers;
  consecutive failures are critical):
  - `HttpError.rate_limited`: a spent rate limit, which waiting fixes: a
    429, or a 403 with `Retry-After` or `X-RateLimit-Remaining: 0` (APIs
    such as GitHub's answer a spent limit with 403). It is recoverable,
    never critical, and retried like any other failure.
  - `HttpError.unfixable`: an error waiting can't fix: a status in
    `http.UNFIXABLE` (401, 403, 404) that isn't a rate limit, nor in the
    request's `recoverable`.
  - `recoverable`: the statuses in `http.UNFIXABLE` that the source's API
    answers at times for a request waiting fixes (`goatcounter`'s 404).
    The request retries them like any other failure, and their error
    isn't unfixable, so it isn't critical at once.
- **Required body.** `require_body=True` counts a success with an empty
  body (or only newlines) as a failure, an `HttpError` with its status,
  retried like any other, as the script's GraphQL retry loop does.
- **Transport.** Every request goes through a transport (`urllib` by
  default). Tests inject a fake one, so no test touches the network.

### Answer reuse

A run asks once for each GET (ADR: a run reuses its successful answers).

- The run's `Http` keeps each successful answer to a GET and serves it
  again to an identical GET later in the run: the same URL and headers
  (their order aside; names as given). Two sources asking GitHub for the
  same thing make one request.
- Every answer the transport returns is kept, one its source can't read
  included (but not an empty body where `require_body` asked for one), so
  asking again in the same run gets the same answer.
- A failure (`HttpError`) is never kept: a retry, or the next source's
  request, is sent.
- A POST (GraphQL, SQL), or a GET with a body, is always sent.
- A kept answer with an empty body isn't served where `require_body` asks
  for one.
- Each reuse logs `Reused the answer to GET <url>` (the URL as above).
- Nothing outlives the run. Sources neither change nor know.

### Saved responses

**`http.SavedResponses`** is a transport that answers from saved
responses in the [case-folder format](TESTING.md#case-folders): matched on
method, URL and body, each served once, several for one request in order.
A request with none left fails as if offline (`HttpError` code 0, `http_code`
`"000"`). The port tests serve their scenarios through it; a run never does.

### Reading an answer

For a source that reads an answer part by part and names the part in a
row's reason:

- `read_json(kind, response)`: the answer's JSON.
- `counts(kind, answer, fields)`: each of `fields` (the row's key to the
  answer's) read from an object, 0 where absent, each an int.
- `config.is_count(value)`: an int, never a boolean. It is in `config` so
  that a source's `spec.py`, which imports no `http`, can use it too.
- An answer it can't use raises `UnusableError`, whose message is the
  reason: `<kind> response was not JSON`, or `unexpected(kind)`'s `<kind>
  response had an unexpected shape`.

## GitHub

`github` (`leadline.sources.github`, in `src/leadline/sources/github/`, the
platform group of the GitHub sources; a source imports `api.py` as
`from leadline.sources.github import api as github`) holds
what the GitHub sources share and nothing any one of them owns; each keeps
its own requests, rows and messages (ADR: sources are modular by
construction).

### Requests and config

- `API`, and `CREDENTIAL` (`GITHUB_TOKEN`), the variable in each GitHub
  source's `ENV`.
- `headers()`: every GitHub request's headers: the token, and GitHub's JSON
  asked for.
- `repo_problems(entry)`: `["repo: must be owner/name"]` unless the entry's
  `repo` is `owner/name`; `maintainer_problems(entry)`: `["maintainer: must
  be a login"]` unless its `maintainer` is a non-empty string. For
  `validate`. They and `file_fields` are in `sources/github/spec.py`, which a
  GitHub source's `spec.py` imports (`from leadline.sources.github.spec import
  repo_problems`); `github` re-exports them.
- `file_fields(repo)`: the fields a GitHub entry's `file` template may use,
  `repo` and `repo_name`, for `config.file_name`.
- `numbered_pages(ctx, url, *, accept=None)`: every page of a list, asked
  for by number (`url&page=N`) while the answer's Link names a next page, as
  the script asks, each answer returned unread; `accept` asks for another
  media type. A failed page raises its `HttpError`.
- `parse_time(value)`: a GitHub timestamp, with its offset, as the script
  reads one; anything else raises `ValueError`.
- `local_date(ctx, value)`: a GitHub timestamp's date in the run's UTC
  offset, as rows are dated; `ValueError` as `parse_time`.
- `profile_location(ctx, login)`: the location of a user's profile, `None`
  for none set or no such user (a 404); a failed request raises its
  `HttpError`, an answer it can't read `UnusableError`. Both
  `github_contributor_locations` and `github_issues`' stars and forks by
  region read profiles through it.

### GraphQL

- `graphql(ctx, kind, query, variables)`: one POST of a query to `GRAPHQL`,
  GitHub's GraphQL API, with the token's headers, as the script sends it.
  Returns the answer's `data`. It raises:
  - the request's `HttpError` when it fails;
  - `http.UnusableError`, naming `kind`, for an answer that isn't JSON or
    isn't an object;
  - `GraphqlError` for an answer with `errors`. Its message joins the first
    three errors' messages, as the script's Cloudflare collector writes
    them.
- `GraphqlError.unfixable` is true when an error's `type` means what a 404
  or 403 means (`GRAPHQL_UNFIXABLE`: `NOT_FOUND`, `FORBIDDEN`,
  `INSUFFICIENT_SCOPES`). Any other, such as a spent rate limit
  (`RATE_LIMITED`) or an error with no type (a timeout), is recoverable.
- `owner_and_name(repo)`: a query's `o` and `n` variables.

### Checks

Building blocks for a GitHub source's `check()`, each returning findings.

- `failed_finding(where, error, fetching, missing=, forbidden=None)`: the
  finding for a failed request. An error waiting can't fix is an `error`
  (`forbidden` is its reason for a 403 where the source knows a better
  one); anything else, a rate limit included, is a `warn`.
- `check_access(ctx, repo, path, fetching=, ok=, forbidden=None)`: whether
  the token can read the repo's API `path`.
- `check_repo(ctx, repo)`: the repo itself (`the repo exists`).
- `check_traffic(ctx, repo)`: its traffic, which needs push access (`the
  token can read its traffic`, or on a 403, `the token can't read its
  traffic, which needs push access`).
- `check_query(ctx, repo, selection, fetching=, ok=, maintainer=,
  misread=)`: whether the token can query `selection` of the repo over
  GraphQL: `ok`; a failed request's `failed_finding`; GraphQL errors as an
  `error` when waiting can't fix them, else a `warn`; an answer without the
  repository, a `warn`. Given a `maintainer`, the same query asks for that
  login (`user(login:)`), and a second finding says whether it exists.
- `check_maintainer(ctx, repo, maintainer, misread=)`: asks for the login
  alone, for a check with no GraphQL query of its own (`github_issues`,
  when its entry names a maintainer).

A maintainer finding is an `error` when GitHub has no such user, or spells
it otherwise (rows compare logins exactly), naming what every night would
misread (`misread`, in the source's words). A mistyped maintainer fails
nothing at collection; the check is where it shows.

## Store

All reads and writes of row files. Store reproduces the script's two write
paths byte for byte, except that no line in a file makes a write fail
(ADR: store never fails on a line it can't use). Its tests run the
script's own writer code as the oracle.

Sources write only through `write_good`, `write_window`, `write_failure`,
`write_partial`, `write_merged`, `fill` and `recompute`, never through
`upsert` or `replace_window` directly
([ARCHITECTURE.md](ARCHITECTURE.md#failures-and-what-sources-report)).

### Reading rows

- `store.rows_by_date(path)`: rows with a string `date`, keyed by it, the
  last line for a date winning. A source reads these to decide what to
  write, such as whether a good row is already there.
- `store.rows(path)`: every line that is a JSON object, in file order, for
  a source that reads line by line as the script does (such as
  `github_issues`' previous snapshot).
- `store.parse_date(value)`: a `YYYY-MM-DD` string naming a real day, else
  None. Health and the reports skip a line without one.
- `store.is_good(row)`: a `status` absent or `ok`; the test of a good row
  ([Terms](ARCHITECTURE.md#terms)) for any code that reads rows.
- `store.dated_rows(path, where=None, hide=())`: the file's rows as the
  reports and trends read them: `good`, the good rows, one a date (its last
  line), oldest first, and `newest`, the newest date with any row; a line
  without a real date is skipped, and `where` keeps the rows whose dotted
  fields have those values. `hide`, a list of `config.Hide`, leaves items out
  of a list in each good row (copies; the file is as it was): the items of the
  list at the `field` whose `key` (a leading `v` ignored) is in its `values`.
  A section's `hide` is how a source's `exclude` reaches every reader.
- `store.value(row, field)`: the value at a dotted path (`issues.open`),
  None where there is none.
- `store.stored(path, row, second_key=None)`: the stored row for `row`'s
  key: the last good or partial line.

### Atomic writes

Every file leadline writes, row files and reports, is replaced atomically
by `store.write_text(path, text)`: the text, in UTF-8 with its line ends as
given, goes to `<file>.<pid>.tmp` (a name of the process, so two runs never
share one), is flushed to disk and is then renamed over the file; a failed
write leaves no temporary file. A run assumes it is the only writer of its
folder: two overlapping runs can still lose one run's row.

Neither write path reads the clock: each takes `now`, the run's `ctx.now`,
and refuses one without a UTC offset.

### Upsert

`store.upsert(path, row, now, second_key=None)`: the script's write path
for every source but `cloudflare_analytics_engine`. It is behind
`write_good` and `write_failure`.

- **Key.** The row's `date`, or `date` plus `second_key`
  (`store.SECOND_KEY`, the forum's `topic_id`, which `leadline diff` also
  matches rows by). A line matches only where each field equals the row's,
  as the script compares them, so a `topic_id` written as a string
  (`"1001"`) has no usable key: it is kept, never replaced.
- **Existing lines** are kept as they are unless their key matches; every
  matching line is dropped. Blank lines are dropped; lines that don't
  parse or aren't an object are kept; a missing final newline is added.
  The file is read as UTF-8 and `\r\n` as `\n`, as the script reads it; a
  byte that isn't valid UTF-8 is kept and written back unchanged (the script
  drops it), and a byte order mark before a row doesn't hide it from its key.
- **The new row** gets `captured_at`, `now` to the second (local time with
  offset), as its last key, and is **appended**. The file is not
  re-sorted. The caller's dict is not changed.
- **Serialization** is `json.dumps` with default settings: `", "` and `":
  "` separators, `ensure_ascii=True`, insertion order.
- **Returns** the row it replaced, or None.

### Window replace

`store.replace_window(path, rows, now)`: the script's write path for
`cloudflare_analytics_engine`, behind `write_window`.

- Loads the existing rows keyed by `date` (the last line for a date wins)
  and replaces the days `rows` covers.
- Rewrites the file: lines that aren't a row (they don't parse, aren't an
  object or have no string `date`) first, as they were, then every row,
  re-serialized and **sorted by date**.
- Each of `rows` gets `captured_at` from `now`.
- Returns the rows it replaced, by date.

### Good rows

`store.write_good(path, row, ctx, source, second_key=None)` (ADR: every
correction is recorded):

- `row` must be a good row; a failure row is refused (it goes through
  `write_failure`).
- It is written as an upsert with `ctx.now`, byte for byte.
- Where it replaced a good row for a date before `ctx.date` with values
  that differ (`captured_at` aside, compared as JSON, an object's keys in
  any order), it calls `ctx.record_correction` with the source, the file,
  the date, the values that differ before and after, and the second key.

`store.write_window(path, rows, ctx, source)` does the same for a window
replace, one check per date. Its `rows` may hold a failure row, as the
script's window does; that row is left out where a good or partial row
holds its date, with `write_failure`'s WARN line, and the rest of the
window is written.

### Failure rows

`store.write_failure(path, row, now, second_key=None)` (ADR: a failure
never replaces good data):

- `row` must be a failure row; a good row is refused.
- Where any line for its key (its `date`, and `second_key` when given) is a
  good or partial row, nothing is written, and store logs `WARN: kept the
  <good|partial> row for <key> in <file>; <status> not recorded`, naming
  the last such row.
- Otherwise it is written as an upsert, byte for byte, replacing an older
  failure row.
- Returns whether it wrote.

### Partial rows

`store.write_partial(path, row, failed, ctx, source, second_key=None)`
(ADRs: a partly failed row keeps its good parts; a run never erases what it
didn't get):

- **Input.** `row` is the source's row, its fields in the source's order,
  each failed part null, and no `status` (or `ok` where the source's good
  rows carry one, as `pypi`'s do; `partial` replaces it in place). `failed`
  maps each failure, as the reason names it (`views request failed (HTTP
  503)`), to the fields of the part it left null; it names at least one.
- **Merge.** Store reads the stored row for the key (the last good or
  partial line) and fills each failed part whose every field that row
  knows: a good row's value, null included, or a partial row's non-null
  one. A part is filled whole or not at all. It logs `Kept the stored
  <fields> for <key> in <file>, which this run failed to get`.
- **Write.** Where every part is filled, the row is good and goes through
  `write_good`, so a changed value on a past day is a correction.
  Otherwise it gets `status` `partial` (`store.PARTIAL`) and a `reason`
  joining the failures still missing with `"; "`, after its other fields,
  and is written as an upsert.
- This run's values overwrite the stored ones, so a merged row can hold
  values from several runs of the day; its `captured_at` is the last
  write's.

### Merged rows

For a part that isn't read whole, such as `github_contributor_locations`'
people, each read on their own (ADR: a partly failed row keeps its good
parts):

- The source reads `store.stored(path, row, second_key=None)` and merges
  its part with it itself.
- `store.write_merged(path, row, missing, ctx, source, second_key=None)`
  writes the merged row, which has no `status` (or `ok`, as in
  `write_partial`): good through `write_good` where `missing` is empty,
  else `partial` with a `reason` joining `missing`.
- `write_partial` ends in `write_merged`, so both write the same way.

### Backfills

`store.fill(path, rows, ctx, second_key=None)` (ADR: past values a source
can rebuild are filled, never changed): `rows` are good rows, without a
`status`, for days before `ctx.date`, rebuilt from what the run fetched
(`github_issues`' totals from each issue's dates). For each row's date,
and its `second_key` where the source writes by one (`store.SECOND_KEY`,
the forum's), by the file's last line for it:

- **A good row** gets each field of `rows`' row that it lacks, inside the
  objects both have too. A field it has, null included, is left as it is,
  and a field that isn't an object on either side is never looked inside;
  but in a row marked `backfilled`, a null, which a rebuild couldn't
  tell, takes a value `rows`' row has. The line keeps its place and its
  `captured_at`, still its last key, and is re-serialized as upsert
  serializes.
- **A failure or partial row** is left as it is.
- **No row**: the row is added, with `backfilled: true`
  (`store.BACKFILLED`) and `captured_at` from `ctx.now`, before the first
  row of a later date (at the end, without one), so a file in date order
  stays in it.

Every other line is kept as upsert keeps it. A fill changes no value, so it
records no correction. It logs one line, `Backfilled <file>: N row(s)
filled, M added, <first date> to <last date>`; with nothing missing it
writes nothing, so a second run with the same rows leaves the file as it
is. A row with a `status`, or for the run date or later, is refused.

The days to rebuild: `store.first_night(path, ctx, where=None)` is the
date of the file's first row a night wrote before the run date (any row
not marked `backfilled`, good or not, that `where` keeps, each dotted
field equal to its value), or None. `store.rebuild_days(path, start, ctx,
where=None)` lists the days from `start`, the first its history can tell,
to the day before it: none without a first night or a `start`, so a new
file gets its history the night after its first row. Days that have a row
are listed too, as the fill adds only what they lack.
`store.lacks(row, field)` says whether a fill would add `field` to a
day's stored row (none, no such field, or a null in a `backfilled` row),
for a source that asks for more only when a day needs it.

### Recomputed history

`store.recompute(path, rows, ctx, source)` (ADR: derived history follows
today's rules): `rows` are good rows, without a `status`, for days before
`ctx.date`, recomputed under the run's rules from what it read
(`github_response_times`' counts from each issue's times, `github_issues`'
counts by kind from each issue's labels). For each row's
date, by the file's last line for it:

- **A good row** gets each value of `rows`' row; a field only the stored
  row has is kept, and a field only `rows`' row has is added before
  `captured_at`. Where a value changed (compared as JSON, an object's keys
  in any order), the line, in its place, gets `captured_at` from
  `ctx.now`, and store calls `ctx.record_correction` with the values that
  differ, as `write_good` does. Where none changed, the line is left as it
  is.
- **A failure or partial row** is replaced in its place by `rows`' row,
  with `captured_at` from `ctx.now`: a good row replacing one, no
  correction.
- **No row**: the row is added as `fill` adds it, marked `backfilled`.

The file is written once. One line logs what it did, `Recomputed <file>:
N corrected, M added, K failure row(s) replaced`, beside a `Corrected`
line for each correction; with nothing changed it writes nothing, so a
second run with the same rows leaves the file as it is. A row with a
`status`, or for the run date or later, is refused.

The days to recompute: `store.recompute_days(path, start, ctx)` lists the
days from `start`, the first its history can tell, to the day before
`ctx.date`: none without a `first_night` or a `start`, so a new file gets
its history the night after its first row, as with a fill.
