# Releasing

How a version reaches PyPI, and how the one-file build is made.

## The one-file build

`leadline.pyz` is the whole tool in one file, for a host with Python 3.8 or
later and no pip.

- **What it is.** A stdlib zipapp of the package that `python3
  leadline.pyz` runs. It holds the package's modules, the places data files
  (`places/*.json`), no caches, tests or Markdown files, and a `__main__.py`
  that exits with the command's status.
- **Building it.** `make pyz` writes `dist/leadline.pyz`
  (`tools/build_pyz.py`). `tests/test_pyz.py` builds it and runs it on each
  Python CI tests.
- **Versions.** Each run records the package version and a config hash in
  `health.jsonl` ([Health](ARCHITECTURE.md#health)), so a night's rows name
  the code that wrote them. A build from a tagged commit makes that version
  name one commit.

## Release

A release is a version of `leadline-metrics` on PyPI, built from a git tag.
It publishes the package only, not `leadline.pyz`.

- **Version.** One home: `__version__` in `src/leadline/__init__.py`
  (SemVer), which hatchling reads.
- **Tag.** `v` and that version (`v0.1.0`).
- **Changelog.** The tagged commit has renamed CHANGELOG.md's
  `## Unreleased` to `## <version> - <date>` (`## 0.1.0 - 2026-10-05`).
- **Wheel.** The package alone: no runtime dependencies, Python 3.8 or
  later, the `leadline` command, and a license expression with no license
  classifier, as PyPI requires (`tests/test_packaging.py`).

To release: set the version and the CHANGELOG heading in one commit on
`main`, run `make setup` so the dev venv matches the new version
([CONTRIBUTING](../CONTRIBUTING.md#ci-and-releases)), then push the tag:

```bash
git tag v<version>
git push origin v<version>
```

Pushing the tag runs `.github/workflows/release.yml`: three jobs, each only
after the one before passes.

1. **`ci`**: CI's gate, `ci.yml` called as it is, on the tagged commit.
2. **`build`**: `tools/check_release.py <tag> --on origin/main` stops the
   release, naming each problem, when the tag isn't `v` and the version,
   CHANGELOG.md has no heading for the version, or the tagged commit isn't
   in `main`'s history. Then hatchling (a fixed version) builds the sdist and
   the wheel, `tools/check_dist.py` stops the release if the wheel holds
   tests, Markdown or anything outside the package, lacks the vendored
   places, or the sdist holds more than `src/leadline` and its notices (no
   `.claude`, `.github`, `tests` or `docs`), and the wheel is installed in a
   clean environment, where `leadline --version` must name the tag.
3. **`publish`**: in the GitHub environment `pypi`, uploads those two files
   by PyPI trusted publishing. Only this job can get an OIDC token
   (`id-token: write`), and PyPI's trusted publisher for the project
   accepts one only from this repo, workflow file and environment, so no
   API token is stored anywhere.

Set the `pypi` environment (repository Settings, Environments) to require a
reviewer, and protect the `v*` tags. The publish job then waits for your
approval each time, so a tag pushed by mistake, or an action that changed
under its version name, cannot upload anything unseen.
