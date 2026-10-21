"""Find the private list's forbidden terms in the repo, without ever printing one.

Usage: python tests/oracle/forbidden_terms.py [--history]

The list is the `forbidden_terms` local setting (tests/oracle/local_settings.py): one term per line,
`#` starting a comment. A term matches ignoring case and the separators space, `_`, `-` and `.`, so
one spelling covers the others ("Quartz Owl" matches `quartz_owl` and `QuartzOwl`).

With no argument, every file in the working tree that git tracks or would track (untracked but not
ignored) is searched, its name and its text; each finding is `path:line:column: term N`, with any
term in the path starred out. `make oracle` runs this (tests/oracle/test_forbidden_terms.py).

With --history, every file content, file name and commit message in the whole history (every ref)
is searched, and only counts are printed, per term by its number in the list. History keeps what a
commit removes, so this is how a rewrite of history is checked; it isn't part of `make oracle`.

Exit status: 0 when nothing is found, 1 when something is.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Pattern

import local_settings

SEPARATORS = " _.-"
HISTORY_PLACES = ("file contents", "file names", "commit messages")


def load(path: Path) -> list[str]:
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def patterns(terms: list[str]) -> list[Pattern[str]]:
    """One pattern per term: its letters in order, any run of separators allowed between them, any case."""
    between = f"[{re.escape(SEPARATORS)}]*"
    return [
        re.compile(between.join(re.escape(c) for c in term if c not in SEPARATORS), re.IGNORECASE) for term in terms
    ]


def matches(pattern: Pattern[str], text: str) -> bool:
    return pattern.search(text) is not None


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout


def _starred(text: str, found: list[Pattern[str]]) -> str:
    for pattern in found:
        text = pattern.sub(lambda m: "*" * len(m.group()), text)
    return text


def scan_worktree(repo: Path, found: list[Pattern[str]]) -> list[str]:
    """Where each term is in the working tree's files that git tracks or would track: names, then lines."""
    names = sorted({n for n in _git(repo, "ls-files", "-co", "--exclude-standard", "-z").decode().split("\0") if n})
    findings = []
    for name in names:
        path = repo / name
        if not path.is_file():
            continue  # tracked, but deleted from the working tree
        shown = _starred(name, found)
        findings += [f"{shown}: term {n} in the file's name" for n, p in enumerate(found, 1) if p.search(name)]
        text = path.read_bytes().decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), 1):
            for n, pattern in enumerate(found, 1):
                findings += [f"{shown}:{line_number}:{m.start() + 1}: term {n}" for m in pattern.finditer(line)]
    return findings


def scan_history(repo: Path, found: list[Pattern[str]]) -> list[dict[str, int]]:
    """For each term, how many file contents, file names and commit messages in the history hold it."""
    listed = _git(repo, "rev-list", "--all", "--objects").decode("utf-8", errors="replace").splitlines()
    ids = [line.partition(" ")[0] for line in listed]
    paths = {line.partition(" ")[2] for line in listed if line.partition(" ")[2]}
    batch = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=repo, input="\n".join(ids).encode(), check=True, capture_output=True
    ).stdout
    contents = []
    position = 0
    while position < len(batch):
        header_end = batch.index(b"\n", position)
        _, kind, size = batch[position:header_end].decode().split()
        start = header_end + 1
        if kind == "blob":
            contents.append(batch[start : start + int(size)].decode("utf-8", errors="replace"))
        position = start + int(size) + 1
    messages = _git(repo, "log", "--all", "--format=%B%x00").decode("utf-8", errors="replace").split("\0")
    places = {"file contents": set(contents), "file names": paths, "commit messages": messages}
    return [{place: sum(1 for text in places[place] if p.search(text)) for place in HISTORY_PLACES} for p in found]


def main(args: list[str]) -> int:
    repo = local_settings.REPO
    found = patterns(load(local_settings.path("forbidden_terms")))
    if args == ["--history"]:
        counts = scan_history(repo, found)
        for n, count in enumerate(counts, 1):
            print(f"term {n}: " + ", ".join(f"{count[place]} {place}" for place in HISTORY_PLACES))
        return 1 if any(any(count.values()) for count in counts) else 0
    if args:
        print("usage: python tests/oracle/forbidden_terms.py [--history]", file=sys.stderr)
        return 2
    findings = scan_worktree(repo, found)
    print("\n".join(findings) or f"none of the {len(found)} terms is in the working tree")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
