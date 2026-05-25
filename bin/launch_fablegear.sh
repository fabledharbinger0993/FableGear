#!/bin/zsh
# FableGear launcher for Automator or direct shell use
# Ensures correct directory and Python environment

# Absolute path to project root
dir="/Users/fabledharbingerFabledHarbinger/Desktop/FABLEDHARBINGER/GIT_REPOS/FableGear"
cd "$dir" || exit 1

# Optionally activate conda or venv if needed
# source "$dir/venv/bin/activate"  # Uncomment if using venv
# source /Users/fabledharbingerFabledHarbinger/miniconda3/etc/profile.d/conda.sh && conda activate base  # Uncomment if using conda

# Launch the app (choose one)
if [ -f "launch.sh" ]; then
  ./launch.sh
else
  python3 main.py
fi
