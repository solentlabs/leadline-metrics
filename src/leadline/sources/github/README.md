# github

The platform group for the GitHub sources: the ten `github_*` packages in
this folder, and the code they share.

- `api.py`: the API, the token, the request helpers, pages, GraphQL and
  times ([CORE.md](../../../../docs/CORE.md#github)). A source imports it as
  `from leadline.sources.github import api as github`.
- `spec.py`: the pure checks of an entry (`repo_problems`,
  `maintainer_problems`, `file_fields`) that a source's `spec.py` imports.
- `__init__.py`: sets `GROUP = True`, so discovery looks inside this folder
  for sources, and re-exports what `api.py` and `spec.py` provide.
- `tests/`: the shared code's tests (`test_github.py`), the response
  builders every GitHub source's tests use (`builders.py`) and their
  oracle kinds (`shapes.py`).

A source here imports the core and this folder's shared modules, never
another source.
