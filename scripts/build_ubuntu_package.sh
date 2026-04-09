#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="llm-pid-tuner"
APP_LABEL="LLM PID Tuner"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/venv_build}"
BUILD_DIR="${BUILD_DIR:-$ROOT_DIR/build/ubuntu}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/artifacts/ubuntu}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

install_runtime_files() {
    local target_dir="$1"

    install -m 755 "$BIN_PATH" "$target_dir/$APP_NAME"
    install -m 644 "$ROOT_DIR/LICENSE" "$target_dir/LICENSE"
    install -m 644 "$ROOT_DIR/config.example.json" "$target_dir/config.example.json"
    install -m 644 "$ROOT_DIR/firmware.cpp" "$target_dir/firmware.cpp"
}

create_virtualenv() {
    local temp_log

    if [ -f "$VENV_DIR/bin/activate" ]; then
        return
    fi

    rm -rf "$VENV_DIR"

    temp_log="$(mktemp)"

    if "$PYTHON_BIN" -m venv "$VENV_DIR" >"$temp_log" 2>&1; then
        rm -f "$temp_log"
        return
    fi

    cat "$temp_log" >&2
    rm -f "$temp_log"
    rm -rf "$VENV_DIR"

    echo "==> Falling back to virtualenv bootstrap"
    "$PYTHON_BIN" -m pip install --user --break-system-packages virtualenv
    "$PYTHON_BIN" -m virtualenv "$VENV_DIR"
}

require_command "$PYTHON_BIN"
require_command dpkg-deb
require_command git
require_command tar

ARCH="$(dpkg --print-architecture)"
GIT_SHA="$(git -C "$ROOT_DIR" rev-parse --short HEAD)"
APP_VERSION="${APP_VERSION:-0.0.0+git${GIT_SHA}}"
DEB_VERSION="${DEB_VERSION:-${APP_VERSION}-1}"
ARTIFACT_BASENAME="${ARTIFACT_BASENAME:-$APP_NAME}"

echo "==> Root: $ROOT_DIR"
echo "==> Version: $APP_VERSION"

create_virtualenv

source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r "$ROOT_DIR/requirements.txt" pyinstaller pytest

rm -rf "$ROOT_DIR/build" "$ROOT_DIR/dist" "$OUTPUT_DIR"
mkdir -p "$BUILD_DIR" "$OUTPUT_DIR"

python -m pytest
python -m PyInstaller --clean --noconfirm "$ROOT_DIR/llm-pid-tuner.spec"

BIN_PATH="$ROOT_DIR/dist/$APP_NAME"

if [ ! -x "$BIN_PATH" ]; then
    echo "Build output not found: $BIN_PATH" >&2
    exit 1
fi

BUNDLE_NAME="$APP_NAME"
BUNDLE_DIR="$BUILD_DIR/$BUNDLE_NAME"

mkdir -p "$BUNDLE_DIR"
install_runtime_files "$BUNDLE_DIR"

cat >"$BUNDLE_DIR/run.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

if [ ! -f config.json ] && [ -f config.example.json ]; then
    cp config.example.json config.json
fi

exec "$APP_DIR/llm-pid-tuner" "$@"
EOF

chmod 755 "$BUNDLE_DIR/run.sh"

TAR_PATH="$OUTPUT_DIR/${ARTIFACT_BASENAME}.tar.gz"
tar -C "$BUILD_DIR" -czf "$TAR_PATH" "$BUNDLE_NAME"

DEB_ROOT="$BUILD_DIR/deb-root"
INSTALL_DIR="/opt/$APP_NAME"

rm -rf "$DEB_ROOT"
mkdir -p \
    "$DEB_ROOT/DEBIAN" \
    "$DEB_ROOT$INSTALL_DIR" \
    "$DEB_ROOT/usr/bin" \
    "$DEB_ROOT/usr/share/applications"

install_runtime_files "$DEB_ROOT$INSTALL_DIR"

cat >"$DEB_ROOT/usr/bin/$APP_NAME" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

APP_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/llm-pid-tuner"
INSTALL_DIR="/opt/llm-pid-tuner"

mkdir -p "$APP_HOME"

if [ ! -f "$APP_HOME/config.json" ] && [ -f "$INSTALL_DIR/config.example.json" ]; then
    cp "$INSTALL_DIR/config.example.json" "$APP_HOME/config.json"
fi

cd "$APP_HOME"
exec "$INSTALL_DIR/llm-pid-tuner" "$@"
EOF

chmod 755 "$DEB_ROOT/usr/bin/$APP_NAME"

cat >"$DEB_ROOT/usr/share/applications/$APP_NAME.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_LABEL
Comment=Tune PID controllers with LLM assistance
Exec=/usr/bin/$APP_NAME
Icon=utilities-terminal
Terminal=true
Categories=Utility;Development;
StartupNotify=true
EOF

cat >"$DEB_ROOT/DEBIAN/control" <<EOF
Package: $APP_NAME
Version: $DEB_VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: KINGSTON-115 <noreply@example.com>
Depends: libc6, zlib1g
Description: LLM-assisted PID tuning toolkit packaged for Ubuntu
 A self-contained Ubuntu package for running the LLM PID Tuner launcher.
EOF

DEB_PATH="$OUTPUT_DIR/${ARTIFACT_BASENAME}.deb"
dpkg-deb --build --root-owner-group "$DEB_ROOT" "$DEB_PATH"

(
    cd "$OUTPUT_DIR"
    sha256sum "$(basename "$TAR_PATH")" "$(basename "$DEB_PATH")" > SHA256SUMS
)

echo "==> Artifacts"
echo "    $TAR_PATH"
echo "    $DEB_PATH"
echo "    $OUTPUT_DIR/SHA256SUMS"
