"""X11/EWMH foreground activation for accessibility-backed GUI steps.

Uses the window manager's _NET_ACTIVE_WINDOW protocol; never clicks coordinates.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import re
import subprocess
import time
from typing import ClassVar


class X11ActivationError(RuntimeError):
    pass


class X11WindowNotFound(X11ActivationError):
    """No EWMH-managed window exposes this exact native identity."""


class X11WindowIdentityConflict(X11ActivationError):
    """The requested title is registered to a different native process."""


class X11WindowAmbiguous(X11ActivationError):
    """More than one EWMH-managed window matches the identity."""


def _xprop(*args: str) -> str:
    try:
        result = subprocess.run(
            ["xprop", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=2, check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise X11ActivationError("Unable to query X11 properties: %s" % exc) from exc
    return result.stdout


def _window_ids() -> list[int]:
    data = _xprop("-root", "_NET_CLIENT_LIST")
    return [int(value, 16) for value in re.findall(r"0x[0-9a-fA-F]+", data)]


def _window_properties(xid: int) -> tuple[int | None, str]:
    """Parse standard xprop ' = ' output and older ':' fixtures.

    Prefer the UTF-8 EWMH title over the legacy WM_NAME.
    """
    data = _xprop("-id", hex(xid), "_NET_WM_PID", "_NET_WM_NAME", "WM_NAME")
    pid_match = re.search(
        r"^\s*_NET_WM_PID\(CARDINAL\)\s*(?:=|:)\s*(\d+)\s*$",
        data, re.MULTILINE,
    )
    title = ""
    for atom in ("_NET_WM_NAME", "WM_NAME"):
        match = re.search(
            r'^\s*' + atom + r'\([^)]*\)\s*(?:=|:)\s*"([^"]*)"\s*$',
            data, re.MULTILINE,
        )
        if match and match.group(1):
            title = match.group(1)
            break
    return (int(pid_match.group(1)) if pid_match else None, title)


def find_window(*, pid: int, title: str) -> int:
    if not title:
        raise X11ActivationError("Cannot identify X11 window without owning-stage title")
    observed = [(xid, *_window_properties(xid)) for xid in _window_ids()]
    matches = [xid for xid, owner_pid, caption in observed
               if owner_pid == pid and caption == title]
    if len(matches) > 1:
        raise X11WindowAmbiguous(
            "Multiple X11 windows for pid=%s title=%r: %s" %
            (pid, title, [hex(xid) for xid in matches])
        )
    if matches:
        return matches[0]
    foreign_title = [(hex(xid), owner_pid) for xid, owner_pid, caption in observed
                     if caption == title and owner_pid not in (None, pid)]
    if foreign_title:
        raise X11WindowIdentityConflict(
            "X11 window title %r belongs to another PID; expected=%s observed=%s" %
            (title, pid, foreign_title)
        )
    same_process = [(hex(xid), caption) for xid, owner_pid, caption in observed
                    if owner_pid == pid]
    raise X11WindowNotFound(
        "No X11 window matched pid=%s title=%r; EWMH windows for PID=%s; "
        "matching-title windows with unknown PID=%s" %
        (pid, title, same_process,
         [hex(xid) for xid, owner_pid, caption in observed
          if caption == title and owner_pid is None])
    )


def list_windows() -> tuple[dict, ...]:
    """Read the current EWMH-managed window inventory without changing focus."""
    windows = []
    for xid in _window_ids():
        try:
            pid, title = _window_properties(xid)
        except X11ActivationError:
            continue
        windows.append({
            "native_window_id": hex(xid),
            "process_id": pid,
            "title": title,
        })
    return tuple(windows)


def request_window_activation(xid: int) -> None:
    """Request foreground for a known native window ID without blocking GTK."""
    if not isinstance(xid, int) or xid <= 0:
        raise X11ActivationError("native window ID must be a positive integer")
    _request_activation(xid)


def active_window() -> int | None:
    match = re.search(r"0x[0-9a-fA-F]+", _xprop("-root", "_NET_ACTIVE_WINDOW"))
    return int(match.group(), 16) if match else None


def _request_activation(xid: int) -> None:
    library = ctypes.util.find_library("X11")
    if not library:
        raise X11ActivationError("libX11 is unavailable")
    xlib = ctypes.CDLL(library)
    class XClientMessageData(ctypes.Union):
        _fields_: ClassVar[list[tuple[str, object]]] = [("b", ctypes.c_char * 20), ("s", ctypes.c_short * 10),
                    ("l", ctypes.c_long * 5)]
    class XClientMessageEvent(ctypes.Structure):
        _fields_: ClassVar[list[tuple[str, object]]] = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                    ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                    ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                    ("format", ctypes.c_int), ("data", XClientMessageData)]
    class XEvent(ctypes.Union):
        _fields_: ClassVar[list[tuple[str, object]]] = [("xclient", XClientMessageEvent), ("pad", ctypes.c_long * 24)]
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
            active_pid = None
            active_title = None
            if current is not None and current != xid:
                try:
                    active_pid, active_title = _window_properties(current)
                except X11ActivationError:
                    pass
            raise X11ActivationError(
                "X11 foreground activation timed out: target=%s pid=%s title=%r "
                "active=%s active_pid=%r active_title=%r"
                % (hex(xid), pid, title,
                   hex(current) if current is not None else None,
                   active_pid, active_title)
            )
        time.sleep(0.05)
