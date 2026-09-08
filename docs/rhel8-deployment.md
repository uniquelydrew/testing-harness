# RHEL 8 Deployment

Automation Harness supports deployment qualification specifically on Red Hat
Enterprise Linux 8 as a Linux/X11 target. The current bootstrap rejects other
distributions and non-RHEL-8 major versions before making changes.

## Bootstrap installation

For an extracted source/archive deployment, the normal installation path is now:

```bash
cd testing-harness
bash bootstrap.sh
```

`bootstrap.sh` is intentionally idempotent. It may be rerun to repair or re-qualify an installation.

The bootstrap performs the following work automatically:

1. Detects RHEL 8 using `/etc/os-release`.
2. Verifies the configured system interpreter is Python 3.6.x.
3. Installs available GTK 3, AT-SPI, X11/Xvfb, D-Bus, Java ATK wrapper, and Python binding RPMs with `dnf`.
4. Creates or repairs `.venv` with system site packages enabled.
5. Pins the last supported Python-3.6 packaging toolchain and compatibility dependencies.
6. Requires Pillow-backed framebuffer capture. It first uses the RHEL `python3-pillow` package when available, then falls back to the pinned Python-3.6-compatible Pillow release in the application virtual environment.
7. Installs Automation Harness from the source tree in development mode.
8. Builds the native JavaFX bridge agent when `javac` and `jar` are available.
9. Locates the Java ATK wrapper without assuming one exact RPM layout.
10. Qualifies CLI import, required Python bindings, Pillow screen capture, GTK authoring, AT-SPI, the JavaFX agent, and the available display mode.
11. Writes `.automation-harness-env` with the resulting runtime paths.

After a successful bootstrap:

```bash
source .automation-harness-env
automation-run selftest
```

The generated environment file also exports `AUTOMATION_HARNESS_JAVA_ATK_WRAPPER` when the wrapper is discovered.

## RHEL privilege and repository requirements

Native RPM installation requires root privileges. The bootstrap uses the current process when already running as root; otherwise it uses `sudo`.

The RHEL host must have access to whatever organizational or Red Hat repositories are required to retrieve the RPMs. The bootstrap cannot bypass repository, subscription, proxy, or network policy. If `python3-pillow` is unavailable from those repositories, the virtual environment must be able to obtain the configured Pillow release through pip.

## Python isolation

Do not replace RHEL's `/usr/bin/python3`. This backport deliberately runs on
the RHEL 8 Python 3.6 ABI so distribution PyGObject, Cairo, and pyatspi bindings
remain compatible. `AUTOMATION_HARNESS_PYTHON` may point to another interpreter,
but bootstrap requires that interpreter to be Python 3.6.x.

The application virtual environment defaults to:

```text
./.venv
```

and can be relocated with:

```bash
AUTOMATION_HARNESS_VENV=/opt/automation-harness/venv bash bootstrap.sh
```

The Pillow pip fallback defaults to `Pillow==8.4.0`, the final Pillow release that supports Python 3.6. An explicit deployment mirror or validated replacement version can be selected with `AUTOMATION_HARNESS_PILLOW_VERSION`.

## AT-SPI and PyGObject

The virtual environment is created with `--system-site-packages` so the RHEL
Python 3.6 interpreter can use the distribution's ABI-compatible PyGObject,
Cairo, and pyatspi installations. The resulting environment must pass:

```bash
automation-run selftest --require-atspi
```

A failure at that gate means the machine is not fully qualified for accessibility-backed GUI automation.

## Required screenshot capability

Pillow is now a required bootstrap capability rather than an optional vision extension. A successful bootstrap guarantees that `PIL.Image`, `PIL.ImageChops`, and `PIL.ImageGrab` import and that `ImageGrab.grab()` can capture a real framebuffer.

When `DISPLAY` is already set, bootstrap probes that display directly. On a headless shell, bootstrap starts a temporary Xvfb display and performs the same capture probe there. If neither an active display nor Xvfb is available, or if Pillow cannot capture the selected framebuffer, bootstrap exits nonzero.

This gate exists because component-bounds screenshots are runtime assertion evidence; an import-only Pillow check is insufficient.

## Java Swing and JavaFX

Linux Java accessibility requires a Java ATK wrapper JAR. The harness discovers it by capability rather than RPM/DEB package identity.

The standard locations checked include:

```text
/usr/share/java/java-atk-wrapper.jar
/usr/share/java/java-atk-wrapper/java-atk-wrapper.jar
```

and recursive discovery beneath:

```text
/usr/share/java
/usr/lib/java
/usr/lib64/java
```

For an organization-specific location:

```bash
export AUTOMATION_HARNESS_JAVA_ATK_WRAPPER=/absolute/path/to/java-atk-wrapper.jar
```

## D-Bus and virtual display

Accessibility-backed runs require a D-Bus session. Virtual-display mode additionally requires `Xvfb`.

The bootstrap installs and qualifies both `dbus-run-session` and `Xvfb`. A native display run still requires an active `DISPLAY` supplied by the desktop session. For bootstrap screenshot qualification, either that native `DISPLAY` or Xvfb must be available.

## Qualification failure policy

Bootstrap exits nonzero when the RHEL/Python version is unsupported, required
Python dependencies cannot install, GTK/AT-SPI/Pillow bindings are not importable
inside the virtual environment, or Pillow cannot capture a native or virtual framebuffer.
JDK tooling, the Java ATK wrapper, and an incomplete reference/AT-SPI self-test remain
separately reported capabilities because they can be added and requalified independently.

There is no `AUTOMATION_HARNESS_ALLOW_PARTIAL` switch. Read the qualification
output and verify every capability required by the intended test workload.

## Initial RHEL qualification target

The initial target remains:

- Red Hat Enterprise Linux 8.x, x86_64
- RHEL system Python 3.6.x in an application virtual environment
- X11/Xvfb
- D-Bus
- AT-SPI2
- PyGObject/PyCairo compatible with the application Python
- `pyatspi` importable by that same Python runtime
- Pillow with working `ImageGrab` framebuffer capture
- Java ATK wrapper for Swing/JavaFX accessibility

The bootstrap preserves the RHEL system runtime and never replaces
`/usr/bin/python3`.
