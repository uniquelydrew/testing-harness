#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${AUTOMATION_HARNESS_VENV:-$ROOT_DIR/.venv}"
RUNTIME_DIR="${AUTOMATION_HARNESS_RUNTIME_DIR:-$ROOT_DIR/.runtime}"
PYTHON_SOURCE_VERSION="${AUTOMATION_HARNESS_PYTHON_VERSION:-3.11.9}"
PYTHON_PREFIX="$RUNTIME_DIR/python-$PYTHON_SOURCE_VERSION"
ALLOW_PARTIAL="${AUTOMATION_HARNESS_ALLOW_PARTIAL:-0}"

log() { printf '[bootstrap] %s\n' "$*"; }
warn() { printf '[bootstrap] WARNING: %s\n' "$*" >&2; }
die() { printf '[bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

require_linux() {
    [[ "$(uname -s)" == "Linux" ]] || die "bootstrap.sh currently supports Linux hosts only"
    [[ -r /etc/os-release ]] || die "/etc/os-release is required for host detection"
}

load_os_release() {
    # shellcheck disable=SC1091
    . /etc/os-release
    HOST_ID="${ID:-unknown}"
    HOST_LIKE="${ID_LIKE:-}"
    HOST_VERSION="${VERSION_ID:-unknown}"
    log "Detected ${PRETTY_NAME:-$HOST_ID $HOST_VERSION}"
}

as_root() {
    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        die "root privileges are required to install native packages; run as root or install sudo"
    fi
}

is_rhel_family() {
    [[ "$HOST_ID" =~ ^(rhel|centos|rocky|almalinux|fedora)$ ]] || [[ " $HOST_LIKE " == *" rhel " ]] || [[ " $HOST_LIKE " == *" fedora " ]]
}

is_debian_family() {
    [[ "$HOST_ID" =~ ^(debian|ubuntu|linuxmint|pop)$ ]] || [[ " $HOST_LIKE " == *" debian " ]]
}

install_rhel_packages() {
    command -v dnf >/dev/null 2>&1 || die "RHEL-family host detected but dnf is unavailable"

    local required=(
        gcc gcc-c++ make curl tar gzip
        openssl-devel bzip2-devel libffi-devel zlib-devel xz-devel
        readline-devel sqlite-devel tk-devel
        pkgconf-pkg-config
        glib2-devel cairo-devel gobject-introspection-devel gtk3-devel
        at-spi2-core at-spi2-atk
        xorg-x11-server-Xvfb xorg-x11-xauth dbus-x11
    )

    log "Installing RHEL-family native prerequisites"
    as_root dnf install -y "${required[@]}"

    local optional=(java-atk-wrapper python3-pyatspi)
    local package
    for package in "${optional[@]}"; do
        if as_root dnf install -y "$package"; then
            log "Installed optional integration package: $package"
        else
            warn "Package '$package' is not available from the enabled repositories"
        fi
    done

    if ! find_python311 >/dev/null 2>&1; then
        log "Attempting to install a distribution Python 3.11 runtime"
        as_root dnf install -y python3.11 python3.11-pip python3.11-devel || true
    fi
}

install_debian_packages() {
    command -v apt-get >/dev/null 2>&1 || die "Debian-family host detected but apt-get is unavailable"
    log "Installing Debian-family native prerequisites"
    as_root apt-get update
    as_root apt-get install -y \
        build-essential curl tar gzip \
        libssl-dev libbz2-dev libffi-dev zlib1g-dev liblzma-dev \
        libreadline-dev libsqlite3-dev tk-dev pkg-config \
        libcairo2-dev libgirepository1.0-dev libgtk-3-dev \
        at-spi2-core xvfb xauth dbus-x11

    local optional=(libatk-wrapper-java python3-pyatspi)
    local package
    for package in "${optional[@]}"; do
        if as_root apt-get install -y "$package"; then
            log "Installed optional integration package: $package"
        else
            warn "Package '$package' is not available from the enabled repositories"
        fi
    done
}

install_native_packages() {
    if is_rhel_family; then
        install_rhel_packages
    elif is_debian_family; then
        install_debian_packages
    else
        die "Unsupported Linux distribution: ID=$HOST_ID ID_LIKE=$HOST_LIKE"
    fi
}

python_is_supported() {
    local exe="$1"
    "$exe" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

find_python311() {
    local candidate
    for candidate in \
        "$PYTHON_PREFIX/bin/python3.11" \
        python3.13 python3.12 python3.11 python3; do
        if [[ "$candidate" == /* ]]; then
            [[ -x "$candidate" ]] && python_is_supported "$candidate" && { printf '%s\n' "$candidate"; return 0; }
        elif command -v "$candidate" >/dev/null 2>&1 && python_is_supported "$candidate"; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

build_python() {
    mkdir -p "$RUNTIME_DIR"
    local archive="$RUNTIME_DIR/Python-$PYTHON_SOURCE_VERSION.tgz"
    local source_dir="$RUNTIME_DIR/Python-$PYTHON_SOURCE_VERSION"
    local url="https://www.python.org/ftp/python/$PYTHON_SOURCE_VERSION/Python-$PYTHON_SOURCE_VERSION.tgz"

    if [[ ! -x "$PYTHON_PREFIX/bin/python3.11" ]]; then
        log "No Python 3.11+ runtime was found; building Python $PYTHON_SOURCE_VERSION locally"
        [[ -f "$archive" ]] || curl -fL "$url" -o "$archive"
        rm -rf "$source_dir"
        tar -xzf "$archive" -C "$RUNTIME_DIR"
        (
            cd "$source_dir"
            ./configure --prefix="$PYTHON_PREFIX" --with-ensurepip=install
            make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '2')"
            make install
        )
    fi

    printf '%s\n' "$PYTHON_PREFIX/bin/python3.11"
}

ensure_python() {
    local python
    if python="$(find_python311)"; then
        printf '%s\n' "$python"
    else
        build_python
    fi
}

create_venv() {
    local python="$1"
    if [[ -d "$VENV_DIR" ]]; then
        if ! "$VENV_DIR/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
            warn "Existing venv uses an unsupported Python; recreating it"
            rm -rf "$VENV_DIR"
        fi
    fi

    if [[ ! -d "$VENV_DIR" ]]; then
        log "Creating isolated virtual environment at $VENV_DIR"
        "$python" -m venv "$VENV_DIR"
    fi

    "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
}

install_python_stack() {
    local py="$VENV_DIR/bin/python"
    log "Installing Automation Harness into the isolated environment"
    "$py" -m pip install "$ROOT_DIR"

    # RHEL 8 ships an older GLib stack. Keep the PyGObject build line compatible
    # with enterprise Linux rather than unconditionally selecting the newest ABI.
    log "Installing Python GUI bindings"
    if ! "$py" -m pip install 'pycairo<1.28' 'PyGObject>=3.42,<3.48'; then
        if [[ "$ALLOW_PARTIAL" == "1" ]]; then
            warn "PyGObject installation failed; continuing because AUTOMATION_HARNESS_ALLOW_PARTIAL=1"
        else
            die "PyGObject installation failed; native development prerequisites or repository access are incomplete"
        fi
    fi
}

bridge_system_pyatspi() {
    local py="$VENV_DIR/bin/python"
    if "$py" -c 'import pyatspi' >/dev/null 2>&1; then
        log "pyatspi is already importable by the harness runtime"
        return 0
    fi

    local site_packages
    site_packages="$($py -c 'import site; print(site.getsitepackages()[0])')"
    local source=""
    local candidate
    for candidate in /usr/lib/python*/site-packages/pyatspi /usr/lib64/python*/site-packages/pyatspi; do
        if [[ -d "$candidate" ]]; then
            source="$candidate"
            break
        fi
    done

    if [[ -n "$source" ]]; then
        log "Bridging distribution pyatspi sources into the isolated runtime"
        rm -rf "$site_packages/pyatspi"
        cp -a "$source" "$site_packages/pyatspi"
    fi

    if ! "$py" -c 'import pyatspi' >/dev/null 2>&1; then
        warn "pyatspi is not importable by Python 3.11+; AT-SPI interaction will not qualify"
        return 1
    fi
}

find_java_atk_wrapper() {
    local candidate
    for candidate in \
        /usr/share/java/java-atk-wrapper.jar \
        /usr/share/java/java-atk-wrapper/java-atk-wrapper.jar; do
        [[ -f "$candidate" ]] && { printf '%s\n' "$candidate"; return 0; }
    done
    find /usr/share/java /usr/lib/java /usr/lib64/java -type f -name 'java-atk-wrapper*.jar' -print -quit 2>/dev/null || true
}

write_environment_file() {
    local wrapper=""
    wrapper="$(find_java_atk_wrapper)"
    {
        printf '# Generated by bootstrap.sh\n'
        printf 'export AUTOMATION_HARNESS_ROOT=%q\n' "$ROOT_DIR"
        printf 'export AUTOMATION_HARNESS_VENV=%q\n' "$VENV_DIR"
        printf 'export PATH=%q:$PATH\n' "$VENV_DIR/bin"
        if [[ -n "$wrapper" ]]; then
            printf 'export AUTOMATION_HARNESS_JAVA_ATK_WRAPPER=%q\n' "$wrapper"
        fi
    } > "$ROOT_DIR/.automation-harness-env"
}

qualify() {
    local run="$VENV_DIR/bin/automation-run"
    log "Running base harness self-test"
    "$run" selftest

    local integration_failures=0
    if bridge_system_pyatspi; then
        log "Running AT-SPI qualification"
        if ! "$run" selftest --require-atspi; then
            warn "AT-SPI qualification failed"
            integration_failures=$((integration_failures + 1))
        fi
    else
        integration_failures=$((integration_failures + 1))
    fi

    local wrapper
    wrapper="$(find_java_atk_wrapper)"
    if [[ -z "$wrapper" ]]; then
        warn "Java ATK wrapper JAR was not found; Swing/JavaFX accessibility is not fully installed"
        integration_failures=$((integration_failures + 1))
    else
        log "Java ATK wrapper: $wrapper"
    fi

    if ! command -v Xvfb >/dev/null 2>&1; then
        warn "Xvfb is unavailable"
        integration_failures=$((integration_failures + 1))
    fi
    if ! command -v dbus-run-session >/dev/null 2>&1; then
        warn "dbus-run-session is unavailable"
        integration_failures=$((integration_failures + 1))
    fi

    if (( integration_failures > 0 )) && [[ "$ALLOW_PARTIAL" != "1" ]]; then
        die "$integration_failures GUI/accessibility qualification check(s) failed. Set AUTOMATION_HARNESS_ALLOW_PARTIAL=1 only for a deliberately partial installation."
    fi
}

main() {
    require_linux
    load_os_release
    install_native_packages

    local python
    python="$(ensure_python)"
    log "Using Python runtime: $python ($($python --version 2>&1))"

    create_venv "$python"
    install_python_stack
    write_environment_file
    qualify

    log "Bootstrap complete"
    log "Activate with: source '$ROOT_DIR/.automation-harness-env'"
    log "Run with: $VENV_DIR/bin/automation-run"
}

main "$@"
