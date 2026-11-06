"""Automation harness.

The package defaults to a synthetic, local-only reference backend. Protected-system
execution is intentionally opt-in and implemented behind a separate backend.
"""

__version__ = "0.5.2"

# Locator matching remains a package-level compatibility contract until every
# driver consumes the shared matcher directly. Pointer behavior is intrinsic to
# ComponentDefinition/ComponentHandle and requires no import-time mutation.
from automation_harness.core.locator_matching import install as _install_locator_matching

_install_locator_matching()
del _install_locator_matching
