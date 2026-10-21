"""The source registry: every package here that keeps the contract, found by `discover`.

A source is a package here, or inside a platform group (a package that sets `GROUP = True`); there is no list to add
it to (docs/ADDING_A_SOURCE.md).

A source has two halves: its `spec` (pure declarations, which the report path reads) and its `source` (collection).
`discover` loads both, for a run; `discover(specs_only=True)` loads each `spec` alone, for `leadline report`, so the
capture code (and HTTP) is never imported. A package's `__init__` is therefore light: `lazy` gives it its public names
from the two modules, on first use.
"""

# cspell:ignore ispkg
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Callable, Generic, Literal, Protocol, Sequence, TypeVar, overload

from leadline import health, reports
from leadline.config import Config, Section
from leadline.context import Context, Finding


class Spec(Protocol):
    """A source's spec module: what the report path reads of it (docs/ARCHITECTURE.md, "Sources")."""

    NAME: str  # its key under "sources" in the config, and its folder's name
    DEFAULT_FILE: str  # the name template of the file it writes (`config.default_file`)

    def validate(self, cfg: Any) -> list[str]:
        """Every problem with its config; each starts with its path there (`[1].repo: `, `.packages: `, `: `)."""
        ...

    def files(self, cfg: Any) -> dict[str, str]:
        """Each file a valid config writes, keyed by the path of its entry (`[1]`, `.packages[0]`, `""`)."""
        ...

    def summary(self, cfg: Any) -> list[Section]:
        """Its sections of the default summary and trends: one per entry (`config.entry_summary`)."""
        ...


class Source(Spec, Protocol):
    """A source package: its spec and its capture (docs/ARCHITECTURE.md, "Sources").

    A source that asks for a retry pass (pypi) also defines `retry(ctx, cfg)`, which the runner calls
    after every source has collected; the others leave it out, so it isn't a member here.
    """

    ENV: tuple[str, ...]  # the credential variables it needs; unset means skipped
    LAGGING: bool  # its newest row is normally yesterday's

    def collect(self, ctx: Context, cfg: Any) -> None: ...

    def check(self, ctx: Context, cfg: Any) -> list[Finding]:
        """Its config against the live API, without writing rows; API trouble is a finding, never a raise."""
        ...


# The members `discover` checks: the Source protocol's, by kind. A test holds the two in step.
SPEC_DATA: dict[str, type] = {"NAME": str, "DEFAULT_FILE": str}
SPEC_CALLABLES = ("validate", "files", "summary")
DATA_MEMBERS: dict[str, type] = {**SPEC_DATA, "ENV": tuple, "LAGGING": bool}
CALLABLE_MEMBERS = ("validate", "files", "collect", "check", "summary")

S = TypeVar("S", bound=Spec, covariant=True)


@dataclass
class Registry(Generic[S]):
    """The sources a run can use (`Registry[Source]`), or the specs a report can use (`Registry[Spec]`), by name; and
    the modules that failed to load, by name, with why."""

    sources: dict[str, S] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


def lazy(package: str, parts: Sequence[str] = ("spec", "source")) -> Callable[[str], Any]:
    """A package's module `__getattr__`: a public name its `__init__` lacks comes from its modules `parts`, in order
    (a source's `spec` then `source`; a group's `spec` then `api`), imported on first use, so importing the package
    (to reach its `spec`) loads no capture code."""

    def member(name: str) -> Any:
        if not name.startswith("_"):
            for part in parts:
                module = importlib.import_module(f"{package}.{part}")
                if hasattr(module, name):
                    return getattr(module, name)
        raise AttributeError(f"module {package!r} has no attribute {name!r}")

    return member


