# goatcounter

- **Reads:** a site's page views by day, backfilled across the week, and its top
  referrers, from GoatCounter's API.
- **Requests:** `<site>.goatcounter.com/api/v0/stats/hits`, `stats/total` and a
  page's referrers, for the week before the run.
- **Credentials:** `GOATCOUNTER_TOKEN`; the source is skipped while it is unset.
  The token needs the permission to read statistics.
- **Writes:** one row a day per site, to its default file unless the entry sets
  `file`. Yesterday's row carries the referrers, `top_pages` (the ten pages with
  the most views in the week, each `{path, title, count}`) and totals; the other
  days' rows hold an empty `top_pages`.
- **Config entry:** `sources.goatcounter` is a list; each entry has `site`, the
  site's code, as in `<site>.goatcounter.com`.

```json
{"site": "example"}
```

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
