# pypi

- **Reads:** each package's downloads by day from pypistats, backfilled from its
  history, and its version and required Python from PyPI; a retry pass asks
  again for a package whose history request failed.
- **Requests:** `pypistats.org/api/packages/{package}/overall` and
  `pypi.org/pypi/{package}/json`, a package at a time, `sleep_s` apart. Only the
  scheduled run asks pypistats; `check` asks PyPI alone.
- **Credentials:** none.
- **Writes:** one row a day per package, to its default file unless the config
  sets `file`: the day's, week's and month's downloads without mirrors, then the
  same with mirrors (null where pypistats gave no with-mirrors rows), the
  latest version and the required Python.
- **Config entry:** `sources.pypi` is one object, not a list. `packages` is the
  PyPI names, in order (each night starts one place further along); `sleep_s` is
  the seconds between pypistats requests; `retry_wait_s` is the seconds the
  retry pass waits first.

```json
{"packages": ["example-package", "example-lib"], "sleep_s": 60, "retry_wait_s": 240}
```

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
