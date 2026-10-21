"""The module boundaries in docs/ARCHITECTURE.md, "Dependency rules", enforced by scanning imports."""

# cspell:ignore orelse
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterator

import pytest

SRC = Path(__file__).parent.parent / "src"
CORE = {
    "leadline",
    "leadline.config",
    "leadline.context",
    "leadline.http",
    "leadline.places",
    "leadline.store",
}
# What the report path (reports, trends, the registry) may import: the core without HTTP, which only a run loads.
OFFLINE = CORE - {"leadline.http"}


def is_module(src: Path, name: str) -> bool:
    path = src.joinpath(*name.split("."))
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


def walk(tree: ast.AST) -> Iterator[ast.AST]:
    """Every node of `tree` that runs: an `if TYPE_CHECKING:` block is for type checkers, so it is left out."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            for orelse in node.orelse:
                yield orelse
                yield from walk(orelse)
            continue
        yield node
        yield from walk(node)


def imported_modules(src: Path, path: Path, module: str) -> set[str]:
    """The modules `path` (named `module`) imports, relative imports resolved."""
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = package.rsplit(".", node.level - 1)[0]
                base = f"{parent}.{base}" if base else parent
            found.add(base)
            # `from leadline import store` imports the module leadline.store too.
            found.update(f"{base}.{a.name}" for a in node.names if is_module(src, f"{base}.{a.name}"))
    return found


def under(name: str, base: str) -> bool:
    """Is `name` the module `base` or one inside it."""
    return name == base or name.startswith(base + ".")


def groups_in(src: Path) -> set[str]:
    """The platform groups: the packages under sources/ whose __init__ sets `GROUP = True`."""
    found: set[str] = set()
    for init in (src / "leadline" / "sources").glob("*/__init__.py"):
        for node in ast.parse(init.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "GROUP" for t in node.targets):
                found.add(init.parent.name)
    return found


def own_sibling(name: str, own: str) -> bool:
    """Is `name` in the source package `own`, other than its source module, which a spec never imports."""
    return under(name, own) and not under(name, f"{own}.source")


def allowed_in_group(src: Path, parts: list[str], name: str) -> bool:
    """What a module of a platform group (or of a source in it) may import."""
    group = ".".join(parts[:3])
    if len(parts) == 3 or (len(parts) == 4 and src.joinpath(*parts[:4]).is_dir()):
        # A group's or a source's __init__ imports nothing of its own, so importing the package (to reach its
        # spec) loads no capture code; its names come lazily (leadline.sources.lazy).
        return name == "leadline.sources"
    if not src.joinpath(*parts[:4]).is_dir():
        # The group's own code (its package, api, spec): the core and itself, never a source or another group.
        return name in CORE or (under(name, group) and not is_source_of(src, group, name))
    # A source in the group: the core, its own modules and the group's shared ones, never another source.
    own = ".".join(parts[:4])
    shared = under(name, group) and not is_source_of(src, group, name)
    if parts[4:] == ["spec"]:
        # Its spec.py is pure declarations: the config, its siblings (not its source) and the group's pure spec.
        return name in {"leadline", "leadline.config", f"{group}.spec"} or own_sibling(name, own)
    return name in CORE or under(name, own) or shared


def allowed(src: Path, groups: set[str], module: str, name: str) -> bool:
    parts = module.split(".")
    if len(parts) > 2 and parts[1] == "sources" and parts[2] in groups:
        return allowed_in_group(src, parts, name)
    if len(parts) > 2 and parts[1] == "sources":
        # A source outside any group, a module or a package: the core and its own modules. Its spec.py is pure
        # declarations: the config and its siblings, no http, store or context.
        own = ".".join(parts[:3])
        if len(parts) == 3 and src.joinpath(*parts[:3]).is_dir():
            return name == "leadline.sources"  # the package's __init__: nothing of its own
        if parts[3:] == ["spec"]:
            return name in {"leadline", "leadline.config"} or own_sibling(name, own)
        return name in CORE or under(name, own)
    # The core, health, the reports and trends read the output files and the run's context, never a source.
    if module in {"leadline.reports", "leadline.trends"}:
        return name in OFFLINE
    if module in {*CORE, "leadline.health"}:
        return name in CORE
    if module == "leadline.sources":
        # The registry also builds the default summary, from the reports and the health lines.
        return name in OFFLINE or name in {"leadline.health", "leadline.reports"} or under(name, "leadline.sources")
    # The runner, the CLI and the rest reach sources through the registry, never by name.
    return not name.startswith("leadline.sources.")


def is_source_of(src: Path, group: str, name: str) -> bool:
    """Is `name` a source package of `group`, or inside one."""
    rest = name.split(".")[len(group.split(".")) :]
    return bool(rest) and src.joinpath(*group.split("."), rest[0]).is_dir() and rest[0] != "tests"


def violations(src: Path) -> list[str]:
    stdlib = getattr(sys, "stdlib_module_names", None)  # Python 3.10+
    groups = groups_in(src)
    found: list[str] = []
    for path in sorted(src.rglob("*.py")):
        parts = path.relative_to(src).with_suffix("").parts
        if "tests" in parts:  # a package's own tests are not runtime code
            continue
        module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        for name in sorted(imported_modules(src, path, module)):
            top = name.split(".")[0]
            if top == "leadline" and not allowed(src, groups, module, name):
                found.append(f"{module} imports {name}")
            elif stdlib is not None and top not in ("leadline", "__future__") and top not in stdlib:
                found.append(f"{module} imports non-stdlib {name}")
    return found


def test_the_package_keeps_its_boundaries() -> None:
    assert violations(SRC) == []


# Each rule, shown to catch what it forbids, on a scratch package.

STUBS = [
    "__init__",
    "config",
    "context",
    "http",
    "store",
    "rowdiff",
    "runner",
    "health",
    "reports",
    "sources/__init__",
    "sources/b",
]


def tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for name in STUBS:
        files.setdefault(f"leadline/{name}.py", "")
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"leadline/sources/a.py": "from leadline.sources import b\n"}, "leadline.sources.a imports leadline.sources"),
        ({"leadline/sources/a.py": "from . import b\n"}, "leadline.sources.a imports leadline.sources.b"),
        ({"leadline/sources/a.py": "import leadline.runner\n"}, "leadline.sources.a imports leadline.runner"),
        ({"leadline/sources/a.py": "from .. import health\n"}, "leadline.sources.a imports leadline.health"),
        ({"leadline/sources/a.py": "from leadline import rowdiff\n"}, "leadline.sources.a imports leadline.rowdiff"),
        ({"leadline/reports.py": "from leadline.sources.b import X\n"}, "leadline.reports imports leadline.sources.b"),
        ({"leadline/reports.py": "from leadline import health\n"}, "leadline.reports imports leadline.health"),
        ({"leadline/trends.py": "from leadline.sources.b import X\n"}, "leadline.trends imports leadline.sources.b"),
        ({"leadline/trends.py": "from leadline import reports\n"}, "leadline.trends imports leadline.reports"),
        ({"leadline/trends.py": "from . import runner\n"}, "leadline.trends imports leadline.runner"),
        ({"leadline/runner.py": "from leadline.sources import b\n"}, "leadline.runner imports leadline.sources.b"),
        ({"leadline/store.py": "from leadline import runner\n"}, "leadline.store imports leadline.runner"),
        ({"leadline/health.py": "from leadline.sources import b\n"}, "leadline.health imports leadline.sources.b"),
        ({"leadline/health.py": "from leadline import sources\n"}, "leadline.health imports leadline.sources"),
        ({"leadline/sources/__init__.py": "from leadline import runner\n"}, "leadline.sources imports leadline.runner"),
        # The report path (reports, trends, the registry) never loads HTTP.
        ({"leadline/reports.py": "from leadline import http\n"}, "leadline.reports imports leadline.http"),
        ({"leadline/trends.py": "from leadline.http import Http\n"}, "leadline.trends imports leadline.http"),
        ({"leadline/sources/__init__.py": "from leadline import http\n"}, "leadline.sources imports leadline.http"),
    ],
)
def test_each_rule_catches_its_violation(tmp_path: Path, files: dict[str, str], expected: str) -> None:
    assert expected in violations(tree(tmp_path, files))


def test_an_import_for_type_checkers_only_is_not_an_import(tmp_path: Path) -> None:
    files = {
        "leadline/reports.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from leadline import http\n"
    }

    assert violations(tree(tmp_path, files)) == []


def test_allowed_imports_pass(tmp_path: Path) -> None:
    files = {
        "leadline/sources/__init__.py": (
            "from leadline.sources import b\nfrom leadline.context import Context\n"
            "from leadline import health, reports\n"
        ),
        "leadline/sources/a.py": "import json\nfrom leadline import store\nfrom ..http import Http\n",
        "leadline/reports.py": "from leadline import store\nfrom leadline.context import Context\n",
        "leadline/health.py": "from leadline import store\nfrom leadline.context import Context\n",
        "leadline/runner.py": "from leadline import sources\nfrom leadline.sources import discover\n",
        "leadline/cli.py": "from leadline import runner\n",
    }
    assert violations(tree(tmp_path, files)) == []


# A source as a package, src/leadline/sources/<x>/, and a platform group, src/leadline/sources/<g>/, which holds
# shared modules (api.py, spec.py) and source packages (g/x/, g/y/).
PACKAGE_X = {"leadline/sources/x/__init__.py": "", "leadline/sources/x/spec.py": "", "leadline/sources/x/source.py": ""}
GROUP_G = {
    "leadline/sources/g/__init__.py": "GROUP = True\n",
    "leadline/sources/g/api.py": "",
    "leadline/sources/g/spec.py": "",
    "leadline/sources/g/gx/__init__.py": "",
    "leadline/sources/g/gx/spec.py": "",
    "leadline/sources/g/gx/source.py": "",
    "leadline/sources/g/gy/__init__.py": "",
}
GROUP_H = {
    "leadline/sources/h/__init__.py": "GROUP = True\n",
    "leadline/sources/h/api.py": "",
    "leadline/sources/h/hx/__init__.py": "",
}


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        # A package's __init__ imports nothing of its own, so importing the package (to reach its spec) loads no
        # capture code; its names come lazily (leadline.sources.lazy).
        (
            {"leadline/sources/x/__init__.py": "from leadline.sources.x.source import collect\n"},
            "leadline.sources.x imports leadline.sources.x.source",
        ),
        (
            {"leadline/sources/x/__init__.py": "from leadline import http\n"},
            "leadline.sources.x imports leadline.http",
        ),
        (
            {**GROUP_G, "leadline/sources/g/__init__.py": "GROUP = True\nfrom leadline.sources.g.api import X\n"},
            "leadline.sources.g imports leadline.sources.g.api",
        ),
        (
            {"leadline/sources/g/gx/__init__.py": "from leadline.sources.g.gx.source import collect\n"},
            "leadline.sources.g.gx imports leadline.sources.g.gx.source",
        ),
        (
            {"leadline/sources/x/source.py": "from leadline.sources import b\n"},
            "leadline.sources.x.source imports leadline.sources.b",
        ),
        (
            {"leadline/sources/x/source.py": "from ..b import Y\n"},
            "leadline.sources.x.source imports leadline.sources.b",
        ),
        (
            {"leadline/sources/x/__init__.py": "from leadline.sources.b import Y\n"},
            "leadline.sources.x imports leadline.sources.b",
        ),
        (
            {"leadline/sources/x/source.py": "from leadline import runner\n"},
            "leadline.sources.x.source imports leadline.runner",
        ),
        (
            {"leadline/sources/x/spec.py": "from leadline import http\n"},
            "leadline.sources.x.spec imports leadline.http",
        ),
        (
            {"leadline/sources/x/spec.py": "from leadline import store\n"},
            "leadline.sources.x.spec imports leadline.store",
        ),
        (
            {"leadline/sources/x/spec.py": "from leadline.context import Context\n"},
            "leadline.sources.x.spec imports leadline.context",
        ),
        (
            {"leadline/sources/x/spec.py": "from leadline.sources import b\n"},
            "leadline.sources.x.spec imports leadline.sources.b",
        ),
        # A spec never imports its own source, which the report path must be able to skip.
        (
            {"leadline/sources/x/spec.py": "from leadline.sources.x.source import collect\n"},
            "leadline.sources.x.spec imports leadline.sources.x.source",
        ),
        (
            {"leadline/sources/x/spec.py": "from . import source\n"},
            "leadline.sources.x.spec imports leadline.sources.x.source",
        ),
        (
            {"leadline/sources/g/gx/spec.py": "from .source import collect\n"},
            "leadline.sources.g.gx.spec imports leadline.sources.g.gx.source",
        ),
        (
            {"leadline/sources/x/source.py": "from leadline.sources.g import api\n", **GROUP_G},
            "leadline.sources.x.source imports leadline.sources.g.api",
        ),
        (
            {"leadline/sources/x/spec.py": "from leadline.sources.g.spec import f\n", **GROUP_G},
            "leadline.sources.x.spec imports leadline.sources.g.spec",
        ),
        (
            {"leadline/sources/g/gx/source.py": "from leadline.sources.g import gy\n"},
            "leadline.sources.g.gx.source imports leadline.sources.g.gy",
        ),
        (
            {"leadline/sources/g/gx/source.py": "from ..gy import Y\n"},
            "leadline.sources.g.gx.source imports leadline.sources.g.gy",
        ),
        (
            {"leadline/sources/g/gx/source.py": "from leadline.sources.g.gy import Y\n"},
            "leadline.sources.g.gx.source imports leadline.sources.g.gy",
        ),
        (
            {"leadline/sources/g/gx/source.py": "from leadline.sources.h import api\n", **GROUP_H},
            "leadline.sources.g.gx.source imports leadline.sources.h.api",
        ),
        (
            {"leadline/sources/g/gx/source.py": "from leadline.sources.h.hx import Y\n", **GROUP_H},
            "leadline.sources.g.gx.source imports leadline.sources.h.hx",
        ),
        (
            {"leadline/sources/g/gx/source.py": "from leadline.sources.x import Y\n", **PACKAGE_X},
            "leadline.sources.g.gx.source imports leadline.sources.x",
        ),
        (
            {"leadline/sources/g/gx/source.py": "from leadline import runner\n"},
            "leadline.sources.g.gx.source imports leadline.runner",
        ),
        (
            {"leadline/sources/g/gx/spec.py": "from leadline.sources.g import api\n"},
            "leadline.sources.g.gx.spec imports leadline.sources.g.api",
        ),
        (
            {"leadline/sources/g/gx/spec.py": "from leadline import http\n"},
            "leadline.sources.g.gx.spec imports leadline.http",
        ),
        (
            {"leadline/sources/g/api.py": "from leadline.sources.g import gx\n"},
            "leadline.sources.g.api imports leadline.sources.g.gx",
        ),
        (
            {"leadline/sources/g/api.py": "from leadline.sources.h import api\n", **GROUP_H},
            "leadline.sources.g.api imports leadline.sources.h.api",
        ),
        (
            {"leadline/sources/g/api.py": "from leadline.sources import b\n"},
            "leadline.sources.g.api imports leadline.sources.b",
        ),
        (
            {"leadline/sources/g/api.py": "from leadline import runner\n"},
            "leadline.sources.g.api imports leadline.runner",
        ),
        (
            {"leadline/sources/g/__init__.py": "GROUP = True\nfrom leadline import runner\n"},
            "leadline.sources.g imports leadline.runner",
        ),
        (
            {"leadline/sources/g/spec.py": "from leadline.sources.g import gy\n"},
            "leadline.sources.g.spec imports leadline.sources.g.gy",
        ),
        ({"leadline/http.py": "from leadline.sources.g import api\n"}, "leadline.http imports leadline.sources.g.api"),
        (
            {"leadline/reports.py": "from leadline.sources.g import api\n"},
            "leadline.reports imports leadline.sources.g.api",
        ),
        (
            {"leadline/runner.py": "from leadline.sources.g import api\n"},
            "leadline.runner imports leadline.sources.g.api",
        ),
        (
            {"leadline/runner.py": "from leadline.sources.x import spec\n", **PACKAGE_X},
            "leadline.runner imports leadline.sources.x",
        ),
        (
            {"leadline/cli.py": "from leadline.sources.x import spec\n", **PACKAGE_X},
            "leadline.cli imports leadline.sources.x.spec",
        ),
        ({"leadline/cli.py": "from leadline.sources.g import api\n"}, "leadline.cli imports leadline.sources.g.api"),
        (
            {"leadline/rowdiff.py": "from leadline.sources.x import spec\n", **PACKAGE_X},
            "leadline.rowdiff imports leadline.sources.x",
        ),
        (
            {"leadline/rowdiff.py": "from leadline.sources.g.gx import spec\n"},
            "leadline.rowdiff imports leadline.sources.g.gx",
        ),
    ],
)
def test_each_package_rule_catches_its_violation(tmp_path: Path, files: dict[str, str], expected: str) -> None:
    assert expected in violations(tree(tmp_path, {**PACKAGE_X, **GROUP_G, **files}))


def test_allowed_package_imports_pass(tmp_path: Path) -> None:
    files = {
        "leadline/sources/__init__.py": "",
        "leadline/sources/x/__init__.py": "from leadline.sources import lazy\n",
        "leadline/sources/x/spec.py": "from leadline import config\nfrom . import helper\nfrom .helper import H\n",
        "leadline/sources/x/helper.py": "",
        "leadline/sources/x/source.py": (
            "import json\nfrom leadline import store\nfrom leadline.context import Context\n"
            "from . import spec\nfrom .spec import NAME\nfrom .deep import more\n"
        ),
        "leadline/sources/x/deep/__init__.py": "",
        "leadline/sources/x/deep/more.py": (
            "from leadline import http\nfrom .. import spec\nfrom leadline.sources.x import helper\n"
        ),
        "leadline/sources/g/__init__.py": "GROUP = True\nfrom leadline.sources import lazy\n",
        "leadline/sources/g/api.py": "from leadline import config, context, http\nfrom . import extra\n",
        "leadline/sources/g/extra.py": "from leadline.sources.g import spec\n",
        "leadline/sources/g/spec.py": "from leadline import config\n",
        "leadline/sources/g/gx/__init__.py": "from leadline.sources import lazy\n",
        "leadline/sources/g/gx/spec.py": (
            "from leadline import config\nfrom leadline.config import Config\n"
            "from leadline.sources.g.spec import repo_problems\nfrom . import helper\n"
        ),
        "leadline/sources/g/gx/helper.py": "",
        "leadline/sources/g/gx/source.py": (
            "import json\nfrom leadline import store\nfrom leadline.sources.g import api as g\n"
            "from leadline.sources.g import api, spec\nfrom leadline.sources.g.api import API\n"
            "from leadline.context import Context\nfrom . import spec as own\nfrom .. import api as sibling_api\n"
        ),
        "leadline/sources/g/gx/deep/__init__.py": "",
        "leadline/sources/g/gx/deep/more.py": (
            "from leadline.sources.g import api\nfrom leadline.sources.g.gx import helper\n"
        ),
        "leadline/sources/g/gy/__init__.py": "",
        "leadline/runner.py": "from leadline import sources\n",
        "leadline/cli.py": "from leadline import runner\nfrom leadline.sources import discover\n",
    }
    assert violations(tree(tmp_path, files)) == []


def test_a_groups_source_is_told_from_the_groups_shared_modules_by_its_folder(tmp_path: Path) -> None:
    files = {
        **GROUP_G,
        "leadline/sources/g/gx/source.py": "from leadline.sources.g import api, gy\n",
    }

    assert violations(tree(tmp_path, files)) == ["leadline.sources.g.gx.source imports leadline.sources.g.gy"]


def test_a_packages_tests_folder_is_not_runtime_and_is_not_scanned(tmp_path: Path) -> None:
    files = {
        "leadline/sources/x/__init__.py": "",
        "leadline/sources/x/tests/__init__.py": "",
        "leadline/sources/x/tests/test_x.py": (
            "import pytest\nfrom scenarios import Scenario\nfrom leadline.sources import b\n"
        ),
    }
    assert violations(tree(tmp_path, files)) == []


@pytest.mark.skipif(not hasattr(sys, "stdlib_module_names"), reason="needs Python 3.10+")
def test_a_third_party_import_is_caught(tmp_path: Path) -> None:
    found = violations(tree(tmp_path, {"leadline/store.py": "import requests\n"}))

    assert "leadline.store imports non-stdlib requests" in found


# A source writes good rows through store.write_good, store.write_window or store.recompute, which record its
# corrections (ADR: every correction is recorded), and failure rows through store.write_failure; never through the
# raw write paths, which record nothing.
# ctx.record_correction is store's to call: a source calling it too would record a correction twice.
RAW_WRITES = {"upsert", "replace_window", "record_correction"}


def raw_writes(src: Path) -> list[str]:
    """Each use of a raw write path, or of recording a correction, in a source module."""
    found: list[str] = []
    sources = src / "leadline" / "sources"
    for path in sorted(sources.rglob("*.py")):
        if "tests" in path.parts or not (path.parent == sources or path.name == "source.py"):
            continue
        name = path.parent.name if path.name == "source.py" else path.stem  # its package folder's name, or the module's
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr in RAW_WRITES:
                found.append(f"{name} uses {node.attr}")
            elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "store":
                found += [f"{name} imports {a.name}" for a in node.names if a.name in RAW_WRITES]
    return found


def test_sources_write_rows_only_through_the_paths_that_record_corrections() -> None:
    assert raw_writes(SRC) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("from leadline import store\nstore.upsert(p, r, n)\n", "a uses upsert"),
        ("from leadline import store\nstore.replace_window(p, r, n)\n", "a uses replace_window"),
        ("from leadline.store import upsert\n", "a imports upsert"),
        ("from ..store import replace_window\n", "a imports replace_window"),
        ("ctx.record_correction('a', 'f', 'd', {}, {})\n", "a uses record_correction"),
    ],
)
def test_a_raw_write_in_a_source_is_caught(tmp_path: Path, text: str, expected: str) -> None:
    assert expected in raw_writes(tree(tmp_path, {"leadline/sources/a.py": text}))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("from leadline import store\nstore.upsert(p, r, n)\n", "a uses upsert"),
        ("from ..store import replace_window\n", "a imports replace_window"),
        ("ctx.record_correction('a', 'f', 'd', {}, {})\n", "a uses record_correction"),
    ],
)
def test_a_raw_write_in_a_source_packages_source_module_is_caught(tmp_path: Path, text: str, expected: str) -> None:
    files = {"leadline/sources/a/__init__.py": "", "leadline/sources/a/source.py": text}

    assert expected in raw_writes(tree(tmp_path, files))


def test_only_a_packages_source_module_is_scanned_for_raw_writes(tmp_path: Path) -> None:
    files = {
        "leadline/sources/a/__init__.py": "",
        "leadline/sources/a/spec.py": "store.upsert(p, r, n)\n",
        "leadline/sources/a/tests/__init__.py": "",
        "leadline/sources/a/tests/test_a.py": "store.upsert(p, r, n)\n",
    }

    assert raw_writes(tree(tmp_path, files)) == []


# The test builders are one module per platform: a builders module imports the shared `common` module (tests/
# scenarios/builders/common.py) and its own platform's builders, and no other platform's, so removing a platform
# removes its builder module and nothing else. They are tests/scenarios/builders/<platform>.py, and tests/builders.py
# (or tests/builders/*.py) in a platform's package, a group or a single source.
BUILDERS = Path(__file__).parent / "scenarios" / "builders"
FLAT = "scenarios.builders."


def import_names(path: Path) -> list[str]:
    """Every module a file imports, and every name a from-import takes from a module, as a dotted name."""
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            found += [f"{node.module}.{a.name}" for a in node.names] + [node.module or ""]
        elif isinstance(node, ast.Import):
            found += [a.name for a in node.names]
    return found


def builder_crossings(folder: Path) -> list[str]:
    """Each import in a flat builder module of another builder module other than `common`."""
    found: list[str] = []
    for path in sorted(folder.glob("*.py")):
        found += [
            f"{path.stem} imports {n}" for n in import_names(path) if n.startswith(FLAT) and n.split(".")[2] != "common"
        ]
    return sorted(set(found))


def package_builder_crossings(src: Path) -> list[str]:
    """Each import in a package's builders of a flat builder module other than `common`, or of the builders of
    another package."""
    found: list[str] = []
    for path in sorted((src / "leadline").rglob("builders*")):
        if path.parent.name != "tests":
            continue
        for module in [path] if path.suffix == ".py" else sorted(path.glob("*.py")):
            relative = module.relative_to(src).parts
            owner = relative[: relative.index("tests")]
            for name in import_names(module):
                parts = name.split(".")
                crossing = name.startswith(FLAT) and parts[2] != "common"
                if parts[:2] == ["leadline", "sources"] and "tests" in parts:
                    k = parts.index("tests")
                    crossing = crossing or (
                        parts[k + 1 : k + 2] != [] and parts[k + 1].startswith("builders") and tuple(parts[:k]) != owner
                    )
                if crossing:
                    found.append(f"{'.'.join(owner)} imports {name}")
    return sorted(set(found))


def test_each_builder_module_imports_only_the_shared_one() -> None:
    assert builder_crossings(BUILDERS) == []
    assert package_builder_crossings(SRC) == []


def test_a_builder_importing_another_platforms_module_is_caught(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("from scenarios.builders.github import github_repo\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from scenarios.builders.common import Json\n", encoding="utf-8")

    assert builder_crossings(tmp_path) == [
        "a imports scenarios.builders.github",
        "a imports scenarios.builders.github.github_repo",
    ]


def test_a_package_builder_may_import_common_and_its_own_platforms_builders_only(tmp_path: Path) -> None:
    files = {
        "leadline/sources/g/tests/builders.py": (
            "from scenarios.builders.common import Json\nfrom leadline.sources.g.tests import builders_more\n"
            "from leadline.sources.g.tests.builders_more import X\nimport json\n"
        ),
        "leadline/sources/h/tests/builders/__init__.py": "from scenarios.builders import common\n",
        "leadline/sources/h/tests/builders/more.py": "from leadline.sources.h.tests.builders import more\n",
        "leadline/sources/x/tests/builders.py": "from scenarios.builders.common import Json\n",
    }
    assert package_builder_crossings(tree(tmp_path, files)) == []


@pytest.mark.parametrize(
    "line",
    [
        "from scenarios.builders.github import github_repo",
        "from scenarios.builders import github",
        "from leadline.sources.h.tests import builders",
        "from leadline.sources.h.tests.builders import X",
        "import leadline.sources.h.tests.builders",
    ],
)
def test_a_package_builder_importing_another_platforms_builders_is_caught(tmp_path: Path, line: str) -> None:
    files = {"leadline/sources/g/tests/builders.py": line + "\n", "leadline/sources/h/tests/builders.py": ""}

    assert package_builder_crossings(tree(tmp_path, files)) != []
