# Archive-First Architecture — Design Document

**Status:** Design pass. Direction chosen; not yet implemented.
**Author:** drafted with Claude, 2026-07-25.
**Origin:** During a live import debug the app was observed touching the desktop
Rekordbox library during what should have been archive/library work, and the
setup wizard was found to be Rekordbox-first. This documents the intended
inversion: **FableGear's archive is the foundation; Rekordbox is downstream.**

---

## 1. The principle

FableGear's **archive is the top-priority foundation** — the single home for:

- the app's **library database** (the SQLite source of truth),
- **pyrekordbox** and the rest of the toolkit's working data,
- **every report**,
- **all undo capacity** (savepoints + the operation journal).

The setup wizard **builds the archive first**. Only *after* the archive and our
database exist does FableGear touch Rekordbox at all — and then only to (a)
offer a backup of the Rekordbox library *into our archive*, and (b) run the
Rekordbox-centered tools (path healer, duplicate finder, relocate, dead-files,
sync) that legitimately operate on Rekordbox.

Setup and everyday "open" **never silently write the live desktop Rekordbox
database.** Writing Rekordbox is always an explicit, user-initiated delivery
step, never a side effect of building or viewing our library.

## 2. Current state (audit)

| Concern | Archive-first target | Today (v1.1.25) | |
|---|---|---|---|
| Archive dir tree | Built first | Built on startup / config-save (`ensure_archive_structure`) | ✅ exists |
| Reports | In the archive | `ARCHIVE_ROOT/Reports` | ✅ |
| Savepoints (pre-write DB backups) | In the archive | `ARCHIVE_ROOT/Savepoints` | ✅ |
| Rekordbox backup | Offered, stored in archive | `snapshot_include_master_db` → archive snapshots | ✅ mechanism exists |
| **App library DB** (`fablegear.db`) | Archive is its home | `~/.fablegear/fablegear.db` (home dir only) | ❌ |
| **Undo journal** (`fg_processing_log`) | In the archive | inside the home-dir DB | ❌ (follows the DB) |
| **Wizard order** | FableGear first | Rekordbox scan + read/write perms are steps 3–5; "Build the FableGear library" is step 7 | ❌ inverted |

Two real divergences to fix: **wizard order** (§4) and **DB home** (§3).

## 3. Database home: archive is source of truth, working copy is local

**Decision (chosen):** the **archive holds the authoritative `fablegear.db`**;
the app runs against a **local working copy** in `~/.fablegear` and syncs it
back to the archive on checkpoints and clean shutdown. "Home" means *source of
truth and backup location* — not the file the live WAL writes to.

**Why not run the live DB directly on the drive:** the archive lives on a
removable, frequently-exFAT USB drive. SQLite in WAL mode on exFAT has broken
file locking and can corrupt the WAL on eject/unmount, and the drive is not
always mounted. Running the live database there risks exactly the corruption
that has cost this project before. The working-copy model gets the archive its
authoritative role without ever putting a live WAL on exFAT.

### 3.1 Layout

```
<ARCHIVE_ROOT>/
  Database/
    fablegear.db            ← AUTHORITATIVE copy (source of truth, backed up)
    fablegear.db.meta.json  ← last-sync timestamp, app version, checksum
  Savepoints/  Quarantine/  Reports/  Logs/   (unchanged)

~/.fablegear/
  fablegear.db              ← LOCAL WORKING COPY (live WAL runs here, fast+safe)
  working.meta.json         ← which archive it mirrors, dirty flag
```

### 3.2 Lifecycle (conservative / secure variant)

Guiding rule: **a healthy live DB is never automatically overwritten.** The
archive is an add-only backup and durable home; the live working copy is the
write target; restore-from-archive is only for a missing/corrupt local or an
explicit user action.

- **Startup / open:**
  1. The app always opens the **local** working copy for live reads/writes.
  2. If the local working copy is **missing or fails an integrity check** and
     the archive drive is mounted with a good `Database/fablegear.db` →
     **restore** from the archive (fresh machine, lost `~/.fablegear`, corrupt
     local). Before restoring, any existing (even damaged) local file is moved
     aside to a timestamped Savepoint, never deleted.
  3. If the local working copy is **healthy** → use it as-is. The archive is
     **not** read over it, even if the archive copy looks newer — differences
     are reconciled by the next backup (local → archive), not by clobbering the
     live DB. (Multi-machine drift is surfaced, not auto-resolved; see §9 Q3.)
  4. If the drive is not mounted → run on the local working copy, surface
     "working offline; will back up when <drive> returns". Never block startup.
