import os
from pathlib import Path
import subprocess


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


def test_bootstrap_installs_python312_native_bindings_into_its_venv():
    source = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")

    assert '"pycairo==$PYCAIRO_VERSION"' in source
    assert '"PyGObject==$PYGOBJECT_VERSION"' in source
    assert "install_pyatspi_from_system" in source
    assert "native_python_bindings_available" in source
    assert "native_binding_build_prerequisites_available" in source
    assert 'install --no-build-isolation "PyGObject==$PYGOBJECT_VERSION"' in source


def test_bootstrap_declares_all_rhel_python312_build_dependencies():
    source = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")

    assert "python3.12-devel" in source
    assert "cairo-gobject-devel" in source
    assert "python_headers_available" in source
    assert 'sysconfig.get_path("include")' in source
    assert "cairo cairo-gobject gobject-introspection-1.0 glib-2.0 libffi" in source
    assert 'if probe_pillow_capture "$display"; then' in source


def test_bootstrap_python_header_probe_uses_configured_interpreter(tmp_path):
    include_dir = tmp_path / "python-include"
    include_dir.mkdir()
    interpreter = tmp_path / "fake-python"
    interpreter.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$FAKE_PY_INCLUDE\"\n",
        encoding="utf-8",
    )
    interpreter.chmod(0o755)

    env = os.environ.copy()
    env["FAKE_PY_INCLUDE"] = str(include_dir)
    command = [
        "bash", "-c", 'source "$1"; python_headers_available "$2"',
        "bootstrap-test", str(ROOT / "bootstrap.sh"), str(interpreter),
    ]
    missing = subprocess.run(command, env=env, text=True, capture_output=True)
    assert missing.returncode == 1
    assert str(include_dir / "Python.h") in missing.stderr

    (include_dir / "Python.h").write_text("/* test stub */\n", encoding="utf-8")
    present = subprocess.run(command, env=env, text=True, capture_output=True)
    assert present.returncode == 0, present.stderr


def test_bootstrap_native_prerequisite_probe_identifies_missing_cairo_gobject(tmp_path):
    tools_dir = tmp_path / "bin"
    tools_dir.mkdir()
    fake_pc = tools_dir / "pkg-config"
    fake_pc.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--exists" ] && [ "$2" = "cairo-gobject" ]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_pc.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(tools_dir) + os.pathsep + env["PATH"]
    command = [
        "bash", "-c", 'source "$1"; native_binding_build_prerequisites_available',
        "bootstrap-test", str(ROOT / "bootstrap.sh"),
    ]
    missing = subprocess.run(command, env=env, text=True, capture_output=True)
    assert missing.returncode == 1
    assert "Missing native pkg-config modules: cairo-gobject" in missing.stderr

    fake_pc.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    present = subprocess.run(command, env=env, text=True, capture_output=True)
    assert present.returncode == 0, present.stderr


