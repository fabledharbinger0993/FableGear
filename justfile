# FableGear justfile. Mirrors .github/workflows/ci.yml as of 2026-11-01.
# Usage: `just setup` once, then `just check` before every push.

default:
    @just --list

# Recreate venv/ on Python 3.13 with CI's dependencies
setup:
    uv venv venv --python 3.13 --clear
    VIRTUAL_ENV=$PWD/venv uv pip install -r fablegear/requirements.txt -r dev/requirements-dev.txt

# Everything CI gates on
check: lint typecheck test

lint:
    ruff check .

typecheck:
    pyright .

test:
    venv/bin/python -m pytest dev/tests -q
