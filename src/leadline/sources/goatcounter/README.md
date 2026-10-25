# goatcounter

- **Reads:** a site's page views by day, backfilled across the week, and its top
  referrers, from GoatCounter's API.
- **Requests:** `<site>.goatcounter.com/api/v0/stats/hits`, `stats/total`, a
  page's referrers and `stats/browsers`, `stats/systems` and `stats/locations`,
  for the week before the run.
- **Credentials:** `GOATCOUNTER_TOKEN`; the source is skipped while it is unset.
  The token needs the permission to read statistics.
- **Summary:** the week's page views, and the newest row's browsers, systems and
  locations.
- **Writes:** one row a day per site, to its default file unless the entry sets
  `file`. Yesterday's row carries the referrers, `top_pages` (the ten pages with
  the most views in the week, each `{path, title, count}`), `top_browsers`,
  `top_systems` and `top_locations` (the ten names with the most visitors, each
  `{name, count}`) and totals; the other days' rows hold them empty.
- **Config entry:** `sources.goatcounter` is a list; each entry has `site`, the
  site's code, as in `<site>.goatcounter.com`.

```json
{"site": "example"}
```

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
