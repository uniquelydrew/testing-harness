from __future__ import annotations

import ctypes
import platform
from dataclasses import dataclass
from typing import Any, Mapping

from automation_harness.drivers.atspi_driver import AtspiDriver
from automation_harness.models.component import CapturedComponent, ComponentState, ResolvedComponent


class JavaAccessibilityUnavailable(RuntimeError):
    pass


class _JabActionInfo(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar * 1024)]


class _JabActionsToDo(ctypes.Structure):
    _fields_ = [("actions_count", ctypes.c_int32), ("actions", _JabActionInfo * 256)]


@dataclass
class JavaAccessibilityDriver:
    """Platform-neutral accessibility driver for externally launched Java UIs."""

    context: Any = None

    @property
    def available(self) -> bool:
        if platform.system() == "Linux":
            return AtspiDriver(self.context).available
        if platform.system() == "Windows":
            try:
                JavaAccessBridgeDriver.require_available()
                return True
            except JavaAccessibilityUnavailable:
                return False
        return False

    def _linux(self) -> AtspiDriver:
        if platform.system() != "Linux":
            raise JavaAccessibilityUnavailable("Java AT-SPI driver is available only on Linux")
        return AtspiDriver(self.context)

    def _windows(self) -> "JavaAccessBridgeDriver":
        if platform.system() != "Windows":
            raise JavaAccessibilityUnavailable("Java Access Bridge driver is available only on Windows")
        return JavaAccessBridgeDriver()

    def resolve(self, component_id: str, **kwargs: Any):
        return self._linux().resolve(component_id, **kwargs) if platform.system() == "Linux" else self._windows().resolve(component_id, **kwargs)

    def inspect(self, **kwargs: Any):
        return self._linux().inspect(**kwargs) if platform.system() == "Linux" else self._windows().inspect(**kwargs)

    def state(self, **kwargs: Any):
        return self._linux().state(**kwargs) if platform.system() == "Linux" else self._windows().state(**kwargs)

    def activate(self, **kwargs: Any):
        return self._linux().activate(**kwargs) if platform.system() == "Linux" else self._windows().activate(**kwargs)

    def get_text(self, **kwargs: Any):
        return self._linux().get_text(**kwargs) if platform.system() == "Linux" else self._windows().get_text(**kwargs)

    def set_text(self, value: str, **kwargs: Any):
        return self._linux().set_text(value, **kwargs) if platform.system() == "Linux" else self._windows().set_text(value, **kwargs)

    def get_selection(self, **kwargs: Any):
        return self._linux().get_selection(**kwargs) if platform.system() == "Linux" else self._windows().get_selection(**kwargs)

    def select_child(self, child_index: int, **kwargs: Any):
        return self._linux().select_child(child_index, **kwargs) if platform.system() == "Linux" else self._windows().select_child(child_index, **kwargs)

    def select_menu_path(self, selectors: list[Mapping[str, Any]], **kwargs: Any):
        return self._linux().select_menu_path(selectors, **kwargs) if platform.system() == "Linux" else self._windows().select_menu_path(selectors, **kwargs)

    def get_value(self, **kwargs: Any):
        return self._linux().get_value(**kwargs) if platform.system() == "Linux" else self._windows().get_value(**kwargs)

    def set_value(self, value: float, **kwargs: Any):
        return self._linux().set_value(value, **kwargs) if platform.system() == "Linux" else self._windows().set_value(value, **kwargs)


