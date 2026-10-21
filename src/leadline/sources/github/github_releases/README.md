# github_releases

- **Reads:** a repo's releases, with each asset's downloads.
- **Requests:** the repo's first 100 releases, in one call (a repo with more has
  its oldest left out).
- **Credentials:** `GITHUB_TOKEN`; the source is skipped while it is unset. The
  token needs to read the repo: any token reads a public one; a private one
  needs read access to it.
- **Writes:** one row a day per entry, to its default file unless the entry sets
  `file`: each release's tag, whether it is a prerelease or a draft, when it was
  published, and each asset's name, downloads and size.
- **Charts:** trends draws each release's downloads so far, worked out from
  the stored `releases` lists with no change to the rows: one line a release on
  one axis, none added to another (a person may download several versions),
  each over a translucent area of its own, the newest in front. Prereleases
  are left out of the chart, not drawn or added into anything. A band that
  holds several tags, a release line, is their sum. A release that leaves the
  list, or a list cut at 100, keeps its count, so no line falls; a release
  rises from 0 at its first listing; a tag's leading `v` is dropped, so tags
  band as versions ([the rule](../../../../../docs/TRENDS.md#trendsjson), "A
  list total"). A second chart, "Downloads since release", draws the same
  counts against days since each release's `published_at` (day 0 is that
  date), a line a release, only for releases published on or after the file's
  first row (an older release lacks its early days), the newest 5, and always
  by exact release whatever `bands` says, the axis ending a week past the
  newest release's age; a release listed after day 0 has no
  line until its first reading, and one listed before day 0 has that reading
  at day 0.
- **Config entry:** `sources.github_releases` is a list; each entry has `repo`
  (`owner/name`) and, optionally, `bands` ([see its values](#bands)) and
  `exclude` ([see it](#exclude)).

```json
{"repo": "example-org/example-app", "bands": "release", "exclude": ["1.0.1"]}
```

## `exclude`

`exclude`, optional: a list of release tags (text) to leave out of what is
shown. A leading `v` is ignored, so `"1.0.1"` and `"v1.0.1"` both name the
release `v1.0.1`.

- A named release is absent from the trends page and from every report that
  reads the file, the default summary included: it is not the latest release,
  gains no downloads in a report, is no line of the downloads chart and is not
  added into "older" or into any other count.
- Only what is shown changes. The source collects, and the file holds, every
  release as GitHub gives it, so removing a tag from `exclude` shows it again,
  with its whole history.
- A tag that names no release does nothing. Anything but a list of non-empty
  text is a config error.

## `bands`

`bands`, optional: how the trends page bands the downloads by version. A
version is `3.14.1`; its release line is `3.14`.

| Value | Bands |
| ----- | ----- |
| `line` | by release line: the 5 newest, the older ones added up as "older". The default |
| `release` | by exact release: the 5 newest releases, the older ones added up as "older" |

- Only the page changes; rows and the summary are the same. Any other value
  is a config error.

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