- **Checkpoint / clean shutdown / after each tool run — back up local → archive:**
  1. **Verify the source first:** run `PRAGMA integrity_check` (or
     `quick_check`) on the live working DB. If it fails, **abort the backup** and
     alert — a corrupt live DB must never be promoted into a backup, and the
     existing good backups are left untouched.
  2. Write the verified source to a temp file on the archive filesystem,
     `fsync`, and confirm its checksum matches the source.
  3. Rotate generations (see below) — **add-only**, so a prior good backup is
     never destroyed by a new one.
  4. Atomically rename the verified temp file to `Database/fablegear.db` and
     update `meta.json` (timestamp, app version, checksum).
- **Two-generation guarantee (N and N-1):** the archive always retains at least
  **two verified-good generations** — the latest and the one behind it —
  `Database/fablegear.db` plus `Database/fablegear.prev.db`. The previous
  generation is only replaced *after* a new verified copy has fully, atomically
  become the latest. So even if corruption somehow slips past the integrity
  check and lands as "latest," there is always a slightly older known-good copy
  to fall back on or rebuild from. (Longer `Database/history/` retention beyond
  the guaranteed two is optional and pruned by count/age, never below two.)
- **Eject safety:** every write to the archive is temp-file → fsync → verify →
  atomic rename. A drive yanked at any instant leaves the previous authoritative
  copy fully intact; there is no window where the live file is half-written.

### 3.4 Why this is strictly safer than today

