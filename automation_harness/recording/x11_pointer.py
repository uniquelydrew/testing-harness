"""Physical pointer transitions for X11 recording.

AT-SPI device events are not a reliable press clock: on some RHEL desktops a
button press is dispatched only after release.  This monitor owns a dedicated
Xlib display and polls the root pointer mask so recording can begin resolving a
transient object while the physical button is still held.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import threading
import time


_BUTTON_MASKS = {
    1: 1 << 8,  # Button1Mask
    3: 1 << 10,  # Button3Mask
}


class _XlibPointerBackend:
    def __init__(self):
        library = ctypes.util.find_library("X11") or "libX11.so.6"
        self._xlib = ctypes.CDLL(library)
        self._xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self._xlib.XOpenDisplay.restype = ctypes.c_void_p
        self._xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self._xlib.XDefaultRootWindow.restype = ctypes.c_ulong
        self._xlib.XQueryPointer.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint),
        ]
        self._xlib.XQueryPointer.restype = ctypes.c_int
        self._xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self._xlib.XCloseDisplay.restype = ctypes.c_int
        self._display = None
        self._root = None

    def open(self):
        self._display = self._xlib.XOpenDisplay(None)
        if not self._display:
            raise RuntimeError("cannot open the X11 display")
        self._root = self._xlib.XDefaultRootWindow(self._display)

    def sample(self):
        root_return = ctypes.c_ulong()
        child_return = ctypes.c_ulong()
        root_x = ctypes.c_int()
        root_y = ctypes.c_int()
        window_x = ctypes.c_int()
        window_y = ctypes.c_int()
        mask = ctypes.c_uint()
        success = self._xlib.XQueryPointer(
            self._display, self._root,
            ctypes.byref(root_return), ctypes.byref(child_return),
            ctypes.byref(root_x), ctypes.byref(root_y),
            ctypes.byref(window_x), ctypes.byref(window_y), ctypes.byref(mask),
        )
        if not success:
            raise RuntimeError("XQueryPointer failed")
        return root_x.value, root_y.value, mask.value

    def close(self):
        if self._display:
            self._xlib.XCloseDisplay(self._display)
            self._display = None


class X11PointerMonitor:
    """Emit primary/secondary press and release transitions as they occur."""

    def __init__(self, backend_factory=None, poll_interval=0.01):
        self._backend_factory = backend_factory or _XlibPointerBackend
        self._poll_interval = max(0.002, float(poll_interval))
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = None
        self._callback = None
        self._startup_error = None

    def start(self, callback):
        if self._thread is not None:
            raise RuntimeError("X11 pointer monitor is already active")
        self._callback = callback
        self._stop.clear()
        self._ready.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._run, name="x11-pointer-monitor", daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(1.0):
            self.stop()
            raise RuntimeError("X11 pointer monitor did not start")
        if self._startup_error is not None:
            error = self._startup_error
            self.stop()
            raise error

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(1.0)
        self._thread = None
        self._callback = None

    def _run(self):
        backend = None
        previous_mask = 0
        try:
            backend = self._backend_factory()
            backend.open()
            _x, _y, previous_mask = backend.sample()
            self._ready.set()
            while not self._stop.wait(self._poll_interval):
                x, y, mask = backend.sample()
                changed = previous_mask ^ mask
                for button, button_mask in _BUTTON_MASKS.items():
                    if not changed & button_mask:
                        continue
                    suffix = "p" if mask & button_mask else "r"
                    callback = self._callback
                    if callback is not None:
                        callback(
                            "mouse:button:{}{}".format(button, suffix),
                            (x, y), time.monotonic(),
                        )
                previous_mask = mask
        except Exception as exc:
            if not self._ready.is_set():
                self._startup_error = exc
                self._ready.set()
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception:
                    pass
            self._ready.set()
