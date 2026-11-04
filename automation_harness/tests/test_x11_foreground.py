"""X11/EWMH parsing and native identity must match real xprop output."""
from __future__ import annotations

import pytest

from automation_harness.drivers import x11_foreground as x11


def test_xprop_equals_parses_javafx_stage_name_and_process_id(monkeypatch):
    title = "SampleApp build.0 Main Video Display, SITE.op1, domain 12"
    monkeypatch.setattr(x11, "_xprop", lambda *args: (
        "_NET_WM_PID(CARDINAL) = 5215\n"
        '_NET_WM_NAME(UTF8_STRING) = "' + title + '"\n'
        'WM_NAME(STRING) = "Old legacy title"\n'
    ))
    assert x11._window_properties(0x987) == (5215, title)


def test_xprop_colon_fixture_remains_compatible(monkeypatch):
    monkeypatch.setattr(x11, "_xprop", lambda *args: (
        "_NET_WM_PID(CARDINAL): 1002\n"
        'WM_NAME(STRING): "Dialog"\n'
    ))
    assert x11._window_properties(0x987) == (1002, "Dialog")


def test_native_lookup_succeeds_with_standard_equals_properties(monkeypatch):
    title = "SampleApp build.0 Main Video Display, SITE.op1, domain 12"
    def xprop(*args):
        if args[0] == "-root":
            return "_NET_CLIENT_LIST(WINDOW): window id # 0x101, 0x202\n"
        if args[1] == "0x101":
            return ('_NET_WM_PID(CARDINAL) = 5215\n'
                    '_NET_WM_NAME(UTF8_STRING) = "' + title + '"\n')
        return ('_NET_WM_PID(CARDINAL) = 4444\n'
                '_NET_WM_NAME(UTF8_STRING) = "Other Stage"\n')

    monkeypatch.setattr(x11, "_xprop", xprop)
    assert x11.find_window(pid=5215, title=title) == 0x101
    assert len(x11.list_windows()) == 2


def test_unlisted_title_is_distinct_from_wrong_foreground(monkeypatch):
    monkeypatch.setattr(x11, "_xprop", lambda *args: (
        "_NET_CLIENT_LIST(WINDOW): window id # 0x101\n" if args[0] == "-root"
        else '_NET_WM_PID(CARDINAL) = 5215\n'
             '_NET_WM_NAME(UTF8_STRING) = "Different display title"\n'
    ))
    with pytest.raises(x11.X11WindowNotFound, match="No X11 window matched"):
        x11.find_window(pid=5215, title="SampleApp Main Video Display")


def test_foreign_process_holding_requested_title_fails_closed(monkeypatch):
    monkeypatch.setattr(x11, "_xprop", lambda *args: (
        "_NET_CLIENT_LIST(WINDOW): window id # 0x101\n" if args[0] == "-root"
        else '_NET_WM_PID(CARDINAL) = 9999\n'
             '_NET_WM_NAME(UTF8_STRING) = "Select Workspace"\n'
    ))
    with pytest.raises(x11.X11WindowIdentityConflict, match="another PID"):
        x11.find_window(pid=5215, title="Select Workspace")


def test_duplicate_native_identity_fails_closed(monkeypatch):
    def xprop(*args):
        if args[0] == "-root":
            return "_NET_CLIENT_LIST(WINDOW): window id # 0x101, 0x102\n"
        return ('_NET_WM_PID(CARDINAL) = 5215\n'
                '_NET_WM_NAME(UTF8_STRING) = "Select Workspace"\n')
    monkeypatch.setattr(x11, "_xprop", xprop)
    with pytest.raises(x11.X11WindowAmbiguous, match="Multiple X11 windows"):
        x11.find_window(pid=5215, title="Select Workspace")


def test_x11_activation_timeout_identifies_foreground_terminal(monkeypatch):
    monkeypatch.setattr(x11, "find_window", lambda **kwargs: 0x101)
    monkeypatch.setattr(x11, "active_window", lambda: 0x777)
    monkeypatch.setattr(x11, "_request_activation", lambda xid: None)
    monkeypatch.setattr(x11, "_window_properties", lambda xid: (1234, "Terminal"))
    with pytest.raises(x11.X11ActivationError, match="active_title='Terminal'"):
        x11.activate_window(pid=5215, title="SampleApp Main", timeout=0)


def test_xprop_uses_python36_compatible_subprocess_options(monkeypatch):
    observed = []

    def run(argv, **kwargs):
        observed.append((argv, kwargs))
        return type("Process", (), {"stdout": "WM_NAME(STRING) = \"Terminal\"\n"})()

    monkeypatch.setattr(x11.subprocess, "run", run)
    assert "WM_NAME" in x11._xprop("-root", "WM_NAME")
    argv, kwargs = observed[0]
    assert argv == ["xprop", "-root", "WM_NAME"]
    assert kwargs["stdout"] is x11.subprocess.PIPE
    assert kwargs["stderr"] is x11.subprocess.PIPE
    assert kwargs["universal_newlines"] is True
    assert "capture_output" not in kwargs
    assert "text" not in kwargs
