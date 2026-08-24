from __future__ import annotations

from pathlib import Path

from automation_harness.backends.reference import ReferenceBackend
from automation_harness.runner.bundle import TestBundle
from automation_harness.runner.validator import validate_bundle


def _bundle(tmp_path: Path, source: str) -> TestBundle:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_registered.py").write_text(source, encoding="utf-8")
    (tmp_path / "manifest.yaml").write_text(
        "name: registry-validation\nversion: 1\nrequires: []\ntests:\n  - tests/test_registered.py\n",
        encoding="utf-8",
    )
    return TestBundle.load(tmp_path)


def test_validator_rejects_unknown_literal_registered_step(tmp_path: Path):
    bundle = _bundle(
        tmp_path,
        "def test_registered(ctx):\n    ctx.run_step('track.folow', 'alpha')\n",
    )
    issues = validate_bundle(bundle, backend_capabilities=ReferenceBackend(gui=False).capabilities)
    rendered = "\n".join(str(issue) for issue in issues)
    assert "unknown registered step 'track.folow'" in rendered
    assert "track.follow" in rendered


def test_validator_checks_registered_step_capabilities(tmp_path: Path):
    bundle = _bundle(
        tmp_path,
        "def test_registered(ctx):\n    ctx.run_step('navigation.component.activate', 'reference.threat.medium')\n",
    )
    issues = validate_bundle(bundle, backend_capabilities=ReferenceBackend(gui=False).capabilities)
    rendered = "\n".join(str(issue) for issue in issues)
    assert "requires backend capabilities: components" in rendered


def test_validator_checks_nested_registered_step_references_in_associated_library(tmp_path: Path):
    tests = tmp_path / "tests"
    steps = tmp_path / "steps"
    tests.mkdir()
    steps.mkdir()
    (tests / "test_registered.py").write_text(
        "def test_registered(ctx):\n    ctx.run_step('workflow.example')\n",
        encoding="utf-8",
    )
    (steps / "common.py").write_text(
        "from automation_harness.core.step_registry import step\n"
        "@step('workflow.example')\n"
        "def workflow(ctx):\n"
        "    ctx.run_step('threat.lvel.set', 'HIGH')\n",
        encoding="utf-8",
    )
    (tmp_path / "manifest.yaml").write_text(
        "name: library-validation\n"
        "version: 1\n"
        "step_libraries:\n  - steps/common.py\n"
        "requires: []\n"
        "tests:\n  - tests/test_registered.py\n",
        encoding="utf-8",
    )
    bundle = TestBundle.load(tmp_path)
    issues = validate_bundle(bundle, backend_capabilities=ReferenceBackend(gui=False).capabilities)
    rendered = "\n".join(str(issue) for issue in issues)
    assert "unknown registered step 'threat.lvel.set'" in rendered
    assert "threat.level.set" in rendered


def test_validator_rejects_associated_library_collision_with_builtin_step(tmp_path: Path):
    tests = tmp_path / "tests"
    steps = tmp_path / "steps"
    tests.mkdir()
    steps.mkdir()
    (tests / "test_registered.py").write_text("def test_registered(ctx):\n    pass\n", encoding="utf-8")
    (steps / "common.py").write_text(
        "from automation_harness.core.step_registry import step\n"
        "@step('track.follow')\n"
        "def duplicate(ctx, track_id):\n"
        "    return track_id\n",
        encoding="utf-8",
    )
    (tmp_path / "manifest.yaml").write_text(
        "name: library-collision\n"
        "version: 1\n"
        "step_libraries:\n  - steps/common.py\n"
        "requires: []\n"
        "tests:\n  - tests/test_registered.py\n",
        encoding="utf-8",
    )
    bundle = TestBundle.load(tmp_path)
    issues = validate_bundle(bundle, backend_capabilities=ReferenceBackend(gui=False).capabilities)
    rendered = "\n".join(str(issue) for issue in issues)
    assert "collides with built-in step 'track.follow'" in rendered


def test_validator_rejects_unknown_literal_output_binding(tmp_path: Path):
    bundle = _bundle(
        tmp_path,
        "def test_registered(ctx):\n"
        "    ctx.run_step('track.create_moving', 'alpha', bind_outputs={'trak_id': 'current_track'})\n",
    )
    issues = validate_bundle(bundle, backend_capabilities=ReferenceBackend(gui=False).capabilities)
    rendered = "\n".join(str(issue) for issue in issues)
    assert "does not declare output 'trak_id'" in rendered
    assert "track_id" in rendered


def test_validator_accepts_declared_library_output_binding(tmp_path: Path):
    tests = tmp_path / "tests"
    steps = tmp_path / "steps"
    tests.mkdir()
    steps.mkdir()
    (tests / "test_registered.py").write_text(
        "def test_registered(ctx):\n"
        "    ctx.run_step('workflow.output', bind_outputs={'value': 'saved'})\n",
        encoding="utf-8",
    )
    (steps / "common.py").write_text(
        "from automation_harness.core.step_registry import step\n"
        "@step('workflow.output', outputs={'value': '$'})\n"
        "def workflow(ctx):\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )
    (tmp_path / "manifest.yaml").write_text(
        "name: library-output-validation\n"
        "version: 1\n"
        "step_libraries:\n  - steps/common.py\n"
        "requires: []\n"
        "tests:\n  - tests/test_registered.py\n",
        encoding="utf-8",
    )
    bundle = TestBundle.load(tmp_path)
    assert validate_bundle(bundle, backend_capabilities=ReferenceBackend(gui=False).capabilities) == []
