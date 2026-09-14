"""Desktop-global authoring hotkeys for X11-based Automation Harness sessions.

The authoring GUI deliberately yields focus to the application under test during
capture and recording. GTK accelerators therefore cannot implement the required
workflow: the key bindings must remain active while another process owns focus.

This module keeps X11-specific mechanics behind a small backend interface so the
GUI command routing is independent of the desktop implementation. The RHEL 8.9
backend uses passive XGrabKey registrations. A future Wayland implementation can
provide the same register/start/stop contract without changing authoring windows.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import threading
import time
from collections import OrderedDict


class HotkeyError(RuntimeError):
    pass


class HotkeyConflictError(HotkeyError):
    pass


class HotkeyUnavailableError(HotkeyError):
    pass


class HotkeyBinding:
    def __init__(self, action, accelerator):
        self.action = str(action)
        self.accelerator = str(accelerator)

    def __repr__(self):
        return "HotkeyBinding(%r, %r)" % (self.action, self.accelerator)


DEFAULT_HOTKEYS = OrderedDict((
    ("capture_pointer", "Ctrl+Alt+C"),
    ("capture_next_click", "Ctrl+Alt+N"),
    ("start_recording", "Ctrl+Alt+R"),
    ("stop_recording", "Ctrl+Alt+S"),
    ("transient_capture", "Ctrl+Alt+T"),
))

_ENV_KEYS = {
    "capture_pointer": "AUTOMATION_HARNESS_HOTKEY_CAPTURE_POINTER",
    "capture_next_click": "AUTOMATION_HARNESS_HOTKEY_CAPTURE_NEXT_CLICK",
    "start_recording": "AUTOMATION_HARNESS_HOTKEY_START_RECORDING",
    "stop_recording": "AUTOMATION_HARNESS_HOTKEY_STOP_RECORDING",
    "transient_capture": "AUTOMATION_HARNESS_HOTKEY_TRANSIENT_CAPTURE",
}

_MODIFIER_MASKS = {
    "shift": 1 << 0,
    "control": 1 << 2,
    "ctrl": 1 << 2,
    "alt": 1 << 3,
    "mod1": 1 << 3,
    "super": 1 << 6,
    "meta": 1 << 6,
    "mod4": 1 << 6,
}

_LOCK_MASK = 1 << 1
_NUM_LOCK_MASK = 1 << 4
_KEY_PRESS = 2
_GRAB_MODE_ASYNC = 1
_BAD_ACCESS = 10


class ParsedAccelerator:
    def __init__(self, key, modifiers):
        self.key = key
        self.modifiers = int(modifiers)


def parse_accelerator(value):
    parts = [item.strip() for item in str(value or "").split("+") if item.strip()]
    if not parts:
        raise ValueError("hotkey accelerator must not be empty")
    key = parts[-1]
    if not key:
        raise ValueError("hotkey accelerator requires a key")
    modifiers = 0
    for token in parts[:-1]:
        mask = _MODIFIER_MASKS.get(token.casefold())
        if mask is None:
            raise ValueError("unsupported hotkey modifier %r" % token)
        modifiers |= mask
    normalized_key = key if len(key) > 1 else key.casefold()
    return ParsedAccelerator(normalized_key, modifiers)


def bindings_from_environment(environ=None):
    environ = os.environ if environ is None else environ
    bindings = []
    for action, default in DEFAULT_HOTKEYS.items():
        raw = environ.get(_ENV_KEYS[action], default)
        if raw is None:
            continue
        raw = str(raw).strip()
        if not raw or raw.casefold() in {"off", "none", "disabled"}:
            continue
        parse_accelerator(raw)
        bindings.append(HotkeyBinding(action, raw))
    return tuple(bindings)


def global_hotkeys_enabled(environ=None):
    environ = os.environ if environ is None else environ
    raw = str(environ.get("AUTOMATION_HARNESS_GLOBAL_HOTKEYS", "1")).strip().casefold()
    return raw not in {"0", "false", "no", "off", "disabled"}


class _XKeyEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("root", ctypes.c_ulong),
        ("subwindow", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("x_root", ctypes.c_int),
        ("y_root", ctypes.c_int),
        ("state", ctypes.c_uint),
        ("keycode", ctypes.c_uint),
        ("same_screen", ctypes.c_int),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [
        ("type", ctypes.c_int),
        ("xkey", _XKeyEvent),
        ("pad", ctypes.c_long * 24),
    ]


class _XErrorEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("resourceid", ctypes.c_ulong),
        ("serial", ctypes.c_ulong),
        ("error_code", ctypes.c_ubyte),
        ("request_code", ctypes.c_ubyte),
        ("minor_code", ctypes.c_ubyte),
    ]


_X_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_XErrorEvent))


class X11GlobalHotkeyBackend:
    """Passive X11 key grabs with a dedicated event-consumer thread."""

    def __init__(self, display_name=None):
        library = ctypes.util.find_library("X11") or "libX11.so.6"
        try:
            self._xlib = ctypes.CDLL(library)
        except OSError as exc:
            raise HotkeyUnavailableError("X11 library is unavailable: %s" % exc)
        self._configure_xlib()
        encoded = display_name.encode("utf-8") if display_name else None
        self._display = self._xlib.XOpenDisplay(encoded)
        if not self._display:
            raise HotkeyUnavailableError("cannot open X11 display")
        self._root = self._xlib.XDefaultRootWindow(self._display)
        self._registered = {}
        self._grabbed = []
        self._callback = None
        self._thread = None
        self._stop = threading.Event()
        self._closed = False
        self._error_code = 0
        self._error_handler = _X_ERROR_HANDLER(self._on_x_error)

    def _configure_xlib(self):
        lib = self._xlib
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        lib.XCloseDisplay.restype = ctypes.c_int
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XStringToKeysym.argtypes = [ctypes.c_char_p]
        lib.XStringToKeysym.restype = ctypes.c_ulong
        lib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        lib.XKeysymToKeycode.restype = ctypes.c_uint
        lib.XGrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.XGrabKey.restype = ctypes.c_int
        lib.XUngrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong]
        lib.XUngrabKey.restype = ctypes.c_int
        lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.XSync.restype = ctypes.c_int
        lib.XPending.argtypes = [ctypes.c_void_p]
        lib.XPending.restype = ctypes.c_int
        lib.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XEvent)]
        lib.XNextEvent.restype = ctypes.c_int
        lib.XSetErrorHandler.argtypes = [ctypes.c_void_p]
        lib.XSetErrorHandler.restype = ctypes.c_void_p

    def _on_x_error(self, _display, event):
        try:
            self._error_code = int(event.contents.error_code)
        except Exception:
            self._error_code = -1
        return 0

    @staticmethod
    def _lock_variants(modifiers):
        return tuple(dict.fromkeys((
            modifiers,
            modifiers | _LOCK_MASK,
            modifiers | _NUM_LOCK_MASK,
            modifiers | _LOCK_MASK | _NUM_LOCK_MASK,
        )))

    @staticmethod
    def _normalized_state(state):
        return int(state) & ~(_LOCK_MASK | _NUM_LOCK_MASK)

    def register(self, action, accelerator):
        if self._thread is not None:
            raise HotkeyError("hotkeys cannot be registered after the X11 listener starts")
        parsed = parse_accelerator(accelerator)
        keysym = self._xlib.XStringToKeysym(parsed.key.encode("utf-8"))
        if not keysym:
            raise HotkeyError("unknown X11 keysym %r" % parsed.key)
        keycode = int(self._xlib.XKeysymToKeycode(self._display, keysym))
        if not keycode:
            raise HotkeyError("X11 has no keycode for %r" % parsed.key)
        identity = (keycode, parsed.modifiers)
        if identity in self._registered:
            raise HotkeyConflictError("hotkey %s conflicts with %s" % (accelerator, self._registered[identity]))

        grabbed_now = []
        old_handler = self._xlib.XSetErrorHandler(ctypes.cast(self._error_handler, ctypes.c_void_p))
        try:
            for modifiers in self._lock_variants(parsed.modifiers):
                self._error_code = 0
                self._xlib.XGrabKey(
                    self._display,
                    keycode,
                    modifiers,
                    self._root,
                    1,
                    _GRAB_MODE_ASYNC,
                    _GRAB_MODE_ASYNC,
                )
                self._xlib.XSync(self._display, 0)
                if self._error_code:
                    for grabbed_modifiers in grabbed_now:
                        self._xlib.XUngrabKey(self._display, keycode, grabbed_modifiers, self._root)
                    self._xlib.XSync(self._display, 0)
                    if self._error_code == _BAD_ACCESS:
                        raise HotkeyConflictError("X11 hotkey %s is already reserved" % accelerator)
                    raise HotkeyError("X11 rejected hotkey %s (error %s)" % (accelerator, self._error_code))
                grabbed_now.append(modifiers)
        finally:
            self._xlib.XSetErrorHandler(old_handler)
        self._registered[identity] = str(action)
        for modifiers in grabbed_now:
            self._grabbed.append((keycode, modifiers))

    def start(self, callback):
        if self._thread is not None:
            return
        self._callback = callback
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="automation-global-hotkeys", daemon=True)
        self._thread.start()

    def _run(self):
        event = _XEvent()
        while not self._stop.is_set():
            if self._xlib.XPending(self._display) <= 0:
                self._stop.wait(0.025)
                continue
            self._xlib.XNextEvent(self._display, ctypes.byref(event))
            if event.type != _KEY_PRESS:
                continue
            identity = (int(event.xkey.keycode), self._normalized_state(event.xkey.state))
            action = self._registered.get(identity)
            if action is not None and self._callback is not None:
                try:
                    self._callback(action)
                except Exception:
                    # Hotkey delivery must never terminate the X11 event loop.
                    pass

    def stop(self):
        if self._closed:
            return
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None
        for keycode, modifiers in tuple(self._grabbed):
            try:
                self._xlib.XUngrabKey(self._display, keycode, modifiers, self._root)
            except Exception:
                pass
        self._grabbed = []
        try:
            self._xlib.XSync(self._display, 0)
            self._xlib.XCloseDisplay(self._display)
        finally:
            self._closed = True


class GlobalHotkeyService:
    """Register authoring commands and marshal X11 callbacks onto the GTK loop."""

    def __init__(self, on_action, backend=None, bindings=None, scheduler=None):
        self.on_action = on_action
        self.backend = backend
        self.bindings = tuple(bindings if bindings is not None else bindings_from_environment())
        self.scheduler = scheduler
        self.errors = OrderedDict()
        self.registered = OrderedDict()
        self.started = False

    def _schedule(self, callback, *args):
        if self.scheduler is not None:
            return self.scheduler(callback, *args)
        from gi.repository import GLib
        return GLib.idle_add(callback, *args)

    def start(self):
        if self.started:
            return self.status()
        if self.backend is None:
            self.backend = X11GlobalHotkeyBackend()
        for binding in self.bindings:
            try:
                self.backend.register(binding.action, binding.accelerator)
            except Exception as exc:
                self.errors[binding.action] = "%s: %s" % (type(exc).__name__, exc)
            else:
                self.registered[binding.action] = binding.accelerator
        if self.registered:
            self.backend.start(self._received)
        self.started = True
        return self.status()

    def _received(self, action):
        self._schedule(self._deliver, str(action))

    def _deliver(self, action):
        self.on_action(action)
        return False

    def status(self):
        return {
            "registered": dict(self.registered),
            "errors": dict(self.errors),
        }

    def stop(self):
        backend = self.backend
        if backend is not None:
            try:
                backend.stop()
            except Exception:
                pass
        self.started = False


def create_global_hotkey_service(on_action, environ=None, backend=None, scheduler=None):
    if not global_hotkeys_enabled(environ):
        return None
    bindings = bindings_from_environment(environ)
    return GlobalHotkeyService(
        on_action,
        backend=backend,
        bindings=bindings,
        scheduler=scheduler,
    )
