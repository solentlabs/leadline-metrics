"""The response builders make what the APIs make: each built response has the shape of the real ones.

For each kind of response, the builders' output is compared with every response of that kind in the
private capture. Each built object has the keys of some real object at that place; each value has a
type the API gives for that field; every part of the real responses (field, list item, map value) is
in some built one; and the built headers are headers the API sends, with Link in the API's own form.
Values aren't compared: they are made up.

Where the capture holds no example (a list that is empty in every real response, or a field that is
null in every one), the shape can't be checked; each kind names those places, and says when the
capture has no Link header of its kind, so a gap is a decision in the code, not a silent pass. Needs
the capture, so it is marked oracle.
"""

from __future__ import annotations

import functools
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

import local_settings
import pytest
from shape_kind import Kind, package_kinds

pytestmark = pytest.mark.oracle

KINDS = package_kinds()  # each package's own (tests/shapes.py)


@functools.lru_cache(maxsize=None)
def capture() -> tuple[Any, ...]:
    """Every exchange in the capture, read once."""
    folder = local_settings.path("capture")
    return tuple(json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json")))


def captured(kind: Kind) -> list[dict[str, Any]]:
    """The captured responses of this kind that succeeded."""
    return [e["response"] for e in capture() if kind.matches(e["request"]) and e["response"].get("status") == 200]


def shape_problems(built: Any, reals: list[Any], path: str, kind: Kind, unverified: set[str]) -> list[str]:
    """Where `built` differs in shape from every real value at `path`; paths with no real example go to `unverified`."""
    if all(r is None for r in reals) and built is not None:
        unverified.add(path)
        return []
    if isinstance(built, dict) and path in kind.maps:
        values = [v for r in reals if isinstance(r, dict) for v in r.values()]
        return [p for v in built.values() for p in shape_problems(v, values, f"{path}{{}}", kind, unverified)]
    if isinstance(built, dict):
        if not any(isinstance(r, dict) and set(r) == set(built) for r in reals):
            nearest = min((r for r in reals if isinstance(r, dict)), key=lambda r: len(set(r) ^ set(built)), default={})
            extra, missing = sorted(set(built) - set(nearest)), sorted(set(nearest) - set(built))
            return [f"{path}: keys differ from the nearest real object: extra {extra}, missing {missing}"]
        # The keys match one real object; each value is held to that field in every real object that has it.
        return [
            p
            for k, v in built.items()
            for p in shape_problems(
                v, [r[k] for r in reals if isinstance(r, dict) and k in r], f"{path}.{k}", kind, unverified
            )
        ]
    types = sorted({type(r).__name__ for r in reals})
    if isinstance(built, list):
        if "list" not in types:
            return [f"{path}: a list where the API gives {types}"]
        items = [x for r in reals if isinstance(r, list) for x in r]
        if built and not items:
            unverified.add(f"{path}[]")
            return []
        return [p for x in built for p in shape_problems(x, items, f"{path}[]", kind, unverified)]
    if type(built).__name__ not in types:
        return [f"{path}: {type(built).__name__} where the API gives {types}"]
    return []


def parts(value: Any, path: str, kind: Kind) -> set[str]:
    """Every path in `value`: object fields, list items (`[]`) and map values (`{}`)."""
    found = {path}
    if isinstance(value, dict):
        for key, v in value.items():
            found |= parts(v, f"{path}{{}}" if path in kind.maps else f"{path}.{key}", kind)
    elif isinstance(value, list):
        for v in value:
            found |= parts(v, f"{path}[]", kind)
    return found


def link_shape(value: str) -> list[Any]:
    """A Link header's form: each target's rel, path (numbers as N) and query parameter names."""
    targets = re.findall(r'<([^>]+)>; rel="(\w+)"', value)
    return [
        (rel, re.sub(r"\d+", "N", urlsplit(url).path), sorted(parse_qs(urlsplit(url).query))) for url, rel in targets
    ]


@pytest.mark.parametrize("kind", KINDS, ids=[k.name for k in KINDS])
def test_built_responses_have_the_shape_of_the_real_ones(kind: Kind) -> None:
    reals = captured(kind)
    assert reals, f"the capture holds no {kind.name} response"
    unverified: set[str] = set()

    real_bodies = [json.loads(r["body"]) for r in reals]
    built_bodies = [json.loads(built["response"]["body"]) for built in kind.built]

    problems = [p for body in built_bodies for p in shape_problems(body, real_bodies, "$", kind, unverified)]
    assert problems == []
    never_built = set().union(*(parts(r, "$", kind) for r in real_bodies)) - set().union(
        *(parts(body, "$", kind) for body in built_bodies)
    )
    assert never_built == set(), "parts of the real responses no built response has"
    assert unverified == set(kind.unverified), "a place the capture has no example of"


@pytest.mark.parametrize("kind", KINDS, ids=[k.name for k in KINDS])
def test_built_headers_are_headers_the_api_sends(kind: Kind) -> None:
    real_headers = [{k.lower(): v for k, v in r.get("headers", {}).items()} for r in captured(kind)]
    real_links = [link_shape(h["link"]) for h in real_headers if "link" in h]

    built_headers = [{k.lower(): v for k, v in built["response"].get("headers", {}).items()} for built in kind.built]
    if kind.unverified_link:
        assert real_links == [], "the capture has a Link of this kind now: hold the built ones to it"
        assert any("link" in h for h in built_headers), "a kind whose Link is unverified builds one"
        built_headers = [{k: v for k, v in h.items() if k != "link"} for h in built_headers]

    assert set().union(*built_headers) <= set().union(*real_headers)
    assert any("link" in h for h in built_headers) == bool(real_links), "Link where the API sends it"
    assert [
        link_shape(h["link"]) for h in built_headers if "link" in h if link_shape(h["link"]) not in real_links
    ] == []
