#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${AUTOMATION_HARNESS_VENV:-$ROOT_DIR/.venv}"
SYSTEM_PYTHON="${AUTOMATION_HARNESS_PYTHON:-/usr/bin/python3}"
DNF_TIMEOUT="${AUTOMATION_HARNESS_DNF_TIMEOUT:-20}"
PIP_TIMEOUT="${AUTOMATION_HARNESS_PIP_TIMEOUT:-30}"
PILLOW_VERSION="${AUTOMATION_HARNESS_PILLOW_VERSION:-8.4.0}"
JAVAFX_AGENT_JAR="$ROOT_DIR/javafx_agent/build/automation-harness-javafx-agent.jar"
JAVA_AGENT_JAR="$ROOT_DIR/java-agent/build/automation-harness-agent.jar"

# Never let an environment inherited from an older bootstrap inject an agent
# into javac/jar or unrelated Java processes while bootstrap is running.
unset JAVA_TOOL_OPTIONS

log() { printf '[bootstrap] %s\n' "$*" >&2; }
warn() { printf '[bootstrap] WARNING: %s\n' "$*" >&2; }
die() { printf '[bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

load_host() { [[ "$(uname -s)" == "Linux" ]] || die "this deployment bootstrap requires Linux"; [[ -r /etc/os-release ]] || die "/etc/os-release is required"; . /etc/os-release; HOST_ID="${ID:-unknown}"; HOST_VERSION="${VERSION_ID:-unknown}"; log "Detected ${PRETTY_NAME:-$HOST_ID $HOST_VERSION}"; [[ "$HOST_ID" == "rhel" ]] || die "this deployment bootstrap requires Red Hat Enterprise Linux 8"; [[ "${HOST_VERSION%%.*}" == "8" ]] || die "this deployment bootstrap requires RHEL 8.x"; }
as_root() { if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then "$@"; elif command -v sudo >/dev/null 2>&1; then sudo "$@"; else return 1; fi; }
with_timeout() { local seconds="$1"; shift; if command -v timeout >/dev/null 2>&1; then timeout --foreground "${seconds}s" "$@"; else "$@"; fi; }
dnf_query() { with_timeout "$DNF_TIMEOUT" dnf -q --setopt=timeout="$DNF_TIMEOUT" --setopt=retries=1 "$@"; }
dnf_install() { if command -v timeout >/dev/null 2>&1; then as_root timeout --foreground "${DNF_TIMEOUT}s" dnf -y --setopt=timeout="$DNF_TIMEOUT" --setopt=retries=1 install "$@"; else as_root dnf -y --setopt=timeout="$DNF_TIMEOUT" --setopt=retries=1 install "$@"; fi; }
verify_python() { [[ -x "$SYSTEM_PYTHON" ]] || die "RHEL system Python was not found at $SYSTEM_PYTHON"; "$SYSTEM_PYTHON" - <<'PY'
import sys
if not ((3, 6) <= sys.version_info[:2] < (3, 7)): raise SystemExit("Automation Harness RHEL-8 backport requires Python 3.6.x; found %s" % sys.version.split()[0])
print("[bootstrap] System Python: %s" % sys.version.split()[0])
PY
}
install_available_rpms() { command -v dnf >/dev/null 2>&1 || return 0; local packages=(python3-gobject python3-cairo python3-pyatspi at-spi2-core at-spi2-atk gtk3 dbus-x11 xorg-x11-xauth xorg-x11-server-Xvfb java-atk-wrapper); local available=() package; for package in "${packages[@]}"; do if rpm -q "$package" >/dev/null 2>&1; then log "RPM present: $package"; continue; fi; log "Checking RHEL repository for: $package"; if dnf_query list --available "$package" >/dev/null 2>&1; then available+=("$package"); else warn "RPM unavailable or repository probe timed out: $package"; fi; done; if ((${#available[@]})); then log "Installing ${#available[@]} available native RPM(s): ${available[*]}"; dnf_install "${available[@]}" || warn "Native RPM installation failed or timed out; continuing to capability checks"; fi; }
create_venv() { if [[ -x "$VENV_DIR/bin/python" ]] && ! "$VENV_DIR/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 6) else 1)' >/dev/null 2>&1; then warn "Removing an existing non-Python-3.6 virtual environment"; rm -rf "$VENV_DIR"; fi; if [[ ! -x "$VENV_DIR/bin/python" ]]; then log "Creating Python 3.6 virtual environment with RHEL system packages visible"; "$SYSTEM_PYTHON" -m venv --system-site-packages "$VENV_DIR" || die "python3 -m venv failed"; fi; }
pillow_available() { "$VENV_DIR/bin/python" - <<'PY' >/dev/null 2>&1
import PIL
from PIL import Image, ImageChops, ImageGrab
PY
}
install_required_pillow() { local py="$VENV_DIR/bin/python"; if pillow_available; then log "Required Pillow vision capability is already available: $($py -c 'import PIL; print(PIL.__version__)')"; return 0; fi; if ! rpm -q python3-pillow >/dev/null 2>&1 && command -v dnf >/dev/null 2>&1 && dnf_query list --available python3-pillow >/dev/null 2>&1; then dnf_install python3-pillow || true; fi; pillow_available && return 0; "$py" -m pip --timeout "$PIP_TIMEOUT" --retries 1 install "Pillow==$PILLOW_VERSION" || die "required Pillow vision dependency could not be installed"; pillow_available || die "Pillow installed but capture modules are not importable"; }
install_python_dependencies() { local py="$VENV_DIR/bin/python"; "$py" -m pip --timeout "$PIP_TIMEOUT" --retries 1 install --upgrade 'pip==21.3.1' 'setuptools==59.6.0' 'wheel==0.37.1' || warn "Packaging-tool upgrade failed"; "$py" -m pip --timeout "$PIP_TIMEOUT" --retries 1 install 'dataclasses==0.8' 'typing_extensions==4.1.1' || die "Python runtime dependencies could not be installed"; install_required_pillow; (cd "$ROOT_DIR" && "$py" setup.py develop) || die "Automation Harness installation failed"; }
build_javafx_agent() { if ! command -v javac >/dev/null 2>&1 || ! command -v jar >/dev/null 2>&1; then warn "JDK compiler tools are unavailable; Java agents were not built"; return 0; fi; log "Building JavaFX native bridge agent with $(env -u JAVA_TOOL_OPTIONS javac -version 2>&1)"; if env -u JAVA_TOOL_OPTIONS bash "$ROOT_DIR/javafx_agent/build.sh" >/dev/null && [[ -f "$JAVAFX_AGENT_JAR" ]]; then log "JavaFX native bridge agent: $JAVAFX_AGENT_JAR"; else warn "JavaFX native bridge agent build failed"; fi; log "Building Java-8-compatible Swing/JOGL native agent"; if env -u JAVA_TOOL_OPTIONS bash "$ROOT_DIR/java-agent/build.sh" >/dev/null && [[ -f "$JAVA_AGENT_JAR" ]]; then log "Java 8+ Swing/JOGL agent: $JAVA_AGENT_JAR"; else warn "Java agent build failed; unexposed Swing/JOGL capture is unavailable"; fi; }
verify_native_python_bindings() { "$VENV_DIR/bin/python" - <<'PY'
modules=("yaml","gi","cairo","pyatspi"); failed=[]
for module in modules:
 try: __import__(module); print("[bootstrap] Python binding OK: %s" % module)
 except Exception as exc: failed.append((module,exc)); print("[bootstrap] Python binding FAIL: %s (%s)" % (module,exc))
try:
 import gi; gi.require_version("Gtk","3.0"); from gi.repository import Gtk
except Exception as exc: failed.append(("Gtk",exc))
try: import PIL; from PIL import Image,ImageChops,ImageGrab
except Exception as exc: failed.append(("Pillow",exc))
if failed: raise SystemExit(1)
PY
}
probe_pillow_capture() { DISPLAY="$1" "$VENV_DIR/bin/python" - <<'PY'
from PIL import ImageGrab
image=ImageGrab.grab()
if image.width<=0 or image.height<=0: raise SystemExit("invalid framebuffer")
print("[bootstrap] Pillow screen capture OK: %sx%s" % (image.width,image.height))
PY
}
verify_pillow_screen_capture() {
    if [[ -n "${DISPLAY:-}" ]]; then
        probe_pillow_capture "$DISPLAY" || die "Pillow cannot capture $DISPLAY"
        return
    fi
    command -v Xvfb >/dev/null 2>&1 || die "Pillow qualification requires DISPLAY or Xvfb"

    local number="" candidate display socket logf pid ready=0 status=1
    for candidate in $(seq 200 249); do
        if [[ ! -e "/tmp/.X11-unix/X$candidate" ]]; then
            number="$candidate"
            break
        fi
    done
    [[ -n "$number" ]] || die "no free Xvfb display is available in :200-:249"

    display=":$number"
    socket="/tmp/.X11-unix/X$number"
    logf="/tmp/automation-harness-bootstrap-xvfb.$.log"
    Xvfb "$display" -screen 0 1280x800x24 -nolisten tcp -ac >"$logf" 2>&1 &
    pid=$!
    for _ in $(seq 1 50); do
        [[ -S "$socket" ]] && { ready=1; break; }
        sleep .1
    done
    if [[ "$ready" -eq 1 ]]; then
        probe_pillow_capture "$display"
        status=$?
    fi
    kill "$pid" >/dev/null 2>&1 || true
    wait "$pid" >/dev/null 2>&1 || true
    rm -f "$logf"
    [[ "$status" -eq 0 ]] || die "Pillow framebuffer capture failed"
}
find_java_atk_wrapper() { local candidate; for candidate in /usr/share/java/java-atk-wrapper.jar /usr/share/java/java-atk-wrapper/java-atk-wrapper.jar /usr/lib64/java-atk-wrapper/java-atk-wrapper.jar; do [[ -f "$candidate" ]] && { printf '%s\n' "$candidate"; return; }; done; find /usr/share/java /usr/lib/java /usr/lib64/java /usr/lib64/java-atk-wrapper -type f -name 'java-atk-wrapper*.jar' -print -quit 2>/dev/null || true; }
write_environment() { local wrapper="$(find_java_atk_wrapper)"; { printf '# Generated by bootstrap.sh for the RHEL 8 / Python 3.6 deployment\n'; printf '# Agent paths are exported, but JAVA_TOOL_OPTIONS is intentionally not set.\n'; printf '# Target launchers must inject the appropriate agent explicitly so JavaFX\n'; printf '# applications do not receive the Swing/JOGL agent and vice versa.\n'; printf 'export AUTOMATION_HARNESS_ROOT=%q\n' "$ROOT_DIR"; printf 'export AUTOMATION_HARNESS_VENV=%q\n' "$VENV_DIR"; printf 'export PATH=%q:$PATH\n' "$VENV_DIR/bin"; [[ -n "$wrapper" ]] && printf 'export AUTOMATION_HARNESS_JAVA_ATK_WRAPPER=%q\n' "$wrapper"; [[ -f "$JAVAFX_AGENT_JAR" ]] && printf 'export AUTOMATION_HARNESS_JAVAFX_AGENT=%q\n' "$JAVAFX_AGENT_JAR"; [[ -f "$JAVA_AGENT_JAR" ]] && printf 'export AUTOMATION_HARNESS_JAVA_AGENT=%q\n' "$JAVA_AGENT_JAR"; } > "$ROOT_DIR/.automation-harness-env"; }
qualify() { local run="$VENV_DIR/bin/automation-run"; "$run" steps list >/dev/null || die "Automation Harness cannot import/run under Python 3.6"; verify_pillow_screen_capture; if [[ -n "${DISPLAY:-}" ]]; then "$VENV_DIR/bin/automation-author" --smoke-test || die "GTK authoring smoke test failed"; fi; local display_mode=""; [[ -n "${DISPLAY:-}" ]] && display_mode="auto"; [[ -z "$display_mode" ]] && command -v Xvfb >/dev/null 2>&1 && display_mode="virtual"; if [[ -n "$display_mode" ]]; then "$run" selftest --reference-display "$display_mode" || warn "reference GUI self-test did not fully qualify"; "$run" selftest --require-atspi --reference-display "$display_mode" || warn "AT-SPI qualification did not fully qualify"; fi; if [[ -f "$JAVA_AGENT_JAR" ]]; then log "Java 8+ Swing/JOGL agent ready with automatic secure endpoint discovery."; log "Source '$ROOT_DIR/.automation-harness-env', then launch the target JVM with: automation-java-target --agent swing -- <target-command> [args...]"; fi; }
main() { load_host; verify_python; install_available_rpms; create_venv; install_python_dependencies; build_javafx_agent; verify_native_python_bindings || die "required bindings are unavailable"; write_environment; qualify; log "Bootstrap complete"; log "Activate with: source '$ROOT_DIR/.automation-harness-env'"; }
main "$@"
