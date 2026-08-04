# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

DJs who use Rekordbox as their primary library/performance tool and manage a personal collection of local audio files across one or more drives. They are hands-on with their own library (not a team or label context) and comfortable with desktop tools, but are not necessarily developers. They reach for FableGear when Rekordbox's own library tools (duplicate handling, path repair, tagging, organization) fall short at scale, or when they need to prep/repair a library before a gig.

## Product Purpose

FableGear is a local-first Rekordbox library toolkit for macOS. It gives DJs tools to clean up, repair, and manage their music library that go beyond what Rekordbox offers natively: auditing the database against the filesystem, fixing broken paths, deduping at both the database and file level, tagging, normalizing loudness, converting formats, and exporting to Pioneer USB drives. Success means a DJ can trust their library is accurate, playable, and not silently corrupted, without manual SQL or filesystem surgery.

## Positioning

Everything runs on the user's Mac: no cloud service, no account/login, no telemetry, no data leaves the machine. FableGear reads and writes the Rekordbox SQLite database and audio files directly. Its defining mechanism is treating "duplicate" as two distinct problems — physical file duplicates (filesystem is source of truth, acoustic-fingerprint based) vs. database record duplicates (Rekordbox DB is source of truth, playlist-safe record merging) — handled by two separate tools rather than one lossy pass, plus a safety layer (backups, dry-run/preview, Health Monitor pre-flight checks, required Rekordbox-closed state) built around the fact that a bad write can silently break a library.

## Operating Context

- macOS desktop app (Monterey 12.0+, Apple Silicon or Intel), packaged as a self-updating .app.
- Reads/writes the Rekordbox SQLite database directly (via pyrekordbox) and the user's audio files on local/external drives.
- Two workspaces: **Record Room** (database-layer tools — browse, audit, import, fix paths, link/manage playlists, consolidate duplicate DB records, Pioneer USB export) and **Chop Shop** (file-layer tools — tag, dedupe by acoustic fingerprint, rename, organize, normalize loudness, convert format, scan for novel tracks on other drives).
- **Pipeline Wizard** chains Chop Shop tools into one run (auto mode or confirm-between-steps).
- **Health Monitor** runs at startup/on demand checking for risky conditions (Rekordbox running, iCloud/Dropbox syncing the library, disk space, symlinked DB, etc.).
- **FableGo** is a mobile companion web UI reachable over local Wi-Fi (or Tailscale remotely) for browsing/playlist management/triggering jobs from a phone; requires the desktop app running.
- Rekordbox itself must be closed before any write operation; this is enforced, not just advised.

## Capabilities and Constraints

- Confirmed tool set: Library Browser + Player, Library Audit, Consolidate Duplicates, Import Tracks, Fix Broken Paths, Link Playlists, Playlist Management, Pioneer USB Export (Record Room); Tag Tracks, Find and Prune Duplicates, Rename Files, Organize Library, Normalize Loudness (EBU R128), Convert Format, Novelty Scanner (Chop Shop).
- Safety constraints are product-level requirements, not optional polish: explicit confirmation before writes, dry-run/preview where practical, automatic pre-write backups, and a rule that no database record is ever removed until every playlist referencing it has been re-wired.
- Terminology: "Record Room" = database-layer work; "Chop Shop" = file-layer work. This split is a deliberate architectural and UX boundary, not just a folder name — keep it consistent in UI copy and navigation.
- Open source (MIT), free, no account system — any future UI work should not assume or design toward login/paywall/account patterns.

## Brand Commitments

- Product name: FableGear. Built by Guthrie Entertainment LLC.
- Existing internal code nicknames for subsystems (seen in source comments, not necessarily user-facing): "The Media Pit" (player/library routes), "The Butcher Shop" (Chop Shop tool routes), "The Zombie Machine" (Rekordbox audit/import/link routes), "The Overlord" (mobile/FableGo routes). Treat these as backend-only flavor unless the user confirms they should surface in UI.
- Existing visual assets under `static/`: app icon, dock icon, per-tool icons (organizer, renamer, deduper, normalizer, converter, novelty, track-tagger, studio, drives, queue, settings, record-room, chop-shop), splash video. A per-tool icon system is already an established pattern to preserve/extend, not invent fresh.

## Evidence on Hand

- README.md documents the full tool set, safety model, install flow, and architecture in detail — treat it as authoritative product truth, not marketing copy to rewrite freely.
- No customer testimonials, case studies, press, or usage benchmarks exist. Do not fabricate any.
- No DESIGN.md yet — no recorded visual system. Existing implementation (templates/, static/fablegear.css, static/vendor, static/shared, static/record_room, static/chop_shop) is the current incumbent visual truth and should be treated as evidence, per Impeccable's document/new-work flow, not redesigned from this file alone.

## Product Principles

1. Local-first, zero-trust-of-the-cloud: no feature should require an account, a network call, or leaving the user's machine to work.
2. Never lose a track or a playlist link silently — every destructive or write path gets a dry-run/preview, a backup, and explicit confirmation.
3. Database truth and filesystem truth are kept conceptually separate (Record Room vs. Chop Shop); tools should not blur which source of truth they operate on.
4. Prefer showing the user what will happen before it happens over asking forgiveness after.
5. The app is free/open-source with no account system — design and copy should never imply gated features, upsells, or logins.

## Accessibility & Inclusion

No product-specific accessibility requirement has been established. Build to standard web accessibility baseline (keyboard operability, color contrast, semantic markup) rather than inventing a specific standard to target.
