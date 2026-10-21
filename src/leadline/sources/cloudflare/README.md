# cloudflare

The platform group for the Cloudflare sources: `cloudflare_web`,
`cloudflare_analytics_engine` and `cloudflare_zone_traffic`.

- `__init__.py`: sets `GROUP = True`, so discovery looks inside this folder
  for sources.
- `tests/builders.py`: the response builders the sources' tests use.

A source here imports the core, never the other source.
