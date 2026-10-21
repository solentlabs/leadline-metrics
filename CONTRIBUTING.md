# Contributing

How to set up, check and release leadline. How it is built is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); how it is tested, in
[docs/TESTING.md](docs/TESTING.md).

## Setup and checks

```bash
make setup   # .venv with the package and dev tools (needs Python 3.10+)
make check   # the everyday gate: ruff, mypy (as Python 3.8), the tests that need only the repo
make oracle  # the tests that need files outside the repo (below)
make docs    # regenerate the docs that list the sources, from the packages and their READMEs
make places  # refetch the standards behind src/leadline/places/ (countries, time zones, US states); review the diff
make pyz     # dist/leadline.pyz: leadline as one file, for a host with Python 3.8+ and no pip
```

- The package runs on Python 3.8 and later, with the standard library
  only. The dev tools need 3.10 or later.
- Every change is written test first, and passes `make check`.
- `make oracle` runs where the files it needs are: the script leadline
  replaces, a capture of its answers, a list of terms the repo must never
  hold, and a leadline config. Their paths go in `.leadline-local.json`,
  which git ignores (copy `.leadline-local.example.json`). It fails,
  naming each setting, when one is missing
  ([docs/TESTING.md](docs/TESTING.md#local-settings-and-the-oracle-split)).
- Adding a platform follows
  [docs/ADDING_A_SOURCE.md](docs/ADDING_A_SOURCE.md). Design decisions and
  their reasons are in
  [docs/ARCHITECTURE_DECISIONS.md](docs/ARCHITECTURE_DECISIONS.md).

## Lint, spelling and Markdown

- Lint is ruff with a curated rule set. `pyproject.toml` lists each rule
  family with the reason it earns its place, and the families left out
  with why. A rule ignored everywhere is listed there with its reason; any
  other ignore is per line or per file and says why.
- `cspell.json` and `.markdownlint.jsonc` configure the spelling and
  Markdown checks VS Code runs. They are editor tooling, not part of
  `make check`, and every change runs both clean:

  ```bash
  npx -y cspell@10.3.3 --no-progress <changed files>
  npx -y markdownlint-cli2@0.23.3 <changed Markdown files>
  ```

## CI and releases

- CI runs `make check`, then the tests on Linux with Python 3.8 through
  3.14, and on macOS and Windows with 3.14
  ([docs/TESTING.md](docs/TESTING.md#operating-systems)).
- The actions the workflows use are named by their version (`@v7`), which
  reads at a glance. Dependabot proposes each newer version as a pull request
  to review.
- A release is a pushed tag, `v<version>`, which CI's gate and
  `release.yml` turn into a PyPI upload
  ([docs/RELEASING.md](docs/RELEASING.md#release)).
- After a version bump, run `make setup`. The dev venv's installed
  metadata must match the new version: `test_packaging`'s
  `test_installed_version` compares them.
