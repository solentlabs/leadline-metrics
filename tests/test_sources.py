"""Source discovery (docs/ARCHITECTURE.md, "Sources"): the registry is found, never listed."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from textwrap import dedent

import pytest

import leadline
from leadline import health, reports, sources
from leadline.config import Config, Section, Shown
from leadline.sources import Registry, discover

SOURCES = Path(leadline.__file__).parent / "sources"
# The sources on disk: a package with a source.py, in a platform group or alone. Adding or removing a source is
# adding or removing a folder, so this list is never edited.
SOURCE_NAMES = sorted(p.parent.name for pattern in ("*/source.py", "*/*/source.py") for p in SOURCES.glob(pattern))


def contract_module(name: str, **replace: str) -> str:
    """The text of a module that keeps the contract, with any member's line replaced."""
    members = {
        "NAME": f'NAME = "{name}"',
        "DEFAULT_FILE": 'DEFAULT_FILE = "x.jsonl"',
        "ENV": "ENV = ()",
        "LAGGING": "LAGGING = False",
        "validate": "def validate(cfg): return []",
        "files": "def files(cfg): return {}",
        "collect": "def collect(ctx, cfg): pass",
        "check": "def check(ctx, cfg): return []",
        "summary": "def summary(cfg): return []",
    }
    members.update(replace)
    return "\n".join(line for line in members.values() if line) + "\n"


def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> str:
    """A package of these files (paths under it), importable under a name of its own; returns that name."""
    package = f"scratch_{abs(hash(tmp_path)):x}"
    for rel, text in {"__init__.py": "", **files}.items():
        path = tmp_path / package / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(text), encoding="utf-8")
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    importlib.invalidate_caches()
    return package


def test_it_finds_every_source_by_name_in_name_order() -> None:
    found = discover()

    assert list(found.sources) == SOURCE_NAMES
    assert found.failed == {}


def test_the_sources_it_finds_are_the_packages() -> None:
    from leadline.sources import pypi

    assert discover().sources["pypi"] is pypi


def test_it_ignores_a_name_starting_with_an_underscore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = scratch(
        tmp_path, monkeypatch, {"_x/__init__.py": contract_module("_x"), "a/__init__.py": contract_module("a")}
    )

    found = discover(package)

    assert (list(found.sources), found.failed) == (["a"], {})


def test_a_flat_module_is_not_a_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = scratch(tmp_path, monkeypatch, {"a/__init__.py": contract_module("a"), "b.py": contract_module("b")})

    found = discover(package)

    assert (list(found.sources), found.failed) == (["a"], {})


def test_a_package_that_fails_to_import_is_isolated_and_the_others_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = scratch(
        tmp_path,
        monkeypatch,
        {
            "a/__init__.py": contract_module("a"),
            "boom/__init__.py": 'raise RuntimeError("no way")\n',
            "c/__init__.py": contract_module("c"),
        },
    )

    found = discover(package)

    assert list(found.sources) == ["a", "c"]
    assert found.failed == {"boom": "RuntimeError: no way"}


def test_a_syntax_error_is_isolated_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = scratch(tmp_path, monkeypatch, {"a/__init__.py": contract_module("a"), "bad/__init__.py": "def (:\n"})

    found = discover(package)

    assert list(found.sources) == ["a"]
    assert found.failed["bad"].startswith("SyntaxError: ")


def test_a_name_that_differs_from_the_folder_name_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = scratch(tmp_path, monkeypatch, {"a/__init__.py": contract_module("b")})

    found = discover(package)

    assert found.sources == {}
    assert found.failed == {"a": "ValueError: NAME 'b' differs from its module name 'a'"}


@pytest.mark.parametrize(
    "member", ["NAME", "DEFAULT_FILE", "ENV", "LAGGING", "validate", "files", "collect", "check", "summary"]
)
def test_a_missing_member_is_rejected_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, member: str) -> None:
    package = scratch(tmp_path, monkeypatch, {"a/__init__.py": contract_module("a", **{member: ""})})

    found = discover(package)

    assert found.sources == {}
    assert found.failed == {"a": f"TypeError: missing or wrong members: {member}"}


def test_a_member_of_the_wrong_kind_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = scratch(
        tmp_path, monkeypatch, {"a/__init__.py": contract_module("a", ENV='ENV = ["X"]', collect="collect = 3")}
    )

    assert discover(package).failed == {"a": "TypeError: missing or wrong members: ENV, collect"}


GROUP = "GROUP = True\n"


