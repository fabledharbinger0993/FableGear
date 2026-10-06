# FableGear audit: findings

Audit of `main` at `1840750`. Run 2026-10-05/06 against the real Flask app in a Linux sandbox:

- headless Chromium;
- real encrypted Rekordbox v6 fixture DBs;
- a synthetic 11-track library (method: `AUDIT_PLAN.md` §0).

**Evidence labels**

- **[D]** demonstrated live: commands, outputs and screenshots are in `FINDINGS_DETAIL.md`.
- **[I]** inferred from code, with a `file:line` citation.
- **[S]** speculative.

No product code was changed.

> **Status: PARTIAL.**
>
> Covered and written up here:
>
> - the code-level z-index review, plus the Record Room and app-wide UI click-through;
> - four Chop Shop tools: Tag Tracks, Find Duplicates + Prune, Convert, Novelty Scanner;
> - my own click-through.
>
> Still running in round 3, after two earlier runs were cut off by account usage limits:
>
> - the install wizard (live + code, including the unmerged GUI installer);
> - Rename, Organize, Normalize, the Pipeline Wizard;
> - undo/resume end to end, the DB tools in the Chop Shop panel, and the Chop Shop UI pass;
> - independent verification of the critical findings.
>
> This file will be updated in place when those land. Until then, critical and high findings carry the original auditor's evidence label but no second-agent verdict. Where two or more auditors found the same defect independently, that is noted as **(×N independent)**.

---

## 1. Short answers to what you asked

### Z-levels and overlapping panels
**Not sound.** There is no layering system:

- **44 distinct `z-index` values, from 0 to 99000, and no tokens.** About 15 layers are dead, and one rule silently overrides another (the command palette is declared at 13000 but actually renders at 9000).
- **There is no shared Escape handling or layer stack.**
  - One stray Escape arms a stale close, so the *next* Settings window or tool report closes itself (×2 independent).
  - Escape also closes the panels *underneath* rather than the top one.
- **Panels overlap in the wrong order:**
  - Staging sits above the USB Export modal.
  - The command palette opens *under* modals but still takes keystrokes.
  - The update prompt opens under Settings, which is its only entry point.
  - The left rail is covered by the scan bar and log panel, so Undo is unreachable while the log is open.
  - The drive flyout is trapped inside the left rail's layer.
  - The file browser opens under the DB panel's click-catcher.
- **Two modals can never render at all,** because they live inside a container that is always hidden. That turns real flows into silent no-ops (§2, H1–H2).

Full inventory and a proposed token scale: `FINDINGS_DETAIL.md` → Z-index.

### Install wizard: checkpoints and exit routes
**Pending, round 3.** What is established so far:

- **The in-app Update button installs untagged `main`, not the latest release. [I]**
  - Its docstring says "latest release", but it runs `git pull origin main --ff-only`.
  - On the next launch, `launch.sh` sees HEAD *ahead* of the tag and keeps it.
  - One click moves a release user onto unreviewed `main` (8 commits since Aug 1 were pushed with no PR), and off the release track.
