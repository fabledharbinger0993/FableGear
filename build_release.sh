#!/bin/bash
# build_release.sh — builds the distributable FableGear.app and uploads a new GitHub release.
#
# Uses a shell-script .app (not osacompile). A shell script as the MacOS
# executable requires no code signature and runs correctly after a single
# Gatekeeper approval — no "damaged" errors, no binary signing issues.
#
# What the generated .app does:
#   First launch  → opens Terminal, git-clones the repo to ~/FableGear,
#                   then hands off to launch.sh (which runs setup.sh if needed)
#   Every launch  → runs launch.sh directly (which does git pull + starts server)
#
# Usage:
#   bash build_release.sh              # builds FableGear.zip in the current dir
#   bash build_release.sh --release    # also creates a new GitHub release and uploads it

set -euo pipefail

REPO_URL="https://github.com/fabledharbinger0993/FableGear.git"
APP_NAME="FableGear.app"
ZIP_NAME="FableGear.zip"
BUILD_DIR="$(mktemp -d)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Parse flags ───────────────────────────────────────────────────────────────
DO_RELEASE=false
for arg in "$@"; do
  [[ "$arg" == "--release" ]] && DO_RELEASE=true
done

# ── Version from latest git tag ───────────────────────────────────────────────
VERSION="$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")"
echo "Building $APP_NAME  version $VERSION"

# ── Create .app bundle structure ──────────────────────────────────────────────
APP_PATH="$BUILD_DIR/$APP_NAME"
mkdir -p "$APP_PATH/Contents/MacOS"
mkdir -p "$APP_PATH/Contents/Resources"

# ── Write the launcher shell script ───────────────────────────────────────────
cat > "$APP_PATH/Contents/MacOS/FableGear" << 'LAUNCHER'
#!/bin/bash
# FableGear bootstrap launcher
# First run: clones the repo. Every run: hands off to launch.sh.
# Uses open -a Terminal (Launch Services) — no Automation permission required.

# ── Escape Rosetta ────────────────────────────────────────────────────────
# Launch Services can run this script-app translated (x86_64) — observed in
# the wild as a launcher stuck inside Rosetta runtime routines forever: the
# Dock icon bounces, launch.sh never runs, and no window ever appears. It
# also makes `uname -m` lie to launch.sh's arch check. If we're translated
# on Apple Silicon, re-exec natively before doing anything else.
if [ "$(/usr/sbin/sysctl -in sysctl.proc_translated 2>/dev/null)" = "1" ]; then
  exec /usr/bin/arch -arm64 /bin/bash "$0" "$@"
fi

INSTALL_DIR="$HOME/FableGear"
REPO_URL="https://github.com/fabledharbinger0993/FableGear.git"

if [ -d "$INSTALL_DIR/.git" ]; then
  # Detach: launch.sh can legitimately take minutes (dependency install,
  # release update) — running it synchronously keeps this process alive,
  # which macOS renders as the Dock icon bouncing the whole time. Hand off
  # and exit so the icon settles immediately; launch.sh owns the rest.
  nohup /bin/bash "$INSTALL_DIR/launch.sh" >/dev/null 2>&1 &
else
  SETUP_SCRIPT="$(mktemp /tmp/fablegear-install.XXXXXX.sh)"
  cat > "$SETUP_SCRIPT" << 'INNER'
#!/bin/bash
INSTALL_DIR="$HOME/FableGear"
REPO_URL="https://github.com/fabledharbinger0993/FableGear.git"
echo ""
echo "  Installing FableGear..."
echo ""
if [ -d "$INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR/.git" ]; then
  echo "  $INSTALL_DIR exists but is not a git repo — backing up to ${INSTALL_DIR}.bak"
  mv "$INSTALL_DIR" "${INSTALL_DIR}.bak"
fi
git clone "$REPO_URL" "$INSTALL_DIR" && bash "$INSTALL_DIR/launch.sh"
rm -f "$0"
INNER
  chmod +x "$SETUP_SCRIPT"
  open -a Terminal "$SETUP_SCRIPT"
fi
LAUNCHER
chmod +x "$APP_PATH/Contents/MacOS/FableGear"
echo "  ✓ Launcher script written"

