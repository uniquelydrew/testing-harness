from automation_harness.backends.gtk_demo import GtkDemoBackend
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


def test_bundle_rejects_legacy_target_configuration(tmp_path):
    (tmp_path / "test_case.py").write_text("def test_case(): pass\n", encoding="utf-8")
    (tmp_path / "manifest.yaml").write_text(
        "name: legacy\nversion: 1\ntarget: {kind: attached-desktop}\ntests: [test_case.py]\n",
        encoding="utf-8",
    )
    try:
        TestBundle.load(tmp_path)
    except BundleError as exc:
        assert "manifest.target is obsolete" in str(exc)
    else:
        raise AssertionError("legacy bundle target configuration must be rejected")


def test_gtk_demo_bundle_backend_requires_example(tmp_path):
    (tmp_path / "test_case.py").write_text("def test_case(): pass\n", encoding="utf-8")
    (tmp_path / "manifest.yaml").write_text(
        "name: gtk\nversion: 1\nbackend: {kind: gtk-demo}\ntests: [test_case.py]\n",
        encoding="utf-8",
    )
    try:
        TestBundle.load(tmp_path)
    except BundleError as exc:
        assert "backend.example" in str(exc)
    else:
        raise AssertionError("GTK Demo backend without an example must be rejected")


def test_bundle_rejects_application_launch_backend(tmp_path):
    (tmp_path / "test_case.py").write_text("def test_case(): pass\n", encoding="utf-8")
    (tmp_path / "manifest.yaml").write_text(
        "name: java\nversion: 1\nbackend: {kind: java-desktop}\ntests: [test_case.py]\n",
        encoding="utf-8",
    )
    try:
        TestBundle.load(tmp_path)
    except BundleError as exc:
        assert "application launch/setup belongs in plan steps" in str(exc)
    else:
        raise AssertionError("application-launch backend must be rejected")


def test_jab_locator_matches_name_role_and_window():
    info = JavaAccessBridgeDriver._ContextInfo()
    info.name = "Follow"
    info.role_en_US = "push button"
    assert JavaAccessBridgeDriver._matches(info, "Tracking", {"name": "Follow", "role": "push button", "window": "Tracking"})
    assert not JavaAccessBridgeDriver._matches(info, "Tracking", {"name": "Stop"})
