# Multi-Drive Library Architecture — Design Document

**Status:** Phase 0/1 design. Not yet implemented. Awaiting path selection.
**Author:** drafted with Claude, 2026-06-14.

This document exists *because* the change it describes is large and touches the
exact data that has burned this project before (which drive your library lives
on). It is deliberately a design pass, not code. Pick a path at the end, then
implementation proceeds in verified stages.

---

## 1. The problem

FableGear today assumes a **single root**. `config.py` bakes in three module
constants from one config file:

- `MUSIC_ROOT` — one music directory
- `LOCAL_DB` — one rekordbox database (the working/source DB)
- `DJMT_DB` — the device DB on the DJ drive

The library view (`/api/library/...`, `routes_player.py`) sources files from
that single `MUSIC_ROOT`. But the real library is spread across multiple
drives (Passport ~4700+ files, the Samsung T7 / CAMAGIG, DJMTGO, etc.). The
user must manually point `music_root` at one drive and is blind to the rest.

The warnings that started this (backups on the internal drive; archive on a
read-only volume) are downstream symptoms: FableGear has no concept of "which
drive is home," so its archive/backup logic has no principled place to live.

## 2. The vision (from the user, verbatim intent)

1. **Discover.** Scan every mounted drive, identify those containing
   music-type files, catalog them as candidate sources.
2. **Unify the view.** The Record Room "all music library" view lists *every*
   song file across *all* music drives — not one configured location.
