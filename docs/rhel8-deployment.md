# RHEL 8 Deployment

Automation Harness supports deployment qualification on Red Hat Enterprise Linux 8 as a Linux/X11 target. Linux runtime checks are capability-based: the application itself does not depend on `apt`, `dpkg`, or distribution-specific package names.

## Bootstrap installation

For an extracted source/archive deployment, the normal installation path is now:

```bash
cd testing-harness
bash bootstrap.sh
```

`bootstrap.sh` is intentionally idempotent. It may be rerun to repair or re-qualify an installation.

The bootstrap performs the following work automatically:

1. Detects the Linux distribution using `/etc/os-release`.
2. Installs native GUI, accessibility, compiler, X11/Xvfb, and D-Bus prerequisites.
3. On RHEL-family systems, uses `dnf`; on Debian-family systems, uses `apt-get`.
4. Attempts to install a distribution-provided Python 3.11+ runtime.
5. If no suitable Python exists, builds an application-owned CPython 3.11 runtime under `.runtime/` without replacing `/usr/bin/python3`.
6. Creates or repairs the application virtual environment at `.venv/`.
7. Installs Automation Harness and its Python dependencies.
8. Installs a PyGObject/PyCairo line compatible with enterprise-Linux GLib versions.
9. Makes the distribution `pyatspi` Python sources available to the application runtime when they are present and compatible with the separately installed PyGObject runtime.
10. Locates the Java ATK wrapper without assuming a package layout.
11. Runs the base self-test and the AT-SPI qualification gate.
12. Writes `.automation-harness-env` with the resulting runtime paths.

After a successful bootstrap:

```bash
source .automation-harness-env
automation-run selftest
```

The generated environment file also exports `AUTOMATION_HARNESS_JAVA_ATK_WRAPPER` when the wrapper is discovered.

## RHEL privilege and repository requirements

Native RPM installation requires root privileges. The bootstrap uses the current process when already running as root; otherwise it uses `sudo`.

On RHEL, some development packages may require CodeReady Builder. If the first native dependency installation fails and `subscription-manager` is available, the bootstrap attempts to enable the architecture-specific CodeReady Builder repository and retries the installation.

The RHEL host must have access to whatever organizational or Red Hat repositories are required to retrieve the RPMs. The bootstrap cannot bypass repository, subscription, proxy, or network policy.

## Python isolation

Do not replace RHEL's `/usr/bin/python3`. RHEL 8 system-management tooling is coupled to the distribution Python stack.

Automation Harness requires Python 3.11+. The bootstrap searches for an existing compatible interpreter in this order:

```text
.runtime Python
python3.13
python3.12
python3.11
python3
```

If none qualifies, the bootstrap downloads and builds the configured CPython source release under `.runtime/` and uses it only for Automation Harness.

The source fallback version can be overridden:

```bash
AUTOMATION_HARNESS_PYTHON_VERSION=3.11.9 bash bootstrap.sh
```

The application virtual environment defaults to:

```text
./.venv
```

and can be relocated with:

```bash
AUTOMATION_HARNESS_VENV=/opt/automation-harness/venv bash bootstrap.sh
```

## AT-SPI and PyGObject

RHEL 8 commonly installs `python3-pyatspi` for the system Python while Automation Harness executes under Python 3.11+.

The bootstrap does not add the complete Python 3.6 site-packages tree to `PYTHONPATH`. Native extension modules from the RHEL Python ABI must not be mixed into the application interpreter.

Instead, it installs PyGObject/PyCairo against the application runtime and, when available, copies only the distribution's pure-Python `pyatspi` package into the isolated environment. The resulting environment must pass:

```bash
automation-run selftest --require-atspi
```

A failure at that gate means the machine is not fully qualified for accessibility-backed GUI automation.

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

The bootstrap installs and qualifies both `dbus-run-session` and `Xvfb`. A native display run still requires an active `DISPLAY` supplied by the desktop session.

## Strict versus partial bootstrap

The default bootstrap is strict: if the core harness installs but the required GUI/accessibility integration cannot qualify, bootstrap exits nonzero instead of silently reporting the host ready.

For deliberately reduced environments, this behavior can be overridden:

```bash
AUTOMATION_HARNESS_ALLOW_PARTIAL=1 bash bootstrap.sh
```

That option is intended for development or reference-only operation. It should not be used to declare a real GUI automation deployment qualified.

## Initial RHEL qualification target

The initial target remains:

- Red Hat Enterprise Linux 8.x, x86_64
- Python 3.11+ in an application-owned environment
- X11/Xvfb
- D-Bus
- AT-SPI2
- PyGObject/PyCairo compatible with the application Python
- `pyatspi` importable by that same Python runtime
- Java ATK wrapper for Swing/JavaFX accessibility

The RHEL 8.6 environment that motivated this support has a stock Python 3.6 system runtime. The bootstrap architecture explicitly preserves that runtime rather than modifying it.
