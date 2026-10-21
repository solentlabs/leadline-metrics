# ha_platform

- **Reads:** Home Assistant's platform-wide analytics: active installations, how
  many report integrations, add-ons and statistics, installation types and the
  top countries. It is the denominator for `ha_integration`'s installs.
- **Requests:** `data.json` from analytics.home-assistant.io, once a run.
- **Credentials:** none.
- **Writes:** one row a day, to its default file unless the entry sets `file`.
- **Config entry:** `sources.ha_platform` is one object, not a list, and has
  nothing to set but `file`.

```json
{}
```

It reports on Home Assistant as a whole, not your projects, so it has no
section in the default summary or trends.

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
