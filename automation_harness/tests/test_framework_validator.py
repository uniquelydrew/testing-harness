from pathlib import Path

from automation_harness.runner.bundle import TestBundle as Bundle
from automation_harness.runner.validator import validate_bundle


def test_validator_reports_unknown_literal_component_reference(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tmp_path / "manifest.yaml").write_text(
        """name: typo-bundle\nversion: 1\nrequires: []\ntests:\n  - tests/test_typo.py\n""",
        encoding="utf-8",
    )
    (tests / "test_typo.py").write_text(
        """from automation_harness.steps.navigation_steps import activate_component\n\ndef test_typo(ctx):\n    activate_component(ctx, 'reference.threat.medum')\n""",
        encoding="utf-8",
    )

    bundle = Bundle.load(tmp_path)
    issues = validate_bundle(bundle, backend_capabilities=set())

    assert any("unknown component reference" in issue.message for issue in issues)
    assert any("reference.threat.medium" in issue.message for issue in issues)
