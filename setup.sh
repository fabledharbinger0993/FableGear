#!/bin/bash
# FableGear — first-run dependency installer
# Opened automatically by launch.sh when system tools or the Python venv
# are missing. Runs in a visible Terminal window so the user can see progress.
#
# When complete it touches .fablegear_ready so launch.sh knows to proceed.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/venv"
SENTINEL="$SCRIPT_DIR/.fablegear_ready"
FAILED="$SCRIPT_DIR/.fablegear_failed"

# Clear any stale sentinels so this run starts from a clean state.
rm -f "$SENTINEL" "$FAILED"

# fail() — signal launch.sh to stop polling immediately, then wait for the
# user to read the (already-printed) error before closing the window.
fail() {
  touch "$FAILED"
  read -rp "     Press Return to close this window." _
  exit 1
}

# ── Banner ────────────────────────────────────────────────────────────────
clear
echo ""
echo "  ╔════════════════════════════════════════════════════════╗"
echo "  ║            FableGear — First-Run Setup                  ║"
echo "  ║  This runs once. FableGear will launch when it's done.  ║"
echo "  ╚════════════════════════════════════════════════════════╝"
echo ""

# ── Helper: print a step header ───────────────────────────────────────────
step() { echo ""; echo "  ── $1"; }
ok()   { echo "  ✓  $1"; }
info() { echo "     $1"; }

# ── Optional Homebrew environment ─────────────────────────────────────────
step "System tools"

BREW=""
for p in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  [ -f "$p" ] && BREW="$p" && break
done

if [ -n "$BREW" ]; then
  info "Found Homebrew at $BREW (FableGear will not update or modify it)."
  eval "$("$BREW" shellenv)"
else
  info "Homebrew not found; FableGear will not install it."
fi

MISSING_TOOLS=()
for tool in ffmpeg fpcalc; do
  command -v "$tool" >/dev/null 2>&1 || MISSING_TOOLS+=("$tool")
done

if [ "${#MISSING_TOOLS[@]}" -gt 0 ]; then
  echo ""
  echo "  ✗  Missing system tools: ${MISSING_TOOLS[*]}"
  echo "     Install ffmpeg and Chromaprint (fpcalc) yourself, then run FableGear again."
  if [ -n "$BREW" ]; then
    echo "     With Homebrew, for example: brew install ffmpeg chromaprint"
  fi
  fail
fi
ok "ffmpeg and fpcalc are available"

# ── Python 3 ─────────────────────────────────────────────────────────────
step "Python 3"

# Prefer supported versions before using whatever python3 resolves to.
PYTHON3=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" &>/dev/null; then
    version=$("$candidate" -c 'import sys; print("%s.%s" % sys.version_info[:2])' 2>/dev/null)
    if [[ "$version" =~ ^3\.(11|12|13)$ ]]; then
      PYTHON3="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON3" ]; then
  echo ""
  echo "  ✗  FableGear requires Python 3.11–3.13, but none was found."
  echo "     Install a supported Python version yourself, then run FableGear again."
  if [ -n "$BREW" ]; then
    echo "     With Homebrew, for example: brew install python@3.13"
  fi
  fail
fi
ok "Python $("$PYTHON3" --version 2>&1 | awk '{print $2}')"

# ── Python virtual environment ────────────────────────────────────────────
step "Python virtual environment"

# Remove a hollow venv left by Python 3.14 (no ensurepip → no activate/pip)
if [ -d "$VENV" ] && [ ! -f "$VENV/bin/activate" ]; then
  info "Broken venv detected (no activate script) — rebuilding..."
  rm -rf "$VENV"
fi

if [ ! -d "$VENV" ]; then
  info "Creating venv at $VENV ..."
  "$PYTHON3" -m venv "$VENV"
fi

# Sanity-check: if activate still missing, bail loudly before touching sentinel
if [ ! -f "$VENV/bin/activate" ]; then
  echo ""
  echo "  ✗  Could not create a working Python venv with $PYTHON3."
  echo "     Install a supported Python 3.11–3.13 version with venv support."
  fail
fi
ok "Virtual environment ready"

# Verify the venv Python meets FableGear's minimum (3.11+)
VENV_MINOR=$("$VENV/bin/python" -c 'import sys; print(sys.version_info[1])' 2>/dev/null)
VENV_VER=$("$VENV/bin/python" --version 2>&1 | awk '{print $2}')
if [ "${VENV_MINOR:-0}" -lt 11 ]; then
  echo ""
  echo "  ✗  Python $VENV_VER in this venv is too old — FableGear requires 3.11 or later."
  echo "     Install Python 3.11–3.13 and run FableGear again."
  fail
fi
ok "Python version check passed ($VENV_VER)"

# shellcheck disable=SC1091
source "$VENV/bin/activate"

# ── Python packages ───────────────────────────────────────────────────────
step "Python packages"

