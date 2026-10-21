# cloudflare_zone_traffic

- **Reads:** a Cloudflare zone's HTTP traffic as a row a day, kept for good
  where Cloudflare keeps it for a year (daily totals) or 31 days (per-request
  detail): requests, cached and encrypted requests and bytes, page views,
  threats, unique visitors and visits, and the counts by country, status
  code, browser, operating system, device type, data center, path, host and
  more. Counts only, never a visitor's address.
- **Requests:** Cloudflare's GraphQL Analytics API, one query for the daily
  dataset (the whole year on the first run, then the last 30 days) and one for
  each of the per-request dataset's parts (the day's visits and origin
  response times, and each of 13 lists), for the last 30 days. `check` asks
  for one day of the zone.
- **Credentials:** `CLOUDFLARE_API_TOKEN`; the source is skipped while it is
  unset. The token needs the zone permission Analytics: Read
  (Zone > Analytics > Read), for the zone or all zones. No account id is
  needed, as the zone is named in the config.
- **Writes:** one row a day per entry, to its default file unless the entry
  sets `file`: the day's `zone` (the entry's label), `status`, the fields
  below, `backfilled` where the day was filled from history, and `captured_at`.
- **Config entry:** `sources.cloudflare_zone_traffic` is a list; each entry
  has `label`, `zone`, the zone's tag (its id, on the zone's Overview page in
  the dashboard), and, optionally, `top`, the length of each list (default 50,
  at most 1000).

```json
{"label": "example", "zone": "0123456789abcdef0123456789abcdef"}
```

## The row

A row is one UTC day of the zone, written for yesterday and healed for the 30
days before it. Every count is a whole number; a map is `{name: count}`, the
largest first, with the name deciding a tie, and an item with no name is
`(none)`.

| Fields | From | Meaning |
| ------ | ---- | ------- |
| `requests`, `cached_requests`, `bytes`, `cached_bytes`, `encrypted_requests`, `encrypted_bytes`, `page_views`, `threats`, `uniques` | the daily dataset | the day's totals; `uniques` is unique visitors |
| `countries`, `country_bytes`, `country_threats` | the daily dataset | requests, bytes and threats by country name |
| `status_codes`, `browser_page_views`, `content_types`, `content_type_bytes`, `http_versions`, `ssl_protocols`, `ip_classes`, `threat_paths` | the daily dataset | requests (page views for `browser_page_views`, bytes for `content_type_bytes`) by status code, browser family, content type, HTTP version, SSL protocol, class of address (`clean`, `botnet`, and so on) and kind of threat |
| `visits`, `origin_response_ms` | the per-request dataset | the day's visits, and the origin's mean (`avg`) and 50th, 95th and 99th percentile (`p50`, `p95`, `p99`) response time in milliseconds |
| `paths`, `hosts`, `user_agents`, `browsers`, `operating_systems`, `device_types`, `data_centers`, `cache_statuses`, `methods`, `bot_categories`, `security_actions`, `security_sources`, `request_sources` | the per-request dataset | the top `top` values by request count |

The daily dataset gives exact totals for a whole day. The per-request dataset
is sampled by Cloudflare, so its counts are estimates, and it keeps 31 days,
so a day older than that has the daily fields only.

## How a run goes

- **The first run** reads the 363 days of the daily dataset that Cloudflare answers for (it keeps a year) and writes
  a row for every day from the first one Cloudflare has to yesterday, each
  marked `backfilled` but yesterday's. A day the zone had no requests on, after
  its first, is a row of zeros. The per-request fields are filled for the last 30
  days. Until the file holds a row older than 30 days, a run reads the year.
- **Every run after** reads the last 30 days. A good row keeps its fields; the
  daily totals are corrected to what Cloudflare says now (each correction is
  recorded for health), and a per-request field is added where the row lacks
  it, never changed (it is a sample). A day with no row or a failed one is
  written fresh.
- **A part that fails** (a list's query fails or can't be read) leaves its
  field null in yesterday's row, which is then `partial` with a `reason` naming
  each failure; an older day's row simply lacks the field. The next run fills
  it. The daily dataset failing is a failure row (`fetch_failed`,
  `graphql_error`, `no_zone` or `parse_failed`). A failure never replaces a good
  row.
- **A token Cloudflare rejects** (401, 403) is not retried and is critical at
  once. `check` reports a token without the permission, or a zone the token
  can't see, as an error.

## What is never kept

A visitor's address, or anything else that identifies a visitor, is never asked
for or kept: leadline holds counts (ADR: zone traffic is kept as counts). The
lists name the paths, browsers and places that requests came from, not who made
them. A path or user agent is text from the requests Cloudflare saw, so a site
that puts a personal value in its URLs (an email address, an id) would see it in
`paths`; keep such values out of the URL path.

## Trends

The trends page draws the day's requests, page views, visits, unique visitors,
threats, bytes, cached requests and cached bytes, and stacks of the status
codes, countries, cache statuses, HTTP versions, browsers, operating systems,
device types, data centers, hosts, paths and content types, and the origin's
response time as lines. The default summary shows the day's counts and the
status codes. The other fields are in the file for the reports that name them.
