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

> **Status: complete.** 15 auditors, 297 findings.
>
> Coverage:
>
> - the z-index system, plus the Record Room, Chop Shop and app-wide UI;
> - both install paths and the update path;
> - every Chop Shop tool, including the Pipeline and the DB tools in the Chop Shop rail;
> - every undo endpoint, plus an end-to-end "restart from previous session" test.
>
> **Every critical finding was re-checked by an independent verifier in its own fresh sandbox:**
>
> - **All 11 were confirmed.**
> - **8 stay critical.**
> - **3 were moved to high, with reasons given in §2.**
>
> Severities below are the verified ones. Where two or more auditors found the same defect independently, that is noted as **(×N independent)**.
>
> The work ran in four rounds, because three runs were cut off by account usage limits. Each later round reused the earlier evidence.

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
- **Chop Shop static layout: mostly fine.** The Sep 2 rail-overlap fix holds at all three viewports, every rail and DB tool docks cleanly, and the report modal fits at 1024×700.
- **Chop Shop run-time layer: weak, and that's where the safety controls live:**
  - Escape during a run closes the docked tool. That tool holds the only Interrupt and Emergency Stop buttons in the Chop Shop, because the scan bar is hidden there.
  - Stop gives no "Stopping…" feedback.
  - After a kill the readout says "Complete" with a full progress bar.
  - **The Pipeline's end-of-run report is never shown.** Its only entry is an 8 px pill under the docked tool.
  - At 1024×700 with all three safety banners showing, the tool body overruns the readout by 61 px.
  - The rail overflows at 1280 px and below: Import, Link and Dead Files are off-screen at 1024, behind a 1 px scrollbar.

Full inventory and a proposed token scale: `FINDINGS_DETAIL.md` → Z-index.

### Install wizard: checkpoints and exit routes
**Exit routes exist, but the checkpoints are unsafe.** One resume path permanently traps a first-time user.

**In-app onboarding wizard** (`templates/onboarding.html`, 8 steps, tested live at every step):

**Checkpoint.** The only checkpoint is the step number plus five text fields, kept in browser `localStorage`. Every consent answer and choice lives in a JS variable that a reload, quit or crash wipes.

**Exit routes:**

| Step | Back | Finish later |
|---|---|---|
| 0 | none (first step) | yes |
| 1 | **none** | yes |
| 2–6 | yes | yes |
| 7 | **none** | yes |

Leaving reconfigure without finishing keeps the config byte-identical (good).

**Findings:**

- **CRITICAL: a reload, quit or crash at steps 5–7 traps a first-run user permanently** (`wizard-live-trap7`, also found by the code auditor). On resume the user lands on step 7, which has no Back button. Both room buttons send an empty config and get a 400. Every relaunch resumes on step 7 again, so the app can never be entered unless you hand-clear WebView storage. [D, ×2 independent]
- **Declined consent turns into full access after a reload.** If you declined Rekordbox read/write, a reload turns that into full read+write, because save-config treats a missing answer as "yes", while the summary still says "Limited mode". [D, ×2 independent]
- **Reconfigure wipes settings.** It shows none of your current values, and finishing it resets settings the wizard never asked about: AcoustID key, excluded folders, mode, import target, LUFS target and MCP config. [D]
- **Setup is marked complete too early,** at step 5 ("Enable AI") or step 6 (Import). "Finish later" then misleads you. Leaving mid-import kills the server and leaves a half-imported library, and the stale draft hijacks later reconfigures. [D]
- **No path is validated.** A `.txt` file is accepted as the Rekordbox DB, a folder as the device DB, and `/proc` as the archive. [D]

**Shell first-run and update path** (`launch.sh`, `setup.sh`, `install.sh`, `/api/update/apply`; code plus stubbed runs):

