#!/bin/bash
# FableGear launcher
# Run directly: bash launch.sh
# Or wrap in Automator > Application > Run Shell Script for a dock icon

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/venv"
SENTINEL="$SCRIPT_DIR/.fablegear_ready"
FAILED="$SCRIPT_DIR/.fablegear_failed"
LOG="$SCRIPT_DIR/fablegear.log"

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


# ── Homebrew update/upgrade (silent, non-blocking, scoped to FableGear deps) ─
if _brew --version &>/dev/null; then
  (_brew update >/dev/null 2>&1 && _brew upgrade ffmpeg chromaprint >/dev/null 2>&1) &
fi

# ── Activate venv ─────────────────────────────────────────────────────────
source "$VENV/bin/activate"

# ── Ensure core dependencies are installed ────────────────────────────────
# setup.sh handles first-run, but cloned repos or manual venv resets can
# leave the venv without packages. Reinstall BOTH the UI deps (Flask, Waitress,
# pywebview — without which no window ever appears) and the library deps.
# Quick no-op when everything is current.
pip install --quiet -r "$SCRIPT_DIR/requirements_ui.txt" >> "$LOG" 2>&1
pip install --quiet -r "$SCRIPT_DIR/requirements.txt" >> "$LOG" 2>&1
# Best-effort: a platform without an essentia wheel must not fail the launch.
# `|| true` keeps the non-zero exit from leaking out of this block regardless
# of how the caller invokes the script. Beat detection degrades to librosa and
# the Health panel reports it.
pip install --quiet -r "$SCRIPT_DIR/requirements_optional.txt" >> "$LOG" 2>&1 || true

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
    # Best-effort heads-up: an update can take minutes and everything below
    # is silenced, so without this the only user feedback is a missing window.
    osascript -e "display notification \"Updating to $LATEST_TAG — the window will open when ready\" with title \"FableGear\"" >/dev/null 2>&1 &
    if git merge-base --is-ancestor HEAD "$LATEST_TAG" 2>/dev/null; then
      echo "FableGear: updating ${CURRENT_TAG:-untagged} -> $LATEST_TAG" >> "$LOG"
      PREV_HEAD=$(git rev-parse HEAD)
      if git merge --ff-only "$LATEST_TAG" >> "$LOG" 2>&1; then
        if pip install --upgrade --quiet -r "$SCRIPT_DIR/requirements_ui.txt" >> "$LOG" 2>&1 \
          && pip install --upgrade --quiet -r "$SCRIPT_DIR/requirements.txt" >> "$LOG" 2>&1; then
          :
        else
          echo "FableGear: dependency install failed after update; rolling back" >> "$LOG"
          if git diff --quiet && git diff --cached --quiet; then
            git reset --hard "$PREV_HEAD" >> "$LOG" 2>&1
          else
            echo "FableGear: skipped rollback because local changes are present" >> "$LOG"
          fi
        fi
      else
        echo "FableGear: fast-forward to $LATEST_TAG failed; staying on ${CURRENT_TAG:-current}" >> "$LOG"
      fi
    elif git merge-base --is-ancestor "$LATEST_TAG" HEAD 2>/dev/null; then
      # HEAD is a DESCENDANT of the latest tag — i.e. ahead of the release
      # (a dev checkout, or a build not yet tagged). Never downgrade; leave it.
      echo "FableGear: HEAD is ahead of $LATEST_TAG; staying on current build" >> "$LOG"
    else
      # HEAD is neither an ancestor nor a descendant of the latest tag — the
      # clone has genuinely DIVERGED (left on an old branch, or files edited
      # outside git), so a fast-forward is impossible and the install would
      # otherwise be stuck on stale code forever. A release install (no .dev)
      # must track the release line, so realign hard to the tag. Any local
      # changes are stashed first (recoverable via `git stash list`), never
      # silently discarded.
      echo "FableGear: clone diverged from $LATEST_TAG; realigning to the release" >> "$LOG"
      if ! git diff --quiet || ! git diff --cached --quiet; then
        git stash push -u -m "fablegear-auto-$(git rev-parse --short HEAD)" >> "$LOG" 2>&1 || true
      fi
      if git reset --hard "$LATEST_TAG" >> "$LOG" 2>&1; then
        pip install --upgrade --quiet -r "$SCRIPT_DIR/requirements_ui.txt" >> "$LOG" 2>&1
        pip install --upgrade --quiet -r "$SCRIPT_DIR/requirements.txt" >> "$LOG" 2>&1
        echo "FableGear: realigned to $LATEST_TAG" >> "$LOG"
      else
        echo "FableGear: could not realign to $LATEST_TAG; staying on ${CURRENT_TAG:-current}" >> "$LOG"
      fi
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