@overload
def discover(
    package: str = ..., path: Sequence[str] | None = ..., *, specs_only: Literal[False] = ...
) -> Registry[Source]: ...
@overload
def discover(package: str = ..., path: Sequence[str] | None = ..., *, specs_only: Literal[True]) -> Registry[Spec]: ...
def discover(package: str = __name__, path: Sequence[str] | None = None, *, specs_only: bool = False) -> Any:
    """Every source in `package` (this one), in name order; `path` is the folders to look in (the package's own).

    With `specs_only`, each source is its `spec` module alone, held to the Spec members only: nothing of its
    capture is imported (the report path).

    A source is a package here, or a package inside a platform group: a package that sets `GROUP = True`, has no
    NAME and is never a source itself. A source is keyed by its own name, which is its folder's name, so a name
    found twice (in two groups) fails both. A module, or a package whose name starts with `_`, is not a source,
    and neither is a `tests` folder inside a group. Each import and contract check is isolated: one that
    raises goes under `failed` as `<ExceptionType>: <message>`, and the others still load. A group that raises
    fails each source package in it, under its own name, and itself.
    """
    found: Registry[Any] = Registry()
    where: dict[str, list[str]] = {}
    folders = importlib.import_module(package).__path__ if path is None else path
    _walk(package, folders, "", found, where, specs_only)
    for name, places in where.items():
        if len(places) > 1:
            found.sources.pop(name, None)
            found.failed[name] = f"ValueError: found twice: {', '.join(places)}"
    found.sources = dict(sorted(found.sources.items()))
    return found


def _walk(
    package: str, path: Sequence[str], within: str, found: Registry[Any], where: dict[str, list[str]], specs_only: bool
) -> None:
    """Load each source in the folders `path` of `package` into `found`; `within` is the groups above, as `g.`."""
    for info in sorted(pkgutil.iter_modules(path), key=lambda i: i.name):
        name = info.name
        if name.startswith("_") or not info.ispkg or (within and name == "tests"):
            continue
        here = f"{within}{name}"
        try:
            module = importlib.import_module(f"{package}.{name}")
            if vars(module).get("GROUP") is True:  # not getattr: a source package would load its capture code
                _walk(f"{package}.{name}", list(module.__path__), f"{here}.", found, where, specs_only)
                continue
            if specs_only:
                module = importlib.import_module(f"{package}.{name}.spec")
            _check_contract(module, name, specs_only)
        except Exception as e:  # noqa: BLE001 (a source that cannot load is recorded under failed, never raised)
            reason = f"{type(e).__name__}: {e}"
            found.failed[name] = reason
            where.setdefault(name, []).append(here)
            _fail_children(name, [f"{folder}/{name}" for folder in path], reason, f"{here}.", found, where)
        else:
            found.sources[name] = module
            where.setdefault(name, []).append(here)


def _fail_children(
    name: str, folders: Sequence[str], reason: str, within: str, found: Registry[Any], where: dict[str, list[str]]
) -> None:
    """A package that failed to import and has no `spec` is probably a group: fail each package in it too."""
    children = list(pkgutil.iter_modules(folders))
    if any(c.name == "spec" for c in children):
        return
    for child in children:
        if child.ispkg and child.name != "tests" and not child.name.startswith("_"):
            where.setdefault(child.name, []).append(f"{within}{child.name}")
            found.failed[child.name] = f"inside {name}, which failed to load: {reason}"


def _check_contract(module: ModuleType, name: str, specs_only: bool = False) -> None:
    """Raise unless `module` has every member of the Source protocol (the Spec's, with `specs_only`), of its kind, and
    its NAME is `name`."""
    data, calls = (SPEC_DATA, SPEC_CALLABLES) if specs_only else (DATA_MEMBERS, CALLABLE_MEMBERS)
    wrong = [m for m, kind in data.items() if not isinstance(getattr(module, m, None), kind)]
    wrong += [m for m in calls if not callable(getattr(module, m, None))]
    if wrong:
        raise TypeError(f"missing or wrong members: {', '.join(wrong)}")
    if name != module.NAME:
        raise ValueError(f"NAME {module.NAME!r} differs from its module name {name!r}")


def summary_sections(config: Config, registry: Registry[Spec]) -> list[Section]:
    """Each configured source's summary() sections, in config order: the default summary's, and trends'.

    A source that failed to load has none."""
    return [
        section
        for name, cfg in config.sources.items()
        if name in registry.sources
        for section in registry.sources[name].summary(cfg)
    ]


def default_reports(config: Config, registry: Registry[Spec]) -> list[dict[str, Any]]:
    """The default summary (reports.default): each configured source's sections, in config order, under the
    run's health lines. A run writes it when the config has no `reports`."""
    return reports.default(summary_sections(config, registry), health.FILE)
