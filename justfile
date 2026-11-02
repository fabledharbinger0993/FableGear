# FableGear justfile. Mirrors .github/workflows/ci.yml as of 2026-11-01.
# Usage: `just setup` once, then `just check` before every push.
#
# setup, check, test and typecheck share one lock (macOS `lockf`), so rebuilding
# venv/ never runs while another recipe is using it.

lock := `git rev-parse --git-common-dir` / "fablegear-venv.lock"

default:
    @just --list

# Recreate venv/ on Python 3.13 with CI's dependencies (waits for a running check)
setup:
    @just _locked _setup

# Everything CI gates on (waits for a running setup)
check:
    @just _locked _check

lint:
    ruff check .

typecheck:
    @just _locked _typecheck

test:
    @just _locked _test

# --- internals ---------------------------------------------------------------

_locked recipe:
    #!/usr/bin/env bash
    set -euo pipefail
    if command -v lockf >/dev/null; then
      exec lockf -k "{{lock}}" "{{just_executable()}}" --justfile "{{justfile()}}" {{recipe}}
    fi
    echo "warning: lockf not found; running {{recipe}} without the venv lock" >&2
    exec "{{just_executable()}}" --justfile "{{justfile()}}" {{recipe}}

_setup:
    uv venv venv --python 3.13 --clear
    VIRTUAL_ENV="$PWD/venv" uv pip install -r fablegear/requirements.txt -r dev/requirements-dev.txt

_check: lint _typecheck _test

_typecheck:
    pyright .

# config.py loads ~/.fablegear/config.json at import time and raises if it's missing,
# so tests run against a throwaway HOME holding the same stub CI writes
# (ci.yml, "Create stub FableGear config"). The suite never depends on the
# developer's real config, and works on a machine that has none.
_test: _require-tools
    #!/usr/bin/env bash
    set -euo pipefail
    home="$(mktemp -d)"
    trap 'rm -rf "$home"' EXIT
    mkdir -p "$home/.fablegear"
    cat > "$home/.fablegear/config.json" <<'EOF'
    {
      "local_db": "/tmp/fablegear-ci/local_master.db",
      "device_db": "/tmp/fablegear-ci/device_master.db",
      "music_root": "/tmp/fablegear-ci/Music",
      "backup_dir": "/tmp/fablegear-ci/backups"
    }
    EOF
    HOME="$home" venv/bin/python -m pytest dev/tests -q

# Tests skip themselves when ffmpeg or node is missing. CI has both, so a local
# run without them would pass on less coverage than CI. Fail instead.
_require-tools:
    #!/usr/bin/env bash
    missing=()
    for tool in ffmpeg node; do
      command -v "$tool" >/dev/null || missing+=("$tool")
    done
    if [ ${#missing[@]} -gt 0 ]; then
      echo "just check needs: ${missing[*]} (tests skip without them; CI has both)" >&2
      exit 1
    fi
