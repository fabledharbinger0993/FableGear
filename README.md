# FableGear

**A local-first rekordbox toolkit for DJs who need safer cleanup, recovery, and library maintenance tools.**

<a href="https://github.com/fabledharbinger0993/FableGear/releases/latest/download/FableGear.zip">
  <img src="https://raw.githubusercontent.com/fabledharbinger0993/FableGear/main/static/icon-logo-fablegear.png" width="200" alt="Download FableGear">
</a>

macOS · Free · Open source · No account required · No subscription

---

## What is FableGear?

FableGear is a companion toolkit for rekordbox libraries. It helps you repair broken paths, audit large collections, find true duplicates, normalize files, tag tracks, reorganize folders, and manage playlists without turning your music library into a guessing game.

It is built for DJs with large or long-lived collections where the usual pain points start stacking up:

- drives that remount under a different name
- rekordbox entries pointing at missing files
- duplicates that slipped past filename-based checks
- tracks with missing BPM or key tags
- inconsistent file naming
- messy folder structures after years of imports, downloads, and migrations

FableGear keeps things local and keeps rekordbox as the source of truth. It opens in a native desktop window, runs on your machine, and does not depend on a cloud backend.

---

## Why use it?

FableGear is designed for maintenance work that is tedious, risky, or awkward inside rekordbox itself.

### Use FableGear when you need to:

- audit a library before a big cleanup
- repair path breakage after moving drives or folders
- detect duplicates by audio content instead of filename alone
- write BPM and musical key into actual file tags
- normalize loudness across tracks
- reorganize a collection into a cleaner folder structure
- batch-import music with previews and backups
- manage playlists and export to Pioneer USB media

---

## Safety first

FableGear is intentionally cautious.

- **Rekordbox must be closed before write operations**
- **Write actions require explicit confirmation**
- **Dry-run and preview flows are used where practical**
- **Backups are created before destructive database operations**
- **Health checks surface risky conditions before you proceed**

The goal is not just power — it is giving you safer ways to perform jobs that are easy to get wrong when done manually.

---

## What you can do with FableGear

FableGear is organized around two kinds of work: **Rekordbox DB** operations and **physical library** operations on your audio files.

### Rekordbox DB tools

| Tool | What it does |
|---|---|
| **Library Audit** | Cross-checks the rekordbox database against your actual drives to find broken paths, orphaned entries, missing files, and untagged tracks. |
| **Import** | Adds new audio files to the rekordbox database with dry-run support and automatic backup before writes. |
| **Fix Broken Paths** | Bulk-updates stored database paths when drives remount under a different name or files have moved. |
| **Link Playlists** | Maps your folder structure to rekordbox playlist names automatically after imports or reorganizations. |

### Physical library tools

| Tool | What it does |
|---|---|
| **Tag Tracks** | Analyzes audio and writes BPM and musical key into the file tags so metadata survives database rebuilds and works outside rekordbox. |
| **Find & Prune Duplicates** | Uses acoustic fingerprinting to detect the same recording even when filenames, formats, or bitrates differ. |
| **Rename Files** | Batch renames files using patterns or inferred rules from example pairs, with preview before execution. |
| **Organize Library** | Rebuilds folder structure from embedded metadata, using album artist intelligently to avoid messy feature-credit folder sprawl. |
| **Normalize Loudness** | Measures integrated loudness and re-encodes tracks outside your target level, with preview and backup behavior. |
| **Convert Format** | Re-encodes a folder of audio files into a target format before import. |
| **Novelty Scanner** | Scans another drive for tracks not already acoustically present in your main library. |

### Pipeline builder

Chain multiple tools into a single automated run.

You can either:

- run in **auto mode** for a straight-through workflow, or
- use **confirm between steps** to pause and review each stage

The step controls support:

- **↻ Re-do** — replay the same step
- **✓ Finish** — stop here
- **⏭ Skip** — skip this result and continue
- **⏹ Stop** — abort immediately

---

## Library health monitor

FableGear includes a startup and on-demand health scanner that checks for conditions likely to cause trouble before or during maintenance work.

It can detect issues such as:

- rekordbox being open during a write attempt
- iCloud or Dropbox sync activity on the library folder
- suspicious database size regression
- read-only volume mounts
- backups living on the same physical volume as the main database
- low free disk space on library drives
- database symlinks instead of real files

