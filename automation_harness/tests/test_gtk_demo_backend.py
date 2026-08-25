from automation_harness.backends.gtk_demo import GtkDemoBackend
from automation_harness.backends.java_desktop import JavaDesktopBackend
from automation_harness.drivers.java_accessibility import JavaAccessBridgeDriver
from automation_harness.runner.bundle import BundleError, TestBundle


def test_gtk_demo_backend_requires_an_example():
    try:
        GtkDemoBackend(example="", display_mode="virtual")
    except ValueError as exc:
        assert "example" in str(exc).lower()
    else:
        raise AssertionError("missing example must be rejected")


def test_gtk_demo_backend_rejects_unknown_display_mode():
    try:
        GtkDemoBackend(example="buttons", display_mode="remote")
    except ValueError as exc:
        assert "display" in str(exc).lower()
    else:
        raise AssertionError("unknown display mode must be rejected")


def test_gtk_demo_target_requires_example(tmp_path):
    (tmp_path / "test_case.py").write_text("def test_case(): pass\n", encoding="utf-8")
    (tmp_path / "manifest.yaml").write_text(
        "name: gtk\nversion: 1\ntarget: {kind: gtk-demo}\ntests: [test_case.py]\n",
        encoding="utf-8",
    )
    try:
        TestBundle.load(tmp_path)
    except BundleError as exc:
        assert "target.example" in str(exc)
    else:
        raise AssertionError("GTK Demo target without an example must be rejected")


def test_java_desktop_target_requires_command(tmp_path):
    (tmp_path / "test_case.py").write_text("def test_case(): pass\n", encoding="utf-8")
    (tmp_path / "manifest.yaml").write_text(
        "name: java\nversion: 1\ntarget: {kind: java-desktop}\ntests: [test_case.py]\n",
        encoding="utf-8",
    )
    try:
        TestBundle.load(tmp_path)
    except BundleError as exc:
        assert "target.command" in str(exc)
    else:
        raise AssertionError("Java desktop target without command must be rejected")


def test_java_desktop_target_accepts_launch_contract(tmp_path):
    (tmp_path / "test_case.py").write_text("def test_case(): pass\n", encoding="utf-8")
    (tmp_path / "manifest.yaml").write_text(
        """name: java
version: 1
target:
  kind: java-desktop
  command: [java, -jar, demo.jar]
  expected_application: Demo
  startup_timeout: 15
  environment: {DEMO_MODE: test}
tests: [test_case.py]
""",
        encoding="utf-8",
    )
    bundle = TestBundle.load(tmp_path)
    assert bundle.target and bundle.target["command"] == ["java", "-jar", "demo.jar"]


def test_java_desktop_backend_rejects_unknown_display_mode():
    try:
        JavaDesktopBackend({"command": ["java", "-version"]}, display_mode="remote")
    except ValueError as exc:
        assert "display" in str(exc).lower()
    else:
        raise AssertionError("unknown display mode must be rejected")


def test_jab_locator_matches_name_role_and_window():
    info = JavaAccessBridgeDriver._ContextInfo()
    info.name = "Follow"
    info.role_en_US = "push button"
    assert JavaAccessBridgeDriver._matches(info, "Tracking", {"name": "Follow", "role": "push button", "window": "Tracking"})
    assert not JavaAccessBridgeDriver._matches(info, "Tracking", {"name": "Stop"})
