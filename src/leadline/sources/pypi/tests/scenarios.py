"""pypi: each package's downloads by day, from pypistats, with its version from PyPI.

The script's collect_pypi PACKAGE asks pypistats for the package's daily downloads (twice while it
fails, at once giving up on a 404), then PyPI for its metadata, and writes rows into
pypi-<package>.jsonl: the run's date first, `ok` with the day's, week's and month's downloads and
the latest version, or `fetch_failed` when pypistats failed or hasn't published the day yet; then a
`backfilled` row for each earlier day since the file's first row that is missing or failed (a row
without a status counts as ok), from where a month's window is whole in the history (without
metadata, the package counts as older than the history), up to the latest day pypistats has
published. A failure row names the last attempt's status, and replaces any row for the run's date,
good or not. retry_failed_pypi asks again, after a wait, for each package whose row for the run's
date failed, unless the day just isn't published. The main run calls collect_pypi for each package
in PYPI_ORDER and the retry pass at the end, as the scenarios do.

A 200 body that doesn't parse, from pypistats or PyPI, writes no row at all, and so does a seed
line that parses but isn't an object, or a seed `date` that isn't a string: the script reads the
file before it writes.

leadline differs by decision on the scenarios in DIVERGENCES, asserted in test_pypi.py: an
error waiting can't fix is never retried; a failure never replaces a good row; failed or
unusable metadata is null in a partial row; an unusable history is a `parse_failed` row; and a seed
line that isn't a row is kept.

Nothing here calls pypistats: every response is made up (docs/ARCHITECTURE_DECISIONS.md).
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from scenarios import Scenario
from scenarios.builders.common import Exchange, exchange, failed

from leadline.sources.pypi.tests.builders import pypi_project, pypistats_overall

CALLS = ['for pkg in "${PYPI_ORDER[@]}"; do collect_pypi "$pkg"; done', "retry_failed_pypi"]
RUN = date(2026, 9, 23)
PACKAGE, OTHER, MISSING = "example-package", "other-package", "missing-package"


SLEEP_S, RETRY_WAIT_S = 60, 240  # the script's PYPI_INTER_PACKAGE_SLEEP and PYPI_RETRY_PASS_WAIT


def packages(*names: str) -> dict[str, list[str]]:
    return {"PYPI_PACKAGES": list(names)}


def leadline_config(scenario: Scenario) -> dict[str, Any]:
    """leadline's config for `scenario`: the script's packages, in its order, and its waits."""
    return {"packages": list(scenario.config["PYPI_PACKAGES"]), "sleep_s": SLEEP_S, "retry_wait_s": RETRY_WAIT_S}


def overall_url(package: str) -> str:
    return f"https://pypistats.org/api/packages/{package}/overall"


def project_url(package: str) -> str:
    return f"https://pypi.org/pypi/{package}/json"


def history(first: date, last: date = RUN, *, quiet: Sequence[date] = (), seed: int = 1) -> dict[date, int | None]:
    """Made-up downloads for each day from `first` to `last`; the `quiet` days had none."""
    rng = random.Random(seed)
    days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
    return {day: None if day in quiet else rng.randint(0, 40) for day in days}


def downloads(package: str, days: Mapping[date, int | None]) -> Exchange:
    return exchange(overall_url(package), pypistats_overall(package, days))


def project(package: str, first_upload: str, requires_python: str | None = ">=3.9") -> Exchange:
    """The package on PyPI: a first release uploaded on `first_upload`, and a later one."""
    releases = {"0.1.0": f"{first_upload}T09:00:00", "0.2.0": "2026-09-01T09:00:00"}
    return exchange(project_url(package), pypi_project(package, releases, requires_python=requires_python))


def overall_status(package: str, status: int) -> Exchange:
    return exchange(overall_url(package), "", status=status)


def row(day: str, status: str | None = "ok", package: str = PACKAGE) -> str:
    """A seed row; `status` None leaves the key out."""
    status_field = "" if status is None else f' "status": "{status}",'
    return f'{{"date": "{day}", "package": "{package}",{status_field} "downloads_last_day": 1}}\n'


RECENT = history(date(2026, 7, 1), quiet=[date(2026, 9, 21)])

SCENARIOS = [
    Scenario(
        "downloads_for_the_day_week_and_month",
        CALLS,
        [downloads(PACKAGE, RECENT), project(PACKAGE, "2026-05-01")],
        config=packages(PACKAGE),
        seed={f"pypi-{PACKAGE}.jsonl": row("2026-09-22")},
    ),
    Scenario(
        "backfills_missing_and_failed_days",
        CALLS,
        [downloads(PACKAGE, RECENT), project(PACKAGE, "2026-05-01")],
        config=packages(PACKAGE),
        seed={
            f"pypi-{PACKAGE}.jsonl": row("2026-09-15")
            + row("2026-09-17", "fetch_failed")
            + "not json\n"
            + row("2026-09-18", status=None)  # counts as ok: not backfilled
            + '{"date": null}\n'
            + row("2026-09-19")
            + row("2026-09-22")
        },
    ),
    Scenario(
        "backfill_starts_where_a_months_window_is_whole",
        CALLS,
        [
            downloads(PACKAGE, history(date(2026, 8, 1))),  # uploaded before the history starts
            project(PACKAGE, "2026-05-01"),
            downloads(OTHER, history(date(2026, 9, 1), seed=2)),  # uploaded within it
            project(OTHER, "2026-09-01"),
        ],
        config=packages(PACKAGE, OTHER),
        seed={f"pypi-{PACKAGE}.jsonl": row("2026-08-20"), f"pypi-{OTHER}.jsonl": row("2026-08-25", package=OTHER)},
    ),
    Scenario(
        # Published three days behind: the backfill stops at the latest published day, not the run's.
        "the_run_date_not_published_yet",
        CALLS,
        [downloads(PACKAGE, history(date(2026, 7, 1), RUN - timedelta(days=3))), project(PACKAGE, "2026-05-01")],
        config=packages(PACKAGE),
        seed={f"pypi-{PACKAGE}.jsonl": row("2026-09-18")},
    ),
    Scenario(
        "the_retry_pass_asks_again_for_failed_packages",
        CALLS,
        [
            exchange(overall_url(PACKAGE), '{"message": "rate limited"}', status=429),
            exchange(overall_url(PACKAGE), "", status=429),
            downloads(PACKAGE, RECENT),  # the retry pass's first attempt
            project(PACKAGE, "2026-05-01"),
            *[failed(overall_url(OTHER))] * 4,  # two attempts in the first pass, two in the retry pass
        ],
        config=packages(PACKAGE, OTHER),
        seed={f"pypi-{PACKAGE}.jsonl": row("2026-09-22"), f"pypi-{OTHER}.jsonl": row("2026-09-22", package=OTHER)},
    ),
    Scenario(
        "a_package_not_found_is_asked_again_in_the_retry_pass",
        CALLS,
        [
            exchange(overall_url(PACKAGE), '{"message": "Not Found"}', status=404),  # the first pass gives up at once
            downloads(PACKAGE, RECENT),  # the retry pass asks again: the port won't
            project(PACKAGE, "2026-05-01"),
        ],
        config=packages(PACKAGE),
    ),
    Scenario(
        # First uploaded within the history (as backfill_starts_where_a_months_window_is_whole's
        # other-package), but without metadata the backfill still waits for a whole month's window.
        "without_metadata_the_version_is_blank_and_backfill_starts_a_month_in",
        CALLS,
        [downloads(PACKAGE, history(date(2026, 8, 1))), exchange(project_url(PACKAGE), "Not Found", status=404)],
        config=packages(PACKAGE),
        seed={f"pypi-{PACKAGE}.jsonl": row("2026-08-10") + row("2026-09-22")},
    ),
    Scenario(
        "a_failure_row_keeps_the_last_status",
        CALLS,
        [
            *[overall_status(PACKAGE, code) for code in (500, 502, 503, 503)],  # a server error every time
            overall_status(OTHER, 503),
            *[failed(overall_url(OTHER))] * 2,
            overall_status(OTHER, 429),  # rate limited last: the reason says so
            *[exchange(overall_url(MISSING), '{"message": "Not Found"}', status=404)] * 2,  # once per pass
        ],
        config=packages(PACKAGE, OTHER, MISSING),
    ),
    Scenario(
        "a_failure_replaces_a_good_row_for_the_run_date",
        CALLS,
        [failed(overall_url(PACKAGE))] * 4,  # two attempts in the first pass, two in the retry pass
        config=packages(PACKAGE),
        seed={f"pypi-{PACKAGE}.jsonl": row("2026-09-22") + row("2026-09-23")},
    ),
    Scenario(
        "a_null_requires_python_is_kept",
        CALLS,
        [downloads(PACKAGE, RECENT), project(PACKAGE, "2026-05-01", requires_python=None)],
        config=packages(PACKAGE),
        seed={f"pypi-{PACKAGE}.jsonl": row("2026-09-22")},
    ),
    Scenario(
        "a_body_that_is_not_json_writes_no_row",
        CALLS,
        [
            exchange(overall_url(PACKAGE), "<html>maintenance</html>"),
            project(PACKAGE, "2026-05-01"),
            downloads(OTHER, RECENT),
            exchange(project_url(OTHER), "<html>maintenance</html>"),
        ],
        config=packages(PACKAGE, OTHER),
        seed={f"pypi-{PACKAGE}.jsonl": row("2026-09-22"), f"pypi-{OTHER}.jsonl": row("2026-09-22", package=OTHER)},
    ),
    Scenario(
        "a_seed_line_not_an_object_writes_nothing",
        CALLS,
        [downloads(PACKAGE, RECENT), project(PACKAGE, "2026-05-01")],
        config=packages(PACKAGE),
        seed={f"pypi-{PACKAGE}.jsonl": row("2026-09-21") + "[1]\n" + row("2026-09-22")},
    ),
    Scenario(
        "a_seed_date_that_is_not_a_string_writes_nothing",
        CALLS,
        [downloads(PACKAGE, RECENT), project(PACKAGE, "2026-05-01")],
        config=packages(PACKAGE),
        seed={f"pypi-{PACKAGE}.jsonl": '{"date": 20260921}\n' + row("2026-09-22")},
    ),
    Scenario(
        "an_empty_history_writes_nothing",
        CALLS,
        [downloads(PACKAGE, {}), project(PACKAGE, "2026-05-01")],
        config=packages(PACKAGE),
        seed={f"pypi-{PACKAGE}.jsonl": row("2026-09-22")},
    ),
]

FAILURE = "ADR: a failure is recorded as a failure"
PARTIAL = "ADR: a partly failed row keeps its good parts"
UNFIXABLE = "ADR: one failed night recovers (an error waiting can't fix is never retried, and is critical at once)"

# The scenarios where leadline's rows or requests differ from the script's by a decision, and the decision.
DIVERGENCES = {
    "a_package_not_found_is_asked_again_in_the_retry_pass": UNFIXABLE,
    "a_failure_row_keeps_the_last_status": UNFIXABLE,  # the same rows, from one request fewer
    "a_failure_replaces_a_good_row_for_the_run_date": "ADR: a failure never replaces good data",
    "without_metadata_the_version_is_blank_and_backfill_starts_a_month_in": PARTIAL,
    "a_body_that_is_not_json_writes_no_row": f"{FAILURE}; {PARTIAL}",
    "an_empty_history_writes_nothing": FAILURE,
    "a_seed_line_not_an_object_writes_nothing": "ADR: store never fails on a line it can't use",
    "a_seed_date_that_is_not_a_string_writes_nothing": "ADR: store never fails on a line it can't use",
}
