"""github_releases: the script's rows on its scenarios, and leadline's side of each decided divergence.

The rows are compared with the goldens through tests/port.py; the scenarios where leadline differs by
a decision are listed in scenarios.py (DIVERGENCES) and asserted here instead.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import port
import pytest
import scenarios
from row_files import files_in
from scenarios.builders.common import exchange, failed, retried, thrice

from leadline import trends
from leadline.config import Hide
from leadline.context import Critical, Finding
from leadline.http import SavedResponses
from leadline.sources.github import github_releases
from leadline.sources.github.github_releases.tests import scenarios as gr
from leadline.sources.github.tests.builders import GITHUB_API, github_numbered_pages, github_release, github_repo

NAME = "github_releases"
CONFIG = gr.LEADLINE_CONFIG
FILE = gr.FILE
REPO = gr.REPO
URL = gr.URL
EARLIER = gr.EARLIER[FILE]
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it


# --- the contract ---


def test_it_has_its_contract() -> None:
    assert github_releases.NAME == NAME
    assert github_releases.ENV == ("GITHUB_TOKEN",)
    assert github_releases.LAGGING is False


def test_a_valid_config_has_no_problems() -> None:
    assert github_releases.validate([{"repo": "a/b"}, {"repo": "a/c", "file": "x-{repo_name}.jsonl"}]) == []


@pytest.mark.parametrize(
    ("cfg", "problems"),
    [
        ({"repo": "a/b"}, [": must be a list of entries"]),
        (["a/b"], ["[0]: must be an object"]),
        ([{}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a"}], ["[0].repo: must be owner/name"]),
        ([{"repo": "a/b/c"}], ["[0].repo: must be owner/name"]),
        ([{"repo": 5}], ["[0].repo: must be owner/name"]),
        (
            [{"repo": "a/b", "file": "x-{label}.jsonl"}],
            ["[0].file: 'x-{label}.jsonl' uses {label}, which is not one of: repo, repo_name"],
        ),
        (
            [{"repo": "a/b", "file": "x-{repo}.jsonl"}],
            ["[0].file: 'x-{repo}.jsonl' must fill to a plain *.jsonl name, not 'x-a/b.jsonl'"],
        ),
    ],
    ids=[
        "not a list",
        "an entry not an object",
        "no repo",
        "a repo without its owner",
        "a repo with two slashes",
        "a repo not a string",
        "a bad file",
        "a file the repo can't name",
    ],
)
def test_validate_names_each_problem_by_its_path(cfg: Any, problems: list[str]) -> None:
    assert github_releases.validate(cfg) == problems


def test_files_are_the_default_or_the_template_of_each_entry() -> None:
    cfg = [{"repo": "a/one"}, {"repo": "a/two", "file": "releases-{repo_name}.jsonl"}]

    assert github_releases.files(cfg) == {"[0]": "github-releases-one.jsonl", "[1]": "releases-two.jsonl"}


def test_it_is_skipped_without_a_token(tmp_path: Path) -> None:
    done = port.run(github_releases, CONFIG, port.scenario(NAME, "skips_without_a_token"), tmp_path)

    assert "SKIP: github_releases (GITHUB_TOKEN not set)" in done.log
    assert (done.status, done.rows) == (0, {})


def test_it_sends_the_token_and_asks_for_the_github_json(tmp_path: Path) -> None:
    sent: list[Any] = []
    transport = SavedResponses(gr.releases([]))

    def recording(request: Any, timeout: float) -> Any:
        sent.append(request)
        return transport(request, timeout)

    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    ctx.http._transport = recording  # to see the request headers
    with port.credentials({"GITHUB_TOKEN": "t0ken"}, github_releases.ENV):
        github_releases.collect(ctx, CONFIG)

    assert sent[0].get_header("Authorization") == "Bearer t0ken"
    assert sent[0].get_header("Accept") == "application/vnd.github+json"


# --- the script's rows ---


@pytest.mark.parametrize("scenario", port.golden_scenarios(NAME), ids=lambda s: s.name)
def test_rows_match_the_scripts(scenario: Any, tmp_path: Path) -> None:
    done = port.run(github_releases, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, scenario.name, tmp_path) == []
    assert (done.status, done.ctx.critical) == (0, [])


def test_the_divergences_are_the_decided_ones() -> None:
    assert sorted(port.divergences(NAME)) == [
        "a_body_not_a_list_writes_nothing",
        "failed_fetch_writes_nothing",
        "missing_fields_take_the_defaults",
        "unparseable_body_writes_nothing",
    ]


def test_a_release_without_its_flags_is_not_filled_with_defaults(tmp_path: Path) -> None:
    """The script's scenario: a bare release and asset take false and 0; leadline records what is missing."""
    done = port.run(github_releases, CONFIG, port.scenario(NAME, "missing_fields_take_the_defaults"), tmp_path)

    reason = "response is missing prerelease, draft, published_at in a release"
    assert done.rows == {FILE: failure_row("parse_failed", reason)}


