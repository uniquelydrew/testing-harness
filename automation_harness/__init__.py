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

# Locator matching remains a package-level compatibility contract until every
# driver consumes the shared matcher directly. Pointer behavior is intrinsic to
# ComponentDefinition/ComponentHandle and requires no import-time mutation.
from automation_harness.core.locator_matching import install as _install_locator_matching

_install_locator_matching()
del _install_locator_matching
