from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_java8_agent_build_uses_release_when_supported():
    source = (ROOT / "java-agent" / "build.sh").read_text(encoding="utf-8")

    assert "JAVAC_LEVEL=(--release 8)" in source
    assert "javac 1.8" in source
    assert 'javac "${JAVAC_LEVEL[@]}"' in source


def test_bootstrap_fails_cleanly_when_xvfb_range_is_exhausted():
    source = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")

    assert 'local number="" candidate' in source
    assert '[[ -n "$number" ]] || die "no free Xvfb display is available in :200-:249"' in source


def test_bootstrap_uses_scoped_target_launcher_not_global_java_tool_options():
    source = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")

    assert "unset JAVA_TOOL_OPTIONS" in source
    assert "automation-java-target --agent swing -- <target-command>" in source
    assert "export JAVA_TOOL_OPTIONS=" not in source


def test_bootstrap_smoke_tests_the_installed_authoring_entry_point():
    source = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")

    assert '"$VENV_DIR/bin/automation-author" --smoke-test' in source
    assert '"$VENV_DIR/bin/automation-capture"' not in source


def test_plan_authoring_window_uses_current_repository_assignment_api():
    source = (
        ROOT / "automation_harness" / "authoring" / "gui" / "plan_authoring_window.py"
    ).read_text(encoding="utf-8")

    assert "assign_repositories" in source
    assert "load_repository_set" in source
    assert "RepositoryAssociation" in source
    assert "RepositoryScope.LOCAL" in source
    assert "assign_repository," not in source


def test_repository_window_wrapper_resolves_project_context_with_imported_path():
    source = (
        ROOT / "automation_harness" / "authoring" / "repository_direct_authoring_runtime.py"
    ).read_text(encoding="utf-8")

    assert "from pathlib import Path" in source
    assert "self.host.project_context = Path(project_context).resolve()" in source