def test_bootstrap_installs_missing_required_rpm_without_a_dnf_lookup(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    rpm = fake_bin / "rpm"
    rpm.write_text(
        '#!/bin/sh\n[ "$2" = "$MISSING_RPM" ] && exit 1\nexit 0\n',
        encoding="utf-8",
    )
    rpm.chmod(0o755)
    dnf = fake_bin / "dnf"
    dnf.write_text("#!/bin/sh\nexit 124\n", encoding="utf-8")
    dnf.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    env["MISSING_RPM"] = "cairo-gobject-devel"
    script = (
        'source "$1"; '
        'python_headers_available() { return 0; }; '
        'dnf_install() { echo "INSTALL:$*"; }; '
        'install_available_rpms'
    )
    result = subprocess.run(
        ["bash", "-c", script, "bootstrap-test", str(ROOT / "bootstrap.sh")],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "INSTALL:cairo-gobject-devel" in result.stdout
    assert "Installing required native RPM(s): cairo-gobject-devel" in result.stderr


def test_bootstrap_skips_uncached_optional_rpm_without_blocking(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    rpm = fake_bin / "rpm"
    rpm.write_text(
        '#!/bin/sh\n[ "$2" = "$MISSING_RPM" ] && exit 1\nexit 0\n',
        encoding="utf-8",
    )
    rpm.chmod(0o755)
    dnf = fake_bin / "dnf"
    dnf.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    dnf.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    env["MISSING_RPM"] = "java-atk-wrapper"
    script = (
        'source "$1"; '
        'python_headers_available() { return 0; }; '
        'dnf_install() { echo "INSTALL:$*"; }; '
        'install_available_rpms'
    )
    result = subprocess.run(
        ["bash", "-c", script, "bootstrap-test", str(ROOT / "bootstrap.sh")],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "INSTALL:" not in result.stdout
    assert "Optional RPM not confirmed in local DNF cache: java-atk-wrapper" in result.stderr


def test_bootstrap_dnf_timeout_does_not_gate_required_rpm_installation():
    source = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")

    assert "AUTOMATION_HARNESS_DNF_TIMEOUT:-180" in source
    assert 'dnf_install "${missing_required[@]}"' in source
    assert 'dnf_cached_query "$package"' in source
    assert "dnf_query list --available" not in source


def test_desktop_preflight_requires_python312():
    source = (ROOT / "scripts" / "ci" / "desktop-preflight.sh").read_text(
        encoding="utf-8"
    )

    assert 'PYTHON="${PYTHON:-python3.12}"' in source
    assert "assert sys.version_info[:2] == (3, 12)" in source


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


def test_bootstrap_invokes_pyatspi_legacy_adaptation_before_import():
    source = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
    assert 'scripts/patch_pyatspi_legacy.py' in source
    assert 'Validating pyatspi import in Python 3.12' in source


def test_bootstrap_requires_xorg_x11_utils_and_validates_xprop():
    source = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")

    assert "dbus-x11 xorg-x11-xauth xorg-x11-utils gcc" in source
    assert "verify_x11_client_tools() {" in source
    assert "command -v xprop >/dev/null 2>&1" in source
    assert 'install the xorg-x11-utils RPM on RHEL 8' in source
    assert source.index("    install_available_rpms") < source.index("    verify_x11_client_tools") < source.index("    create_venv")


def test_bootstrap_xprop_preflight_fails_with_actionable_error():
    script = (
        'source "$1"; '
        'command() { if [[ "$1" == "-v" && "$2" == "xprop" ]]; then return 1; fi; '
        'builtin command "$@"; }; '
        'verify_x11_client_tools'
    )
    result = subprocess.run(
        ["bash", "-c", script, "bootstrap-test", str(ROOT / "bootstrap.sh")],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "install the xorg-x11-utils RPM on RHEL 8" in result.stderr


def test_bootstrap_never_uses_terminal_stdin_for_optional_install_and_gui_checks():
    source = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
    assert '"$py" -m pip --no-input' in source
    assert '"$py" -m pip --no-input install -e . </dev/null' in source
    assert 'bash "$ROOT_DIR/javafx_agent/build.sh" </dev/null' in source
    assert 'bash "$ROOT_DIR/java-agent/build.sh" </dev/null' in source
    assert '"$VENV_DIR/bin/automation-author" --smoke-test </dev/null' in source
    assert '"$run" selftest --reference-display "$display_mode" </dev/null' in source
    assert '"$run" selftest --require-atspi --reference-display "$display_mode" </dev/null' in source


def test_bootstrap_annotates_sudo_as_the_only_expected_interactive_stage():
    source = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
    assert 'Stage 2/9: Provision approved RPM dependencies (sudo may request authentication)' in source
    assert 'Stage 4/9: Install Python packages without interactive stdin' in source
    assert 'Stage 8/9: Qualify GUI and reference runtime without interactive stdin' in source
    assert 'sudo "$@"' in source

def test_bootstrap_dnf_probe_is_bounded_and_cannot_read_terminal_input():
    source = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
    assert 'DNF_PROBE_TIMEOUT="${AUTOMATION_HARNESS_DNF_PROBE_TIMEOUT:-10}"' in source
    assert 'with_timeout "$DNF_PROBE_TIMEOUT" dnf --cacheonly -q list --available "$@" </dev/null' in source
    assert 'Checking optional RPM in cached DNF metadata (max ${DNF_PROBE_TIMEOUT}s): $package' in source
    assert 'sudo "$@"' in source  # Required RPM installation may still authenticate.


def test_bootstrap_optional_dnf_query_does_not_consume_inherited_stdin(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable = fake_bin / "dnf"
    record = tmp_path / "read-result"
    executable.write_text(
        "#!/bin/sh\n"
        'if IFS= read -r line; then\n'
        '    printf "READ:%s\\n" "$line" > "$DNF_READ_RESULT"\n'
        "else\n"
        '    printf "EOF\\n" > "$DNF_READ_RESULT"\n'
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    env["DNF_READ_RESULT"] = str(record)
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; dnf_cached_query test-optional-package || true',
         "bootstrap-test", str(ROOT / "bootstrap.sh")],
        input="MUST_NOT_BE_READ\n",
        text=True, capture_output=True, env=env, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert record.read_text(encoding="utf-8") == "EOF\n"


def test_bootstrap_optional_dnf_probe_timeout(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable = fake_bin / "dnf"
    executable.write_text("#!/bin/sh\nexec sleep 5\n", encoding="utf-8")
    executable.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    env["AUTOMATION_HARNESS_DNF_PROBE_TIMEOUT"] = "1"
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; dnf_cached_query test-optional-package',
         "bootstrap-test", str(ROOT / "bootstrap.sh")],
        capture_output=True, text=True, env=env, timeout=4,
    )
    assert result.returncode == 124, result.stderr
