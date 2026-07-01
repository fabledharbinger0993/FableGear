#!/bin/bash
# shellcheck shell=bash
# FableGear installer
#
# One-command install — paste this in Terminal:
#   curl -fsSL https://raw.githubusercontent.com/fabledharbinger0993/FableGear/main/install.sh | bash
#
# What it does:
#   1. Clones FableGear to ~/FableGear  (or updates if already installed)
#   2. Hands off to launch.sh which handles dependencies, venv, and first launch
#   3. On first launch, offers to add FableGear to your Dock natively

set -euo pipefail

REPO_URL="https://github.com/fabledharbinger0993/FableGear.git"
INSTALL_DIR="$HOME/FableGear"
FRESH_INSTALL=0

# ── Colour output helpers ──────────────────────────────────────────────────
_green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
_blue()  { printf '\033[0;34m%s\033[0m\n' "$*"; }
_red()   { printf '\033[0;31m%s\033[0m\n' "$*"; }

_green "──────────────────────────────────────────"
_green "  FableGear Installer"
_green "──────────────────────────────────────────"

# ── Require git ───────────────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
    _red "Git is required."
    echo "Install Git first, then re-run. On macOS, use Homebrew or the Git installer:"
    echo "  brew install git"
    exit 1
fi

# ── Clone or update ───────────────────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
    _blue "FableGear already installed — updating..."
    git -C "$INSTALL_DIR" fetch origin --tags --quiet
    # A plain --ff-only pull hard-fails (and aborts this script under set -e)
    # if the existing clone has diverged — e.g. it was left on an old branch
    # or files were edited outside git. Recover by realigning to origin/main,
    # stashing any local changes first so nothing is silently discarded.
    if ! git -C "$INSTALL_DIR" pull --ff-only origin main; then
        _blue "Clone has diverged — realigning to origin/main (local changes stashed)..."
        if ! git -C "$INSTALL_DIR" diff --quiet || ! git -C "$INSTALL_DIR" diff --cached --quiet; then
            git -C "$INSTALL_DIR" stash push -u -m "fablegear-install-$(git -C "$INSTALL_DIR" rev-parse --short HEAD)" || true
        fi
        git -C "$INSTALL_DIR" checkout main 2>/dev/null || git -C "$INSTALL_DIR" checkout -B main origin/main
        git -C "$INSTALL_DIR" reset --hard origin/main
    fi
else
    _blue "Cloning FableGear to $INSTALL_DIR ..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    FRESH_INSTALL=1
fi

# ── Fresh install onboarding gate ────────────────────────────────────────
# Reset setup state on brand-new installs so first app open always runs
# the onboarding walkthrough (permissions + library/file-location setup).
if [ "$FRESH_INSTALL" -eq 1 ]; then
    mkdir -p "$HOME/.fablegear"
    cat > "$HOME/.fablegear/fablegear-state.json" <<'EOF'
{
  "setup_complete": false,
  "db_read": null,
  "db_write": null,
  "drive_scan": false
}
EOF
fi

# ── First-run setup (run inline here so the user sees progress) ──────────
# launch.sh normally opens setup.sh in a new Terminal window, but that
# requires macOS Automation permission for Terminal — which silently fails
# on a fresh machine, hanging the poll loop forever.  When installing via
# this script we already have a visible Terminal, so run it here directly.
if [ ! -f "$INSTALL_DIR/.fablegear_ready" ]; then
    _blue "Running first-time setup (installs Homebrew, ffmpeg, Python packages)..."
    _blue "You may be prompted for your Mac password."
    echo ""
    bash "$INSTALL_DIR/setup.sh"
fi

# ── Launch ────────────────────────────────────────────────────────────────
_green "Starting FableGear..."
echo ""
bash "$INSTALL_DIR/launch.sh"
