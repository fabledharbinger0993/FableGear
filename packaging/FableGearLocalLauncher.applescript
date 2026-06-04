-- FableGearLocalLauncher.applescript
-- Launches FABLEGEAR directly from FABLEGEAR_HOME or the standard install path.
-- No GitHub clone or git pull — safe to use during active development.
-- Swap back to FableGearLauncher.applescript for public releases.

do shell script "repo=${FABLEGEAR_HOME:-$HOME/FableGear/FableGear}; if [ ! -f \"$repo/launch_local.sh\" ]; then echo \"FableGear repo not found at $repo. Set FABLEGEAR_HOME to your checkout.\" >&2; exit 1; fi; bash \"$repo/launch_local.sh\" > /dev/null 2>&1 &"
