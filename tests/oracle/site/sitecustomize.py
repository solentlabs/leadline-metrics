"""Imported by every Python the harnessed script starts (script_harness.py puts it on PYTHONPATH).

It freezes the clock and replaces the network with the saved responses
(shim.install). Python reports and then ignores an exception raised here,
which would leave the real network open, so any failure ends the process.
"""

from __future__ import annotations

import os
import traceback

try:
    import shim

    shim.install()
except BaseException:  # noqa: BLE001 (any failure must end the process, see above)
    traceback.print_exc()
    os._exit(70)
