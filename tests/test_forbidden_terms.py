"""The forbidden-terms guard (tests/oracle/forbidden_terms.py), on a made-up list and a scratch git repo.

The real list is private, so the guard's run on this repo is an oracle test
(tests/oracle/test_forbidden_terms.py); these tests use a made-up term.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import forbidden_terms
import pytest

# cspell:ignore quartzowl

TERM = "Quartz Owl"  # the made-up forbidden term


def git(repo: Path, *args: str) -> None:
    identity = ["-c", "user.name=Example", "-c", "user.email=example@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", *identity, *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repo / "clean.txt").write_text("nothing here\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "Start")
    return repo


def test_the_list_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    listed = tmp_path / "terms.txt"
    listed.write_text("# private\n\nQuartz Owl\n  second-term  \n", encoding="utf-8")

    assert forbidden_terms.load(listed) == ["Quartz Owl", "second-term"]


@pytest.mark.parametrize(
    "text",
    ["Quartz Owl", "quartzowl", "QUARTZ_OWL", "quartz-owl", "quartz.owl", "Quartz  Owl", "a QuartzOwl2 b"],
)
def test_a_term_matches_ignoring_case_and_separators(text: str) -> None:
    assert forbidden_terms.matches(forbidden_terms.patterns([TERM])[0], text)


@pytest.mark.parametrize("text", ["Quartz", "Quartz/Owl", "Quartz, Owl", "owl quartz"])
def test_other_text_does_not_match(text: str) -> None:
    assert not forbidden_terms.matches(forbidden_terms.patterns([TERM])[0], text)


def test_the_working_tree_scan_finds_contents_and_names_without_printing_the_term(repo: Path) -> None:
    (repo / "tracked.md").write_text("line one\nsee QUARTZ_OWL here\n", encoding="utf-8")
    git(repo, "add", "tracked.md")
    (repo / "untracked-quartz.owl-notes.txt").write_text("clean\n", encoding="utf-8")
    (repo / "ignored.txt").write_text("Quartz Owl\n", encoding="utf-8")

    found = forbidden_terms.scan_worktree(repo, forbidden_terms.patterns([TERM]))

    assert found == ["tracked.md:2:5: term 1", "untracked-**********-notes.txt: term 1 in the file's name"]
    assert "quartz" not in "\n".join(found).lower()


def test_a_clean_working_tree_has_no_findings(repo: Path) -> None:
    assert forbidden_terms.scan_worktree(repo, forbidden_terms.patterns([TERM])) == []


def test_a_tracked_file_deleted_from_the_working_tree_is_skipped(repo: Path) -> None:
    (repo / "clean.txt").unlink()

    assert forbidden_terms.scan_worktree(repo, forbidden_terms.patterns([TERM])) == []


def test_the_history_scan_counts_each_term_in_contents_names_and_messages(repo: Path) -> None:
    (repo / "notes.txt").write_text("quartz-owl\n", encoding="utf-8")
    (repo / "QuartzOwl.txt").write_text("clean\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "Add the quartz owl notes")
    git(repo, "rm", "-q", "notes.txt", "QuartzOwl.txt")
    git(repo, "commit", "-q", "-m", "Remove them")

    counts = forbidden_terms.scan_history(repo, forbidden_terms.patterns([TERM, "absent term"]))

    assert counts == [
        {"file contents": 1, "file names": 1, "commit messages": 1},
        {"file contents": 0, "file names": 0, "commit messages": 0},
    ]
    assert forbidden_terms.scan_worktree(repo, forbidden_terms.patterns([TERM])) == []
