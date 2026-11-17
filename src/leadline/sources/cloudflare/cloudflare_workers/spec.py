"""What cloudflare_workers declares: its name, its file, its config's checks, its query, and its summary.

No requests. The query is text built here, so the code that sends it and the tests that serve it name one query.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from leadline import config

NAME = "cloudflare_workers"
DEFAULT_FILE = config.default_file(NAME, "script")  # cloudflare-workers-{script}.jsonl

KEYS = ("script",)  # the keys an entry may have, besides `file`
WINDOW_DAYS = 28  # read and rewritten every run, yesterday the last; one query spans at most a month
HISTORY_DAYS = 3 * WINDOW_DAYS  # a first run's reach, in 28-day queries: Cloudflare keeps three months
CHECK_DAYS = 7  # the days `check` asks about
PAGE = 10000  # the most groups an answer holds (maxPageSize): every status of every day

# A day's sums: a row field and the API's.
SUMS = (("requests", "requests"), ("errors", "errors"), ("subrequests", "subrequests"))
OUTCOMES = "outcomes"  # the day's invocations by how they ended (Cloudflare's `status`)
CPU = "cpu_us"  # the day's CPU time percentiles, in microseconds
QUANTILES = (("p50", "cpuTimeP50"), ("p99", "cpuTimeP99"))  # a key of `cpu_us` and the API's quantile


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def _problems(entry: dict[str, Any]) -> list[str]:
    script = entry.get("script")
    return [] if isinstance(script, str) and script else ["script: must be a non-empty string"]


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, {"script": entry["script"]})


# --- the query ---

QUERY = """
{
  viewer {
    accounts(filter: {accountTag: %(account)s}) {
      daily: workersInvocationsAdaptive(
        limit: %(days)d
        filter: {scriptName: %(script)s, date_geq: "%(start)s", date_leq: "%(end)s"}
        orderBy: [date_ASC]
      ) {
        dimensions { date }
        sum { %(sums)s }
        quantiles { %(quantiles)s }
      }
      outcomes: workersInvocationsAdaptive(
        limit: %(page)d
        filter: {scriptName: %(script)s, date_geq: "%(start)s", date_leq: "%(end)s"}
        orderBy: [date_ASC]
      ) {
        dimensions { date status }
        sum { requests }
      }
    }
  }
}
"""


def query(account: str, script: str, start: date, end: date) -> str:
    """The script's invocations for each day from start to end (UTC dates): the day's sums and CPU time percentiles,
    and its invocations by status. The account and script are quoted as JSON strings, which GraphQL reads alike."""
    return QUERY % {
        "account": json.dumps(account),
        "script": json.dumps(script),
        "days": (end - start).days + 1,
        "page": PAGE,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sums": " ".join(api for _, api in SUMS),
        "quantiles": " ".join(api for _, api in QUANTILES),
    }


# --- the summary ---

LABEL = "Workers"  # what trends heads its sections with
SHOWN = (config.Shown("requests", "Requests", "daily"), config.Shown("errors", "Errors", "daily"))
# What only trends draws: how the invocations ended, and the CPU time percentiles as lines.
CHARTS = (
    config.Shown(OUTCOMES, "Outcomes", "breakdown", half=True),
    config.Shown(CPU, "CPU time (microseconds)", "breakdown", lines=True, half=True),
)


def summary(cfg: Any) -> list[config.Section]:
    return config.entry_summary(NAME, cfg, "script", file_name, SHOWN, LABEL, charts=CHARTS)
