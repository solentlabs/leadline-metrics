"""cloudflare_web: a site's page views and visits from Cloudflare Web Analytics, over GraphQL.

The script's collect_cloudflare_analytics LABEL SITE_TAG asks for the 30 complete days before the run
(daily page loads and visits) and the week's top referrers, up to three times while the request fails
or its body is empty. It writes a row for yesterday into cloudflare-<label>.jsonl: `ok` with
yesterday's and the week's numbers (a day given twice counts once, its last reading), or a failure
status. With an `ok` answer it also heals the 30 days (CLOUDFLARE_HEAL_DAYS), from the file's first
row on: each day with no row, a failed row, or a row read under another site tag (a row without
`site_tag` included) is rewritten from the answer, marked `backfilled`. Every row it reads it tags with
the `site_tag` it was read under.

Where leadline differs by a decision, the scenario is in DIVERGENCES, and test_cloudflare_web.py
asserts leadline's side: a failure replaces a good row for the same day, where leadline keeps the good
row (ADR: a failure never replaces good data); a body that parses but can't be read (`null`, a list,
a null `viewer`, an error that isn't an object) writes no row, and `data` null reads as an account
that matched nothing, where leadline writes a `parse_failed` row (ADR: a failure is recorded as a
failure); a seed line that parses but isn't an object stops the script's run, where leadline keeps
the line and writes the rows (ADR: store never fails on a line it can't use); a good row in the 30
days whose numbers changed is left as it was, where leadline corrects it (ADR: cloudflare_web re-reads
and corrects its last 30 days).
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from scenarios import CREDENTIALS, Scenario
from scenarios.builders.common import Exchange, exchange, failed

from leadline.sources.cloudflare.tests.builders import CLOUDFLARE_GRAPHQL, rum_day, rum_referrer, web_analytics

LABEL = "example"
SITE = "site0tag0example"  # a made-up site tag
OTHER_SITE = "other0tag0example"  # another made-up site tag, as if the configured one had changed
FILE = f"cloudflare-{LABEL}.jsonl"
CALLS = [f'collect_cloudflare_analytics "{LABEL}" "{SITE}"']
YESTERDAY = date(2026, 9, 22)  # the run is 2026-09-23
WEEK_AGO = YESTERDAY - timedelta(days=6)
HEAL_START = YESTERDAY - timedelta(days=29)  # 30 complete days, yesterday the last: 2026-08-24

# The script's query, filled in by its own Python, and sent as {"query": ...}.
QUERY = """
{
  viewer {
    accounts(filter: {accountTag: "%s"}) {
      daily: rumPageloadEventsAdaptiveGroups(
        filter: {date_geq: "%s", date_leq: "%s", siteTag: "%s"}
        limit: 100
        orderBy: [date_ASC]
      ) {
        count
        sum { visits }
        dimensions { date }
      }
      referrers: rumPageloadEventsAdaptiveGroups(
        filter: {date_geq: "%s", date_leq: "%s", siteTag: "%s"}
        limit: 10
        orderBy: [count_DESC]
      ) {
        count
        dimensions { refererHost }
      }
    }
  }
}
"""
DAYS = (HEAL_START.isoformat(), YESTERDAY.isoformat(), SITE)  # the daily numbers: the 30 days
REFERRERS = (WEEK_AGO.isoformat(), YESTERDAY.isoformat(), SITE)  # the top referrers: the week
SENT = json.dumps({"query": QUERY % (CREDENTIALS["CLOUDFLARE_ACCOUNT_ID"], *DAYS, *REFERRERS)})


def answer(body: object, status: int = 200) -> Exchange:
    return exchange(CLOUDFLARE_GRAPHQL, body, method="POST", sent=SENT, status=status)


def failure() -> Exchange:
    return failed(CLOUDFLARE_GRAPHQL, method="POST", sent=SENT)


WEEK = web_analytics(
    [rum_day(WEEK_AGO + timedelta(days=d), 10 + 5 * d, 4 + d) for d in range(7)],
    [rum_referrer("", 60), rum_referrer("github.com", 20), rum_referrer("community.home-assistant.io", 5)],
)
GOOD_ROW = (
    '{"date": "2026-09-22", "site": "example", "status": "ok", "pageviews": 40, "visits": 10, '
    '"captured_at": "2026-09-23T08:00:00+00:00"}\n'
)
OLDER_ROW = '{"date": "2026-09-21", "site": "example", "status": "ok", "pageviews": 35, "visits": 9}\n'


def day(n: int) -> date:
    """Day `n` of the 30: 0 is HEAL_START, 29 yesterday, and a negative day is before the 30."""
    return HEAL_START + timedelta(days=n)


def month_day(n: int) -> tuple[date, int, int]:
    """Day `n` of the 30, and its page views and visits in MONTH."""
    return day(n), 20 + n, 5 + n % 4


MONTH = web_analytics(
    [rum_day(*month_day(n)) for n in range(30)], [rum_referrer("", 90), rum_referrer("example.org", 7)]
)
TENTH = 17  # 2026-09-10, the day the heal scenarios change


def stored(when: date, pageviews: int, visits: int, site_tag: str | None = SITE) -> str:
    """The row an earlier run wrote for `when`, read under `site_tag` (None: before rows carried one)."""
    tag = {} if site_tag is None else {"site_tag": site_tag}
    row = {"date": when.isoformat(), "site": LABEL, **tag, "status": "ok", "pageviews": pageviews, "visits": visits}
    return json.dumps(row) + "\n"


def fetch_failed(when: date) -> str:
    """The failure row an earlier run wrote for `when`: no connection."""
    row = {"date": when.isoformat(), "site": LABEL, "status": "fetch_failed", "http_code": "000"}
    return json.dumps({**row, "reason": "graphql request failed"}) + "\n"


def stored_month(first: int = 0, instead: dict[int, str] | None = None) -> str:
    """MONTH's days `first` to 28 (the day before yesterday) as earlier runs read them, under SITE;
    `instead` gives the text for a day, by its number, in place of its row ("" for no row)."""
    instead = instead or {}
    return "".join(instead.get(n, stored(*month_day(n))) for n in range(first, 29))


# leadline's config for these scenarios: the script's label and site tag, writing the script's file.
LEADLINE_CONFIG = [{"label": LABEL, "site_tag": SITE, "file": "cloudflare-{label}.jsonl"}]

# The scenarios where leadline's rows differ from the script's by a decision, and the decision.
DIVERGENCES = {
    "a_failure_replaces_a_good_row": "ADR: a failure never replaces good data",
    "null_data_reads_as_no_account": "ADR: a failure is recorded as a failure",
    "a_null_body_writes_no_row": "ADR: a failure is recorded as a failure",
    "a_list_body_writes_no_row": "ADR: a failure is recorded as a failure",
    "a_null_viewer_writes_no_row": "ADR: a failure is recorded as a failure",
    "an_error_that_is_not_an_object_writes_no_row": "ADR: a failure is recorded as a failure",
    "a_seed_line_not_an_object_stops_the_write": "ADR: store never fails on a line it can't use",
    "a_good_row_whose_numbers_changed_is_left": "ADR: cloudflare_web re-reads and corrects its last 30 days",
}

SCENARIOS = [
    Scenario("week_of_traffic", CALLS, [answer(WEEK)], seed={FILE: OLDER_ROW}),
    Scenario("quiet_week", CALLS, [answer(web_analytics())]),
    Scenario(
        "yesterday_not_in_the_window",
        CALLS,
        [answer(web_analytics([rum_day(WEEK_AGO, 7, 3), rum_day(WEEK_AGO + timedelta(days=2), 5, 2)]))],
    ),
    Scenario(
        "nulls_read_as_zero_and_an_empty_referrer_as_direct",
        CALLS,
        [
            answer(
                {
                    "data": {
                        "viewer": {
                            "accounts": [
                                {
                                    "daily": [
                                        {"count": None, "sum": {"visits": None}, "dimensions": {"date": "2026-09-22"}},
                                        {"count": 4, "sum": None, "dimensions": None},
                                    ],
                                    "referrers": [
                                        {"count": None, "dimensions": {"refererHost": None}},
                                        {"count": 3, "dimensions": None},
                                    ],
                                }
                            ]
                        }
                    },
                    "errors": None,
                }
            )
        ],
    ),
    Scenario(
        "retries_until_the_request_succeeds",
        CALLS,
        [answer("", status=500), failure(), answer(WEEK)],
    ),
    Scenario("retries_after_an_empty_body", CALLS, [answer(""), answer(WEEK)]),
    Scenario(
        "three_failed_attempts_record_the_last_status",
        CALLS,
        [answer('{"errors": [{"message": "busy"}]}', status=500), answer("", status=429), answer("", status=503)],
        seed={FILE: OLDER_ROW},
    ),
    Scenario(
        "a_failure_replaces_a_good_row",
        CALLS,
        [failure()] * 3,  # each of the three attempts
        seed={FILE: OLDER_ROW + GOOD_ROW},
    ),
    Scenario("an_empty_body_is_a_failure", CALLS, [answer("")] * 3),  # each of the three attempts
    Scenario(
        "graphql_errors_in_a_success",
        CALLS,
        [
            answer(
                {
                    "data": None,
                    "errors": [
                        {"message": "rate limited", "path": ["viewer"], "extensions": {"code": "limit"}},
                        {"message": "a message longer than the row keeps " * 8},
                        {"code": 7},
                        {"message": "never shown"},
                    ],
                }
            )
        ],
    ),
    Scenario(
        "account_matched_nothing",
        CALLS,
        [answer({"data": {"viewer": {"accounts": []}}, "errors": None})],
    ),
    Scenario("null_data_reads_as_no_account", CALLS, [answer({"data": None, "errors": None})]),
    Scenario(
        "a_day_given_twice_counts_once_by_its_last_reading",
        CALLS,
        [answer(web_analytics([rum_day(WEEK_AGO, 7, 3), rum_day(YESTERDAY, 12, 5), rum_day(YESTERDAY, 8, 2)]))],
    ),
    Scenario("a_body_that_is_not_json", CALLS, [answer("<html>Bad gateway</html>")]),
    Scenario("a_null_body_writes_no_row", CALLS, [answer(None)]),
    Scenario("a_list_body_writes_no_row", CALLS, [answer([])]),
    Scenario("a_null_viewer_writes_no_row", CALLS, [answer({"data": {"viewer": None}, "errors": None})]),
    Scenario("an_error_that_is_not_an_object_writes_no_row", CALLS, [answer({"data": None, "errors": ["busy"]})]),
    Scenario("a_seed_line_not_an_object_stops_the_write", CALLS, [answer(WEEK)], seed={FILE: OLDER_ROW + "[1]\n"}),
    # The heal: MONTH read over a file that already holds each of the 30 days but one change.
    Scenario("a_whole_month_read_as_it_is_changes_only_yesterday", CALLS, [answer(MONTH)], seed={FILE: stored_month()}),
    Scenario("heals_a_day_with_no_row", CALLS, [answer(MONTH)], seed={FILE: stored_month(instead={TENTH: ""})}),
    Scenario(
        "heals_a_failed_day",
        CALLS,
        [answer(MONTH)],
        seed={FILE: stored_month(instead={TENTH: fetch_failed(day(TENTH))})},
    ),
    Scenario(
        "heals_a_day_read_under_another_tag",
        CALLS,
        [answer(MONTH)],
        seed={FILE: stored_month(instead={TENTH: stored(day(TENTH), 0, 0, site_tag=OTHER_SITE)})},
    ),
    Scenario(
        "a_good_row_whose_numbers_changed_is_left",
        CALLS,
        [answer(MONTH)],
        seed={FILE: stored_month(instead={TENTH: stored(day(TENTH), 3, 1)})},
    ),
    Scenario(
        "heals_from_the_files_first_row",  # 2026-09-15 failed and 2026-09-16 missing; nothing before is filled
        CALLS,
        [answer(MONTH)],
        seed={FILE: stored_month(22, instead={22: fetch_failed(day(22)), 23: ""})},
    ),
    Scenario(
        "heals_30_days_back_and_no_further",  # failures before 2026-08-24 stay; 2026-08-24 itself is healed
        CALLS,
        [answer(MONTH)],
        seed={FILE: fetch_failed(day(-4)) + fetch_failed(day(-1)) + stored_month(instead={0: fetch_failed(day(0))})},
    ),
    Scenario(
        "the_first_run_with_the_right_tag_heals_every_row_read_without_one",  # rows read zero under a beacon token
        CALLS,
        [answer(MONTH)],
        seed={FILE: "".join(stored(day(n), 0, 0, site_tag=None) for n in range(-3, 29))},
    ),
    Scenario("skips_without_credentials", CALLS, env={"CLOUDFLARE_API_TOKEN": CREDENTIALS["CLOUDFLARE_API_TOKEN"]}),
]
