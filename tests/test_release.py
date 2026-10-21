"""tools/check_release.py: a release's tag, the package version and CHANGELOG.md agree, or it stops.

release.yml runs it before building, so a tag that disagrees with either publishes nothing
(docs/RELEASING.md, "Release").
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import check_release
import pytest

RELEASED = "# Changelog\n\n## Unreleased\n\n## 0.2.0 - 2026-10-05\n\n- A change.\n"


def project(root: Path, version: str, changelog: str) -> Path:
    """A folder laid out as the repo is, holding only what the check reads."""
    (root / "src" / "leadline").mkdir(parents=True)
    (root / "src" / "leadline" / "__init__.py").write_text(
        f'"""leadline."""\n\n__version__ = "{version}"\n', encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    return root


def test_a_tag_that_names_the_version_with_its_changelog_heading_passes(tmp_path: Path) -> None:
    assert check_release.problems("v0.2.0", project(tmp_path, "0.2.0", RELEASED)) == []


def test_a_tag_that_differs_from_the_version_is_a_problem(tmp_path: Path) -> None:
    root = project(tmp_path, "0.2.0", RELEASED + "\n## 0.2.1 - 2026-10-06\n")

    assert check_release.problems("v0.2.1", root) == [
        "tag v0.2.1 does not match the package version 0.2.0 (src/leadline/__init__.py)"
    ]


def test_a_tag_without_the_v_is_a_problem(tmp_path: Path) -> None:
    assert check_release.problems("0.2.0", project(tmp_path, "0.2.0", RELEASED)) == [
        "tag 0.2.0 does not match the package version 0.2.0 (src/leadline/__init__.py)"
    ]


@pytest.mark.parametrize(
    "changelog",
    [
        pytest.param("# Changelog\n\n## Unreleased\n\n- A change.\n", id="still-unreleased"),
        pytest.param("# Changelog\n\n## 0.2.0\n\n- A change.\n", id="no-date"),
        pytest.param("# Changelog\n\n## 0.2.0rc1 - 2026-10-05\n", id="another-version"),
        pytest.param("# Changelog\n\n- ## 0.2.0 - 2026-10-05\n", id="not-a-heading"),
    ],
)
def test_a_version_without_its_changelog_heading_is_a_problem(tmp_path: Path, changelog: str) -> None:
    assert check_release.problems("v0.2.0", project(tmp_path, "0.2.0", changelog)) == [
        "CHANGELOG.md has no heading `## 0.2.0 - YYYY-MM-DD`"
    ]


def test_both_problems_are_named(tmp_path: Path) -> None:
    assert len(check_release.problems("v0.3.0", project(tmp_path, "0.2.0", "# Changelog\n"))) == 2


def test_the_command_exits_1_naming_each_problem_or_0(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, "0.2.0", RELEASED)

    assert check_release.main(["v0.2.0"], root) == 0
    assert check_release.main(["v0.3.0"], root) == 1
    assert "tag v0.3.0 does not match the package version 0.2.0" in capsys.readouterr().err


# --- the tagged commit is on main ---


def git(repo: Path, *args: str) -> str:
    identity = ["-c", "user.name=Example", "-c", "user.email=example@example.invalid", "-c", "commit.gpgsign=false"]
    done = subprocess.run(["git", *identity, *args], cwd=repo, check=True, capture_output=True, text=True)
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo with main (two commits) and a branch off its first commit, which has a commit main lacks."""
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    git(tmp_path, "add", "a.txt")
    git(tmp_path, "commit", "-q", "-m", "first")
    git(tmp_path, "checkout", "-q", "-b", "side")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    git(tmp_path, "add", "b.txt")
    git(tmp_path, "commit", "-q", "-m", "only on side")
    git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "c.txt").write_text("c", encoding="utf-8")
    git(tmp_path, "add", "c.txt")
    git(tmp_path, "commit", "-q", "-m", "second")
    return tmp_path


def test_a_commit_in_mains_history_passes(repo: Path) -> None:
    assert check_release.not_on("main", repo) == []
    git(repo, "checkout", "-q", "HEAD~1")  # an older commit of main
    assert check_release.not_on("main", repo) == []


def test_a_commit_only_on_another_branch_is_a_problem(repo: Path) -> None:
    git(repo, "checkout", "-q", "side")

    assert check_release.not_on("main", repo) == ["the tagged commit is not on main: merge it there first"]


def test_a_branch_that_does_not_exist_is_a_problem_not_a_crash(repo: Path) -> None:
    [problem] = check_release.not_on("origin/main", repo)

    assert problem.startswith("cannot compare the tagged commit with origin/main: ")


def test_the_command_checks_the_branch_only_when_asked(repo: Path) -> None:
    root = project(repo, "0.2.0", RELEASED)
    git(repo, "checkout", "-q", "side")

    assert check_release.main(["v0.2.0"], root) == 0
    assert check_release.main(["v0.2.0", "--on", "main"], root) == 1