def test_only_the_first_page_is_read_as_the_script_reads_it(tmp_path: Path) -> None:
    """The script asks for 100 releases and no more; a repo with more has its oldest left out."""
    many = [github_release(REPO, f"v0.{n}.0", published_at="2026-01-01T00:00:00Z") for n in range(101, 0, -1)]
    first = github_numbered_pages(REPO, "releases", "per_page=100", many, per_page=100, numbered=False)
    scenario = replace(port.scenario(NAME, "a_repository_without_releases"), responses=first)

    done = port.run(github_releases, CONFIG, scenario, tmp_path)

    assert done.rows[FILE].count('"tag": ') == 100
    assert '"tag": "v0.1.0"' not in done.rows[FILE]


# --- decided divergences: ADR "A failure is recorded as a failure" ---


def failure_row(status: str, reason: str, http_code: str | None = None) -> str:
    code = f', "http_code": "{http_code}"' if http_code is not None else ""
    return (
        f'{{"date": "2026-09-23", "repo": "{REPO}", "status": "{status}"{code}, "reason": "{reason}", '
        f'"captured_at": "{NOW}"}}\n'
    )


def test_a_failed_fetch_is_a_fetch_failed_row(tmp_path: Path) -> None:
    scenario = port.scenario(NAME, "failed_fetch_writes_nothing")
    scenario = replace(scenario, responses=retried(scenario.responses))

    done = port.run(github_releases, CONFIG, scenario, tmp_path)

    assert done.rows == {
        FILE: EARLIER + failure_row("fetch_failed", "releases request failed (HTTP 500, empty body)", "500")
    }
    assert (done.status, done.ctx.critical) == (0, [])  # recoverable: the next run writes the day


def test_an_unparseable_body_is_a_parse_failed_row(tmp_path: Path) -> None:
    done = port.run(github_releases, CONFIG, port.scenario(NAME, "unparseable_body_writes_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "response was not JSON")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_an_object_in_place_of_the_list_is_a_parse_failed_row(tmp_path: Path) -> None:
    done = port.run(github_releases, CONFIG, port.scenario(NAME, "a_body_not_a_list_writes_nothing"), tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "response had an unexpected shape")}
    assert (done.status, done.ctx.critical) == (0, [])


RELEASE = {"tag_name": "v1.0.0", "prerelease": False, "draft": False, "published_at": None}
ASSET = {"name": "a.zip", "download_count": 3, "size": 1}


@pytest.mark.parametrize(
    ("release", "reason"),
    [
        ({"tag_name": "v1.0.0", "assets": []}, "response is missing prerelease, draft, published_at in a release"),
        ({**RELEASE, "assets": None}, None),
        (
            {"prerelease": False, "draft": False, "published_at": None, "assets": []},
            "response is missing tag_name in a release",
        ),
        ({**RELEASE}, "response is missing assets in a release"),
        ({**RELEASE, "assets": [{"download_count": 3, "size": 1}]}, "response is missing name in an asset"),
        ({**RELEASE, "assets": [{"name": "a.zip", "size": 1}]}, "response is missing download_count in an asset"),
        ({**RELEASE, "assets": [{"name": "a.zip", "download_count": 3}]}, "response is missing size in an asset"),
    ],
    ids=[
        "no flags",
        "assets null",
        "no tag",
        "no assets",
        "an asset without a name",
        "an asset without its downloads",
        "an asset without its size",
    ],
)
def test_a_release_or_asset_without_its_fields_is_a_parse_failed_row_naming_them_not_defaults(
    tmp_path: Path, release: Any, reason: str | None
) -> None:
    """ADR: a failure is recorded as a failure. The script read a missing flag as false and a missing count as 0,
    which would count a draft's downloads or show none for an asset."""
    scenario = replace(port.scenario(NAME, "a_body_not_a_list_writes_nothing"), responses=[exchange(URL, [release])])

    done = port.run(github_releases, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", reason or "response had an unexpected shape")}


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        '"v1.0.0"',
        5,
        ["v1.0.0"],
        [{**RELEASE, "assets": None}],
        [{**RELEASE, "assets": {"name": "a.zip"}}],
        [{**RELEASE, "assets": ["a.zip"]}],
    ],
    ids=[
        "an empty object, which the script reads as no releases",
        "null",
        "a JSON string",
        "a number",
        "a release not an object",
        "null assets",
        "assets an object",
        "an asset not an object",
    ],
)
def test_any_answer_the_source_cant_use_is_a_parse_failed_row(tmp_path: Path, body: Any) -> None:
    scenario = replace(port.scenario(NAME, "a_body_not_a_list_writes_nothing"), responses=[exchange(URL, body)])

    done = port.run(github_releases, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + failure_row("parse_failed", "response had an unexpected shape")}


