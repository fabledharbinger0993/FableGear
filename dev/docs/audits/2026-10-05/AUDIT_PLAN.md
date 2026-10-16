# FableGear fine-tooth audit — plan (2026-10-05)

Scope: everything on `origin/main` at `1840750` (the latest merge, the 2026-09-02 debug pass).
Branch for this audit: `claude/stoic-hamilton-ycmk9v`.

Evidence labels used throughout the findings:

| Label | Meaning |
|---|---|
| **demonstrated** | Reproduced live in the sandbox (screenshot / command output / file hash), or proven by a failing check |
| **inferred** | Read directly from code, with a file:line citation, but not exercised live |
| **speculative** | A pattern-match risk. Named so it can be checked, never presented as fact |

## 0. Sandbox (how "live" is possible on Linux)

FableGear is a macOS app (pywebview / WKWebView over a Flask server). The sandbox runs the **same Flask server**
(`app.py`) and drives it with headless Chromium through Playwright. What this covers and what it doesn't:

- **Covered:** every route, template, CSS and JS file. Layering, overlap, click-through and occlusion. Real
  file mutations by Chop Shop tools on a synthetic library. Real encrypted Rekordbox v6 `master.db` fixtures
  (`fablegear_database.rekordbox_fixture`). Server restart (to stand in for app quit/relaunch). Undo/revert
  endpoints.
- **Not covered (stated limits):**
  - WKWebView-specific rendering, and macOS font metrics (Chromium falls back to Linux fonts).
  - pywebview drag-and-drop.
  - Homebrew / Full Disk Access / `osascript` folder pickers.
  - A running Rekordbox process.
  - Real CDJ hardware.
  - `fpcalc` is absent in the sandbox, so AcoustID fingerprint *lookups* cannot be exercised live.

Layout:

- `scratchpad/sbx/` is a pristine template:
  - `home/` holds `~/.fablegear` and `~/Library/Pioneer/rekordbox/master.db`.
  - `Volumes/DJDRIVE/Music Library/` holds 11 ffmpeg-generated tracks in mp3, flac, wav, aiff and m4a. They
    include a near-duplicate pair, a cross-format duplicate, an extended mix, an untagged file, a very loud
    track, a very quiet track, a sample-pack file and a messy filename.
  - `Volumes/DJDRIVE/PIONEER/Master/master.db` is the device DB.
  - The Rekordbox DB also has one broken path, to test Fix Paths.
  - `Incoming/` holds a track to import.
- `launch_sandbox.sh NAME PORT [configured|fresh]` gives each auditor its own copy of the template and its own
  server and port, so destructive tests never collide.

## 1. Change review (what moved since Aug 1)

- Read every Claude session that touched FableGear and was active since 2026-08-01: 24 of them, through
  `list_events`.
- Cross-check each session against `git log origin/main --since=2026-08-01` and every unmerged remote branch.
- Output: `CHANGES_SINCE_AUG1.md`. It ranks the most important changes, lists stranded work and open
  threads, and includes a risk register that feeds sections 2–5 below.

## 2. UI layering, z-index, overlap

**Static pass:**

- Inventory every `z-index` in `static/fablegear.css`, `static/shared/ui_extras.css`, inline template styles,
  and JS-assigned styles (e.g. `z-index:${++cardZ}` in the card stack).
- Build the stacking-context tree. Contexts are created by `position` plus `z-index`, `transform`, `filter`,
  `opacity < 1`, `isolation` and `will-change`.
- Flag:
  - Unbounded or magic numbers (there are values from 0 up to 99000 today).
  - Two layers that claim the same tier.
  - `!important` z-indexes.
  - A modal or toast that sits *under* a panel because it lives in a lower stacking context.

**Live pass:** run at viewports 1440×900, 1280×800 and 1024×700 (the smallest plausible pywebview window).
Open every surface and every *combination* that can co-exist:

- header and space switcher (Record Room ↔ Chop Shop)
- DB panel (Audit, Fix Paths, Link, Import, Dead Files)
- staging panel
- tool modal
- undo wizard
- health panel and modal
- FableGo panel
- settings
- USB export modal
- toasts
- hotplug and drive-offline banners
- resize handles

For each state, check:

- **Overlap:** pairwise bounding-rect intersection of visible `fixed`/`absolute`/`sticky` elements that are
  not in a parent/child relationship.
- **Occlusion:** `document.elementFromPoint` at the center of every visible button, input and link. If the hit
  element isn't the control or its descendant, the control is covered.
- **Clipping:** `scrollWidth > clientWidth` on labelled controls, and text cut off by `overflow:hidden`.
- **Dismissal:** Escape closes the top layer only, focus returns sensibly, and a backdrop click doesn't close
  the wrong layer.
- Screenshot each state into `screens/`.

## 3. Install / onboarding wizard

There are two installers:

1. The shell first-run path: `launch.sh` → `setup.sh` / `install.sh`, with the `.fablegear_ready` and
   `.fablegear_failed` sentinels and `.launch_lock`.
2. The in-app wizard: `templates/onboarding.html`, 8 steps, `/api/onboarding/*`.

