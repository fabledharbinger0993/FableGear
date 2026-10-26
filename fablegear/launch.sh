#!/bin/bash
# FableGear launcher
# Run directly: bash launch.sh
# Or wrap in Automator > Application > Run Shell Script for a dock icon

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# A source checkout keeps the app in <repo>/fablegear/ and its venv, sentinels and log at
# the repo root (where they always lived); a release install keeps them beside the code.
STATE_DIR="$SCRIPT_DIR"
[ -d "$SCRIPT_DIR/../.git" ] && STATE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$STATE_DIR/venv"
SENTINEL="$STATE_DIR/.fablegear_ready"
FAILED="$STATE_DIR/.fablegear_failed"
LOG="$STATE_DIR/fablegear.log"

# Finder-launched apps may not inherit Homebrew's usual PATH.
for p in /opt/homebrew/bin /usr/local/bin; do
  case ":$PATH:" in
    *":$p:"*) ;;
    *) [ -d "$p" ] && PATH="$p:$PATH" ;;
  esac
done
export PATH

# ── Single-launcher lock ──────────────────────────────────────────────────
# The .app hands off to this script detached, so an impatient double-click
# could start two launchers racing through pip installs and git updates at
# once. mkdir is atomic: second instance bails out silently. A lock older
# than 30 minutes is from a dead launcher — take it over.
LOCK_DIR="$SCRIPT_DIR/.launch_lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if [ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then
    rmdir "$LOCK_DIR" 2>/dev/null
    mkdir "$LOCK_DIR" 2>/dev/null || exit 0
  else
    exit 0
  fi
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

# ── Determine whether first-run setup is needed ───────────────────────────
_setup_needed() {
  command -v ffmpeg >/dev/null 2>&1 || return 0
  command -v fpcalc >/dev/null 2>&1 || return 0
  # Python venv missing or broken (hollow venv has no activate)?
  [ ! -d "$VENV" ] && return 0
  [ ! -f "$VENV/bin/activate" ] && return 0
  # Sentinel not yet written by setup.sh?
  [ ! -f "$SENTINEL" ] && return 0
  return 1
}

# ── First-run setup ───────────────────────────────────────────────────────
# Must happen before exec > /dev/null so Automator doesn't see it as an
# error, yet users still need a visible window for password prompts and
# progress. Solution: open a new Terminal window running setup.sh and poll
# for the sentinel file before proceeding.
if _setup_needed; then
  rm -f "$SENTINEL" "$FAILED"   # clear any stale sentinels from a prior run
  # open -a Terminal runs the script via Launch Services — no Automation
  # permission required (unlike osascript "tell application Terminal do script")
  open -a Terminal "$SCRIPT_DIR/setup.sh"
  # Wait for setup.sh to touch the ready sentinel (max 40 min, polls every 2 s).
  # setup.sh writes the FAILED sentinel on any error exit so we can bail
  # immediately instead of hanging out the full 40-minute timeout.
  _waited=0
  until [ -f "$SENTINEL" ]; do
    if [ -f "$FAILED" ]; then
      echo "FableGear: setup failed — see the setup window for details" >&2
      exit 1
    fi
    sleep 2
    _waited=$((_waited + 2))
    if [ $_waited -ge 2400 ]; then
      echo "FableGear: setup timed out — check the setup window for errors" >&2
      exit 1
    fi
  done
  unset _waited
fi

# ── Silence all output — Automator treats any stdout as an error ──────────
exec > /dev/null 2>&1


# ── Activate venv ─────────────────────────────────────────────────────────
source "$VENV/bin/activate"
# pip is always run as "$VENV/bin/python" -m pip below, never a bare `pip`: a bare
# one resolves through PATH and could install into the system or Homebrew Python.

# ── Ensure core dependencies are installed ────────────────────────────────
# setup.sh handles first-run, but cloned repos or manual venv resets can
# leave the venv without packages. Reinstall BOTH the UI deps (Flask, Waitress,
# pywebview — without which no window ever appears) and the library deps.
# Quick no-op when everything is current.
"$VENV/bin/python" -m pip install --quiet -r "$SCRIPT_DIR/requirements_ui.txt" >> "$LOG" 2>&1
"$VENV/bin/python" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt" >> "$LOG" 2>&1
# Best-effort: a platform without a wheel for an optional package must not
# fail the launch. `|| true` keeps the non-zero exit from leaking out of this
# block regardless of how the caller invokes the script.
"$VENV/bin/python" -m pip install --quiet -r "$SCRIPT_DIR/requirements_optional.txt" >> "$LOG" 2>&1 || true

# ── Updates are never applied here ────────────────────────────────────────
# This script used to fast-forward or hard-reset a source checkout to GitHub's
# "latest release" tag on every launch and then `pip install --upgrade` its
# requirements, with no signature check and no prompt. A compromised GitHub
# account or release tag would therefore have run attacker code on every
# user's machine the next time they opened the app.
#
# Updating is now always an explicit user action: the in-app banner
# (update_checker.py) announces a new release, the Update button
# (/api/update/apply) applies it, and an installed copy is updated by running
# a newer installer. Nothing below this line changes code on disk.
cd "$SCRIPT_DIR"

# ── Launch FableGear (arch-aware) ─────────────────────────────────────────
# Resolve once here so the running app can read it directly (helpers.py /
# state_tracker.py both check FABLEGEAR_VERSION first, falling back to a
# `git rev-parse` subprocess call per-request when it's unset).
if [ -f "$SCRIPT_DIR/VERSION" ]; then
  FABLEGEAR_VERSION="$(head -n1 "$SCRIPT_DIR/VERSION" | tr -d '[:space:]')"
else
  FABLEGEAR_VERSION="$(git describe --tags --abbrev=0 2>/dev/null || git rev-parse --short HEAD 2>/dev/null || echo "unknown")"
fi
export FABLEGEAR_VERSION

# Use 'arch -arm64' only on Apple Silicon; use plain Python on Intel Macs.
ARCH_NAME=$(uname -m)
if [ "$ARCH_NAME" = "arm64" ]; then
  nohup arch -arm64 "$VENV/bin/python" "$SCRIPT_DIR/main.py" >> "$LOG" 2>&1 &
else
  nohup "$VENV/bin/python" "$SCRIPT_DIR/main.py" >> "$LOG" 2>&1 &
fi

# ── Close Terminal window if launched interactively (not via Automator) ───
# Automator runs via do shell script (no TTY), so this block is skipped there.
# When run manually from Terminal, close the window so it doesn't linger.
if [ -t 0 ]; then
  osascript -e 'tell application "Terminal" to close front window' > /dev/null 2>&1 &
fi
