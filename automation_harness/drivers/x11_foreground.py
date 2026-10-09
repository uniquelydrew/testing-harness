"""X11/EWMH foreground activation for accessibility-backed GUI steps.

Uses the window manager's _NET_ACTIVE_WINDOW protocol; never clicks coordinates.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import re
import subprocess
import time


class X11ActivationError(RuntimeError):
    pass


def _xprop(*args: str) -> str:
    try:
        result = subprocess.run(
            ["xprop", *args], capture_output=True, text=True, timeout=2, check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise X11ActivationError("Unable to query X11 properties: %s" % exc) from exc
    return result.stdout


def _window_ids() -> list[int]:
    data = _xprop("-root", "_NET_CLIENT_LIST")
    return [int(value, 16) for value in re.findall(r"0x[0-9a-fA-F]+", data)]


def _window_properties(xid: int) -> tuple[int | None, str]:
    data = _xprop("-id", hex(xid), "_NET_WM_PID", "_NET_WM_NAME", "WM_NAME")
    pid_match = re.search(r"_NET_WM_PID\(CARDINAL\):\s*(\d+)", data)
    title_match = re.search(r'(?:_NET_WM_NAME\([^)]*\)|WM_NAME\([^)]*\)):\s*"([^"]*)"', data)
    return (int(pid_match.group(1)) if pid_match else None,
            title_match.group(1) if title_match else "")


def find_window(*, pid: int, title: str) -> int:
    if not title:
        raise X11ActivationError("Cannot identify X11 window without owning-stage title")
    matches = [
        xid for xid in _window_ids()
        if _window_properties(xid) == (pid, title)
    ]
    if len(matches) != 1:
        raise X11ActivationError(
            "Expected one X11 window for pid=%s title=%r; found %s"
            % (pid, title, [hex(x) for x in matches])
        )
    return matches[0]


def active_window() -> int | None:
    match = re.search(r"0x[0-9a-fA-F]+", _xprop("-root", "_NET_ACTIVE_WINDOW"))
    return int(match.group(), 16) if match else None


def _request_activation(xid: int) -> None:
    library = ctypes.util.find_library("X11")
    if not library:
        raise X11ActivationError("libX11 is unavailable")
    xlib = ctypes.CDLL(library)
    class XClientMessageData(ctypes.Union):
        _fields_ = [("b", ctypes.c_char * 20), ("s", ctypes.c_short * 10),
                    ("l", ctypes.c_long * 5)]
    class XClientMessageEvent(ctypes.Structure):
        _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                    ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                    ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                    ("format", ctypes.c_int), ("data", XClientMessageData)]
    class XEvent(ctypes.Union):
        _fields_ = [("xclient", XClientMessageEvent), ("pad", ctypes.c_long * 24)]
    xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    xlib.XOpenDisplay.restype = ctypes.c_void_p
    xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    xlib.XDefaultRootWindow.restype = ctypes.c_ulong
    xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    xlib.XInternAtom.restype = ctypes.c_ulong
    xlib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                ctypes.c_long, ctypes.POINTER(XEvent)]
    xlib.XSendEvent.restype = ctypes.c_int
    xlib.XFlush.argtypes = [ctypes.c_void_p]
    xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = xlib.XOpenDisplay(None)
    if not display:
        raise X11ActivationError("Cannot connect to X11 display")
    try:
        root = xlib.XDefaultRootWindow(display)
        atom = xlib.XInternAtom(display, b"_NET_ACTIVE_WINDOW", 0)
        event = XEvent()
        event.xclient.type = 33  # ClientMessage
        event.xclient.window = xid
        event.xclient.message_type = atom
        event.xclient.format = 32
        event.xclient.data.l[0] = 1  # application request
        sent = xlib.XSendEvent(display, root, 0, (1 << 20) | (1 << 19),
                               ctypes.byref(event))
        xlib.XFlush(display)
        if not sent:
            raise X11ActivationError("XSendEvent rejected activation request")
    finally:
        xlib.XCloseDisplay(display)


def activate_window(*, pid: int, title: str, timeout: float = 2.0) -> dict:
    xid = find_window(pid=pid, title=title)
    before = active_window()
    if before != xid:
        _request_activation(xid)
    deadline = time.monotonic() + timeout
    while True:
        current = active_window()
        if current == xid:
            return {"native_window_id": hex(xid), "previous_active_window": (
                hex(before) if before is not None else None),
                "active_window": hex(current), "native_foreground_verified": True}
        if time.monotonic() >= deadline:
            raise X11ActivationError(
                "X11 foreground activation timed out: target=%s pid=%s title=%r active=%s"
                % (hex(xid), pid, title, hex(current) if current is not None else None)
            )
        time.sleep(0.05)
