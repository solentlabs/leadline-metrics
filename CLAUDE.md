# Claude Rules

> **This file**: how AI tools and contributors work in this repository. It
> points rather than restates.

## What This Is

`leadline-metrics` (PyPI `leadline-metrics`, CLI/import `leadline`):
daily adoption and health metrics for software projects. A stdlib-only
Python tool, with everything user-specific in a config file.
The goals are in `docs/ARCHITECTURE.md`, "Goals".

## Where Things Live

| Topic | Location |
| ----- | -------- |
| For a new user: pitch, platforms, quick start, scheduling | `README.md` |
| Dev setup, `make` targets, lint, CI, releasing | `CONTRIBUTING.md` |
| Design: goals, modules, the run, source contract and table, health | `docs/ARCHITECTURE.md` |
| Core APIs sources use (http, store, github) | `docs/CORE.md` |
| Config file, credentials | `docs/CONFIGURATION.md` |
| Report blocks (`reports` setting) | `docs/REPORTS.md` |
| Testing: scenarios, goldens, port tests, oracle | `docs/TESTING.md` |
| The one-file build, release | `docs/RELEASING.md` |
| Decisions and rationale | `docs/ARCHITECTURE_DECISIONS.md` |
| Adding a source (steps for a new platform) | `docs/ADDING_A_SOURCE.md` |
| `trends.json`'s format and the `trends.html` viewer | `docs/TRENDS.md` |

## Hard Constraints

1. **Runtime is stdlib-only, Python 3.8, JSON config.** Dev tooling may have
   dependencies.
2. **Modular by construction.** Adding or removing a source means one folder
   (in its platform's folder when the service has several), its config
   entry, and `make docs` — nothing else (`docs/ADDING_A_SOURCE.md`).
   Sources never import each other; they share only the core (config,
   context, http, store) and their own platform folder's shared code
   (`sources/github/` holds what the GitHub sources share and nothing one
   source owns). Reports read the output files, never a source's internals.
   A change that breaks this rule needs an ADR.
3. **Do not call pypistats.org ad hoc.** It rate-limits by IP, and probing
   degrades a scheduled run (`docs/ARCHITECTURE_DECISIONS.md`,
   "pypistats.org is called only by the scheduled run").
4. **No visitor IP addresses.** leadline keeps counts, never the addresses
   behind them, not even as an option.

## Engineering Principles

- **TDD.** Write the test first and watch it fail, then write the code.
  Every behavior has a test; a bug fix starts with a test that reproduces it.
- **KISS.** The simplest design that meets the need. No speculative
  options, flags or abstractions.
- **DRY.** Each piece of knowledge exists once. Before writing a helper,
  look for an existing one in the core.
- **Single source of truth.** Each fact has one home: design in
  ARCHITECTURE.md, rationale in ARCHITECTURE_DECISIONS.md, tool config in
  `pyproject.toml`. Elsewhere, link to it.
- **Docs and code change together.** A change that alters behavior, a
  contract or a decision updates the doc in the same commit. Docs state the
  present, not history.
- **Review as a principal engineer.** Before any change is proposed or a PR
  opened, review the full diff for: correctness and edge cases, simplicity,
  duplication, test quality (tests that can fail for the right reason), fit
  with ARCHITECTURE.md, and docs matching the code. Fix what the review finds
  first.

## Escalate, Don't Decide

Stop and ask the maintainer before any change that:

- departs from ARCHITECTURE.md or an ADR, or needs a new ADR
- changes a row format or a file name (existing files must stay readable)
- adds a dependency, runtime or dev
- touches credentials

Everything else, decide, and list the decision in the handoff.

## Verify

- `make check` is the everyday gate (ruff, format, mypy as 3.8, and every
  test that needs only the repo); CI runs the same, plus those tests on
  Python 3.8–3.14. Setup: `make setup`.
- `make oracle` runs the tests that need files outside the repo, which only
  the maintainer has (`docs/TESTING.md`). It fails when any is missing;
  contributors need only `make check`.

## Working Style

- Add a file or tool only when this project needs it.
