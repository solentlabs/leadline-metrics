"""cloudflare_workers: a Worker's daily invocations from Cloudflare's GraphQL Analytics API.

The script has no collector for it, so each scenario carries the rows the source must leave (`expected`), written by
hand and reviewed; they are the source's golden. The run is 2026-09-23, so the newest day it writes is 2026-09-22, and
the 28 days it reads begin 2026-08-26. A first run (a file with no row before those 28 days) reads the 84 days from
2026-06-30, in three queries of 28 days, oldest first. A scenario about the nightly window seeds an older row (OLDER).

Every answer is built by the builders of the cloudflare group from made-up values (no capture holds these answers).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from scenarios import CREDENTIALS, Scenario
from scenarios.builders.common import Exchange, exchange, failed

from leadline.sources.cloudflare.cloudflare_workers import spec
from leadline.sources.cloudflare.tests.builders import (
    CLOUDFLARE_GRAPHQL,
    workers_day,
    workers_metrics,
    workers_outcome,
)

SCRIPT = "example-worker"
ACCOUNT = CREDENTIALS["CLOUDFLARE_ACCOUNT_ID"]
FILE = f"cloudflare-workers-{SCRIPT}.jsonl"
NOW = "2026-09-23T23:31:05+00:00"  # the scenarios' clock as captured_at gives it
EARLIER = "2026-09-22T23:30:02+00:00"  # an earlier night's
YESTERDAY = date(2026, 9, 22)
WINDOW_START = YESTERDAY - timedelta(days=spec.WINDOW_DAYS - 1)  # 2026-08-26
HISTORY_START = YESTERDAY - timedelta(days=spec.HISTORY_DAYS - 1)  # 2026-06-30
LEADLINE_CONFIG = [{"script": SCRIPT}]
DIVERGENCES: dict[str, str] = {}  # the script has no collector for this source


def sent(query: str) -> str:
    return json.dumps({"query": query})


def answer(body: Any, status: int = 200, start: date = WINDOW_START) -> Exchange:
    """The answer to the query for the 28 days from `start` (the nightly window unless given)."""
    query = spec.query(ACCOUNT, SCRIPT, start, start + timedelta(days=spec.WINDOW_DAYS - 1))
    return exchange(CLOUDFLARE_GRAPHQL, body, method="POST", sent=sent(query), status=status)


def failure(start: date = WINDOW_START) -> Exchange:
    query = spec.query(ACCOUNT, SCRIPT, start, start + timedelta(days=spec.WINDOW_DAYS - 1))
    return failed(CLOUDFLARE_GRAPHQL, method="POST", sent=sent(query))


def history(*bodies: Any) -> list[Exchange]:
    """A first run's three answers, oldest first: `bodies` for the first ones, the rest empty."""
    starts = [HISTORY_START + timedelta(days=n * spec.WINDOW_DAYS) for n in range(3)]
    given = [*bodies, *[workers_metrics()] * (3 - len(bodies))]
    return [answer(body, start=start) for body, start in zip(given, starts)]


def day(n: int) -> date:
    """Day `n` before yesterday: 0 is yesterday, 1 the day before."""
    return YESTERDAY - timedelta(days=n)


def daily() -> list[Any]:
    """Two days with invocations, the second yesterday; the days between had none."""
    return [
        workers_day(day(2), 120, errors=3, subrequests=40, p50=1234.4, p99=8765.5),
        workers_day(day(0), 200, errors=0, subrequests=10, p50=900, p99=2100.2),
    ]


def outcomes() -> list[Any]:
    return [
        workers_outcome(day(2), "success", 117),
        workers_outcome(day(2), "scriptThrewException", 2),
        workers_outcome(day(2), "exceededResources", 1),
        workers_outcome(day(2), "internalError", 0),  # a zero is left out
        workers_outcome(day(0), "clientDisconnected", 100),  # a tie: the name decides
        workers_outcome(day(0), "success", 100),
    ]


# The fields of the days daily() and outcomes() give, as the row holds them.
DAY_2: dict[str, Any] = {
    "requests": 120,
    "errors": 3,
    "subrequests": 40,
    "outcomes": {"success": 117, "scriptThrewException": 2, "exceededResources": 1},
    "cpu_us": {"p50": 1234, "p99": 8766},
}
DAY_0: dict[str, Any] = {
    "requests": 200,
    "errors": 0,
    "subrequests": 10,
    "outcomes": {"clientDisconnected": 100, "success": 100},
    "cpu_us": {"p50": 900, "p99": 2100},
}


def row(when: date, captured: str = NOW, **fields: Any) -> str:
    """The row for a day, in the order the source writes its fields: zeros unless `fields` give its values."""
    base: dict[str, Any] = {
        "date": when.isoformat(),
        "script": SCRIPT,
        "status": "ok",
        "requests": 0,
        "errors": 0,
        "subrequests": 0,
        "outcomes": {},
        "cpu_us": {},
    }
    unknown = set(fields) - set(base)
    if unknown:
        raise ValueError(f"not a field of a row: {sorted(unknown)}")
    return json.dumps({**base, **fields, "captured_at": captured}) + "\n"


