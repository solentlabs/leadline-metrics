"""The README's GitHub Actions recipe is true: its workflow is the structure intended, runs the command
and config the quick start sets up, maps a secret to every credential a source reads, pins actions as
this repo's CI does, and its steps, run in a scratch git repo, commit what leadline wrote and nothing
else.

The workflow is read out of README.md, as tests/test_readme.py reads the starter config. A workflow
is YAML, which the stdlib can't read, so `Subset` reads the plain subset the recipe keeps to and
refuses anything else.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import build_pyz
import pytest
from test_readme import README, fenced, section

from leadline.sources import discover

CI = README.parent / ".github" / "workflows" / "ci.yml"


class Subset:
    """A reader of the plain YAML the recipe keeps to: block mappings and lists of mappings, plain or quoted
    scalars, `|` blocks, and comments. Every scalar is a string, and blank and comment lines are dropped,
    even in a `|` block. Anything else fails an assert."""

    def __init__(self, text: str) -> None:
        self.lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        self.pos = 0

    def load(self) -> Any:
        loaded = self.node(indent(self.lines[0]))
        assert self.pos == len(self.lines), f"outside the subset: {self.lines[self.pos]!r}"
        return loaded

    def at(self, level: int, item: bool) -> bool:
        """Whether the next line is at `level`, and a list item or not, as `item` says."""
        if self.pos == len(self.lines):
            return False
        line = self.lines[self.pos]
        return indent(line) == level and line.lstrip().startswith("- ") == item

    def node(self, level: int) -> Any:
        return self.items(level) if self.at(level, item=True) else self.mapping(level)

    def items(self, level: int) -> list[Any]:
        out: list[Any] = []
        while self.at(level, item=True):
            self.lines[self.pos] = " " * (level + 2) + self.lines[self.pos].lstrip()[2:]  # a mapping two deeper
            out.append(self.mapping(level + 2))
        return out

    def mapping(self, level: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        while self.at(level, item=False):
            key, sep, value = self.lines[self.pos].strip().partition(":")
            assert sep, f"not a key: {self.lines[self.pos]!r}"
            assert not value or value[0] == " ", f"not a key: {self.lines[self.pos]!r}"
            assert key not in out, f"duplicate key {key!r}"
            self.pos += 1
            out[key] = self.value(scalar(value.strip()), level)
        return out

    def value(self, text: str, level: int) -> Any:
        """A key's value: `text` itself, the `|` block or the node indented under the key, or None."""
        deeper = []
        while self.pos < len(self.lines) and indent(self.lines[self.pos]) > level:
            deeper.append(self.lines[self.pos])
            self.pos += 1
        if text == "|":
            depth = min(indent(line) for line in deeper)
            return "".join(line[depth:] + "\n" for line in deeper)
        if not deeper:
            return text or None
        assert not text, f"a value and lines under it: {text!r}"
        return Subset("\n".join(deeper)).load()


def indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def scalar(value: str) -> str:
    """The value after `key:`, or "" when there is none, without its comment and quotes."""
    quoted = re.fullmatch(r"([\"'])(.*?)\1\s*(?:#.*)?", value)
    if quoted:
        return quoted.group(2)
    value = re.sub(r"(?:^|\s+)#.*$", "", value)
    assert not value or value[0] not in "[{&*!>", f"outside the subset: {value!r}"
    return value


def load_yaml(text: str) -> Any:
    return Subset(text).load()


def test_load_yaml_reads_its_subset_and_refuses_the_rest() -> None:
    text = "a: 1  # note\nb:\n  - c: 'x: y'  # note\n    d: |\n      one\n        two\n  - e:\nf:  # note\n"

    assert load_yaml(text) == {"a": "1", "b": [{"c": "x: y", "d": "one\n  two\n"}, {"e": None}], "f": None}
    for outside in ("a: [1, 2]\n", "a: {b: 1}\n", "a: 1\n  b: 2\n", "a: 1\na: 2\n"):
        with pytest.raises(AssertionError):
            load_yaml(outside)


def workflow() -> dict[str, Any]:
    blocks = fenced(section("Run it every day"), "yaml")
    assert len(blocks) == 1, "the recipe has one workflow"
    loaded: dict[str, Any] = load_yaml(blocks[0])
    return loaded


def steps() -> list[dict[str, Any]]:
    (job,) = workflow()["jobs"].values()
    found: list[dict[str, Any]] = job["steps"]
    return found


def leadline_step() -> dict[str, Any]:
    (step,) = [step for step in steps() if "leadline.pyz" in step.get("run", "")]
    return step


def commit_step() -> dict[str, Any]:
    (step,) = [step for step in steps() if "git push" in step.get("run", "")]
    return step


