"""Fakes for running the script's code: a frozen clock, and saved responses in place of the network.

Two uses:

- `python shim.py heredoc NOW RESPONSES HEREDOC [ARG ...]` runs one of the
  script's Python heredocs (tests/test_store.py). NOW is a naive ISO datetime
  that `datetime.now()` returns. RESPONSES is a JSON file holding the replies
  `urllib.request.urlopen` gives, in order, or `-` for none. Each reply is
  `{"body": ...}`, `{"http_error": CODE, "body": ...}` or
  `{"url_error": REASON}`. `time.sleep` returns at once. The heredoc runs with
  `sys.argv[0] == "-"`, as `python3 - ARG ...` gives it in the script, and
  with placeholder Cloudflare credentials, since the usage writer reads them.

- The whole-script harness (script_harness.py) puts `curl` and `date` on PATH
  as `python shim.py curl|date ARG ...`, and a sitecustomize that calls
  `install()` in every Python the script starts. They read the harness's
  environment: `LEADLINE_ORACLE_NOW` (the frozen clock),
  `LEADLINE_ORACLE_RESPONSES` (the saved responses) and
  `LEADLINE_ORACLE_STATE` (where served counts and unmatched requests are
  recorded). Each saved response is served once; a request with none
  left fails, as if offline, and is recorded. Nothing here ever reaches the
  network.

The saved responses' format and serving rules are in docs/TESTING.md,
"Case folders".
"""

from __future__ import annotations

import datetime
import io
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path
from typing import Any, Dict

_real_datetime = datetime.datetime

Exchange = Dict[str, Any]


def freeze_clock(frozen: datetime.datetime) -> None:
    """Make `datetime.now()` return `frozen` (naive, local time), and `time.sleep` return at once."""

    class FrozenDatetime(_real_datetime):
        @classmethod
        def now(cls, tz: Any = None) -> _real_datetime:  # type: ignore[override]
            return frozen if tz is None else frozen.astimezone(tz)

    datetime.datetime = FrozenDatetime  # type: ignore[misc]
    time.sleep = lambda seconds: None


def frozen_now() -> datetime.datetime:
    return _real_datetime.fromisoformat(os.environ["LEADLINE_ORACLE_NOW"])


class Responses:
    """The saved responses, served by request, with what was served recorded under `state`."""

    def __init__(self, folder: Path, state: Path) -> None:
        self.saved: list[Exchange] = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]
        self.state = state

    @classmethod
    def from_env(cls) -> Responses:
        return cls(Path(os.environ["LEADLINE_ORACLE_RESPONSES"]), Path(os.environ["LEADLINE_ORACLE_STATE"]))

    def serve(self, method: str, url: str, body: str | None) -> dict[str, Any] | None:
        """The request's next saved response, each served once, in order.

        None, recorded as unmatched, when none is saved or every one has been served.
        """
        matches = [
            e["response"]
            for e in self.saved
            if (e["request"]["method"], e["request"]["url"], e["request"].get("body")) == (method, url, body)
        ]
        counts_file = self.state / "served.json"
        counts = json.loads(counts_file.read_text(encoding="utf-8")) if counts_file.exists() else {}
        key = json.dumps([method, url, body])
        served = counts.get(key, 0)
        if served == len(matches):
            with (self.state / "unmatched.log").open("a", encoding="utf-8") as f:
                f.write(f"{method} {url}\n")
            return None
        counts[key] = served + 1
        counts_file.write_text(json.dumps(counts), encoding="utf-8")
        response: dict[str, Any] = matches[served]
        return response


# curl's options that take a value, and the name each is kept under (None: ignored).
_CURL_VALUE_OPTIONS = {
    "--connect-timeout": None,
    "--max-time": None,
    "-H": None,
    "-X": "method",
    "-d": "data",
    "--data": "data",
    "-w": "writeout",
    "-o": "output",
    "-D": "header_file",
}


def _curl_options(args: list[str]) -> tuple[dict[str, str], str] | None:
    """The value options and URL by name, and the letters of the -s/-f/-S flags; None when unsupported.

    `data` holds the body to send: for `-d @file`, the file's contents.
    """
    options: dict[str, str] = {}
    flags = ""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in _CURL_VALUE_OPTIONS:
            i += 1
            name = _CURL_VALUE_OPTIONS[arg]
            if name is not None:
                options[name] = args[i]
        elif arg.startswith("-") and len(arg) > 1 and set(arg[1:]) <= set("sfS"):
            flags += arg[1:]
        elif not arg.startswith("-") and "url" not in options:
            options["url"] = arg
        else:
            print(f"curl shim: unsupported argument {arg!r}", file=sys.stderr)
            return None
        i += 1
    if "url" not in options or ("f" in flags and "writeout" in options):
        print("curl shim: unsupported combination of arguments", file=sys.stderr)
        return None
    if options.get("data", "").startswith("@"):
        # curl -d @file strips carriage returns and newlines from the file.
        options["data"] = Path(options["data"][1:]).read_text(encoding="utf-8").replace("\r", "").replace("\n", "")
    return options, flags


