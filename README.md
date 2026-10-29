# leadline-metrics

Daily adoption and health metrics for software projects.

GitHub keeps your traffic for 14 days, then throws it away. PyPI
downloads, Home Assistant installs, web analytics and forum threads each
live on their own site, in their own shape. `leadline` collects them once
a day into one permanent history, in plain files you own.

- **Kept for good.** One JSON line per day for each thing you track, in a
  folder you choose. No service, no account, no database.
- **Honest.** A failed fetch is recorded as a failure, never as a zero.
  Missed days are filled in where the platform still has them.
- **Loud when it breaks.** Each run checks your config against each
  platform, writes a health row naming anything stale or failing, and
  exits non-zero when something needs you.
- **Runs anywhere with Python 3.8+.** Standard library only. One file,
  `leadline.pyz`, runs on a box with no pip.

> **Status: early.** There is no release on PyPI yet. Install from a
> clone, as the quick start shows.

## Who it's for

Open-source maintainers who want to know, over months and years:

- whether adoption is growing, and on which platform;
- how much support load there is, and how fast it gets answered;
- where users come from;
- what a competing project is doing.

Each **source** reads one kind of data from one platform. Turn on the ones
you need in the config. The [list of sources](docs/CONFIGURATION.md#sources)
says what each reads and links its README, which has its config keys and
credentials.

## Quick start

About five minutes, for one GitHub repo you can push to.

**1. Get it.** You need Python 3.8 or later.

```bash
git clone https://github.com/solentlabs/leadline-metrics
python3 leadline-metrics/tools/build_pyz.py ~/leadline/leadline.pyz
python3 ~/leadline/leadline.pyz --version
```

`leadline.pyz` is the whole tool in one file: copy it anywhere. If you
would rather have a `leadline` command, `python3 -m pip install
./leadline-metrics` (in a virtual environment) gives you one, and once
leadline is released, `pip install leadline-metrics` will too. Either way,
`leadline run ...` (or `python3 -m leadline run ...`) then does what
`python3 leadline.pyz run ...` does.
On Windows, type `py` for `python3`, and `set` for `export` in step 3.

**2. Write a config.** Save this as `~/leadline/leadline.json`, with
`your-name/your-repo` changed to your repo and `your-repo` in the file
name changed to match:

```json
{
  "out_dir": "metrics",
  "sources": {
    "github_repo_stats": [{"repo": "your-name/your-repo"}],
    "github_traffic_daily": [{"repo": "your-name/your-repo"}]
  }
}
```

`out_dir` is relative to the config file.

**3. Give it a token.** Credentials come only from environment variables,
never from the config. Create a
[GitHub token](https://github.com/settings/tokens) that can read the repo.
Traffic needs push access to it.

```bash
export GITHUB_TOKEN=your-token
```

**4. Run it.**

```bash
cd ~/leadline
python3 leadline.pyz run --config leadline.json
```

**5. Look at what it wrote.**

- `metrics/github-repo-stats-your-repo.jsonl`: today's stars, forks and
  14-day traffic totals.
- `metrics/github-traffic-daily-your-repo.jsonl`: views and clones, a row
  per day, back as far as GitHub still has them (14 days).
- `metrics/health.jsonl`: the run's health row.
- `metrics/leadline.log`: the run's log, also printed as it runs.
- `metrics/logs/leadline-YYYY-MM-DD.log`: each night's log, the last 14
  nights.
- `metrics/SUMMARY.md`: the latest numbers, how they changed, and
  anything that needs you.
- `metrics/trends.html`: the same numbers charted over time; open it in
  a browser. `metrics/trends.json` holds its data.

A log line `SKIP: github_repo_stats (GITHUB_TOKEN not set)` means the
token isn't in the environment the run sees: the source is skipped, not
failed. A run with no credentials at all skips every source that needs one
and still exits 0, with "All metrics sources current" in the summary, so
check the log for `SKIP` lines on a first run. What a token needs is in
each source's README.

Run it again tomorrow, and every day after. The next section shows how.

The sources that rebuild history (issues, response times, contributor
regions, catalog counts) write only today's row on the first run and fill in
the days before it on the first run of a later day; see
[History on first use](docs/CONFIGURATION.md#history-on-first-use).

## Run it every day

leadline is made to run once a day from a scheduler, at the same time
each day. A second run on the same day replaces that day's rows. Each
recipe gives the token to what the scheduler runs, never to the config.

Run it late in your day, as the cron, launchd and Windows recipes do, at
23:30. The `pypi` source
needs pypistats.org to have published the run's date, which it does just
after that UTC day ends, so a run late in the evening in a time zone west
of UTC (23:30 in the Americas) finds it. A run earlier, in UTC or east of
it records that date as a failure row, "pypistats has not published" it,
which the next run fills in.

The code uses only the standard library and no Unix-only calls. CI tests
it on Linux, on Python 3.8 through 3.14, and on macOS and Windows, on
Python 3.14.

### Linux and macOS: cron

Save `~/leadline/nightly.sh`, then `chmod 700 ~/leadline/nightly.sh`:

```sh
#!/bin/sh
export GITHUB_TOKEN=your-token
cd "$HOME/leadline" && exec python3 leadline.pyz run --config leadline.json
```

Add it with `crontab -e`, here at 23:30 each day:

```text
30 23 * * * $HOME/leadline/nightly.sh
```

cron doesn't read your shell's profile, so give the full path to
`python3` in the script if cron can't find it.

### macOS: launchd

Run the same `nightly.sh` from a LaunchAgent. Save it as
`~/Library/LaunchAgents/com.example.leadline.plist`, with your home
folder in the path, then `launchctl load` it:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.example.leadline</string>
  <key>ProgramArguments</key><array><string>/Users/you/leadline/nightly.sh</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>23</integer><key>Minute</key><integer>30</integer></dict>
</dict>
</plist>
```

Unlike cron, launchd runs a job it missed while the Mac was asleep.

### Windows: Task Scheduler

Keep the files in `%USERPROFILE%\leadline`, which only your account can
read, and save this there as `nightly.cmd`:

```bat
@echo off
set GITHUB_TOKEN=your-token
cd /d "%USERPROFILE%\leadline"
py leadline.pyz run --config leadline.json
```

Schedule it from a Command Prompt:

```bat
schtasks /Create /TN leadline /SC DAILY /ST 23:30 /TR "%USERPROFILE%\leadline\nightly.cmd"
```

`py` is the Python launcher that comes with Python from python.org; use
`python` if you don't have it. The task runs only while you are signed
in, unless you set "Run whether user is logged on or not" in Task
Scheduler.

### A NAS with no pip

A NAS that has Python 3.8 or later runs `leadline.pyz` as is: nothing to
install. On a Synology, for example, create a scheduled task
(Control Panel, Task Scheduler, Create, Scheduled Task, User-defined
script) with the token and the run in its script:

```sh
export GITHUB_TOKEN=your-token
cd /volume1/leadline && python3 leadline.pyz run --config leadline.json
```

### GitHub Actions

With no machine of your own, GitHub can run leadline each day in a repo
of yours and commit what it writes back to that repo. Make a repo for
your metrics (private, if you like), commit `leadline.pyz` and your
`leadline.json` at its top level, and save this beside them as
`.github/workflows/leadline.yml`:

```yaml
name: leadline

on:
  schedule:
    - cron: "30 22 * * *"  # each day at 22:30 UTC
  workflow_dispatch:  # a Run workflow button on the Actions tab

permissions:
  contents: write  # to push what leadline wrote

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - id: leadline
        env:
          GITHUB_TOKEN: ${{ secrets.LEADLINE_GITHUB_TOKEN }}
          GOATCOUNTER_TOKEN: ${{ secrets.LEADLINE_GOATCOUNTER_TOKEN }}
          CLOUDFLARE_API_TOKEN: ${{ secrets.LEADLINE_CLOUDFLARE_API_TOKEN }}
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.LEADLINE_CLOUDFLARE_ACCOUNT_ID }}
        run: python3 leadline.pyz run --config leadline.json
      # Runs when leadline exits 1 too, so a failed night's rows are kept.
      - if: ${{ !cancelled() && steps.leadline.outcome != 'skipped' }}
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add -A
          git diff --cached --quiet || git commit -m "leadline $(date -u +%F)"
          git push
```

Then, in the repo's Settings, under Secrets and variables, Actions, add
a secret `LEADLINE_GITHUB_TOKEN` holding a token like the quick start's.
GitHub refuses secret names that start with `GITHUB_`, so each secret
has `LEADLINE_` in front of the variable leadline reads. Add
`LEADLINE_GOATCOUNTER_TOKEN`, `LEADLINE_CLOUDFLARE_API_TOKEN` and
`LEADLINE_CLOUDFLARE_ACCOUNT_ID` only if you use their sources: a secret
you don't add is empty, and a source whose credential is empty is
skipped. Try it with Run workflow on the Actions tab.

- **Actions' own token isn't enough.** Each run gets a token of its own,
  which pushes the commit, but it can't read traffic or any other
  private repo. The `github_*` sources need yours.
- **Schedules run late.** The cron time is UTC, and so is each row's
  date. When Actions is busy, above all at the start of each hour,
  GitHub starts a scheduled run late or drops it. A run that starts
  after midnight UTC is dated the next day, and a missed day stays
  missing unless the source fills it in from the platform's history.
- **Idle public repos.** GitHub turns off the schedule in a public repo
  with no activity for 60 days. It doesn't say whether the workflow's
  own commits count, so if the runs stop, turn the workflow back on from
  the Actions tab. Private repos aren't affected.
- **PyPI fails more often.** pypistats.org limits requests per IP
  address, and Actions shares its addresses among many users, so `pypi`
  gets refused more often than at home. A refused night is recorded as
  a failure and filled in from pypistats' history on the next good
  night. The recipe runs at 22:30 UTC, before pypistats has published
  that UTC day, so each night's `pypi` row is a failure row until the
  next night's run fills it in. One such night isn't critical, so the job
  doesn't fail for it.
- **A failed job is the alert.** When something is critical, leadline
  exits 1, the job fails, and GitHub notifies whoever last changed the
  cron line. The commit step still runs.
- **Pushes can collide.** If you push to the repo while a run is going,
  the run's push fails and the job fails. That night's rows are lost,
  and, as with a missed day, only a source that reads the platform's
  history fills them in.
- **Upgrading** is committing a newer `leadline.pyz` over the old one.

## What you get

**Rows.** Each file holds one JSON object per line, one line per day.
A line from `github-repo-stats-your-repo.jsonl`:

```json
{"date": "2026-09-28", "repo": "your-name/your-repo", "stars": 41, "forks": 6, "watchers": 5, "open_issues": 3, "network_count": 6, "size_kb": 1024, "pushed_at": "2026-09-28T09:12:44Z", "views_14d": 217, "views_unique_14d": 42, "clones_14d": 28, "clones_unique_14d": 14, "captured_at": "2026-09-28T23:30:04+01:00"}
```

A failed fetch writes a row with a `status` and a `reason`, and no
counts, so a gap never looks like a zero.

**Summary.** After a week, `SUMMARY.md` reads:

```markdown
<!-- leadline:start summary -->
## Summary

- Run: 2026-09-28
- All sources current

### github_repo_stats: your-name/your-repo

- Stars: 41 · +5 in 7d
- Forks: 6 · +0 in 7d
- Views, 14 days: 217 · +30 in 7d

### github_traffic_daily: your-name/your-repo

- Views: 162 in 7d (+26)
- Clones: 29 in 7d (+6)
<!-- leadline:end summary -->
```

It shows each source's main numbers, with their change over 7 and 30
days once there is that much history. leadline rewrites only the text
between its markers, so the rest of the file is yours.

To choose what it shows, run `python3 leadline.pyz default-reports
--config leadline.json`. It prints the summary as a `reports` setting:
paste it into your config and change it. `"reports": []` writes no
summary. Tables by day or week, version breakdowns and more are in
[docs/REPORTS.md](docs/REPORTS.md).

After you change `reports`, `projects` or `pages`, rebuild the summary and
the charts from the files already there, without collecting anything (no
network, no tokens, no new row):

```bash
python3 leadline.pyz report --config leadline.json
```

**Trends.** `trends.html` charts each number the summary shows, day by
day, over the last 30 or 90 days, a year, or everything. Hover over a
chart for the values. With several projects, the `projects` setting
groups the charts by project, with links between them
([Projects](docs/CONFIGURATION.md#projects)), and the `pages` setting
gives each portfolio of projects its own page
([Pages](docs/CONFIGURATION.md#pages)). It is one file that needs
no server and loads nothing from the internet: double-click it. Its data
is also in `trends.json`, for your own tools, one section per summary
section:

```json
{
  "source": "github_repo_stats",
  "name": "your-name/your-repo",
  "title": "github_repo_stats: your-name/your-repo",
  "label": "Repo stats",
  "heading": "Traffic · your-name/your-repo",
  "project": null,
  "file": "github-repo-stats-your-repo.jsonl",
  "where": null,
  "dates": ["2026-09-21", "2026-09-28"],
  "fields": [
    {"field": "stars", "label": "Stars", "kind": "snapshot", "color": "s3", "values": [36, 41]},
    {"field": "forks", "label": "Forks", "kind": "snapshot", "color": "s6", "values": [6, 6]},
    {"field": "views_14d", "label": "Views, 14 days", "kind": "snapshot", "charted": false, "values": [187, 217]}
  ],
  "card": "Traffic",
  "row": "Stars and forks",
  "about": "The repository's stars and forks, as of each day."
}
```

Its format is in [docs/TRENDS.md](docs/TRENDS.md).

**Health.** `health.jsonl` gets one row per run: files that are stale,
failing or missing days, each source's config check, what is critical,
and any past day whose numbers changed
([details](docs/ARCHITECTURE.md#health)).

**Exit status.** 0 when nothing needs you, 1 when something is critical
(a source failing two nights running, a rejected token, a repo that
doesn't exist), 2 when the config is unusable and nothing ran
([details](docs/ARCHITECTURE.md#exit-status)).

## Going further

- [docs/CONFIGURATION.md](docs/CONFIGURATION.md): every setting,
  credential and source entry, with a fuller example.
- [docs/REPORTS.md](docs/REPORTS.md): building your own summaries.
- [docs/TRENDS.md](docs/TRENDS.md): the format of `trends.json`, and
  the charts in `trends.html`.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how a run works, and
  what each source reads.
- [docs/ADDING_A_SOURCE.md](docs/ADDING_A_SOURCE.md): adding a platform.
- [docs/ARCHITECTURE_DECISIONS.md](docs/ARCHITECTURE_DECISIONS.md): why
  it works the way it does.

## Contributing

Setup, checks and the release process are in
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT, see [LICENSE](LICENSE).
