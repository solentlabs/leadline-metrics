# github_discussions

- **Reads:** a repo's discussions: totals, community and maintainer, answers,
  categories and the most recent ten; the days before the file's first row
  rebuilt back to the first discussion, comments included, where the queries
  read them all.
- **Requests:** GitHub's GraphQL API, once a run for the 100 most recently
  updated discussions (more only to rebuild past days).
- **Credentials:** `GITHUB_TOKEN`; the source is skipped while it is unset. The
  token needs to read the repo: any token reads a public one; a private one
  needs read access to it.
- **Writes:** one row a day per entry, to its default file unless the entry sets
  `file`: the total, the discussions the community and the maintainer opened,
  answers and their comments, the count per category.
- **Config entry:** `sources.github_discussions` is a list; each entry has
  `repo` (`owner/name`) and `maintainer`, the login counted apart from the
  community (required).

```json
{"repo": "example-org/example-app", "maintainer": "example-maintainer"}
```

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
