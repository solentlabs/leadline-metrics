# github_tree_counts

- **Reads:** the paths in a repo's git tree that match a pattern: files, items,
  groups and items per group; optionally the files counted by a value read from
  each one's text, each blob read once; the days before the file's first row
  rebuilt from git history, back to the first tree that matches, and with the
  counts by value, every day through yesterday.
- **Requests:** the repo's whole tree at its `ref` in one call and, with
  `content`, each matching file's blob once, ever.
- **Credentials:** `GITHUB_TOKEN`; the source is skipped while it is unset. The
  token needs to read the repo: any token reads a public one; a private one
  needs read access to it.
- **Writes:** one row a day per entry, to its default file unless the entry sets
  `file`: the counts under the entry's `keys`.
- **Config entry:** `sources.github_tree_counts` is a list; each entry has
  `repo` (`owner/name`); `pattern`, a regex searched in each path, so `^`
  anchors it (group 1 is the group, group 2 the item, other groups are ignored),
  or a list of them for files that have lived in several places ([see
  patterns across a move](#patterns-across-a-move));
  `ref` (default `HEAD`); `keys`, the row keys for `files`, `items`, `groups`
  and `items_per_group` (defaults: those names); `content` (optional, [see its
  keys](#content)).

```json
{"repo": "example-org/example-app", "pattern": "^catalog/([^/]+)/([^/]+)/"}
```

## `content`

`content`, optional: counts the matching files by a value read from each
one's text (ADR: a tree's files are counted by what they say).

| Key | Meaning |
| --- | ------- |
| `pattern` | a regex with one group, run over every line of a file's text (`^status:\s*(\w+)`); the group is the value |
| `key` | the row key the counts are written under, an object of file counts by value |
| `map` | optional, an object of values to the names they count as (`{"verified": "confirmed"}`); [see a value written differently](#a-value-written-differently) |

- A file the pattern doesn't match, or captures nothing from, counts as
  `unknown`; so does a path that matches the entry's `pattern` but isn't a
  file. The counts add up to the row's file count.
- Each file's text is read once, by its blob: a night reads only the files
  no earlier row has read, usually none. The row lists the blobs it read
  under the key and `_blobs` (`status_blobs` for `status`), which a reader
  can ignore.
- Past days get the counts from the tree of their last commit: a night asks
  for at most 500 trees and reads no more than a thousand blobs, so a long
  history takes more than one night.
- A blob that can't be read leaves the counts null and the row `partial`;
  the next night tries again.
- The key must be one no count or row key takes, nor its `_blobs` key.
  Without `content`, nothing is read.
- Trends titles the chart with the key, underscores as spaces and the first
  letter capitalized (`catalog_by_status` reads "Catalog by status").

## A value written differently

The `content` pattern is one for every file read, so a line that sat indented
in older files needs a pattern that allows it (`^[ \t]*status:\s*(\w+)`). A
value renamed over time counts as one with `map`, each captured value matched
exactly:

```json
{"content": {"pattern": "^status:\\s*(\\w+)", "key": "by_status", "map": {"verified": "confirmed"}}}
```

- The counts and the blobs listed under `<key>_blobs` use the mapped name; a
  value not in `map` is unchanged, and `unknown` is not mapped.
- A name is mapped once: a mapped-to name that is itself mapped (a chain or a
  cycle) is a config problem.
- Rows already written keep the names they were written with, as every past
  value does; the blobs they list under an old name are read under the mapped
  one, so adding a `map` reads no blob again. The rebuild writes the mapped
  names, and a night's row, whenever it is written, does too. To re-count
  rows already in the file, set the key to `null` in those marked `backfilled`
  (the next night's rebuild fills it); removing them rebuilds only days after
  the file's first row.

## Patterns across a move

When the files have lived in more than one place, `pattern` is a list of
patterns, the newest place first. Each is a regex searched in every path, with
the same groups (1 the group, 2 the item). Each day, a (group, item) pair
is counted from the first pattern in the list that matches it: the paths of
that pattern are its files, and no other pattern's. A pair that sits in two
places counts once, and the counts stay continuous across the move.

```json
{"pattern": [
  "^lib/items/([^/]+)/([^/]+)/(item[^/]*\\.yaml)$",
  "^items/([^/]+)/([^/]+)/(item[^/]*\\.yaml)$",
  {"pattern": "^src/([^/_][^/]*)/([^/_][^/]*)\\.py$", "content": false}
]}
```

A list item is a regex or an object with the `pattern` and `content`. With
`content` set to `false`, the pattern's files are counted but their text is
never read, so they count as `unknown` under the entry's [`content`](#content);
that is for files that carry no such line (source files, whose text may still
contain it). `content` may instead be an object with a `pattern` (one group)
and a `map`, each used in place of the entry's, for files that say the value
another way: the counts go under the entry's key, with the same names. A
string, or a list of one, counts as before.

```json
{"pattern": "^src/([^/_]+)/([^/_]+)\\.py$",
 "content": {"pattern": "^[ \\t]*status\\s*=\\s*Status\\.(\\w+)", "map": {"VERIFIED": "confirmed"}}}
```

- **Priority is per pair, not per day.** A pair in the first pattern's place
  and also in a later one's counts only the first place's paths, so `files`
  counts the winner's paths and `items`, `groups` and `items_per_group` count
  the pair once.
- **A place that holds a copy of another** (a generated or synced folder) is
  left out of the patterns, or its copies count as pairs of their own.
- **The history reaches back to the first tree any pattern matches,** under
  the same rule.
- **The history reaches back only from a file with no rebuilt rows.** The
  rebuild fills the days before the file's first night's row, and, once it has
  found where the history starts, never looks before that start. Widening
  `pattern` on a file that already holds rebuilt rows leaves its rows as they
  are and adds none before the first: to extend it, remove the rebuilt rows
  (those marked `backfilled`), keeping the nights' rows, and the next nights
  rebuild the whole history.
- A pattern that matches nothing in today's tree is fine (an old place is
  gone); if none does, `leadline check` warns.

## Known limits

- **Both groups of each pattern must always match.** A match whose group is
  optional and empty gives `None` beside text, the sort fails, and the run
  records a crash (critical, exit 1) with no row for the entry. `validate`
  can't see optional groups, so write a pattern whose two groups always
  match.
- **A blob that can't be read keeps the counts null every night.** With
  `content`, the row stays `partial` and a 404 on the blob is critical each
  night, until it reads.
- **Past rows can differ by one.** A past row's `files` comes from the
  night's tree, its counts by content from the tree of the day's last
  commit, so a commit after that night's run shows as a difference of one
  between them.

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