def test_it_walks_into_a_group_and_finds_its_sources_by_their_own_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = scratch(
        tmp_path,
        monkeypatch,
        {
            "g/__init__.py": GROUP + contract_module("g"),
            "g/api.py": "VALUE = 1\n",
            "g/tests/__init__.py": "",
            "g/a/__init__.py": contract_module("a"),
            "g/b/__init__.py": contract_module("b"),
            "c/__init__.py": contract_module("c"),
        },
    )

    found = discover(package)

    assert (list(found.sources), found.failed) == (["a", "b", "c"], {})
    assert found.sources["a"] is importlib.import_module(f"{package}.g.a")


def test_a_package_that_does_not_declare_itself_a_group_is_not_walked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = scratch(tmp_path, monkeypatch, {"p/__init__.py": "", "p/a/__init__.py": contract_module("a")})

    found = discover(package)

    assert found.sources == {}
    assert "p" in found.failed


def test_a_source_in_a_group_that_fails_is_isolated_under_its_own_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = scratch(
        tmp_path,
        monkeypatch,
        {
            "g/__init__.py": GROUP,
            "g/a/__init__.py": contract_module("a"),
            "g/boom/__init__.py": 'raise RuntimeError("no way")\n',
            "g/c/__init__.py": contract_module("other"),
        },
    )

    found = discover(package)

    assert list(found.sources) == ["a"]
    assert found.failed == {
        "boom": "RuntimeError: no way",
        "c": "ValueError: NAME 'other' differs from its module name 'c'",
    }


def test_a_group_that_fails_to_import_fails_each_source_in_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = scratch(
        tmp_path,
        monkeypatch,
        {
            "g/__init__.py": 'raise RuntimeError("no way")\n',
            "g/api.py": "",
            "g/tests/__init__.py": "",
            "g/a/__init__.py": contract_module("a"),
            "g/b/__init__.py": contract_module("b"),
            "c/__init__.py": contract_module("c"),
        },
    )

    found = discover(package)

    assert list(found.sources) == ["c"]
    assert found.failed == {
        "a": "inside g, which failed to load: RuntimeError: no way",
        "b": "inside g, which failed to load: RuntimeError: no way",
        "g": "RuntimeError: no way",
    }


def test_a_source_name_found_in_two_groups_fails_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = scratch(
        tmp_path,
        monkeypatch,
        {
            "g/__init__.py": GROUP,
            "g/x/__init__.py": contract_module("x"),
            "h/__init__.py": GROUP,
            "h/x/__init__.py": contract_module("x"),
            "h/y/__init__.py": contract_module("y"),
        },
    )

    found = discover(package)

    assert list(found.sources) == ["y"]
    assert found.failed == {"x": "ValueError: found twice: g.x, h.x"}


def test_it_looks_in_the_path_it_is_given(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = scratch(tmp_path, monkeypatch, {"a/__init__.py": contract_module("a")})

    found = discover(package, [str(tmp_path / package)])

    assert list(found.sources) == ["a"]


def test_the_registry_holds_what_was_found_and_what_failed() -> None:
    assert Registry({}, {}) == Registry()


def test_the_checked_members_are_the_source_protocols() -> None:
    declared = [
        member for protocol in (sources.Spec, sources.Source) for member in (*protocol.__annotations__, *vars(protocol))
    ]

    assert sorted(n for n in dict.fromkeys(declared) if not n.startswith("_")) == sorted(
        [*sources.DATA_MEMBERS, *sources.CALLABLE_MEMBERS]
    )


class Summarizing:
    """A source whose summary is one section titled by its name."""

    DEFAULT_FILE = "n.jsonl"

    def __init__(self, name: str) -> None:
        self.NAME = name

    def validate(self, cfg: object) -> list[str]:
        return []

    def files(self, cfg: object) -> dict[str, str]:
        return {}

    def summary(self, cfg: object) -> list[Section]:
        return [Section(self.NAME, None, f"{self.NAME}.jsonl", [Shown("n", self.NAME)], "Label")]


def test_the_summary_sections_are_each_configured_sources_in_config_order_and_none_from_one_that_failed(
    tmp_path: Path,
) -> None:
    registry = Registry({n: Summarizing(n) for n in "ab"}, {"c": "ImportError: x"})
    config = Config(tmp_path, "Agent/1.0", {"b": {}, "c": {}, "a": {}})

    assert [s.title for s in sources.summary_sections(config, registry)] == ["b", "a"]


def test_the_default_reports_are_the_sections_under_the_health_lines(tmp_path: Path) -> None:
    registry = Registry({"a": Summarizing("a")})
    config = Config(tmp_path, "Agent/1.0", {"a": {}})

    assert sources.default_reports(config, registry) == reports.default(
        sources.summary_sections(config, registry), health.FILE
    )
