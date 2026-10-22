# cloudflare_analytics_engine

- **Reads:** a Workers Analytics Engine dataset's daily totals over SQL, each
  dimension on its own; the last `refetch_days` days are read again every run.
- **Requests:** the dataset's events, one SQL query per field of each layout.
- **Credentials:** `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`; the
  source is skipped while either is unset. The token needs the account
  permission Account Analytics: Read (the SQL API).
- **Writes:** one row a day per dataset, to its default file unless the entry
  sets `file`.
- **Config entry:** `sources.cloudflare_analytics_engine` is a list; each entry
  has `dataset`, `layouts` ([see its keys](#layouts)) and, optionally,
  `refetch_days` (default 7, at most 90).

```json
{"dataset": "example_events", "layouts": [{"dimensions": {"event": {"column": "blob1", "pattern": "[a-z.]+"}}, "fields": [{"key": "events", "by": "event"}]}]}
```

## `layouts`

`layouts`: what the dataset's columns hold, oldest first. Each layout but
the newest ends at `until`, a UTC time (`2026-09-25T12:00:00Z`) when the
app writing the dataset changed its layout. A layout is read only for the
events of its own time, and a day that holds a switch adds up what each
layout read of it.

Each layout has:

- **`dimensions`**, each named: the name is its alias in the query, so
  lowercase letters, digits and `_`, from a letter, and not `day`, `n`,
  `timestamp`, `dataset` or the name of a column (`blob1`, `double2`), which
  the alias would shadow. Each has:
  - `column`: `index1`, or `blob1` to `blob20`;
  - its allowed values: a closed list (`values`), or a regex the whole value
    must match (`pattern`);
  - optionally `only_when` (`{"country": ["US"]}`): set only on events whose
    other dimension has one of those values.

  Choose a list for a closed set (countries, browsers): any other word is
  left out and critical. Choose a pattern for a set that grows (an app's
  screens): a new value of the right shape is written without a config edit,
  and only a value of another shape (an email, an id) is left out and
  critical. A pattern can't tell one word from another, so it gives up
  catching a wrong word of the right shape.
- **`private`** (optional): the columns that hold a per-person id.
- **`fields`**: the row's keys after `date`, `dataset` and `status`, in
  order. Each is one of:
  - a total by a dimension, written as an object of each value's total:
    events counted (`{"key": "events", "by": "event"}`), or with `sum`, a
    `double1` to `double20` summed;
  - a count of events where a column is set, optionally where a dimension
    has one value, written as a number (`{"key": "active_day_shared",
    "count_nonempty": "blob2", "where": {"event": "active.day"}}`).

  A key is the same kind in every layout.

**Privacy rules**, kept by the code whatever the config (ADR: Analytics
Engine datasets are read as totals, one dimension at a time):

- A private column is never selected or grouped by, only tested for being
  set, and no dimension may be one.
- No query totals two dimensions.
- A value outside its dimension's allowed values, or a dimension set where
  its `only_when` doesn't allow it, is left out of the row, never written
  or logged, and is critical, named by the dataset, the dimension and a
  count.

**The script's layout.** The script's collector reads one layout: `blob1`
the event and `blob2` the id counted where set, whose fields `events` and
`active_day_shared` give its queries and rows. A config keeps that layout
first, with its `until`, so the file's history continues.

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
