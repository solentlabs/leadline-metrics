# github_traffic

- **Reads:** a repo's 14-day views, clones, top referrers and top paths, as one snapshot.
- **Requests:** the repo's traffic views, clones, popular referrers and popular
  paths, one call each. Traffic needs push access to the repo.
- **Credentials:** `GITHUB_TOKEN`; the source is skipped while it is unset. The
  token needs push access to the repo, for its traffic.
- **Writes:** one row a day per entry, to its default file unless the entry sets
  `file`: the views' and clones' totals and unique visitors, `window_end`, and
  the top five referrers, and `top_paths`, the ten most viewed paths, each
  `{path, title, count, uniques}`. Referrers or paths that fail leave their
  field null in a `partial` row.
- **Config entry:** `sources.github_traffic` is a list; each entry has `repo`
  (`owner/name`).

```json
{"repo": "example-org/example-app"}
```

On the [trends page](../../../../../docs/TRENDS.md#the-pages) its charts are
the "Rolling 14 days" row of the Traffic card, above the
[`github_traffic_daily`](../github_traffic_daily/README.md) row of days: views
in blue, unique visitors in purple (only here) and clones in green.

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
