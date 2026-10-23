# discourse_topics

- **Reads:** views, replies and likes of topics on a Discourse forum, a row per
  topic a day; each topic's days before its first row rebuilt back to its
  creation: replies and last post.
- **Requests:** `<base_url>/t/<id>.json`, one request per topic, once a run.
- **Credentials:** none.
- **Writes:** one row a day per topic, to its default file unless the entry sets
  `file`: its views, replies, likes, last post, `participants`, `closed`,
  `solved` (whether it has an accepted answer) and `tags`; each of the last four
  is null where the answer lacks it. Two forums in one config each set a `file`, since entries may not
  share one.
- **Config entry:** `sources.discourse_topics` is a list; each entry has
  `base_url`, the forum's address, and `topics`, each an `id` and a `label`.

```json
{"base_url": "https://forum.example.com", "topics": [{"id": 1001, "label": "example-topic"}]}
```

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
