"""Response builders, one module per API or platform (docs/TESTING.md, "Scenarios").

Every value is made up. Each builder returns a response body as the API gives it, parsed: the same keys at
every level and values of the same types (tests/oracle/test_builder_shapes.py holds each to the real
responses in the private capture). Arguments are what a scenario steers; everything else has a plain default.
Builders that make many items take a seeded `random.Random`, so a scenario's data is the same on every run
and every Python. `common` holds what the modules share; a module imports it and no other builder module.
"""
