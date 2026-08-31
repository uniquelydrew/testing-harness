"""Automation harness.

The package defaults to a synthetic, local-only reference backend. Protected-system
execution is intentionally opt-in and implemented behind a separate backend.
"""

import sys

__version__ = "0.5.2"

if sys.version_info < (3, 7):
    # Python 3.6 exposes ast.Constant but ast.parse still produces the legacy
    # Str/Num/Bytes/NameConstant nodes. The framework's static validators were
    # written against the unified Constant API. Normalize those legacy nodes
    # before installing the source-compatibility loader so validation semantics
    # remain identical on the fixed RHEL 8.6 runtime.
    import ast

    if not hasattr(ast.Str, "value"):
        ast.Str.value = property(lambda node: node.s)
    if not hasattr(ast.Num, "value"):
        ast.Num.value = property(lambda node: node.n)
    if not hasattr(ast.Bytes, "value"):
        ast.Bytes.value = property(lambda node: node.s)
    ast.Constant = (ast.Str, ast.Num, ast.Bytes, ast.NameConstant)

    from automation_harness.compat.python36 import install

    install()

# Locator matching and pointer behavior are package-level semantic contracts.
# Install them before repositories, drivers, or component handles are used so
# authoring and execution share the same behavior.
from automation_harness.core.locator_matching import install as _install_locator_matching
from automation_harness.core.pointer_actions import install as _install_pointer_actions

_install_locator_matching()
_install_pointer_actions()
del _install_locator_matching
del _install_pointer_actions
