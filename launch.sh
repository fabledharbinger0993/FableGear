#!/bin/bash
# Compatibility shim for source checkouts made before v1.2.1: FableGear's code moved into
# fablegear/, but FableGear.app launchers created earlier still run <repo>/launch.sh.
# This file is NOT shipped in the installer (the payload is fablegear/ only).
exec bash "$(cd "$(dirname "$0")" && pwd)/fablegear/launch.sh" "$@"
