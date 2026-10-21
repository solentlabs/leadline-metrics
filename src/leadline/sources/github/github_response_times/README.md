# github_response_times

- **Reads:** how long community issues wait for an answer: the maintainer's
  first reply or the issue's closing, whichever comes first; every past day
  recomputed back to the first community issue, under today's rules.
- **Requests:** every issue of the repo, oldest first, 100 a page by cursor,
  each with its first 30 comments, over GraphQL.
- **Credentials:** `GITHUB_TOKEN`; the source is skipped while it is unset. The
  token needs to read the repo: any token reads a public one; a private one
  needs read access to it.
- **Writes:** one row a day per entry, to its default file unless the entry sets
  `file`: the median, mean, 90th percentile and fastest hours to the answer, and
  the share answered within 1, 24 and 72 hours.
- **Charts:** trends draws the median, mean, 90th percentile and fastest hours
  as lines of one chart; the default summary shows the median and the count
  not answered.
- **Config entry:** `sources.github_response_times` is a list; each entry has
  `repo` (`owner/name`) and `maintainer`, the login counted apart from the
  community (required).

```json
{"repo": "example-org/example-app", "maintainer": "example-maintainer"}
```

## Known limits

- **A reopened issue's close time is unchecked.** An issue is answered at
  its GraphQL `closedAt`. If GitHub keeps the first close there after a
  reopen, a reopened issue counts as answered at that close.
- **A late correction is routine.** The nightly run reads at 23:30 and the
  recompute counts each day to midnight, so activity in the last half hour
  shows as a correction to yesterday's row the next night.

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
