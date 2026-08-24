-- FableGearLauncher.applescript
-- Launches FABLEGEAR via launch.sh using FABLEGEAR_HOME or the standard install path.
-- Works from any .app location (~/Applications/, Dock, etc.)

do shell script "repo=${FABLEGEAR_HOME:-$HOME/FableGear}; if [ ! -f \"$repo/launch.sh\" ]; then echo \"FableGear repo not found at $repo. Set FABLEGEAR_HOME to your checkout.\" >&2; exit 1; fi; bash \"$repo/launch.sh\" > /dev/null 2>&1 &"
