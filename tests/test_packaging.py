"""The runtime stays stdlib-only: the NAS that runs leadline has Python 3.8 and no pip."""

from __future__ import annotations

from importlib import metadata

import leadline


def test_runtime_has_no_dependencies() -> None:
    requires = metadata.requires("leadline-metrics") or []
    assert [req for req in requires if "extra ==" not in req] == []


def test_installed_version_matches_package() -> None:
    assert metadata.version("leadline-metrics") == leadline.__version__


def test_installs_on_python_3_8_with_the_leadline_command() -> None:
    dist = metadata.distribution("leadline-metrics")
    scripts = {ep.name: ep.value for ep in dist.entry_points if ep.group == "console_scripts"}

    assert dist.metadata["Requires-Python"] == ">=3.8"
    assert scripts == {"leadline": "leadline.cli:main"}


def test_the_license_is_an_expression_alone_as_pypi_requires() -> None:
    # PEP 639: PyPI rejects a distribution with License-Expression and a License classifier.
    meta = metadata.metadata("leadline-metrics")

    assert meta["License-Expression"] == "MIT"
    assert [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")] == []
