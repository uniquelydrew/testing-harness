#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${AUTOMATION_HARNESS_VENV:-$ROOT_DIR/.venv}"
SYSTEM_PYTHON="${AUTOMATION_HARNESS_PYTHON:-/usr/bin/python3}"
DNF_TIMEOUT="${AUTOMATION_HARNESS_DNF_TIMEOUT:-20}"
PIP_TIMEOUT="${AUTOMATION_HARNESS_PIP_TIMEOUT:-30}"
JAVAFX_AGENT_JAR="$ROOT_DIR/javafx_agent/build/automation-harness-javafx-agent.jar"

log() { printf '[bootstrap] %s\n' "$*" >&2; }
warn() { printf '[bootstrap] WARNING: %s\n' "$*" >&2; }
die() { printf '[bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

load_host() {
    [[ "$(uname -s)" == "Linux" ]] || die "this deployment bootstrap requires Linux"
    [[ -r /etc/os-release ]] || die "/etc/os-release is required"
    # shellcheck disable=SC1091
    . /etc/os-release
    HOST_ID="${ID:-unknown}"
    HOST_VERSION="${VERSION_ID:-unknown}"
    log "Detected ${PRETTY_NAME:-$HOST_ID $HOST_VERSION}"
    [[ "$HOST_ID" == "rhel" ]] || die "this branch is qualified specifically for Red Hat Enterprise Linux 8"
    [[ "${HOST_VERSION%%.*}" == "8" ]] || die "this branch is qualified specifically for RHEL 8.x"
}

as_root() {
    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        return 1
    fi
}

with_timeout() {
    local seconds="$1"
    shift
    if command -v timeout >/dev/null 2>&1; then
        timeout --foreground "${seconds}s" "$@"
    else
        "$@"
    fi
}

dnf_query() {
    with_timeout "$DNF_TIMEOUT" dnf -q \
        --setopt=timeout="$DNF_TIMEOUT" \
        --setopt=retries=1 \
        "$@"
}

dnf_install() {
    if command -v timeout >/dev/null 2>&1; then
        as_root timeout --foreground "${DNF_TIMEOUT}s" dnf -y \
            --setopt=timeout="$DNF_TIMEOUT" \
            --setopt=retries=1 \
            install "$@"
    else
        as_root dnf -y \
            --setopt=timeout="$DNF_TIMEOUT" \
            --setopt=retries=1 \
            install "$@"
    fi
}

verify_python() {
    [[ -x "$SYSTEM_PYTHON" ]] || die "RHEL system Python was not found at $SYSTEM_PYTHON"
    "$SYSTEM_PYTHON" - <<'PY' || exit $?
import sys
if not ((3, 6) <= sys.version_info[:2] < (3, 7)):
    raise SystemExit("Automation Harness RHEL-8 backport requires Python 3.6.x; found %s" % sys.version.split()[0])
print("[bootstrap] System Python: %s" % sys.version.split()[0])
PY
}

install_available_rpms() {
    command -v dnf >/dev/null 2>&1 || return 0

    local packages=(
        python3-gobject
        python3-cairo
        python3-pyatspi
        at-spi2-core
        at-spi2-atk
        gtk3
        dbus-x11
        xorg-x11-xauth
        xorg-x11-server-Xvfb
        java-atk-wrapper
    )
    local available=() package

    for package in "${packages[@]}"; do
        if rpm -q "$package" >/dev/null 2>&1; then
            log "RPM present: $package"
            continue
        fi
        log "Checking RHEL repository for: $package"
        if dnf_query list --available "$package" >/dev/null 2>&1; then
            available+=("$package")
        else
            warn "RPM unavailable or repository probe timed out: $package"
        fi
    done

    if ((${#available[@]})); then
        log "Installing ${#available[@]} available native RPM(s): ${available[*]}"
        if ! dnf_install "${available[@]}"; then
            warn "Native RPM installation failed or timed out; continuing to capability checks"
        fi
    fi
}

create_venv() {
    if [[ -x "$VENV_DIR/bin/python" ]]; then
        if ! "$VENV_DIR/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 6) else 1)' >/dev/null 2>&1; then
            warn "Removing an existing non-Python-3.6 virtual environment"
            rm -rf "$VENV_DIR"
        fi
    fi
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        log "Creating Python 3.6 virtual environment with RHEL system packages visible"
        "$SYSTEM_PYTHON" -m venv --system-site-packages "$VENV_DIR" || die "python3 -m venv failed; the RHEL Python installation must provide venv support"
    fi
}

install_optional_pillow() {
    local py="$VENV_DIR/bin/python"
    if "$py" -c 'import PIL; from PIL import Image; print(PIL.__version__)' >/dev/null 2>&1; then
        log "Pillow vision capability is already available"
        return 0
    fi

    if rpm -q python3-pillow >/dev/null 2>&1; then
        log "RHEL python3-pillow RPM is installed"
    elif command -v dnf >/dev/null 2>&1; then
        log "Checking RHEL repository for optional vision package: python3-pillow"
        if dnf_query list --available python3-pillow >/dev/null 2>&1; then
            log "Installing optional RHEL vision package: python3-pillow"
            dnf_install python3-pillow || warn "Could not install python3-pillow"
        else
            warn "python3-pillow is unavailable from the enabled repositories"
        fi
    fi

    if "$py" -c 'import PIL; from PIL import Image; print(PIL.__version__)' >/dev/null 2>&1; then
        log "Pillow vision capability is available through the RHEL Python stack"
        return 0
    fi

    warn "Pillow is unavailable. Core GTK/AT-SPI Object Capture remains supported, but screenshots, visual baselines, masks, and image-based matching are disabled."
    return 1
}

install_python_dependencies() {
    local py="$VENV_DIR/bin/python"
    log "Updating Python packaging tools to the last Python-3.6-compatible line"
    "$py" -m pip --timeout "$PIP_TIMEOUT" --retries 1 install --upgrade \
        'pip==21.3.1' 'setuptools==59.6.0' 'wheel==0.37.1' || \
        warn "Packaging-tool upgrade failed; continuing with existing tools"

    log "Installing Python 3.6 compatibility/runtime dependencies"
    "$py" -m pip --timeout "$PIP_TIMEOUT" --retries 1 install \
        'dataclasses==0.8' \
        'typing_extensions==4.1.1' || die "Python runtime dependencies could not be installed"

    install_optional_pillow || true

    log "Installing Automation Harness from the extracted source tree"
    (cd "$ROOT_DIR" && "$py" setup.py develop) || die "Automation Harness installation failed"
}

build_javafx_agent() {
    if ! command -v javac >/dev/null 2>&1 || ! command -v jar >/dev/null 2>&1; then
        warn "JDK compiler tools are unavailable; JavaFX native bridge agent was not built"
        return 0
    fi
    log "Building JavaFX native bridge agent with $(javac -version 2>&1)"
    if bash "$ROOT_DIR/javafx_agent/build.sh" >/dev/null && [[ -f "$JAVAFX_AGENT_JAR" ]]; then
        log "JavaFX native bridge agent: $JAVAFX_AGENT_JAR"
    else
        warn "JavaFX native bridge agent build failed; Swing/AT-SPI capture remains available"
    fi
}

verify_native_python_bindings() {
    local py="$VENV_DIR/bin/python"
    "$py" - <<'PY'
modules = ("yaml", "gi", "cairo", "pyatspi")
failed = []
for module in modules:
    try:
        loaded = __import__(module)
        print("[bootstrap] Python binding OK: %s (%s)" % (module, getattr(loaded, "__file__", "built-in")))
    except Exception as exc:
        failed.append((module, exc))
        print("[bootstrap] Python binding FAIL: %s (%s: %s)" % (module, type(exc).__name__, exc))
try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    print("[bootstrap] GTK binding OK: %s.%s" % (Gtk.get_major_version(), Gtk.get_minor_version()))
except Exception as exc:
    failed.append(("Gtk", exc))
    print("[bootstrap] GTK binding FAIL: %s: %s" % (type(exc).__name__, exc))
try:
    import PIL
    print("[bootstrap] Optional Pillow binding OK: %s (%s)" % (getattr(PIL, "__version__", "unknown"), getattr(PIL, "__file__", "built-in")))
except Exception as exc:
    print("[bootstrap] Optional Pillow binding unavailable: %s: %s" % (type(exc).__name__, exc))
if failed:
    raise SystemExit(1)
PY
}

find_java_atk_wrapper() {
    local candidate
    for candidate in \
        /usr/share/java/java-atk-wrapper.jar \
        /usr/share/java/java-atk-wrapper/java-atk-wrapper.jar \
        /usr/lib64/java-atk-wrapper/java-atk-wrapper.jar; do
        [[ -f "$candidate" ]] && { printf '%s\n' "$candidate"; return 0; }
    done
    find /usr/share/java /usr/lib/java /usr/lib64/java /usr/lib64/java-atk-wrapper \
        -type f -name 'java-atk-wrapper*.jar' -print -quit 2>/dev/null || true
}

write_environment() {
    local wrapper=""
    wrapper="$(find_java_atk_wrapper)"
    {
        printf '# Generated by bootstrap.sh for the RHEL 8 / Python 3.6 deployment\n'
        printf 'export AUTOMATION_HARNESS_ROOT=%q\n' "$ROOT_DIR"
        printf 'export AUTOMATION_HARNESS_VENV=%q\n' "$VENV_DIR"
        printf 'export PATH=%q:$PATH\n' "$VENV_DIR/bin"
        [[ -n "$wrapper" ]] && printf 'export AUTOMATION_HARNESS_JAVA_ATK_WRAPPER=%q\n' "$wrapper"
        [[ -f "$JAVAFX_AGENT_JAR" ]] && printf 'export AUTOMATION_HARNESS_JAVAFX_AGENT=%q\n' "$JAVAFX_AGENT_JAR"
    } > "$ROOT_DIR/.automation-harness-env"
}

qualify() {
    local run="$VENV_DIR/bin/automation-run"
    log "Checking CLI import and registered-step catalog"
    "$run" steps list >/dev/null || die "Automation Harness cannot import/run under Python 3.6"

    if [[ -n "${DISPLAY:-}" ]]; then
        log "Smoke-testing GTK Object Capture on native display $DISPLAY"
        "$VENV_DIR/bin/automation-capture" --smoke-test || die "GTK Object Capture smoke test failed"
    else
        warn "DISPLAY is not set; GTK authoring UI cannot be smoke-tested in this shell"
    fi

    local display_mode=""
    if [[ -n "${DISPLAY:-}" ]]; then
        display_mode="auto"
    elif command -v Xvfb >/dev/null 2>&1; then
        display_mode="virtual"
    fi
    if [[ -n "$display_mode" ]]; then
        log "Running reference self-test with display mode: $display_mode"
        "$run" selftest --reference-display "$display_mode" || warn "reference GUI self-test did not fully qualify"
        log "Running AT-SPI qualification"
        "$run" selftest --require-atspi --reference-display "$display_mode" || warn "AT-SPI qualification did not fully qualify"
    else
        warn "Neither DISPLAY nor Xvfb is available; GUI execution cannot yet be qualified"
    fi

    local wrapper
    wrapper="$(find_java_atk_wrapper)"
    if [[ -n "$wrapper" ]]; then
        log "Java ATK wrapper for Swing accessibility: $wrapper"
    else
        warn "Java ATK wrapper is not installed; Swing accessibility remains unavailable"
    fi
    if [[ -f "$JAVAFX_AGENT_JAR" ]]; then
        log "JavaFX bridge agent ready. Instrument JavaFX targets with: -javaagent:$JAVAFX_AGENT_JAR"
    else
        warn "JavaFX bridge agent is unavailable; Linux JavaFX Node capture is disabled"
    fi
}

main() {
    load_host
    verify_python
    install_available_rpms
    create_venv
    install_python_dependencies
    build_javafx_agent
    verify_native_python_bindings || die "required RHEL GTK/AT-SPI bindings are not visible inside the virtual environment"
    write_environment
    qualify
    log "Bootstrap complete"
    log "Activate with: source '$ROOT_DIR/.automation-harness-env'"
}

main "$@"
