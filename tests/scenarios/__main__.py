"""Rewrite the golden files from the script: `make goldens`, or `python -m scenarios [SOURCE ...]`."""

from __future__ import annotations

import sys

import scenarios

for name in sys.argv[1:] or scenarios.sets():
    print(scenarios.write_golden(name))
