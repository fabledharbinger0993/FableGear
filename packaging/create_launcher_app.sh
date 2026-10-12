#!/bin/bash
# FableGear — create ~/Applications/FableGear.app
#
# Shared by setup.sh (Terminal install) and installer_wizard.py (GUI install) so the
# two can't drift apart. Usage: create_launcher_app.sh <path-to-FableGear-folder>
# Safe to re-run. Always exits 0: a missing launcher is an inconvenience, not an install
# failure, and the output already tells the user to run launch.sh directly.

SCRIPT_DIR="${1:?usage: create_launcher_app.sh <FableGear folder>}"

ok()   { echo "  ✓  $1"; }
info() { echo "     $1"; }

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

exit 0
