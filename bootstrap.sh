#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${AUTOMATION_HARNESS_VENV:-$ROOT_DIR/.venv}"
SYSTEM_PYTHON="${AUTOMATION_HARNESS_PYTHON:-/usr/bin/python3}"
DNF_TIMEOUT="${AUTOMATION_HARNESS_DNF_TIMEOUT:-20}"
PIP_TIMEOUT="${AUTOMATION_HARNESS_PIP_TIMEOUT:-30}"
PILLOW_VERSION="${AUTOMATION_HARNESS_PILLOW_VERSION:-8.4.0}"
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
    [[ "$HOST_ID" == "rhel" ]] || die "this deployment bootstrap requires Red Hat Enterprise Linux 8"
    [[ "${HOST_VERSION%%.*}" == "8" ]] || die "this deployment bootstrap requires RHEL 8.x"
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

pillow_available() {
    "$VENV_DIR/bin/python" - <<'PY' >/dev/null 2>&1
import PIL
from PIL import Image, ImageChops, ImageGrab
PY
}

install_required_pillow() {
    local py="$VENV_DIR/bin/python"
    if pillow_available; then
        log "Required Pillow vision capability is already available: $($py -c 'import PIL; print(PIL.__version__)')"
        return 0
    fi

    if rpm -q python3-pillow >/dev/null 2>&1; then
        log "RHEL python3-pillow RPM is installed; rechecking the virtual environment"
    elif command -v dnf >/dev/null 2>&1; then
        log "Checking RHEL repository for required vision package: python3-pillow"
        if dnf_query list --available python3-pillow >/dev/null 2>&1; then
            log "Installing required RHEL vision package: python3-pillow"
            if ! dnf_install python3-pillow; then
                warn "RHEL python3-pillow installation failed; falling back to pip"
            fi
        else
            warn "python3-pillow is unavailable from the enabled repositories; falling back to pip"
        fi
    fi

    if pillow_available; then
        log "Required Pillow vision capability is available through the RHEL Python stack: $($py -c 'import PIL; print(PIL.__version__)')"
        return 0
    fi

    log "Installing required Pillow==$PILLOW_VERSION into the application virtual environment"
    "$py" -m pip --timeout "$PIP_TIMEOUT" --retries 1 install "Pillow==$PILLOW_VERSION" || \
        die "required Pillow vision dependency could not be installed from either RHEL repositories or pip"

    pillow_available || die "Pillow installed but required Image/ImageChops/ImageGrab modules are not importable"
    log "Required Pillow vision capability installed: $($py -c 'import PIL; print(PIL.__version__)')"
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

    install_required_pillow

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
    from PIL import Image, ImageChops, ImageGrab
    print("[bootstrap] Required Pillow binding OK: %s (%s)" % (getattr(PIL, "__version__", "unknown"), getattr(PIL, "__file__", "built-in")))
except Exception as exc:
    failed.append(("Pillow", exc))
    print("[bootstrap] Required Pillow binding FAIL: %s: %s" % (type(exc).__name__, exc))
if failed:
    raise SystemExit(1)
PY
}

probe_pillow_capture() {
    local display="$1"
    DISPLAY="$display" "$VENV_DIR/bin/python" - <<'PY'
from PIL import ImageGrab
image = ImageGrab.grab()
if image.width <= 0 or image.height <= 0:
    raise SystemExit("captured framebuffer has invalid dimensions: %sx%s" % (image.width, image.height))
print("[bootstrap] Pillow screen capture OK: %sx%s" % (image.width, image.height))
PY
}

verify_pillow_screen_capture() {
    if [[ -n "${DISPLAY:-}" ]]; then
        log "Qualifying Pillow framebuffer capture on native display $DISPLAY"
        probe_pillow_capture "$DISPLAY" || die "Pillow is importable but cannot capture the active display $DISPLAY"
        return 0
    fi

    command -v Xvfb >/dev/null 2>&1 || \
        die "Pillow screenshot qualification requires an active DISPLAY or Xvfb"

    local display_number="" number
    for number in $(seq 200 249); do
        if [[ ! -e "/tmp/.X11-unix/X$number" ]]; then
            display_number="$number"
            break
        fi
    done
    [[ -n "$display_number" ]] || die "no free X11 display was available for Pillow screenshot qualification"

    local display=":$display_number"
    local socket="/tmp/.X11-unix/X$display_number"
    local xvfb_log="/tmp/automation-harness-bootstrap-xvfb.$$.log"
    log "Qualifying Pillow framebuffer capture on temporary Xvfb display $display"
    Xvfb "$display" -screen 0 1280x800x24 -nolisten tcp -ac >"$xvfb_log" 2>&1 &
    local xvfb_pid=$!
    local ready=0 attempt
    for attempt in $(seq 1 50); do
        if [[ -S "$socket" ]]; then
            ready=1
            break
        fi
        if ! kill -0 "$xvfb_pid" >/dev/null 2>&1; then
            break
        fi
        sleep 0.1
    done

    local status=0
    if [[ "$ready" -eq 1 ]]; then
        probe_pillow_capture "$display" || status=$?
    else
        status=1
    fi

    kill "$xvfb_pid" >/dev/null 2>&1 || true
    wait "$xvfb_pid" >/dev/null 2>&1 || true
    if [[ "$status" -ne 0 ]]; then
        if [[ -s "$xvfb_log" ]]; then
            warn "Temporary Xvfb output: $(tr '\n' ' ' < "$xvfb_log")"
        fi
        rm -f "$xvfb_log"
        die "Pillow is importable but framebuffer capture failed on the bootstrap qualification display"
    fi
    rm -f "$xvfb_log"
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

    verify_pillow_screen_capture

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
    verify_native_python_bindings || die "required RHEL GTK/AT-SPI/Pillow bindings are not visible inside the virtual environment"
    write_environment
    qualify
    log "Bootstrap complete"
    log "Activate with: source '$ROOT_DIR/.automation-harness-env'"
}

main "$@"
