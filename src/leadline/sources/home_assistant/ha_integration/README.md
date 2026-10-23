# ha_integration

- **Reads:** a custom integration's installs by version, and its rank among all
  of them, from Home Assistant's analytics.
- **Requests:** `custom_integrations.json` from analytics.home-assistant.io,
  once a run.
- **Credentials:** none.
- **Writes:** one row a day per entry, to its default file unless the entry sets
  `file`: the integration's total, its versions, its rank and the number of
  integrations tracked.
- **Config entry:** `sources.ha_integration` is a list; each entry has
  `integration`, the integration's domain, as the feed lists it (a name the feed
  doesn't list reads as no installs every night, and `check` says so), and,
  optionally, `bands` ([see its values](#bands)) and `notes` ([see its format](#notes)).

```json
{"integration": "my_integration", "bands": "release",
 "notes": [{"date": "2026-10-22", "text": "HA changed the feed"}]}
```

## Reading the rank

The rank is a position in the feed as it stands that night, and Home Assistant
changes what the feed lists. Trends therefore charts two lines on one axis from
zero: the integrations the feed lists that night, and the integrations we
outrank (that count less our rank), shaded between. When the feed's list
changes, both lines rise together and the gap hardly moves. Hovering a night
adds its rank and its percentile. The summary shows the count, the rank and the
percentile. Installs by version keep their own chart.

## `notes`

`notes`, optional: up to 10 `{"date": "YYYY-MM-DD", "text": "..."}` objects,
the text 1 to 60 characters. Each is a dashed vertical line with its text on the
chart, where its date is inside the range drawn: the date Home Assistant changed
what its feed lists, for one. Only the page changes; rows and the summary are
the same. See [Notes](../../../../../docs/CONFIGURATION.md#notes).

## `bands`

`bands`, optional: how the trends page bands the installs by version. A
version is `3.14.1`; its release line is `3.14`.

| Value | Bands |
| ----- | ----- |
| `line` | by release line: the 5 newest, the older ones added up as "older". The default |
| `release` | by exact release: every release the file holds, none added up |

- Prereleases are the same either way: a band for each base version's
  betas.
- Only the page changes; rows and the summary are the same. Any other value
  is a config error.

How the source works and where it differs from the script it replaces:
the docstring in `__init__.py`.
