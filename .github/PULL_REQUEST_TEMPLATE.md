# What this changes, and why

<!-- Say it in a few lines, then tick what applies below. -->

## Checklist

- [ ] Written test first: a test that failed before the change and passes
  now
- [ ] `make check` passes (and `make oracle` where the change touches what
  it covers)
- [ ] Docs and code changed together; `make docs` leaves the tree unchanged
- [ ] `cspell` and `markdownlint-cli2` run clean on the changed files
- [ ] A new source is one folder, its config entry and `make docs`, nothing
  else ([the guide](../docs/ADDING_A_SOURCE.md))
- [ ] No token, private name or visitor's address in the change, tests
  included
- [ ] A line in `CHANGELOG.md` for a change a user would notice