def test_it_runs_daily_and_on_demand_with_write_access_to_its_own_repo_only() -> None:
    flow = workflow()

    assert set(flow["on"]) == {"schedule", "workflow_dispatch"}
    (schedule,) = flow["on"]["schedule"]
    assert len(schedule["cron"].split()) == 5
    assert flow["permissions"] == {"contents": "write"}


def test_it_pins_actions_as_this_repos_ci_does() -> None:
    pinned = set(re.findall(r"uses: (\S+)", CI.read_text(encoding="utf-8")))
    uses = [step["uses"] for step in steps() if "uses" in step]

    assert uses == [use for use in pinned if use.startswith("actions/checkout@")]


def test_it_runs_the_quick_starts_command_on_the_quick_starts_config() -> None:
    step = leadline_step()

    assert step["run"].strip() == "python3 leadline.pyz run --config leadline.json"
    assert "`~/leadline/leadline.json`" in section("Quick start")
    assert "leadline.pyz run --config leadline.json" in section("Quick start")


def test_every_credential_a_source_reads_comes_from_a_secret_of_its_name_with_leadline_in_front() -> None:
    """GitHub refuses secret names starting GITHUB_, so each is LEADLINE_<variable>; a secret not added is empty."""
    env = leadline_step()["env"]

    assert set(env) == {var for source in discover().sources.values() for var in source.ENV}
    assert env == {var: f"${{{{ secrets.LEADLINE_{var} }}}}" for var in env}
    assert all(f"`LEADLINE_{var}`" in section("Run it every day") for var in env), "the recipe names each secret"


def test_the_commit_step_runs_whenever_leadline_ran_and_holds_no_credential() -> None:
    """An `if` without a status check function gets `success() &&` in front, which would skip the step after
    leadline exits 1; `!cancelled()` runs it after a failure, but not in a cancelled run."""
    step = commit_step()

    assert re.search(r"\b(success|failure|cancelled|always)\(\)", step["if"]), "a status check function"
    assert step["if"] == "${{ !cancelled() && steps.leadline.outcome != 'skipped' }}"
    assert leadline_step()["id"] == "leadline"
    assert "env" not in step


# git with no user or system config, so a signing or hook setting of the machine's can't change a commit.
ISOLATED = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, env=ISOLATED, capture_output=True, text=True, check=True
    ).stdout.strip()


def step_script(step: dict[str, Any], repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """A step's `run`, as Actions runs it on Linux (`bash -e`), with python3 the interpreter running the tests."""
    bin_dir = repo.parent / "bin"
    bin_dir.mkdir(exist_ok=True)
    python3 = bin_dir / "python3"
    if not python3.exists():
        python3.symlink_to(sys.executable)
    path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    return subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        cwd=repo,
        env={**ISOLATED, **env, "PATH": path},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Actions runs the recipe on Linux")
def test_its_steps_commit_what_leadline_wrote_and_nothing_when_nothing_changed(tmp_path: Path) -> None:
    """A metrics repo holding the pyz and the quick start's config, with no secrets added: the sources skip,
    and leadline still writes its health row, log, summary and trends, which the commit step pushes."""
    origin, repo = tmp_path / "origin.git", tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], env=ISOLATED, check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], env=ISOLATED, check=True, capture_output=True)
    build_pyz.build(repo / "leadline.pyz")
    (config,) = [b for b in fenced(section("Quick start"), "json") if '"sources"' in b]
    (repo / "leadline.json").write_text(config, encoding="utf-8")
    identity = ["-c", "user.name=Example", "-c", "user.email=example@example.invalid"]
    git(repo, "add", "-A")
    git(repo, *identity, "commit", "-q", "-m", "setup")
    git(repo, "push", "-q", "-u", "origin", "HEAD")
    no_secrets = dict.fromkeys(leadline_step()["env"], "")

    ran = step_script(leadline_step(), repo, no_secrets)
    committed = step_script(commit_step(), repo, {})

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert committed.returncode == 0, committed.stderr
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "rev-parse", "HEAD") == git(origin, "rev-parse", "HEAD")
    assert git(repo, "log", "-1", "--format=%an <%ae>") == (
        "github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>"
    )
    assert git(repo, "show", "--name-only", "--format=", "HEAD").splitlines() == [
        "metrics/SUMMARY.md",
        "metrics/health.jsonl",
        "metrics/leadline.log",
        f"metrics/logs/leadline-{date.today()}.log",  # noqa: DTZ011 (the run date is local)
        "metrics/trends.html",
        "metrics/trends.json",
    ]

    again = step_script(commit_step(), repo, {})

    assert again.returncode == 0, again.stderr
    assert git(repo, "rev-list", "--count", "HEAD") == "2"
