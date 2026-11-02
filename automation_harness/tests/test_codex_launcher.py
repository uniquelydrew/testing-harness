from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_source_launcher_has_one_canonical_runtime_dispatch_path():
    source = (ROOT / "scripts" / "codex-runtime").read_text(encoding="utf-8")

    assert "ENTRY=" not in source
    assert 'exec "$PYTHON" -m automation_harness.codex_runtime "$@"' in source
    assert "automation-codex-runtime" not in source


def test_packaged_and_source_codex_entrypoints_target_the_same_module():
    setup = (ROOT / "setup.py").read_text(encoding="utf-8")
    source = (ROOT / "scripts" / "codex-runtime").read_text(encoding="utf-8")

    assert "automation-codex-runtime=automation_harness.codex_runtime:main" in setup
    assert "automation_harness.codex_runtime" in source
