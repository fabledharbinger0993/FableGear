#!/bin/bash
# Local Docker build script for FableGear.app
# Usage: bash docker-build.sh
# Output: dist/FableGear.app

set -e

echo "Building FableGear.app in Docker…"
echo ""

# Build the Docker image
docker build -f Dockerfile.build -t fablegear-builder:latest .

# Run the container and mount dist/ to the host
docker run --rm \
  -v "$(pwd)/dist:/app/dist" \
  fablegear-builder:latest

echo ""
echo "✓ Done: dist/FableGear.app"
echo ""
echo "To package for distribution:"
echo "  cd dist && zip -r FableGear.zip FableGear.app"
