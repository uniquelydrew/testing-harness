"""Automation harness.

The package defaults to a synthetic, local-only reference backend. Protected-system
execution is intentionally opt-in and implemented behind a separate backend.
"""

import sys

__version__ = "0.5.2"

if sys.version_info < (3, 7):
    from automation_harness.compat.python36 import install

    install()
