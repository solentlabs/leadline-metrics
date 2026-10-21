"""The oracle harness: shims, fail-closed network, and a script that can't reach the real folder.

The tests run a small stand-in script, so they run everywhere, CI included. The scenarios' goldens,
which the real collect-metrics.sh writes, have their own test (tests/oracle/test_scenario_goldens.py).
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import script_harness
from script_harness import MAIN_LINE, prepare, run_script

pytestmark = script_harness.script_side

BASE_DIR_LINE = 'BASE_DIR="/srv/example/community"'
STAND_IN = f"""#!/bin/bash
set -euo pipefail
{BASE_DIR_LINE}
METRICS_DIR="${{BASE_DIR}}/metrics"
SITES=("real:tag")
NAME="real"
DATE=$(date +%Y-%m-%d)
ORDER=("${{SITES[@]}}")

collect_fake() {{
    local got posted code
    got=$(curl --connect-timeout 10 --max-time 60 -sf -H "User-Agent: x" "https://example.test/get")
    posted=$(curl -sf -X POST -H "Content-Type: application/json" -d '{{"q": 1}}' "https://example.test/post")
    printf 'a\\r\\nb\\n' > "${{TMPDIR}}/payload"
    code=$(curl -sS -w '\\n%{{http_code}}' --data @"${{TMPDIR}}/payload" -D "${{TMPDIR}}/h" "https://example.test/file")
    sleep 600
    python3 - "$got" "$posted" "$code" "$DATE" "$(date '+%Y-%m-%d %H:%M:%S')" "$(grep -i '^link' "${{TMPDIR}}/h")" \\
        "${{GITHUB_TOKEN:-unset}}" "${{DUMMY_TOKEN:-unset}}" >> "${{METRICS_DIR}}/fake.jsonl" <<'PYTHON'
import json, sys, time, urllib.request
from datetime import datetime, timezone
time.sleep(600)
got, posted, code, date, clock, link, token, dummy = sys.argv[1:]
with urllib.request.urlopen(urllib.request.Request("https://example.test/post", b'{{"q": 1}}')) as resp:
    via_urllib = json.load(resp)
print(json.dumps({{
    "date": date, "got": json.loads(got), "posted": json.loads(posted), "code": code, "clock": clock,
    "link": link.strip(), "now": datetime.now().isoformat(), "utc": datetime.now(timezone.utc).isoformat(),
    "via_urllib": via_urllib, "github_token": token, "dummy_token": dummy,
}}))
PYTHON
}}

main() {{
    collect_fake
}}

