# discourse_forum

- **Reads:** a Discourse forum's own site statistics (members, topics, posts,
  likes, active members, and the forum's chat counts where it has chat), a row
  a day per forum.
- **Requests:** `<base_url>/about.json`, one request per forum, once a run.
- **Credentials:** none.
- **Writes:** one row a day per forum, to its default file unless the entry sets
  `file`: its `base_url`, then every integer of the answer's `about.stats` under
  Discourse's own name (`users_count`, `topics_count`, `posts_count`,
  `active_users_7_days`, `posts_last_day`, and so on). A forum that hides its
  statistics from visitors is a `parse_failed` row. A row is keyed by its date,
  so two forums in one config each set a `file`, since entries may not share one.
- **Config entry:** `sources.discourse_forum` is a list; each entry has
  `base_url`, the forum's address.

```json
{"base_url": "https://forum.example.com"}
```

How the source works: the docstring in `__init__.py`.