- **The in-app Update button installs untagged `main`, then the app keeps showing the old release tag.** If a release tag isn't on `main`, `launch.sh` hard-resets the branch, which then breaks the in-app updater. [D, ×2 independent]
- **A failed update can leave an app that silently never opens.** `/api/update/apply` has no rollback and ignores pip failures. [I]
- **On a fresh Mac whose only Python is Apple's 3.9,** `setup.sh` builds the venv, aborts with "too old", and never rebuilds it. Following the on-screen advice can't fix it. [D]
- **A closed Terminal window loses setup's error.** `setup.sh` keeps no log and sets no trap, so `launch.sh` waits silently for up to 40 minutes. The launch lock goes stale at 30 minutes while setup is allowed 40, so a second `setup.sh` can start. [D]
- **Homebrew can't be installed by the documented one-liner.** `curl | bash` runs it with non-TTY stdin, so Homebrew can't prompt for sudo and aborts on a normal Mac. [I]

**Unmerged standalone GUI installer** (`feature/install-wizard`, PR #159):

- **No Cancel, Back or Finish-later.** Closing the window is the only exit, and it leaves brew/pip running orphaned, writing into `venv/`. [I]
- **One double-click on Continue skips the Python packages step.** It writes `.fablegear_ready` and launches the app with no packages installed. [D]
- **A dropped connection counts as a successful install.** [D]
- **`/api/complete` doesn't check that anything succeeded.** [D]
- **A best-effort essentia failure blocks the whole install,** offering only "Try Again". On macOS 12–14 with Python 3.13 the user can never finish. [D]

### Chop Shop tools: reports, "revert back to" markers, safe undo
**No Chop Shop tool meets the bar you set.** Only the *move* tools (Rename, Organize, Novelty) have a working file-level undo. Even those leave Rekordbox broken, and the *rewrite* tools (Tag Tracks, Normalize, Convert) delete originals with no way back. Summary for the 8 tools tested (full matrix in §4):

| Tool | Report written | Restore point before mutating | Undo actually works | Resume after restart |
|---|---|---|---|---|
| Tag Tracks | Aggregate only, none on cancel | **None** | **No** (API refuses) | Browser-only banner, no starting point |
| Find Duplicates (scan) | Yes, plus a good CSV | n/a (read-only for audio) | n/a | Browser-only; Resume switches Deep → Quick |
| Prune | **None**, though the UI says "check the report" | Savepoint + trash, **not linked to the run** | **No** in the UI; the API restore misplaces files | **None** |
| Rename | Yes | Savepoint (unlinked) + per-file journal | Files: **yes**, byte-identical and idempotent. **Rekordbox rows are not reverted** (tracks go "missing"). Device DB never relinked. | Browser-only banner; resume works |
| Organize | Yes | **None** (journal rows only) | Files: yes when sequential. **With 2–4 workers, colliding files are overwritten and undo restores the wrong audio.** Rekordbox paths broken. | Browser-only; server check broken |
| Normalize | Mislabelled ("Tag Write Failures") | **None; originals deleted** | **No** (API refuses; 0 of 15 originals survive anywhere) | Browser-only; never checkpoints on cancel |
| Convert | Counts only | **None; originals are deleted** | **No** (API refuses) | Browser-only, no format or progress |
| Novelty Scanner | Counts only | None tied to a run (journal rows only) | Partial: the copy can be undone, leaving empty folders | Browser-only; stale checkpoint silently skips |
| Pipeline Wizard | Per step | **None for the run** | Partial: only the rename and organize steps; chained moves revert in the wrong order | **Auto mode saves nothing.** Confirm-mode Resume re-runs every step from step 1 (a re-applied Rename moved a file out of the library). |
| DB tools in the Chop Shop rail (Fix Paths, Link, Import) | Yes, but reports success when every write failed | **Local-DB savepoint before every write** (good) | **No:** restore overwrites the USB device DB; Fix Paths revert reports ok, changes nothing | **None** |

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

**End-to-end test** (demonstrated):

- **The session:**
  1. Sync the library.
  2. Rename two folders.
  3. Organize.
  4. Normalize 8 long files.
  5. `kill -9` the app mid-Normalize.
  6. Relaunch as a returning user.
- **What the user saw after relaunch:**
  - One "Interrupted run — Nm ago" banner, *inside the Normalize card*, which has no rail button.
  - It showed no steps, no progress (4 of 8 files had already been rewritten) and no restore point.
  - With a fresh browser profile, **nothing at all**.
  - The Timeline was empty and the Record Room showed nothing.
- **Trying to get the library back to its starting state using only the app:**
  - Rename and Organize file moves came back byte-identical, and a second revert was a safe no-op.
  - **The 4 normalized files could not be restored.**
  - **4 local Rekordbox rows still point at missing files.**
  - The only Rekordbox savepoint can only be written onto the *device* DB, which made the USB worse: 4 → 11 tracks, now a copy of the laptop library.
- **Danger found along the way** (verified high):
  - The same "Interrupted run" banner also appears **while the job is still running**, for example in a second tab or after a reload.
  - Clicking **Resume** skips the destructive-action confirmation, and the server has no single-job guard.
  - So a second Normalize starts on the same folder, and a file gets gain-boosted twice, ending at +10 LUFS against 0 dBFS. That is permanent.

---

## 2. Critical and high findings

### Critical: data loss, demonstrated live, independently verified

The verifier re-ran each row in a fresh sandbox. The last column shows its verdict.

| ID | What happens | Where | Verifier |
|---|---|---|---|
| **C1** `tool-convert-c-originals-destroyed` | **Convert deletes every original.** The card says originals go to Quarantine. They don't: the `.bak` is unlinked right after each conversion. There's no confirmation, preview or restore point, and the undo API refuses convert. | `audio_processor.py:680` [D] | **Confirmed critical** |
| **C2** `tool-convert-c-aiff-tag-wipe` | **Converting to AIFF wipes every tag** (BPM, key, artist, album, genre, label, artwork). With C1, the loss is permanent. The next library sync then also erases the only surviving copy in the FableGear DB. AIFF is the Pipeline's *default* convert format. | ffmpeg called without `-write_id3v2 1` [D] | **Confirmed critical** |
| **C3** `tool-convert-c-parallel-stem-race` | **With the default 4 workers, two sources with the same name race** (e.g. `X.flac` and `X.wav` → `X.mp3`). One lossless original is destroyed, and the report says "No errors … nothing lost". Reproduced 3 of 3 times. | [D] | **Confirmed critical** |
| **C4** `tool-process-b-normalize-strips-aiff-wav-id3` | **Tag Tracks' normalize option strips all ID3 tags** from AIFF/WAV during the re-encode, and the report still claims the keys were written. | `audio_processor.py` normalize path [D] | **Confirmed critical** |
| **C5** `dupb-fg-playlists-cascade-lost` | **Prune hard-deletes the pruned files' FableGear DB rows.** The FableGear DB is the Record Room's default library, so their Record Room playlist memberships, cues and beatgrids cascade away. Nothing moves them to the keeper and no backup covers them, despite the card's "Your playlists stay intact". | `pruner.py` / `_journal_prune` [D] | **Confirmed critical** |
| **C6** `tool-normalize-no-restore-no-undo` | **Normalize rewrites audio permanently.** There's no restore point, originals are deleted as soon as each swap succeeds, and the undo API refuses. A search of the whole sandbox found 0 of 15 originals surviving. | `audio_processor.py:543-546` [D] | **Confirmed, re-graded HIGH.** A blocking confirm() discloses the permanent rewrite (`runners.js:231-238`); the defect is real. |
| **C7** `tool-normalize-lossless-tags-stripped` | **Standalone Normalize strips every ID3 tag from WAV and AIFF:** title, artist, BPM, key, cover art and Serato cues. | [D] (same root cause as C4) | **Confirmed critical** |
| **C8** `tool-normalize-wav-bitdepth-downgrade` | **WAV is always re-encoded as 16-bit.** 24-bit and 32-bit float sources lose resolution, contradicting the "same bit depth" claim, and the original is deleted. | [D] | **Confirmed, re-graded HIGH.** Silent quality loss against an explicit promise, but the track stays usable and no clipping is introduced. |
| **C9** `tool-organize-workers-race-overwrite` | **Organize with 2 or 4 workers lets colliding files overwrite each other.** 2 of 120 distinct files were permanently lost with "No errors" reported. Undo then put the wrong audio into two original paths. | `library_organizer.py` [D] | **Confirmed critical** |
| **C10** `wizard-live-trap7` | **A reload, quit or crash at onboarding steps 5–7 permanently traps a first-run user on step 7.** It has no Back, save-config returns 400, and every relaunch resumes there. | `onboarding.html` step 7 [D, ×2 independent] | **Confirmed critical** |

### High

**The undo surface is broken (blocks recovery from everything else)**

- **H0a — Savepoints and Trash tabs crash.** `undoLoadSavepoints` and `undoLoadTrash` are not defined (`undo.js:39-40`). The tab row also overflows, so Trash is off-screen. [D ×5]
- **H0b — Timeline is always empty for UI-run jobs.** `routes_undo.py:40-48` reads `job_dispatcher`, which only `mcp_server.py` initialises. [D, lead]
- **H0c — Savepoint restore always targets the device DB (×3 independent).** Restoring a *local* Rekordbox savepoint overwrites the USB `master.db` (`tool-process-b-savepoint-restore-wrong-db`). [D]
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

**Rename, Organize, Normalize, Pipeline (round 3)**

- **Rename: revert leaves Rekordbox broken.** Revert moves files back but never reverts Rekordbox `master.db`, so every relinked track becomes "missing". Later renames can't relink them either (`tool-rename-undo-leaves-rekordbox-broken`). [D]
- **Rename: device DB not relinked.** Rename relinks only the local `master.db`. The device (USB) DB rows for the same files break and aren't backed up (`tool-rename-device-db-not-relinked`). [D]
- **Rename: learned rules can move files outside the library.** A learned "exact rename" target isn't validated, so `../..` or an absolute path moves the file outside the library, and the log hides it (`tool-rename-manual-target-path-escape`). [D]
- **Organize: Rekordbox paths break with no warning.** Organize relinks the FableGear DB but leaves every Rekordbox FolderPath broken (10/10 local, 4/4 device) (`tool-organize-rekordbox-paths-broken`). [D]
- **Organize: interrupt can strand a file.** An Interrupt can land between a move and its journal row, and undo then strands that file (`tool-organize-cancel-unjournaled-move`). [D]
- **Organize: undo merges separate runs.** Undo groups runs within 15 minutes into one session, so separate runs can't be reverted individually (`tool-organize-undo-session-merge`). [D]
- **Normalize: some formats always fail.** Every 24-bit AIFF and every M4A/AAC/ALAC fails (`tool-normalize-aiff24-m4a-always-fail`). [D]
- **Normalize: failures are mislabelled and "Retry" does the wrong thing.** Failures are labelled "Tag Write Failures", and the offered "Retry with Force" rewrites BPM/key tags (overwriting an existing BPM) instead of retrying the normalize (`…-retry-force-wrong-remedy`). [D]
- **Normalize: interrupts leave truncated temp files.** Interrupt or window close during an encode leaves truncated `tmpXXXX.mp3` files in the library, which later runs treat as real tracks (`…-cancel-orphan-tmp-tracks`). Interrupted runs leave no report, journal or checkpoint naming the changed files (`…-interrupt-no-record`). [D]
- **Normalize: MP3 rewrites lose DJ data.** They drop COMM and Serato GEOB (cue) frames, re-encode the cover art and upsize every MP3 to 320k (`…-mp3-tags-reencode`). [D]
- **Pipeline: Resume re-runs completed steps.** It re-runs every step from step 1, and a re-applied Rename moved a file out of the library (`tool-pipeline-resume-reruns-completed-steps`). [D]
- **Pipeline: auto mode never checkpoints.** In the default auto mode, a crash or cancel leaves nothing to resume (`…-no-checkpoint-auto-mode`). [D]
- **Pipeline: "Dry Run" writes.** "Dry Run (preview only)" is on by default, yet it writes tags and a FableGear DB Import row that can't be reverted (`…-dry-run-writes`). [D]
- **Pipeline: no run-level restore point** (`…-no-run-level-revert`). [D]

**Install and update (round 3)**

- **Declined consent becomes full access after a reload** (`wizard-live-consent-flip`). [D ×2]
- **Reconfigure wipes unrelated settings** (`wizard-live-reconfigure-wipes`). [D]
- **Setup is marked complete at step 5 or 6** (`wizard-live-setup-complete-early`). [D]
- **In-app Update moves users to untagged `main`; an off-`main` release tag breaks the updater** (`wizard-code-update-untagged-main`). [D]
- **An update has no rollback** (`wizard-code-update-no-rollback-brick`). [I]
- **Apple Python 3.9 venv trap in `setup.sh`** (`wizard-code-setup-py39-venv-trap`). [D]
- **Homebrew install aborts non-interactively** (`wizard-code-homebrew-noninteractive`). [I]
- **Unmerged GUI installer:** double-clicking skips the package step, a dropped connection counts as success, `/api/complete` is unconditional, and a best-effort essentia failure traps the user (`wizard-code-iw-*`). [D]

**Undo, resume and DB tools (round 4)**

- **A second Resume can double-apply Normalize** (`undo-resume-concurrent-resume-double-gain`). While a job runs, a second tab or a reload shows "Interrupted run" + Resume. Resume skips the destructive-action confirm and the server has no single-job guard. A second Normalize then ran on the same folder, and a file ended at +10 LUFS against 0 dBFS. Verifier: confirmed, re-graded from critical to high. [D, verified]
- **No session surface after a crash.** After crash and relaunch, nothing names the previous session, its steps, where it stopped, or a restore point (`undo-resume-no-session-surface`). [D]
- **The app alone can't restore the starting state.** File moves come back, but normalized audio, local Rekordbox rows and the device DB can't, and the one DB restore makes the USB DB worse (`undo-resume-e2e-partial-restore`). [D]
- **Savepoint restore always overwrites the USB device DB.** Restoring the savepoint a DB tool took overwrites the USB device DB, and the local DB stays wrong (`db-rail-savepoint-restore-overwrites-device`). **3 independent auditors** hit this. [D ×3]
- **Fix Paths relinks to the wrong file.** It re-points a broken FLAC record to the `.aiff`, although the exact `.flac` filename exists (`db-rail-relocate-wrong-file`). [D]
- **Fix Paths revert says "ok" and does nothing.** It is listed as "revertible: true", but in the normal broken-path case the revert blocks every row and still reports "ok" with 0 changes (`db-rail-relocate-revert-always-blocked`). [D]
- **Import "Preview (dry run)" writes.** For targets Both and FableGear it writes the FableGear DB and records an import transaction that can't be reverted (`db-rail-import-preview-writes-fg-db`). [D]
- **"Move Rekordbox Library to Drive" deletes folders with no restore point** (`rmtree`s an existing destination and the source; no savepoint, report or undo). Its UI button is currently a silent no-op because the guard is inverted, so today it's reachable only by API (`db-rail-migrate-rmtree-no-restore`). [D]
- **DB tools: jobs whose every write failed still say "Finished successfully".** None of the rail tools offers any resume: `kill -9` left 800 of 1500 rows committed with no journal, report or transaction. [D]
- **The Pipeline's end-of-run report is never shown** (`ui-live-chop-pipeline-report-unreachable`). [D]

**UI traps and silent no-ops**

- **H1 — Rename with Dry Run off silently does nothing.** Its preflight modal is in an always-hidden container: the probe returned 5 candidates and no file changed (`zindex-static-rename-preflight-unrenderable`). [D]
- **H2 — Confirm-mode Pipeline locks the Chop Shop.** Its gate is invisible and its buttons are disabled (`zindex-static-pipeline-gate-unrenderable`). [D]
- **H3 — Stray Escape closes the next modal.** It makes the next Settings or tool report close itself, and the report becomes an unclickable 8 px pill (`…-escape-stale-close`, `…-escape-poisons-modals`). [D ×2]
- **H4 — The Decks toggle is unclickable while idle.** It has `opacity:0; pointer-events:none` unless a scan is running (`ui-live-record-deck-toggle-unreachable`). [D]
- **H5 — The in-app Update button jumps to untagged `main`** (see §1). [I, lead]

---

## 3. Recommended fix order, then root causes

**Stop the data loss first.** Each step is small and local, and closes the listed findings:

1. **Keep originals and copy tags.**
   - In `_convert_file` and `_normalise_file`, move the original into `Archive/Quarantine/<tool>/<run>/` instead of `bak.unlink()`.
   - Add `-write_id3v2 1` for WAV/AIFF, and keep the source bit depth.
   - Re-read the tags and refuse the swap on a mismatch.
   - Closes C1, C2, C4, C6, C7 and C8, plus most of the Convert and Normalize highs.
2. **Reserve destinations atomically in parallel workers** (`O_EXCL` plus `.part` → `os.replace`) for Convert, Organize and Novelty. Until that ships, default `workers=1`. Closes C3, C9 and the Novelty race.
3. **Prune keeps Record Room playlists:**
   - re-thread FableGear playlist rows to the keeper before `delete_content`;
   - refuse to delete the last copy of a track or a keeper;
   - confirm before any permanent delete.
   Closes C5 and four Prune highs.
4. **Onboarding:**
   - persist the consent answers;
   - add Back on step 7 and clear the draft on a 400;
   - stop save-config defaulting missing consent to `true`;
   - validate the paths.
   Closes C10 and three wizard highs.
5. **Make undo reachable:**
   - implement `undoLoadSavepoints` and `undoLoadTrash`;
   - record which DB each savepoint is from, and restore to *that* DB;
   - write a manifest into each trash folder so files go back where they came from;
   - write UI runs into the `job_dispatcher` store from the Flask process.
   Closes H0a–H0d.
6. **Allow one job per tool.** Add a server-side guard, and show the confirm on Resume too. Closes the double-gain finding.
7. **Make the in-app Update follow release tags,** as `launch.sh` does.
8. **Then** build the session ledger (§5) and the layer stack plus z-token scale (§1 and `FINDINGS_DETAIL.md`).

### Root causes: fix these and most findings close

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

## 4. Per-tool safety matrix (every Chop Shop tool)

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
| Rename | Yes | Savepoint (unlinked) + per-file journal | Files byte-identical, idempotent; **Rekordbox not reverted** | Cancel and kill -9 left DBs consistent (one commit per file) | `localStorage` banner; resume finishes the job | Yes (dry run); non-dry-run single-folder is a silent no-op (H1) | **Writes Rekordbox local DB; device DB never relinked** |
| Rename (learned rules) | — | **None** (quarantine move only in manifest) | **None**; rules can't be retracted | — | Rules persist forever | — | Target path not validated (escapes library) |
| Organize | Yes | **None** before mutating (journal only) | Sequential: byte-identical, pruned folders recreated. **Parallel: data loss, wrong audio restored** | SIGTERM can strand a moved file | `localStorage` only; server check broken | Yes | Relinks FableGear DB only; **Rekordbox broken** |
| Normalize | Mislabelled | **None; `.bak` unlinked** | **None** | Truncated `tmp*.mp3` left in library; no record | `localStorage` only | Preview (clips) is read-only (good) | File only |
| Pipeline Wizard | Per step | **None per run** | Partial (rename, organize only); wrong order for chained moves | kill -9 leaves child `cli.py` writing | **Auto: none.** Confirm: re-runs all steps | **"Dry Run" writes tags + DB** | Inherits every step's boundary issues |
| DB tools: Fix Paths / Link / Import (Rekordbox writes) | Yes, but "Finished successfully" even when every write failed | **Savepoint of the local DB before every write, verified row-identical** (good) | **Broken:** restore overwrites the *device* DB; relocate revert reports ok and changes nothing; Import undo refuses honestly | kill -9 left 800/1500 rows committed, no journal | **None** for any rail tool | Import "Preview" **writes** the FableGear DB | DB-layer tools hosted in the Chop Shop rail |
| DB tools: Audit / Dead Files / export-audit | Yes | n/a (read-only) | n/a | — | — | Yes | Audit runs silently on every page load and writes a report |
| Move Rekordbox Library to Drive (migrate) | **None** | **None** | **None** (`rmtree` of destination and source) | — | — | — | UI button is a no-op (inverted guard); reachable by API only |

**Confirmed good, so keep these:**

- DB tools take a local-DB savepoint before every Rekordbox write, verified row-identical to the pre-run DB. Import undo refuses honestly, and re-running an interrupted FableGear import skips work already done.
- Interrupt and Emergency Stop really do kill the job with no orphan `cli.py`, and the Sep 2 rail-overlap fix holds at all three viewports.
- Savepoint restore blocks path tricks and is itself reversible.
- Rename's file moves are byte-identical, collisions are numbered "(2)", and Rekordbox local rows are relinked one commit per file, so cancel and kill -9 left the DBs consistent.
- Organize with 1 worker: undo restores every file byte-identical and recreates pruned folders.
- Normalize preview is read-only on sources.
- The onboarding wizard has "Finish later" on every step, Back keeps values, leaving reconfigure keeps the config byte-identical, and the AI and Import buttons have double-submit guards.
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

- **Live coverage:**
  - every Record Room surface, at 1440×900, 1280×800 and 1024×700;
  - z-index conflicts confirmed live;
  - Tag Tracks (about 20 runs), Duplicates + Prune (two DB schemas), Convert (format matrix, races, cancel, kill -9) and Novelty Scanner (15 runs);
  - Rename (dry run, multi-folder live run, 300-file revert, cancel, kill -9, learned rules), Organize (assimilate and integrate, 4-worker stress, Interrupt, kill -9, resume), Normalize (9-format bench including 24-bit and 32-bit float, interrupts, kill -9) and the Pipeline Wizard (all step types, cancel, kill -9, confirm-mode resume);
  - the onboarding wizard at every step, including reload and restart, Finish later, bad inputs, double-clicks, reconfigure and a corrupt config;
  - the install and update shell scripts, run with stubs in throwaway directories;
  - every undo endpoint on live data (savepoint, trash, operations, database history, path-traversal attempts), plus the end-to-end crash-and-relaunch session test with a fresh browser profile and two tabs;
  - the DB tools in the Chop Shop rail, with 29 row-level DB snapshots;
  - the Chop Shop UI at three viewports (41 new screenshots in round 4).
  - Each run was backed by sha256 before/after snapshots and DB dumps.
- **The sandbox can't reproduce:**
  - WKWebView rendering and macOS fonts;
  - pywebview drag-and-drop and the native pickers;
  - a running Rekordbox;
  - real CDJ hardware;
  - AcoustID (`fpcalc` absent; the missing-fpcalc paths *were* tested, and they fail silently, which is itself a finding).
- **Harness note:** the auditors initially shared one app copy, and the product's cancel/quit paths killed each other's jobs. That is product finding `tool-process-b-cross-instance-kill`. From round 2 on, every sandbox runs its own app copy.
- **Evidence:** full per-finding evidence, repro commands and screenshot paths are in `FINDINGS_DETAIL.md`.
- **Screenshots:** the 79 screenshots cited by critical and high findings, plus the lead's own, are committed under `screens/`, palette-reduced to keep the repo small. The other ~1,000 captures, the probe JSON, the raw scripts and the snapshots stay in the session scratchpad, which is why some paths cited in `FINDINGS_DETAIL.md` are absent from the repo.