{MAIN_LINE}
"""


def exchange(url: str, body: str, method: str = "GET", sent: str | None = None, **response: Any) -> dict[str, Any]:
    request = {"method": method, "url": url, "body": sent}
    return {"request": request, "response": {"status": 200, "body": body, **response}}


def make_case(
    tmp_path: Path,
    calls: list[str],
    exchanges: list[dict[str, Any]],
    env: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
    tz: str | None = None,
) -> Path:
    case = tmp_path / "case"
    (case / "responses").mkdir(parents=True)
    spec: dict[str, Any] = {"now": "2026-09-23T23:31:05", "calls": calls, "env": env or {}}
    if config is not None:
        spec["config"] = config
    if tz is not None:
        spec["tz"] = tz
    (case / "case.json").write_text(json.dumps(spec), encoding="utf-8")
    for i, e in enumerate(exchanges):
        (case / "responses" / f"{i:02d}.json").write_text(json.dumps(e), encoding="utf-8")
    return case


def run_stand_in(tmp_path: Path, case: Path) -> script_harness.Run:
    script = tmp_path / "stand-in.sh"
    script.write_text(STAND_IN, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    return run_script(case, work, script)


EXCHANGES = [
    exchange("https://example.test/get", '{"n": 1}'),
    exchange("https://example.test/post", '{"n": 2}', method="POST", sent='{"q": 1}'),  # curl's
    exchange("https://example.test/post", '{"n": 3}', method="POST", sent='{"q": 1}'),  # then urllib's
    exchange(
        "https://example.test/file", "{}", method="POST", sent="ab", status=201, headers={"Link": '<x>; rel="next"'}
    ),
]


def test_the_script_runs_under_the_shims(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-leak")
    case = make_case(tmp_path, ["collect_fake"], EXCHANGES, env={"DUMMY_TOKEN": "dummy"})

    started = time.monotonic()
    run = run_stand_in(tmp_path, case)

    assert time.monotonic() - started < 60, "sleep 600 (bash and Python) must return at once"
    assert (run.returncode, run.unmatched) == (0, []), run.output
    assert json.loads((run.metrics / "fake.jsonl").read_text(encoding="utf-8")) == {
        "date": "2026-09-23",
        "got": {"n": 1},
        "posted": {"n": 2},
        "code": "{}\n201",
        "clock": "2026-09-23 23:31:05",
        "link": 'Link: <x>; rel="next"',
        "now": "2026-09-23T23:31:05",
        "utc": "2026-09-23T23:31:05+00:00",
        "via_urllib": {"n": 3},
        "github_token": "unset",
        "dummy_token": "dummy",
    }
    assert run.metrics == tmp_path / "work" / "community" / "metrics"


def test_the_clock_is_local_time_in_the_cases_zone(tmp_path: Path) -> None:
    """`now` is the local time in `tz`: the dates and clock the script reads are local, UTC is offset."""
    case = make_case(tmp_path, ["collect_fake"], EXCHANGES, tz="America/New_York")

    run = run_stand_in(tmp_path, case)

    assert (run.returncode, run.unmatched) == (0, []), run.output
    written = json.loads((run.metrics / "fake.jsonl").read_text(encoding="utf-8"))
    assert (written["date"], written["clock"], written["now"]) == (
        "2026-09-23",
        "2026-09-23 23:31:05",
        "2026-09-23T23:31:05",
    )
    assert written["utc"] == "2026-09-24T03:31:05+00:00"


def test_seed_files_are_in_the_metrics_folder_before_the_run(tmp_path: Path) -> None:
    case = make_case(tmp_path, ['cp "${METRICS_DIR}/old.jsonl" "${METRICS_DIR}/copied.jsonl"'], [])
    (case / "seed").mkdir()
    (case / "seed" / "old.jsonl").write_text('{"date": "2026-09-22"}\n', encoding="utf-8")

    run = run_stand_in(tmp_path, case)

    assert (run.metrics / "copied.jsonl").read_text(encoding="utf-8") == '{"date": "2026-09-22"}\n'


def test_responses_for_one_request_are_served_once_each_in_order(tmp_path: Path) -> None:
    """A request after its saved responses are all served has none left: it fails and is recorded."""
    url = "https://example.test/get"
    exchanges = [exchange(url, "", status=429), exchange(url, "ok")]
    calls = [f'curl -s -w " %{{http_code}}\\n" "{url}" >> "${{METRICS_DIR}}/out.txt"'] * 3
    run = run_stand_in(tmp_path, make_case(tmp_path, calls, exchanges))

    assert (run.metrics / "out.txt").read_text(encoding="utf-8") == " 429\nok 200\n 000\n"
    assert run.unmatched == [f"GET {url}"]


def test_curl_fail_flag_fails_on_http_errors(tmp_path: Path) -> None:
    url = "https://example.test/get"
    calls = [f'curl -sf "{url}" || echo "exit $?" >> "${{METRICS_DIR}}/out.txt"']
    run = run_stand_in(tmp_path, make_case(tmp_path, calls, [exchange(url, "gone", status=404)]))

    assert (run.metrics / "out.txt").read_text(encoding="utf-8") == "exit 22\n"


def test_unknown_requests_fail_closed_without_touching_the_network(tmp_path: Path) -> None:
    """No saved response means failure, recorded, and no connection: not even to a listening local port."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        probe = (
            "import socket, sys, urllib.request\n"
            "try:\n    urllib.request.urlopen(sys.argv[1])\nexcept OSError as e:\n    print('urlopen:', e)\n"
            "try:\n    socket.socket().connect(('127.0.0.1', int(sys.argv[2])))\nexcept OSError as e:\n"
            "    print('socket:', e)\n"
            "try:\n    socket.getaddrinfo('pypistats.org', 443)\nexcept OSError as e:\n    print('dns:', e)\n"
        )
        out = '>> "${METRICS_DIR}/out.txt" 2>&1'
        calls = [
            f'curl -sS "https://pypistats.org/api/packages/leadline/recent" {out} || echo "exit $?" {out}',
            f'curl -sS "http://127.0.0.1:{port}/" {out} || echo "exit $?" {out}',
            f'python3 -c "{probe}" http://127.0.0.1:{port}/ {port} {out}',
        ]
        run = run_stand_in(tmp_path, make_case(tmp_path, calls, []))

        listener.setblocking(False)
        with pytest.raises(BlockingIOError):
            listener.accept()

    assert (run.metrics / "out.txt").read_text(encoding="utf-8").splitlines() == [
        "curl: (7) no saved response: GET https://pypistats.org/api/packages/leadline/recent",
        "exit 7",
        f"curl: (7) no saved response: GET http://127.0.0.1:{port}/",
        "exit 7",
        f"urlopen: <urlopen error no saved response for GET http://127.0.0.1:{port}/>",
        "socket: network blocked by the oracle harness",
        "dns: network blocked by the oracle harness",
    ]
    assert run.unmatched == [
        "GET https://pypistats.org/api/packages/leadline/recent",
        f"GET http://127.0.0.1:{port}/",
        f"GET http://127.0.0.1:{port}/",
    ]


