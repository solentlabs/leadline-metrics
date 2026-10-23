# Reports

The `reports` setting: the blocks leadline writes into Markdown files, and
the language that shapes them (ADR: reports are config-driven marked
blocks). A config without it gets [the default summary](#the-default-summary).

`reports.py` writes the blocks after
[health](ARCHITECTURE.md#health) has written the run's row, so a block can
show it. It reads only the output files, through `store`, and reads each
file without the items its source's configuration hides from it (the
[`exclude`](../src/leadline/sources/github/github_releases/README.md#exclude)
of a `github_releases` entry hides releases): whatever block reads that file,
the excluded items are not there, and the file is not changed.
`reports.validate` checks the list with the sources' configs, before
anything runs ([The run](ARCHITECTURE.md#steps)). What a report needs
beyond what is here is a new named aggregate, generic in code, never code
for one project.

## Blocks

Each entry of `reports` is one block:

| Key | Meaning |
| --- | ------- |
| `file` | a `*.md` path, relative to `out_dir`, so `--out` moves the reports with the rows (`../SUMMARY.md` is beside the metrics folder); created if missing, but its folder must exist |
| `block` | the block's name (letters, digits, `-`, `_`), once per file |
| `place` | `top` or `bottom`: where a block the file doesn't hold yet goes |
| `heading` | optional: the block opens with `## <heading>` and a blank line |
| `table` or `lines` | what it shows ([Tables](#tables), [Lines](#lines)) |

- **Markers.** leadline owns the lines from `<!-- leadline:start NAME -->`
  to `<!-- leadline:end NAME -->`, each marker a line of its own, and never
  changes a byte outside them.
- **Placement.** A new block goes at its `place`, with a blank line between
  it and the text beside it; new blocks keep config order. After that the
  block is rewritten where it is, wherever it has been moved.
- **Empty blocks.** A block with nothing to show (a table with no good row,
  or lines each left out) is only its markers, with no heading.
- **Files.** A file is read once and written once, atomically
  (`store.write_text`). The blocks take the file's line ends (`\r\n` where
  its first line has them).

## Each block on its own

A block that can't be written fails alone; the others are written.

- **Errors.** A block fails when:
  - its start or end marker is missing or there twice, the end is before
    the start, or another block's marker is between them (leadline never
    guesses where such a block ends, which could rewrite the user's text);
  - its file can't be read as UTF-8;
  - an exception is raised while building it (a bug).
- **Outcome.** The block is left as it is, and the failure is logged and
  recorded critical (`ctx.record_critical("reports", file, "block NAME:
  ...")`), so the run exits 1: waiting mends nothing here.
  `health.jsonl`, written before the reports, doesn't list it; the log and
  the exit status carry it.

## Reading rows

- A block reads row files in the output folder: good rows only (a failure
  or partial row is skipped; ADR: a failure is recorded as a failure), one
  per date (the last line for it), oldest first.
- A `field` is a dotted path into a row (`issues.open`).
- `format`, where given, is a Python format spec without a sign (`.2f`)
  for each number a default text shows. A change always shows its sign.
- No value breaks a line: its line breaks become spaces. A value cannot open
  a leadline marker either: its `<!-- leadline:` is shown as `&lt;!--
  leadline:`. A character no file can hold (a lone surrogate) is shown as
  `\uFFFD`.

## Templates

A `text` is a template: text with fields in braces.

- A field is a name or a dotted path into one (`{value}`, `{change:+}`,
  `{row.repo.forks}`), with an optional Python format spec after a colon.
  `{{` and `}}` are braces.
- A template shows only where every field it names has a value. A field
  with none (missing or null) leaves out the piece, item or cell whose
  template names it; there is never a placeholder.
- Where a piece says so, a value is cleaned before a template shows it:
  - `replace`: `{"<field>": [["<pattern>", "<replacement>"], ...]}`, each a
    Python regular expression substitution on the value as text, in turn
    (`"date": [["^\\d{4}-", ""]]` shows `09-23`; `"has_har": [["^True$",
    "HAR"]]`).
  - `shorten`: `{"<field>": N}`: a value longer than N characters is cut to
    N-1, trailing spaces dropped, and ends with `…`.

  Both apply in the piece that gives them and in every piece inside it.

## Conditions

`when` is a list of `[field, op, value]`; the piece shows only where each
holds.

- `op` is one of `<`, `<=`, `=`, `!=`, `>=`, `>`.
- `value` is a JSON number, string, `true`, `false` or `null`.
- A field with no value fails. A list or an object compares by how many
  items it has. Only numbers are ordered.
- Examples: `["count", ">", 2]`, `["change", "!=", 0]`,
  `["row.queue.items", ">", 1]`.

## Tables

`table` shows a file's rows, a table row per day or per week.

| Key | Meaning |
| --- | ------- |
| `from` | the file whose rows it shows |
| `group` | `day` (default), a table row a day; or `week`, a table row a week (weeks start on Monday) dated by the week's last row, then `(wk)` after a space |
| `recent_weeks` | with `week` only: the newest N weeks shown a row a day; default 0 |
| `columns` | the columns, below |

Each column has a `title`, a `field`, and optionally:

- `from`: another file, whose row of the same date the field is read from.
- `per` (`{"field", "from"}`, `from` optional): the value divided by that
  one; none where it is missing or 0.
- `format`, and `bold` (the cell's aggregate in bold).
- `aggregate`, over the rows a table row stands for:
  - `last` (default): the last row's value;
  - `change`: the last value less the previous table row's, signed (`+3`);
    the first table row's is `+0`;
  - `range`: the lowest and the highest, `lo-hi`, or the value when they
    are equal;
  - `versions`, of an object of counts by version (`{"1.2.0": 40}`): each
    count, oldest version first, `**v1.2.0:** 40`. Releases and the newest
    prerelease each stand alone; older prereleases are added up by base
    version and channel (`1.3.0-beta`) unless alone.

A column whose `field` is `date`, with no `from`, shows the table row's
date. A cell shows `-` where there is no value.

A column can instead combine several aggregates of its values: a `text`, or
`parts` ([Lines](#lines)), with `when`, `replace` and `shorten`, over the
fields `last`, `change`, `low`, `high` and `spread` (high less low) where
the values have them, and `text`, what the cell's `aggregate` shows. A cell
whose text doesn't show is `-`:

```json
{"title": "Installs", "field": "total", "parts": [
  {"text": "**{last}** ({change:+})"},
  {"text": " · range {low}-{high}", "when": [["spread", ">", 0]]}]}
```

## Lines

Each entry of `lines` is a piece, which gives one line, a line per item, or
nothing. A piece:

- **Reads a file** with `from`, `field` and a [kind](#kinds). `where`
  (`{"label": "main"}`) keeps only the rows with those values, for a file
  with several rows a day. Its kind gives fields from the file's newest
  good row, `row` (that row) among them.
- **Marks old data.** Where the file's newer rows are failure rows, the
  piece's text ends with `as_of`, a template of `{date}`, the date of the
  row shown (default `" (as of {date})"`; `""` for none).
- **Inherits fields.** A piece that reads no file takes its fields from the
  pieces it is in; a line that reads none is plain text. A piece that does
  read a file adds its kind's fields over them, so one the kind doesn't give
  stays the outer piece's: a part that reads a file and has no `compare` of
  its own shows the outer piece's `change` and `then` in a template that
  names them.
- **Shows** `text`; or with `label`, `- <label>:` and a space before its
  kind's text (as in the examples here); or its `parts`; or its kind's
  text.
- **Combines parts.** `parts` is a list of pieces, each filled from its own
  fields and those of the pieces it is in. The ones that show are joined
  with `join` (default nothing) into the field `parts`, and the piece shows
  only where one does. A part that reads a file adds that file's fields;
  one that doesn't only shapes the text around it:

  ```json
  {"from": "issues.jsonl", "field": "repo.stars", "compare": "7d", "parts": [
    {"text": "- Stars {value}"}, {"text": " ({change:+} this week)"},
    {"text": " · forks {row.repo.forks}"}]}
  ```

## Kinds

What a piece reads from its file: the fields it gives, and its default
text.

- **`field` alone**: `value` and `date` (the row's). With `compare`
  (`yesterday` or `<N>d`), also `then`, the value in the newest row that
  many days or more before the newest, and `change`, the value less it.
  Text: `101 (+1 vs yesterday)`, `101 (+5 vs 7d ago)`, the change left off
  where there is no such row. With `complement: true`, each value is 100
  less the field's, to its own decimals, and none where the field isn't a
  number: a percent read the other way round, such as a rank's
  `percentile` (lower is better) as the share ranked below it. Only a
  field alone takes it.
- **`series: N`**: the newest N values, oldest first; with `every: D`, the
  value D days apart back from the newest row, as it stood then. Fields:
  `values` (`93 94 101`, the text) and `count`, how many there are.
- **`aggregate`**, a named aggregate:
  - `peak`: `value`, the highest; `date`, the first day of it; `age`, its
    days before the newest row. Text: `101 on 2026-09-24`.
  - `sum`, with `count: N`: `total`, of the newest N rows, through `date`,
    the newest row's; and once the N rows before them are all there,
    `prior`, their total, and `change`, `total` less `prior`. Nothing
    until there are N rows. A history shorter than twice N has no prior: both
    fields are empty and the text leaves it off. Text: `302 through
    2026-09-23 (prior 621)`.
  - `versions`: the version breakdown, as in a table.
  - `latest_release`, of a list of releases (`github_releases`'
    `releases`): the newest published release that is neither a draft nor
    a prerelease; or, with `tag` (a template of the fields of the pieces it
    is in), the first release whose tag, as a template shows it after
    `replace`, is that. Fields: `tag`, `downloads` (its assets'), and where
    published, `date` and `age`, its days at the row's date. Text:
    `v1.1.0, published 2026-09-10 (14 days ago), 455 downloads`.
  - The [items](#items) aggregates.

## Items

Some aggregates give items, each filled from its own fields by `item`, a
template or a piece.

- Without `join`, each item is a line, through the piece's text. With
  `join`, the items are joined into one line, in the field `items`. Inside
  another piece, `items` (and `versions` with `pick`) needs a `join`.
- `match` (`{"<field>": "<pattern>"}`) keeps the items whose fields match
  those regular expressions; `skip` leaves out the first N of those;
  `count` shows at most N after that. The field `found` is how many
  matched. The text is the items.

The items aggregates:

- **`items`**: the objects of a list in the newest row, each with its own
  fields; `sort` names a field whose largest values come first. The
  [critical block](#the-critical-block) is one.
- **`counts`**, of an object of counts (`{"os": 40, "core": 12}`): each
  key and its count, the largest first. Item fields `key`, `count` and
  `share` (its percent of all the counts, rounded); fields `total`, of the
  counts matched, and `rest`, of those past `count`. Default item `{key}
  {count}`, joined ` · `: `os 40 · core 12`.
  With `days: N` (a whole number, 1 or more), the objects of the rows in the
  N days up to the newest row's are added up by key, a row without an object
  skipped: the week's events, not the day's.
- **`release_downloads`**, with `compare`: each release's downloads gained
  since the row `compare` back, the most first, only those that gained.
  Item fields `tag` and `gain`. Default item `{tag} +{gain}`, joined ` · `:
  `v1.1.0 +415 · v1.0.0 +9`.
- **`versions` with `pick`**, of an object of counts by version, newest
  first. `pick` is `releases` (versions without a prerelease tag),
  `newest_prereleases` (the prereleases of the newest prerelease's
  version) or `older_prereleases` (the others).
  - Item fields: `version`, `base`, `tag` (the prerelease tag, `beta.5`),
    `count`, `share` (its percent of all the counts, rounded); with
    `trail: N`, `trail`, its count in each of the newest N rows, 0 where a
    row doesn't have it (`24 45 56 60`), and `trail_change`, the last less
    the first.
  - Fields: `total`, of the counts matched; `rest`, of those past `count`;
    and for `newest_prereleases`, `base`.

  ```json
  {"from": "installs.jsonl", "field": "versions", "aggregate": "versions",
   "pick": "releases", "skip": 1, "count": 3, "join": " · ",
   "item": "{version} {count}",
   "parts": [{"text": "- Earlier: {items}"}, {"text": "older {rest}", "when": [["rest", ">", 0]]}]}
  ```

## The critical block

The run's critical list at the top of SUMMARY.md (ADR: critical sources
surface at the top of SUMMARY.md). It is an ordinary block, read from the
health row this run has just written. On a night with nothing critical it
is only its markers:

```json
{"file": "../SUMMARY.md", "block": "critical", "place": "top", "heading": "Critical",
 "lines": [{"label": "Critical", "from": "health.jsonl", "field": "critical",
            "aggregate": "items", "item": "{source}: {reason}"}]}
```

## The default summary

What a run, or `leadline report`, writes when the config has no
`reports` (ADR: the default
summary is config built from each source's summary()). `"reports": []`
writes nothing, and a config with its own blocks writes only those; a
`SUMMARY.md` written before is then left as it is.

It is one block, `summary`, at the top of `SUMMARY.md` in the output
folder, headed `Summary`:

- **Health**, from the run's row in `health.jsonl`: the run date; `All
  sources current` when nothing is stale or failed; each failed file, its
  status, since when and, past one night, how many nights; each stale file
  and its last row; each critical entry; each check finding at `warn` or `error`.
- **One section per source entry**, in config order: `### <source>: <id>`,
  then a line for each field the source's `summary()` names (`ha_platform`,
  Home Assistant as a whole, has none), then any lines the source adds
  (`github_releases`: the latest release and downloads in 7d). A section
  shows once its file has a good row; where the file's newer rows are
  failure rows, its heading
  ends with the date of the row shown. Each field shows by its kind:

  | Kind | Shows |
  | ---- | ----- |
  | `snapshot` | the newest value, then its change since the newest row at least 7, and at least 30, days older, where there is one: `- Stars: 41 · +5 in 7d · +12 in 30d` |
  | `change` | only those changes: `- Issues opened: +4 in 7d` |
  | `daily` | the total of the newest 7 and 30 rows, each with its change since the rows before, once they are all there: `- Views: 162 in 7d (+26) · 610 in 30d` |
  | `value` | the newest value: `- Version: 0.2.0` |
  | `breakdown` | an object of counts, the newest: its 5 largest, then the rest added up (a `counts` line): `- Regions: DE 40 · US 12 · other 3` |

  A snapshot, change or value the source marks `complement` shows 100 less
  the field, and its changes are that value's (`- Percentile rank (HA
  custom integrations): 73.4 · +0.5 in 7d`, from a `percentile` of 26.6).

`leadline default-reports --config P` prints the default summary as a
`reports` setting, to paste into the config and change.

Every run, and every `leadline report`, also writes the same fields' history,
whatever this setting says: `trends.json` and a page that charts it, `trends.html`
([TRENDS.md](TRENDS.md)).