def curl_main(args: list[str]) -> int:
    """`curl`, for the options the script uses, answered from the saved responses."""
    parsed = _curl_options(args)
    if parsed is None:
        return 2
    options, flags = parsed
    url, data = options["url"], options.get("data")
    writeout, output, header_file = options.get("writeout"), options.get("output"), options.get("header_file")
    fail = "f" in flags
    method = options.get("method") or ("POST" if data is not None else "GET")
    loud = "S" in flags or "s" not in flags

    response = Responses.from_env().serve(method, url, data)
    status = 0
    exit_code = 0
    body = b""
    if response is None or "error" in response:
        reason = "no saved response" if response is None else response["error"]
        if loud:
            print(f"curl: (7) {reason}: {method} {url}", file=sys.stderr)
        exit_code = 7
    else:
        status = response["status"]
        if fail and status >= 400:
            if loud:
                print(f"curl: (22) The requested URL returned error: {status}", file=sys.stderr)
            return 22
        body = response["body"].encode("utf-8")
        if header_file is not None:
            lines = [f"HTTP/1.1 {status}"] + [f"{k}: {v}" for k, v in response.get("headers", {}).items()]
            Path(header_file).write_bytes("".join(line + "\r\n" for line in [*lines, ""]).encode("utf-8"))
    if output is not None:
        if exit_code == 0:
            Path(output).write_bytes(body)
    else:
        sys.stdout.buffer.write(body)
    if writeout is not None:
        sys.stdout.buffer.write(writeout.replace("\\n", "\n").replace("%{http_code}", f"{status:03d}").encode("utf-8"))
    return exit_code


def date_main(args: list[str]) -> int:
    """`date +FORMAT`, at the frozen time."""
    if len(args) != 1 or not args[0].startswith("+"):
        print(f"date shim: unsupported arguments {args!r}", file=sys.stderr)
        return 2
    print(frozen_now().strftime(args[0][1:]))
    return 0


def _no_network(*args: Any, **kwargs: Any) -> Any:
    raise OSError("network blocked by the oracle harness")


def _urlopen(url: Any, data: Any = None, timeout: Any = None, **kwargs: Any) -> Any:
    # The request is only matched against the saved responses, never opened.
    req = url if isinstance(url, urllib.request.Request) else urllib.request.Request(url, data)  # noqa: S310
    body = req.data.decode("utf-8") if isinstance(req.data, bytes) else None
    response = Responses.from_env().serve(req.get_method(), req.full_url, body)
    if response is None:
        raise urllib.error.URLError(f"no saved response for {req.get_method()} {req.full_url}")
    if "error" in response:
        raise urllib.error.URLError(response["error"])
    headers = Message()
    for name, value in response.get("headers", {}).items():
        headers[name] = value
    content = io.BytesIO(response["body"].encode("utf-8"))
    if response["status"] >= 400:
        raise urllib.error.HTTPError(req.full_url, response["status"], "error", headers, content)
    return urllib.response.addinfourl(content, headers, req.full_url, response["status"])


def install() -> None:
    """Freeze the clock, answer `urlopen` from the saved responses, and block every other connection."""
    freeze_clock(frozen_now())
    Responses.from_env()  # fail now, not at the first request, when the harness's settings are missing
    urllib.request.urlopen = _urlopen
    socket.socket.connect = _no_network  # type: ignore[method-assign]
    socket.socket.connect_ex = _no_network  # type: ignore[method-assign]
    socket.getaddrinfo = _no_network


def heredoc_main(args: list[str]) -> None:
    now_iso, responses_path, heredoc = args[:3]
    freeze_clock(_real_datetime.fromisoformat(now_iso))

    replies: list[dict[str, Any]] = []
    if responses_path != "-":
        replies = json.loads(Path(responses_path).read_text(encoding="utf-8"))

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> io.BytesIO:
        reply = replies.pop(0)
        if "http_error" in reply:
            body = io.BytesIO(reply["body"].encode("utf-8"))
            raise urllib.error.HTTPError(req.full_url, reply["http_error"], "error", Message(), body)
        if "url_error" in reply:
            raise urllib.error.URLError(reply["url_error"])
        return io.BytesIO(reply["body"].encode("utf-8"))

    urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
    os.environ["CLOUDFLARE_API_TOKEN"] = os.environ["CLOUDFLARE_ACCOUNT_ID"] = "placeholder"  # noqa: S105

    code = compile(Path(heredoc).read_text(encoding="utf-8"), heredoc, "exec")
    sys.argv = ["-", *args[3:]]
    exec(code, {"__name__": "__main__"})  # noqa: S102 (running the script's heredoc is this shim's job)


def main() -> None:
    command, args = sys.argv[1], sys.argv[2:]
    if command == "heredoc":
        heredoc_main(args)
    elif command == "curl":
        sys.exit(curl_main(args))
    elif command == "date":
        sys.exit(date_main(args))
    else:
        sys.exit(f"shim: unknown command {command!r}")


if __name__ == "__main__":
    main()
