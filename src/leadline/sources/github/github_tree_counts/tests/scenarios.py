"""github_tree_counts: counts of the paths in one repository's git tree that match a pattern.

The entry's `pattern` is a regex whose group 1 names a group and group 2 an item within it; the
counts are the matching files, the items (group and item), the groups, and items per group. The
script's collect_catalog_size is this with its own names for the counts (configs, models, vendors,
models_per_vendor): it reads the repository's whole tree at CATALOG_TREE_REF in one call, counts the
files matching CATALOG_MODEM_RE, and writes one row for the run's date into catalog-size.jsonl,
marking a truncated tree, whose counts are floors. A failed fetch, or a tree that doesn't parse,
writes nothing, and a body without a tree counts nothing.

Where leadline differs by a decision, the scenario is in DIVERGENCES, and
tests/test_github_tree_counts.py asserts leadline's side: each of those answers writes a failure row
(`fetch_failed` or `parse_failed`) for the run's date, where the script writes nothing, or zeros for a
body without a tree (ADR: a failure is recorded as a failure).

leadline's own `content` setting, which the script doesn't have, has two scenarios with reviewed rows (CONTENT_NAMES): a
night that reads each blob once, and a file whose nights lack the counts by status, which the history gives (ADR: a
tree's files are counted by what they say).
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from scenarios import Scenario
from scenarios.builders.common import Exchange, Json, exchange

from leadline.sources.github.tests.builders import (
    GITHUB_API,
    github_blob,
    github_blob_sha,
    github_commit,
    github_numbered_pages,
    github_tree,
)

REPO = "solentlabs/example"
CONFIG = {"GITHUB_REPO": REPO}
CALLS = ["collect_catalog_size"]
FILE = "catalog-size.jsonl"
TREE_URL = f"{GITHUB_API}/repos/{REPO}/git/trees/main?recursive=1"
EARLIER = '{"date": "2026-09-22", "models": 2, "configs": 3, "vendors": 1}\n'
PATTERN = r"catalog/modems/([^/]+)/([^/]+)/(modem[^/]*\.yaml)$"  # the script's CATALOG_MODEM_RE

# leadline's config for these scenarios: the script's repo, ref, pattern and row keys, writing its file.
LEADLINE_CONFIG = [
    {
        "repo": REPO,
        "pattern": PATTERN,
        "ref": "main",
        "keys": {"files": "configs", "items": "models", "groups": "vendors", "items_per_group": "models_per_vendor"},
        "file": FILE,
    }
]

# The scenarios where leadline's rows differ from the script's by a decision, and the decision.
DIVERGENCES = {
    "a_body_without_a_tree_or_truncated_counts_nothing": "ADR: a failure is recorded as a failure",
    "failed_fetch_writes_nothing": "ADR: a failure is recorded as a failure",
    "unparseable_tree_writes_nothing": "ADR: a failure is recorded as a failure",
}

MODEMS = [
    "catalog/modems/acme/x100/modem.yaml",
    "catalog/modems/acme/x100/modem-cookie.yaml",  # a second configuration of one model
    "catalog/modems/acme/x200/modem.yaml",
    "catalog/modems/zenith/g5/modem.yaml",
    "docs/catalog/modems/orbit/i9/modem.yaml",  # the pattern isn't anchored, so this counts too
]
NOT_MODEMS = [
    "README.md",
    "catalog/modems/acme/x100/parser.py",
    "catalog/modems/acme/x100/fixtures/modem.yaml",  # a level too deep
    "catalog/modems/zenith/modem.yaml",  # a level too shallow
    "catalog/modems/zenith/g5/modem.yml",
    "scripts/check.sh",
]


def tree(files: list[str], *, truncated: bool = False, contents: Mapping[str, str] | None = None) -> list[Exchange]:
    return [exchange(TREE_URL, github_tree(REPO, files, truncated=truncated, contents=contents))]


# The repo's history: a first commit with no modems, then two on the 17th (the second the day's last), and one on the
# 19th; the commits listing gives them newest first, and each commit's tree is asked for by its sha.
HISTORY = [
    (github_commit(REPO, "Initial commit", when="2026-09-15T10:00:00Z"), NOT_MODEMS),
    (github_commit(REPO, "Add the x100", when="2026-09-17T10:00:00Z"), MODEMS[:1]),
    (github_commit(REPO, "Add its cookie configuration", when="2026-09-17T15:00:00Z"), MODEMS[:2]),
    (github_commit(REPO, "Add the x200 and g5", when="2026-09-19T10:00:00Z"), MODEMS[:4]),
]
TREED = [0, 2, 3]  # the commits whose tree is asked for: each the last of its day


def commits(history: list[tuple[Json, list[str]]]) -> list[Exchange]:
    """The commits listing on the ref, newest first."""
    newest_first = [commit for commit, _ in reversed(history)]
    return github_numbered_pages(REPO, "commits", "per_page=100&sha=main", newest_first, per_page=100)


def commit_tree(commit: Json, files: list[str], contents: Mapping[str, str] | None = None) -> Exchange:
    url = f"{GITHUB_API}/repos/{REPO}/git/trees/{commit['sha']}?recursive=1"
    return exchange(url, github_tree(REPO, files, contents=contents))


LATE = (
    '{"date": "2026-09-21", "models": 3, "configs": 4, "vendors": 2}\n'
    '{"date": "2026-09-22", "models": 3, "configs": 4, "vendors": 2}\n'
)
STAMP = '"tree_truncated": false, "backfilled": true, "captured_at": "2026-09-23T23:31:05+00:00"}\n'
# The days before the file's rows, from the first whose tree matched, each counted in the tree of the day's last
# commit (ADR: past values a source can rebuild are filled, never changed).
REBUILT = "".join(
    f'{{"date": "2026-09-{day}", "models": {models}, "configs": {configs}, "vendors": {len(per_vendor)}, '
    f'"models_per_vendor": {per_vendor}, ' + STAMP
    for day, models, configs, per_vendor in [
        (17, 1, 2, {"acme": 1}),
        (18, 1, 2, {"acme": 1}),
        (19, 3, 4, {"acme": 2, "zenith": 1}),
        (20, 3, 4, {"acme": 2, "zenith": 1}),
    ]
).replace("'", '"')
TODAY = (
    '{"date": "2026-09-23", "models": 4, "configs": 5, "vendors": 3, '
    '"models_per_vendor": {"acme": 2, "orbit": 1, "zenith": 1}, "tree_truncated": false, '
    '"captured_at": "2026-09-23T23:31:05+00:00"}\n'
)

# leadline's own: the files counted by a value read from each one's text (ADR: a tree's files are counted by what
# they say). The pattern's group 1 is the value, a file without a match counts as `unknown`, and a blob is read once
# for any number of files, days of history and nights that hold it.
CONTENT = {"pattern": r"^status:\s*(\w+)", "key": "by_status"}
CONTENT_NAMES = ("content_counts_files_by_the_value_a_pattern_reads", "content_is_rebuilt_for_every_past_day")
NEW_X100 = "name: X100\nstatus: confirmed\n"
OLD_X100 = "name: X100\nstatus: awaiting\n"
AWAITING = "status: awaiting\n"
CONFIRMED = "status: confirmed\n"
NO_STATUS = "name: X200\n"
# Each path's text in today's tree, and in the trees the history asks for (the last commits of the 17th and 19th).
TEXTS_TODAY = dict(zip(MODEMS, [NEW_X100, AWAITING, NO_STATUS, CONFIRMED, CONFIRMED]))
TEXTS_17TH = {MODEMS[0]: OLD_X100, MODEMS[1]: AWAITING}
TEXTS_19TH = dict(zip(MODEMS[:4], [NEW_X100, AWAITING, NO_STATUS, CONFIRMED]))
BLOB_SHA = github_blob_sha


def blob(text: str) -> Exchange:
    """The request for a file's text, and its answer."""
    return exchange(f"{GITHUB_API}/repos/{REPO}/git/blobs/{BLOB_SHA(text)}", github_blob(REPO, text))