info "Upgrading pip..."
pip install --upgrade pip --quiet

# No --quiet on the requirements installs: if a wheel fails to build (e.g. a
# C-extension or SQLCipher/pyrekordbox), the user must see the real error in
# this visible window. Any failure aborts setup via fail() so launch.sh does
# not proceed to launch an app with missing dependencies.
info "Installing UI packages (Flask, Waitress, pywebview)..."
pip install -r "$SCRIPT_DIR/requirements_ui.txt" \
  || { echo "  ✗  UI package install failed — see the errors above."; fail; }

info "Installing library packages..."
pip install -r "$SCRIPT_DIR/requirements.txt" \
  || { echo "  ✗  Library package install failed — see the errors above."; fail; }

# Optional packages are installed best-effort and MUST NOT abort setup. essentia
# in particular has narrow wheel coverage (no cp313 wheel below macOS 15), and
# as a hard requirement a missing wheel would take the whole install down with
# it. Without it, beat detection falls back to librosa and health.py raises a
# `beat_tracker_degraded` warning — degraded, not broken.
info "Installing optional packages (better beat detection)..."
if pip install -r "$SCRIPT_DIR/requirements_optional.txt"; then
  ok "Optional packages installed"
else
  echo "  !  Optional packages unavailable for this Python/macOS combination."
  echo "     FableGear will run, using less accurate beat detection."
  echo "     The Health panel will show this as 'beat detection in fallback mode'."
fi

ok "All Python packages installed"

# ── Create launcher .app ─────────────────────────────────────────────────
step "Creating FableGear.app launcher"

APP_DEST="$HOME/Applications/FableGear.app"

# Locate the packaged launcher app — name casing differs across checkouts,
# and case-sensitive filesystems won't match "FableGear.app" to "FABLEGEAR.app".
PACKAGED_APP=""
for candidate in "$SCRIPT_DIR/packaging/FableGear.app" "$SCRIPT_DIR/packaging/FABLEGEAR.app"; do
  [ -d "$candidate" ] && PACKAGED_APP="$candidate" && break
done

mkdir -p "$HOME/Applications"

# Prefer the bundled shell-script launcher because Finder surfaces generic
# "/bin/bash" alerts when the AppleScript do-shell-script wrapper fails.
# The packaged .app already knows how to hand off to ~/FableGear.
rm -rf "$APP_DEST"
if [ -n "$PACKAGED_APP" ] && [ -d "$PACKAGED_APP" ]; then
  cp -R "$PACKAGED_APP" "$APP_DEST"
  chmod +x "$APP_DEST/Contents/MacOS/FableGear" 2>/dev/null || true
else
  LAUNCH_PATH="$SCRIPT_DIR/launch.sh"
  osacompile -o "$APP_DEST" - 2>/dev/null <<APPLESCRIPT
do shell script "bash '$LAUNCH_PATH'"
APPLESCRIPT
fi

if [ -d "$APP_DEST" ]; then
  ok "FableGear.app created at ~/Applications/FableGear.app"

  # ── Apply icon (sips + iconutil) ────────────────────────────────────────
  ICON_SRC="$SCRIPT_DIR/static/icon-app-dock.png"
  if [ -f "$ICON_SRC" ]; then
    ICONSET="$(mktemp -d)/fg.iconset"
    mkdir -p "$ICONSET"
    for size in 16 32 64 128 256 512; do
      sips -z "$size" "$size" "$ICON_SRC" \
        --out "$ICONSET/icon_${size}x${size}.png" >/dev/null 2>&1
      double=$((size * 2))
      sips -z "$double" "$double" "$ICON_SRC" \
        --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null 2>&1
    done
    iconutil -c icns "$ICONSET" \
      -o "$APP_DEST/Contents/Resources/applet.icns" 2>/dev/null && \
      ok "Icon applied" || info "Icon apply skipped (iconutil unavailable)"
    rm -rf "$(dirname "$ICONSET")"
    # Applet bundles ship an Assets.car whose compiled AppIcon outranks
    # applet.icns in Finder/Dock — remove it so the custom icon wins, then
    # touch the bundle so LaunchServices notices the change.
    rm -f "$APP_DEST/Contents/Resources/Assets.car"
    touch "$APP_DEST"
  fi

  info "Drag it to your Dock for one-click access."
  info "Or double-click it from ~/Applications."
else
  info "Could not create FableGear.app — run launch.sh directly from Terminal."
fi

# ── Done ──────────────────────────────────────────────────────────────────
touch "$SENTINEL"

echo ""
echo "  ╔════════════════════════════════════════════════════════╗"
echo "  ║  ✓  Setup complete. FableGear is launching now.         ║"
echo "  ║     This window will close in 4 seconds.               ║"
echo "  ╚════════════════════════════════════════════════════════╝"
echo ""
sleep 4
