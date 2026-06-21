#!/bin/bash
# FableGear launcher
# Run directly: bash launch.sh
# Or wrap in Automator > Application > Run Shell Script for a dock icon

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/venv"
SENTINEL="$SCRIPT_DIR/.fablegear_ready"
LOG="$SCRIPT_DIR/fablegear.log"

# ── Locate Homebrew (works on both Apple Silicon and Intel) ───────────────
_brew() {
  for p in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -f "$p" ] && { "$p" "$@"; return; }
  done
  return 1
}

# ── Determine whether first-run setup is needed ───────────────────────────
_setup_needed() {
  # Homebrew missing?
  _brew --version &>/dev/null || return 0
  # Required formulas missing?
  for formula in ffmpeg chromaprint; do
    _brew list --formula "$formula" &>/dev/null || return 0
  done
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
  rm -f "$SENTINEL"   # clear any stale sentinel
  # open -a Terminal runs the script via Launch Services — no Automation
  # permission required (unlike osascript "tell application Terminal do script")
  open -a Terminal "$SCRIPT_DIR/setup.sh"
  # Wait for setup.sh to touch the sentinel (max 40 min, polls every 2 s)
  _waited=0
  until [ -f "$SENTINEL" ]; do
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


# ── Homebrew update/upgrade (silent, non-blocking, scoped to FableGear deps) ─
if _brew --version &>/dev/null; then
  (_brew update >/dev/null 2>&1 && _brew upgrade ffmpeg chromaprint >/dev/null 2>&1) &
fi

# ── Activate venv ─────────────────────────────────────────────────────────
source "$VENV/bin/activate"

# ── Ensure core dependencies are installed ────────────────────────────────
# setup.sh handles first-run, but cloned repos or manual venv resets can
# leave the venv without packages. Quick no-op when everything is current.
pip install --quiet -r "$SCRIPT_DIR/requirements.txt" >> "$LOG" 2>&1

# ── Update to the latest RELEASE (skip in dev mode) ──────────────────────
# Release-gated: tracks GitHub's "latest release" tag, same endpoint the
# in-app update_checker.py uses, so the launcher and the UI always agree.
# Devs working from source opt out by touching a .dev sentinel in the repo root.
# Offline-safe: if the API is unreachable, the app launches on current code.
cd "$SCRIPT_DIR"
if [ ! -f "$SCRIPT_DIR/.dev" ]; then
  git fetch origin --tags --quiet >> "$LOG" 2>&1
  LATEST_TAG=$(curl -fsSL --max-time 10 \
    "https://api.github.com/repos/fabledharbinger0993/FableGear/releases/latest" 2>/dev/null \
    | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)
  CURRENT_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
  if [ -n "$LATEST_TAG" ] && [ "$LATEST_TAG" != "$CURRENT_TAG" ]; then
    if git merge-base --is-ancestor HEAD "$LATEST_TAG" 2>/dev/null; then
      echo "FableGear: updating ${CURRENT_TAG:-untagged} -> $LATEST_TAG" >> "$LOG"
      PREV_HEAD=$(git rev-parse HEAD)
      if git merge --ff-only "$LATEST_TAG" >> "$LOG" 2>&1; then
        if pip install --upgrade --quiet -r "$SCRIPT_DIR/requirements_ui.txt" >> "$LOG" 2>&1 \
          && pip install --upgrade --quiet -r "$SCRIPT_DIR/requirements.txt" >> "$LOG" 2>&1; then
          :
        else
          echo "FableGear: dependency install failed after update; rolling back" >> "$LOG"
          git reset --hard "$PREV_HEAD" >> "$LOG" 2>&1
        fi
      else
        echo "FableGear: fast-forward to $LATEST_TAG failed; staying on ${CURRENT_TAG:-current}" >> "$LOG"
      fi
    else
      echo "FableGear: release $LATEST_TAG is not a forward update from current HEAD; skipping" >> "$LOG"
    fi
  fi
fi

# ── Bring up Tailscale for FableGo remote access (best-effort, non-blocking) ─
# FableGear runs fully offline without this. Tailscale just enables the mobile web app
# to connect remotely. Silent on failure — missing Tailscale is not an error.
if command -v tailscale &>/dev/null; then
  tailscale up --accept-routes >> "$LOG" 2>&1 &
fi

# ── Launch FableGear (arch-aware) ─────────────────────────────────────────
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
