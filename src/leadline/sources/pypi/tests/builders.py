"""pypistats.org and PyPI builders."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Mapping

from scenarios.builders.common import Json


def pypistats_overall(package: str, days: Mapping[date, int | None], *, mirrors: int = 3) -> Json:
    """`packages/NAME/overall`: each day's downloads with and without mirrors, by category then day.

    A day whose count is None has no `without_mirrors` row, as pypistats gives a day with no downloads;
    it keeps its `with_mirrors` row, the mirrors' downloads.
    """
    with_mirrors = [
        {"category": "with_mirrors", "date": day.isoformat(), "downloads": (n or 0) + mirrors}
        for day, n in days.items()
    ]
    without = [
        {"category": "without_mirrors", "date": day.isoformat(), "downloads": n}
        for day, n in days.items()
        if n is not None
    ]
    return {"data": with_mirrors + without, "package": package, "type": "overall_downloads"}


def pypi_file(package: str, version: str, uploaded: str, *, wheel: bool = True) -> Json:
    """A release's file; `uploaded` is its upload time, as `2026-07-31T20:30:14`."""
    stem = package.replace("-", "_")
    filename = f"{stem}-{version}-py3-none-any.whl" if wheel else f"{stem}-{version}.tar.gz"
    digest = hashlib.sha256(filename.encode()).hexdigest()
    return {
        "comment_text": None,
        "core-metadata": {"sha256": digest} if wheel else False,
        "digests": {"blake2b_256": digest, "md5": digest[:32], "sha256": digest},
        "downloads": -1,
        "filename": filename,
        "has_sig": False,
        "md5_digest": digest[:32],
        "packagetype": "bdist_wheel" if wheel else "sdist",
        "python_version": "py3" if wheel else "source",
        "requires_python": ">=3.8",
        "size": 20000,
        "upload_time": uploaded,
        "upload_time_iso_8601": f"{uploaded}.000000Z",
        "url": f"https://files.pythonhosted.org/packages/{digest[:2]}/{digest[2:4]}/{digest[4:]}/{filename}",
        "yanked": False,
        "yanked_reason": None,
    }


def pypi_project(package: str, releases: Mapping[str, str], *, requires_python: str | None = ">=3.8") -> Json:
    """`pypi/NAME/json`: the project, and each release's files; `releases` maps version to upload time.

    `requires_python` is None for a project that declares none, as PyPI gives it.
    """
    files = {v: [pypi_file(package, v, when), pypi_file(package, v, when, wheel=False)] for v, when in releases.items()}
    latest = list(releases)[-1]
    return {
        "info": {
            "author": "Example Maintainer",
            "author_email": None,
            "bugtrack_url": None,
            "classifiers": ["Programming Language :: Python :: 3"],
            "description": "An example package.",
            "description_content_type": "text/markdown",
            "docs_url": None,
            "download_url": None,
            "downloads": {"last_day": -1, "last_month": -1, "last_week": -1},
            "dynamic": ["license-file"],
            "home_page": None,
            "keywords": "example",
            "license": None,
            "license_expression": "MIT",
            "license_files": ["LICENSE"],
            "maintainer": None,
            "maintainer_email": None,
            "name": package,
            "package_url": f"https://pypi.org/project/{package}/",
            "platform": None,
            "project_url": f"https://pypi.org/project/{package}/",
            "project_urls": {"Homepage": f"https://github.com/solentlabs/{package}"},
            "provides_extra": ["dev"],
            "release_url": f"https://pypi.org/project/{package}/{latest}/",
            "requires_dist": ["pytest; extra == 'dev'"],
            "requires_python": requires_python,
            "summary": "An example package.",
            "version": latest,
            "yanked": False,
            "yanked_reason": None,
        },
        "last_serial": 30000000,
        "ownership": {"organization": None, "roles": [{"role": "Owner", "user": "example-maintainer"}]},
        "releases": files,
        "urls": files[latest],
        "vulnerabilities": [],
    }
