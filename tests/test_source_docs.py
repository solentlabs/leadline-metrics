"""The docs generated from the source packages (tools/gen_source_docs.py, `make docs`).

Each source's README holds, in two labeled bullets, what it reads and its config entry; the package holds its
default file, credentials and lag. The generator writes the source table, the lagging sentence, the list of sources
and the credentials table between markers; these tests hold the files to it, and a README to its two fields.
"""

from __future__ import annotations

import re
import types
from pathlib import Path
from typing import Any

import gen_source_docs as gen
import pytest

from leadline.sources import discover

ROOT = Path(__file__).resolve().parent.parent


def fake(
    root: Path, name: str, readme: str | None, env: tuple[str, ...] = (), lagging: bool = False, group: str = ""
) -> Any:
    """A source package under root/src/leadline/sources[/group]/<name>, with this README (none when None)."""
    folder = root / "src" / "leadline" / "sources" / group / name
    folder.mkdir(parents=True)
    (folder / "__init__.py").touch()
    if readme is not None:
        (folder / "README.md").write_text(readme, encoding="utf-8")
    return types.SimpleNamespace(
        __file__=str(folder / "__init__.py"), NAME=name, DEFAULT_FILE=f"{name}-{{id}}.jsonl", ENV=env, LAGGING=lagging
    )


def readme(name: str, reads: str = "a thing.", entry: str = "`id`.") -> str:
    return f"# {name}\n\n- **Reads:** {reads}\n- **Writes:** rows.\n- **Config entry:** {entry}\n"


def registry(*sources: Any) -> Any:
    return types.SimpleNamespace(sources={s.NAME: s for s in sources}, failed={})


def test_the_generated_blocks_are_what_the_files_hold() -> None:
    assert gen.stale(ROOT) == [], "run `make docs`"


def test_every_source_found_has_a_readme_with_its_two_fields() -> None:
    for name, source in discover().sources.items():
        reads, entry = gen.source_fields(gen.readme_of(source, ROOT))
        assert reads
        assert entry, name


def test_the_source_table_lists_each_source_by_name_with_its_readmes_words(tmp_path: Path) -> None:
    b = fake(tmp_path, "b", readme("b", reads="reads b.", entry="`x`; `y`."))
    a = fake(tmp_path, "a", readme("a", reads="reads\n  a over\n  lines."), group="g")

    table = gen.render(registry(b, a), tmp_path)["docs/ARCHITECTURE.md"]["source-table"]

    assert table.splitlines() == [
        "| Source | Reads | Default file | Config entry |",
        "| ------ | ----- | ------------ | ------------ |",
        "| [`a`](../src/leadline/sources/g/a/README.md) | reads a over lines. | `a-{id}.jsonl` | `id`. |",
        "| [`b`](../src/leadline/sources/b/README.md) | reads b. | `b-{id}.jsonl` | `x`; `y`. |",
    ]


def test_a_link_to_the_readmes_own_section_points_into_that_readme(tmp_path: Path) -> None:
    a = fake(tmp_path, "a", readme("a", entry="`kinds` ([keys](#kinds))."))

    table = gen.render(registry(a), tmp_path)["docs/ARCHITECTURE.md"]["source-table"]

    assert table.endswith("| `kinds` ([keys](../src/leadline/sources/a/README.md#kinds)). |")


def test_the_lagging_sentence_names_the_lagging_sources(tmp_path: Path) -> None:
    sources = [fake(tmp_path, n, readme(n), lagging=n != "b") for n in "abc"]

    lagging = gen.render(registry(*sources), tmp_path)["docs/ARCHITECTURE.md"]["lagging-sources"]

    assert lagging == "The lagging sources are `a` and `c`."


@pytest.mark.parametrize(
    ("lags", "sentence"),
    [
        ([], "No source lags."),
        (["a"], "The lagging source is `a`."),
        (["a", "b", "c"], "The lagging sources are `a`, `b` and `c`."),
    ],
)
def test_the_lagging_sentence_reads_for_any_count(tmp_path: Path, lags: list[str], sentence: str) -> None:
    sources = [fake(tmp_path, n, readme(n), lagging=n in lags) for n in "abc"]

    assert gen.render(registry(*sources), tmp_path)["docs/ARCHITECTURE.md"]["lagging-sources"] == sentence


def test_the_lagging_sentence_wraps_at_eighty_columns(tmp_path: Path) -> None:
    names = ["a_long_source_name", "another_long_source_name", "yet_another_long_one"]
    sources = [fake(tmp_path, n, readme(n), lagging=True) for n in names]

    lagging = gen.render(registry(*sources), tmp_path)["docs/ARCHITECTURE.md"]["lagging-sources"]

    assert len(lagging.splitlines()) > 1
    assert all(len(line) <= 80 for line in lagging.splitlines())


def test_the_source_list_links_each_readme(tmp_path: Path) -> None:
    a = fake(tmp_path, "a", readme("a", reads="reads a."))

    listed = gen.render(registry(a), tmp_path)["docs/CONFIGURATION.md"]["source-list"]

    assert listed.splitlines() == [
        "| Source | Reads |",
        "| ------ | ----- |",
        "| [`a`](../src/leadline/sources/a/README.md) | reads a. |",
    ]


