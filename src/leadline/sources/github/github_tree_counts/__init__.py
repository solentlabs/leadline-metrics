"""github_tree_counts: the paths in a repo's git tree that match a pattern: files, items, groups, items per group.

Each entry reads the repo's whole tree at its `ref` in one call and writes one row for the run's date,
as the script's collect_catalog_size does: the entry's `pattern` (a regex, searched in each path, not
anchored unless the pattern says so) picks the files; its group 1 names a group and group 2 an item
within it (further groups are ignored). `pattern` may be a list, newest location first, for files that have
lived in several places: each (group, item) pair is counted from the first pattern that matches it, its paths only,
and a list item `{"pattern": ..., "content": false}` is one whose files `content` doesn't read (they count as
`unknown`), or `"content": {"pattern": ..., "map": ...}` one whose files it reads by its own pattern and map.
The row holds, under the entry's `keys`, the distinct items, the matching files, the groups and each group's
items, and whether GitHub truncated the tree, which makes the counts floors.

With a `content` setting, a regex and a key, it also reads the text of each matching file (its blob, in the tree's
list, by the blob's SHA) and counts the files by the regex's group 1, as an object under the key (`unknown` for a file
the regex doesn't match, or that isn't a file); the counts add up to the files. A blob is read once, ever: its SHA names
its text, so each row lists the blobs it was the first to read, under the key `<key>_blobs`, and a night starts from
every blob the file's rows list and reads only the others (ADR: a tree's files are counted by what they say). A blob
that can't be read leaves the part null, a partial row (ADR: a partly failed row keeps its good parts).

After a good row it writes a row for each day before the file's first row a night wrote
(store.first_night), from the first day whose tree matches any pattern, each counted in the tree of the
last commit on the ref by the end of that day (in the run's offset, by its committer date): one listing
of the ref's commits, then one tree for each such commit a missing day needs (store.fill; ADR: past
values a source can rebuild are filled, never changed). A failed request stops it for the night,
keeping the days counted so far; the next night starts after the rows the fill added, which mark the
start, and asks for no tree a day already has. Where leadline differs, by decision:

- A failed fetch is a `fetch_failed` row, and an answer it can't use (not JSON, no tree, a tree entry
  the script's code stops on) is a `parse_failed` row, where the script writes nothing, or zeros for
  an answer without a tree (ADR: a failure is recorded as a failure).
- A failure never replaces a good row (docs/ARCHITECTURE_DECISIONS.md).
- An error waiting can't fix (401, 403 or 404 that isn't a rate limit) is critical at once.
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers; at run time `lazy` finds these on first use
    from leadline.sources.github.github_tree_counts.source import ENV, LAGGING, check, collect
    from leadline.sources.github.github_tree_counts.spec import DEFAULT_FILE, NAME, files, summary, validate

__getattr__ = lazy(__name__)

__all__ = ["DEFAULT_FILE", "ENV", "LAGGING", "NAME", "check", "collect", "files", "summary", "validate"]
