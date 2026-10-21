"""No term on the private forbidden list is in the working tree: not in a file's name, not in its text.

Needs the private list, so it is marked oracle. A finding names the file, line and column and the
term's number in the list, never the term (tests/oracle/forbidden_terms.py).
"""

from __future__ import annotations

import forbidden_terms
import local_settings
import pytest

pytestmark = pytest.mark.oracle


def test_no_forbidden_term_is_in_the_working_tree() -> None:
    terms = forbidden_terms.load(local_settings.path("forbidden_terms"))

    assert terms, "the forbidden-terms list is empty"
    assert forbidden_terms.scan_worktree(local_settings.REPO, forbidden_terms.patterns(terms)) == []
