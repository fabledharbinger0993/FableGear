# Docker & Build Guide for FableGear

---

## Docker — Headless Server Mode

Docker runs FableGear as a **headless web server** — no native window, just Flask over HTTP. Open `http://localhost:5000` in any browser.

This is useful when:
- You're on **Windows or Linux** (no macOS pywebview available)
- You want FableGear running as a **persistent background service**
- You want a **reproducible environment** for backend testing across your machines (Windows, Mac Studio, Mac mini)

```bash
bash docker-build.sh
```

Mount a music folder:
```bash
bash docker-build.sh --music /Volumes/YourDrive
```

Force rebuild the image:
```bash
bash docker-build.sh --rebuild
```

**Requirements:** Docker installed, ~1.5GB free disk space.

> **Note:** Docker cannot build a macOS `.app` bundle — PyInstaller requires the target OS. For distributable builds, use the GitHub Actions workflow (push a version tag) or run `bash build_release.sh` on a Mac.

---

## Automated Release — GitHub Actions

Push a version tag to automatically build and publish a release:

```bash
git tag v1.0.8
git push origin v1.0.8
```

The `build-release.yml` workflow:
1. Triggers on any tag matching `v*.*.*`
2. Builds `FableGear.app` on a macOS runner using PyInstaller
3. Packages it as `FableGear.zip`
4. Creates a GitHub Release with the zip and `install.sh` attached
5. Generates release notes with install instructions

**Release notes are customizable** — edit `.github/workflows/build-release.yml` and update the `body:` section.

---

## Local macOS Build (PyInstaller)

Builds a fully self-contained `.app` with Python + all dependencies bundled — no Python or Homebrew needed on the user's machine.

```bash
bash build.sh
# Output: dist/FableGear.app
```

To package for distribution:
```bash
cd dist && zip -r FableGear.zip FableGear.app
```

---

## Local macOS Build (Shell Launcher — lightweight)

Produces a minimal `.app` (~100KB) that clones the repo and runs `launch.sh` on first open. Users need internet access on first launch; setup is automatic.

```bash
bash build_release.sh            # build only
bash build_release.sh --release  # build + publish GitHub release
```

---

## Which approach to use?

| Goal | Use |
|------|-----|
| Run on Windows or Linux | `bash docker-build.sh` |
| Give someone a self-contained Mac app | `bash build.sh` → `dist/FableGear.app` |
| Publish an official release | `git tag vX.Y.Z && git push origin vX.Y.Z` |
| Quick lightweight launcher | `bash build_release.sh` |

---

## Troubleshooting

**"Docker image build failed"**
- Ensure Docker is running: `docker ps`
- Check disk space: `docker system df`

**"FableGear server won't start in Docker"**
- Check logs: `docker logs fablegear-server`
- Verify port 5000 isn't already in use: `lsof -i :5000`

**"GitHub Actions build failed"**
- Check the workflow logs: GitHub repo → Actions tab
- Common issues: FableGear.spec referencing wrong paths, missing Homebrew packages