Findings are shown with severity levels, and safe auto-fixes are applied where appropriate.

---

## Library view and built-in player

FableGear also includes a library browser and audio player backed directly by the rekordbox database.

Features include:

- browsing tracks with BPM, key, duration, and file path
- split-view browsing of the filesystem alongside database tracks
- hotplug detection for connected drives
- playlist create / rename / delete / reorder flows
- audio playback with waveform display
- export to Pioneer USB in the expected directory format
- path integrity checking against what is actually on disk

---

## FableGo: mobile web companion

FableGo is the built-in mobile companion for FableGear.

It can connect over your local network, and optionally over Tailscale for remote access.

### What FableGo can do

- browse your music folders remotely
- trigger server-side downloads with live progress
- browse, create, edit, and delete rekordbox playlists
- add and remove tracks from playlists
- trigger BPM and key analysis jobs remotely
- browse connected drives and export playlists to Pioneer USB

FableGo uses bearer token authentication from your local config. The desktop app/server must be running for FableGo to work.

---

## Install

### Recommended: one-command install on macOS

Open Terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/fabledharbinger0993/FableGear/main/install.sh | bash
```

This bootstraps dependencies, clones the repo, and launches FableGear. On first run, a setup window walks through anything that still needs to be configured.

### Manual download

1. Click **Download FableGear** above
2. Unzip the archive
3. Open **FableGear.app**
4. If macOS blocks the first launch, right-click the app and choose **Open**

On first launch, FableGear can also offer to create a native Dock launcher locally on your Mac.

---

## Requirements

### Current practical target

The current install and launch flow is primarily geared toward **macOS**.

### Runtime requirements

- macOS Monterey 12.0 or later
- Apple Silicon or Intel Mac
- internet connection for first-time setup / dependency install
- rekordbox closed for any write operation

### System dependencies

FableGear relies on these system tools for core media workflows:

- `ffmpeg`
- `chromaprint` / `fpcalc`

Python dependencies are installed from `requirements.txt` and include:

- Flask + Waitress for the local app server
- pyrekordbox for rekordbox database access
- librosa for BPM and key analysis
- mutagen for tag reading/writing
- pyacoustid / Chromaprint for duplicate detection
- pyloudnorm for loudness measurement
- pywebview for the native desktop window

---

## Quick start

1. Install and launch FableGear
2. Let the first-run setup complete
3. Point it at your rekordbox database and music root
4. Start with **Library Audit** before changing anything
5. Review health warnings before running write operations
6. Use dry-run / preview flows whenever available

If you are working with a large or older library, the safest path is usually:

**Audit → fix paths → detect duplicates → tag / normalize → organize → import / relink playlists**

---

## Under the hood

| Library | Purpose |
|---|---|
| [pyrekordbox](https://github.com/dylanljones/pyrekordbox) | Direct read/write access to the rekordbox SQLite database |
| [librosa](https://librosa.org) | BPM detection, beat tracking, key detection, and chroma analysis |
| [Chromaprint / fpcalc](https://acoustid.org/chromaprint) | Acoustic fingerprinting for duplicate detection |
| [mutagen](https://mutagen.readthedocs.io) | Audio metadata reading and writing |
| [pyloudnorm](https://github.com/csteinmetz1/pyloudnorm) | EBU R128 integrated loudness measurement |
| [Flask](https://flask.palletsprojects.com) + [Waitress](https://docs.pylonsproject.org/projects/waitress) | Local application server |
| [pywebview](https://pywebview.flowrl.com) | Native desktop window wrapper |
| [flask-sock](https://flask-sock.readthedocs.io) | WebSocket support for live progress and companion-device events |

FableGear runs locally and uses a native window shell backed by a local Flask app. The desktop UI opens on your machine, while companion features are served by the same local app stack.

---

## Releasing

```bash
# Tag and publish a release
./scripts/release.sh v2.x.x

# Or with custom release notes
./scripts/release.sh v2.x.x .github/release-notes.md
```

GitHub Actions attaches `FableGear.zip` and `install.sh` to published releases automatically.

---

## Built by

**Guthrie Entertainment LLC** · Free and open source · [github.com/fabledharbinger0993](https://github.com/fabledharbinger0993)
