#!/bin/bash
# FableGear installer app -- the executable inside "Install FableGear.app".
#
# build_release.sh copies this file to Contents/MacOS/ and puts FableGear's code in
# Contents/Resources/payload/. Double-clicking the app runs the install wizard from that
# payload, which builds a private environment and copies FableGear to
# ~/Library/Application Support/FableGear/app, then adds FableGear.app to /Applications
# (or ~/Applications).
#
# Contract: this script installs nothing and changes nothing. It only finds a Python to run
# the wizard with. The wizard itself is stdlib-only, so any Python 3.9+ can run it, but
# FableGear needs 3.11-3.13, so we prefer one of those and the wizard tells the user if
# none exists.

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PAYLOAD="$HERE/Resources/payload"
TARGET="${FABLEGEAR_INSTALL_TARGET:-$HOME/Library/Application Support/FableGear/app}"
LOG_DIR="$HOME/Library/Logs"
LOG="$LOG_DIR/FableGear-installer.log"
mkdir -p "$LOG_DIR" 2>/dev/null

# Launch Services can run script apps translated on Apple Silicon; re-exec natively.
if [ "$(/usr/sbin/sysctl -in sysctl.proc_translated 2>/dev/null)" = "1" ]; then
  exec /usr/bin/arch -arm64 /bin/bash "$0" "$@"
fi

dialog() {  # dialog <message> -- plain alert; best effort
  # FABLEGEAR_NO_DIALOG: test hook -- an alert would block a headless run waiting for a click.
  if [ -n "$FABLEGEAR_NO_DIALOG" ]; then echo "$1" >&2; return; fi
  /usr/bin/osascript -e "display dialog \"$1\" buttons {\"OK\"} default button \"OK\" with icon caution with title \"FableGear Installer\"" >/dev/null 2>&1
}

if [ ! -f "$PAYLOAD/installer_wizard.py" ]; then
  dialog "This installer is incomplete (its files are missing). Download FableGear again."
  exit 1
fi

# Finder-launched apps don't inherit the usual PATH. Append, so a PATH the caller set still wins.
for p in /opt/homebrew/bin /usr/local/bin /opt/local/bin; do
  case ":$PATH:" in *":$p:"*) ;; *) [ -d "$p" ] && PATH="$PATH:$p" ;; esac
done
export PATH

_minor() {  # _minor <python> -> "11" for 3.11.x, empty if not python3
  "$1" -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null
}

PY=""
# Prefer a supported Python (3.11-3.13) so the wizard's own checks pass.
# FABLEGEAR_PYTHON_CANDIDATES: test hook that replaces this list.
CANDIDATES="${FABLEGEAR_PYTHON_CANDIDATES:-/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 python3.13 python3.12 python3.11 python3}"
for cand in $CANDIDATES; do
  exe="$(command -v "$cand" 2>/dev/null)"
  [ -n "$exe" ] || continue
  v="$(_minor "$exe")"
  if [ -n "$v" ] && [ "$v" -ge 311 ] && [ "$v" -le 313 ]; then PY="$exe"; break; fi
done

# Otherwise any working Python 3.9+ can still run the wizard (it will explain what to install).
# /usr/bin/python3 is only a shim on a Mac without the Command Line Tools -- running it would
# pop up Apple's developer-tools installer, which is a system change we promised not to make.
if [ -z "$PY" ] && [ -z "$FABLEGEAR_PYTHON_CANDIDATES" ] && /usr/bin/xcode-select -p >/dev/null 2>&1 && [ -x /usr/bin/python3 ]; then
  v="$(_minor /usr/bin/python3)"
  [ -n "$v" ] && [ "$v" -ge 309 ] && PY=/usr/bin/python3
fi

if [ -z "$PY" ] && [ -n "$FABLEGEAR_NO_DIALOG" ]; then
  echo "no supported Python found" >&2
  exit 1
fi

if [ -z "$PY" ]; then
  /usr/bin/osascript >/dev/null 2>&1 <<'APPLESCRIPT'
set r to button returned of (display dialog "FableGear needs Python 3.11, 3.12 or 3.13, and this Mac doesn't have one yet.

Install it from python.org (the macOS installer is fine), then open this installer again. The installer never installs system software for you." buttons {"Cancel", "Open python.org"} default button "Open python.org" with icon caution with title "FableGear Installer")
if r is "Open python.org" then open location "https://www.python.org/downloads/macos/"
APPLESCRIPT
  exit 1
fi

{
  echo "=== $(date) installer start: python=$PY target=$TARGET"
} >> "$LOG"
exec "$PY" "$PAYLOAD/installer_wizard.py" --target "$TARGET" >> "$LOG" 2>&1