def test_python_without_the_harness_settings_exits(tmp_path: Path) -> None:
    """sitecustomize ends the process rather than run with the network open."""
    env = script_harness.environment(tmp_path, tmp_path, "2026-09-23T23:31:05", {})
    del env["LEADLINE_ORACLE_NOW"]

    done = subprocess.run(["python3", "-c", "print('ran')"], env=env, capture_output=True, text=True, check=False)

    assert (done.returncode, done.stdout) == (70, "")


@pytest.mark.parametrize("args", [["-k", "https://x"], ["-sf", "-w", "%{http_code}", "https://x"], ["-s"]])
def test_curl_refuses_what_it_does_not_implement(tmp_path: Path, args: list[str]) -> None:
    env = script_harness.environment(tmp_path, tmp_path, "2026-09-23T23:31:05", {})

    done = subprocess.run(["curl", *args], env=env, capture_output=True, text=True, check=False)

    assert done.returncode == 2
    assert done.stderr.startswith("curl shim: unsupported")


def test_date_refuses_what_it_does_not_implement(tmp_path: Path) -> None:
    env = script_harness.environment(tmp_path, tmp_path, "2026-09-23T23:31:05", {})

    done = subprocess.run(["date", "-d", "yesterday"], env=env, capture_output=True, text=True, check=False)

    assert done.returncode == 2


def test_config_overrides_the_scripts_constants(tmp_path: Path) -> None:
    """A case's `config` replaces constants, and what the script derives from them after its setup."""
    calls = ['printf "%s;" "${SITES[@]}" "${NAME}" "${ORDER[@]}" > "${METRICS_DIR}/out.txt"']
    case = make_case(tmp_path, calls, [], config={"SITES": ["fake:one", "it's two"], "NAME": "a b"})

    run = run_stand_in(tmp_path, case)

    assert (run.metrics / "out.txt").read_text(encoding="utf-8") == "fake:one;it's two;a b;fake:one;it's two;"


def test_prepare_puts_overrides_after_the_constants(tmp_path: Path) -> None:
    prepared = prepare(STAND_IN, tmp_path, ["main"], {"NAME": "x"})

    lines = prepared.splitlines()
    assert lines.index("NAME=x") == lines.index("DATE=$(date +%Y-%m-%d)") - 1
    assert lines.index('NAME="real"') < lines.index("NAME=x")


@pytest.mark.parametrize(
    ("script", "config"),
    [
        (STAND_IN, {"MISSING": "x"}),
        (STAND_IN, {"NAME=x; rm -rf /": "x"}),
        (STAND_IN.replace("DATE=$(date +%Y-%m-%d)", "DATE=today"), {"NAME": "x"}),
    ],
    ids=["unknown constant", "not a name", "no date line"],
)
def test_prepare_refuses_a_config_it_cannot_apply(tmp_path: Path, script: str, config: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"^config: "):
        prepare(script, tmp_path, ["main"], config)


def test_prepare_rewrites_base_dir_and_the_calls(tmp_path: Path) -> None:
    prepared = prepare(STAND_IN, tmp_path, ["collect_fake", 'collect_x "a b"'])

    assert f'BASE_DIR="{tmp_path}"\n' in prepared
    assert prepared.endswith('\ncollect_fake || true\ncollect_x "a b" || true\n')
    assert "/srv/example/community" not in prepared


@pytest.mark.parametrize(
    "script",
    [
        STAND_IN.replace(BASE_DIR_LINE, f'{BASE_DIR_LINE}\nBASE_DIR="/elsewhere"'),
        STAND_IN.replace(MAIN_LINE, "main"),
        STAND_IN.replace("DATE=$(", 'OUT="/srv/example/community/x"\nDATE=$('),
        STAND_IN.replace("DATE=$(", 'OUT="/volume2/x"\nDATE=$('),
        STAND_IN.replace("DATE=$(", 'OUT="/mnt/d/x"\nDATE=$('),
        STAND_IN.replace("got=$(curl", "got=$(/usr/bin/curl"),
        STAND_IN.replace("DATE=$(date", "DATE=$(/bin/date"),
        STAND_IN.replace("python3 -", "python3 -I -"),
    ],
    ids=[
        "two base dirs",
        "no main line",
        "base dir named again",
        "NAS volume",
        "mounted drive",
        "real curl",
        "real date",
        "isolated python",
    ],
)
def test_prepare_refuses_a_script_that_could_escape(tmp_path: Path, script: str) -> None:
    refusal = r"^(expected one|script still names a real path|script could bypass the shims)"
    with pytest.raises(ValueError, match=refusal):
        prepare(script, tmp_path, ["main"])
