# github_traffic_daily

- **Reads:** a repo's views and clones, a row per day, backfilled across the
  14-day window.
- **Requests:** the repo's traffic views and clones, one call each. Traffic
  needs push access to the repo.
- **Credentials:** `GITHUB_TOKEN`; the source is skipped while it is unset. The
  token needs push access to the repo, for its traffic.
- **Writes:** a row per day, to its default file unless the entry sets `file`: a
  row for each day of the window the file has no good row for, and for yesterday
  again; never today.
- **Config entry:** `sources.github_traffic_daily` is a list; each entry has
  `repo` (`owner/name`).

```json
{"repo": "example-org/example-app"}
```

On the [trends page](../../../../../docs/TRENDS.md#the-pages) its charts are
the "By day" row of the Traffic card, under the
[`github_traffic`](../github_traffic/README.md) row of 14-day totals: views
in blue and clones in green as there, each with a faint line of its 7-day
average.

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
