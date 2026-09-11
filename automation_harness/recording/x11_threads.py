"""Early Xlib thread initialization for GTK/X11 authoring processes.

XInitThreads must run before any other Xlib call in a process.  The authoring
entry points invoke this module before importing GTK; the X11 pointer backend
also calls it defensively for non-GUI consumers.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import threading


_lock = threading.Lock()
_initialized = False
_result = None


def initialize_x11_threads():
    global _initialized, _result
    with _lock:
        if _initialized:
            return _result
        library = ctypes.util.find_library("X11") or "libX11.so.6"
        xlib = ctypes.CDLL(library)
        xlib.XInitThreads.argtypes = []
        xlib.XInitThreads.restype = ctypes.c_int
        result = int(xlib.XInitThreads())
        if result == 0:
            raise RuntimeError("XInitThreads failed; X11 recording cannot safely share the process with GTK")
        _initialized = True
        _result = result
        return result