def test_a_failure_keeps_a_good_row(tmp_path: Path) -> None:
    """ADR: a failure never replaces good data."""
    good = f'{{"date": "2026-09-23", "repo": "{REPO}", "releases": []}}\n'
    base = port.scenario(NAME, "failed_fetch_writes_nothing")
    scenario = replace(base, responses=retried(base.responses), seed={FILE: EARLIER + good})

    done = port.run(github_releases, CONFIG, scenario, tmp_path)

    assert done.rows == {FILE: EARLIER + good}
    assert f"WARN: kept the good row for 2026-09-23 in {FILE}; fetch_failed not recorded" in done.log


def test_a_good_row_replaces_a_failure_row(tmp_path: Path) -> None:
    failure = f'{{"date": "2026-09-23", "repo": "{REPO}", "status": "fetch_failed", "http_code": "500"}}\n'
    scenario = replace(port.scenario(NAME, "a_repository_without_releases"), seed={FILE: failure})

    port.run(github_releases, CONFIG, scenario, tmp_path)

    assert port.golden_differences(NAME, "a_repository_without_releases", tmp_path) == []


# --- failures: recoverable or not ---


@pytest.mark.parametrize("code", [401, 403, 404])
def test_an_error_waiting_cant_fix_is_critical_at_once(tmp_path: Path, code: int) -> None:
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=[exchange(URL, "", status=code)])

    done = port.run(github_releases, CONFIG, scenario, tmp_path)

    assert done.rows == {
        FILE: EARLIER + failure_row("fetch_failed", f"releases request failed (HTTP {code}, empty body)", str(code))
    }
    assert done.ctx.critical == [Critical(NAME, FILE, f"{REPO}: HTTP {code} from GitHub's releases API; empty body")]
    assert done.status == 1


@pytest.mark.parametrize(
    ("code", "headers"),
    [(403, {"X-RateLimit-Remaining": "0"}), (403, {"Retry-After": "60"}), (429, {})],
    ids=["403 none remaining", "403 retry-after", "429"],
)
def test_a_rate_limit_is_retried_and_is_recoverable(tmp_path: Path, code: int, headers: dict[str, str]) -> None:
    limited = exchange(URL, '{"message":"API rate limit exceeded"}', status=code, headers=headers)
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=thrice(limited))

    done = port.run(github_releases, CONFIG, scenario, tmp_path)

    assert done.rows == {
        FILE: EARLIER + failure_row("fetch_failed", f"releases request failed (HTTP {code}, rate limited)", str(code))
    }
    assert (done.status, done.ctx.critical, done.sleeps) == (0, [], [10, 20])


def test_no_connection_is_recorded_as_000_and_is_recoverable(tmp_path: Path) -> None:
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=thrice(failed(URL)))

    done = port.run(github_releases, CONFIG, scenario, tmp_path)

    assert done.rows == {
        FILE: EARLIER + failure_row("fetch_failed", "releases request failed (HTTP 000, connection refused)", "000")
    }
    assert (done.status, done.sleeps) == (0, [10, 20])  # three tries


