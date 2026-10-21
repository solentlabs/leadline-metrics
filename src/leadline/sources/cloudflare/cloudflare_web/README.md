# cloudflare_web

- **Reads:** a site's page views and visits from Cloudflare Web Analytics, each
  row with the site tag it was read under; the last 30 days healed and
  corrected.
- **Requests:** Cloudflare's GraphQL Web Analytics query, for the 30 complete
  days before the run and the week's top referrers. `check` lists the account's
  Web Analytics sites.
- **Credentials:** `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`; the
  source is skipped while either is unset. The token needs two account
  permissions: Account Analytics: Read, for the collection, and Account
  Settings: Read, for `check()`, which lists the account's Web Analytics sites
  (`rum/site_info/list`).
- **Writes:** one row a day per entry, to its default file unless the entry sets
  `file`.
- **Config entry:** `sources.cloudflare_web` is a list; each entry has `label`,
  and `site_tag`, the site's tag (not the beacon token in its pages).

```json
{"label": "example", "site_tag": "0123456789abcdef"}
```

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
