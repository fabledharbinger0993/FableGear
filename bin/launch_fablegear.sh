#!/usr/bin/env bash
# FableGear launcher for Automator or direct shell use
# Ensures correct directory and Python environment

# Project root is one level above this script's directory.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(dirname "$script_dir")"
cd "$repo_dir" || exit 1

# Optionally activate conda or venv if needed
# source "$repo_dir/venv/bin/activate"  # Uncomment if using venv
# source "$HOME/miniconda3/etc/profile.d/conda.sh" && conda activate base  # Uncomment if using conda

# Launch the app (choose one)
if [ -f "launch.sh" ]; then
  ./launch.sh
else
  python3 main.py
fi
