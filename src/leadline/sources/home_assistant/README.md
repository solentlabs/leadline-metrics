# home_assistant

The platform group for the Home Assistant sources: `ha_integration` and
`ha_platform`.

- `__init__.py`: sets `GROUP = True`, so discovery looks inside this folder
  for sources.
- `tests/builders.py`: the response builders both sources' tests use.

A source here imports the core, never the other source.