- **The unmerged standalone installer (`feature/install-wizard`, PR #159) has three problems. [I]**
  - It has no Cancel, Back or Finish-later; closing the window is the only exit.
  - Closing the window mid-install leaves brew/pip running orphaned, writing into `venv/`.
  - `/api/complete` writes `.fablegear_ready` without checking that the install steps succeeded.
- **The in-app onboarding has a "Finish later" exit and claims to "resume where you left off".** The live step-by-step test of every checkpoint and exit route is in round 3.

### Chop Shop tools: reports, "revert back to" markers, safe undo
**No tool tested so far meets the bar you set.** Summary for the four tested tools (full matrix in §4):

| Tool | Report written | Restore point before mutating | Undo actually works | Resume after restart |
|---|---|---|---|---|
| Tag Tracks | Aggregate only, none on cancel | **None** | **No** (API refuses) | Browser-only banner, no starting point |
| Find Duplicates (scan) | Yes, plus a good CSV | n/a (read-only for audio) | n/a | Browser-only; Resume switches Deep → Quick |
| Prune | **None**, though the UI says "check the report" | Savepoint + trash, **not linked to the run** | **No** in the UI; the API restore misplaces files | **None** |
| Convert | Counts only | **None; originals are deleted** | **No** (API refuses) | Browser-only, no format or progress |
| Novelty Scanner | Counts only | None tied to a run (journal rows only) | Partial: the copy can be undone, leaving empty folders | Browser-only; stale checkpoint silently skips |

Cross-cutting, for every tool:

- **The Undo Wizard's Savepoints and Trash tabs crash with a ReferenceError** (×5 independent).
- **Its Timeline is always empty for tools run from the UI** [D, lead].
- **Restoring a savepoint can overwrite the USB device DB with your computer's DB** [D].

So today there is **no UI path to any revert point at all**.

### "Restart from previous session (with starting point reference)"
**Does not exist.** It is spread across five partial mechanisms that don't talk to each other:

1. Per-tool resume banners stored only in browser `localStorage`. They are lost if WebView storage clears, and they show only an age and folder paths: no modes, no done/remaining counts, no restore point.
2. Server checkpoints, written only every 25 files. `/api/checkpoint/check` can't find them, because of a key mismatch.
3. A per-library `.fablegear_state.json` that records only "last run, exit code".
4. An MCP-only job history that the UI never writes to.
5. An operations journal that groups runs into 15-minute windows.

No surface says "last session started at X, did A→B→C, stopped at step N; restore point = Y", and nothing can revert to that point. A design for a session ledger built from the existing pieces is in §5.

---

## 2. Critical and high findings

### Critical: data loss, demonstrated live

| ID | What happens | Where |
|---|---|---|
| **C1** `tool-convert-c-originals-destroyed` | **Convert deletes every original.** The card says originals go to Quarantine. They don't: the `.bak` is unlinked right after each conversion. There's no confirmation, preview or restore point, and the undo API refuses convert. | `audio_processor.py:680` [D] |
| **C2** `tool-convert-c-aiff-tag-wipe` | **Converting to AIFF wipes every tag** (BPM, key, artist, album, genre, label, artwork). With C1, the loss is permanent. The next library sync then also erases the only surviving copy in the FableGear DB. AIFF is the Pipeline's *default* convert format. | ffmpeg called without `-write_id3v2 1` [D] |
| **C3** `tool-convert-c-parallel-stem-race` | **With the default 4 workers, two sources with the same name race** (e.g. `X.flac` and `X.wav` → `X.mp3`). One lossless original is destroyed, and the report says "No errors … nothing lost". Reproduced 3 of 3 times. | [D] |
| **C4** `tool-process-b-normalize-strips-aiff-wav-id3` | **Tag Tracks' normalize option strips all ID3 tags** from AIFF/WAV during the re-encode, and the report still claims the keys were written. | `audio_processor.py` normalize path [D] |
| **C5** `dupb-fg-playlists-cascade-lost` | **Prune hard-deletes the pruned files' FableGear DB rows.** The FableGear DB is the Record Room's default library, so their Record Room playlist memberships, cues and beatgrids cascade away. Nothing moves them to the keeper and no backup covers them, despite the card's "Your playlists stay intact". | `pruner.py` / `_journal_prune` [D] |

### High

**The undo surface is broken (blocks recovery from everything else)**

- **H0a — Savepoints and Trash tabs crash.** `undoLoadSavepoints` and `undoLoadTrash` are not defined (`undo.js:39-40`). The tab row also overflows, so Trash is off-screen. [D ×5]
- **H0b — Timeline is always empty for UI-run jobs.** `routes_undo.py:40-48` reads `job_dispatcher`, which only `mcp_server.py` initialises. [D, lead]
- **H0c — Savepoint restore always targets the device DB.** Restoring a *local* Rekordbox savepoint overwrites the USB `master.db` (`tool-process-b-savepoint-restore-wrong-db`). [D]
- **H0d — Trash restore misplaces and overwrites files.** It dumps files flat into the music root under mangled names, silently overwrites same-named files, and restores no DB state (`dupb-trash-restore-flattens-overwrites`). [D]

**Destructive operations with no guard or a wrong result**

- **Prune: no guard against deleting every copy.** Keepers can be queued, every copy of a track can be deleted, and permanent delete has no confirmation (`dupb-no-guard-all-copies`). [D]
- **Prune: a stale report can remove the last copy.** If a report's keeper is missing, Prune auto-selects and removes the only remaining copy (`dupb-stale-report-last-copy`). [D]
- **Prune: files move even when their DB removal failed.** The run is still reported as a success (`dupb-db-error-files-moved`). [D]
- **Prune: a keeper that isn't in Rekordbox leaves dangling rows.** The in-library copy is deleted anyway, its playlist slot dangles and its cues are orphaned (`dupb-keeper-not-in-rb`). [D]
- **Convert: DJ tags are lost across formats.** BPM, key, comment and label are remapped into non-standard frames, and M4A BPM is dropped (`tool-convert-c-tag-mapping-loss`). [D]
- **Convert: Rekordbox links break.** Every converted track breaks in Rekordbox (local and device), and only the FableGear DB is relinked. Path integrity fell from 90.9% to 18.2% (`tool-convert-c-rekordbox-not-relinked`). [D]
- **Convert: the whole library is pre-filled with no confirm.** The card arrives pre-filled with the entire library, so adding one subfolder converts everything, with no confirm or preview (`tool-convert-c-prefilled-library-no-confirm`). [D]
- **Tag Tracks: passive mode overwrites M4A tags.** Existing M4A BPM and key are overwritten (118 → 162, 4A → 5A) (`tool-process-b-m4a-passive-overwrite`). [D]
- **Tag Tracks: dry run writes tags.** This applies to the API and to the Pipeline's "Dry Run (preview only)" (`tool-process-b-dry-run-writes-tags`). [D]
- **Tag Tracks: no restore point or undo** for tag writes, re-encodes or DB upserts (`tool-process-b-no-revert-marker-no-undo`). [D]
- **Novelty Scanner: a stale checkpoint skips tracks silently.** A fresh run consumes it and skips tracks it never copied (75 of 120), and the report hides it (`tool-novelty-c-stale-checkpoint-silent-skip`). [D]
- **Novelty Scanner: interrupted copies become "already present".** An interrupted copy leaves a truncated file, which a later run reports as "confirmed already present". The only library copy of a 40-minute recording was a 49-second stub (`…-truncated-copy-confirmed-present`). [D]
- **Novelty Scanner: parallel copies overwrite each other.** With workers > 1, same-named tracks race and overwrite each other (60 reported copied, 58 on disk) (`…-parallel-copy-race-overwrites`). [D]
- **Novelty Scanner: filename mode reports false matches.** Non-Latin names normalise to an empty key, so unrelated recordings are "confirmed already present" (`…-filename-mode-false-present`). [D]
- **Record Room: a column-header click rewrites playlist order.** One click on a header in a playlist permanently rewrites the curated order, with no confirm or undo (`ui-live-record-playlist-header-sort-destructive`). [D]
- **Record Room: deleting a playlist leaves no revert marker** (`ui-live-record-playlist-delete-no-undo`). [D]
- **Record Room: title edits target the wrong database.** In the default view, title edit always 404s because it targets Rekordbox `master.db`, and every failure still writes a `master.db` savepoint. These look like real restore points (`ui-live-record-title-edit-wrong-db`). [D]
- **Drive unplugged: the app writes onto the boot disk.** Opening the app with the music drive unplugged recreates the drive's folder tree, writes an archive, report and DB there, and toasts "Library audit complete ✓" (`ui-live-record-offline-drive-phantom-writes`). [D]

**UI traps and silent no-ops**

- **H1 — Rename with Dry Run off silently does nothing.** Its preflight modal is in an always-hidden container: the probe returned 5 candidates and no file changed (`zindex-static-rename-preflight-unrenderable`). [D]
- **H2 — Confirm-mode Pipeline locks the Chop Shop.** Its gate is invisible and its buttons are disabled (`zindex-static-pipeline-gate-unrenderable`). [D]
- **H3 — Stray Escape closes the next modal.** It makes the next Settings or tool report close itself, and the report becomes an unclickable 8 px pill (`…-escape-stale-close`, `…-escape-poisons-modals`). [D ×2]
- **H4 — The Decks toggle is unclickable while idle.** It has `opacity:0; pointer-events:none` unless a scan is running (`ui-live-record-deck-toggle-unreachable`). [D]
- **H5 — The in-app Update button jumps to untagged `main`** (see §1). [I, lead]

---

## 3. Root causes: fix these and most findings close

1. **No single undo or run ledger.**
   - Every tool writes (or doesn't write) its own breadcrumbs: savepoints, trash folders, `fg_processing_log` rows, `localStorage` checkpoints, MCP job history.
   - None of them carries a run ID, so nothing can say "revert this run".
   - **Fix:** one run record per job (start time, tool, roots, config, restore points), written *before* the first mutation and shown in the Undo Wizard. See §5.
2. **Destructive file tools don't keep originals.**
   - Convert and normalize delete the `.bak` after verifying.
   - **Fix:** move originals into `Archive/Quarantine/<tool>/<run>/<relative path>` and journal it. Then undo is a move back.
3. **File tools write databases without covering those writes.**
   - Convert relinks the FableGear DB but not Rekordbox. Prune deletes FableGear rows and cascades playlists. Tag Tracks upserts with no snapshot.
   - **Fix:** every DB write goes through a transaction that the run record references.
4. **Tag I/O isn't format-aware or verified.**
   - AIFF/WAV lose ID3 on re-encode. M4A "missing" checks look at ID3 frame names.
   - **Fix:** copy tags explicitly after ffmpeg, verify by re-reading, and refuse to swap on a mismatch. Anvil already has this write-verify pattern; it just isn't wired in.
5. **Concurrency has no destination reservation.**
   - Convert and Novelty pick names, then write.
   - **Fix:** reserve the destination atomically (`O_EXCL`), write to `.part`, verify, then `os.replace`.
6. **Interrupts are unhandled.**
   - `cli.py` has no SIGTERM handler, so cancel leaves partial files, no report and no checkpoint flush.
   - Cancel and Quit also kill *every* `cli.py` under the install directory, including another instance's jobs.
7. **UI layering has no system.**
   - **Fix:** one z-token scale, one layer stack with a single Escape handler, and modals mounted at `<body>` level, never inside room containers.

---

## 4. Per-tool safety matrix (tools tested so far)

Each cell is a summary; the full evidence per cell is in `FINDINGS_DETAIL.md`.

| Tool | R — report | M — restore point | U — undo | C — cancel / interrupt | S — resume after restart | D — dry run | B — boundary |
|---|---|---|---|---|---|---|---|
| Tag Tracks (tags) | Aggregate `.txt`; none on cancel | None | None (API: "cannot be reverted") | SIGTERM, no handler; checkpoint every 25 files | `localStorage` banner; server check broken | **Writes tags** | Writes FableGear DB, no marker |
| Tag Tracks (normalize mode) | Wrong folder; claims keys written | `.bak` deleted | None | Atomic per file | As above | None | File only |
| Tag Tracks (rename mode) | One line | Savepoint (unlinked) + journal | Partial; restore targets wrong DB | Skipped on cancel | From `localStorage` | None | **Writes Rekordbox DB** |
| Find Duplicates | `.txt` + CSV (good) | n/a | n/a | No checkpoint or report | `localStorage`; Deep → Quick | Scan is the preview | Writes FableGear DB despite "Read-Only" badge |
| Prune | **None** | Savepoint + trash, unlinked | UI dead; API misplaces | DB phase OK; file phase ignores cancel | **None** | Not in UI | **Deletes Rekordbox + FableGear rows** |
| Convert | Counts only | **None** | **None** | Partial temp files left in library | `localStorage`, no format | None; pipeline refuses (good) | Relinks FableGear DB only; Rekordbox broken |
| Novelty Scanner | Counts only | None per run | Partial (empty dirs left) | Truncated file left | `localStorage`; stale checkpoint used silently | Yes, but has side effects | Never touches Rekordbox (good) |
| Rename / Organize / Normalize / Pipeline / DB tools | *Pending: round 3* | | | | | | |

**Confirmed good, so keep these:**

- Novelty never moves or modifies the source, and its operations revert is idempotent.
- Prune's DB-phase cancel rolls back cleanly.
- The prune savepoint API restores Rekordbox rows exactly.
- Keeper re-threading works when the keeper is in Rekordbox.
- The rename journal revert works and is idempotent.
- Normalize failures keep the original.
- Convert's path guard works, and the pipeline refuses a dry-run convert.
- Toasts render above every layer.
- The drive flyout's Escape-and-focus handling is the model the other layers should copy.

---

## 5. Recommended design: a session ledger with a starting-point reference

This is built only from pieces that already exist.

1. **Run record.** On every `/api/run/*` and every DB-writing route, `helpers._sse_response` (and the in-process routes) creates a row in `job_dispatcher`'s SQLite store *from the Flask process*. That alone fixes the empty Timeline. The row holds `run_id`, `session_id`, tool, roots, full config, `started_at` and status.
2. **Restore points, linked.** Before the first mutation, each run registers its restore points against `run_id`:
   - DB savepoint path, plus *which* DB it is (fixes the wrong-target restore);
   - quarantine folder for originals;
   - trash folder plus a manifest of original paths;
   - FableGear DB transaction ID;
   - prior tag values.
3. **Session.** A session is opened at app launch, or by an explicit "Start session". The first run's pre-state is the session's **starting point**. `restart.sh`, a crash or a quit leaves the session open, with its last run marked `interrupted` by the existing `_db_mark_stale_jobs`.
4. **On relaunch,** a banner and the Undo Wizard show: *"Last session — started Oct 5 18:02 · Rename → Organize → Normalize (stopped at 7/40) · Resume Normalize · Revert this run · Revert whole session to starting point."* Resume uses the server checkpoint, not `localStorage`, with the key mismatch fixed. Revert walks the runs in reverse and applies each run's restore points.
5. **Retire `localStorage` as the source of truth.** Keep it only as a cache of the server ledger.

---

## 6. Coverage and limits

- **Live coverage so far:**
  - every Record Room surface, at 1440×900, 1280×800 and 1024×700;
  - z-index conflicts confirmed live;
  - Tag Tracks (about 20 runs), Duplicates + Prune (two DB schemas), Convert (format matrix, races, cancel, kill -9) and Novelty Scanner (15 runs).
  - Each run was backed by sha256 before/after snapshots and DB dumps.
- **The sandbox can't reproduce:**
  - WKWebView rendering and macOS fonts;
  - pywebview drag-and-drop and the native pickers;
  - a running Rekordbox;
  - real CDJ hardware;
  - AcoustID (`fpcalc` absent; the missing-fpcalc paths *were* tested, and they fail silently, which is itself a finding).
- **Harness note:** the auditors initially shared one app copy, and the product's cancel/quit paths killed each other's jobs. That is product finding `tool-process-b-cross-instance-kill`. From round 2 on, every sandbox runs its own app copy.
- **Evidence:** full per-finding evidence, repro commands and screenshot paths are in `FINDINGS_DETAIL.md`. Raw scripts and snapshots are in the session scratchpad and are referenced there.