def test_a_bot_challenge_is_named_in_the_failed_row(tmp_path: Path) -> None:
    challenge = exchange(URL, "<html><title>Just a moment...</title></html>", status=403)
    scenario = replace(port.scenario(NAME, "failed_fetch_writes_nothing"), responses=thrice(challenge))

    done = port.run(github_releases, CONFIG, scenario, tmp_path)

    reason = "releases request failed (HTTP 403, bot challenge)"
    assert done.rows == {FILE: EARLIER + failure_row("fetch_failed", reason, "403")}
    assert (done.status, done.ctx.critical) == (0, [])


def test_a_failing_entry_does_not_stop_the_next(tmp_path: Path) -> None:
    other = github_numbered_pages("a/other", "releases", "per_page=100", [], per_page=100, numbered=False)
    scenario = replace(
        port.scenario(NAME, "failed_fetch_writes_nothing"), responses=[*thrice(exchange(URL, "", status=502)), *other]
    )

    done = port.run(github_releases, [*CONFIG, {"repo": "a/other"}], scenario, tmp_path)

    assert done.rows[FILE] == EARLIER + failure_row(
        "fetch_failed", "releases request failed (HTTP 502, empty body)", "502"
    )
    assert '"repo": "a/other", "releases": []' in done.rows["github-releases-other.jsonl"]


# --- check() ---

REPO_URL = f"{GITHUB_API}/repos/{REPO}"


def check(tmp_path: Path, cfg: Any, *responses: Any) -> list[Finding]:
    transport = SavedResponses(responses)
    ctx = port.context(tmp_path, transport, port.local_time(scenarios.NOW, "UTC"), [])
    with port.credentials(scenarios.CREDENTIALS, github_releases.ENV):
        findings = github_releases.check(ctx, cfg)
    assert transport.unmatched == transport.never_served() == []
    assert list(tmp_path.iterdir()) == []  # a check writes no rows
    return findings


def test_check_finds_the_repo(tmp_path: Path) -> None:
    assert check(tmp_path, CONFIG, exchange(REPO_URL, github_repo(REPO))) == [Finding("ok", f"{REPO}: the repo exists")]


@pytest.mark.parametrize(
    ("responses", "finding"),
    [
        (
            [exchange(REPO_URL, '{"message":"Not Found"}', status=404)],
            Finding("error", f"{REPO}: HTTP 404: the repo doesn't exist, or the token can't read it"),
        ),
        (
            [exchange(REPO_URL, "", status=401)],
            Finding("error", f"{REPO}: HTTP 401: the repo doesn't exist, or the token can't read it"),
        ),
        (
            thrice(exchange(REPO_URL, "", status=403, headers={"Retry-After": "60"})),
            Finding("warn", f"{REPO}: could not fetch the repo: HTTP 403, rate limited"),
        ),
        (thrice(exchange(REPO_URL, "", status=503)), Finding("warn", f"{REPO}: could not fetch the repo: HTTP 503")),
        (thrice(failed(REPO_URL)), Finding("warn", f"{REPO}: could not fetch the repo: HTTP 0")),
    ],
    ids=["not found", "rejected", "rate limited", "unavailable", "no connection"],
)
def test_check_reports_api_trouble_as_a_finding_never_a_crash(
    tmp_path: Path, responses: list[Any], finding: Finding
) -> None:
    assert check(tmp_path, CONFIG, *responses) == [finding]


def test_check_checks_every_entry(tmp_path: Path) -> None:
    other = f"{GITHUB_API}/repos/a/other"
    findings = check(
        tmp_path, [*CONFIG, {"repo": "a/other"}], exchange(REPO_URL, github_repo(REPO)), exchange(other, "", status=404)
    )

    assert [f.level for f in findings] == ["ok", "error"]


# --- the summary ---


def test_trends_chart_each_versions_downloads_as_lines() -> None:
    (section,) = github_releases.summary(CONFIG)

    assert section.shown == ()  # the default summary is as it was: its lines
    assert all(c.half for c in section.charts)  # side by side
    assert [(c.field, c.label, c.kind, c.by, c.lines, c.prereleases, c.since, c.bands) for c in section.charts] == [
        ("releases[].assets[].downloads", "Downloads by version", "breakdown", "tag", True, False, "", "line"),
        (
            "releases[].assets[].downloads",
            "Downloads since release",
            "breakdown",
            "tag",
            True,
            False,
            "published_at",
            "release",
        ),
    ]


