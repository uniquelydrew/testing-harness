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
2. Verifies the configured interpreter is Python 3.12.x.
3. Installs required GTK 3, AT-SPI, X11 client tools (`xorg-x11-utils`, including `xprop`), D-Bus, and native build RPMs with `dnf`, plus available optional Xvfb and Java ATK wrapper RPMs.
4. Creates or repairs `.venv` with system site packages enabled.
5. Installs the current packaging toolchain, Pillow, and Python-3.12-compatible Pycairo/PyGObject bindings in the application virtual environment. It copies the distribution-provided pure-Python `pyatspi` sources into that environment after installing its `gi` dependency.
6. Requires Pillow-backed framebuffer capture. It uses an already importable Pillow installation when available; otherwise it installs the pinned Python 3.12-compatible release with pip in the application virtual environment. It does not attempt to install the RHEL `python3-pillow` RPM, which may target a different Python ABI.
7. Installs Automation Harness from the source tree in development mode.
8. Builds the native JavaFX bridge agent when `javac` and `jar` are available.
9. Locates the Java ATK wrapper without assuming one exact RPM layout.
10. Qualifies CLI import, required Python bindings, Pillow screen capture, GTK authoring, AT-SPI, the JavaFX agent, and the available display mode.
11. Writes `.automation-harness-env` in the source checkout with exports for the runtime tools and any discovered Java agents.

After a successful bootstrap:

```bash
source ./.automation-harness-env
automation-run selftest
```

The generated environment file also exports `AUTOMATION_HARNESS_JAVA_ATK_WRAPPER` when the wrapper is discovered. Set `AUTOMATION_HARNESS_VENV` to relocate the virtual environment, and use `AUTOMATION_HARNESS_RUNTIME_DIR` to relocate runtime artifacts and diagnostic evidence.

## RHEL privilege and repository requirements

Native RPM installation requires root privileges. The bootstrap uses the current process when already running as root; otherwise it uses `sudo`. Missing required native RPMs are installed in **one DNF transaction**, without individual repository-availability queries. Optional Xvfb and Java ATK wrapper RPMs are installed only when visible in locally cached DNF metadata; if necessary, install these separately on deployments where they are not cached. The transaction-wide timeout defaults to 180 seconds (`AUTOMATION_HARNESS_DNF_TIMEOUT`) and each DNF network operation uses a 30-second timeout (`AUTOMATION_HARNESS_DNF_NETWORK_TIMEOUT`). Optional cache-only DNF package checks do not inherit terminal stdin and are individually limited to 10 seconds (`AUTOMATION_HARNESS_DNF_PROBE_TIMEOUT`). Bootstrap logs the package name before each check, so a stalled Stage 2/9 can be isolated without relying on hidden DNF output. Installation still permits the normal interactive `sudo` authentication prompt when privileges are required.

