# github_repo_stats

- **Reads:** a repo's stars, forks, watchers and open issues, with its 14-day
  traffic totals.
- **Requests:** the repository, then its traffic views and clones, one call
  each. Traffic needs push access to the repo.
- **Credentials:** `GITHUB_TOKEN`; the source is skipped while it is unset. The
  token needs push access to the repo, for its traffic.
- **Writes:** one row a day per entry, to its default file unless the entry sets
  `file`: the counts, the repo's `network_count`, `size_kb` and `pushed_at`, and
  the views' and clones' 14-day totals and unique visitors.
- **Config entry:** `sources.github_repo_stats` is a list; each entry has `repo`
  (`owner/name`).

```json
{"repo": "example-org/example-app"}
```

On the [trends page](../../../../../docs/TRENDS.md#the-pages) its charts are
the "Stars and forks" row of the Traffic card, placed among the card's rows
by the order the project lists the sections; its 14-day views are in the
summary only, as the card's other rows chart them.

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