def window(captured: str = NOW, **days: dict[str, Any]) -> str:
    """The 28 rows of the window, oldest first: each day's fields from `days` (`d2=DAY_2` for day(2)), else zeros."""
    return "".join(row(day(n), captured, **days.get(f"d{n}", {})) for n in range(spec.WINDOW_DAYS - 1, -1, -1))


def failure_row(status: str, reason: str, **fields: Any) -> str:
    """The failure row this run writes for yesterday."""
    head = {"date": YESTERDAY.isoformat(), "script": SCRIPT, "status": status, **fields}
    return json.dumps({**head, "reason": reason, "captured_at": NOW}) + "\n"


OLDER = row(day(60), EARLIER, requests=5)  # a day before the window, which no run touches again
FAILED = failure_row("fetch_failed", "graphql request failed (HTTP 000, connection refused)", http_code="000")

SCENARIOS = [
    Scenario(
        "writes_a_row_for_each_day_of_the_window",
        [],
        [answer(workers_metrics(daily(), outcomes()))],
        seed={FILE: OLDER},
        expected={FILE: OLDER + window(d2=DAY_2, d0=DAY_0)},
    ),
    Scenario(
        "a_quiet_worker_writes_zeros",
        [],
        [answer(workers_metrics())],
        seed={FILE: OLDER},
        expected={FILE: OLDER + window()},
    ),
    Scenario(
        "corrects_a_changed_day_and_fills_a_failed_one",
        [],
        [answer(workers_metrics(daily(), outcomes()))],
        seed={
            FILE: OLDER
            + row(day(2), EARLIER, **{**DAY_2, "requests": 110})
            + row(day(1), EARLIER)
            + FAILED.replace(NOW, EARLIER)
        },
        expected={
            FILE: OLDER
            + window(d2=DAY_2, d0=DAY_0).replace(row(day(1)), row(day(1), EARLIER))  # unchanged: left as it was
        },
    ),
    Scenario(
        "three_failed_attempts_write_a_failure_row",
        [],
        [failure()] * 3,
        seed={FILE: OLDER},
        expected={FILE: OLDER + FAILED},
    ),
    Scenario(
        "a_failure_keeps_a_good_row",
        [],
        [failure()] * 3,
        seed={FILE: OLDER + row(YESTERDAY, EARLIER, **DAY_0)},
        expected={FILE: OLDER + row(YESTERDAY, EARLIER, **DAY_0)},
    ),
    Scenario(
        "graphql_errors_in_a_success",
        [],
        [answer({"data": None, "errors": [{"message": "not authorized", "path": ["viewer"]}]})],
        seed={FILE: OLDER},
        expected={FILE: OLDER + failure_row("graphql_error", "not authorized")},
    ),
    Scenario(
        "an_account_the_token_cannot_see_is_no_account",
        [],
        [answer({"data": {"viewer": {"accounts": []}}, "errors": None})],
        seed={FILE: OLDER},
        expected={FILE: OLDER + failure_row("no_account", "account filter matched nothing")},
    ),
    Scenario(
        "a_body_that_is_not_json",
        [],
        [answer("<html>Bad gateway</html>")],
        seed={FILE: OLDER},
        expected={FILE: OLDER + failure_row("parse_failed", "response was not JSON")},
    ),
    Scenario(
        "a_count_of_another_kind_is_an_unexpected_shape",
        [],
        [answer(workers_metrics([workers_day(day(0), -1)]))],
        seed={FILE: OLDER},
        expected={FILE: OLDER + failure_row("parse_failed", "response had an unexpected shape")},
    ),
    Scenario(
        "retries_until_the_request_succeeds",
        [],
        [failure(), answer("", status=500), answer(workers_metrics([workers_day(day(0), 9)]))],
        seed={FILE: OLDER},
        expected={FILE: OLDER + window(d0={"requests": 9, "cpu_us": {"p50": 0, "p99": 0}})},
    ),
    Scenario(
        "a_first_run_reads_three_months_from_the_first_day_with_invocations",
        [],
        history(workers_metrics([workers_day(day(70), 4, p50=500, p99=600)], [workers_outcome(day(70), "success", 4)])),
        expected={
            FILE: "".join(row(day(n)) for n in range(70, spec.WINDOW_DAYS - 1, -1)).replace(
                row(day(70)), row(day(70), requests=4, outcomes={"success": 4}, cpu_us={"p50": 500, "p99": 600})
            )
            + window()
        },
    ),
    Scenario(
        "a_first_run_with_nothing_older_writes_the_window",
        [],
        history(),
        expected={FILE: window()},
    ),
    Scenario(
        "a_first_run_that_fails_writes_a_failure_row",
        [],
        [failure(HISTORY_START)] * 3,
        expected={FILE: FAILED},
    ),
    Scenario(
        "skips_without_the_account_id",
        [],
        env={"CLOUDFLARE_API_TOKEN": CREDENTIALS["CLOUDFLARE_API_TOKEN"]},
        expected={},
    ),
]
