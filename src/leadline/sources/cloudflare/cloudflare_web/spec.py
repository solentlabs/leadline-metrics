"""What cloudflare_web declares: its name, its file, its config's checks, and its summary. No requests."""

from __future__ import annotations

from typing import Any

from leadline import config

NAME = "cloudflare_web"
DEFAULT_FILE = config.default_file(NAME, "label")  # cloudflare-web-{label}.jsonl

KEYS = ("label", "site_tag")  # the keys an entry may have, besides `file`

# The week's top values of a dimension of the page loads, each read by a request of its own so that one the API
# refuses leaves the others: the row's field, and the dimension's name in the GraphQL schema.
LISTS = (("top_countries", "countryName"), ("top_devices", "deviceType"), ("top_paths", "requestPath"))
TOP = 10  # how many values a list keeps


def validate(cfg: Any) -> list[str]:
    return config.entry_problems(cfg, KEYS, _problems, file_name)


def _problems(entry: dict[str, Any]) -> list[str]:
    bad = [key for key in ("label", "site_tag") if not isinstance(entry.get(key), str) or not entry[key]]
    return [f"{key}: must be a non-empty string" for key in bad]


def files(cfg: Any) -> dict[str, str]:
    return config.entry_files(cfg, file_name)


# The fields the default summary shows of each entry's file.
SHOWN = (config.Shown("pageviews", "Page views", "daily"), config.Shown("visits", "Visits", "daily"))
LABEL = "Web analytics"  # what trends heads its sections with


def summary(cfg: Any) -> list[config.Section]:
    return [_section(entry) for entry in cfg]


def _section(entry: Any) -> config.Section:
    """The entry's section: `SHOWN`, and the week's top countries, devices and paths of the newest row."""
    file = file_name(entry)
    lists = [
        {
            "from": file,
            "field": field,
            "aggregate": "items",
            "sort": "count",
            "item": "{name} {count}",
            "join": " · ",
            "as_of": "",
            "text": f"- Top {name} (week): {{items}}",
        }
        for field, name in (("top_countries", "countries"), ("top_devices", "devices"), ("top_paths", "paths"))
    ]
    return config.Section(NAME, entry["label"], file, SHOWN, LABEL, lines=lists)


def file_name(entry: Any) -> str:
    return config.file_name(entry, DEFAULT_FILE, {"label": entry["label"]})
