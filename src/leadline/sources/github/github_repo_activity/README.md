# github_repo_activity

- **Reads:** one public repo's week: commits, authors, fixes and redesigns,
  issues, contributors, the latest release and the code size.
- **Requests:** the repository, its commits and issues since a week before the
  run's date, its top contributors, its latest releases and its languages.
- **Credentials:** `GITHUB_TOKEN`; the source is skipped while it is unset. The
  token needs to read the repo: any token reads a public one; a private one
  needs read access to it.
- **Writes:** one row a day per entry, to its default file unless the entry sets
  `file`: the week's counts under the entry's label.
- **Config entry:** `sources.github_repo_activity` is a list; each entry has
  `repo` (`owner/name`); `label` (default: the repo's name); `fix_pattern` and
  `redesign_pattern`, regexes matched ignoring case against each commit's first
  line (the defaults are the script's).

```json
{"repo": "other-org/other-app", "label": "other-app"}
```

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
