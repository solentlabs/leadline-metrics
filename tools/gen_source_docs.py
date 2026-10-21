"""Regenerate the docs that list the sources, from the source packages and their READMEs.

    python tools/gen_source_docs.py            # rewrite the blocks (`make docs`)
    python tools/gen_source_docs.py --check    # exit 1 naming the files whose blocks are stale

A source's README holds two labeled bullets, `- **Reads:** ...` and `- **Config entry:** ...`; its package holds
`DEFAULT_FILE`, `ENV` and `LAGGING`. Between `<!-- BEGIN GENERATED name -->` and `<!-- END GENERATED name -->` the
docs in BLOCKS hold what is made of those: the source table and the lagging sentence (ARCHITECTURE.md), the list of
sources and the credentials table (CONFIGURATION.md). Stdlib only, like the package (docs/ARCHITECTURE.md, "Source
table"); `tests/test_source_docs.py` holds the files to it.
"""

from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path
from types import ModuleType
from typing import cast

from leadline.sources import Registry, Source, discover

ROOT = Path(__file__).resolve().parent.parent
BLOCKS: dict[str, tuple[str, ...]] = {
    "docs/ARCHITECTURE.md": ("source-table", "lagging-sources"),
    "docs/CONFIGURATION.md": ("source-list", "credentials-table"),
}
DOCS = "docs"  # where the generated docs are, so a README is `../<its path>` from there


class SourceDocError(Exception):
    """A source or file the docs can't be generated from."""


def folder_of(source: Source) -> Path:
    return Path(cast(ModuleType, source).__file__ or "").parent


def readme_of(source: Source, root: Path = ROOT) -> Path:
    """The source's README.md, in its package's folder; `root` is the repository (the source's own is under it)."""
    path = folder_of(source) / "README.md"
    if not path.is_file():
        raise SourceDocError(f"{source.NAME} has no README.md at {path.relative_to(root).as_posix()}")
    return path


def source_fields(readme: Path) -> tuple[str, str]:
    """The text of the README's `Reads` and `Config entry` bullets, each on one line."""
    text = readme.read_text(encoding="utf-8")
    found = []
    for label in ("Reads", "Config entry"):
        match = re.search(rf"^- \*\*{label}:\*\*(.*(?:\n  .*)*)", text, re.M)
        if not match:
            raise SourceDocError(f"{readme.parent.name} has no `- **{label}:**` bullet")
        words = " ".join(match.group(1).split())
        if not words:
            raise SourceDocError(f"{readme.parent.name} has an empty `- **{label}:**` bullet")
        if "|" in words:
            raise SourceDocError(f"{readme.parent.name} has a `|` in its `{label}` bullet")
        found.append(words)
    return found[0], found[1]


def _from_docs(words: str, readme: str) -> str:
    """The bullet's text for a doc: a link to a section of the README itself points into that README."""
    for target in re.findall(r"\]\(([^)]*)\)", words):
        if not target.startswith(("#", "https://")):
            raise SourceDocError(f"{readme} links to {target}; a bullet links only to its own sections or a URL")
    return re.sub(r"\]\((#[^)]*)\)", lambda m: f"]({readme}{m.group(1)})", words)


def render(registry: Registry[Source], root: Path = ROOT) -> dict[str, dict[str, str]]:
    """Each generated block's text, by file and block name; raises SourceDocError for a source it can't document."""
    if registry.failed:
        raise SourceDocError("sources failed to load: " + "; ".join(f"{n}: {w}" for n, w in registry.failed.items()))
    rows, listed = [], []
    for name, source in sorted(registry.sources.items()):
        readme = f"../{readme_of(source, root).relative_to(root).as_posix()}"
        reads, entry = source_fields(readme_of(source, root))
        reads, entry = _from_docs(reads, readme), _from_docs(entry, readme)
        rows.append(f"| [`{name}`]({readme}) | {reads} | `{source.DEFAULT_FILE}` | {entry} |")
        listed.append(f"| [`{name}`]({readme}) | {reads} |")
    table = [
        "| Source | Reads | Default file | Config entry |",
        "| ------ | ----- | ------------ | ------------ |",
        *rows,
    ]
    return {
        "docs/ARCHITECTURE.md": {"source-table": "\n".join(table), "lagging-sources": _lagging(registry)},
        "docs/CONFIGURATION.md": {
            "source-list": "\n".join(["| Source | Reads |", "| ------ | ----- |", *listed]),
            "credentials-table": _credentials(registry),
        },
    }


def _lagging(registry: Registry[Source]) -> str:
    names = [f"`{n}`" for n, s in sorted(registry.sources.items()) if s.LAGGING]
    if not names:
        sentence = "No source lags."
    elif len(names) == 1:
        sentence = f"The lagging source is {names[0]}."
    else:
        sentence = f"The lagging sources are {', '.join(names[:-1])} and {names[-1]}."
    return textwrap.fill(sentence, 80, break_long_words=False, break_on_hyphens=False)


def _credentials(registry: Registry[Source]) -> str:
    users: dict[str, list[str]] = {}
    for name, source in sorted(registry.sources.items()):
        for variable in source.ENV:
            users.setdefault(variable, []).append(f"`{name}`")
    rows = [f"| `{variable}` | {', '.join(names)} |" for variable, names in sorted(users.items())]
    return "\n".join(["| Variable | Used by |", "| -------- | ------- |", *rows])


def markers(block: str) -> tuple[str, str]:
    return f"<!-- BEGIN GENERATED {block} -->", f"<!-- END GENERATED {block} -->"


def empty(block: str) -> str:
    """The markers of a block with nothing between them, as a doc starts with."""
    begin, end = markers(block)
    return f"{begin}\n{end}\n"


def replace_block(text: str, block: str, body: str) -> str:
    """`text` with what is between the block's markers replaced by `body`; each marker must occur once."""
    begin, end = markers(block)
    if text.count(begin) != 1 or text.count(end) != 1 or text.index(begin) > text.index(end):
        raise SourceDocError(f"the file needs one {begin} and one {end}, in that order")
    head, rest = text.split(begin, 1)
    return f"{head}{begin}\n{body}\n{end}{rest.split(end, 1)[1]}"


def _regenerated(root: Path, registry: Registry[Source] | None) -> dict[str, str]:
    blocks = render(discover() if registry is None else registry, root)
    texts = {}
    for doc, names in BLOCKS.items():
        text = (root / doc).read_text(encoding="utf-8")
        for name in names:
            text = replace_block(text, name, blocks[doc][name])
        texts[doc] = text
    return texts


def stale(root: Path = ROOT, registry: Registry[Source] | None = None) -> list[str]:
    """The generated docs, relative to `root`, whose blocks differ from what the sources give, sorted."""
    texts = _regenerated(root, registry)
    return sorted(doc for doc, text in texts.items() if (root / doc).read_text(encoding="utf-8") != text)


def write(root: Path = ROOT, registry: Registry[Source] | None = None) -> list[str]:
    """Rewrite the blocks that are stale; returns the files it changed."""
    texts = _regenerated(root, registry)
    changed = stale(root, registry)
    for doc in changed:
        (root / doc).write_bytes(texts[doc].encode("utf-8"))  # LF on every OS
    return changed


def main(argv: list[str]) -> int:
    try:
        if argv == ["--check"]:
            behind = stale()
            for doc in behind:
                print(f"stale: {doc} (run `make docs`)")
            return 1 if behind else 0
        for doc in write():
            print(f"wrote {doc}")
    except SourceDocError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