def test_the_credentials_table_lists_each_variable_with_the_sources_that_read_it(tmp_path: Path) -> None:
    sources = [
        fake(tmp_path, "a", readme("a"), env=("TOKEN",)),
        fake(tmp_path, "b", readme("b"), env=("ACCOUNT", "TOKEN")),
        fake(tmp_path, "c", readme("c")),
    ]

    table = gen.render(registry(*sources), tmp_path)["docs/CONFIGURATION.md"]["credentials-table"]

    assert table.splitlines() == [
        "| Variable | Used by |",
        "| -------- | ------- |",
        "| `ACCOUNT` | `b` |",
        "| `TOKEN` | `a`, `b` |",
    ]


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        (None, "has no README.md"),
        ("# a\n\n- **Config entry:** `id`.\n", "has no `- **Reads:**` bullet"),
        ("# a\n\n- **Reads:** a thing.\n", "has no `- **Config entry:**` bullet"),
        ("# a\n\n- **Reads:**\n- **Config entry:** `id`.\n", "has an empty `- **Reads:**` bullet"),
        (readme("a", reads="a | b"), "has a `|` in its `Reads` bullet"),
        (readme("a", entry="[keys](../docs/X.md#k)"), "links to ../docs/X.md#k"),
    ],
    ids=["no README", "no Reads", "no Config entry", "empty Reads", "a pipe", "a relative link"],
)
def test_a_source_without_its_two_fields_fails_the_generator(tmp_path: Path, text: str | None, problem: str) -> None:
    a = fake(tmp_path, "a", text)

    with pytest.raises(gen.SourceDocError, match=re.escape(problem)):
        gen.render(registry(a), tmp_path)


def test_a_source_that_failed_to_load_fails_the_generator(tmp_path: Path) -> None:
    broken: Any = types.SimpleNamespace(sources={}, failed={"a": "ImportError: x"})

    with pytest.raises(gen.SourceDocError, match="a: ImportError: x"):
        gen.render(broken, tmp_path)


MARKED = "before\n<!-- BEGIN GENERATED x -->\nold\n<!-- END GENERATED x -->\nafter\n"


def test_a_block_is_replaced_between_its_markers_and_the_rest_stays() -> None:
    new = gen.replace_block(MARKED, "x", "new\nlines")

    assert new == "before\n<!-- BEGIN GENERATED x -->\nnew\nlines\n<!-- END GENERATED x -->\nafter\n"
    assert gen.replace_block(new, "x", "new\nlines") == new


@pytest.mark.parametrize(
    "text", ["no markers\n", MARKED + MARKED, MARKED.replace("END", "FIN")], ids=["none", "two", "no end"]
)
def test_a_file_must_hold_each_block_once(text: str) -> None:
    with pytest.raises(gen.SourceDocError, match="GENERATED x"):
        gen.replace_block(text, "x", "new")


def test_regenerating_a_stale_tree_rewrites_only_the_blocks_and_then_is_current(tmp_path: Path) -> None:
    a = fake(tmp_path, "a", readme("a"), env=("TOKEN",), lagging=True)
    for doc, blocks in gen.BLOCKS.items():
        (tmp_path / doc).parent.mkdir(exist_ok=True)
        (tmp_path / doc).write_text("# T\n" + "".join(gen.empty(b) for b in blocks) + "tail\n", encoding="utf-8")

    assert gen.stale(tmp_path, registry(a)) == sorted(gen.BLOCKS)
    gen.write(tmp_path, registry(a))

    assert gen.stale(tmp_path, registry(a)) == []
    text = (tmp_path / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert text.startswith("# T\n")
    assert text.endswith("tail\n")
    assert "| [`a`]" in text


def test_the_readme_sends_the_reader_to_the_generated_list_of_sources() -> None:
    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
    configuration = (ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")

    assert "(docs/CONFIGURATION.md#sources)" in readme_text
    assert re.search(r"^## Sources$", configuration, re.M)
    assert not re.search(r"^\| Platform \|", readme_text, re.M), "the platform table is the generated list now"


def anchors(path: Path) -> set[str]:
    """The anchors of a Markdown file's headings, as GitHub makes them (outside code blocks)."""
    found, fence = set(), False
    for line in path.read_text(encoding="utf-8").splitlines():
        fence ^= line.startswith("```")
        if not fence and (heading := re.match(r"#+ (.*)", line)):
            found.add(re.sub(r"[^\w\- ]", "", heading.group(1).lower()).replace(" ", "-"))
    return found


def test_every_link_in_a_source_readme_resolves_to_a_file_and_a_heading() -> None:
    for readme in sorted((ROOT / "src" / "leadline" / "sources").rglob("README.md")):
        for target in re.findall(r"\]\(([^)]+)\)", readme.read_text(encoding="utf-8")):
            if target.startswith("https://"):
                continue
            file, _, anchor = target.partition("#")
            linked = readme.parent / file if file else readme
            assert linked.is_file(), f"{readme.relative_to(ROOT)} links to {target}"
            assert not anchor or anchor in anchors(linked), f"{readme.relative_to(ROOT)} links to {target}"