# ── Write Info.plist ──────────────────────────────────────────────────────────
cat > "$APP_PATH/Contents/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>FableGear</string>
  <key>CFBundleIconFile</key>
  <string>applet</string>
  <key>CFBundleIdentifier</key>
  <string>com.fabledharbinger.fablegear</string>
  <key>CFBundleName</key>
  <string>FableGear</string>
  <key>CFBundleDisplayName</key>
  <string>FableGear</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>${VERSION#v}</string>
  <key>CFBundleVersion</key>
  <string>${VERSION#v}</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>LSArchitecturePriority</key>
  <array>
    <string>arm64</string>
    <string>x86_64</string>
  </array>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSAppleEventsUsageDescription</key>
  <string>FableGear uses Terminal to install dependencies and launch the server.</string>
</dict>
</plist>
PLIST
echo "  ✓ Info.plist written"

# ── Apply FableGear icon ───────────────────────────────────────────────────────
# Prefer new branded icon, fall back to legacy logo
ICON_SRC="$SCRIPT_DIR/static/icon-app-dock.png"
[[ ! -f "$ICON_SRC" ]] && ICON_SRC="$SCRIPT_DIR/static/RB_LOGO.png"

if [[ -f "$ICON_SRC" ]]; then
  ICONSET_DIR="$BUILD_DIR/fablegear.iconset"
  mkdir -p "$ICONSET_DIR"
  for size in 16 32 64 128 256 512; do
    sips -z $size $size "$ICON_SRC" --out "$ICONSET_DIR/icon_${size}x${size}.png" &>/dev/null
    double=$((size * 2))
    sips -z $double $double "$ICON_SRC" --out "$ICONSET_DIR/icon_${size}x${size}@2x.png" &>/dev/null
  done
  iconutil -c icns "$ICONSET_DIR" -o "$APP_PATH/Contents/Resources/applet.icns" 2>/dev/null \
    && echo "  ✓ Icon applied" \
    || echo "  ⚠ Icon conversion failed — app will use default icon"
fi

# ── Package into FableGear.zip ─────────────────────────────────────────────────
# Remove any existing archive first — `zip` UPDATES an existing file in place
# rather than replacing it, which can leak stale bundle contents into a release.
ZIP_PATH="$(pwd)/$ZIP_NAME"
rm -f "$ZIP_PATH"
(cd "$BUILD_DIR" && zip -qr "$ZIP_PATH" "$APP_NAME")
echo "  ✓ Packaged → $ZIP_PATH"
rm -rf "$BUILD_DIR"

# ── Optionally create a GitHub release ───────────────────────────────────────
if [[ "$DO_RELEASE" == true ]]; then
  echo ""
  echo "Creating GitHub release $VERSION …"

  RELEASE_NOTES="## Download

**↓ Click FableGear.zip below** — the two \"Source code\" files are auto-generated by GitHub and are not the app.

---

## Install

1. Download **FableGear.zip** above
2. Unzip — you get **FableGear.app**
3. Move it to your Desktop or Applications folder
4. Double-click to launch

> **First launch** opens a Terminal window and clones FableGear, then automatically installs everything needed — Homebrew, \`ffmpeg\`, \`chromaprint\`, and all Python packages. This runs once and takes a few minutes. FableGear opens in your browser when it's done.

> **Future launches** update FableGear automatically — no manual downloads needed.

## \"FableGear can't be opened\"?

This is macOS Gatekeeper — it blocks apps that aren't signed with an Apple Developer certificate. To allow it:

1. Go to **System Settings → Privacy & Security**
2. Scroll down — you'll see *\"FableGear was blocked from use\"*
3. Click **Open Anyway**

Alternatively, right-click the app → **Open** → **Open Anyway**."

  /opt/homebrew/bin/gh release create "$VERSION" "$ZIP_PATH" \
    --title "FableGear $VERSION" \
    --notes "$RELEASE_NOTES" \
    --latest 2>/dev/null \
    || /opt/homebrew/bin/gh release upload "$VERSION" "$ZIP_PATH" --clobber

  echo "  ✓ Release $VERSION published"
  echo "  → https://github.com/fabledharbinger0993/FableGear/releases/tag/$VERSION"
fi

echo ""
echo "Done. $ZIP_NAME is ready."
