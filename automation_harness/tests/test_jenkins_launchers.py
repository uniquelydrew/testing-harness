from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_jenkins_dispatcher_exposes_supported_lanes_and_artifact_roots():
    source = (ROOT / "scripts" / "ci" / "jenkins.sh").read_text(encoding="utf-8")

    for lane in ("unit)", "agents)", "desktop)"):
        assert lane in source
    assert "CI_ARTIFACTS_DIR" in source
    assert "desktop-preflight.sh" in source


def test_unit_launcher_publishes_junit_at_the_configured_artifact_root():
    source = (ROOT / "scripts" / "ci" / "unit.sh").read_text(encoding="utf-8")

    assert 'ARTIFACTS="${CI_ARTIFACTS_DIR:-$ROOT/artifacts/unit}"' in source
    assert '--junitxml="$ARTIFACTS/pytest.xml"' in source


def test_jenkinsfile_keeps_desktop_execution_opt_in_and_archives_results():
    source = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")

    assert "RUN_DESKTOP_QUALIFICATION" in source
    assert "beforeAgent true" in source
    assert "scripts/ci/jenkins.sh unit" in source
    assert "scripts/ci/jenkins.sh agents" in source
    assert "scripts/ci/jenkins.sh desktop" in source
    assert "artifacts/jenkins/unit/pytest.xml" in source