class JavaAccessBridgeDriver:
    """Windows Java Access Bridge loader and Java-window discovery.

    The native bridge is intentionally loaded lazily: Linux installations do
    not import Windows DLLs, and Windows preflight can report a precise setup
    error before bridge use.
    """

    DLL_NAME = "WindowsAccessBridge.dll"

    class _ContextInfo(ctypes.Structure):
        _fields_ = [
            ("name", ctypes.c_wchar * 1024),
            ("description", ctypes.c_wchar * 1024),
            ("role", ctypes.c_wchar * 256),
            ("role_en_US", ctypes.c_wchar * 256),
            ("states", ctypes.c_wchar * 256),
            ("states_en_US", ctypes.c_wchar * 256),
            ("index_in_parent", ctypes.c_int32),
            ("children_count", ctypes.c_int32),
            ("x", ctypes.c_int32),
            ("y", ctypes.c_int32),
            ("width", ctypes.c_int32),
            ("height", ctypes.c_int32),
            ("accessible_component", ctypes.c_int32),
            ("accessible_action", ctypes.c_int32),
            ("accessible_selection", ctypes.c_int32),
            ("accessible_text", ctypes.c_int32),
            ("accessible_value", ctypes.c_int32),
        ]

    @classmethod
    def require_available(cls) -> None:
        if platform.system() != "Windows":
            raise JavaAccessibilityUnavailable("Windows Java Access Bridge requires Windows")
        try:
            bridge = ctypes.WinDLL(cls.DLL_NAME)
            bridge.Windows_run.restype = None
            bridge.Windows_run()
        except OSError as exc:
            raise JavaAccessibilityUnavailable(f"could not load {cls.DLL_NAME}: {exc}") from exc

    def __init__(self) -> None:
        self.require_available()
        self.bridge = ctypes.WinDLL(self.DLL_NAME)
        self.bridge.getAccessibleContextFromHWND.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_longlong)]
        self.bridge.getAccessibleContextFromHWND.restype = ctypes.c_int32
        self.bridge.getAccessibleContextInfo.argtypes = [ctypes.c_long, ctypes.c_longlong, ctypes.POINTER(self._ContextInfo)]
        self.bridge.getAccessibleContextInfo.restype = ctypes.c_int32
        self.bridge.getAccessibleChildFromContext.argtypes = [ctypes.c_long, ctypes.c_longlong, ctypes.c_int32]
        self.bridge.getAccessibleChildFromContext.restype = ctypes.c_longlong
        self.bridge.doAccessibleActions.argtypes = [ctypes.c_long, ctypes.c_longlong, ctypes.POINTER(_JabActionsToDo), ctypes.POINTER(ctypes.c_int32)]
        self.bridge.doAccessibleActions.restype = ctypes.c_int32
        self.bridge.releaseJavaObject.argtypes = [ctypes.c_long, ctypes.c_longlong]
        self.bridge.releaseJavaObject.restype = None

    def _roots(self):
        user32 = ctypes.windll.user32
        values: list[tuple[int, int, int, str]] = []
        CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def visit(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd) or not bool(self.bridge.isJavaWindow(hwnd)):
                return True
            title = ctypes.create_unicode_buffer(1024)
            user32.GetWindowTextW(hwnd, title, len(title))
            vm_id, context = ctypes.c_long(), ctypes.c_longlong()
            if self.bridge.getAccessibleContextFromHWND(hwnd, ctypes.byref(vm_id), ctypes.byref(context)):
                values.append((int(hwnd), int(vm_id.value), int(context.value), title.value))
            return True

        user32.EnumWindows(CALLBACK(visit), 0)
        return values

    def _nodes(self):
        for hwnd, vm_id, root, title in self._roots():
            stack = [root]
            while stack:
                context = stack.pop()
                info = self._ContextInfo()
                if not self.bridge.getAccessibleContextInfo(vm_id, context, ctypes.byref(info)):
                    continue
                yield hwnd, vm_id, context, title, info
                for index in range(max(0, int(info.children_count))):
                    child = int(self.bridge.getAccessibleChildFromContext(vm_id, context, index))
                    if child:
                        stack.append(child)

    @staticmethod
    def _matches(info: "JavaAccessBridgeDriver._ContextInfo", title: str, identity: Mapping[str, Any]) -> bool:
        for key, expected in identity.items():
            if key == "name" and info.name != expected:
                return False
            if key == "role" and info.role_en_US.casefold() != str(expected).casefold():
                return False
            if key in {"application", "window"} and title != expected:
                return False
            if key == "accessible_id":
                return False  # JAB exposes names/roles, not a portable accessibility ID field.
            if key in {"parent", "hierarchy"}:
                return False
        return True

    def _find(self, identification: Mapping[str, Any] | None):
        identity = dict((identification or {}).get("mandatory", identification or {}))
        candidates = [node for node in self._nodes() if self._matches(node[4], node[3], identity)]
        if not candidates:
            raise LookupError(f"Java Access Bridge component not found: {identity}")
        if len(candidates) > 1:
            raise LookupError(f"Java Access Bridge component is ambiguous: {identity} ({len(candidates)} matches)")
        return candidates[0], identity

    def _capture(self, node) -> CapturedComponent:
        _hwnd, _vm_id, _context, title, info = node
        states = {item.strip().casefold() for item in info.states_en_US.split(",") if item.strip()}
        return CapturedComponent(
            name=info.name or None,
            role=info.role_en_US or info.role or None,
            description=info.description or None,
            accessible_id=None,
            application=title or None,
            window=title or None,
            hierarchy=(title, info.name) if title and info.name else ((title,) if title else ()),
            actions=("activate",) if info.accessible_action else (),
            bounds=(int(info.x), int(info.y), int(info.width), int(info.height)),
            state=ComponentState(present=True, visible="visible" in states, showing="showing" in states, enabled="enabled" in states, focused="focused" in states),
            backend_properties={"states": info.states_en_US, "children_count": int(info.children_count)},
            framework="java_accessibility",
            native_class=None,
        )

    def resolve(self, component_id: str, *, identification: Mapping[str, Any] | None = None, **_kwargs: Any) -> ResolvedComponent:
        node, identity = self._find(identification)
        captured = self._capture(node)
        metadata = captured.to_dict()
        metadata["resolution"] = {"identification": identity, "bridge": "java-access-bridge"}
        return ResolvedComponent(component_id=component_id, strategy="java_accessibility", metadata=metadata)

    def inspect(self, *, identification: Mapping[str, Any] | None = None, **_kwargs: Any) -> CapturedComponent:
        return self._capture(self._find(identification)[0])

    def state(self, *, identification: Mapping[str, Any] | None = None, **_kwargs: Any) -> ComponentState:
        return self.inspect(identification=identification).state

    def activate(self, *, identification: Mapping[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
        (node, identity) = self._find(identification)
        _hwnd, vm_id, context, _title, info = node
        if not info.accessible_action:
            raise RuntimeError("Java Access Bridge component exposes no actions")
        actions = _JabActionsToDo()
        actions.actions_count = 1
        actions.actions[0].name = "click"
        failure = ctypes.c_int32(-1)
        if not self.bridge.doAccessibleActions(vm_id, context, ctypes.byref(actions), ctypes.byref(failure)):
            raise RuntimeError(f"Java Access Bridge click action failed at index {failure.value}")
        return {"action": "click", "identification": identity}

    def _unsupported(self, operation: str):
        raise JavaAccessibilityUnavailable(f"Windows Java Access Bridge {operation} is not implemented by this adapter")

    def get_text(self, **kwargs: Any): return self._unsupported("text retrieval")
    def set_text(self, value: str, **kwargs: Any): return self._unsupported("text entry")
    def get_selection(self, **kwargs: Any): return self._unsupported("selection retrieval")
    def select_child(self, child_index: int, **kwargs: Any): return self._unsupported("selection")
    def select_menu_path(self, selectors: list[Mapping[str, Any]], **kwargs: Any): return self._unsupported("menu path selection")
    def get_value(self, **kwargs: Any): return self._unsupported("value retrieval")
    def set_value(self, value: float, **kwargs: Any): return self._unsupported("value setting")
