# Authoring global hotkeys

The artifact-aware authoring GUI registers X11-global hotkeys so capture and recording commands remain available while the application under test owns keyboard focus. The implementation is intentionally behind a backend boundary; the current deployment backend uses X11 passive key grabs and is appropriate for the RHEL 8.9/X11 environment.

## Default bindings

| Command | Default | Behavior |
| --- | --- | --- |
| Capture object at pointer | `Ctrl+Alt+C` | Immediately captures the semantic object currently under the pointer. |
| Capture next click | `Ctrl+Alt+N` | Arms the repository's native next-click capture workflow. |
| Start recording | `Ctrl+Alt+R` | Starts recording for the most recently focused Test Plan window. |
| Stop recording | `Ctrl+Alt+S` | Stops that recording even while the target application owns focus. |
| Transient capture | `Ctrl+Alt+T` | Immediately captures the object under the pointer after a simulated click has opened a menu, context menu, popup, or other transient state. |

Hotkeys act only on the **most recently focused Automation Harness window**. The dispatcher does not search other open windows for one that happens to support the command. This prevents a hotkey from unexpectedly operating on a different Project, Test Plan, or Object Repository.

The transient-capture command intentionally does not resurface or focus an Automation Harness window before inspecting the pointer. This preserves transient application state long enough for capture. After capture, the normal repository/workbench workflow resurfaces the authoring UI.

`Escape` is not registered as a desktop-global hotkey because doing so would steal a fundamental cancellation key from every application in the X11 session. Existing capture surfaces retain their local Escape cancellation behavior.

## Configuration

Each binding can be changed with an environment variable. Set a binding to `off`, `none`, or `disabled` to suppress only that command.

```bash
export AUTOMATION_HARNESS_HOTKEY_CAPTURE_POINTER='Ctrl+Alt+C'
export AUTOMATION_HARNESS_HOTKEY_CAPTURE_NEXT_CLICK='Ctrl+Alt+N'
export AUTOMATION_HARNESS_HOTKEY_START_RECORDING='Ctrl+Alt+R'
export AUTOMATION_HARNESS_HOTKEY_STOP_RECORDING='Ctrl+Alt+S'
export AUTOMATION_HARNESS_HOTKEY_TRANSIENT_CAPTURE='Ctrl+Alt+T'
```

Disable global hotkeys entirely with:

```bash
export AUTOMATION_HARNESS_GLOBAL_HOTKEYS=0
```

Registrations tolerate Caps Lock and the conventional X11 Num Lock modifier. If another desktop component has already reserved a binding, that binding is reported unavailable while non-conflicting Automation Harness bindings remain active.