For each step:

- **Checkpoints:** is progress persisted (localStorage and/or server) so a refresh, quit or crash resumes at
  the same step? Line 892 claims "Resumed where you left off". Verify what is restored: step index, chosen
  paths, consent flags. Also verify what is *not* restored.
- **Exit routes:**
  - Does "Finish later" work on every step?
  - Does Back work on every step after step 0?
  - Is there an exit while a long operation is running (dep install, library scan, import-sources)?
  - What state does each exit leave on disk? A half-written config, or `setup_complete` set while the config
    is incomplete?
- **Failure paths:**
  - dep install fails
  - Full Disk Access denied
  - nonexistent or unwritable paths
  - DB path pointing at a non-Rekordbox file
  - import-sources error mid-run
  - double-click on Continue (double submit)
  - network-less
- **Re-entry:** `/onboarding?reconfigure=1` shows current values, and cancelling it leaves the existing
  config untouched.
- **Gate integrity:** `_setup_gate_status` should never admit the main UI with an unusable config, and never
  trap a configured user in onboarding.

## 4. Chop Shop — every tool

Tools:

1. Tag Tracks (`step-process`)
2. Find Duplicates + Prune (`step-duplicates`)
3. Rename (`step-rename`)
4. Organize (`step-organize`)
5. Normalize Loudness (`step-normalize`)
6. Convert Format (`step-convert`)
7. Novelty Scanner (`step-novelty`)
8. Pipeline Wizard (chains the tools above)
9. The DB-maintenance rail reachable from the Chop Shop: Audit, Fix Paths, Link, Import, Dead Files, plus
   USB export audit and dedupe.

The rail tools are DB-layer tools, so section 6 also covers them.

For **each** tool, answer with evidence:

| # | Question | How it's checked |
|---|---|---|
| R | **Report:** does it write a report on success, on cancel and on failure? Where (`Archive/Logs/<tool>`, `Archive/Reports`)? Is it linked from the UI? | Run live. List the archive before and after. Read the report. |
| M | **"Revert back to" marker:** before the first mutation, does it create a restore point (savepoint DB copy, trash folder, op-journal rows)? Is that point *referenced* by the job, so the undo timeline shows "revert to before <tool> run at <time>"? | Code trace plus `/api/undo/timeline`, `/api/undo/operations`, `/api/undo/savepoints` and `/api/undo/trash` before and after |
| U | **Undo:** can every mutation be reverted? Is the revert verified as byte-identical (sha256 of every touched file plus a DB row diff)? Is the revert itself undoable and safe when run twice? | Run → hash → revert → hash → compare |
| C | **Cancel and interrupt:** does a mid-run cancel leave a consistent state? Is a checkpoint saved? Is a partial report written? | Cancel via `/api/cancel` mid-run, and also `kill -9` the server mid-run |
| S | **Restart from previous session:** after an app restart, is the interrupted run offered for resume (tool banner, `/api/checkpoint/check`)? Does it carry a *starting-point reference*: when it started, roots and config, files done or remaining, and the restore point to roll back to? Does resume skip completed work, and does it detect that inputs changed since? | Restart the server, reload the UI, check the banner and resume |
| D | **Dry-run / preview:** can the user see what will change before it changes? | UI plus API |
| B | **Boundary:** does a "file" tool silently write the Rekordbox DB (or the reverse)? If so, is that DB write also covered by M and U? | Code trace |

## 5. Session resume (cross-cutting)

Mechanisms in play:

- `checkpoint.py` (`~/.fablegear/checkpoints/<tool>/<key>.json.gz`)
- `state_tracker.py` (`<library>/.fablegear_state.json`)
- the pipeline checkpoint (`pipe-resume-banner`)
- per-tool resume banners (`_showToolResumeBanner`)
- staging batches (`/api/staging/batch/*`)
- the undo timeline (`routes_undo.py`)
- the onboarding resume

Questions:

- Are these one coherent "session" concept, or several partial ones that disagree?
- Is there *any* surface that says "you were last here, at this point, and here is the restore point to go
  back to"?
- What survives a server restart, and what lives only in `localStorage`, which is lost if the WebView storage
  is cleared?

## 6. Record Room / Chop Shop boundary (standing check)

- Record Room must stay database-first: no Finder or folder navigation.
- Chop Shop owns file writes.
- Flag any tool that crosses the boundary without a matching revert marker.

## 7. Verification and reporting

- Every high or critical finding gets an independent adversarial verifier. The verifier re-runs the repro or
  re-reads the code and tries to refute it. Findings it refutes are dropped. Ones it can't decide are
  downgraded to *speculative*.
- Deliverables, in `docs/audits/2026-10-05/`:
  - `CHANGES_SINCE_AUG1.md`
  - `AUDIT_PLAN.md` (this file)
  - `AUDIT_FINDINGS.md`: findings ranked by severity, with evidence label, repro and fix guidance
  - `screens/`: screenshots that back the UI findings
- No product code is changed in this pass. Fixes are proposed, not applied, so the findings stay
  reproducible against `1840750`.