Today the library DB exists **only** at `~/.fablegear/fablegear.db` — one copy,
no backup anywhere (the archive backs up the *Rekordbox* DB and holds reports,
but not FableGear's own library database). A lost or corrupt `~/.fablegear`
means total loss of the library DB, cues, and undo history.

The model above adds a second physical copy (on the archive drive), keeps all
live WAL writes on the safe internal filesystem (never exFAT), writes the
archive copy only via atomic verified renames, keeps add-only history so a bad
copy can't destroy a good one, and never auto-overwrites a healthy live DB.
Every failure mode that exists today is reduced; the new code paths are
overwrite-safe by construction.

### 3.3 What moves, what stays

- `fablegear_database.database.DEFAULT_DB_PATH` stays the **working-copy** path
  (`~/.fablegear/fablegear.db`) — the live engine keeps opening a local file.
- New: an `archive_db_path()` (derived from `ARCHIVE_ROOT`) and a small
  `sync_db_to_archive()` / `hydrate_working_copy_from_archive()` pair, called at
  the lifecycle points above.
- Undo journal (`fg_processing_log`) and cues/beatgrids live inside
  `fablegear.db`, so they travel with it automatically — no separate move.
- Reports and Savepoints already live in the archive; unchanged.

## 4. Wizard reorder (FableGear-first)

Current: `Dependencies → App install → Scan Rekordbox → Read-RB perm →
Write-RB perm → AI → Build FableGear library → Room`.

Target:

1. Dependencies
2. App install
3. **Build the FableGear archive + database** (create `ARCHIVE_ROOT`, the
   `Database/` home, and the schema — the foundation, before anything Rekordbox)
4. **Offer to back up Rekordbox into the archive** (optional, opt-in; uses the
   existing `snapshot_include_master_db` path, just surfaced as a wizard step)
5. Read-Rekordbox permission (needed for the RB-centered tools)
6. Write-Rekordbox permission (needed only for delivery/export + RB tools)
7. AI integration (MCP) — opt-in
8. Choose a room

The read/write-Rekordbox permission prompts stay (the RB tools need them) but
move *after* our own foundation is built and after the backup offer.

## 5. Import data flow (clarified)

- **Register into our database first.** An import scans files and records them
  into `fablegear.db` (the archive's library DB) as the source of truth —
  including whatever enrichment/analysis we compute. This is already what
  `import_directory_database_first` does (`FableGearDatabase()` first).
- **Deliver to Rekordbox as an explicit step.** Getting tracks playable on CDJs
  means writing them into Rekordbox — but as a deliberate export/sync from our
  DB, never a silent side effect. The current code exports to a *separate*
  `~/.fablegear/rekordbox_export.db`; whether delivery should target that, the
  configured device DB, or the desktop library is a per-action user choice, not
  a default of "open".
- **Never** let "open" or the library view write the live desktop Rekordbox DB.
  (Observed doing so via a stale build during the debug that prompted this doc.)

## 6. Rekordbox-centered tools (unchanged in intent)

Path healer / relocate, duplicate finder, dead-files, rekordbox-dedupe, sync,
export — these legitimately read/write Rekordbox and keep doing so. Nothing here
removes them; the change is only that they run *on top of* an
already-established FableGear foundation, against an explicit DB target, with
our archive as the backup/source-of-truth layer beneath them.

## 7. Implementation stages

1. **Archive DB home + sync layer** (§3): `archive_db_path()`,
   `hydrate_working_copy_from_archive()`, `sync_db_to_archive()`, wired into
   startup and the checkpoint/shutdown points. Behind a feature check so a
   missing drive degrades gracefully. Tests: hydrate/sync round-trip, offline
   startup, atomic-rename eject safety, conflict → savepoint.
2. **Wizard reorder** (§4): move "Build the FableGear library" ahead of the
   Rekordbox steps; add the "back up Rekordbox into the archive" step.
3. **Import/delivery clarity** (§5): make the Rekordbox-write step explicit in
   the UI/CLI; ensure no open/view path writes the desktop RB DB.
4. **Migration:** on first run of the new build, if `~/.fablegear/fablegear.db`
   exists and the archive has none, seed the archive copy from it (the local DB
   becomes the working copy of a freshly-seeded archive home).

## 8. Self-contained offline archive (portable bootstrap kit)

**Goal:** on first archive build — especially on a removable drive — offer to
make the archive self-sufficient, so the drive can be plugged into another Mac
with no internet and reconstitute the full FableGear toolchain.

**What does NOT work (and why):** copying the live `venv/` to the drive.
Verified against the dev machine:
- the venv does not contain Python — `pyvenv.cfg` points at a framework Python
  (`/Library/Frameworks/Python.framework/...`) that won't exist on a fresh Mac;
- every console script hardcodes an absolute shebang
  (`#!/Users/.../FableGear/venv/bin/python3.13`) — venvs are non-relocatable;
- `ffmpeg`/`fpcalc` are arch-specific Mach-O binaries (arm64 here) — dead on an
  Intel Mac, useless on Windows/Linux;
- **exFAT (the removable-drive case) stores neither the executable bit nor
  symlinks** — a venv/Python tree copied there literally cannot execute.

**What to store instead — an offline *bootstrap kit* (ingredients, not a live
install), reconstituted onto the target's internal disk:**

```
<ARCHIVE_ROOT>/Bootstrap/
  FableGear.zip                 ← the app package (~1.2 MB) — always offer this
  python/                       ← relocatable standalone Python (uv / python-build-standalone)
  wheelhouse/                   ← every Python dep as a wheel (pip download); offline install
  bin/                          ← ffmpeg, fpcalc — universal2 where possible
  install_offline.sh            ← copies kit → ~/FableGear (APFS), builds venv from wheelhouse
  MANIFEST.json                 ← arch(es), OS, versions, checksums
```

On a new Mac the offline installer copies the kit to `~/FableGear` on the
**internal APFS disk** (where exec bits + symlinks work), re-`chmod +x` the
binaries (exFAT stripped them), then `pip install --no-index
--find-links=wheelhouse` — a full install with **zero internet**.

**Scope / caveats to surface in the offer:**
- Per-OS and per-CPU-arch. "Any Mac" needs universal2 binaries + both-arch
  wheels; cross-OS (Windows/Linux) is out of scope for a Mac kit.
- ~600 MB–1.2 GB on the drive (acceptable on a music drive; make it opt-in).
- Bundled unsigned binaries trigger a one-time Gatekeeper approval unless
  notarized.
- Tie the offered kit to the installed FableGear version so app + deps match.

**Staging:** this is independent of §3–§5 and lower priority — the DB safety
model and wizard reorder land first; the bootstrap kit is a later, self-contained
feature (a wizard offer + a build-kit routine + `install_offline.sh`).

## 9. Open questions

1. **Delivery default target.** When a user imports and then delivers to
   Rekordbox, is the default target the configured device DB
   (`/Volumes/.../PIONEER/Master/master.db`), the separate
   `rekordbox_export.db`, or the desktop library? Recommendation: the configured
   device DB, never the desktop library by default.
2. **Archive DB drift/cleanup.** The current live archive DB has ~182k content
   rows against ~71k files on disk — heavy orphaning from prior runs. Should the
   working-copy hydrate also run a reconciliation (prune rows whose files no
   longer exist), or is that a separate explicit "clean library" tool?
3. **Multi-machine same-drive.** If the same archive drive is opened on two
   Macs, the conflict rule (§3.2) preserves both — is that enough, or do we want
   a soft lock file in `Database/`?
