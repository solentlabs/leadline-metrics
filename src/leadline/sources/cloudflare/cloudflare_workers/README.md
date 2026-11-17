# cloudflare_workers

- **Reads:** each Cloudflare Worker's invocations as a row a day, kept for good
  where Cloudflare keeps them three months: requests, errors and
  subrequests, the invocations by how they ended, and the CPU time's 50th and
  99th percentiles. The first run reads three months; every run after, the
  last 28 days.
- **Requests:** Cloudflare's GraphQL Analytics API, one query per entry for
  the 28 days ending yesterday (UTC), three on the first run, from the account's
  `workersInvocationsAdaptive` dataset filtered by the Worker's script name.
  `check` asks for the 7 days ending yesterday.
- **Credentials:** `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`; the
  source is skipped while either is unset. The token needs the account
  permission Account Analytics: Read.
- **Writes:** one row a day per entry, to its default file unless the entry
  sets `file`: the day's `script`, `status`, the fields below, and
  `captured_at`.
- **Config entry:** `sources.cloudflare_workers` is a list; each entry has
  `script`, the Worker's script name (its name in the dashboard's Workers &
  Pages list).

```json
{"script": "example-worker"}
```

## The row

A row is one UTC day of the Worker. Every count is a whole number.

| Field | Meaning |
| ----- | ------- |
| `requests` | the day's invocations |
| `errors` | the invocations that ended in an error |
| `subrequests` | the requests the Worker made (`fetch` calls and the like) |
| `outcomes` | `{status: invocations}`, by how they ended, as Cloudflare names it (`success`, `clientDisconnected`, `scriptThrewException`, `exceededResources`, `internalError`, or any other it gives), the largest first, with the name deciding a tie, without zeros |
| `cpu_us` | `{"p50": n, "p99": n}`, the 50th and 99th percentile CPU time of an invocation in microseconds, rounded; `{}` on a day with no invocations |

## How a run goes

- **The first run** (a file with no row before the last 28 days) reads the
  84 days ending yesterday, in three queries of 28 days (a query spans at
  most a month, and Cloudflare keeps three months), and writes a row for
  each day from the first one Cloudflare has to yesterday.
- **Every run after** reads the 28 days ending yesterday and rewrites them: a day
  with no invocations is a row of zeros, and a value Cloudflare changed is
  corrected (each correction is recorded for health). Days older than the 28
  are never touched.
- **A failure** writes a failure row for yesterday: `fetch_failed` (with
  `http_code`), `graphql_error` (Cloudflare's messages), `no_account` (the
  account filter matched nothing) or `parse_failed` (an answer that is not
  JSON, or of another shape). The answer is read whole or not at all, and a
  failure never replaces a good row.
- **A token Cloudflare rejects** (401, 403) is not retried and is critical at
  once. `check` reports a rejected token, or an account the token can't read,
  as an error, and a Worker with no invocations in 7 days (a misspelled
  script name reads that way every night) as a warning.

## What is never kept

The rows hold counts per Worker only; the dataset has no visitor data.

## Trends

The default summary shows the day's requests and errors; the trends page
also draws the outcomes as a stack and the CPU time percentiles as lines.