The RHEL host must have access to whatever organizational or Red Hat repositories are required to retrieve the RPMs. The bootstrap cannot bypass repository, subscription, proxy, or network policy. Compiling Python 3.12 GTK bindings requires matching CPython development headers (`Python.h`, typically from `python3.12-devel` when using RHEL's packaged interpreter) and the native development RPMs `cairo-devel`, `cairo-gobject-devel`, `gobject-introspection-devel`, `glib2-devel`, and `libffi-devel`. The bootstrap checks for Python headers in the configured interpreter's include path and verifies `pkg-config` modules `cairo`, `cairo-gobject`, `gobject-introspection-1.0`, `glib-2.0`, and `libffi` before native compilation. It reports missing capabilities with actionable diagnostics.

If the packages are not visible, verify that your approved RHEL repositories are enabled; CodeReady Builder may be required in some environments. The script does not change subscription-manager repository configuration. If `python3-pillow` is unavailable from those repositories, the virtual environment must be able to obtain the configured Pillow release through pip.

## Python isolation

Do not replace RHEL's `/usr/bin/python3`. Configure
`AUTOMATION_HARNESS_PYTHON` to a Python 3.12 interpreter; bootstrap requires
that interpreter to be Python 3.12.x. The interpreter and any native
PyGObject, Cairo, and pyatspi bindings must use the same ABI.

By default bootstrap uses `/usr/bin/python3.12`. Set the override when Python
3.12 is installed elsewhere:

```bash
AUTOMATION_HARNESS_PYTHON=/opt/python3.12/bin/python3.12 bash bootstrap.sh
```

The application virtual environment defaults to:

```text
./.venv
```

and can be relocated with:

```bash
AUTOMATION_HARNESS_VENV=/opt/automation-harness/venv bash bootstrap.sh
```

The Pillow pip fallback defaults to `Pillow==10.4.0`, which supports Python
3.12. An explicit deployment mirror or validated replacement version can be
selected with `AUTOMATION_HARNESS_PILLOW_VERSION`.

## Jenkins integration

The repository includes a declarative `Jenkinsfile` and a stable dispatcher:

```bash
bash scripts/ci/jenkins.sh unit
bash scripts/ci/jenkins.sh agents
bash scripts/ci/jenkins.sh desktop
```

The pipeline expects Jenkins agents labeled `linux && python312` for unit
tests, `linux && jdk11` for agent builds, and `linux && rhel8 && desktop` for
desktop qualification. The desktop stage is disabled by default; enable the
`RUN_DESKTOP_QUALIFICATION` build parameter only on a qualified interactive
RHEL 8 runner. It never runs against an ordinary Linux worker.

Each lane writes CI artifacts beneath `artifacts/jenkins/<lane>` unless
`CI_ARTIFACTS_DIR` is already set. Jenkins publishes the unit and desktop JUnit
XML plus available diagnostic artifacts, and fingerprints the built Java agent
JARs.

## Optional declarative-plan pytest adapter

The normal RHEL bootstrap **does not install the optional pytest dependency**.
Install it only when `.ahplan` collection or external pytest/JUnit orchestration
is needed. From the source checkout, using the bootstrapped Python environment:

```bash
cd testing-harness
source ./.automation-harness-env
python -m pip install -e '.[pytest]'
automation-pytest --help
```

The optional dependency uses pytest 8-compatible releases on Python 3.12.
Use the `automation-pytest` command to load the plan-adapter plugin. Do **not**
assume that invoking bare `pytest` from another interpreter initializes it.

A basic collection-only invocation does not require launching the target
desktop or reference backend:

```bash
automation-pytest --collect-only -q /path/to/test-plans
```

Actual `--ah-backend=live-desktop` execution still requires a qualified
desktop session, a usable `DISPLAY`, a D-Bus session, and at least one
supported accessibility driver. An externally supplied CI/X11 session must
meet those requirements; the adapter does not provision an isolated desktop.
The plugin does not run later plan steps after a failed managed step. For
external cleanup, configure `--ah-teardown-command` with a bounded,
idempotent stop script. The command runs in a `finally` block even if
setup or execution fails; it cannot guarantee recovery from process
termination or host failure.

See [`pytest-integration.md`](pytest-integration.md) for options and current
qualification status.

The manually dispatched RHEL desktop workflow now includes
`scripts/ci/pytest-plan-desktop.sh`, which exercises a synthetic GUI
`.ahplan` through pytest and archives JUnit/harness artifacts. It requires a
registered RHEL desktop runner and has not yet been executed as part of this
branch's qualification.

## AT-SPI and PyGObject

The virtual environment is created with `--system-site-packages` for access to
the system GTK/AT-SPI typelibs. It builds the Python 3.12 Pycairo and
PyGObject bindings in the virtual environment instead of attempting to import
the distribution's bindings built for a different Python ABI. The bootstrap
copies the pure-Python `pyatspi` package from `python3-pyatspi` into the same
environment. RHEL 8's older `registry.py` uses the now-reserved `async` keyword; bootstrap adapts **only the private virtual-environment copy** using `scripts/patch_pyatspi_legacy.py`, compiles all copied Python modules, then validates the actual import. The installed system RPM remains unchanged. A syntactically compatible copy does not by itself establish working accessibility operations or event delivery. The resulting environment must pass:

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

## X11 window management dependencies

The foreground driver (`automation_harness/drivers/x11_foreground.py`) calls
the external `xprop` binary for EWMH window enumeration, process ownership,
titles, and active-window verification. RHEL 8 provides this binary in the
`xorg-x11-utils` package. Bootstrap installs that required RPM in its
bounded DNF transaction and fails early with an actionable diagnostic if
`xprop` is not on `PATH`. Confirm it with:

```bash
command -v xprop
xprop -root _NET_CLIENT_LIST
xprop -root _NET_ACTIVE_WINDOW
```

The commands that query window properties require a valid X11 `DISPLAY`,
a reachable X server, and appropriate authorization; installing the binary
alone does not establish foreground activation.

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
- Python 3.12.x in an application virtual environment
- X11/Xvfb
- D-Bus
- AT-SPI2
- PyGObject 3.44.2 and Pycairo 1.26.1 built for the application Python
- `pyatspi` copied from `python3-pyatspi` and importable by that same Python runtime
- Pillow with working `ImageGrab` framebuffer capture
- Java ATK wrapper for Swing/JavaFX accessibility

The bootstrap preserves the RHEL system runtime and never replaces
`/usr/bin/python3`.
