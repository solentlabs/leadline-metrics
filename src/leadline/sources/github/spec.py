"""What the GitHub sources' specs share: the checks of an entry's `repo` and `maintainer`, and its file-name fields.

Declarations only: it imports no request, store or context code, so a source's spec.py may import it.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from leadline import config

_REPO = re.compile(r"[^/\s]+/[^/\s]+")


def repo_problems(entry: Mapping[str, Any]) -> list[str]:
    """The problem with an entry's `repo`, as `repo: problem`, unless it is `owner/name`."""
    repo = entry.get("repo")
    return [] if isinstance(repo, str) and _REPO.fullmatch(repo) else ["repo: must be owner/name"]


def maintainer_problems(entry: Mapping[str, Any]) -> list[str]:
    """The problem with an entry's `maintainer`, as `maintainer: problem`, unless it is a login, for `validate`."""
    maintainer = entry.get("maintainer")
    return [] if isinstance(maintainer, str) and maintainer else ["maintainer: must be a login"]


def file_fields(repo: str) -> dict[str, str]:
    """The fields a GitHub entry's file template may use: `repo`, and `repo_name`, the part after the slash."""
    return {"repo": repo, "repo_name": repo.split("/")[1]}


# The row of trends' Traffic card that shows the repo's stars and forks: the sources that read them name it, so
# the card is the same whichever file they come from.
STARS_AND_FORKS = config.Card("Traffic", "Stars and forks", "The repository's stars and forks, as of each day.")
