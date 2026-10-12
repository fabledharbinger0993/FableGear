#!/bin/bash
# build_release.sh — builds the distributable FableGear.zip and uploads a new GitHub release.
#
# Uses a shell-script .app (not osacompile). A shell script as the MacOS
# executable requires no code signature and runs correctly after a single
# Gatekeeper approval — no "damaged" errors, no binary signing issues.
#
# What the zip contains: "Install FableGear.app", an installer that carries a snapshot of
# FableGear's code. Opening it runs the install wizard (private venv first, packages only
# there), copies FableGear to ~/Library/Application Support/FableGear/app and adds
# FableGear.app to /Applications (or ~/Applications). See packaging/installer_launcher.sh.
#
# Usage:
#   bash build_release.sh              # builds FableGear.zip in the current dir
#   bash build_release.sh --release    # also creates a new GitHub release and uploads it

set -euo pipefail

REPO_URL="https://github.com/fabledharbinger0993/FableGear.git"
APP_NAME="Install FableGear.app"
ZIP_NAME="FableGear.zip"
BUILD_DIR="$(mktemp -d)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Parse flags ───────────────────────────────────────────────────────────────
DO_RELEASE=false
for arg in "$@"; do
  [[ "$arg" == "--release" ]] && DO_RELEASE=true
done

# ── Version from latest git tag ───────────────────────────────────────────────
# Only vX.Y.Z tags count: the repo also carries non-release tags (an archival marker tag) that
# `git describe` would otherwise pick, stamping the wrong version and naming the release after it.
# git's --match is a glob (v[0-9]* would still accept v1-beta), so filter with an anchored regex.
VERSION="$(git -C "$SCRIPT_DIR" tag --merged HEAD --sort=-v:refname 2>/dev/null \
  | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1 || true)"
VERSION="${VERSION:-v0.0.0}"
echo "Building $APP_NAME  version $VERSION"

# ── Create the installer app ──────────────────────────────────────────────────
# The zip holds one app, "Install FableGear.app". It carries a snapshot of FableGear's code
# (committed HEAD only -- never uncommitted edits) and a launcher that runs the install wizard
# from it. The wizard builds FableGear's private environment, installs the packages there,
# copies the code to ~/Library/Application Support/FableGear/app and adds FableGear.app to
# /Applications. Nothing is cloned or downloaded for the code itself.
APP_PATH="$BUILD_DIR/$APP_NAME"
PAYLOAD="$APP_PATH/Contents/Resources/payload"
mkdir -p "$APP_PATH/Contents/MacOS" "$PAYLOAD"

if [ -n "$(git -C "$SCRIPT_DIR" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  echo "  ⚠ Uncommitted changes in the repo are NOT in this build (it packages committed HEAD)."
fi
COMMIT="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"

# What the installed app does not need: tests, docs, dev scripts, CI config, source-only extras.
# Git pathspec excludes are exact repo-root paths, unlike tar patterns, which match a name at any depth
# and would silently strip any future static/docs or library/scripts folder the app needs.
EXCLUDES=(tests docs scripts .github fg-logo.png FableGear.spec FableGear_win.spec requirements-dev.txt
          requirements_build.txt MARKETING.md IMPLEMENTATION_COMPLETE.md DESIGN.md PRODUCT.md install.sh
          start_remote.sh build_release.sh ruff.toml pyrightconfig.json)
PATHSPEC=(.)
for ex in "${EXCLUDES[@]}"; do PATHSPEC+=(":(exclude)$ex"); done
git -C "$SCRIPT_DIR" archive HEAD -- "${PATHSPEC[@]}" | tar -x -C "$PAYLOAD"

# Stamp the build. A release gets the clean tag; a local build says so, so it can never be
# mistaken for the published release.
if [[ "$DO_RELEASE" == true ]]; then
  echo "$VERSION" > "$PAYLOAD/VERSION"
else
  echo "${VERSION}+g${COMMIT}-local" > "$PAYLOAD/VERSION"
fi
PAYLOAD_MB="$(du -sm "$PAYLOAD" | cut -f1)"
echo "  ✓ Payload: committed HEAD ${COMMIT}, ${PAYLOAD_MB} MB"

cp "$SCRIPT_DIR/packaging/installer_launcher.sh" "$APP_PATH/Contents/MacOS/Install FableGear"
chmod +x "$APP_PATH/Contents/MacOS/Install FableGear"
echo "  ✓ Launcher script written"

# ── Write Info.plist ──────────────────────────────────────────────────────────
cat > "$APP_PATH/Contents/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>Install FableGear</string>
  <key>CFBundleIconFile</key>
  <string>applet</string>
  <key>CFBundleIdentifier</key>
  <string>com.fabledharbinger.fablegear.installer</string>
  <key>CFBundleName</key>
  <string>Install FableGear</string>
  <key>CFBundleDisplayName</key>
  <string>Install FableGear</string>
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

1. Download **FableGear.zip** above and unzip it — you get **Install FableGear.app**
2. Open **Install FableGear** (macOS will ask you to approve it once — see below)
3. Follow the installer window. It checks your Mac, builds FableGear's own private Python environment, installs FableGear's packages there, and adds **FableGear** to your Applications folder
4. Open **FableGear** from Applications. The intro plays, then a short setup asks for the access it needs
5. You can delete the installer afterwards

> Before you start, install Python 3.11–3.13, \`ffmpeg\`, and \`fpcalc\` (Chromaprint) yourself. FableGear never installs or updates Homebrew or system tools — if one is missing the installer tells you exactly what to get and waits. Everything FableGear installs lives in its own folder (\`~/Library/Application Support/FableGear\`) and its private environment.

> **Updating:** download the newest FableGear.zip and run its installer again. It replaces FableGear's program files and keeps your library, settings and environment.

## \"FableGear can't be opened\"?

This is macOS Gatekeeper — it blocks apps that aren't signed with an Apple Developer certificate. To allow it, once:

1. Double-click Install FableGear.app, then click **Done** on the warning
2. Apple menu → **System Settings → Privacy & Security** (scroll the sidebar if needed)
3. In the **Security** section, click **Open Anyway** next to the Install FableGear message
4. Enter your Mac's login password

The button stays for about an hour after your attempt; if it's gone, double-click the app again. FableGear may also ask for access to your music folders and external drives (**Privacy & Security → Files & Folders**) and, for your Rekordbox library, **Full Disk Access**. Full walkthrough: https://github.com/fabledharbinger0993/FableGear#first-launch-on-a-mac-two-things-macos-will-ask-you-for"

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