def leadline_config(scenario: Scenario) -> list[dict[str, Any]]:
    """leadline's config for `scenario`: LEADLINE_CONFIG, with the content setting where the scenario reads one."""
    if scenario.name not in CONTENT_NAMES:
        return LEADLINE_CONFIG
    return [{**entry, "content": CONTENT} for entry in LEADLINE_CONFIG]


def read_by_status(**texts: list[str]) -> str:
    """A row's `by_status_blobs` as it is written: the blobs of the texts it read, grouped by the status they say,
    each group's SHAs in order."""
    return json.dumps({status: sorted(BLOB_SHA(text) for text in group) for status, group in texts.items()})


# Today's row: five files, four blobs (the two that say `confirmed` and the same are one); the blobs it read.
TODAY_BLOBS = read_by_status(awaiting=[AWAITING], confirmed=[NEW_X100, CONFIRMED], unknown=[NO_STATUS])
TODAY_WITH_STATUS = TODAY.replace(
    '"captured_at"',
    '"by_status": {"awaiting": 1, "confirmed": 3, "unknown": 1}, "by_status_blobs": ' + TODAY_BLOBS + ', "captured_at"',
)
# The nightly rows of the 21st and 22nd, as they stand without the counts by status; counted in the tree of the 19th's
# last commit, the last before them, the 19th and every later day have the same counts, and no blob to read.
NIGHT = (
    '{{"date": "2026-09-{day}", "models": 3, "configs": 4, "vendors": 2, '
    '"models_per_vendor": {{"acme": 2, "zenith": 1}}, "tree_truncated": false, {more}'
    '"captured_at": "2026-09-{day}T23:30:00+00:00"}}\n'
)
BY_STATUS_19TH = '"by_status": {"awaiting": 1, "confirmed": 2, "unknown": 1}, '
NIGHTS = NIGHT.format(day=21, more="") + NIGHT.format(day=22, more="")
NIGHTS_WITH_STATUS = NIGHT.format(day=21, more=BY_STATUS_19TH) + NIGHT.format(day=22, more=BY_STATUS_19TH)
# The days before the nightly rows: the 17th's tree has two files, both awaiting, one of them the old text of the x100
# (the only blob today's row hasn't read, so the 17th's row records it); the 19th's has four, all read already.
REBUILT_17TH = '"by_status": {"awaiting": 2}, "by_status_blobs": ' + read_by_status(awaiting=[OLD_X100]) + ", "
REBUILT_WITH_STATUS = "".join(
    line.replace('"tree_truncated": false, ', '"tree_truncated": false, ' + more, 1)
    for line, more in zip(
        REBUILT.splitlines(keepends=True),
        [REBUILT_17TH, '"by_status": {"awaiting": 2}, ', BY_STATUS_19TH, BY_STATUS_19TH],
    )
)

