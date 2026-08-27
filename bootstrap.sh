#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${AUTOMATION_HARNESS_VENV:-$ROOT_DIR/.venv}"
SYSTEM_PYTHON="${AUTOMATION_HARNESS_PYTHON:-/usr/bin/python3}"
DNF_TIMEOUT="${AUTOMATION_HARNESS_DNF_TIMEOUT:-20}"
DNF_RETRIES="${AUTOMATION_HARNESS_DNF_RETRIES:-1}"

log() { printf '[bootstrap] %s\n' "$*" >&2; }
warn() { printf '[bootstrap] WARNING: %s\n' "$*" >&2; }
die() { printf '[bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

run_timed() {
    local seconds="$1"; shift
    if command -v timeout >/dev/null 2>&1; then
        timeout --foreground "${seconds}s" "$@"
    else
        "$@"
    fi
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

load_host() {
    [[ "$(uname -s)" == "Linux" ]] || die "this deployment bootstrap requires Linux"
    [[ -r /etc/os-release ]] || die "/etc/os-release is required"
    # shellcheck disable=SC1091
    . /etc/os-release
    HOST_ID="${ID:-unknown}"
    HOST_VERSION="${VERSION_ID:-unknown}"
    log "Detected ${PRETTY_NAME:-$HOST_ID $HOST_VERSION}"
    [[ "$HOST_ID" == "rhel" ]] || die "this bootstrap is qualified specifically for Red Hat Enterprise Linux"
    [[ "${HOST_VERSION%%.*}" == "8" ]] || die "this bootstrap is qualified specifically for RHEL 8.x"
}

verify_python() {
    [[ -x "$SYSTEM_PYTHON" ]] || die "RHEL system Python was not found at $SYSTEM_PYTHON"
    "$SYSTEM_PYTHON" - <<'PY'
import sys
if not ((3, 6) <= sys.version_info[:2] < (3, 7)):
    raise SystemExit("Automation Harness RHEL-8 deployment requires Python 3.6.x; found %s" % sys.version.split()[0])
print("[bootstrap] System Python: %s" % sys.version.split()[0])
PY
}

dnf_cmd() {
    dnf -y \
        --setopt=timeout="$DNF_TIMEOUT" \
        --setopt=retries="$DNF_RETRIES" \
        --setopt=metadata_expire=0 \
        "$@"
}

probe_dnf() {
    command -v dnf >/dev/null 2>&1 || { warn "dnf is unavailable; continuing with already-installed host packages"; return 1; }
    log "Probing enabled RHEL repositories (timeout ${DNF_TIMEOUT}s)"
    if ! run_timed "$((DNF_TIMEOUT + 5))" dnf_cmd repolist; then
        warn "DNF repository probe timed out or failed; bootstrap will not wait on repository operations"
        return 1
    fi
    return 0
}

install_available_rpms() {
    local packages=(
        python3-gobject python3-cairo python3-pyatspi
        at-spi2-core at-spi2-atk gtk3 dbus-x11
        xorg-x11-xauth xorg-x11-server-Xvfb java-atk-wrapper
    )
    local missing=() available=() package

    for package in "${packages[@]}"; do
        if rpm -q "$package" >/dev/null 2>&1; then
            log "RPM present: $package"
        else
            missing+=("$package")
        fi
    done

    ((${#missing[@]})) || return 0
    probe_dnf || {
        warn "Skipping package installation because repository access is unhealthy"
        return 0
    }

    log "Checking ${#missing[@]} missing RPM(s) without indefinite waits"
    for package in "${missing[@]}"; do
        if run_timed "$((DNF_TIMEOUT + 5))" dnf_cmd -q list --available "$package" >/dev/null 2>&1; then
            available+=("$package")
            log "RPM available: $package"
        else
            warn "RPM unavailable or repository lookup failed: $package"
        fi
    done

    if ((${#available[@]})); then
        log "Installing available RPMs: ${available[*]}"
        if ! run_timed "$((DNF_TIMEOUT * 3 + 30))" as_root dnf_cmd install "${available[@]}"; then
            warn "RPM installation timed out or failed; continuing to capability verification"
        fi
    fi
}

create_venv() {
    if [[ -x "$VENV_DIR/bin/python" ]] && ! "$VENV_DIR/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 6) else 1)' >/dev/null 2>&1; then
        warn "Removing an existing non-Python-3.6 virtual environment"
        rm -rf "$VENV_DIR"
    fi
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        log "Creating Python 3.6 virtual environment with RHEL system packages visible"
        "$SYSTEM_PYTHON" -m venv --system-site-packages "$VENV_DIR" || die "python3 -m venv failed"
    fi
}

install_python_dependencies() {
    local py="$VENV_DIR/bin/python"
    log "Installing Python 3.6-compatible runtime dependencies"
    "$py" -m pip install --disable-pip-version-check --timeout 20 --retries 1 \
        'dataclasses==0.8' 'typing_extensions==4.1.1' 'Pillow==8.4.0' || \
        die "Python runtime dependencies could not be installed"

    log "Installing Automation Harness from the extracted source tree"
    (cd "$ROOT_DIR" && "$py" setup.py develop) || die "Automation Harness installation failed"
}

verify_native_python_bindings() {
    local py="$VENV_DIR/bin/python"
    "$py" - <<'PY'
modules = ("yaml", "gi", "cairo", "pyatspi", "PIL")
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
if failed:
    raise SystemExit(1)
PY
}

find_java_atk_wrapper() {
    local candidate
    for candidate in /usr/share/java/java-atk-wrapper.jar /usr/share/java/java-atk-wrapper/java-atk-wrapper.jar; do
        [[ -f "$candidate" ]] && { printf '%s\n' "$candidate"; return 0; }
    done
    find /usr/share/java /usr/lib/java /usr/lib64/java -type f -name 'java-atk-wrapper*.jar' -print -quit 2>/dev/null || true
}

write_environment() {
    local wrapper=""
    wrapper="$(find_java_atk_wrapper)"
    {
        printf '# Generated by bootstrap.sh for RHEL 8 / Python 3.6\n'
        printf 'export AUTOMATION_HARNESS_ROOT=%q\n' "$ROOT_DIR"
        printf 'export AUTOMATION_HARNESS_VENV=%q\n' "$VENV_DIR"
        printf 'export PATH=%q:$PATH\n' "$VENV_DIR/bin"
        [[ -n "$wrapper" ]] && printf 'export AUTOMATION_HARNESS_JAVA_ATK_WRAPPER=%q\n' "$wrapper"
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
    else
        warn "Neither DISPLAY nor Xvfb is available; GUI regression self-test skipped"
    fi
}

main() {
    load_host
    verify_python
    install_available_rpms
    create_venv
    install_python_dependencies
    verify_native_python_bindings || die "required RHEL GTK/AT-SPI bindings are not visible inside the virtual environment"
    write_environment
    qualify
    log "Bootstrap complete"
    log "Activate with: source '$ROOT_DIR/.automation-harness-env'"
}

main "$@"