def test_the_bands_of_an_entry_band_the_lines() -> None:
    line, release = github_releases.summary([{"repo": "a/b", "bands": "line"}, {"repo": "a/c", "bands": "release"}])

    assert [c.bands for c in line.charts] == ["line", "release"]  # the chart by days since release is always exact
    assert [c.bands for c in release.charts] == ["release", "release"]


def test_a_valid_exclude_has_no_problems() -> None:
    assert (
        github_releases.validate([{"repo": "a/b", "exclude": []}, {"repo": "a/c", "exclude": ["3.13.1", "v3.0"]}]) == []
    )


@pytest.mark.parametrize(
    "exclude", ["3.13.1", {"a": 1}, [3], ["ok", ""], [None]], ids=["a string", "an object", "a number", "empty", "none"]
)
def test_exclude_must_be_a_list_of_release_tags(exclude: Any) -> None:
    assert github_releases.validate([{"repo": "a/b", "exclude": exclude}]) == [
        "[0].exclude: must be a list of release tags (non-empty text)"
    ]


def test_the_exclude_of_an_entry_hides_those_releases_ignoring_a_leading_v() -> None:
    plain, hiding = github_releases.summary([{"repo": "a/b"}, {"repo": "a/c", "exclude": ["3.13.1", "v3.12.0"]}])

    assert plain.hide == ()
    assert hiding.hide == (Hide("releases", "tag", frozenset({"3.13.1", "3.12.0"})),)


def test_an_excluded_release_is_in_no_chart_but_the_file_keeps_it(tmp_path: Path) -> None:
    cfg = [{**CONFIG[0], "exclude": ["v1.0.0"], "bands": "release"}]
    (section,) = github_releases.summary(cfg)
    golden = scenarios.load_golden(NAME)["every_release_with_its_asset_downloads"]["rows"]
    for file, text in golden.items():
        (tmp_path / file).write_text(text, encoding="utf-8")
    before = {file: (tmp_path / file).read_text(encoding="utf-8") for file in golden}

    (built,) = trends.build(files_in(tmp_path), date(2026, 9, 24), [section], "health.jsonl")["sections"]

    assert built["fields"][0]["keys"] == ["0.9.0"]  # v1.0.0 is gone
    assert {file: (tmp_path / file).read_text(encoding="utf-8") for file in golden} == before


def test_valid_bands_have_no_problems() -> None:
    assert github_releases.validate([{"repo": "a/b", "bands": "line"}, {"repo": "a/c", "bands": "release"}]) == []


@pytest.mark.parametrize("bands", ["x", 3])
def test_bands_must_be_one_of_the_values(bands: Any) -> None:
    assert github_releases.validate([{"repo": "a/b", "bands": bands}]) == [
        f"[0].bands: must be one of line, release, not {bands!r}"
    ]


def test_the_charts_count_every_release_but_no_prerelease_from_the_golden_rows(tmp_path: Path) -> None:
    (section,) = github_releases.summary([{**CONFIG[0], "bands": "release"}])
    golden = scenarios.load_golden(NAME)["every_release_with_its_asset_downloads"]["rows"]
    for file, text in golden.items():
        (tmp_path / file).write_text(text, encoding="utf-8")

    (built,) = trends.build(files_in(tmp_path), date(2026, 9, 24), [section], "health.jsonl")["sections"]

    field, since = built["fields"]
    assert since["since"] is True
    assert since["keys"] == []  # the golden's releases were published before its one row
    assert (field["keys"], field["values"], field["prerelease"]) == (["0.9.0", "1.0.0"], [[0], [149]], [0])
    # v1.1.0-beta.2's 12 downloads are in no line


def test_its_summary_shows_its_fields_from_the_golden_rows(tmp_path: Path) -> None:
    assert port.summary(github_releases, CONFIG, "every_release_with_its_asset_downloads", tmp_path) == [
        "",
        "### github_releases: solentlabs/example",
        "",
        "- Latest release: v1.0.0, published 2026-08-01 (53 days ago), 149 downloads",
    ]