SCENARIOS = [
    Scenario("counts_configurations_models_and_vendors", CALLS, tree(MODEMS + NOT_MODEMS), config=CONFIG),
    Scenario("a_truncated_tree_is_marked", CALLS, tree(MODEMS[:2], truncated=True), config=CONFIG),
    Scenario("a_tree_without_modems", CALLS, tree(NOT_MODEMS), config=CONFIG),
    Scenario(
        "a_body_without_a_tree_or_truncated_counts_nothing",
        CALLS,
        [exchange(TREE_URL, {"sha": "0" * 40, "url": TREE_URL})],
        config=CONFIG,
    ),
    Scenario("skips_without_a_token", CALLS, env={}, config=CONFIG),
    Scenario(
        "failed_fetch_writes_nothing",
        CALLS,
        [exchange(TREE_URL, '{"message":"Not Found"}', status=404)],
        config=CONFIG,
        seed={FILE: EARLIER},
    ),
    Scenario(
        "unparseable_tree_writes_nothing",
        CALLS,
        [exchange(TREE_URL, "<html>maintenance</html>")],
        config=CONFIG,
        seed={FILE: EARLIER},
    ),
    # leadline's own, which the script doesn't do: reviewed rows.
    Scenario(
        "a_file_starting_late_gets_rows_back_to_the_first_commit_that_matches",
        [],
        [*tree(MODEMS + NOT_MODEMS), *commits(HISTORY), *(commit_tree(*HISTORY[n]) for n in TREED)],
        config=CONFIG,
        seed={FILE: LATE},
        expected={FILE: REBUILT + LATE + TODAY},
    ),
    Scenario(
        CONTENT_NAMES[0],
        [],
        [*tree(MODEMS + NOT_MODEMS, contents=TEXTS_TODAY), *(blob(text) for text in sorted(set(TEXTS_TODAY.values())))],
        config=CONFIG,
        expected={FILE: TODAY_WITH_STATUS},
    ),
    Scenario(
        CONTENT_NAMES[1],
        [],
        [
            *tree(MODEMS + NOT_MODEMS, contents=TEXTS_TODAY),
            *(blob(text) for text in sorted(set(TEXTS_TODAY.values()))),
            *commits(HISTORY),
            commit_tree(HISTORY[0][0], HISTORY[0][1]),
            commit_tree(HISTORY[2][0], HISTORY[2][1], TEXTS_17TH),
            commit_tree(HISTORY[3][0], HISTORY[3][1], TEXTS_19TH),
            blob(OLD_X100),
        ],
        config=CONFIG,
        seed={FILE: NIGHTS},
        expected={FILE: REBUILT_WITH_STATUS + NIGHTS_WITH_STATUS + TODAY_WITH_STATUS},
    ),
]