3. **Per-drive, not deduplicated.** Every track is listed **once per drive it
   exists on**. The same song on Passport and the T7 appears twice. A clear
   **visual separator** marks the boundary as you scroll from one drive's
   tracks into the next. The view shows disk reality; it does not try to be
   clever about cross-drive duplicates. (Dedup remains a separate Chop Shop
   *tool*, never the view's job.)
4. **Elect a home.** FableGear measures which drive holds the largest library
   and proposes it as the **primary destination** — where the FableGear
   Archive folders live and update from.
5. **Finder-style placement.** A Finder-like picker (or hand-off to Finder)
   lets the user place/nest the archive folders exactly as desired.
6. **One anchor, three roles.** Whatever location wins as best-for-archive is
   *also* the recommended **main library database** location in the
   Organization tool — and that DB is the canonical source that builds
   playlists and writes to the rekordbox `master.db`.

The core insight in (6): **archive location, working-DB location, and
playlist-build source are the same decision.** Pick the home drive once; all
three anchor there together.

## 3. What already exists (don't rebuild these)

`routes_player.py :: api_library_fs_browse` already:
- enumerates `/Volumes/*`,
- counts audio files per drive (`audio_estimate`),
- reports total/free space per drive,
- detects which drives carry a `PIONEER/rekordbox/master.db` (`has_pioneer_db`),
- can recursively walk a drive collecting audio (with a hard cap).

`config.py` already has `ARCHIVE_ROOT` (nests inside the music drive) and an
`archive_mode` ("auto"/"none"/custom). The new model **extends** this; it does
not invent archiving from zero.

`AUDIO_EXTENSIONS` (config.py) is the authoritative music-file set:
`.mp3 .wav .aiff .aif .aifc .flac .m4a .m4p .mp4 .m4v .ogg .opus`.

**Gap to build:** the layer *above* these primitives — unify discovered drives
into one grouped view, elect a home, and anchor archive + working-DB +
playlist-source to that election.

## 4. The central design question

Two architecturally distinct ways to model "the library across drives." This
is the decision that everything else hangs on.

### Path A — Elected Home + Read-only Sources

One drive is the **home**: it holds the working library DB, the FableGear
Archive, and is the playlist-build source / rekordbox write target. Other
music drives are **read-only sources** — visible in the unified view, available
to copy/import *from*, but FableGear writes only to home.

- **Library view:** concatenates per-drive file listings (home first, then
  others), drive separators between groups. Matches the user's "every track
  per drive, with separators" requirement directly.
- **Playlist building:** unambiguous — always against the home DB.
- **Pros:** single clear write target (safest for a recovery-scarred library);
  minimal change to the playlist/rekordbox-writer code (it still has one DB);
  the "one anchor, three roles" insight falls out naturally.
- **Cons:** cross-drive operations (e.g. a playlist referencing tracks that
  live on a non-home drive) need a copy-to-home or path-reference policy.
- **Risk:** low. One writer, many readers.

### Path B — Virtual Unified Index

FableGear maintains its **own index** spanning all drives. The "library" is the
index, not any one drive's DB. Playlists are built against the index; on
export, the index resolves each track to whichever drive physically holds it.

- **Library view:** queries the index, grouped by drive for display.
- **Playlist building:** against the index; export resolves real paths.
- **Pros:** most flexible; a playlist can freely span drives without copying;
  closest to a "true" multi-drive library manager.
- **Cons:** a whole new index subsystem to build, sync, and keep honest as
  drives mount/unmount/change; two sources of truth (index vs. the actual
  rekordbox DBs) that can drift; far more code touching the playlist/writer
  path.
- **Risk:** high. An index that drifts from disk reality is exactly the class
  of bug (paths pointing at the wrong place) that has cost this project before.

### Path C — Anastomosis (the likely winner)

Elected-home as the **write/anchor model** (Path A's safety), plus a
**lightweight read-only catalog** of the other drives purely for the *view*
and for *copy-from* operations (a sliver of Path B's flexibility, with none of
its write-path risk). The catalog never writes, never builds playlists, never
becomes a second source of truth — it is display + import-source only.

- Playlist building and rekordbox writes: **always against the home DB.** One
  writer. (Path A's safety, fully preserved.)
- The unified view: home DB tracks + catalog of other drives, grouped with
  separators. (The user's exact view requirement.)
- Cross-drive: surfaced as "this track lives on <drive>, copy to home to
  include it" — explicit, never silent.

This keeps the dangerous part (what writes the rekordbox DB) simple and
single-target, while delivering the multi-drive *experience* the user wants.

**Recommendation: Path C.** It gives the user's full vision without putting the
playlist/rekordbox writer — the highest-stakes code — behind a
drift-prone index.

## 5. Home election logic

1. Scan all mounted volumes (reuse `api_library_fs_browse` enumeration).
2. For each, count audio files **depth-aware** (the shallow 3-level scan
   undercounts — CAMAGIG showed ~8 at depth 3 but its real library is deeper).
   Use a bounded full walk with a generous cap and a progress signal.
3. Rank by audio-file count (tiebreak: free space, then existing
   `has_pioneer_db`).
4. Propose the largest as home. **Never auto-commit** — propose in the wizard,
   user confirms or overrides with the Finder-style picker.
5. Stability caveat: surface mount stability if known (Passport has been
   flapping this session). A drive that keeps disconnecting is a poor home
   even if largest — warn, don't block.

## 6. The Finder-style placement step

Two implementation options:
- **Hand off to Finder** — simplest, native, zero custom UI: open the chosen
  parent in Finder and let the user create/arrange folders, then point
  FableGear at the result. Lowest effort, fully familiar.
- **In-app Finder-like picker** — the existing `api_library_fs_browse` already
  powers a directory browser; extend it into a "choose/create archive folder"
  modal. More work, but keeps the user in-app.

Recommendation: ship the **hand-off to Finder** first (it's nearly free and
unambiguous), add the in-app picker later if the hand-off feels clunky.

## 7. Config model changes

From three flat constants to a home-anchored structure. Sketch:

```
{
  "home_drive":        "/Volumes/Passport",        # elected, user-confirmed
  "local_db":          "<home>/.../master.db",     # working DB on/near home
  "device_db":         "...",                       # unchanged (DJ drive)
  "archive_root":      "<home>/FableGear Archive",  # anchored to home
  "backup_dir":        "<archive>/Savepoints",      # under archive, off internal
  "known_music_drives":["/Volumes/Passport", "/Volumes/CAMAGIG", ...],  # catalog
}
```

Migration: existing single-root configs map cleanly — current `music_root`
becomes the initial `home_drive` proposal; user re-confirms in the wizard.
`LOCAL_DB`/`MUSIC_ROOT` remain derivable so existing code keeps working during
the transition.

## 8. Wizard flow (new/changed steps)

1. **Scan drives** (progress UI while the depth-aware count runs).
2. **Review & elect home** — table of drives (name, audio count, free space,
   pioneer-db, stability), largest pre-selected, user can override.
3. **Place archive** — Finder hand-off (or in-app picker) to set/nest the
   FableGear Archive under home.
4. **Confirm anchor** — show the resulting local_db / archive / backup paths;
   "this is where playlists build from and back up to." Explicit consent.
5. Existing permission/dependency steps unchanged.

This is also reachable post-setup via `/onboarding?reconfigure=1` to re-elect
home (e.g. after adding a bigger drive).

## 9. Health-check implications

The two warnings that started this resolve naturally under Path C:
- "Backups on same volume as source DB" → backups now live under
  `archive_root` on the home drive, off the internal drive.
- "Archive on read-only volume" → the home-election step can refuse / warn on a
  read-only or unstable candidate, and the First-Aid-before-write tool (a
  separate, already-agreed feature) gates any remount.

## 10. Staged build plan (after path selection)

1. Depth-aware multi-drive scan + home-election logic (pure Python, testable
   in isolation, no UI). Tests pin the ranking.
2. Config model migration (single-root → home-anchored), backward-compatible.
3. Unified library view: per-drive grouping + separators (the visible payoff).
4. Wizard steps: scan → elect → place → confirm.
5. Re-anchor archive/backup/working-DB to elected home; health warnings clear.
6. (Separate, already-agreed) First-Aid-before-write disk tool.

Each stage: committed separately, verified, reviewed before the next.

## 11. Open decisions for the user

1. **Path A / B / C?** (Recommendation: C.)
2. **Finder hand-off vs in-app picker first?** (Recommendation: hand-off.)
3. **Cross-drive playlist policy** — when a playlist wants a track that lives
   on a non-home drive: copy-to-home (safe, uses space) vs reference-in-place
   (flexible, but the track's drive must be mounted at export). This can be
   deferred until stage 4, but it's the one genuinely hard sub-question.
