# Docker Build Setup for FableGear

## Local Development Build

Build FableGear.app locally using Docker (useful for reproducible builds and CI testing):

```bash
bash docker-build.sh
```

This:
1. Builds a Docker image with Python 3.11, ffmpeg, chromaprint, and all dependencies
2. Runs PyInstaller inside the container
3. Outputs `dist/FableGear.app` on your host machine

**Requirements:**
- Docker installed
- ~2GB free disk space

**Output:**
- `dist/FableGear.app` — ready to distribute

To package for release:
```bash
cd dist && zip -r FableGear.zip FableGear.app
```

---

## Automated GitHub Actions Release

Push a version tag to trigger an automatic build and release:

```bash
git tag v1.0.8
git push origin v1.0.8
```

The workflow:
1. Triggers on any tag matching `v*.*.*`
2. Builds FableGear.app on a macOS runner
3. Creates a GitHub Release with the .app zip and install.sh
4. Auto-generates release notes

**Release notes are customizable** — edit `.github/workflows/build-release.yml` and update the `body:` section.

---

## How It Works

### Dockerfile.build

Builds a Python 3.11 image with:
- ffmpeg (audio processing)
- chromaprint (acoustic fingerprinting)
- PyInstaller (app bundling)
- All FableGear dependencies from requirements.txt

### docker-build.sh

Local build script that:
1. Builds the Docker image
2. Runs the container with `dist/` mounted
3. Outputs the final .app to your host filesystem

### .github/workflows/build-release.yml

GitHub Actions workflow that:
1. Listens for version tags (`v1.0.0`, etc.)
2. Checks out the code
3. Sets up Python 3.11 and Homebrew dependencies
4. Builds the .app and zips it
5. Creates a GitHub Release with the artifact

---

## Troubleshooting

**"Docker image build failed"**
- Ensure Docker is running: `docker ps`
- Check available disk space: `docker system df`

**"pyinstaller command not found"**
- The Dockerfile installs PyInstaller; if it's missing, rebuild the image: `docker build -f Dockerfile.build --no-cache -t fablegear-builder:latest .`

**"dist/FableGear.app not created"**
- Check Docker logs: `docker logs <container_id>`
- Verify all templates and static files are present in the repo root

**"GitHub Actions build failed"**
- Check the workflow logs in your GitHub repo: Actions tab
- Common issues: missing secrets, incorrect Python version, or Homebrew package unavailable
