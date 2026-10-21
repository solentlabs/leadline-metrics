# `make check` is the everyday gate, and CI runs it too. `make oracle` runs the tests that need the
# real script, the capture and the forbidden-terms list, which stay out of the repo and CI; their paths
# are in .leadline-local.json (see .leadline-local.example.json). Run both before calling a change done.
VENV := .venv/bin

.PHONY: setup check test oracle goldens docs places lint format type-check pyz

# Dev venv needs Python 3.10+ (current pytest); the package itself runs on 3.8+.
setup:
	python3 -m venv .venv
	$(VENV)/python -m pip install -q --upgrade pip
	$(VENV)/python -m pip install -q -e ".[dev]"

check: lint type-check test
	$(VENV)/ruff format --check .

test:
	$(VENV)/pytest

# Stops at once, naming each local setting that is missing (conftest.py).
oracle:
	$(VENV)/pytest -m oracle

# Reruns the script on every scenario and rewrites the goldens (a scenario with reviewed expected rows
# needs no script); `make goldens SOURCES=ha_integration` for some.
goldens:
	PYTHONPATH=tests:tests/oracle $(VENV)/python -m scenarios $(SOURCES)

# Rewrites the generated blocks of the docs (the source table, the lagging sources, the list of sources and the
# credentials table) from the source packages and their READMEs (tools/gen_source_docs.py).
docs:
	$(VENV)/python tools/gen_source_docs.py

# Fetches again the standards the places data in src/leadline/places/ comes from (ISO 3166-1 countries, IANA time
# zones, USPS states) and rewrites its files (`make places` for all three; `python tools/refresh_places.py zones` for one); review the diff. No schedule: run it when a standard changes.
places:
	$(VENV)/python tools/refresh_places.py

lint:
	$(VENV)/ruff check .

# Fixes lint and formatting in place.
format:
	$(VENV)/ruff check --fix .
	$(VENV)/ruff format .

# leadline as one file, dist/leadline.pyz, which `python3 leadline.pyz` runs (docs/RELEASING.md).
pyz:
	$(VENV)/python tools/build_pyz.py

type-check:
	$(VENV)/mypy
