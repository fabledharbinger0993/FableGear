#!/bin/bash
# docker-build.sh — build and run FableGear as a headless web server via Docker
#
# This runs FableGear without pywebview (no native window) — the UI is
# accessible at http://localhost:5000 in any browser on your local machine.
#
# Useful when:
#   - You're on a machine without macOS (Windows, Linux)
#   - You want to run FableGear as a persistent background server
#   - You want a reproducible environment for backend testing
#
# For building a distributable macOS .app, push a version tag instead:
#   git tag v1.0.x && git push origin v1.0.x
#
# Usage:
#   bash docker-build.sh                         # build image + start server
#   bash docker-build.sh --music /Volumes/Drive  # mount a specific music folder
#   bash docker-build.sh --rebuild               # force rebuild image first

set -e

MUSIC_PATH=""
REBUILD=false

for arg in "$@"; do
  case "$arg" in
    --rebuild) REBUILD=true ;;
    --music)   shift; MUSIC_PATH="$1" ;;
  esac
  shift 2>/dev/null || true
done

echo "FableGear — Docker headless server"
echo ""

if [[ "$REBUILD" == true ]] || ! docker image inspect fablegear:latest &>/dev/null 2>&1; then
  echo "Building Docker image…"
  docker build -f Dockerfile.build -t fablegear:latest .
  echo ""
fi

MOUNT_ARGS=""
if [[ -n "$MUSIC_PATH" ]]; then
  echo "Mounting music folder: $MUSIC_PATH → /music"
  MOUNT_ARGS="-v ${MUSIC_PATH}:/music"
fi

echo "Starting FableGear server at http://localhost:5000"
echo "Press Ctrl+C to stop."
echo ""

docker run --rm \
  -p 5000:5000 \
  $MOUNT_ARGS \
  --name fablegear-server \
  fablegear:latest
