# RHEL 8 Deployment

Automation Harness supports deployment qualification on Red Hat Enterprise Linux 8 as a Linux/X11 target. Linux runtime checks are capability-based: the harness does not require `apt`, `dpkg`, or any other distribution-specific package manager at runtime.

## Baseline

The initial RHEL qualification target is:

- Red Hat Enterprise Linux 8.x, x86_64
- Python 3.11 or newer in an application-owned environment
- X11, with Xvfb available for virtual-display runs
- AT-SPI2 and a D-Bus session for accessibility-backed object interaction
- a `pyatspi` binding importable by the same Python runtime that executes Automation Harness
- Java plus the Java ATK wrapper for Swing/JavaFX targets

Do not replace RHEL's `/usr/bin/python3` to satisfy the harness Python requirement. Install or provision Python 3.11+ separately and create an application-owned virtual environment.

## Python runtime

Example layout:

```text
/opt/automation-harness/
├── venv/
├── repositories/
├── testplans/
└── runs/
```

Create the environment with the qualified Python interpreter:

```bash
/opt/python311/bin/python3.11 -m venv /opt/automation-harness/venv
source /opt/automation-harness/venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install ./automation_harness-0.5.2-py3-none-any.whl
```

If RHEL provides the required interpreter as `python3.11`, use that executable instead of `/opt/python311/bin/python3.11`.

## Native capability qualification

Before deploying a real GUI target, verify the host provides the required Linux capabilities:

```bash
command -v Xvfb
command -v dbus-run-session
command -v java

echo "DISPLAY=${DISPLAY:-<unset>}"
echo "DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-<unset>}"

find /usr/share/java /usr/lib/java /usr/lib64/java \
  -type f -name 'java-atk-wrapper*.jar' 2>/dev/null

python - <<'PY'
for module in ("pyatspi", "gi", "cairo"):
    try:
        imported = __import__(module)
        print(f"{module}: OK ({getattr(imported, '__file__', '<built-in>')})")
    except Exception as exc:
        print(f"{module}: FAIL ({type(exc).__name__}: {exc})")
PY
```

The important constraint is that `pyatspi` must be importable by the Python 3.11+ runtime actually running the harness. A binding installed only for RHEL's system Python does not satisfy this requirement.

## Java ATK wrapper discovery

The Linux Java backend no longer assumes a Debian/Ubuntu package name or calls `dpkg-query`. It discovers the Java ATK wrapper by capability.

The following locations are checked automatically:

```text
/usr/share/java/java-atk-wrapper.jar
/usr/share/java/java-atk-wrapper/java-atk-wrapper.jar
```

The backend also searches beneath:

```text
/usr/share/java
/usr/lib/java
/usr/lib64/java
```

If the RHEL installation places the wrapper elsewhere, set:

```bash
export AUTOMATION_HARNESS_JAVA_ATK_WRAPPER=/absolute/path/to/java-atk-wrapper.jar
```

This keeps deployment independent of RPM package naming and allows RHEL installations, internally packaged environments, and future Linux distributions to expose the same runtime capability through different package layouts.

## D-Bus and virtual displays

A Java accessibility run requires a D-Bus session. Virtual-display mode additionally requires `Xvfb` on `PATH`.

Where the host does not already provide a desktop D-Bus session, launch qualification or execution under:

```bash
dbus-run-session -- automation-run selftest --require-atspi
```

For a managed Java target, `display_mode: virtual` uses an isolated Xvfb display. `display_mode: native` requires an existing `DISPLAY`; `display_mode: auto` uses an existing display when present and otherwise creates a virtual display.

## Qualification

After installing the Python wheel and native accessibility dependencies:

```bash
automation-run selftest
automation-run selftest --require-atspi
```

The second command is the meaningful qualification gate for a host that will execute accessibility-backed GUI automation.

## RHEL 8.6 known deployment boundary

A stock RHEL 8.6 installation may expose AT-SPI/PyGObject bindings only to the distribution's Python 3.6 runtime. Automation Harness requires Python 3.11+. Do not add the Python 3.6 site-packages directories to a Python 3.11 environment: PyGObject and related modules include native ABI components.

The supported deployment must provide a Python 3.11-compatible `pyatspi`/PyGObject stack to the harness runtime. Qualify this explicitly with the import test above before treating the machine as ready for real AT-SPI execution.
