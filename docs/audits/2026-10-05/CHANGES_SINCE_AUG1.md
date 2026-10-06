# FableGear: what changed since Aug 1, 2026

**Sources**

- Every Claude Code session that touched the FableGear repo and was active on or after 2026-08-01: 24 sessions. Transcripts were read through `list_events`; 21 of them were read in full or near-full.
- `git log origin/main --since=2026-08-01` (38 commits).
- Every unmerged remote branch.
- The open-PR list as of 2026-10-05.
- Claims below marked "checked" were re-verified against the repo today.

**Evidence labels**

- **[D]** demonstrated: a commit, file contents, or a transcript quote.
- **[I]** inferred: from code or another session's report, not re-run.
- **[S]** speculative.

---

## 1. Executive summary

1. **The biggest real safety fix: savepoint backups and restores were silently broken until Aug 6 (#152, `10184bf`). [D]**
   - Rekordbox's `master.db` runs in WAL mode.
   - Backups copied only the main file, so committed data sitting in `-wal` was lost.
   - Restores left the live `-wal` in place, so SQLite replayed it and the restore did nothing.
   - Backup timestamps were second-granular, so two backups in the same second overwrote each other.
   - All three backup paths now share `copy_db_with_sidecars()`.
2. **Import now really writes to Rekordbox, and the user chooses where it writes (#141 → v1.1.28; #147). [D]**
   - Before Aug 1, "Import Tracks" never committed to `master.db`.
   - Since v1.1.28 the default ("Both") commits to the live Rekordbox library.
   - This behaviour change reached installs through auto-update.
   - "FableGear only" skips the Rekordbox-closed gate. "RekordBox only" runs a code path that was previously unreachable and is lightly exercised. [D]
3. **Ship-readiness fixes (Aug 1, #141, v1.1.28). [D]**
   - The trash-rescue gate on prune was dead code; it is now wired in.
   - Import "undo" used to fake success; it now fails honestly.
   - Jobs can be cancelled.
   - USB export detects the drive being unplugged mid-export.
   - The "is Rekordbox running" checks now fail closed (if the check can't run, assume it is running).
   - Prune runs each path in its own savepoint, and `db_migrator` checks the copy's size before deleting the original (`87dd051`).
4. **The emergency stop could kill unrelated programs (fixed Sep 2, #164, `9133440`), but the fix has never been released. [D]**
   - The "is a scan still running" detector matched any process with `cli.py` in its command line, then `os.killpg()`'d its process group.
   - That included your editor, a shell, or another project's `cli.py`.
   - The fix is on `main` but is newer than the latest release tag (**v1.1.30 = `01d39ca`, Aug 14**). See §5, R1.
5. **CI became a real gate (Aug 4). [D]**
   - ruff is clean and gates (#148).
   - pyright went from 429 errors to 0 and gates (#150). This found two real bugs.
   - ffmpeg is now installed in CI, so the 12 audio regression tests run for the first time (#146). These include the guard for the old normalize bug that clipped 39 of 39 tracks and deleted the originals.
   - pyright was still red on `main` from Aug 24 to Sep 2, because of an Iron test that was pushed straight to main.
6. **Corrupted CSS shipped in a release, then got fixed (Aug 12–14). [D]**
   - `ac9e260` was pushed straight to `main` with no PR. It pasted a raw diff into `fablegear.css`, broke the "One Room, One Light" colour boundary, and was tagged **v1.1.29**.
   - #155 (`01d39ca`) fixed it and added album art and Record Room stat tiles. You later tagged that commit as v1.1.30.
7. **Anvil (tag I/O) and Iron (BPM/key) were built, but are not connected to the app. [D]**
   - About 4,000 lines landed on main (#158, `e20a581`, `95a8f9d`, `3b767c4`, `755bf5d`).
   - No app code imports either package (checked by grep).
   - The only live change from this work: `b671cb1` (#160) centres the 90-second analysis window for every librosa-based BPM/key run. It has never been validated on real audio. [D] for the change, [I] for the risk.
8. **The Iron work has split into competing branches, and future sessions will get conflicting instructions. [D]**
   - `anvil`: 20 commits, no PR. Its `CLAUDE.md` names `docs/IRON_RESEARCH.md` as the research log.
   - `iron-tempo-rebuild` (PR #165, open): makes Iron the primary BPM detector with no flag. Its ruff check fails, and it updated neither research doc.
   - `main`'s `CLAUDE.md` names `docs/iron/RESEARCH.md`.
   - The two branches' accuracy numbers can't be compared, because they use different samples and different ground truth.

---

## 2. Most important changes, ranked by user impact × risk

| # | Change | Why it matters | Evidence | Status |
|---|---|---|---|---|
| 1 | WAL-safe backup, restore and snapshot (`copy_db_with_sidecars`); microsecond timestamps | Undo and savepoints used to be silently ineffective | `10184bf` (#152); tests `test_db_connection_wal_backup.py`, `test_undo_restore_wal_safety.py` | Merged Aug 6; released (v1.1.29+) [D] |
| 2 | Import writes to Rekordbox; 3-way import target (`import_target`) | Behaviour change on real libraries, shipped through auto-update | #141 / `90ec780` (v1.1.28); #147 / `dc47449` | Released [D] |
| 3 | Trash-rescue wired into prune; honest import-undo failure; job cancel; unmount detection; fail-closed Rekordbox checks; per-path savepoints in prune | Fewer silent destructive failures | `90ec780`, `69545d2`, `87dd051` | Released [D]. `69545d2` is a partial broad-except cleanup: 7 of 10 agents were cut off and their edits were reviewed by hand, so it deserves a re-review [I] |
| 4 | Process-group kill safety; HTTP errors no longer turned into 500s | Emergency stop could kill unrelated processes; 404 and 405 were reported as 500 | `9133440`, `0910d6b`, `2ebf677` (#164) | On main, **not released** [D] |
| 5 | CI gates: ruff, pyright, ffmpeg tests, stub config | Merges are actually checked now | #142, #146, #148, #150, #153 | Merged [D] |
| 6 | CSS corruption (v1.1.29), then repaired, plus album art and stat tiles | v1.1.29 users had broken CSS until v1.1.30 | `ac9e260` (direct push); `01d39ca` (#155) | Fixed in v1.1.30 [D] |
| 7 | Chop Shop rail overlap fix; duplicate `:root` token block removed; 6 hardcoded cyan colours replaced with `--room-accent` | Layering and the room-colour boundary | `0910d6b` | On main, unreleased [D] |
| 8 | Analysis-window centring for librosa BPM and key | Changes every non-essentia analysis result; never validated on real audio; `sf.info` may not read M4A/AAC, in which case it silently falls back to the start of the track | `b671cb1` (#160) | Released? **No.** It is newer than v1.1.30 [D] for the change, [I] for the M4A claim |
| 9 | Anvil: ID3, FLAC, Vorbis and MP4 reader/writer with atomic, verified writes; Iron: DSP, tempo, key, beats, dry-run | Groundwork for dropping GPL/AGPL dependencies | #158, `e20a581`, `95a8f9d`, `3b767c4`, `9376103`, `755bf5d` | On main, unwired [D] |
| 10 | CLI loads the archive on a fresh machine | A fresh install used to start with an empty library | `a7a7671` (#151) | Released [D] |
| 11 | MCP SSE: loopback by default, bearer token required off-loopback; cleaner CLI first run | Closed an unauthenticated listener on `0.0.0.0` | `e03c467` (#143) | Released [D] |
| 12 | Modal close keyframes; brew banner; Relocate renamed "Fix Paths"; `DESIGN.md` / `PRODUCT.md` added | Every modal using the shared close helper failed to close before this | `a4f5ba2` (#144) | Released [D] |

---

## 3. Work that never landed

| Item | Where | What is stranded | Recommendation |
|---|---|---|---|
| **Per-tool "Undo Last Run" banner and a tagger review step before tags are written** | `mac-wipe-backup-FableGear-1787585473` @ `00d83fd` (auto-commit, Aug 24, on a Jul 18 base) | Exactly the "revert back to" UX you asked for, built on `/api/undo/operations` | **Salvage by hand.** It can't be merged as-is: it would undo main's `_restartToolFresh`, it predates the WAL fix, and it adds a `PYTEST_CURRENT_TEST` bypass to the Rekordbox safety check. [D] |
| Font size +20%, weight 500, brighter `--text`, room-tinted glow, darker track-table columns | Your Mac only (`/Users/camalama/FableGear`), session `01Eb3mzQ…`, Aug 17 | Never committed. `main` still has `html { font-size: 10px; }` and `--text: #dce8ef` (checked) | **You may believe this shipped. It didn't.** Re-apply on a branch if wanted. [D] |
| Standalone install wizard (pywebview) plus the Spectre mascot | PR #159, `feature/install-wizard` (+2/−15) | 1,781 lines; never wired into `setup.sh` or the `.app` | Audit before merge (see `AUDIT_FINDINGS.md` W1–W3): no Cancel or Back; closing the window leaves brew/pip running; `/api/complete` marks setup ready without checking the steps succeeded. [I] |
| Iron tempo rebuild, made the primary BPM path | PR #165, `iron-tempo-rebuild` (+2/−0) | 91.7% exact on a 300-track club set | **Don't merge as-is.** ruff fails (`RUF046` `iron/dsp.py:236`); there's no flag (against `ANVIL_IRON_STATUS.md` §3.2); it doesn't include `anvil`'s research; it didn't update either research log, which `CLAUDE.md` requires. [D] |
| Iron research, 20 commits, including the **`numba<0.62` segfault pin** | `anvil` (+20/−4), no PR | About 8 of the 20 duplicate work already on main. Real new work: `energy_flux` onset (60.7% → 74.5% MIREX), 60–180 BPM range, the numba pin | Port the numba pin to main now (see R5). Then reconcile the two research docs and the two `CLAUDE.md` files. [D] |
| Rekordbox behaviour observer (`scripts/rb_observe.py`) | PR #157 (draft) | Read-only snapshot-diff harness; never run on a real `master.db` | Run it once against your library; it's the first step toward the volume-remount fix (§4). [D] |
| One-line `log.error` → `log.exception` for sync failures | PR #161 (draft) | `main` still logs without the traceback at `rekordbox_sync.py:350` (checked) | Merge. [D] |
| Iron testing theory (§7) | PR #163 (draft) | Already merged into `anvil` (`e56b025`) | Close, or fold into the `anvil` reconciliation. [D] |
| Handoff notes | PR #156 | Stale: mostly about the marketing site, and contradicted by later Iron work | Close. [D] |
| Superseded branches | `claude/fablegeargear-marketing-review-nei55o`, `claude/record-room-dashboard-css-5udcer` | Identical to the squashed #154 and #155 | Delete. [D] |
| Experiment scripts and test log PDF | Session scratchpads on your Mac (`/private/tmp`) | Never committed | Gone unless copied out. [D] |

---

## 4. Open threads, and things you were asked to do

**Security (do these first)**

- **Rotate exposed credentials. [D]**
  - Session `01TDM1sk…` (Aug 20) printed a GitHub keychain OAuth token and a fine-grained PAT in plain text in its transcript, at 18:25:46 and 18:26:14.
  - Session `01QGDW6F…` echoed a live Cloudflare API token (`cfat_…`) from your iCloud-synced `claude_desktop_config.json`.
  - That config also enables `bypassPermissions` for `/Users/cameronkelly` and `/Volumes`.
- **The local API has no Host, Origin or CSRF check, and mutating tools are plain GET requests. [I]** (session `011xedEN…`, Oct 2)
  - `/api/run/organize`, `/rename` and `/convert` take paths from the query string and spawn `cli.py`.
  - The proposed fix: reject non-loopback `Host`, require a token or custom header on `/api/run/*`, and move mutating routes to POST.
  - Not demonstrated. It is listed here because it bears on "safely undo".

**Your local machines** (only you can check these)

- `~/FableGear` may still be in an unfinished `anvil` merge, with a `CLAUDE.md` add/add conflict, from session `015zd4z5…` (Aug 25). [D] for the session state, unknown now.
- `~/Downloads/FableGear-main` is a zero-commit repo with the live `origin` remote. Work done there isn't versioned and can be pushed by mistake. [D]
- On Intel macOS 12 with Python 3.13:
  - a fresh install from `main` fails, because llvmlite has no Intel wheel without the `numba<0.62` pin;
  - `launch.sh:104` retries `requirements_optional.txt` (essentia) **on every launch**, a roughly 5-minute stall (checked);
  - `setup.sh` keeps no log, so closing Terminal loses the error. [D]
- Did your library recover after the **80,000-path loss at the gig**? The likely cause is the volume remounting as `"<Drive> 1"`. The fix (tier-0 volume-UUID + relative-path resolution) was designed in session `015CQFxE…`, Aug 16, but **not built**. No volume-UUID handling exists anywhere. [D]

**Questions sessions asked that never got an answer**

- Iron: top-K tempo candidates; validating the tiered config at 400+ tracks; the slow (<100 BPM) end and half-time bias; a DBN prototype.
- Whether Booth Mode should re-tint room accents. Today 69 rules stay cyan while 132 turn orange. [D]
- Whether to commit the two real ANLZ sample files as hardware fixtures. They contain real paths, a privacy question. [D]
- About half of the roughly 500 broad-except sites were never reviewed. [D]
- 26 pip-audit vulnerabilities remain in `mcp`'s transitive dependencies. No code signing or notarization. [D]

---

## 5. Risk register for this audit

These feed straight into `AUDIT_FINDINGS.md`.

- **R1. Release lag, and two update channels that disagree.**
  - `launch.sh` follows release tags.
  - The in-app **Update** button runs `git pull origin main --ff-only` (`app.py:733+`), even though its docstring says "latest release". That puts users onto untagged `main`, which since Aug 1 has carried 8 commits pushed with no PR.
  - The Sep 2 kill-safety fix is on `main` but not in any release. [I from code]
- **R2. Undo coverage is partial.**
  - `/api/undo/operations` journals only organize, rename, novelty-copy, convert and relocate. `_REVERTIBLE` = {organize, rename, novelty_copy, relocate} (`routes_undo.py:321`).
  - In-place normalize deletes its `.bak` after verifying.
  - Tag writes, prune (only via trash) and dead-file actions aren't journaled.
  - Import undo fails honestly.
  - `IMPLEMENTATION_COMPLETE.md` describes `reversible_operations`, `file_backup`, `preview_confirm` and `checkpoint_manager` modules that **do not exist** (checked). [D]
- **R3. Resume is in the browser.**
  - Per-tool resume lives in `localStorage` (`rb_ckpt_*`).
  - `job_dispatcher` checkpoints are written only on completion, and only for MCP jobs. [D]
- **R4. Layering.**
  - `01d39ca` added a full-viewport `body.fg-space-*::after` grid overlay (`position:fixed; z-index:0`).
  - Any `:root` token derived from a value a room overrides on `body` won't follow the room. That was the root cause of the rail overlap and the Booth split.
  - The z-index values in use run from 0 to 99000. [I]
- **R5. Process crash.** Without the numba pin, the librosa beat-tracker can segfault the **whole process** mid-batch on Python 3.13. [D on `anvil`]
- **R6. Boundary.**
  - The Record Room "All Music" mode opens a **"Filesystem"** sidebar with path breadcrumbs, folder rows and a stage-folder button (`index.html:1044`, `library_mode.js:246-249`).
  - Record Room code lives in `static/chop_shop/tool_modal.js`. [D]
- **R7. Dangling result targets.** JS references IDs that no template defines, including `normalize-result` and `process-result`. Chop Shop result rendering aimed at them silently does nothing. [I]

---

## 6. Sessions that weren't about the FableGear app

- `01YQQNGm…`: marketing review. Only `MARKETING.md` (#154) landed in FableGear; the rest is guthrieent.com.
- `015oZtr9…`, `01643hKY…`: whether to fold the website repo into FableGear. Advice only; the second hit a rate limit.
- `01UiPbhA…`: guthrieent.com DNS / Cloudflare outage.
- `01CA8yFK…`: guthrieent EPK page CSS.
- `01Py8spZ…`: the chef-cam-culinary-labs repo. FableGear was only attached.
- Mostly website work, with some FableGear:
  - `015CQFxE…`: PR #157 and the gig post-mortem.
  - `01FCoUZK…`: merged #154 and #155 about a minute after marking them ready, on green CI alone.
  - `01K446Gr…`: PR #160.

---

## 7. Contradictions between sessions and the git record

1. **"Iron isn't a real concept"** (session `01MqZCeD…`, Aug 24) is **wrong**. Iron was already on `origin/anvil`, and on `main` by Aug 24.
2. **"Iron doesn't exist as code yet"** (session `01K446Gr…`, Aug 21) was **stale**: the `iron/` package existed only unpushed on your Mac. `docs/iron/RESEARCH.md` still frames Iron as the librosa fallback, which conflicts with the `iron/` package now on main.
3. **"No stray Record Room folder-first chrome"** (session `011xedEN…`, Oct 2) is a **false negative**. The Sep 2 session demonstrated the "Filesystem" sidebar, and it is still at `templates/index.html:1044` (checked).
4. **`18ba7a2` claims "untracked files can never conflict with a fast-forward pull."** Not strictly true: git refuses a pull that would overwrite an untracked file. The result is a failed update, not data loss.
5. **`docs/ANVIL_IRON_STATUS.md` is stale.**
   - It says Anvil covers MP3/WAV/AIFF only; FLAC, MP4 and OGG were added in `e20a581`.
   - It says nothing populates `downbeat_offset`; `755bf5d` does.
   - Its §3.3 cutover list misses about 9 mutagen import sites.
6. **The Iron accuracy numbers aren't comparable across branches:**

   | Branch | Sample | Iron | librosa |
   |---|---|---|---|
   | `anvil` | n=1000, genre-diverse, tag ground truth | 67.1% MIREX | 84.3% (inflated by its fixed [76,152) fold) |
   | `iron-tempo-rebuild` | 300 club tracks, Rekordbox ground truth | 96.3% MIREX | — |
   | earlier live run | 150 tracks | 50.0% MIREX | — |

7. **`IMPLEMENTATION_COMPLETE.md` describes undo modules that don't exist** (`chop_shop/reversible_operations.py`, `chop_shop/file_backup.py`, `chop_shop/preview_confirm.py`, `checkpoint_manager/`). Checked: none are present. Anyone reading it would overestimate how reversible the Chop Shop is.
