"""Regression tests for private RHEL pyatspi source compatibility."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[2]
COMPAT_SCRIPT = ROOT / "scripts" / "patch_pyatspi_legacy.py"


def _compat_module():
    spec = importlib.util.spec_from_file_location("patch_pyatspi_legacy", COMPAT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


LEGACY_REGISTRY = '''
class Registry:
    def configure(self):
        self.async = False  # not fully supported yet

    def start(self, async=False, gil=True):
        return gil
'''


def _fixture_package(root: Path, registry_source: str) -> Path:
    package = root / "pyatspi"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("from .registry import Registry\n", encoding="utf-8")
    (package / "registry.py").write_text(registry_source, encoding="utf-8")
    return package


def test_legacy_registry_reserved_words_are_adapted_without_renaming_public_attribute():
    patched, changed = _compat_module().adapt_registry(LEGACY_REGISTRY)
    assert changed
    assert 'setattr(self, "async", False)' in patched
    assert "def start(self, asynchronous=False, gil=True, **kwargs)" in patched
    namespace = {}
    exec(compile(patched, "registry.py", "exec"), namespace)
    registry = namespace["Registry"]()
    registry.configure()
    assert getattr(registry, "async") is False
    assert registry.start(**{"async": True}) is True
    assert registry.start(False, False) is False
    with pytest.raises(TypeError):
        registry.start(unsupported=True)


def test_modern_registry_is_left_unchanged():
    modern = "class Registry:\n    def start(self, asynchronous=False, gil=True):\n        return gil\n"
    patched, changed = _compat_module().adapt_registry(modern)
    assert patched == modern
    assert not changed


def test_partial_or_unknown_legacy_syntax_fails_closed():
    with pytest.raises(ValueError, match="Unsupported legacy"):
        _compat_module().adapt_registry("class Registry:\n    self.async = False\n")
    with pytest.raises(SyntaxError):
        _compat_module().adapt_registry("def start(self, async=False):\n    pass\n")


def test_private_copy_is_patched_and_idempotent(tmp_path):
    source = _fixture_package(tmp_path / "system", LEGACY_REGISTRY)
    target = tmp_path / "venv" / "site-packages" / "pyatspi"
    compat = _compat_module()
    assert compat.install_compatible_copy(source, target)
    assert "self.async =" in (source / "registry.py").read_text(encoding="utf-8")
    assert "self.async =" not in (target / "registry.py").read_text(encoding="utf-8")
    # A rerun re-copies the original RPM and reapplies the same bounded patch.
    assert compat.install_compatible_copy(source, target)
    assert "self.async =" not in (target / "registry.py").read_text(encoding="utf-8")


def test_rejects_copying_over_system_sources(tmp_path):
    source = _fixture_package(tmp_path / "system", LEGACY_REGISTRY)
    with pytest.raises(ValueError, match="distribution-owned"):
        _compat_module().install_compatible_copy(source, source)


def test_syntax_failure_keeps_previous_private_copy(tmp_path):
    source = _fixture_package(tmp_path / "system", LEGACY_REGISTRY)
    target = tmp_path / "venv" / "site-packages" / "pyatspi"
    compat = _compat_module()
    assert compat.install_compatible_copy(source, target)
    previous = (target / "registry.py").read_text(encoding="utf-8")
    (source / "registry.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        compat.install_compatible_copy(source, target)
    assert (target / "registry.py").read_text(encoding="utf-8") == previous
