# Audit 2026-10-05: full findings detail

Generated from each auditor's structured output. Every finding carries the auditor's evidence label (demonstrated / inferred / speculative) and, for critical/high findings, the independent verifier's verdict. The prioritized summary is in `AUDIT_FINDINGS.md`; this file is the evidence appendix.

> Auditors with no result (not covered here): ui-live-chop, undo-resume, db-rail

## Contents

- [Z-index / stacking contexts (static + live confirm)](#z-index--stacking-contexts-static--live-confirm)
- [Record Room + global chrome click-through](#record-room--global-chrome-click-through)
- [Onboarding wizard (live)](#onboarding-wizard-live)
- [Installers (shell first-run, in-app wizard, unmerged GUI installer) — code](#installers-shell-first-run-in-app-wizard-unmerged-gui-installer--code)
- [Chop Shop: Tag Tracks](#chop-shop-tag-tracks)
- [Chop Shop: Find Duplicates + Prune](#chop-shop-find-duplicates--prune)
- [Chop Shop: Rename](#chop-shop-rename)
- [Chop Shop: Organize](#chop-shop-organize)
- [Chop Shop: Normalize Loudness](#chop-shop-normalize-loudness)
- [Chop Shop: Convert Format](#chop-shop-convert-format)
- [Chop Shop: Novelty Scanner](#chop-shop-novelty-scanner)
- [Chop Shop: Pipeline Wizard](#chop-shop-pipeline-wizard)

## Z-index / stacking contexts (static + live confirm)

_Auditor: `zindex-static`_

I inventoried every z-index in fablegear.css (77), ui_extras.css (1), the JS (3 sites) and onboarding.html (3 inline). For each one I recorded the selector, value, position, other context-creating properties, surface and whether it is live, dead or unreachable. I built the stacking-context tree from the Jinja-rendered DOM, then checked the conflicts live in headless Chromium on a port-5100 sandbox (computed style plus elementFromPoint, at 1440×900, 1280×800 and 1024×700). The layering has no system behind it: 44 distinct values from 0 to 99000, about 15 dead or unreachable layer selectors, duplicated Chop Shop rules, and a palette z-index that ui_extras.css silently overrides (13000 becomes 9000).

The most serious problems are not numeric. Two Chop Shop modals live inside #chop-card-stack, which is display:none in both rooms. As a result the Rename preflight can never show, so Rename with Dry Run off is a silent no-op: the real probe returned 5 candidates and no file changed. The pipeline confirm gate is likewise invisible, and its buttons are disabled.

A single stray Escape (for example, pressed at the boot Welcome modal) arms a stale animationend callback. The next Settings or Report modal then closes itself within about 0.3s, and the report collapses into an 8px pill that is covered by other layers.

Medium-severity layering conflicts, all reproduced live:
- The update prompt opens under Settings and Welcome, which are its only entry points.
- The palette opens under every modal but still takes keystrokes; Enter ran an action blind behind Settings.
- The non-modal Staging panel (9700) sits above the USB Export modal.
- The left rail (175) sits under the full-width scan bar and log panel, so Undo is unreachable while the log is open.
- The drive flyout is trapped in #left-panel's stacking context.
- The file-browser drawer (158) sits under the DB/Undo click-catchers (159/160).
- Safety banners never render in the Record Room.
- Escape closes the layers underneath instead of the top one.

Incidentally, the Undo Wizard's Savepoints and Trash tabs throw ReferenceErrors. The inventory, stacking tree, conflict list and a single proposed token scale (with a mapping for every current value) are in zindex_inventory.md.

**Coverage**

- STATIC (complete): every z-index and context-forming property in static/fablegear.css and static/shared/ui_extras.css, extracted by a brace-aware parser (scratchpad/audit/zindex-static/parse_css.py, inventory.py, raw output z_raw.tsv and sc_raw.tsv). Every JS style or z-index assignment (grep for cssText, style.zIndex, style.transform/opacity, Object.assign(...style)). Inline styles in templates/**/*.html. A Jinja-rendered index.html DOM outline (render_tree.py, tree_index.txt) to place each layer in its parent containers. Every opener and closer function for each surface, to classify layers as live, dead-DOM or dead-opener.
- LIVE (sandbox sbx-zindex, port 5100, configured; headless Chromium at /opt/pw-browsers/chromium; viewports 1440x900, 1280x800 and 1024x700). Scripts: live_probe.py through live_probe5.py; results in results.json through results5.json; 41 screenshots in docs/audits/2026-10-05/screens/zindex-static/.
- Checked live: computed palette z-index; palette under Settings plus a blind Enter executing an action; update prompt under Settings and Welcome; staging above the USB export modal at 3 viewports; pipeline gate and rename preflight unrenderable (rename via the real 'Probe Ambiguities' click and the real 'Run Clean File Names' click with Dry Run off, plus a sha256 listing of the music folder before and after); fg-back-hint unstyled and never removed; summary pills 8px wide and covered; Escape interaction matrix (DB panel, Undo panel, Settings); stray Escape making Settings and Report self-close; Undo tab ReferenceErrors; DB and Undo panels stacked in the same slot; left-rail occlusion by the scan bar and log panel at 3 viewports; drive flyout trapped and covered by staging; file browser under the DB backdrop via the real DB-panel 'Browse…' click; Chop Shop file browser covering rail tabs and the banner dock overlapping the terminal; safety banners hidden in the Record Room; long toast width at 1024.
- NOT exercised end-to-end, with reasons:
- (a) A real confirm-mode pipeline run. I invoked the gate with the same function and pre-state that runPipeline uses (setAllButtons(true) then _showPipeGate). The hang after step 1 is inferred from pipeline.js:1067.
- (b) A real update feed. _rkbShowUpdateModal was called directly; it is the function both fablegearUpdateCheck and fablegearUpdateCheckManual call.
- (c) Real hotplug or drive-offline events. The banners were shown with the same style.display assignments drives.js, updates.js and health.js use.
- (d) WKWebView and macOS font metrics, pywebview drag-and-drop, and the native folder picker. On Linux, /api/pick-folder fails, which is exactly the fallback path that opens the file browser. On macOS that path only runs when the native picker is unavailable.
- (e) Glossary cards and the FableGo panel. They have no entry point in the DOM, so they were analysed statically only.
- (f) fpcalc is not installed, but nothing in this assignment needs it.
- The sandbox server on 5100 was stopped at the end (PIDs 10557 and 2667 killed; port returns 000). No product code was edited.

**Findings**

#### [HIGH · demonstrated] A stray Escape arms a stale close callback, so the next Settings or Report modal closes itself

- **ID:** `zindex-static-escape-stale-close` · **Area:** ui-layering · **Tool/surface:** Settings modal, Report modal (tool completion report)
- **Expected:** Escape closes only the top-most open layer. Calling close*() on an already-hidden modal is a no-op. Opening a modal never triggers a leftover close.
- **Actual:** Escape is pressed constantly: on the boot Welcome, the DB panel, the drive flyout, the palette. Each press leaves a pending close on Settings and on the Report modal. The next tool-completion report flashes for about 280ms and then disappears into an unreachable 8px pill. The next Settings open closes itself. The user loses the report and cannot reopen it.
- **Evidence:** Code path:
- info.js:266-270 calls closeSettings() and closeReportModal() unconditionally on every Escape keydown.
- Both run _sbAnim(el,'sb-modal-out',...) (modals.js:60-64; settings.js:86-90; modals.js:154-168). That sets el.style.animation and adds a once-listener for animationend.
- When the modal is hidden (its backdrop is display:none), no animation runs and the listener stays pending.
- On the next open, sb-modal-in fires animationend, and the stale close callback (_sbFadeBd(...,false) plus _addOrUpdateSummaryPill) fires with it.

Live (live_probe2.py, results2.json):
- Control with no Escape: settings_open_after_1500ms=true.
- Repro: at boot, the auto-opened Welcome modal is visible; press Escape. Welcome stays open (welcome_visible_after_escape=true) because Welcome has no Escape handler. After the Escape, settings-modal.style.animation was already 'sb-modal-out … forwards', and so was report-modal's.
- Close Welcome with its button, then click the left-rail Settings button: settings_open_at_150ms=true, settings_open_at_1500ms=false.
- Press Escape, then openReportModal('Audit — Library Health Check', …): report_open_at_150ms=true, report_open_at_1500ms=false. The report becomes a pill titled 'Step Complete' with rect 8x151 (see the summary-pills finding).
- Screenshots: screens/zindex-static/M1_settings_opening_after_stray_escape_150ms.png, M2_settings_self_closed_after_stray_escape_1500ms.png, M3_report_modal_self_closed_after_stray_escape_1500ms.png.
- **Fix:** 1. Guard every close: if (backdrop.classList.contains('hidden')) return; at the top of closeSettings, closeReportModal, closeWelcome, closeMcpPanel and choosePath, the same as closeHealthModal at health.js:22-24.
2. In _sbAnim, remove any previous animationend handler before adding one (keep a handle on el._sbAnimCb). Alternatively, use el.getAnimations() and finished promises, so a hidden element resolves immediately: if getComputedStyle(el).display==='none', call cb() synchronously.
3. Replace the Escape listeners at info.js:266, db_rail.js:55, boot.js:10, drives.js:218 and ui_extras.js:195 with one layer-stack Escape handler that closes only the top layer. Add Escape support to Welcome.

#### [HIGH · demonstrated] Rename preflight modal (z 13051) can never render, so Rename with Dry Run off is a silent no-op

- **ID:** `zindex-static-rename-preflight-unrenderable` · **Area:** chop-shop-tool · **Tool/surface:** Rename (step-rename) — Probe Ambiguities / Run Clean File Names
- **Expected:** Clicking 'Run Clean File Names' or 'Probe Ambiguities' shows the preflight decisions modal above the docked tool modal. After Apply, the rename runs.
- **Actual:** Nothing visible happens: no modal, no toast, no log line. On any library where the probe finds ambiguous names (5 of 11 synthetic tracks here), a non-dry-run single-folder rename can never execute. The only paths that bypass it are undocumented: select two or more folders (preflight skipped), or use the Pipeline.
- **Evidence:** Markup:
- #rename-learn-backdrop and #rename-learn-modal are siblings of #step-rename inside #chop-card-stack (templates/partials/physical_library/file_rename.html:56-73, included at index.html:215, inside div#chop-card-stack in main#chop-shop-workspace).
- In the Chop Shop, #chop-card-stack is display:none (fablegear.css:6213 and 6921). In the Record Room, the whole workspace is display:none !important (fablegear.css:840).
- tool_modal.js:56 moves only the .card into the docked modal, not the preflight modal.
- runRename (runners.js:470-477): Dry Run off with one folder calls runRenameWithPreflight, which calls /api/rename/probe. If it returns candidates, it calls openRenamePreflightModal (runners.js:528, 601-602) and returns without renaming.

Live (live_probe.py E, live_probe2.py):
1. In the Chop Shop, click the rename rail tab, add the music folder pill and click #btn-rename-probe. /api/rename/probe returned 200 with 5 candidates. #rename-learn-modal got .open, but its rect is 0x0, checkVisibility()=false and its ancestor chain is ['div#rename-learn-modal.open [pos:fixed z:13051]', 'div#chop-card-stack [DISPLAY:NONE]']. #rename-learn-apply-btn is also 0x0.
2. Uncheck Dry Run and click #btn-rename. The only request was /api/rename/probe, with no /api/run/rename. isRunning=false, the log was empty, and the sha256 listing of all 12 files in the music folder was unchanged.

Screenshots: E_rename_preflight_modal_invisible_chop_1440.png, E2_rename_dry_run_off_click_does_nothing_chop_1440.png.
- **Fix:** 1. Move #rename-learn-backdrop and #rename-learn-modal out of #chop-card-stack to direct children of <body> (for example, a partials/modals.html include after the room containers), or have JS portal them on open with document.body.appendChild.
2. Give them the modal tier (--z-modal in the proposed scale).
3. Add a regression check: after openRenamePreflightModal, assert modal.checkVisibility() is true in both rooms.
4. As a belt-and-braces measure, if the modal is not visible, fall back to showing the candidates in the report modal, or toast 'Preflight needs review'.

#### [HIGH · demonstrated] Pipeline confirm-between-steps gate is invisible and its buttons are disabled, so a confirm-mode pipeline traps the user

- **ID:** `zindex-static-pipeline-gate-unrenderable` · **Area:** chop-shop-tool · **Tool/surface:** Pipeline Wizard (confirm between steps)
- **Expected:** After each step, a visible gate offers Finish, Re-do, Skip and Stop, and those buttons are enabled.
- **Actual:** After step 1, the pipeline waits forever on an invisible promise. isRunning=true and every .btn is disabled, so the Chop Shop is locked. The user is trapped unless they discover that reopening and closing the Pipeline Wizard stops the run.
- **Evidence:** Markup and code:
- #pipe-confirm-gate (z 1100, fixed) is in file_rename.html:78-93, inside #chop-card-stack, which is display:none in the Chop Shop (fablegear.css:6213 and 6921) and in the Record Room (fablegear.css:840).
- runPipeline calls setAllButtons(true) (pipeline.js:998; scan_bar.js:373-375 disables every .btn). In confirm mode it then awaits _showPipeGate (pipeline.js:1067).
- The gate's buttons #pipe-btn-finish, #pipe-btn-redo and #pipe-btn-skip are .btn, and _showPipeGate never re-enables them (pipeline.js:873-912).

Live (live_probe.py D, results.json D_pipeline_gate). In the Chop Shop I ran setAllButtons(true); _showPipeGate(true,'Tag Tracks','Normalize',[…]), the same pre-state as runPipeline:
- gate_probe: display:none, rect 0x0, checkVisibility=false, ancestors ['#pipe-confirm-gate [DISPLAY:NONE]', '#chop-card-stack [DISPLAY:NONE]'].
- #pipe-btn-finish.disabled=true.
- The only exit found: open the Pipeline Wizard from the rail (a .step-tab, so not disabled) and close it. closePipelineWizard (pipeline.js:40-44) resolved the gate with 'stop' (gate_resolved_after_wizard_close='stop').

Screenshot: D_pipeline_confirm_gate_invisible_chop_1440.png. A full end-to-end confirm-mode run was not executed; the hang is inferred from pipeline.js:1067.
- **Fix:** 1. Move #pipe-confirm-gate to <body> (or portal it on show) at the --z-float tier.
2. In _showPipeGate, re-enable the gate's own buttons (gate.querySelectorAll('button').forEach(b=>b.disabled=false)), or scope setAllButtons to exclude #pipe-confirm-gate.
3. Add a visible Stop control in the docked tool modal while awaiting the gate.

#### [HIGH · demonstrated] Undo Wizard: the Savepoints and Trash tabs throw ReferenceError, and the Timeline Details and Restore-checkpoint buttons call undefined functions (incidental)

- **ID:** `zindex-static-undo-tabs-undefined` · **Area:** undo-revert · **Tool/surface:** Undo Wizard panel
- **Expected:** The Savepoints tab lists DB snapshots with Restore. The Trash tab lists pruned files with Restore. Timeline Details opens a styled overlay.
- **Actual:** Both restore surfaces are blank and throw errors. Details and Restore checkpoint throw on click. The Database tab never shows rows.
- **Evidence:** Code:
- undo.js:39-40 calls undoLoadSavepoints() and undoLoadTrash().
- undo.js:279 and 284 call undoViewJobDetail and undoRestoreCheckpoint.
- `grep -rn` across the repo finds no definition of any of the four. undo.js:318-319 says: 'To complete the hardening, apply this same … logic to the _renderSavepoints and _renderTrashFolders functions'.
- undo.js:57-60 (Database tab) creates a row per transaction but never appends it.

Live (live_probe.py K): clicking #undo-tab-savepoints and #undo-tab-trash logged 'PAGEERROR: undoLoadSavepoints is not defined' and 'PAGEERROR: undoLoadTrash is not defined'. Both lists stayed empty. Screenshot: K_undo_wizard_savepoints_trash_tabs_1440.png.

Also inferred: the overlay that _undoShowDetail builds (#undo-detail-modal.undo-detail-overlay) has no CSS anywhere, so if it were wired up it would render unstyled in normal flow.
- **Fix:** 1. Implement undoLoadSavepoints and undoLoadTrash (against /api/undo/savepoints and /api/undo/trash), plus undoViewJobDetail and undoRestoreCheckpoint, or remove the tabs and buttons until they exist.
2. Append the rows in undoLoadDatabase.
3. Add CSS for .undo-detail-overlay at the --z-modal-2 tier, since it opens from the Undo drawer.
4. Other auditors (undo-revert) should own the backend verification.

#### [MEDIUM · demonstrated] The update prompt (z 9000) opens underneath Settings (10000) and Welcome (10100), which are its only manual entry points, and under the boot Welcome

- **ID:** `zindex-static-update-prompt-under-modals` · **Area:** ui-layering · **Tool/surface:** FableGear update prompt (#rkb-update-overlay)
- **Expected:** Clicking 'Check for Updates' visibly shows the update prompt above the modal it was launched from.
- **Actual:** When an update is found, nothing visible happens: there is no toast on that path, and the prompt is hidden behind the modal. The prompt appears only after the user closes Settings or Welcome. At boot it is hidden behind the auto-Welcome.
- **Evidence:** Code:
- #rkb-update-overlay is at z 9000 (fablegear.css:5848).
- The 'Check for Updates' buttons are inside #settings-modal (index.html:342) and #welcome-modal (index.html:616). They call fablegearUpdateCheckManual, which calls _rkbShowUpdateModal (updates.js:89-92 and 104-134) without closing the parent modal.
- The automatic check runs at 1000ms (updates.js:284), and Welcome auto-opens at 1200ms (launcher.js:27-31).

Live (live_probe.py B):
- With Settings open, _rkbShowUpdateModal({…}): #rkb-update-go hit-test = label.settings-excl-label (covered). In the 4x4 grid over #rkb-update-dialog, all 16 points hit settings elements.
- With Welcome open: the hit is img.fgwin-card-art (covered).
- With staging open, the staging panel (9700) is above the update scrim; .staging-panel-close is still clickable.
- Screenshots: B1_update_dialog_hidden_under_settings_1440.png, B2_update_dialog_hidden_under_welcome_1440.png, B3_staging_panel_above_update_overlay_1440.png.
- **Fix:** 1. Give the update prompt the stacked-modal tier (--z-modal-2, above --z-modal), or open it through a layer stack so it gets depth+1.
2. Alternatively, close Settings or Welcome before showing it.
3. Delay the boot update check until Welcome is dismissed, or queue it.

#### [MEDIUM · demonstrated] The command palette's effective z is 9000 (the 13000 rule is overridden). It opens under every modal and still captures typing, so Enter runs actions blind

- **ID:** `zindex-static-palette-z-overridden` · **Area:** ui-layering · **Tool/surface:** Command palette (⌘K / Ctrl+K)
- **Expected:** The palette appears above whatever is open, or is refused or deferred while a modal is open. Only one source of truth for its styles.
- **Actual:** The palette is invisible under any modal, but keyboard input and Enter still execute palette actions, such as switching rooms or opening DB tools, out of sight behind the modal.
- **Evidence:** CSS:
- fablegear.css:7931-7937 declares #fg-palette-backdrop{z-index:13000}, with a comment that it 'was created by ui_extras.js with zero CSS'.
- ui_extras.css:154-160 declares the same ID with z-index:9000. ui_extras.css loads after fablegear.css (index.html:8-9), so it wins at equal specificity. It also overrides padding-top (14vh) and the input font-size (30px vs .95rem). Its header (ui_extras.css:4) claims 'zero overrides of existing rules'.

Live (live_probe.py A):
- Computed z-index is '9000'.
- Open Settings, press Ctrl+K: _fgPalState.open=true and document.activeElement is input#fg-palette-input, but the hit-test at its center is div.settings-header (covered).
- Type 'dead files' and press Enter: _dbPanelActive='dead-files', the space switched to 'chop', the DB panel opened, and Settings stayed open on top.
- With staging open, the palette is partly covered (2 of 36 grid points at 1440).
- Screenshots: A1_palette_hidden_under_settings_1440.png, A2_palette_blind_enter_opened_dbpanel_behind_settings_1440.png, A3_staging_panel_over_command_palette_1440.png.
- **Fix:** 1. Delete one of the two #fg-palette-backdrop/#fg-palette rule sets; keep the ui_extras.css copy, which is the one users see today.
2. Set z-index:var(--z-palette), above --z-modal.
3. In fgPaletteOpen, either close the top modal first or open through the layer stack.
4. Fix the 'zero overrides' comment.

#### [MEDIUM · demonstrated] The non-modal Staging panel (z 9700) floats above modals: USB Export (601), the update prompt (9000) and the palette (9000)

- **ID:** `zindex-static-staging-above-modals` · **Area:** ui-layering · **Tool/surface:** Staging Queue panel
- **Expected:** A modal's scrim covers every non-modal layer, including the staging panel.
- **Actual:** The staging panel covers the USB Export header and controls, and its buttons remain interactive while the export modal is meant to be modal.
- **Evidence:** Code:
- .staging-panel is z 9700, fixed, with transform and opacity (fablegear.css:581-608). It only closes from its own ✕ or the left-rail toggle (staging.js:120-133); there is no outside-click or close-on-modal-open.
- #le-export-backdrop/#le-export-modal are z 600/601 (fablegear.css:5060-5075).

Live (live_probe.py C), with toggleStagingPanel() then leOpenExportModal() in the Record Room:
- At 1280x800, 2 of 36 grid points in the export modal hit staging.
- At 1024x700, 8 of 36 hit staging, including #staging-goto-chop 'Open Chop Shop →', which stays clickable over the 'modal'.
- At 1440x900 the empty-queue staging panel ended exactly at the modal's top edge. A populated queue is taller (max-height calc(100vh - 52px)) and overlaps more.
- Screenshots: C_staging_over_usb_export_1024x700.png (export header covered), C_staging_over_usb_export_1280x800.png and C_staging_over_usb_export_1440x900.png.
- **Fix:** 1. Demote the staging panel to --z-float (below --z-modal-scrim) in the proposed scale.
2. Alternatively, have every modal open call closeStagingPanel(), or route it through the layer stack.

#### [MEDIUM · demonstrated] The left rail (175) sits under the full-width #scan-bar (190) and the Record Room #log-panel (200): the Splash button is always covered, and Undo, Studio and Splash are covered while the log is open

- **ID:** `zindex-static-left-rail-under-bottom-bars` · **Area:** ui-layering · **Tool/surface:** Left activity rail (#left-panel): Undo Wizard, Studio, Splash buttons
- **Expected:** The app rail is always reachable, and the bottom bars start to the right of it (left: var(--left-panel-w)), as they already do in the Chop Shop.
- **Actual:** With the log drawer open in the Record Room, the Undo Wizard entry point is unreachable. The Splash button is permanently cut off by the scan bar. Workaround: close the log from its own bar.
- **Evidence:** CSS:
- #left-panel is fixed z 175, from top: titlebar to bottom 0 (fablegear.css:320-332).
- #scan-bar is fixed z 190 with left:0 (fablegear.css:1396-1397).
- #log-panel is fixed z 200 with left:0 (fablegear.css:1498-1499).
- body.log-open lifts the scan bar to bottom: var(--log-h) (fablegear.css:1424).
- The .lp-bottom buttons are in header.html:53-70.

Live (live_probe.py F, results.json F_rail_*), Record Room:
- Log closed: the center of '.lp-bottom .lp-btn:last-child' (Splash) hits div#scan-bar (covered) at 1440x900, 1280x800 and 1024x700.
- After reopenLog(): #undo-wizard-btn, #fg-booth-btn and Splash all hit div#log-output (covered) at all 3 viewports.
- Screenshots: F1_left_rail_bottom_vs_scanbar_{1440x900,1280x800,1024x700}.png and F2_left_rail_bottom_vs_logpanel_*.png.
- **Fix:** 1. Set #scan-bar and Record Room #log-panel to left: var(--left-panel-w) (the Chop Shop already does this at 6280-6293).
2. Also raise #left-panel above the bars: --z-chrome (110) above --z-chrome-bar (100) in the proposed scale.

#### [MEDIUM · demonstrated] The file-browser drawer (158) sits under the transparent DB/Undo click-catchers (159/160), so the DB panel's own 'Browse…' fallback opens an unusable drawer

- **ID:** `zindex-static-fb-drawer-under-db-scrim` · **Area:** db-tool · **Tool/surface:** DB panel (Dead Files, Import, Fix Paths…) plus the file browser #fb-panel
- **Expected:** The drawer the app tells the user to drag from is interactive while the DB panel it drags into is open.
- **Actual:** Every click or drag-start on the drawer lands on the invisible DB backdrop and closes the DB panel. The suggested workflow is impossible. On macOS this fallback runs only when the native picker is unavailable.
- **Evidence:** Code:
- #fb-panel z 158 (fablegear.css:3041-3054); #db-panel-backdrop z 159, inset 0, transparent, closes the DB panel on click (fablegear.css:4014-4022; db_rail.js:34-35); #undo-panel-backdrop z 160 (fablegear.css:4043-4052).
- pickFolderFor (dnd.js:254-265): when the native picker returns nothing, it toasts 'Use the file browser sidebar to navigate to your folder, then drag it here.' and calls toggleFileBrowser(). db_rail.js:47-51 explicitly expects the file browser and DB panel to coexist.

Live (live_probe4.py, results4.json): openDbPanel('dead-files'), then a real click on the panel's 'Browse…' button (onclick=pickFolderFor('dead-files-pills')):
- fb_open=true and db_open=true.
- #fb-header hit-test = div#db-panel-backdrop.open, and all 16 of 16 grid points over #fb-list hit the backdrop.
- Screenshot: P1_file_browser_under_db_backdrop_1280x800.png (toast plus a visible but dead drawer).
- **Fix:** 1. Put #fb-panel in the drawer tier above the drawer scrim (--z-drawer > --z-drawer-scrim).
2. Alternatively, make the DB backdrop start at left: calc(var(--left-panel-w) + var(--sidebar-w)) when body:has(#fb-panel.fb-open).

#### [MEDIUM · demonstrated] Hotplug, drive-offline and health banners never render in the Record Room (the default room)

- **ID:** `zindex-static-safety-banners-hidden-in-record-room` · **Area:** ui-layering · **Tool/surface:** #chop-banner-dock: #hotplug-banner, #drive-offline-banner, #health-panel
- **Expected:** 'Music library drive is offline' and 'New drive connected' are visible wherever the user is, since the Record Room also reads tracks from those drives.
- **Actual:** In the Record Room the user gets no drive-offline, hotplug or health signal until they switch to the Chop Shop.
- **Evidence:** Markup and CSS:
- #chop-banner-dock and its three banners are inside main#chop-shop-workspace (index.html:120-160).
- main#chop-shop-workspace is display:none !important in body.fg-space-record (fablegear.css:840).
- updates.js:406-… (_updateDriveBanner), drives.js:40-50 (hotplug) and health.js:35-45 only toggle style.display on these nodes.

Live (live_probe3.py, results3.json), with the same display assignments the code uses, at 1024x700:
- Record Room: all three have rect 0x0, checkVisibility=false, ancestor 'main#chop-shop-workspace [DISPLAY:NONE]'.
- Chop Shop: all three are visible in the dock (z 80).
- Screenshots: N_safety_banners_record_1024x700.png, N_safety_banners_chop_1024x700.png.
- **Fix:** 1. Move the banner dock out of main#chop-shop-workspace into a room-agnostic container: fixed, z --z-room-raised, positioned under the titlebar in the Record Room and docked as now in the Chop Shop.
2. Alternatively, mirror the drive-offline state into a titlebar pill.

#### [MEDIUM · demonstrated] Session 'reopen summary' pills are 8px wide, sit in normal flow and are covered by both rooms, so a dismissed report cannot be reopened

- **ID:** `zindex-static-summary-pills-unreachable` · **Area:** ui-layering · **Tool/surface:** Report modal → summary pill (#session-pills-container)
- **Expected:** After closing a report, a reachable 'reopen summary' affordance exists, for example in the left rail or titlebar.
- **Actual:** The pill is invisible and covered. Session-only reports, which have no file on disk ('Session summary only — no file saved for this step'), are lost once dismissed. This is worse combined with the self-closing report from the stale-Escape finding.
- **Evidence:** Markup and CSS:
- #session-pills-container is a non-positioned body child (index.html:280) styled as a column for the removed right nav bar (fablegear.css:2449-2460).
- .summary-pill has width calc(var(--nav-bar-right-w) - 8px) = calc(0px - 8px) (fablegear.css:2476).
- closeReportModal always converts the report into a pill (modals.js:154-168, 37-54).

Live (live_probe.py H):
- Chop Shop: pill rect [756,185,8,108]; the hit at its center is div#tool-float-modal (covered).
- Record Room: pill rect [756,37,8,108]; the hit is div.le-header (covered).
- In J1_escape_closed_dbpanel_and_docked_tool_chop_1440.png the pill shows only as a vertical 8px strip of letters ('Step 1 Summary') when the docked modal is closed.
- Screenshots: H_summary_pill_chop_1440.png, H_summary_pill_record_1440.png.
- **Fix:** 1. Move #session-pills-container into #left-panel, above .lp-bottom, or into the titlebar pills.
2. Give .summary-pill a real width (for example 64px or auto).
3. Alternatively, list session reports in the Undo Wizard timeline.

#### [MEDIUM · demonstrated] Escape closes layers underneath instead of the top one: one Escape closes the DB panel and the docked Chop Shop tool; with Undo or Settings open, it closes the tool beneath

- **ID:** `zindex-static-escape-closes-underlying-layers` · **Area:** ui-layering · **Tool/surface:** Global Escape handling (7 independent keydown listeners)
- **Expected:** Escape closes exactly the top-most layer, in stack order: popover, then modal, then drawer. It never closes the Chop Shop's docked workspace, which state.js:48-55 says must never be empty.
- **Actual:** Multiple layers close at once. The Undo drawer ignores Escape while the surface beneath it closes, and the Chop Shop is left with an empty work zone.
- **Evidence:** Code:
- Listeners: info.js:266 (closeSettings and closeReportModal, unconditionally), db_rail.js:55-58 (closeDbPanel if _dbPanelActive; closeToolFloatModal if _toolFloatActive, both in the same event), boot.js:10-19 (closeToolFloatModal if display:flex), drives.js:218-223, ui_extras.js:195, usb_export.js:184 and deck.js:784.
- None of them stops propagation or checks which layer is on top. There is no Escape for the Undo panel or Welcome.

Live (live_probe.py J), Chop Shop:
- Docked tool step-process open, then openDbPanel('audit'), then Escape: dbPanel=null, tool=null, tfmDisplay='none'.
- openUndoPanel(), then Escape: undoOpen=true, but the docked tool beneath closed.
- openSettings(), then Escape: Settings closed and the docked tool closed.
- The upper zone is then empty (the card stack is display:none) while the Tagger rail tab still shows active.
- Screenshots: J1_escape_closed_dbpanel_and_docked_tool_chop_1440.png, J2_escape_left_undo_open_closed_tool_beneath_chop_1440.png.
- **Fix:** 1. Use one layer stack (fgLayer.open/close) and one keydown listener that closes only stack.top, with stopImmediatePropagation.
2. In the Chop Shop, treat the docked tool modal as part of the room rather than a layer, so remove it from the Escape handling in db_rail.js and boot.js.
3. Register the Undo panel and Welcome in the stack.

#### [MEDIUM · demonstrated] Drives flyout (z 9800) is trapped in #left-panel's stacking context (175) and gets covered by the staging panel

- **ID:** `zindex-static-drive-flyout-trapped` · **Area:** ui-layering · **Tool/surface:** Left rail → Drives flyout (#drive-list.lp-drives-flyout)
- **Expected:** A flyout or dropdown opened by the user is above panels it overlaps.
- **Actual:** The flyout renders underneath the staging panel and any root layer above 175 that overlaps it. Its 9800 has no effect.
- **Evidence:** Markup and CSS:
- #drive-list is a child of #left-panel (header.html:43).
- #left-panel is fixed with z-index 175, so it forms a stacking context (fablegear.css:320-332).
- The flyout rule sets fixed and z 9800 (fablegear.css:440-455), which can only order it inside #left-panel. It competes at 175 at the root.

Live (live_probe.py F/G):
- The flyout's ancestor chain is ['div#drive-list.lp-drives-flyout [pos:fixed z:9800, backdrop-filter]', 'div#left-panel [pos:fixed z:175]'].
- With the staging panel open, 25 of 25 grid points over the flyout hit staging at 1024x700, and 5 of 25 at 1280x800.
- Screenshots: G2_drive_flyout_vs_staging_1024x700.png and _1280x800.png. The log panel did not reach the flyout at the tested heights (G1_*).
- **Fix:** 1. Portal the flyout to <body> on open (document.body.appendChild(list), then restore it on close), or render a separate body-level flyout node.
2. Use --z-popover. Never put fixed popovers inside a z-indexed fixed ancestor.

#### [MEDIUM · demonstrated] Chop Shop with the file browser open: the drawer (158) covers the first rail tool tabs (30), and the banner dock (80) is not shifted, so it overlaps the live-terminal header

- **ID:** `zindex-static-chop-fb-open-rail-and-dock-overlap` · **Area:** ui-layering · **Tool/surface:** Chop Shop rail plus #chop-banner-dock plus #fb-panel
- **Expected:** Opening the drawer reflows every Chop Shop surface consistently. Rail tools stay reachable and banners don't sit on the terminal.
- **Actual:** The Tag Tracks, Rename and Convert rail tabs are unreachable while the drawer is open. The safety banner dock covers the 'LIVE TERMINAL — OUTPUT' header.
- **Evidence:** CSS:
- body:has(#fb-panel.fb-open) shifts #tool-float-modal, #log-panel and #chop-readout right by --sidebar-w (fablegear.css:6273-6275, 6295-6297, 6322-6324 and duplicates at 6981-7040).
- There is no such rule for .workflow-rail (fablegear.css:853-869, left: var(--left-panel-w)) or for #chop-banner-dock (fablegear.css:6215-6227 and 6925-6937).

Live (live_probe5.py, results5.json), Chop Shop with the hotplug banner shown and Ctrl+B:
- Rail tabs covered by div.fb-empty: step-process and step-rename at 1440x900; step-process, step-rename and step-convert at 1024x700. With the drawer closed, none are covered.
- The #log-panel .log-bar hit-test is span#hotplug-banner-msg: 36 of 36 grid points covered at 1024x700, 24 of 36 at 1440x900.
- Screenshots: Q_chop_file_browser_open_rail_and_banner_1024x700.png and _1440x900.png.
- **Fix:** 1. Add body:has(#fb-panel.fb-open).fg-space-chop rules for .workflow-rail (left) and #chop-banner-dock (left: calc(var(--left-panel-w) + var(--sidebar-w) + var(--chop-terminal-w) + 14px)).
2. Alternatively, make #fb-panel push the whole room via a single --room-left variable used by every docked surface.

#### [MEDIUM · inferred] Interrupted-run resume banners are injected into cards in the display:none card stack, so a resume offer is invisible until the user opens that specific tool

- **ID:** `zindex-static-resume-banners-hidden` · **Area:** session-resume · **Tool/surface:** Per-tool resume banners (_showToolResumeBanner)
- **Expected:** After a restart, any interrupted tool run is surfaced where the user lands, for example a rail badge or a startup prompt with a starting-point reference.
- **Actual:** The resume offer for normalize, convert, duplicates, organize, novelty or rename exists only inside a hidden card. It appears only if the user happens to open that tool.
- **Evidence:** - _showToolResumeBanner (pipeline.js:598-629) prepends .tool-resume-banner into card.querySelector('.card-form') of each #step-* card.
- _initToolCheckpoints (pipeline.js:745-753) runs for 7 tools.
- Cards live in #chop-card-stack, which is display:none in the Chop Shop (fablegear.css:6213 and 6921); the whole workspace is display:none in the Record Room (fablegear.css:840).
- Only the card borrowed into the docked modal is visible (tool_modal.js:56). On entering the Chop Shop that is step-process (state.js:52-55).
- No rail badge or global indicator exists for pending checkpoints.
- Not verified live: no interrupted run was staged in this pass.
- **Fix:** 1. Add a rail badge per tool with a checkpoint, and/or a single body-level 'Resume interrupted runs' banner in the room-agnostic dock (see the safety-banners finding).
2. Keep the in-card banner as a secondary surface. The session-resume auditor should confirm with a staged checkpoint.

#### [LOW · demonstrated] The DB panel (498) and Undo panel (499) share the same right-hand slot and can be open together. Undo hides the DB panel and its ✕, and the DB panel (top:0) covers the titlebar

- **ID:** `zindex-static-undo-db-panel-same-slot` · **Area:** ui-layering · **Tool/surface:** #db-panel / #undo-panel
- **Expected:** Side drawers are mutually exclusive, or stack with a visible relationship. The titlebar drag region is not covered.
- **Actual:** Two stacked drawers. Closing Undo reveals a DB panel the user may not know is open. In the frameless pywebview window, the top 28px of the DB panel replace the titlebar.
- **Evidence:** CSS and code:
- Both are right:0, width var(--db-panel-w) (fablegear.css:3906-3920 and 4025-4041). #db-panel uses top:0 and z 498, which is above #titlebar at 200.
- openUndoPanel (undo.js:6-12) doesn't close the DB panel, and the left-rail Undo button (175) is above the DB backdrop (159).

Live (live_probe.py L):
- After openDbPanel('audit') and clicking #undo-wizard-btn, both are open.
- .db-panel-close is covered by div#undo-panel.open.
- The point (W-50,10) is .db-panel-header, so the titlebar's right side is covered.
- #nav-btn-rb-relocate in the rail is covered by the undo panel nav.
- Screenshot: L_undo_panel_stacked_over_db_panel_chop_1440.png.
- **Fix:** 1. Close the other drawer on open (openUndoPanel calls closeDbPanel, and vice versa).
2. Start #db-panel at top: var(--titlebar-h), as #undo-panel does.
3. Put both on --z-drawer.

#### [LOW · demonstrated] The 'Back to Record Room' hint (#fg-back-hint) has no CSS: it renders as an unstyled static line under the banner dock and is never removed

- **ID:** `zindex-static-back-hint-unstyled` · **Area:** ui-layering · **Tool/surface:** Chop Shop entry hint
- **Expected:** A small fixed popover next to the logo that disappears after 10 seconds.
- **Actual:** An invisible or garbled full-width line that leaks a DOM node per room switch until the next space change.
- **Evidence:** Code:
- state.js:66-86 appends div.fg-back-hint to <body> with inline top/left.
- No .fg-back-hint rule exists in any CSS file (grep), so position is static and top/left are ignored.
- dismissBackToRecordHint() without the immediate flag (state.js:88-96) waits for animationend on a 'hiding' class that has no animation.

Live (live_probe.py I):
- After switching to the Chop Shop: position static, rect [80,193,1360,15], and the center hit is div.health-panel-header (covered).
- After 11s: exists=true, class 'fg-back-hint hiding'.
- The text is faintly visible in the terminal column in J1_escape_closed_dbpanel_and_docked_tool_chop_1440.png.
- Screenshot: I1_fg_back_hint_unstyled_chop_1440.png.
- **Fix:** 1. Add CSS: position:fixed; z-index:var(--z-popover); a pill style; and @keyframes for .hiding.
2. Or remove the node on a setTimeout instead of waiting for animationend.

#### [LOW · inferred] The FableGear update reminder and Homebrew banners are in normal flow, so the Record Room overlay (fixed z 5) covers them

- **ID:** `zindex-static-update-banners-under-record-room` · **Area:** ui-layering · **Tool/surface:** #fablegear-update-banner, #brew-banner
- **Expected:** The update reminder is visible after 'Skip' in either room.
- **Actual:** In the Record Room (the default room), the reminder banner is painted under the room surface.
- **Evidence:** - Both are non-positioned body children at the top of the flow (index.html:53, 61; fablegear.css:792-837).
- #library-editor-overlay is fixed at top: var(--titlebar-h), left: var(--left-panel-w), z 5 (fablegear.css:4394-4400), so it paints over in-flow content at y≥28.
- rkbUpdateSkip (updates.js:237-241) relies on the banner as the 'reminder' after Skip.
- The footer comment at fablegear.css:6128-6131 describes the same class of problem.
- Not verified live.
- **Fix:** Render the banners in the room-agnostic dock or as titlebar pills (fixed, --z-room-raised). Do not rely on body flow, since every surface is fixed.

#### [LOW · inferred] Glossary cards use an unbounded ++cardZ plus a 9997 !important hover (latent; currently unreachable)

- **ID:** `zindex-static-glossary-unbounded-cardZ` · **Area:** ui-layering · **Tool/surface:** Glossary / info cards (.gls-card)
- **Expected:** Raising a card reuses a bounded range within one tier.
- **Actual:** Monotonic growth across every tier, and a hover rule that inverts the order.
- **Evidence:** - info.js:184 sets cardZ=1000. info.js:232 sets the inline z to ++cardZ on open, and info.js:240 does ++cardZ on every mouseenter. It is never reset.
- fablegear.css:2933 sets .gls-card:hover{z-index:9997 !important}, the only !important z-index.
- After about 8,700 opens and hovers, the inline z exceeds the staging panel (9700) and then the modals (10000–13000). From then on, the !important hover rule lowers a hovered card to 9997.
- Currently unreachable: openCard and toggleCard have no DOM entry point (#info-hover-panel and #info-panel-list do not exist).
- **Fix:** 1. If revived: set z-index:var(--z-float) for all cards, and raise one by moving its DOM node last (parent.appendChild(card)) or by toggling a .gls-top class (--z-float + 1).
2. Drop !important. Otherwise delete info.js glossary code and CSS.

#### [LOW · inferred] About 15 dead or unreachable z-layers, 4 duplicated Chop Shop docked rules, and a duplicated palette rule set

- **ID:** `zindex-static-dead-and-duplicate-layers` · **Area:** ui-layering · **Tool/surface:** fablegear.css / ui_extras.css
- **Expected:** One rule per surface, and no unreachable layers occupying tier numbers.
- **Actual:** Dead layers hold 6 of the 10 highest values (13000, 12000, 10499, 10000, 10001, 99000). This inflates the scale and hides real ordering. Editing one duplicate copy silently does nothing.
- **Evidence:** Dead (no element):
- header z170 (fablegear.css:122)
- #nav-bar-right 180 (1985)
- .confirm-panel 13000 / #confirm-backdrop 10499 (1756/1815)
- #ckpt-modal-backdrop 12000 (2866)
- #fb-toggle-btn (3035)
- .toolkit-modal 10000 (5509)
- #fg-room-launcher 99000 (6757)

Dead opener:
- #path-backdrop 11000 (2397; only closed at utility.js:84)
- #fablego-backdrop/#fablego-panel 10000/10001 (3125/3137; only closeFableGo)
- .folder-dropdown 200 (2050; three opacity-0 nodes at index.html:275-277; toggleRightNavDropdown never called)
- .gls-card 1000 / 9997
- the floating #tool-float-modal 12000 and its backdrop 11999 (only docked mode is reachable)

Killed by !important: #tool-help-panel.open z70 (7082-7089) loses to #tool-help-panel{display:none !important} (6371). Live: with .open its computed display is 'none' (results.json L_panels).

Duplicates: the Chop Shop docked block at 6213-6324 is repeated at 6921-7040, with drift ('live terminal - ' vs '—'; readout --bg vs --bg-deepest). The comment at 6910-6918 claims the duplicates were removed. #fg-palette rules exist in both fablegear.css:7931-7960 and ui_extras.css:154-203.
- **Fix:** 1. Delete the dead selectors and the first copy of the Chop Shop docked block.
2. Keep one palette rule set.
3. Either delete #tool-help-panel (HTML, CSS, JS) or remove the !important at 6371.
4. Remove the floating-mode tool modal CSS and the drag code if docking is final.
5. Add a stylelint rule (declaration-property-value-allowed-list for z-index: /^var\(--z-/) to stop literals from returning.

#### [INFO · inferred] Several surfaces share a z value and are ordered only by DOM position (no z-index scale exists)

- **ID:** `zindex-static-tier-ties-dom-order` · **Area:** ui-layering · **Tool/surface:** Modal tiers
- **Expected:** A documented scale of CSS custom properties, plus a runtime stack for modal-over-modal.
- **Actual:** Ad-hoc literals. Today the ties happen to be harmless only because these modals rarely co-exist.
- **Evidence:** Ties:
- 10000: #settings-backdrop (2154), #health-modal-backdrop (5998), #fablego-backdrop (3125), .toolkit-modal (5509)
- 11000: #report-modal-backdrop (2820), #path-backdrop (2397)
- 12000: #pipeline-wizard-backdrop (3413), #mcp-panel-backdrop (7837), #tool-float-modal (5643), #ckpt-modal-backdrop (2866)
- 200: #titlebar, Record Room #log-panel, .folder-dropdown
- 55: Chop Shop #log-panel and #chop-readout

The values span 44 distinct numbers from 0 to 99000, with no tokens. Comments such as 'must exceed toolkit-modal (10000)' (1756, 1815) and 'must exceed floating tool modal (12000)' (1824) reference layers that are dead. The full mapping and proposed scale are in scratchpad/audit/zindex-static/zindex_inventory.md, sections A, D and D.1.
- **Fix:** 1. Adopt the token scale: --z-decor 0, --z-room 10, --z-room-raised 20, --z-chrome-bar 100, --z-chrome 110, --z-drawer-scrim 200, --z-drawer 210, --z-float 300, --z-popover 350, --z-modal 400, --z-modal-2 410, --z-palette 450, --z-splash 500, --z-tooltip 600, --z-toast 700. Map every current value as in inventory section D.1.
2. Add a tiny fgLayer stack that assigns calc(var(--z-modal) + depth*10) at open time.
3. Add a rule that no modal or popover may be placed inside a room container or a z-indexed fixed ancestor.

#### [INFO · demonstrated] Confirmed-good layering behaviors

- **ID:** `zindex-static-confirmed-good` · **Area:** ui-layering · **Tool/surface:** various
- **Expected:** n/a
- **Actual:** n/a
- **Evidence:** - .sb-toast (20000) is the top-most live layer: a toast rendered above the Settings modal in A1_palette_hidden_under_settings_1440.png. A 610px toast fits at 1024px (results3.json toast; O_long_toast_fits_1024x700.png).
- #chop-banner-dock (80) is above the docked tool modal (60), and boot.js:26-41 pushes the modal down by --chop-banner-h. With the drawer closed, no rail tab is covered and the banners don't overlap the form (results5.json rail_fb_closed = []).
- Report (11000) and Pipeline Wizard (12000) sit above Settings and Health (10000), and MCP (12000) sits above Settings (inferred from CSS; the MCP panel is opened from Settings).
- In onboarding, .ob-exit (101) is above #ob-update-banner (100), so 'Finish later' is never covered (inferred, onboarding.html:55 and 455).
- All z0/z1 decorations (.card::before, .drop-wrap::after, modal and body watermarks) use pointer-events:none and steal no clicks (fablegear.css:1081-1083, 2985 and 2147-2149).
- **Fix:** Keep these properties when migrating to the token scale: toast always last, dock above the docked modal, and the onboarding exit above its banner.

## Record Room + global chrome click-through

_Auditor: `ui-live-record`_

Live click-through of the Record Room and global chrome on sandbox ui-record (port 5102, configured, /api/library/db/sync gave 11 tracks). I drove headless Chromium at 1440x900, 1280x800 and 1024x700 and saved about 172 screenshots, each with a JSON probe dump (fixed/absolute layers with z-index, pairwise overlaps, elementFromPoint occlusion, clipping), plus focused repro scripts.

Verdict: the base Record Room layout is mostly sound. Settings fits at 1024x700. The drive flyout closes on Escape and gives focus back to its trigger. The export modal closes on a backdrop click. But the "undo / revert" safety net the user cares about is broken in the UI, and the layer system has no shared stack or Escape model.

Most serious, all demonstrated:
- The Undo Wizard Savepoints and Trash tabs call functions that don't exist (ReferenceError). Trash is also off-screen at every viewport.
- One stray Escape press makes the next Settings open, or the next step-completion report, close itself. The report then turns into a dead summary pill.
- One click on a column header in a playlist permanently rewrites the curated playlist order, with no confirm and no undo.
- Deleting a FableGear playlist leaves no revert marker.
- Title edit in the default FableGear view always fails with 404 because it targets Rekordbox master.db. Each failure still writes a master.db savepoint.
- Opening the app with the music drive unplugged recreates the drive's folder tree, then toasts "Library audit complete ✓".
- The Decks toggle can't be clicked while idle.
- Drive-offline and health alerts are never visible in the Record Room.

Boundary: "All Music" mode and Ctrl+B both put Finder-style folder navigation inside the Record Room.

**Coverage**

- LIVE: sandbox /tmp/claude-0/-home-user-FableGear/9074577f-2e6a-5e16-bdce-ef5b26f1b16b/scratchpad/sbx-ui-record on port 5102 (configured). POST /api/library/db/sync imported 11 tracks. The server was killed at the end.
- LIVE probe: /tmp/claude-0/-home-user-FableGear/9074577f-2e6a-5e16-bdce-ef5b26f1b16b/scratchpad/audit/ui-live-record/probe.py, used by run_solo.py, run_flows.py, run_combo.py and analyze.py, plus targeted scripts: run_escape_bug.py, undo_tabs.py, playlist_sort.py, add_selected.py, race_check.py, fs_return.py, drive_offline2.py, rb_source.py, palette_combo.py, focus_trap.py, pill_visibility.py, chop_checks.py, file_browser_rr.py. Logs and JSON sit beside them.
- LIVE screenshots and per-state JSON (about 172 PNG): /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/<WxH>_<state>.png|json
- LIVE at all 3 viewports (1440x900, 1280x800, 1024x700): welcome modal, header and space switcher (button plus Ctrl+1/Ctrl+2), left rail, drive flyout, library table and filters, All Music / Library / Integrated modes, FableGear / Rekordbox / Device sources, staging panel (empty and with items), settings (all 5 tabs), Undo Wizard (all 5 tabs), USB export modal, log panel, decks (empty and loaded), command palette, playlist create/rename/delete, drag row to playlist, Add Selected, title edit, inline preview, toasts, resize-handle drag, combinations (staging+export, export+toast, undo+settings, palette over settings/staging/export, DB panel+staging, room switch with staging/undo/export/deck open), Escape and backdrop tests, focus trap, Tab walk.
- LIVE once at 1440x900 or 1280x800: drive-offline scenario (renamed sandbox Volumes/DJDRIVE; phantom dirs kept for evidence at sbx-ui-record/Volumes/DJDRIVE_phantom2 and DJDRIVE_phantom_created_while_offline), 10-load boot race, playlist header-sort persistence, Rekordbox-source title edit (reverted afterwards through the same PATCH API), Ctrl+B file browser in the Record Room.
- FORCED (no UI path exists): I opened the health modal with openHealthModal() and FableGo by adding .open. Both have no trigger in the Record Room. Layering was checked after forcing them open.
- NOT TESTABLE: WKWebView rendering and macOS font metrics. Native pywebview drag-and-drop (I used Playwright's synthetic HTML5 drag for row-to-playlist and row-to-deck; both worked). A real USB export: no Pioneer drive is mounted, so the modal shows 'No Pioneer USB drives found' and Export stays disabled. AAC/.m4a playback: Playwright Chromium has no proprietary codecs, so preview of Glitterball.m4a failed with MEDIA_ERR 4 (sandbox limit, not reported as a finding). macOS /Volumes permission semantics for the phantom-folder consequence (labelled speculative). A running Rekordbox process. Native folder pickers.
- SANDBOX SIDE EFFECTS (sandbox DBs only): FableGear DB now holds 6 'Double Submit' playlists, a 'Friday Set (curated order)' playlist, and 'Audit Set A' (created and deleted). 2 items are staged. The master.db title of track 1017 was edited and reverted. That added several master.backup_* savepoints.

**Findings**

#### [HIGH · demonstrated] Undo Wizard Savepoints and Trash tabs are dead: their loader functions do not exist

- **ID:** `ui-live-record-undo-loaders-missing` · **Area:** undo-revert · **Tool/surface:** Undo Wizard (Savepoints, Trash tabs)
- **Expected:** Savepoints lists DB snapshots with Restore. Trash lists pruned files with Restore. Together these are the documented undo path for Prune, Import, Normalize and Fix Paths.
- **Actual:** Both tabs throw a ReferenceError and render nothing. Pruned-file recovery and savepoint restore cannot be done from the app.
- **Evidence:** Clicked the Savepoints tab, then the Trash tab (keyboard). Page errors: 'ReferenceError: undoLoadSavepoints is not defined at undoSwitchTab (static/shared/undo.js:39)' and 'undoLoadTrash is not defined' (undo.js:40); logged in combo_1440.log, undo_tabs.py and rb_source.py. Each section shows only its blurb with 0 rows, while GET /api/undo/savepoints returned 2 entries, later 6. grep finds no definition of either function anywhere in static/, and no JS calls /api/undo/savepoints or /api/undo/trash. On origin/main, static/shared/undo.js has never contained them since it was introduced (8777c14); an older commit d24749c ('Update undo.js', 2026-06-25) deleted both. Chain: a Rekordbox-source title edit wrote master.db and created savepoint master.backup_20261005_183914_145750.db, but the UI offers no way to restore it. Screenshots: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_undo_after_savepoints_click.png, /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_16_rekordbox_title_edit_no_restore_ui.png
- **Fix:** Re-implement undoLoadSavepoints() and undoLoadTrash() against /api/undo/savepoints and /api/undo/trash, with restore buttons wired to the existing restore endpoints. Make undoSwitchTab defensive (typeof fn === 'function', else show an error row). Add a JS smoke test that clicks every Undo tab and fails on any pageerror.

#### [HIGH · demonstrated] A stray Escape press makes the next Settings open and the next step report close themselves; the report becomes an unclickable pill

- **ID:** `ui-live-record-escape-poisons-modals` · **Area:** ui-layering · **Tool/surface:** Settings modal, step-completion report modal (all tools)
- **Expected:** Escape with nothing open is a no-op. A tool's completion report stays until dismissed and can be re-opened from its pill.
- **Actual:** The next Settings open, or the next tool completion report, vanishes immediately. The report's pill has the wrong key and cannot reopen it, so the run's report is unreachable in-app for the session (the file on disk is the only copy).
- **Evidence:** Script /tmp/claude-0/-home-user-FableGear/9074577f-2e6a-5e16-bdce-ef5b26f1b16b/scratchpad/audit/ui-live-record/run_escape_bug.py.
Control (no Escape first): settings visible 16/16 samples over 1.6s.
Press Escape with nothing open, then click Settings: visible 0/16 samples.
After 3 stray Escapes, 1 of the next 4 Settings clicks was swallowed.
Report modal: one Escape, then openReportModal('Tag Tracks — BPM & Key Detection', ...). It was visible for about 100ms, then auto-closed into a pill labelled 'Step Complete Summary' (dataset key 'Step Complete'), while sessionReports holds 'Tag Tracks — BPM & Key Detection'. Clicking the pill does nothing (pill_check.py).
One Escape with Settings and the report both open closes both.
Cause: static/shared/info.js:266-270 calls closeSettings() and closeReportModal() on every Escape. Both call _sbAnim (modals.js:60) on a display:none modal, so 'animationend' never fires and the {once:true} close callback stays armed until the next open.
Screenshots: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_esc_bug_settings_t120ms.png, /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_esc_bug_report_t120ms.png, /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_esc_bug_report_t1800ms.png, /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_esc_bug_report_pill.png
- **Fix:** Guard closeSettings() and closeReportModal() with an early return when the backdrop is .hidden, as closeHealthModal (health.js:22) already does. In _sbAnim, remove any previously armed handler before adding a new one, or also listen for 'animationcancel'. Replace the scattered Escape listeners with a single layer-stack dispatcher that closes only the topmost open layer.

#### [HIGH · demonstrated] One click on a column header in a playlist view permanently overwrites the playlist order, with no confirm or undo

- **ID:** `ui-live-record-playlist-header-sort-destructive` · **Area:** undo-revert · **Tool/surface:** Record Room playlist view (track-table header)
- **Expected:** A header click sorts the view only. Persisting a new set order is an explicit, confirmable action that can be undone.
- **Actual:** A curated DJ set order is destroyed by an incidental header click, with no way back.
- **Evidence:** playlist_sort.py: created 'Friday Set (curated order)' via API with order [Whisper, Clipper, Kaito_-_sunrise_FINAL_v2, Pressure, Glitterball]. Selected it in the UI and clicked the 'Title' header once. Toast: 'Playlist sorted by title (reversed) — order saved.' Then GET /api/library/playlists/<id>/tracks returned [Whisper, Pressure, Kaito..., Glitterball, Clipper]. No confirm dialog. /api/undo/database/history = []. Code: static/chop_shop/tool_modal.js:1177-1229 (leSortBy calls leSortPlaylistBy, which PUTs /tracks/order). Sort state is global (_leSortCol defaults to 'title', ascending), so the first click on a playlist reverses it. Screenshot: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_31_playlist_header_click_reorders.png
- **Fix:** Make header clicks view-only in playlist mode. Add an explicit 'Save this order to playlist' action with confirm. Snapshot the previous order (DatabaseUndoManager transaction or an Undo toast) before PUT /tracks/order. Reset sort state when entering a playlist.

#### [HIGH · demonstrated] Deleting a FableGear playlist leaves no revert marker or undo entry

- **ID:** `ui-live-record-playlist-delete-no-undo` · **Area:** undo-revert · **Tool/surface:** Record Room playlist tree, Delete button
- **Expected:** A destructive DB-layer op creates a restore point referenced by the Undo Wizard, e.g. 'revert to before delete playlist X at <time>'.
- **Actual:** The playlist, its membership and its order are gone for good. Create and rename are also not journaled.
- **Evidence:** run_flows.py at all 3 viewports: select 'Audit Set A (renamed)', click Delete, accept confirm('Delete playlist ...? Tracks will remain in your library.'). The playlist is gone from /api/library/playlists. Afterwards /api/undo/database/history = [], /api/undo/timeline = [], /api/undo/operations = {sessions: []}. /api/undo/savepoints lists only master.backup_* (Rekordbox) files. FableGear-source DELETE (routes_player.py:1341-1351) calls fg.delete_playlist with no backup or transaction. Screenshot: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_30_playlist_deleted.png
- **Fix:** Record a DatabaseUndoManager transaction with affected records (playlist row, membership rows, order) before fg.delete_playlist and before rename and reorder, and show it in the Database tab. Alternatively soft-delete to a 'Recently deleted' folder with restore.

#### [HIGH · demonstrated] Track title edit in the default FableGear view always fails (404) because it targets Rekordbox master.db; each failure still writes a master.db savepoint

- **ID:** `ui-live-record-title-edit-wrong-db` · **Area:** other · **Tool/surface:** Record Room track table, double-click title (the only metadata editor)
- **Expected:** Editing a title in the FableGear library updates the FableGear DB record shown. Nothing touches master.db, and no savepoint is created for a no-op.
- **Actual:** Always 404 in the default view. A failed edit still snapshots master.db. Possible cross-DB write to the wrong record.
- **Evidence:** run_flows.py at all 3 viewports: double-click 'Clipper' (FableGear id 9), prompt 'Edit track title:', accept 'Clipper (edited)'. Result: '404 PATCH /api/library/tracks/9', toast 'Track not found', FableGear title unchanged. routes_player.py:1482-1503 always opens write_db(LOCAL_DB) and calls db.get_content(ID=track_id), ignoring the ?db= source; leEditTrackTitle (tool_modal.js:1113) sends no db param. FableGear ids are 1-11; Rekordbox ids are 1003-1031. server.log shows 'Backup created: .../master.backup_20261005_182045_320132.db' right before the 404. Three failed edits produced 3 spurious savepoints (18:20:45, 18:32:15, 18:34:37) that look like real restore points. In Rekordbox source the edit succeeds (track 1017) and writes master.db (rb_source.py). Inferred: if a FableGear id ever equals a DjmdContent ID, a different Rekordbox track is silently renamed. Screenshot: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_24_title_edit_toast.png
- **Fix:** Route PATCH by source like the playlist routes: FableGear source updates through the fg DB API; local/device go through write_db. Pass ?db=_leDbSource from leEditTrackTitle. Look up the track before opening a write session or taking a backup. Add a test that PATCHing a FableGear id never opens LOCAL_DB.

#### [HIGH · demonstrated] Opening the app with the music drive unplugged recreates the drive's folder tree and toasts 'Library audit complete ✓'

- **ID:** `ui-live-record-offline-drive-phantom-writes` · **Area:** boundary · **Tool/surface:** Silent launch audit (/api/run/audit), /api/setup-archive, state tracker
- **Expected:** With the drive offline, nothing is written under its path. The launch audit is skipped or reports failure, and the user sees an offline warning.
- **Actual:** The drive directory tree is recreated, a report and state are written there, the audit is recorded as a success, and a success toast appears.
- **Evidence:** drive_offline2.py: renamed sandbox Volumes/DJDRIVE to DJDRIVE_offline, so the path no longer exists, then loaded '/'. Within 6s the toasts seen were ['Library audit complete ✓']. Afterwards Volumes/DJDRIVE existed again with ['FableGear Archive', 'Music Library'].
DJDRIVE_phantom_created_while_offline contains Music Library/.fablegear_state.json (audit exit_code 0), FableGear Archive/{Savepoints,Quarantine,Reports/Audit/audit_20261005_182713.txt,Logs/*,Database/fablegear.db + meta}. The report says 'Path integrity: 0.0% (0/11 local files found), Missing files: 11'.
The drive-offline banner existed but was not visible in the Record Room.
Code: utility.js:56-62 runs POST /api/setup-archive and runSilentAudit on every launch. config.py:110-125 ensure_archive_structure() calls mkdir(parents=True) although its docstring says 'Skips silently if the drive isn't mounted'. state_tracker.py:47 calls mkdir(parents=True).
Speculative (macOS): a phantom /Volumes/<DRIVE> folder can make the real drive mount as '<DRIVE> 1', after which FableGear reads and writes the boot volume.
Screenshot: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_62_offline_drive_audit_toast.png
- **Fix:** Before any write under music_root or the archive root, require the volume to be mounted (os.path.ismount on the /Volumes/<name> root, or music_root existing). Do not create parents of a missing root. Skip or fail runSilentAudit when /api/status drives.music_root_ok is false, and make the toast reflect missing files. Make ensure_archive_structure do what its docstring says.

#### [HIGH · demonstrated] The DJ Decks toggle can't be clicked when idle (opacity 0, pointer-events none)

- **ID:** `ui-live-record-deck-toggle-unreachable` · **Area:** ui-layering · **Tool/surface:** Scan bar '🎛 Decks' and '▸ Output' buttons
- **Expected:** The decks can always be opened from the Record Room.
- **Actual:** In the normal idle Record Room, the dual-deck player is undiscoverable and can't be clicked.
- **Evidence:** run_solo.py at 3 viewports: scan-bar view buttons (idle) = [{'t':'🎛 Decks','op':'0','pe':'none'}, {'t':'▸ Output','op':'0','pe':'none'}]. Playwright click('#deck-toggle-btn') timed out ('element is not visible'). CSS: fablegear.css:1419-1421, '#scan-bar .scan-bar-right {opacity:0; pointer-events:none}' unless #scan-bar.active, i.e. a scan is running. The left-rail Log button (#view-output-btn) is display:none when idle. The only routes are the Ctrl+K palette, which harvests '🎛 Decks | Tool' (run_flows.py deck step), or dropping a track on an already-open deck. Tab walk: the hidden Decks and Output buttons still take keyboard focus.
- **Fix:** Move the Decks toggle into the Record Room header or left rail, always visible in body.fg-space-record. Keep only scan-specific controls (Interrupt, Emergency Stop) hidden when idle.

#### [MEDIUM · demonstrated] Drive-offline, hotplug and health alerts are never visible in the Record Room

- **ID:** `ui-live-record-safety-banners-hidden` · **Area:** ui-layering · **Tool/surface:** #chop-banner-dock (drive-offline-banner, hotplug-banner, health-panel), health modal
- **Expected:** Safety alerts (drive offline, health criticals) are visible in whichever room is active.
- **Actual:** A user who stays in the Record Room never sees that the library drive is offline or that health issues exist.
- **Evidence:** All three banners sit inside #chop-shop-workspace (index.html:124-165), which is 'display:none !important' in body.fg-space-record (fablegear.css:840-841).
Drive offline (drive_offline2.py): the banner has inline display:block, offsetParent null and a 0x0 rect in the Record Room, but shows in the Chop Shop ('⚡ Drive Offline — DJ drive database offline').
Health: /api/status health = {warn:2, total:2}. The Chop Shop shows '2 warnings Health Issues'; the Record Room shows nothing.
Both openHealthModal triggers (#health-panel-badge, the Review button) report visible:false. Playing a track from the offline drive in the Record Room only toasts 'Track file not found or format unsupported.'.
Screenshots: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_60_record_room_drive_offline.png, /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_61_chop_shop_drive_offline.png, /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_53_deck_carried_into_chop.png
- **Fix:** Move the alert dock out of #chop-shop-workspace into room-neutral chrome (e.g. under the titlebar), or add a compact Record Room indicator (titlebar pill with a count) that opens #health-modal and the offline details. Keep the neutral accent per the '#brew-banner' precedent.

#### [MEDIUM · demonstrated] Undo Wizard tab bar overflows its fixed 400px panel: Trash is off-screen and Savepoints is cut off at every viewport

- **ID:** `ui-live-record-undo-tabs-overflow` · **Area:** ui-layering · **Tool/surface:** Undo Wizard
- **Expected:** All five tabs can be reached with a mouse.
- **Actual:** Trash (pruned-file recovery) can't be reached by mouse even after its loader is restored.
- **Evidence:** undo_tabs.py: at 1440x900 the panel spans x 1040..1440; Savepoints spans 1401..1509 (39/108px visible); Trash spans 1517..1625, fully outside the viewport. At 1024x700 the panel spans 624..1024; Trash spans 1101..1209. A raw mouse click on the visible sliver of Savepoints leaves scrollLeft at 0 and Trash still not hit-testable. Only keyboard Tab focus scrolls the overflow:hidden #undo-panel (scrollLeft 185), which shifts the whole panel. .undo-panel-nav (fablegear.css:4087) has no wrap or overflow-x; .undo-tab-btn has min-width 108px; #undo-panel is 400px with overflow:hidden. Screenshots: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_06_undo_timeline.png, /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1024x700_06_undo_timeline.png
- **Fix:** Let .undo-panel-nav wrap (flex-wrap: wrap), use a compact segmented control, or set overflow-x:auto with visible scroll affordance. Alternatively widen the panel to min(560px, 100vw - left rail).

#### [MEDIUM · demonstrated] An open deck panel covers the library's status bar, playlist tree and most of the track list

- **ID:** `ui-live-record-deck-covers-library` · **Area:** ui-layering · **Tool/surface:** Deck panel (#deck-panel) and library editor
- **Expected:** With the deck open, the library's status-bar actions and playlist tree stay reachable.
- **Actual:** Library actions and playlists are covered while the deck is open, at every viewport.
- **Evidence:** Deck geometry at all 3 viewports: deck height 331px, but body.deck-open moves #library-editor-overlay's bottom up by only 200px (fablegear.css:7823-7825). At 1440x900: deck 525..856, library bottom 656, status bar 617..656, so 131px of overlap. Hit-tests: Remove/Add Selected → #deck-sync-b, + Stage Selected → .deck-wave-times, ↺ Reload → #deck-b-vinyl. At 1024x700 only about 1.5 track rows show, and the playlist tree (drop targets) is under the deck. Screenshots: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_09_decks_open_empty.png, /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1024x700_26_deck_loaded.png
- **Fix:** Publish the deck's real height through a ResizeObserver as --deck-h and set body.deck-open #library-editor-overlay {bottom: calc(var(--scan-bar-h) + var(--deck-h))}. Or make the deck part of the library's flex column. At heights of 700px or less, collapse the deck's control rows.

#### [MEDIUM · demonstrated] Staging panel (z 9700) sits above the USB export modal (z 601) and covers its controls; it can't be dismissed with Escape or an outside click

- **ID:** `ui-live-record-staging-above-modals` · **Area:** ui-layering · **Tool/surface:** Staging Queue panel with the USB Export modal
- **Expected:** Modals always sit above non-modal panels. Panels close on Escape or an outside click.
- **Actual:** A non-modal panel stays interactive on top of a modal and hides the modal's controls.
- **Evidence:** run_combo.py staging_export at 1024x700: export modal checkbox 'input.le-export-pl-cb' hit-test → #staging-panel(z=9700); the modal's title and Target Drive section are hidden under the staging panel, while 'Clear all' and 'Open Chop Shop →' stay clickable above a modal backdrop. Escape left both open. CSS: .staging-panel z-index 9700 (fablegear.css:597); #le-export-backdrop and #le-export-modal 600/601 (fablegear.css:5064/5073). Staging alone (all viewports): after Escape and after a click on the library, _stagingPanelOpen stayed true; it covers the mode toggle, DB source buttons, BPM/Key/Genre filters and the All Tracks item. Screenshots: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1024x700_40_staging_plus_export.png, /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_04_staging_open_empty.png
- **Fix:** Adopt one z-scale (chrome < panels such as staging/undo/db < modals such as export/settings/health/welcome < palette < toasts), using tokens rather than ad-hoc numbers. Drop staging into the panel tier, or close it when any modal opens. Add Escape and outside-click dismissal through the shared layer stack.

#### [MEDIUM · demonstrated] Command palette opens behind Settings but takes keyboard focus; one Escape then closes both

- **ID:** `ui-live-record-palette-under-settings` · **Area:** ui-layering · **Tool/surface:** Command palette (Ctrl/Cmd+K)
- **Expected:** The palette appears on top of whatever is open, and Escape closes only the palette.
- **Actual:** Typed input goes into an invisible field, and Escape also dismisses the layer underneath.
- **Evidence:** palette_combo.py: with Settings open, pressing Ctrl+K gives palette_on_top False; elementFromPoint at the palette input hits 'settings-header'; computed z-index is 9000 while focus is in input#fg-palette-input (invisible). A single Escape closed both palette and settings. ui_extras.css:155 sets #fg-palette-backdrop z-index:9000 and overrides fablegear.css:7934 (13000), which is below settings (10000), welcome (10100) and MCP (12000). Screenshot: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_54_palette_over_settings.png
- **Fix:** Delete the duplicate rule and give the palette a single token above modals. Route Escape through one topmost-layer handler. Optionally refuse to open the palette while a modal is open.

#### [MEDIUM · demonstrated] Escape and backdrop dismissal are inconsistent across Record Room layers, and focus is not returned

- **ID:** `ui-live-record-escape-backdrop-inconsistent` · **Area:** ui-layering · **Tool/surface:** All Record Room layers
- **Expected:** Escape closes only the topmost layer, the backdrop closes only its own layer, and focus returns to the trigger.
- **Actual:** Behaviour differs per surface: several layers trap the user until they find the ✕, and others close in batches.
- **Evidence:** run_solo.py / run_combo.py, all 3 viewports.
Escape does NOT close: welcome, staging, Undo Wizard, USB export, health modal, FableGo, MCP panel, log panel, deck, splash overlay, Ctrl+B file browser.
Backdrop click does NOT close: welcome, health modal. It DOES close: USB export, splash, undo (transparent z160 backdrop).
Where Escape works, independent listeners fire on the same key (info.js:266, db_rail.js:55, boot.js:10, ui_extras.js:182), so it closes several layers at once: settings+report, palette+settings.
Focus after closing settings (Escape or ✕) and after closing USB export (backdrop or Cancel): body (focus_trap.py).
Confirmed good: the drive flyout closes on Escape and returns focus to 'Connected Drives' (drives.js:205-219).
- **Fix:** Introduce a small layer-stack module (push on open, pop on close; one keydown handler closes the top entry and restores document.activeElement saved at open). Register every surface in it, and reuse the drive flyout's pattern.

#### [MEDIUM · demonstrated] Modals don't take or trap focus; Tab reaches controls behind them, including the quit button, and in closed panels

- **ID:** `ui-live-record-no-focus-trap` · **Area:** ui-layering · **Tool/surface:** Welcome, Settings, USB Export modals; closed panels
- **Expected:** Focus moves into the modal and is trapped there. Hidden panels are inert.
- **Actual:** Keyboard users act on invisible or covered controls, including destructive ones ('Clear all' asks for confirm; quit).
- **Evidence:** focus_trap.py: on open, focus stays on body or the trigger for welcome, settings and export. In each modal, 12 of 12 Tab presses landed outside the modal; for welcome the first stop is '#quit-btn Close FableGear' behind the backdrop, so Enter quits. run_combo.py tab_walk: 26 of 69 tab stops were invisible or covered controls, including closed staging panel buttons (effective opacity 0: Clear all, Save batch…, Open Chop Shop →), off-screen undo/db/log panel controls, the hidden Decks/Output buttons, and 'Replay intro video' (under the scan bar).
- **Fix:** Set the `inert` attribute on closed panels (staging, undo, db, fb, log) and on the app root while a modal is open. Focus the first control on open and restore focus on close.

#### [MEDIUM · demonstrated] Session summary pills (re-open a tool report) render as 8px slivers behind the Record Room overlay and the Chop Shop banner

- **ID:** `ui-live-record-summary-pills-hidden` · **Area:** ui-layering · **Tool/surface:** #session-pills-container (step report recall)
- **Expected:** Pills float bottom-right and re-open the run's report.
- **Actual:** The in-app report recall surface is invisible in both rooms.
- **Evidence:** pill_visibility.py: after a normal report close ('Got it'), the pill '📋 Step 2 Summary' has rect [756,37,8,108] in the Record Room; elementFromPoint hits 'le-header-left', so it can't be clicked. In the Chop Shop the rect is [756,185,8,108] and the hit is 'health-panel-header'. #session-pills-container (index.html:280; fablegear.css:2449) is in normal flow with no position or z-index, even though the template comment says 'float at bottom-right'. Screenshots: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_18_summary_pill_record.png, /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_18_summary_pill_chop.png
- **Fix:** Set #session-pills-container to position:fixed; right:16px; bottom:calc(var(--scan-bar-h) + 16px); width:auto, with a z-index in the panel tier. Fix the label key mismatch from the Escape finding.

#### [MEDIUM · demonstrated] Boundary: 'All Music' mode and Ctrl+B put Finder-style folder navigation inside the Record Room

- **ID:** `ui-live-record-fs-browse-in-record-room` · **Area:** boundary · **Tool/surface:** Record Room mode toggle 'All Music' (fs), global file browser (#fb-panel)
- **Expected:** The Record Room stays database-first: records, metadata, playlists. Folder navigation lives in the Chop Shop.
- **Actual:** The Record Room offers directory trees, breadcrumbs, parent-folder affordances and a drag-from-disk file browser.
- **Evidence:** run_solo.py 11/11b/11c: All Music swaps the playlist tree for a 'FILESYSTEM' section with '↑ Up', a raw path breadcrumb, 📁 folder rows with '+Q', drive cards with GB free and '+ Queue', and '+ Queue current folder' (library_mode.js:172-275; index.html:1043-1053). file_browser_rr.py: Ctrl+B in the Record Room opens #fb-panel (breadcrumb '/media', 'Drag any item to a drop zone ↗'), pushes the library right, and Escape doesn't close it (info.js:271-274). Copy also frames it as disk navigation: Settings > Help says 'Record Room — Library browser with database and filesystem views' (index.html:551), and the staging drop zone says 'Drop folders or tracks here'. Screenshots: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_11b_mode_fs_drilled.png, /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1280x800_19_ctrl_b_file_browser_in_record_room.png
- **Fix:** Two options. A: move All Music and the file browser into the Chop Shop (Ctrl+B active only in fg-space-chop). B: replace All Music with a metadata view such as 'Not yet in your library', grouped by artist/album, with 'Add to library'. Either way, fix the Settings copy.

#### [MEDIUM · demonstrated] Leaving All Music leaves stale folder content; Reload throws a TypeError and status sticks on 'Loading library…'

- **ID:** `ui-live-record-allmusic-return-breaks-view` · **Area:** other · **Tool/surface:** Record Room mode toggle, ↺ Reload
- **Expected:** Returning to Library shows the library.
- **Actual:** The view shows stale folder content until the user clicks All Tracks, and Reload is broken.
- **Evidence:** fs_return.py: Library view (11 rows), then All Music (list = drive cards), then Library. Result: rows 0, list still shows drive cards, status 'Loading library…'. Clicking ↺ Reload twice gives 'PAGEERROR: Cannot read properties of null (reading 'style')' and nothing changes. Clicking All Tracks renders the 11 rows below the stale cards. Cause: leFsBrowse replaces #le-track-list.innerHTML and destroys #le-empty-state, and leLoadLibrary (tool_modal.js:703-704) dereferences it outside try. Inferred root cause: library_mode.js:88/134 read and write window._leTracksLoaded, while library_editor.js:25 declares a separate lexical `let _leTracksLoaded`, so setLibraryMode('db') reloads every time. Screenshots: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_13_mode_back_to_library.png, /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_14_back_from_allmusic_reload.png
- **Fix:** Render FS mode into its own container rather than overwriting #le-track-list. Use one state store (all window-scoped, or all lexical). Guard leLoadLibrary's empty-state access with _leEnsureEmptyState().

#### [MEDIUM · demonstrated] On launch the library often looks empty ('Select All Tracks…') while All Tracks is highlighted with count 11

- **ID:** `ui-live-record-boot-race-empty-list` · **Area:** other · **Tool/surface:** Record Room boot (setFableGearSpace vs leLoadPlaylistsOnly)
- **Expected:** The launch state is deterministic.
- **Actual:** Roughly 40% of launches show an apparently empty library.
- **Evidence:** race_check.py, 10 fresh loads: 4 of 10 (runs 0, 4, 7, 9) ended with the empty state shown ('Select All Tracks or a playlist to load music.'), status 'Playlists loaded — select a playlist or load all tracks.', and All Tracks active; rows were rendered but pushed below the fold. 6 of 10 showed '11 tracks'. Two loaders race: setFableGearSpace → setLibraryMode('db') → leLoadLibrary (state.js:45) and usb_export.js:172 leLoadPlaylistsOnly(), and whichever resolves last wins. Screenshots: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_race_load_0.png, /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1024x700_02_base_library.png
- **Fix:** Drop the boot call to leLoadPlaylistsOnly when leLoadLibrary is already in flight, or gate both behind a load token and let only the newest request write the DOM.

#### [MEDIUM · demonstrated] 'Add Selected' can only add tracks already in the playlist, and it toasts success when nothing was added

- **ID:** `ui-live-record-add-selected-misleading` · **Area:** other · **Tool/surface:** Record Room status bar 'Add Selected'
- **Expected:** You can pick tracks in the library and add them to a chosen playlist. The toast reports the true count.
- **Actual:** The primary add control can't do its job, and it reports a false success.
- **Evidence:** add_selected.py: selected track 5 in All Tracks (button reads 'Select Playlist'), then clicked the playlist. Selection becomes [] (leSetTrackView clears _leSelectedTrackIds, tool_modal.js:335-340) and Add stays disabled. The only way to enable it is to select a row inside the playlist ('Add 1 Track'). Clicking it toasts 'Added 1 track to playlist.' while the playlist size stays 5 → 5 (the toast uses data.added || trackIds.length, tool_modal.js:1298). Drag-and-drop is the only real add path, and native pywebview DnD was not testable here.
- **Fix:** Keep the selection across view switches, or add an 'Add to playlist ▸' menu or button on the All Tracks selection. Show data.added (0 means 'Already in playlist').

#### [MEDIUM · demonstrated] Double-clicking Create makes duplicate playlists

- **ID:** `ui-live-record-double-submit-create` · **Area:** other · **Tool/surface:** Record Room '＋ Playlist' create bar
- **Expected:** One playlist per submit.
- **Actual:** Duplicates are created, and they are not undoable (no journal).
- **Evidence:** run_flows.py: filled 'Double Submit', then .le-create-submit clicked twice synchronously. Each run added 2 identical playlists (6 'Double Submit' after 3 viewport runs; visible in /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1024x700_40_staging_plus_export.png). leSubmitCreate (tool_modal.js:1253) has no in-flight guard, and the server has no duplicate-name check.
- **Fix:** Disable the button and input while the request is pending. Optionally reject or merge identical names under the same parent within a short window server-side.

#### [MEDIUM · demonstrated] Switching rooms leaves Record Room layers open: the export modal floats over the Chop Shop, and a playing deck keeps sounding with its controls covered

- **ID:** `ui-live-record-room-switch-carries-layers` · **Area:** ui-layering · **Tool/surface:** Space switch (left rail, Ctrl+1/Ctrl+2)
- **Expected:** Room-specific surfaces close or pause on switch, and room hotkeys are ignored while a modal is open.
- **Actual:** Record Room modal and audio leak into the Chop Shop, and the playing audio can't be stopped there.
- **Evidence:** run_combo.py room_switch: USB export open, then Ctrl+2 → {'export': True, 'space': 'chop'}; the export modal sits over the Chop Shop UI. chop_checks.py: deck A playing, switch to Chop Shop → deckState('a').playing stays true 1.5s later, and the deck pause button hit-test lands on 'log-output' (covered by Chop Shop z55-60 layers) at 1024 and 1280. Room hotkeys fire while a modal is open. setFableGearSpace (state.js:19-58) closes the DB panel, tool modal and help panel but not export, deck or settings. Screenshots: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_52_export_modal_carried_into_chop.png, /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_53_deck_carried_into_chop.png
- **Fix:** In setFableGearSpace('chop'), call leCloseExportModal(), deckPauseAll() and deckSetPanel(false). In launcher.js, ignore Ctrl+1/2 while any modal layer is open.

#### [LOW · demonstrated] Scan bar (z 190) covers the bottom of the left rail (z 175); the Splash button can't be clicked

- **ID:** `ui-live-record-scanbar-covers-left-rail` · **Area:** ui-layering · **Tool/surface:** Left rail / scan bar
- **Expected:** Rail buttons are fully clickable.
- **Actual:** The bottom rail button sits under the scan bar in the Record Room.
- **Evidence:** run_solo.py LP geometry at 1440x900: 'Replay intro video' spans 828..892, the scan bar starts at 856, and the centre hit is 'scan-bar'. The same happens at 1280x800 and 1024x700 (the analyze.py OCCLUDED row appears in every state at every viewport). run_combo.py: 'Splash button click: FAILED'. #left-panel uses bottom:0 (fablegear.css:320-332); #scan-bar uses bottom:0 at z190 (fablegear.css:1396).
- **Fix:** In the Record Room set #left-panel bottom: var(--scan-bar-h), or give the rail overflow-y:auto and stop it short of the scan bar.

#### [LOW · demonstrated] FableGo panel is dead UI: nothing opens it

- **ID:** `ui-live-record-fablego-orphaned` · **Area:** ui-layering · **Tool/surface:** #fablego-panel / #fablego-backdrop
- **Expected:** Either a trigger exists or the markup is removed.
- **Actual:** About 135 lines of unreachable markup plus a network call on every launch.
- **Evidence:** run_combo.py misc_modals: typeof openFableGo === 'undefined'. The only [onclick] references are closeFableGo/rkgGoTo/rkgPrev/rkgNext, and nothing in static/ adds .open. state_tracker.js:90,153 reference #fablego-btn-dot, which doesn't exist, yet it still fetches /api/connectivity on every load. Force-opened, it layers correctly (z 10001 over the 10000 backdrop), but Escape doesn't close it. Screenshot: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_48_fablego_forced.png
- **Fix:** Add a trigger (Settings > General or the left rail) with an openFableGo() function, or remove the panel and the boot fetch.

#### [LOW · demonstrated] 'Resize handles' on the Undo and DB panels are dead markup (height 0, no JS); dragging near the edge closes the panel

- **ID:** `ui-live-record-resize-handles-dead` · **Area:** ui-layering · **Tool/surface:** .sidebar-resize-handle in #undo-panel and #db-panel
- **Expected:** Working resize handles, or no handle markup.
- **Actual:** Fixed 400px panels, which also causes the Undo tab overflow.
- **Evidence:** run_combo.py undo_trash: handle box {'width':399,'height':0}; dragging 250px left left the panel width at 400 → 400, and the mouseup landed on #undo-panel-backdrop and closed the panel. fablegear.css:2096 says '/* sidebar-resize-handle removed */'; grep finds no JS handler.
- **Fix:** Remove the markup (index.html:865, undo_wizard.html:5) or implement resizing with a min/max width. If removed, fix the tab overflow by wrapping.

#### [LOW · demonstrated] The 'Create playlist' input bar shows on every load and in every mode without being requested

- **ID:** `ui-live-record-create-bar-always-open` · **Area:** ui-layering · **Tool/surface:** #le-create-bar
- **Expected:** The bar appears only after '＋ Playlist' or '＋ Folder'.
- **Actual:** It permanently takes about 50px of vertical space and invites accidental creates.
- **Evidence:** run_flows.py: 'create bar visible on load (no click): True' at all 3 viewports. It is visible in All Music and Integrated too (e.g. /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1280x800_12_mode_integrated.png). index.html:1012 lacks class 'hidden'; leCloseCreate adds it.
- **Fix:** Add class="le-create-bar hidden" in index.html:1012.

#### [LOW · demonstrated] Toasts stack on the same spot and intercept clicks

- **ID:** `ui-live-record-toasts-overlap` · **Area:** ui-layering · **Tool/surface:** .sb-toast
- **Expected:** Toasts stack legibly and never block controls.
- **Actual:** Toasts overlap each other and briefly block status-bar buttons.
- **Evidence:** run_flows.py toast_stack: four toasts' rects all at y≈802, centred, drawn on top of each other. An inline-play failure emits two toasts at once ('Track file not found or format unsupported.' + 'Could not play track.'). At 1024x700 a lingering toast covered #le-remove-btn (analyze.py OCCLUDED by .sb-toast). fablegear.css:2675 sets position:fixed at bottom center, pointer-events auto. Screenshot: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_29_three_toasts.png
- **Fix:** Use one fixed toast container (flex column, gap) with pointer-events:none, and pointer-events:auto only on actionable toasts. Collapse the duplicate play-error toast.

#### [LOW · demonstrated] Welcome modal blocks the app 1.2s after every launch and can't be dismissed with Escape or a backdrop click

- **ID:** `ui-live-record-welcome-every-launch` · **Area:** ui-layering · **Tool/surface:** #welcome-backdrop (launcher.js)
- **Expected:** A configured user lands in their last room. The launcher is dismissible with Escape.
- **Actual:** There is a mandatory click on every launch, with no keyboard dismissal.
- **Evidence:** Every fresh load showed #welcome-backdrop (launcher.js:27-31 calls openWelcome after 1200ms). run_solo.py: 'welcome still open after Escape: True', 'welcome open after backdrop click: True'. It covers all controls at z10100. Its 'Database' pill silently moves the user to the Chop Shop (openDbPanel switches space): {'db': True, 'space': 'chop'}. Screenshot: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_00_welcome_on_load.png
- **Fix:** Show the launcher only on first run or on request ('Don't show at startup'). Register it in the layer stack for Escape and backdrop close.

#### [LOW · demonstrated] Boundary labelling: the palette tags Chop Shop rail tools as 'Record Room'

- **ID:** `ui-live-record-palette-labels-db-tools-record` · **Area:** boundary · **Tool/surface:** Command palette entries
- **Expected:** Hints name the room the action opens.
- **Actual:** The palette says Record Room, but the action lands in the Chop Shop.
- **Evidence:** run_combo.py db_panel: palette entries 'Audit/Relocate/Link/Import/Dead Files … | Record Room'. Choosing 'Import' gave {'db': True, 'space': 'chop'}, i.e. it switched to the Chop Shop. Labels come from ui_extras.js:76-82.
- **Fix:** Change the hint to 'Chop Shop' for the DB-panel tools. Keep 'Record Room' for USB Export.

#### [LOW · demonstrated] Staging hint promises a right-click 'Stage for Chop Shop' menu that doesn't exist

- **ID:** `ui-live-record-staging-hint-false` · **Area:** boundary · **Tool/surface:** Staging Queue empty-state hint
- **Expected:** Copy matches the UI ('+ Stage Selected').
- **Actual:** It points users to a non-existent interaction.
- **Evidence:** run_flows.py staging_add: right-click on a track row → no fixed context-menu element appears. The only contextmenu handler in static/ is on deck hotcue pads (deck.js:821). The hint text is in templates/partials/staging_panel.html:35.
- **Fix:** Change the hint to 'Select tracks and click + Stage Selected', or implement a row context menu with Stage / Add to playlist / Load to deck.

#### [LOW · demonstrated] '+Q' and '+ Queue' buttons in All Music and Integrated views render as raw unstyled browser buttons

- **ID:** `ui-live-record-unstyled-stage-buttons` · **Area:** ui-layering · **Tool/surface:** .le-stage-btn, .le-stage-folder-btn, .le-split-stage, .le-vol-stage-btn
- **Expected:** Buttons styled with room tokens.
- **Actual:** White default buttons and unstyled subtitle text.
- **Evidence:** grep: none of these classes (nor .le-split-col-sub) appear in fablegear.css or ui_extras.css. Screenshots show white '+Q' boxes: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1440x900_11b_mode_fs_drilled.png, /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1280x800_12_mode_integrated.png
- **Fix:** Add styles for these classes using --room-accent tokens, or reuse .le-load-btn.

#### [LOW · demonstrated] (Observed in passing, Chop Shop) The rail's DB tools scroll off-screen at 1280 and 1024 widths with no scroll cue

- **ID:** `ui-live-record-chop-rail-overflow` · **Area:** ui-layering · **Tool/surface:** #workflow-rail
- **Expected:** All rail tools visible at 1024 wide, or an obvious overflow cue.
- **Actual:** DB tools are hidden off the right edge.
- **Evidence:** chop_checks.py: at 1024x700, rail scrollWidth 1236 > clientWidth 944; FIX PATHS, IMPORT, LINK and DEAD FILES lie beyond innerWidth. At 1280x800, DEAD FILES (1234..1298) is off-screen. overflow-x is auto, so the rail can be scrolled but shows no cue. Screenshot: /home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1024x700_53_deck_carried_into_chop.png
- **Fix:** Hand to the Chop Shop UI auditor. Shrink the rail icons below 1280px, wrap the DB group, or add edge fades or arrows.

#### [INFO · inferred] Record Room library and playlist logic lives in static/chop_shop/tool_modal.js

- **ID:** `ui-live-record-rr-code-in-chopshop-file` · **Area:** boundary · **Tool/surface:** Code organisation
- **Expected:** Record Room behaviour in static/record_room/, with one state store.
- **Actual:** The room boundary is blurred in code, so fixes risk touching chop_shop/.
- **Evidence:** leLoadLibrary, leRenderTracks, leRenderPlaylistTree, playlist create/rename/delete, leEditTrackTitle and leSortPlaylistBy are defined in static/chop_shop/tool_modal.js:320-1419, not in static/record_room/. Record Room state is split three ways (library_editor.js lexical lets, library_mode.js window state, tool_modal.js), which is the root of the _leTracksLoaded divergence.
- **Fix:** Move the le* functions to static/record_room/library_view.js with a single state object. Mechanical move first, behaviour fixes second.

#### [INFO · demonstrated] Confirmed-good behaviour (no action needed)

- **ID:** `ui-live-record-confirmed-good` · **Area:** other · **Tool/surface:** various
- **Expected:** n/a
- **Actual:** Works as intended.
- **Evidence:** Drive flyout: Escape closes it and returns focus to 'Connected Drives'. USB export: backdrop click closes it, and with no Pioneer drive it shows 'No Pioneer USB drives found…' and keeps Export disabled. Settings modal fits with its footer reachable at 1024x700 (/home/user/FableGear/docs/audits/2026-10-05/screens/ui-live-record/1024x700_05_settings_tab-archive.png). Create bar: Escape inside the input closes it and '+ Playlist' focuses the input. Rename uses a prompt pre-filled with the current name; Delete confirms, saying tracks stay. Toasts (z 20000) render above every modal. Staging '+ Stage Selected' updates the badge and /api/staging. Drag row → playlist adds and toasts. Drag row → deck A loads and plays (deckState playing:true). The palette's '🎛 Decks' opens the deck. A Rekordbox-source title edit takes a master.db backup before writing.
- **Fix:** Keep these behaviours, and use the drive flyout's focus handling as the model for other layers.

## Onboarding wizard (live)

_Auditor: `wizard-live`_

I audited the in-app onboarding wizard (templates/onboarding.html, 8 steps, plus /api/onboarding/* and _setup_gate_status) live on Linux with headless Chromium. This round's sandbox was sbx-wizard-r3 on port 8101. I reused round-1 and round-2 evidence, which came from the same product code (the app copy diffs clean against the repo), and re-ran the top findings live on port 8101.

The wizard's only checkpoint is the step index plus five text inputs, kept in localStorage. Every consent and choice lives in a JS variable (confirmedPaths) that a reload, restart or crash wipes.

The worst result is a permanent trap. Reload or relaunch at step 5, 6 or 7, carry on, and you reach step 7, which has no Back button. Both room buttons then POST an empty config and get a 400. Every relaunch resumes at step 7 again, so the app can never be entered (critical; demonstrated again in round 3, including after a server restart).

Three high findings:
- **Consent flip:** after a reload, consent you declined is silently turned into full read+write access, because save-config treats a missing value as true.
- **Reconfigure wipes settings:** it shows no current values, and finishing it resets the AcoustID key, excluded dirs, mode, import target, LUFS target, snapshot settings and the MCP config.
- **Setup marked complete too early:** step 5 "Enable AI" and step 6 Import mark setup complete. The "Finish later" text and the localStorage draft then lie, and leaving mid-import kills the server and leaves the library half-imported.

The gate admits a `{}` or invalid config.json. It also sends a configured user whose state file is missing back to a blank first-run wizard.

The wizard validates no paths: a .txt file is accepted as the Rekordbox DB, a folder as the device DB, and /proc as the archive.

Working correctly: "Finish later" on every step, Back keeping values, leaving reconfigure without finishing (config stays byte-identical), double-submit guards on the AI and Import buttons, nothing written to the server before step 5, and reload during an import.

**Coverage**

- STEP TABLE (from code + live). Format is: index | purpose | inputs | server calls | Continue enabled when | Back | Finish later.
- step-0 | dependency check | none | GET /api/onboarding/dep-check; optional POST install-deps (`open -a Terminal setup.sh`), then dep-check polled every 5s up to 180 times | Continue starts disabled and enables once dep-check returns (also on fetch error, onboarding.html:974); 'Skip for now' is always enabled | Back: none (first step) | Finish later: yes
- step-1 | install FableGear.app | toggles 'Add to Applications' and 'Pin to Dock' | POST /api/onboarding/install-app (osacompile/sips/iconutil, Dock plist, killall Dock) | 'Install & continue' and 'Skip' always enabled | Back: NONE (cannot return to step 0) | Finish later: yes
- step-2 | Rekordbox read consent | none | GET /api/onboarding/check-fda on entry; 'Open Settings' POSTs open-fda-prefs | Allow Read goes to step 3; 'Skip — limited mode' sets read=false, write=false and jumps to step 4 | Back: to step 1 | Finish later: yes
- step-3 | write consent | none | none | 'Allow Write' or 'Read-only mode' goes to step 4 | Back: to step 2 | Finish later: yes
- step-4 | library paths | scan consent card (Allow scan runs GET /api/onboarding/scan-library; or 'Enter paths manually'); local DB and device DB as free text (Browse uses pywebview or GET /api/pick-file); music root is readonly (pick from scan list or Open Finder, GET /api/pick-folder); archive mode auto or custom; snapshot cadence; include master.db | Confirm paths is always enabled; client-side check is non-empty local DB, device DB and music root (and custom archive) only | Back: to step 3 | Finish later: yes
- step-5 | AI/MCP opt-in | client picker | 'Enable AI integration' POSTs save-config (sets setup_complete=true) then POST /api/mcp/enable | 'Not now' goes to step 6; after enabling, a Continue button appears | Back: to step 4 | Finish later: yes
- step-6 | seed FableGear DB | checkboxes for home library and scan roots | 'Import selected' POSTs save-config (setup_complete=true) and POST import-sources, then polls status every 700ms and auto-jumps to step 7 when done | 'Skip for now' always enabled; Import is disabled while running | Back: to step 5 (also during import) | Finish later: yes
- step-7 | summary and room choice | none | 'Record Room' or 'Chop Shop' POSTs save-config, then clears localStorage and goes to /?space=… | always enabled | Back: NONE; dots not clickable | Finish later: yes (on first run it quits the server)
- WHAT IS PERSISTED WHERE: localStorage key fg_onboarding_progress_v1 holds {step, inputs: local-db, device-db, music-root, archive-root, cadence, master-db value 'on'}; it is written on every goToStep and on input change. NOT persisted: confirmedPaths (db_read/db_write, mcp_opted_in, drive_scan, backup_dir), scanConsent, scan results, the archive-mode radio, the master-db checked state, install toggles, the import summary. Server side: fablegear-state.json is written with defaults on the first gate check; config.json and the state file (setup_complete:true) are written by save-config at step 5 Enable / step 6 Import / step 7; MCP keys by /api/mcp/enable; ~/.fablegear/fablegear.db by import; ~/Applications is created by install-app even when the install fails.
- LIVE in round 3 (port 8101, sbx-wizard-r3, script audit/wizard-live/r3/r3_confirm.py, results in r3/R3_*.json, screenshots screens/wizard-live/R3_*.png): the step-7 trap (reload plus graceful restart/relaunch), consent flip and its no-reload control, double-click on step-1 Skip, reconfigure (no prefill; leave without finishing; full run; config diff), gate with config `{}` and with the state file deleted.
- LIVE in rounds 1 and 2 (same code; JSON in audit/wizard-live/*.json and r2/*.json, screenshots A_..K_, R2_*): happy path; reload and restart at every step 0-7 (B_resume); kill -9 at steps 3 and 4 (r2_resume.log); Finish later at every step plus after MCP enable (D_finish_later); empty, nonexistent, non-Rekordbox and unwritable (/proc) inputs (E_*); double-click on every button (F_dblclick); install-deps and install-app failure on Linux; reload during scan; Back, Finish later and reload during an 8000-file import (G_*); reconfigure (H_); gate with corrupt config and state files (J_); layout at 1440x900, 1024x700 and 900x600 (K_, R2_layout); Back navigation (R2_back).
- NOT testable here: Homebrew/setup.sh in Terminal, osacompile/sips/Dock pinning, the Full Disk Access prompt and denial path, osascript and pywebview native pickers (music root was set with selectMusicRoot(path) to stand in for a successful Finder pick, which is labelled where relevant), WKWebView localStorage persistence semantics, network-less behaviour (only inferred: the update check and dep-check fail quietly). fpcalc is not involved in onboarding.
- NOT covered: the shell first-run installer (launch.sh, setup.sh, install.sh, .fablegear_ready/.fablegear_failed sentinels, .launch_lock), which is outside this live wizard assignment; an import-sources exception partway through a run (only the 400 'not a directory' path was exercised; the s.error to 'Retry import' path is inferred from onboarding.html:1546-1554).
- Sandbox tooling note for the lead: round-2 notes say sbx-*/restart.sh stores `$!` of a `cd … && nohup …&` list, which can be the wrapper subshell's PID, so its kill can miss the real server. Round 3 restarted by SIGTERM to the real python PID and relaunched with the same env (lib3.server_restart). The server on 8101 was killed by PID 2510 at the end.

**Findings**

#### [CRITICAL · demonstrated] A reload, quit or crash at steps 5-7 permanently traps a first-run user on step 7 (save-config always fails, no Back, every relaunch resumes there)

- **ID:** `wizard-live-trap7` · **Area:** install-wizard · **Tool/surface:** onboarding wizard (templates/onboarding.html, /api/onboarding/save-config)
- **Expected:** Resuming at any step restores every value needed to finish. If a save fails, the user gets a way back to the step that collects the missing data.
- **Actual:** The user can never complete onboarding. 'Finish later' quits, and each relaunch resumes on the broken step 7. The only escape is clearing the WebView's localStorage, which no normal user can do.
- **Evidence:** Round 3: r3_confirm.py trap7 run against :8101. Path taken: Read allowed, Read-only chosen, paths entered, weekly cadence plus master.db, then step 5 and page.reload(). Before the reload confirmedPaths held 11 keys; after it, confirmedPaths={} and masterDbChecked flipped true→false, although the text inputs were restored. Step 6 showed 'No sources discovered…'. Skip led to step 7, whose summary wrongly said 'Limited mode'. Record Room returned 400 'Could not save configuration: Missing required fields: local_db, music_root, device_db', and Chop Shop returned the same. A graceful server restart followed by reopening '/' landed on /onboarding at step 7, and clicking again gave 400 once more. Recorded: save_config_responses=[400,400,400]; gate config_missing; config.json absent. Step 7 has no Back button and the dots are not clickable. Files: audit/wizard-live/r3/R3_trap7.json; screens/wizard-live/R3_trap7_s5_after_reload.png, R3_trap7_s6_no_sources.png, R3_trap7_s7_save_400.png, R3_trap7_after_restart_still_trapped.png. Round 1 C_trap7.json showed the same trap after a second reload. Code: confirmedPaths is a plain JS var (onboarding.html:851); restoreOnboardingProgress restores only step and inputs (878-894); launch() posts confirmedPaths (1604-1610).
- **Fix:** Persist confirmedPaths (consents, archive mode, backup_dir, mcp, drive_scan) in the progress blob, or better in a server-side draft file. At step 7 (and before any save-config), rebuild the payload from the restored inputs and route the user to step 4 for anything missing. Add a Back button to step 7. When save-config returns 400 for missing fields, jump to the step that owns them.

#### [HIGH · demonstrated] Declined Rekordbox read/write consent becomes full access after a reload, while the summary still says 'Limited mode'

- **ID:** `wizard-live-consent-flip` · **Area:** install-wizard · **Tool/surface:** onboarding wizard + /api/onboarding/save-config
- **Expected:** A declined consent stays declined until the user changes it. A missing consent value never defaults to granted.
- **Actual:** A reload or relaunch silently upgrades the user to write access on their Rekordbox DB, and the UI tells them the opposite.
- **Evidence:** Round 3 consentflip case: 'Skip — limited mode' set confirmedPaths {db_read:false, db_write:false}. Paths were filled and confirmed, landing on step 5, then the page was reloaded. Back showed the consent card again, with inputs restored. 'Enter paths manually', then Confirm, then Not now, then Skip. The step-7 summary read '⚠ Limited mode — database tools unavailable…'. After clicking Record Room, fablegear-state.json = {setup_complete:true, db_read:true, db_write:true} and /api/setup-status reports db_read true / db_write true. Control run with no reload (R3_limitedcontrol.json) correctly saved db_read:false, db_write:false. Files: R3_consentflip.json, R3_consentflip_s7_summary_limited.png, R3_consentflip_main_ui.png. Round 1 C_consent_read.json and C_consent_write.json show the same (a read-only choice became write:true). Root cause: app.py:1729-1730 `bool(data.get("db_read", True))` / `bool(data.get("db_write", True))`, combined with consent never being persisted client-side.
- **Fix:** Default db_read and db_write to False (or reject a missing value with 400) in save-config. Persist the consent answers with the wizard progress. If consent is unknown on resume, send the user back to step 2. Build the step-7 summary from the same payload that will be saved.

#### [HIGH · demonstrated] Reconfigure shows none of the current settings and, when finished, resets settings the wizard never asked about (AcoustID key, excluded dirs, mode, import target, LUFS target, MCP config)

- **ID:** `wizard-live-reconfigure-wipes` · **Area:** install-wizard · **Tool/surface:** /onboarding?reconfigure=1 + /api/onboarding/save-config
- **Expected:** Reconfigure pre-fills the current values and merges its changes into the existing config.
- **Actual:** Every completed reconfigure destroys user settings with no warning or way to revert.
- **Evidence:** Round 3 reconf case on a completed setup, with config.json extended by acoustid_api_key=MYOWNKEY123, excluded_dirs=['Sample Packs'], mode=rural, import_target=fablegear, snapshot weekly + master.db, target_lufs=-9. Plain /onboarding redirects to '/' (good). /onboarding?reconfigure=1 opens at step 0 with 'Exit setup' and every input empty (cadence 'monthly', master.db unchecked). Exit setup goes to '/' with config sha unchanged (good). Running the wizard through again with the same paths gave this config diff: acoustid_api_key MYOWNKEY123→wAbRWVEfls, excluded_dirs ['Sample Packs']→[], import_target fablegear→both, mode rural→suburban, snapshot_cadence weekly→monthly, snapshot_include_master_db true→false, target_lufs -9.0→-8.0. Round 1 H_reconfigure.json H4 also showed mcp_enabled, mcp_autostart, mcp_expose, mcp_port and mcp_token dropped, and H3 showed the token regenerated when enabling AI during reconfigure. Files: R3_reconf.json, R3_reconf_open_empty.png. Code: save-config builds cfg only from wizard fields plus DEFAULTS (app.py:1706-1720) and save_user_config overwrites the file (user_config.py:260-283). There is no backup of the old config and no undo.
- **Fix:** In save-config, load the existing config and update only the keys the wizard owns; never re-apply DEFAULTS over existing keys. Render current values into the wizard when already_configured is true. Write a timestamped config backup before overwriting.

#### [HIGH · demonstrated] Setup is marked complete at step 5/6 before the user finishes; 'Finish later' then misleads, can cut an import in half, and leaves a stale draft that hijacks later reconfigures

- **ID:** `wizard-live-setup-complete-early` · **Area:** install-wizard · **Tool/surface:** onboarding wizard (mcpOptIn/importSelectedSources → _saveConfigNow) + obExitSetup → /api/quit
- **Expected:** setup_complete is set only when the user finishes (step 7), or the wizard knows it has become 'configured' and its exit text and behaviour change to match. Leaving during an import either warns and cancels cleanly, or keeps the import running and reports it.
- **Actual:** The app can open into a half-imported library with no notice, and the user was told setup was not finished. Stale drafts reappear in later reconfigure sessions.
- **Evidence:** Round 1 D_finish_later.json '5mcp': after 'Enable AI integration', fablegear-state.json had setup_complete:true. The Finish-later dialog still said 'FableGear needs setup finished… you'll pick up right here next time'. After accepting, the server quit, and relaunch went to '/' (main UI), not the wizard. Round 1 G_import_exit.json: during an 8000-file import the gate was already 'ready'. Finish later then quit; /api/quit killed the server with 1200 of 8000 tracks imported. Relaunch landed on '/' with import status idle, no toast and no pill, and the import never resumes. Page error: 'Cannot read properties of null (reading 'classList')', from the import poll running against the replaced body. Round 3 R3_reconf.json reconf_reopen_step=4: a draft left in localStorage (launch() is the only thing that clears it, onboarding.html:1615) makes the next /onboarding?reconfigure=1 resume mid-wizard. Code: _saveConfigNow is called in mcpOptIn (1420) and importSelectedSources (1522); alreadyConfigured is fixed at render time (855), so the exit control keeps quitting.
- **Fix:** Split save-config into 'save draft config' (no setup_complete) and 'finish'. Set alreadyConfigured=true after any successful save-config. Make Finish-later during an import confirm with a dedicated message and either block it or let the import finish in the background with a resumable job. Clear the progress draft whenever the gate turns ready.

#### [MEDIUM · demonstrated] Setup gate admits the main UI with an empty or invalid config.json, and sends a configured user back to a blank first-run wizard when the state file is missing

- **ID:** `wizard-live-gate-integrity` · **Area:** install-wizard · **Tool/surface:** _setup_gate_status (app.py:1257-1282) / user_config.config_exists (user_config.py:157-159)
- **Expected:** The gate admits the main UI only when the config is parseable and has the required keys. A configured user with a lost state flag is not treated as a first-run user who must redo setup and lose settings.
- **Actual:** config_exists() only checks that the file exists. The state flag alone decides between full admission and a full blank wizard.
- **Evidence:** Round 3 gate case: with config.json='{}', GET / returned 200, /api/setup-status gave gate_reason 'ready', and /api/status showed drives.configured=false with every path null. The browser landed on '/' with no banner (R3_gate_empty_config_main_ui.png). With fablegear-state.json deleted and a valid config, gate_reason was setup_incomplete; '/' redirected to /onboarding with exit label 'Finish later' (quits the app) and every input blank (R3_gate_state_deleted_wizard.png). Finishing that wizard overwrites the config (see wizard-live-reconfigure-wipes). Round 1 J_gate.json showed the same for invalid JSON and for a config missing music_root (both 'ready'), and a corrupt state file (back to the wizard).
- **Fix:** Have the gate validate config (parse it and require local_db, device_db and music_root), and return a 'config_invalid' reason that opens reconfigure pre-filled. If config is valid but the state file is missing or corrupt, rebuild setup_complete=true, or open reconfigure mode with prefill instead of first-run mode.

#### [MEDIUM · demonstrated] Nonexistent paths, a non-Rekordbox file as the DB, a directory as the device DB and an unwritable archive are all accepted and marked setup-complete

- **ID:** `wizard-live-no-path-validation` · **Area:** install-wizard · **Tool/surface:** confirmPaths (onboarding.html:1338-1376) + /api/onboarding/save-config (app.py:1677-1720)
- **Expected:** Each path is checked at Confirm: the file exists, it opens as an encrypted Rekordbox master.db, the device DB is a file, the music root is a directory, and the archive/backup dir is creatable and writable. Clear inline errors are shown.
- **Actual:** Only emptiness is checked. Users end up in a main UI that silently cannot read their library or write savepoints.
- **Evidence:** Round 1 E_nonexistent.json: local_db=/nonexistent/rekordbox/master.db, device_db=/Volumes/NOPE/…, music_root=/nonexistent/Music. Confirm went to step 5 with no notice. Import returned 400 'not a directory', but save-config had already set setup_complete:true. Skip then Record Room opened the main UI; /api/status showed local/device_db_ok false, /api/audit/path-roots returned 500, and no toast or banner appeared. E_notrekordbox.json: local_db=not_a_db.txt and device_db=a music folder were both accepted, and /api/status reported local_db_ok:true and device_db_ok:true; library sync 'skipped 11' with no error. E_unwritable_archive.json: custom archive /proc/fablegear-archive was accepted, setup completed, and the archive was never created. Screenshots: E_*_confirm.png, E_*_main_ui.png.
- **Fix:** Add a /api/onboarding/validate-paths endpoint that checks existence and type, tries to open the DB with pyrekordbox (or checks the SQLCipher header), and mkdir/touch-tests the archive. Call it from confirmPaths and again in save-config. Show per-field errors.

#### [MEDIUM · demonstrated] 'Limited mode' and users without a DJ drive still must supply both Rekordbox DB paths; the only way through is typing a fake path

- **ID:** `wizard-live-limited-mode-needs-db-paths` · **Area:** install-wizard · **Tool/surface:** confirmPaths (onboarding.html:1347-1350) + save-config required fields (app.py:1689-1692)
- **Expected:** Limited mode, and setups without a DJ drive, can finish with the DB paths left blank or marked 'not configured'.
- **Actual:** Both DB paths are hard-required on the client and the server, whatever the consent choice.
- **Evidence:** Round 1 E_empty.json: with both DB fields empty, Confirm shows 'Please fill in the database paths before continuing.' even after 'Skip — limited mode'. Round 2 R2_table_s4_results.png: scan finds no device DB ('No DJ drive detected — connect your drive and re-scan, or enter path manually'). E_nonexistent.json shows that a bogus device_db path is accepted. So a user with no DJ drive (or no Rekordbox) can finish only by entering a fake path, which is then saved as real config.
- **Fix:** Make device_db optional (and local_db optional when db_read=false) in confirmPaths and save-config. Store null or empty, and let the main UI show 'not configured' states.

#### [MEDIUM · demonstrated] Music root can't be typed (readonly); if the scan finds nothing and the native picker fails, step 4 can't be completed, and Re-scan wipes the choice

- **ID:** `wizard-live-musicroot-readonly` · **Area:** install-wizard · **Tool/surface:** step-4 music root (onboarding.html:690, 1297-1307, 1257)
- **Expected:** A typed path is accepted (validated). A picker failure is shown to the user. Re-scan keeps a valid existing selection.
- **Actual:** Completing the wizard depends entirely on the scan or a working native dialog, and failures are silent.
- **Evidence:** Round 2 R2_table.json: music_root_readonly=true. Typing produced '' (music_root_after_typing). Open Finder on Linux (no pywebview API, /api/pick-folder has no osascript) left the value '' with no notice: the failure is only console.warn'd (onboarding.html:1306). The sandbox scan found music_roots=[]. Confirm then showed 'Select your home library folder…' (confirm_without_music_root). R2_back.json confirm_after_rescan: after Re-scan, the previously selected root was cleared (populateScan sets musicInput.value='' at onboarding.html:1257), and Confirm failed again. On macOS the pywebview picker normally works; that path was not testable here (inferred).
- **Fix:** Remove readonly (or add an 'enter path' toggle) and validate on change. Surface picker errors through obNotice. In populateScan, only clear the music root if it is no longer valid.

#### [MEDIUM · demonstrated] Double-clicking 'Skip' on step 1 also clicks step 2's 'Skip — limited mode', silently declining Rekordbox access

- **ID:** `wizard-live-dblclick-skip` · **Area:** install-wizard · **Tool/surface:** goToStep / step-1 & step-2 action rows
- **Expected:** One activation per step transition; a consent decision is never made by the second half of a double-click.
- **Actual:** The consent choice is set by accident and nothing on screen says so.
- **Evidence:** Round 3 dblskip case: page.mouse.dblclick on the centre of the step-1 'Skip' button (bbox x=924.9,y=461.5,w=28,h=29) landed on step 4 with confirmedPaths {db_read:false, db_write:false} (R3_dblskip.json, R3_dblskip_landed.png). Round 1 F_dblclick.json shows the same for step-1 Skip. Correct double-submit handling also observed there: 'Enable AI' sent one save-config plus one mcp/enable; 'Import selected' sent one save-config plus one import-sources (both buttons disable). Step-7 double-click sent save-config twice (harmless).
- **Fix:** Ignore clicks for about 400ms after a step transition (or disable the new step's action row until its enter animation ends). Avoid putting a destructive or consent-declining button in the same spot as the previous step's advance button.

#### [MEDIUM · demonstrated] 'Resumed where you left off' restores only the step number and text fields; consents, scan state, archive mode and the master.db checkbox are lost (and resume is browser-profile local)

- **ID:** `wizard-live-resume-partial` · **Area:** session-resume · **Tool/surface:** saveOnboardingProgress / restoreOnboardingProgress (onboarding.html:862-894)
- **Expected:** Resume restores every answer the user gave, or re-asks those that can't be restored. The resume notice is accurate.
- **Actual:** Partial restoration feeds the step-7 trap and the consent flip, and silently drops snapshot and archive choices.
- **Evidence:** Round 1 B_resume_0_1_2_3_4_5_6_7.json: reload and server restart at every step 0-7 resumed the same step index with the notice. Lost every time: confirmedPaths (reset to {}), scanConsent (back to null, so the consent card shows again), scan results, archive mode (custom→auto at steps 5-7), and the master.db checkbox (true→false). The checkbox is saved as el.value 'on' whatever its checked state (line 872) and restored by setting .value, not .checked (line 886). The 'fresh_profile' runs started at step 0, so progress is browser-local only. r2_resume.log: kill -9 at steps 3 and 4 behaved the same as a reload. Round 3 R3_trap7.json: masterDbChecked true→false after a reload. No server-side writes happen before step 5 (disk snapshots unchanged), which is good.
- **Fix:** Serialize a full wizard model {step, confirmedPaths, scanConsent, archiveMode, checkbox states, mcp, importDone} into localStorage and, ideally, a server-side ~/.fablegear/onboarding-draft.json, so a WebView storage reset doesn't lose it. Restore checkboxes with .checked and radios by value.

#### [MEDIUM · demonstrated] Long operations have no cancel; navigating Back during an import gets yanked to step 7; the library scan is a blocking request with no progress

- **ID:** `wizard-live-long-ops` · **Area:** install-wizard · **Tool/surface:** importSelectedSources (onboarding.html:1501-1578), triggerScan (1138-1163), installDeps/pollDeps (978-1022)
- **Expected:** Long operations can be cancelled, and leaving the step doesn't produce surprise navigation.
- **Actual:** The user can't stop a mis-chosen import, and gets moved between steps without asking.
- **Evidence:** Round 1 G_import_back.json: during an 8000-file import, Back is enabled and goes to step 5. When the import finishes, the still-running poll calls goToStep(7) (line 1568), and the user was pulled to step 7 after 7.1s from wherever they were. There is no cancel control and no cancel endpoint (_OB_IMPORT has no stop flag, app.py:1593-1637). G_import_reload.json (good): a reload mid-import lands on '/' with a background pill 'Importing 4,772/8,000' and a 'Background import complete: 8000 new' toast. G_scan_reload.json: the scan is a single GET with no cancel; a reload mid-scan returns to the consent card. Dependency install was observed on Linux only: install-deps returns 500 with "[Errno 2] No such file or directory: 'open'", and the UI shows 'Could not start the installer… Run setup.sh manually, or Skip for now' (G_install_deps.json). The poll ceiling of 180×5s was not exercised (inferred).
- **Fix:** Add a cancel flag to _OB_IMPORT that FileImporter checks between files, plus a Cancel button. Only auto-advance if currentStep is still 6. Run the scan as a background job with status polling and a cancel option.

#### [MEDIUM · demonstrated] The main app's body padding and 10px root font leak into the wizard: the content area is about 60% of the window, the primary buttons sit below an inner-scroll fold, and the text is tiny

- **ID:** `wizard-live-layout-clipped` · **Area:** ui-layering · **Tool/surface:** templates/onboarding.html + static/fablegear.css
- **Expected:** The wizard uses the full window, its primary action is visible without hidden inner scrolling, and its text is readable.
- **Actual:** The content is cut off at roughly 60% of the window height, with a 4px scrollbar as the only cue. Text is very small, and the exit control can be covered by the update banner.
- **Evidence:** Round 2 R2_layout.json at 1400x900: body padding is '28px 0 318px 80px' (from fablegear.css:66-76, padding-left var(--left-panel-w) and padding-bottom calc(var(--log-h)+var(--scan-bar-h)+4px)). The .ob-wrap scroller is 554px tall (bottom 582) for 978px of step-4 content. 'Confirm paths' sits at y=974, hit-test BODY, and becomes reachable only after wheeling inside the wrap; wheeling in the dead area scrolls 0. At 900x600 the wrap is 344px. Round 1 K_layout.json at 1024x700: wrap 414px; step-0 'Skip for now' and step-3 'Back' are clipped (hit BODY); the update banner overlaps the 'Finish later' pill (overlapExitBanner:true). fablegear.css:65 sets html{font-size:10px}, so the wizard's rem sizes (e.g. .82rem) render at about 8px. Screenshots: R2_table_s4_results.png, R2_layout_1400x900_step4_manual.png, K_1024x700_step*.png, K_1024x700_update_banner.png.
- **Fix:** Give onboarding.html a body class that resets padding (e.g. body.ob-page{padding:0}) and set html{font-size:16px}, or stop loading the full fablegear.css. Make the step's .ob-actions sticky at the bottom of .ob-wrap. Offset #ob-exit below the update banner.

#### [LOW · demonstrated] App-install step: masked 500, stray ~/Applications, a button stuck at 'Installing…' after Back, silent advance on network error, insecure tempfile.mktemp

- **ID:** `wizard-live-install-app-errors` · **Area:** install-wizard · **Tool/surface:** installApp (onboarding.html:1032-1062) + /api/onboarding/install-app (app.py:1508-1582)
- **Expected:** Install failures produce a specific, actionable message. Nothing is created on failure. Button state resets on every path.
- **Actual:** The install step mostly fails cleanly, with these rough edges.
- **Evidence:** Round 1 G_install_app.json (Linux): POST install-app returned 500 {error:'internal_error', message:'Something went wrong.'}. The UI notice read 'Installation failed: internal_error', and home/Applications was created anyway (install_dir.mkdir at app.py:1523, before the attempt; the FileNotFoundError from the missing osacompile is not caught). Round 2 R2_back.json: with 'Add to Applications' unchecked, Install disables the button and sets its text to 'Installing…', then goes to step 2 (line 1040). After Back, install_btn_after_back = {text:'Installing…', disabled:true}, so the step is stuck (R2_back_install_btn_stuck.png). Inferred: `.catch(function(){ goToStep(2); })` (line 1061) silently advances on a network error, and app.py:1537 uses tempfile.mktemp, which has a name race.
- **Fix:** Catch OSError and FileNotFoundError around osacompile and return a clear message. Create ~/Applications only on success. Reset the button before the early goToStep(2) and in the catch. Use tempfile.NamedTemporaryFile or mkstemp.

#### [LOW · demonstrated] No Back on step 1 or step 7, and step dots aren't navigable

- **ID:** `wizard-live-missing-back` · **Area:** install-wizard · **Tool/surface:** templates/onboarding.html step-1/step-7 action rows
- **Expected:** Every step after 0 offers Back, including the summary step.
- **Actual:** Users can't return to dependencies from step 1, or fix anything from the summary. This combines with wizard-live-trap7.
- **Evidence:** R2_table.json rows: s1 buttons are ['Skip', 'Install & continue →'] with no Back; s7 buttons are only the two room cards. C_trap7.json dots_clickable:false. After a resume, dots for steps 1-6 aren't marked done (R3_trap7_after_restart_still_trapped.png). Back on steps 2-6 works and keeps every value (R2_back.json rows b_s4…b_s2 keep inputs and confirmedPaths).
- **Fix:** Add '← Back' to steps 1 and 7. Make done dots clickable for backward navigation. Re-mark dots 0..n-1 as done on resume.

#### [LOW · demonstrated] After choosing a room on wizard step 7, the main UI shows the room picker again

- **ID:** `wizard-live-room-picker-repeat` · **Area:** install-wizard · **Tool/surface:** launch() → /?space=record + main UI room picker
- **Expected:** The ?space= choice from the wizard is honoured without asking again.
- **Actual:** The user is asked the same question twice in a row.
- **Evidence:** R3_consentflip_main_ui.png: after clicking 'Record Room' on step 7, the main UI opened with the FableGear room-picker modal (Record Room / Chop Shop) over the Record Room.
- **Fix:** Skip the startup room picker when a ?space= parameter is present, or on the first load after onboarding.

#### [INFO · demonstrated] Confirmed-good behaviour: Finish later on every step, Back keeps values, leaving reconfigure without finishing is non-destructive, no early server writes, double-submit guards, import survives reload

- **ID:** `wizard-live-good` · **Area:** install-wizard · **Tool/surface:** onboarding wizard
- **Expected:** n/a
- **Actual:** Works as intended.
- **Evidence:** D_finish_later.json for steps 0-7: confirm dialog; Cancel keeps the step and the server alive; Accept quits (own sandbox app copy, so E6 is not triggered across auditors) and shows 'You can close this window…'; relaunch resumes the same step. R2_back.json: Back from 5→4→3→2 keeps every input and confirmedPaths. R3_reconf.json exit_cfg_unchanged=true and exit_url '/'. B_resume disk snapshots show no config or state writes before step 5. F_dblclick.json: Enable-AI and Import send exactly one request each. G_import_reload.json: a reload mid-import goes to the main UI with a progress pill and a completion toast. user_config.save_user_config writes atomically (temp file then rename, user_config.py:279-283).
- **Fix:** Keep these behaviours when fixing the findings above (especially the atomic config write and non-destructive exit from reconfigure).

## Installers (shell first-run, in-app wizard, unmerged GUI installer) — code

_Auditor: `wizard-code`_

I audited the code of both installers and the update path: launch.sh, setup.sh, install.sh, the bootstrap launcher in build_release.sh, packaging/, single_instance.py, main.py, update_checker.py, /api/update/apply and the in-app onboarding wizard (onboarding.html and /api/onboarding/*). I also audited the unmerged origin/feature/install-wizard installer. No product server was started. Instead I re-ran the pure-logic parts in throwaway directories with stubs, against real git and real setup.sh/launch.sh code blocks, and ran the branch wizard's Flask app under Playwright with stubbed commands.

Verdict: neither installer has a dependable checkpoint, exit or rollback story.
- **Shell first-run path.**
  - Steps are idempotent, so re-running restarts and mostly converges.
  - A fresh Mac whose only Python is Apple's 3.9 gets stuck in a venv it never rebuilds (demonstrated with stubs).
  - setup.sh keeps no log and sets no trap, so a closed Terminal window leaves launch.sh waiting silently for up to 40 minutes (demonstrated).
  - The lock goes stale at 30 minutes while setup is allowed 40, so a second setup.sh starts (demonstrated).
  - None of the .app wrappers shows a failure in the GUI.
- **Update path.**
  - All three lead claims are confirmed. The in-app update moves the install to untagged main, launch.sh then leaves it there, and the version shown is the old tag (demonstrated).
  - A release tag that is not on main makes launch.sh hard-reset the local branch, after which the in-app updater fails with "Not possible to fast-forward" (demonstrated).
  - /api/update/apply never rolls back. launch.sh's rollback runs again on every launch with no GUI message (demonstrated).
  - The SIGTERM shutdown handler is never installed in the desktop app, so Quit and Update skip the archive sync.
- **In-app wizard.** The consent answers and confirmed paths are kept in page memory only. After a restart, save-config therefore treats missing consent as granted, and the user can get stuck on step 7, which has no Back button (code root cause; the wizard-live auditor reproduced both). setup_complete is also written partway through the wizard (steps 5 and 6).
- **Branch installer.** All four leads are confirmed live. On top of them, double-clicking Continue on the System tools step skips the Python package install entirely (3 of 3 trials), and a failed optional essentia install blocks the whole wizard.

**Coverage**

- LIVE (throwaway dirs, stubs, real git/bash): launch.sh lines 106-165 (update block) run verbatim against a throwaway origin repo with stubbed curl, pip and osascript. Scripts and output: <scratch>/audit/wizard-code/upd/run_update_demo.sh and .out, where <scratch> = /tmp/claude-0/-home-user-FableGear/9074577f-2e6a-5e16-bdce-ef5b26f1b16b/scratchpad
- LIVE: the whole launch.sh first-run and lock section run in a throwaway dir with a stub `open` command (no Terminal, no setup actually runs). Script and output: <scratch>/audit/wizard-code/lock/run_lock_demo.sh and .out
- LIVE: setup.sh run under SIGHUP with a stubbed Homebrew installer: <scratch>/audit/wizard-code/setup/
- LIVE: setup.sh lines 90-148 (Python and venv section) run verbatim with stub interpreters (CLT python3 3.9, then python3.12): <scratch>/audit/wizard-code/pyvenv/run_py_trap.sh and .out
- LIVE: the origin/feature/install-wizard Flask app (extracted to <scratch>/audit/wizard-code/iw_branch) run through iw_harness.py with stubbed checks and commands. It listened on an OS-assigned ephemeral 127.0.0.1 port: I had no assigned port, and this avoids collisions with other auditors. Tests: disconnect with orphaned children, a dropped connection under Playwright (iw_ui.py / iw_ui.json), a bare /api/complete call, and double-click Continue (iw_dbl.py / iw_dbl.json). Screenshots are in /home/user/FableGear/docs/audits/2026-10-05/screens/wizard-code/
- LIVE: bash -n on every shell script (all pass). shellcheck (installed via shellcheck-py) output is in <scratch>/audit/wizard-code/shellcheck.txt; only minor items (launch.sh:111 SC2164, launch.sh:178 SC2155, setup.sh:99 SC2034).
- LIVE: the Python rule that signal.signal() raises ValueError outside the main thread (this is the reason app.py's SIGTERM handler is never installed under main.py)
- FETCHED for citation: Homebrew's current install.sh, saved to <scratch>/audit/wizard-code/dl_homebrew/install.sh (non-TTY non-interactive logic at lines 129-145, 293-299 and 594-595)
- CODE ONLY: app.py /api/update/apply, /api/onboarding/*, the gate, /api/quit; main.py; single_instance.py; update_checker.py; install.sh; the build_release.sh bootstrap; packaging/*; onboarding.html JS state machine
- I did not drive the in-app onboarding wizard live; the wizard-live auditor did. I cite their artifacts (<scratch>/audit/wizard-live/C_consent_write.json, C_trap7.json, J_gate.json, G_install_*.json) only as corroboration, and label those findings inferred from code.
- NOT TESTABLE on Linux: open -a Terminal, osacompile, the Dock plist and cfprefsd, the real Homebrew install, sudo prompts, Full Disk Access, pywebview window-close semantics, WKWebView, and power loss in the middle of a git checkout (left speculative). I ran no real pip or brew installs. fpcalc is not involved here.

**Findings**

#### [HIGH · demonstrated] setup.sh builds the venv from Apple's Python 3.9, aborts as 'too old', and never rebuilds it, so following the on-screen advice can never fix it

- **ID:** `wizard-code-setup-py39-venv-trap` · **Area:** install-wizard · **Tool/surface:** setup.sh
- **Expected:** Candidates outside 3.11-3.13 are rejected, and python@3.13 is installed when none qualifies. An existing venv whose interpreter is too old, or not the chosen one, is recreated automatically.
- **Actual:** Setup fails the same way on every run until the user deletes ~/FableGear/venv by hand. Nothing on screen tells them to.
- **Evidence:** <scratch>/audit/wizard-code/pyvenv/run_py_trap.sh runs setup.sh lines 90-148 verbatim with PATH limited to stub interpreters. Run 1 has only python3, reporting 3.9 like Apple's CLT /usr/bin/python3: 'OK Python 3.9.0 ... ✗ Python 3.9.0 in this venv is too old ... Run: brew install python@3.12', then fail(). Next, a python3.12 stub is added, as if the user did what the message says. Runs 2 and 3 both print 'OK Python 3.12.0' and then the same '✗ Python 3.9.0 in this venv is too old', and fail(). The final venv is still 3.9. Root cause: setup.sh:97-107 only rejects minor > 13 (`[ "${major:-99}" -le 13 ]`), so 3.9 is accepted. setup.sh:120-128 only rebuilds the venv when bin/activate is missing. setup.sh:140-147 fails on the existing old venv without removing it. Precondition (how common it is, speculative): python3.11, 3.12 and 3.13 are all missing from PATH, e.g. a fresh Mac where nothing brewed a Python.
- **Fix:** In the candidate loop, require 11 <= minor <= 13. Before reusing $VENV, compare `$VENV/bin/python -c 'import sys;print(sys.version_info[:2])'` with the minimum and `rm -rf` it when too old. Write a marker file recording the interpreter used, and rebuild the venv when it changes.

#### [HIGH · demonstrated] Branch install wizard: double-clicking Continue on 'System tools' skips the Python package step, writes .fablegear_ready and launches the app with no packages

- **ID:** `wizard-code-iw-dblclick-skips-packages` · **Area:** install-wizard · **Tool/surface:** installer_wizard.html (origin/feature/install-wizard)
- **Expected:** Continue is disabled synchronously on click, and /api/complete is only reachable after the package step reports [SEQUENCE_OK].
- **Actual:** One double-click marks the install complete and hands off to main.py with no packages installed.
- **Evidence:** <scratch>/audit/wizard-code/iw_dbl.py: Get Started, then Continue, then the stubbed brew step finishes, then a Playwright dblclick() on #primaryBtn. Result in iw_dbl.json, 3 of 3 trials: the requests were GET /api/plan, then POST /api/complete, then GET /api/install/python. The step label showed 'Done', sentinel_written was true and wizard_process_exited was true. Root cause: transitionToStep() sets `step = n` synchronously (html:710). The step-2 branch calls showPlan() (html:990-991), which only disables the button after the /api/plan fetch resolves. The second click therefore lands in `else if (step === 3)` (html:992-997) and calls fetch('/api/complete'). That writes the sentinel and runs os._exit(0) (installer_wizard.py:379-400), killing any pip that had started.
- **Fix:** Set els.primary.disabled = true before any async work in every branch of the click handler. Track the phase result explicitly (pythonOk=true only on [SEQUENCE_OK]) and require it before calling /api/complete. Also make the server refuse (see wizard-code-iw-complete-unconditional).

#### [HIGH · demonstrated] Branch install wizard: /api/complete writes .fablegear_ready without checking that any install sequence succeeded

- **ID:** `wizard-code-iw-complete-unconditional` · **Area:** install-wizard · **Tool/surface:** installer_wizard.py /api/complete
- **Expected:** The server records each phase's exit status and refuses /api/complete (409) unless brew and the required Python groups succeeded.
- **Actual:** Any POST marks setup complete; launch.sh then skips setup.sh, and its own pip runs (launch.sh:98-99) are unchecked and log-only.
- **Evidence:** <scratch>/audit/wizard-code/iw_ui.py run B started a fresh wizard and made a bare `curl -X POST /api/complete` with nothing installed. Response {"ok":true}, sentinel_after_bare_complete=true, harness2_exit_code=0 (os._exit after spawning main.py). Code: installer_wizard.py:379-383 calls `(REPO_ROOT / ".fablegear_ready").touch()` unconditionally. stream_steps (py:285-299) keeps no server-side record of [SEQUENCE_OK]. tests/test_installer_wizard.py:305 (on the branch) asserts this unconditional behaviour. The sentinel matters because launch.sh:_setup_needed (launch.sh:37-50) checks only brew formulas, venv/bin/activate and the sentinel, not the Python packages.
- **Fix:** Keep a server-side phase_status dict set only on [SEQUENCE_OK]. Gate /api/complete on it. Add a test that /api/complete fails before the installs.

#### [HIGH · demonstrated] Branch install wizard: a dropped SSE connection (server crash, window reload, network blip) is shown as a successful install

- **ID:** `wizard-code-iw-dropped-connection-success` · **Area:** install-wizard · **Tool/surface:** installer_wizard.html streamInstall()/runPythonStep()
- **Expected:** Success only on an explicit [SEQUENCE_OK]. A connection error before it is shown as 'interrupted', offering Retry or Re-check.
- **Actual:** An interrupted install is reported as done, and the user is sent to Finish or the next phase.
- **Evidence:** <scratch>/audit/wizard-code/iw_ui.py with output in iw_ui.json. During the stubbed 60 s Python step, the wizard server was sent SIGKILL. 1.5 s later the button read 'Finish' (enabled), all three cards were 'grp is-done' (including requirements.txt and requirements_optional.txt, which never ran), and the failure details panel stayed closed. Clicking Finish showed 'You're all set — Launching now'. fetch('/api/complete') has no catch (pageerror 'Failed to fetch'), so nothing launches. Screenshots: docs/audits/2026-10-05/screens/wizard-code/iw_02_dropped_connection_shown_as_success.png and iw_03_all_set_with_dead_server.png (the panels in iw_03 are stacked; see the low finding). Code: html:792 `es.addEventListener("error", () => { es.close(); onDone && onDone(!failed); })`; html:961-975 has the same pattern in the Python step. Success is defined as 'no [SEQUENCE_FAILED] seen', not '[SEQUENCE_OK] seen'.
- **Fix:** Track `sawOk` and call onDone(sawOk && !failed) from the error handler. Handle fetch('/api/complete') failure and only show 'all set' after a 200.

#### [HIGH · demonstrated] Branch install wizard: a failure in the best-effort essentia group fails the whole Python step, and the only offer is 'Try Again', so the user is trapped

- **ID:** `wizard-code-iw-optional-blocks-install` · **Area:** install-wizard · **Tool/surface:** installer_wizard.py python_install_commands/stream_steps
- **Expected:** Optional groups run with failures tolerated and shown as 'degraded'. Finish stays available.
- **Actual:** On macOS 12-14 with Python 3.13, the wizard can never reach Finish.
- **Evidence:** Running the branch module directly: build_plan() marks ('requirements_optional.txt', False), but python_install_commands() returns a plain `pip install -r .../requirements_optional.txt` as the 4th step of the same fail-fast sequence (py:330-338). `stream_steps([[true],[true],[bash -c 'echo ERROR: Failed building wheel for essentia; exit 1']])` ends with 'data: [SEQUENCE_FAILED]' (py:285-299). The UI then offers only 'Try Again', which re-runs everything (html:971). There is no Skip, Cancel or Finish-later. requirements_optional.txt:23-33 itself says there is no cp313 essentia wheel below macOS 15, so pip tries an sdist build that fails. setup.sh:177-183 and launch.sh:104 treat this group as best-effort. The branch wizard does not, and test_stream_steps_stops_at_first_failure enshrines that.
- **Fix:** Run the required groups in one fail-fast sequence and the optional group as a separate stream whose failure is non-fatal. Pass group.required through to the client.

#### [HIGH · inferred] Homebrew is installed with non-TTY stdin by the documented `curl | bash` install and by the branch wizard, so Homebrew goes non-interactive (`sudo -n`) and aborts on a normal Mac

- **ID:** `wizard-code-homebrew-noninteractive` · **Area:** install-wizard · **Tool/surface:** install.sh -> setup.sh; installer_wizard.py brew_install_commands
- **Expected:** Homebrew is installed in a context with a TTY (`/bin/bash -c "$(curl ...)" < /dev/tty`, or INTERACTIVE=1 with a tty) or via the official .pkg. Formula commands resolve brew after installing it.
- **Actual:** The documented one-liner and the GUI wizard cannot install Homebrew on a Mac without cached sudo credentials.
- **Evidence:** install.sh:6 documents `curl -fsSL .../install.sh | bash`. The script runs `bash "$INSTALL_DIR/setup.sh"` (install.sh:89) with stdin = the curl pipe. setup.sh:51 runs `/bin/bash -c "$(curl ...Homebrew/install/HEAD/install.sh)"`, which inherits that stdin. In Homebrew's current install.sh (fetched to <scratch>/audit/wizard-code/dl_homebrew/install.sh), lines 135-140 set `elif [[ ! -t 0 ]] ... NONINTERACTIVE=1` and lines 297-299 add `SUDO+=("-n")`. Lines 594-595 then `abort "Insufficient permissions to install Homebrew..."` when /opt is not writable and sudo -n fails. The branch wizard deliberately uses `curl ... | /bin/bash` (installer_wizard.py:319-324), so the inner bash's stdin is the pipe there too. Also, setup.sh fail() runs `read -rp` (setup.sh:21) on that pipe, which swallows a line of install.sh instead of waiting. Separately (demonstrated): with brew absent, brew_install_commands() returns `['brew','install','ffmpeg']` with a bare 'brew', resolved before Homebrew exists (py:302-327). Under a GUI PATH (main.py:47-54 explains it lacks /opt/homebrew/bin), the first attempt therefore fails with '[ERROR] could not start'.
- **Fix:** In install.sh, run setup.sh with `< /dev/tty`. In the wizard, use Homebrew's .pkg installer or open Terminal for that one step. Compute the brew path after the Homebrew step (call _find_brew() per command, or re-plan after step 1).

#### [HIGH · demonstrated] In-app Update pulls untagged origin/main; launch.sh then leaves it there and the app reports the old release tag. An off-main release tag makes launch.sh hard-reset the branch, which breaks the in-app updater

- **ID:** `wizard-code-update-untagged-main` · **Area:** undo-revert · **Tool/surface:** app.py /api/update/apply + launch.sh + update_checker.py
- **Expected:** Every updater targets the same release tag (`git fetch --tags && git merge --ff-only <latest tag>`), and the version shown matches HEAD.
- **Actual:** Users run untested main while the UI shows the release tag. After an off-main hotfix, the in-app updater stays broken until a later launch.sh realign.
- **Evidence:** <scratch>/audit/wizard-code/upd/run_update_demo.sh (output in .out). Setup: origin has A (tagged v1.0), then B (main, untagged); the user sits on v1.0. Step 1, app.py:784's `git pull origin main --ff-only`: HEAD = 'B (untagged main)'. Step 2, launch.sh's update block with GitHub latest = v1.0: no-op and empty log (launch.sh:117-118 sees describe == LATEST_TAG; lines 140-143 never downgrade a descendant). FABLEGEAR_VERSION and update_checker report 'v1.0' while running B. Step 3, hotfix v1.0.1 tagged on a release branch: update_checker flags an update (is-ancestor rc=1); in-app Update gives 'Already up to date.' with rc=0, so the API returns ok and relaunches; launch.sh logs 'clone diverged from v1.0.1; realigning' and runs `git reset --hard v1.0.1`, after which local main != origin/main. Step 4, next release v1.1 on main: the in-app update fails with 'fatal: Not possible to fast-forward, aborting.' (rc=128). Also: the docstring (app.py:736) says 'Pull the latest release'. The comment at app.py:802-805 says 'launch.sh no longer pulls or reinstalls on every open; this ... is now the only updater', which is false (launch.sh:98-104, 106-165). install.sh:44 and :61 also track main.
- **Fix:** Make /api/update/apply fetch tags and ff-merge to update_checker's latest_version (same logic as launch.sh:122-139). Compute the version with `git describe --tags` without --abbrev=0, or flag 'tag+N'. Make install.sh check out the latest release tag.

#### [HIGH · inferred] /api/update/apply has no rollback: pip failures are ignored, and a half-applied update (new code, missing deps) leaves an app that silently never opens

- **ID:** `wizard-code-update-no-rollback-brick` · **Area:** undo-revert · **Tool/surface:** app.py /api/update/apply, launch.sh, main.py
- **Expected:** Record HEAD before the pull. If pip fails, or the new build does not come up healthy within N seconds, reset to the recorded HEAD, reinstall from the old requirements, and surface the error in the GUI.
- **Actual:** The update is fire-and-forget with no failure surface and no way back except manual git.
- **Evidence:** app.py:800-821 runs `pip install --upgrade` with check=False and capture_output, and discards the result. There is no PREV_HEAD, and git is never reset. It then relaunches (app.py:823-838). On relaunch, launch.sh:98-99 pip installs are unchecked (log only). The tag logic will not roll back because HEAD is now a descendant of the tag (launch.sh:140-143). main.py imports app inside the server thread (main.py:80-84). If that import fails (missing module), _wait_for_server gives up after 30 s and does `sys.exit(1)` with the message only in fablegear.log (main.py:171-176). No window or dialog appears, and every relaunch repeats this until pip succeeds. The docstring step 1 'Refuse if ... Rekordbox is open' (app.py:739) is not implemented; only managed subprocesses are checked (app.py:744-752). Speculative: a git pull killed by the 60 s timeout (subprocess.run SIGKILL) or by power loss mid-checkout can leave .git/index.lock or a mixed tree. Every later update then fails ('fast-forward failed' is logged silently by launch.sh:138), and nothing removes the lock.
- **Fix:** Write the pre-update SHA to ~/.fablegear/update_pending.json. In launch.sh/main.py, if the server fails to start and that file exists, `git reset --hard <sha>` plus pip install, then show a native alert (osascript display dialog) pointing to fablegear.log. Return a pip failure from the API as an error before the restart.

#### [HIGH · inferred] In-app wizard: consent answers are not persisted, and save-config treats missing db_read/db_write as granted, so after a restart a declined write permission is saved as granted

- **ID:** `wizard-code-onboarding-consent-default-true` · **Area:** session-resume · **Tool/surface:** templates/onboarding.html + app.py /api/onboarding/save-config
- **Expected:** Consent is persisted with the draft (or re-asked on resume), and the server never defaults consent to granted.
- **Actual:** A user who declined write access gets write access recorded after any refresh, quit or crash between the permission step and the save.
- **Evidence:** confirmedPaths is in memory only (onboarding.html:852). saveOnboardingProgress stores only {step, 6 path inputs} (html:867-875). restoreOnboardingProgress jumps to the saved step (html:891), so permGrant/Deny (html:1081-1105) are never replayed. confirmPaths copies `db_read: confirmedPaths.db_read` (html:1370-1371), which is undefined, and JSON.stringify drops the key. app.py:1729-1730 `bool(data.get("db_read", True))` / `bool(data.get("db_write", True))` then writes true. scanConsent, the MCP choice and the archive mode are also not persisted. Corroborated live by the wizard-live auditor (<scratch>/audit/wizard-live/C_consent_write.json): before reload {db_read:true, db_write:false}; after reload state_file.db_write=true, while the summary still said 'Limited mode'.
- **Fix:** Persist confirmedPaths (minus secrets) and scanConsent in the draft. On resume to a step after 2-3 with consent unknown, send the user back to step 2. Change the server defaults to False/None and reject a request when the key is missing.

#### [HIGH · inferred] In-app wizard: resuming at step 7 (or reloading there) leaves the user permanently stuck: step 7 has no Back, launch() always fails with 400, and the draft is never cleared

- **ID:** `wizard-code-onboarding-step7-trap` · **Area:** session-resume · **Tool/surface:** templates/onboarding.html
- **Expected:** Resume validates that the data needed for the saved step exists; otherwise it rewinds to the path step. Every step has a Back control.
- **Actual:** The only way out is clearing WKWebView localStorage by hand.
- **Evidence:** Step 7 markup (html:827-845) has only the two launch('record'|'chop') buttons, with no Back or Skip, and the dots are not clickable. After a restore, confirmedPaths is {} (html:852), so launch() posts {} (html:1604-1609) and gets 400 'Missing required fields'. clearOnboardingProgress runs only on success (html:1615), so every relaunch restores step 7 again (html:889-892). 'Finish later' on first run quits (html:901-922). Corroborated live by wizard-live (C_trap7.json: after_second_reload_step 7; notice 'Could not save configuration: Missing required fields: music_root, local_db, device_db'; gate config_missing).
- **Fix:** Persist confirmedPaths. In restoreOnboardingProgress, if saved.step >= 5 and the required paths are missing, go to step 4. Add Back to step 7. On a save-config 400, show a 'Review paths' button that calls goToStep(4).

#### [MEDIUM · demonstrated] launch.sh retries the same failing release update on every launch: fast-forward, pip fails, rollback, with no record, backoff or GUI notice, and the venv is left partially upgraded

- **ID:** `wizard-code-launch-rollback-loop` · **Area:** undo-revert · **Tool/surface:** launch.sh
- **Expected:** A failed update is recorded and skipped (or retried with backoff), the venv is restored (e.g. from `pip freeze`), and the user is told.
- **Actual:** Each launch is slowed by a doomed update attempt, and the user sees nothing.
- **Evidence:** run_update_demo.sh scenario 5 makes the stub pip fail on `--upgrade -r requirements.txt`. Three launches each logged 'updating v1.0 -> v1.1 / Fast-forward / dependency install failed after update; rolling back / HEAD is now at b97f91f A', with 6 pip calls in total. requirements_ui.txt was upgraded before requirements.txt failed, and the git reset (launch.sh:130-135) does not undo that, so the old code runs on partially upgraded packages. All output goes to fablegear.log after `exec > /dev/null` (launch.sh:82). The realign branch (launch.sh:156-159) does not check pip at all.
- **Fix:** Write a .update_failed_<tag> marker and skip that tag until the next release. Snapshot `pip freeze` before upgrading and reinstall it on rollback. Apply the same checks to the realign path.

#### [MEDIUM · demonstrated] The launcher lock goes stale at 30 min while setup may take 40, so a second click re-runs setup.sh on the same venv. The first launcher's EXIT trap then deletes the second's lock. Clicks inside 30 minutes exit silently.

- **ID:** `wizard-code-launch-lock-stale-race` · **Area:** install-wizard · **Tool/surface:** launch.sh / setup.sh / app.py install-deps
- **Expected:** One setup at a time, guarded by a lock held for setup's whole lifetime (e.g. flock or a PID file inside setup.sh). Stale detection uses PID liveness, not age. A second click brings the setup window forward or shows a notification.
- **Actual:** Concurrent setup.sh runs, a lock deleted by the wrong owner, and silent no-op clicks.
- **Evidence:** <scratch>/audit/wizard-code/lock/run_lock_demo.sh (output in .out), using a stub `open`. A: launcher #1 polls with no sentinel (setup window gone). B: a second click exits 0 with no 'open' call and no feedback. C: with the lock at 31 min, launcher #3 takes it over and calls `open -a Terminal .../setup.sh` again (2 opens logged) while #1 is still polling. D: when #1 ends, its trap (launch.sh:26) removes the lock #3 owns; 'lock dir exists while #3 still running? NO-removed-by-#1', and launcher #4 got in concurrently (3 opens). Code: launch.sh:19 `-mmin +30` vs launch.sh:73 `2400` s. Two setup.sh runs race on one venv, and setup.sh:120-123 `rm -rf "$VENV"` can delete a venv the other run is still creating (activate is written last). The in-app 'Install dependencies' path also races (inferred): app.py:1474 opens setup.sh, setup.sh:15 deletes .fablegear_ready immediately, and any Dock click or relaunch while it runs gives launch.sh:48 `_setup_needed`, which opens a second setup.sh (the lock is free because the app's launcher has already exited).
- **Fix:** Have setup.sh take its own lock (mkdir plus a PID file and `kill -0` liveness check, or `flock` via python). In launch.sh, store $$ in the lock and only rmdir if it still holds your PID. Replace the age heuristic with a PID check, and show `osascript display notification` on a blocked click.

#### [MEDIUM · demonstrated] setup.sh has no trap and no log file, and every .app wrapper discards launch.sh's stderr. An interrupted or failed first run is never shown in the GUI, and launch.sh waits silently for up to 40 minutes.

- **ID:** `wizard-code-setup-no-trap-no-log` · **Area:** install-wizard · **Tool/surface:** setup.sh / launch.sh / packaging
- **Expected:** `trap 'touch "$FAILED"' INT HUP TERM ERR`, with output tee'd to ~/FableGear/setup.log. The launcher shows a native alert on failure or timeout that points to the log.
- **Actual:** Closing the setup window, or a brew failure, gives silence; at best the next launch quietly re-runs setup.
- **Evidence:** `grep trap setup.sh` returns nothing. In <scratch>/audit/wizard-code/setup/, setup.sh was sent SIGHUP mid-'Homebrew install' (stub). setup.sh died, no .fablegear_ready or .fablegear_failed was written, and its child was re-parented to PID 1 (orphan). launch.sh:66-77 then polls until 2400 s. Its messages (launch.sh:68, 74) go to stderr, which build_release.sh:65 (`>/dev/null 2>&1 &`), packaging/FableGearLauncher.applescript:5 and the app.py:1536 osacompile script all discard. setup.sh output exists only in the Terminal window, with no tee to a file. setup.sh:84-86 prints '✓ <formula> installed' even when `brew install` fails, and `brew update` / `pip install --upgrade pip` results are ignored (setup.sh:65, 157). Only the osacompile fallback in setup.sh:210-212 (blocking `do shell script`, no redirect) would show launch.sh's stderr as a dialog, so the three .app generators behave differently.
- **Fix:** Add the trap and a `exec > >(tee -a "$SCRIPT_DIR/setup.log") 2>&1`. Check the `brew install` exit status and call fail(). In launch.sh, use osascript to show 'Setup failed or was interrupted — see setup.log' instead of writing to stderr. Unify the .app generators.

#### [MEDIUM · inferred] In the desktop app the SIGTERM/SIGINT 'clean shutdown' handler is never installed, so Quit and Update kill the process without the archive DB sync

- **ID:** `wizard-code-sigterm-handler-not-installed` · **Area:** other · **Tool/surface:** app.py signal handlers / main.py
- **Expected:** Quit and Update perform the documented clean-shutdown checkpoint.
- **Actual:** The checkpoint runs only when the window is closed normally (atexit). Quit and Update skip it.
- **Evidence:** main.py:80-84 imports app inside the server daemon thread (`from app import app` runs at module level in _start_server, which is started at main.py:173). app.py:345-352 calls signal.signal() and swallows ValueError/OSError. Demonstrated Python behaviour: calling signal.signal(SIGTERM, ...) from a non-main thread raises "ValueError('signal only works in main thread of the main interpreter')", and the disposition stays SIG_DFL (0). So the os.kill(SIGTERM) in /api/quit (app.py:1752) and /api/update/apply (app.py:836) terminates the process immediately. Neither _sync_archive_db_on_exit (atexit/handler, app.py:305-338) nor main.py:242-247 runs. The comment at app.py:323-331 claims these paths are routed through the sync. This affects the in-app 'Finish later' (onboarding obExitSetup, then /api/quit) and Update. See also E6 (/api/quit kills every cli.py under the install dir).
- **Fix:** Install the handlers from main.py's main thread (e.g. call an app.install_signal_handlers() after import), or avoid self-SIGTERM: run the sync explicitly in the _shutdown and _relaunch threads before os.kill, or call webview window.destroy() so main returns normally.

#### [MEDIUM · inferred] In-app wizard: opting into MCP (step 5) or starting an import (step 6) writes setup_complete=true before the wizard finishes

- **ID:** `wizard-code-onboarding-setup-complete-midway` · **Area:** install-wizard · **Tool/surface:** onboarding.html _saveConfigNow + app.py save-config
- **Expected:** Mid-wizard saves write the config without setup_complete; only launch() marks completion. The state file is written atomically.
- **Actual:** Completion is recorded early, and drafts go stale.
- **Evidence:** mcpOptIn (html:1415-1421) and importSelectedSources (html:1521) call _saveConfigNow(), which hits /api/onboarding/save-config. That route always writes `"setup_complete": True` (app.py:1727-1739). If the user then chooses Finish later or quits, the gate (app.py:1257-1281) admits them on the next launch, and the remaining steps (import, summary) are skipped silently. The localStorage draft is cleared only by launch() (html:1615), so a later /onboarding?reconfigure=1 resumes the stale step and paths. With alreadyConfigured=false baked in at render time (html:856), 'Finish later' still tells the user that setup must be finished. Both config writes are non-atomic: save_user_config is atomic (user_config.py:260-288), but the state file uses write_text (app.py:1247, 1738). A torn state file is 'repaired' to defaults (setup_complete false, consent null) by _load_setup_state (app.py:1222-1254).
- **Fix:** Add a `complete` flag to save-config, set only by launch(). Clear the draft whenever setup_complete flips. Use the same tmp+rename write for fablegear-state.json.

#### [MEDIUM · inferred] The setup gate admits the main UI with an empty or invalid config.json because it checks only that the file exists

- **ID:** `wizard-code-gate-config-exists-only` · **Area:** install-wizard · **Tool/surface:** app.py _setup_gate_status / user_config.config_exists
- **Expected:** The gate requires REQUIRED_KEYS (user_config.py:61) and parseable JSON. A drive being offline should be shown as a state, not let a broken config through.
- **Actual:** A corrupt or empty config lets the user into the main UI with failing APIs instead of the repair or onboarding path.
- **Evidence:** user_config.py:157-159 is `return CONFIG_FILE.exists()`, and its docstring says '(may still be incomplete)'. app.py:1270-1281 returns ready whenever that is true and setup_complete is set. save-config validates only that the three path strings are non-empty (app.py:1691-1694), not that they exist or are Rekordbox DBs. Corroborated live by wizard-live (J_gate.json): config '{}' and invalid JSON both give gate_reason 'ready', landing on '/' with 500s in the console.
- **Fix:** In the gate, load the config and check REQUIRED_KEYS. If they are missing, return 'config_incomplete', and have /onboarding pre-fill the paths it can.

#### [MEDIUM · inferred] Every launch re-attempts the optional essentia install (an sdist build on macOS 12-14 with cp313) and runs an untimed `git fetch` before the window appears, with no feedback

- **ID:** `wizard-code-launch-every-launch-cost` · **Area:** install-wizard · **Tool/surface:** launch.sh / update_checker.py
- **Expected:** Optional installs happen once (record the failure for that interpreter and macOS version). Network steps are bounded and run after the window is up.
- **Actual:** Every launch is slowed and the user sees nothing.
- **Evidence:** launch.sh:104 runs `pip install --quiet -r requirements_optional.txt ... || true` on every launch, before main.py. requirements_optional.txt:23-33 says there is no cp313 wheel below macOS 15 and that pip falls back to the sdist and a failing C++ build. pip does not cache failed builds, so the download and build attempt repeat on every launch, with output only in fablegear.log (launch.sh:82). launch.sh:113 `git fetch origin --tags` has no timeout (only curl has --max-time 10), so a stalled network (captive portal) blocks the launch with all output silenced. update_checker.py:70 `git fetch --tags` likewise has no timeout, and it runs inside the synchronous /api/update/status?refresh=1 request (app.py:725-727).
- **Fix:** Write a marker such as .optional_failed-<pyver>-<macos> and skip while it exists; or move optional installs to a background Health action. Use `timeout 20 git fetch` (or GIT_HTTP_LOW_SPEED_LIMIT/TIME). Add timeout=20 to update_checker's subprocess.run.

#### [MEDIUM · inferred] Clicking the Dock icon while FableGear is running re-runs pip and a possible git update under the live app, then exits silently instead of focusing the window

- **ID:** `wizard-code-second-launch-while-running` · **Area:** install-wizard · **Tool/surface:** launch.sh / main.py / single_instance.py
- **Expected:** launch.sh checks the instance lock (or port 5001) first and, if the app is running, only activates the existing window.
- **Actual:** A mid-session update can be applied under the running app, and the second click seems to do nothing.
- **Evidence:** launch.sh's lock is released when the launcher exits right after starting main.py (launch.sh:26, 183-185), so a second click runs launch.sh again. That run does pip installs (launch.sh:98-104) and possibly `git merge --ff-only` or `git reset --hard <tag>` (launch.sh:125, 156), changing code and packages under the running server: templates and static files are served fresh from disk while the Python routes are stale. Only then does main.py hit single_instance.acquire() (main.py:160-168), print 'already running' to the log and `sys.exit(0)`, so no window is raised. The main.py docstring (lines 13-14), 'the existing server is reused and a new window is opened', is stale. single_instance.py itself is correct (flock, released by the OS).
- **Fix:** At the top of launch.sh, probe the flock (python -c using single_instance) or curl 127.0.0.1:5001/api/setup-status; if live, `open` the app or send a focus request and exit before any pip or git step.

#### [MEDIUM · demonstrated] Branch install wizard: no Cancel, Back or Finish-later; children keep running after the client disconnects; a failed check disables Continue with no way to re-check

- **ID:** `wizard-code-iw-no-exit-orphans` · **Area:** install-wizard · **Tool/surface:** installer_wizard.py stream_command / installer_wizard.html
- **Expected:** Cancel stops the child cleanly (terminate, then kill after a grace period) and leaves a resumable state. A Re-check button is shown on failed checks. Back is offered between phases.
- **Actual:** The only exit is closing the window, which orphans or truncates brew/pip mid-run. Fixing a failed check requires restarting the wizard.
- **Evidence:** The page has exactly one button, ['primaryBtn:Get Started'] (iw_ui.json; html:501). Disconnect test (<scratch>/audit/wizard-code/iw_harness.py orphan mode): curl disconnected after 2.5 s from /api/install/python (a chatty child printing every 1 s), and after 1.5 s from /api/install/brew (a silent child doing `sleep 6`). Both children kept running and finished (DONE_SILENT and DONE_CHATTY were created). The server never terminated them, because stream_command (py:260-282) has no try/finally and no proc.terminate() on GeneratorExit. When the wizard process exits (window closed, or os._exit in py:400), running children are re-parented, and the next write to the closed pipe kills them with SIGPIPE at an arbitrary point (inferred; observed in the SIGHUP setup demo as re-parenting to PID 1). runChecks: a 'fail' result (e.g. network) sets primary.disabled=true with no pendingRetry or re-check (html:838). fetch('/api/checks') has no catch (html:812), so the button stays 'Checking…'.
- **Fix:** Wrap stream_command in try/finally: proc.terminate(); proc.wait(5); proc.kill(). Track active procs and add /api/cancel. Handle pywebview's closing event to cancel. On a check failure set pendingRetry=runChecks with the label 'Re-check'.

#### [LOW · inferred] install-app rewrites com.apple.dock.plist in place (non-atomic, bypassing cfprefsd), uses tempfile.mktemp, and quotes paths unsafely in the AppleScript

- **ID:** `wizard-code-install-app-dock-plist` · **Area:** install-wizard · **Tool/surface:** app.py /api/onboarding/install-app, _pin_to_dock
- **Expected:** Use `defaults write com.apple.dock persistent-apps -array-add ...` or a tmp+rename write, and use NamedTemporaryFile.
- **Actual:** A crash during the write can reset the user's Dock layout.
- **Evidence:** app.py:1481-1505 opens com.apple.dock.plist for 'wb' and calls plistlib.dump directly (no tmp+rename), then `killall Dock`; it also runs killall when the tile already exists (app.py:1490-1491). The duplicate check compares a raw POSIX path with _CFURLString, which the Dock may normalise to a file:// URL, so duplicate tiles are possible (speculative). app.py:1537 uses tempfile.mktemp. app.py:1536 interpolates launch_sh into `do shell script "bash '{launch_sh}' ..."`, which breaks on paths containing quotes. All subprocess calls use list argv with no shell=True: no injection from request data, and `force` and `dock` are the only inputs. Exit while running is harmless (synchronous, short). The existing-.app branch (app.py:1527-1532) does not verify that the .app points to this install.
- **Fix:** Use `defaults` (via cfprefsd) or a dockutil-like tmp+atomic replace. Use tempfile.NamedTemporaryFile(delete=False). Escape the path for AppleScript (or use the packaged launcher).

#### [LOW · demonstrated] launch.sh's 'diverged' realign hard-resets the current branch ref to the tag; only uncommitted changes are stashed, and committed local work survives only in the reflog

- **ID:** `wizard-code-realign-moves-branch` · **Area:** undo-revert · **Tool/surface:** launch.sh / install.sh
- **Expected:** Realign by checking out the tag (detached) or a dedicated release branch, never by moving a user branch.
- **Actual:** Local commits on the checked-out branch become unreachable except through the reflog.
- **Evidence:** run_update_demo.sh scenario 3: after realign, 'HEAD now: 2239a68 hotfix; local main == origin/main? NO'. The local main branch was moved to the off-main tag. launch.sh:153-156 stashes only when `git diff` is non-empty, then runs `git reset --hard "$LATEST_TAG"` on whatever branch is checked out. install.sh:49-50 does the same with origin/main. The comment says 'never silently discarded', which holds for the working tree but not for local commits. This affects anyone without .dev (launch.sh:112).
- **Fix:** Use `git checkout -B fablegear-release "$LATEST_TAG"`, or a detached checkout, and leave other branches alone. Log the old SHA.

#### [LOW · demonstrated] Branch wizard: not wired into any launcher; installs into whatever Python runs it when the venv is missing; quick clicks can leave two panels shown

- **ID:** `wizard-code-iw-misc` · **Area:** install-wizard · **Tool/surface:** installer_wizard.py / installer_wizard.html
- **Expected:** The wizard creates or validates the venv itself (or refuses without it), and the panel transition is idempotent.
- **Actual:** If wired in as-is, it could pip install into a system or externally-managed Python.
- **Evidence:** Neither setup.sh nor launch.sh on origin/feature/install-wizard references installer_wizard.py (grep). The diff vs main touches only installer_wizard.py, the template, the tests and the mascot PNGs, yet the docstring (py:5-8) says it 'runs after setup.sh'. With no venv, python_install_commands() printed `-m pip install ...` for sys.executable (py:331-332), i.e. the system or brew Python. The wizard never creates the venv; check_venv only warns (py:161-165). Under instant stubs, a click landing within 200 ms of a transition left two `.panel.active` sections at once (screens/wizard-code/iw_03 shows step 3 and step 4 stacked), because transitionToStep reads `.panel.active` before the deferred activateNext runs (html:696-717).
- **Fix:** Fail the Python phase (or run `python -m venv`) when VENV_DIR/bin/python is missing. In transitionToStep, cancel the pending timeout and clear 'active' from all panels before activating.

#### [INFO · inferred] Confirmed-good behaviour in the installer and update paths

- **ID:** `wizard-code-confirmed-good` · **Area:** install-wizard · **Tool/surface:** launch.sh / setup.sh / single_instance.py / user_config.py / app.py
- **Expected:** n/a
- **Actual:** n/a
- **Evidence:** (1) Interrupted venv creation is handled. CPython's EnvBuilder writes the activate scripts after ensurepip, and setup.sh:120-123 rebuilds a venv that has no activate. A re-run of setup.sh restarts every stage but is idempotent (brew list checks, venv reuse, pip -r), so it converges, except for the Python 3.9 trap above. (2) launch.sh's update rollback only resets when the tree is clean (launch.sh:131-135), and the diverged path stashes uncommitted changes with -u (launch.sh:153-154); both were seen working in the demo. (3) single_instance.py uses flock(LOCK_EX|LOCK_NB), which the OS releases on crash, so there is no stale-PID problem. (4) save_user_config is atomic (tmp + rename, user_config.py:260-288). (5) /api/update/apply refuses when tracked files are dirty or managed subprocesses are running (app.py:744-779), and update_checker's SHA-vs-tag loop fix holds (update_checker.py:260-294). (6) `bash -n` passes on all shell scripts, and shellcheck reports only minor items: launch.sh:111 unchecked `cd` (SC2164), launch.sh:178 SC2155, setup.sh:99 unused `ver`. (7) The onboarding install-deps and install-app routes use list-form subprocess calls with no shell=True (app.py:1474, 1539-1542).
- **Fix:** Keep these and add regression tests (e.g. pytest that stubs PATH and runs the launch.sh update block, as in upd/run_update_demo.sh).

## Chop Shop: Tag Tracks

_Auditor: `tool-process-b`_

I audited Tag Tracks (#step-process, /api/run/process, /api/run/process-retry, cli.py cmd_process, audio_processor) live, in sandbox sbx-process-b on port 6111. I drove it through the real UI with Playwright: about 20 runs covering the passive defaults, aggressive, normalize, rename, MusicBrainz, cancel, kill -9, /api/quit, page reload, resume and Start Fresh. I also used the dry_run and pipeline-dry-run APIs, and hashed every file before and after each run.

Overall verdict: Tag Tracks is a destructive file tool with no working revert story.
- **No restore point or undo.** Tag writes, loudness re-encodes and fg_content upserts get no restore point and cannot be undone. The undo timeline never lists the run. The only per-run record is one aggregate fg_processing_log row with file_path NULL.
- **Passive mode overwrites M4A tags.** It overwrites existing M4A BPM and key (demonstrated 118→162 and 4A→5A), despite the card's "won't overwrite your Mix In Key work".
- **Normalize mode strips AIFF/WAV metadata.** It strips all ID3 tags (artist, album, BPM, key, comments) from AIFF/WAV, irreversibly. The report still claims the keys were written.
- **"Dry run" writes tags.** Both dry_run=1 and the pipeline's "Dry Run (preview only)" write tags.
- **Interruptions leave no record.** Cancel, quit, reload and kill -9 leave no report, no journal row, and no server checkpoint for runs under 25 files.
- **Resume relies on browser storage.** The resume banner lives only in localStorage and shows just the age and the folder paths. /api/checkpoint/check always returns exists:false for process because of a key mismatch.
- **A stale checkpoint can make a run skip files.** An aggressive (overwrite) run silently reused a passive run's checkpoint and skipped 25 files.

Problems I hit in the shared undo path:
- The Savepoints and Trash tabs crash: undoLoadSavepoints and undoLoadTrash are undefined.
- Restoring the savepoint that Tag Tracks' rename stage creates overwrote the USB device master.db with the local collection DB. That backup is of the local DB, but restore always targets the device DB.
- /api/cancel and /api/quit kill every cli.py process system-wide, including jobs owned by other FableGear instances. Two of my runs were killed this way.

Confirmed good:
- Smart-skip preserves fully tagged ID3/FLAC files.
- Cancel followed by resume ends byte-identical to an uninterrupted run.
- Start Fresh clears both the browser and the server checkpoint.
- A server-side checkpoint (25 or more files) does skip completed files on resume.
- The rename journal revert works and is safe to run twice.
- The rename stage takes a DB savepoint before writing.
- Normalize failures leave the original file intact.

**Coverage**

- LIVE (UI via Playwright, headless Chromium): default passive run (run1); MusicBrainz passive run (enrich); Interrupt via the UI button after 3/10 files (cancel1); reload plus Resume (cancel1_resume); kill -9 of the server mid-run, restart, reload, Resume with a new file added (kill1*); 32-file crate runs cancelled at 27 to force a server checkpoint, then an aggressive run, Resume and Start Fresh (bulk_*); Rename passive (rename3) plus Undo Wizard > Operations revert (rename3_ops_*); Normalize passive (norm1); report-modal 'Retry with Force' (retry_*); page reload mid-run; /api/quit mid-run followed by relaunch.
- LIVE (API): GET /api/run/process?dry_run=1; POST /api/run/pipeline {dry_run:true, steps:[process]}; /api/run/process with normalize_mode=aggressive on ID3-tagged AIFFs (with and without cover art); /api/checkpoint/check; /api/undo/operations/preview and revert for tag_tracks and rename (run twice); /api/undo/savepoint/restore on the Tag Tracks rename savepoint (device DB restored out-of-band afterwards from my copy).
- Before/after evidence: snapshot script /tmp/claude-0/-home-user-FableGear/9074577f-2e6a-5e16-bdce-ef5b26f1b16b/scratchpad/audit/tool-process-b/snap.py records sha256 and tags for every file under Volumes/, Incoming/, ~/.fablegear, ~/rekordbox-toolkit, ~/.Trash and ~/Library/Pioneer, plus all five /api/undo/* endpoints and the fg_content / fg_processing_log rows. Snapshots are in .../audit/tool-process-b/snaps/*.json; SSE captures and UI logs are in .../audit/tool-process-b/out/*.
- Library reset: reset_lib.py restores the Music Library out-of-band, byte-identical to the 'before1' snapshot (0 mismatches each time). The app has no undo for this tool, so the reset was necessary.
- Screenshots (68): /home/user/FableGear/docs/audits/2026-10-05/screens/tool-process-b/. Key ones: run1_3_done.png, after1_undo_timeline.png, after1_undo_savepoints.png, after1_undo_trash.png, cancel1_reload_banner.png, kill1_reload_banner.png, kill1_fresh_storage_no_banner.png, rename3_ops_list/preview/reverted.png, retry_0_option.png, retry_2_report_modal_actions.png, reload_midrun_after.png.
- NOT testable live: AcoustID lookups, because fpcalc is absent. Only the failure path (a silent skip) was exercised. Essentia is absent too, so librosa was the fallback. The synthetic sine tracks give 'BPM 0.0 out of range' on 8 of 10 files, so the BPM-write path ran only on the M4A; key writes ran on every file.
- Sandbox limitation: the fixture has no ~/Library/Pioneer/rekordbox/share directory, so pyrekordbox update_content_path failed in rename mode. The renamer then rolled the renames back (good behaviour), but the rename-plus-Rekordbox-DB-path-update path could not be seen succeeding.
- Code only: the MCP tag_tracks tool (mcp_server.py:485-541), workers>1, Rekordbox-running gating, WKWebView/pywebview window-close semantics. I did not wait out the pipeline wizard UI; its process-step dry-run was exercised through the same /api/run/pipeline endpoint the UI uses.
- ISOLATION NOTE FOR THE LEAD (1/2): my Tag Tracks jobs were SIGTERMed twice by other auditors' servers: sbx-wizard POST /api/quit at 18:59:34 and sbx-dupes-b POST /api/cancel at 19:00:56. From 19:02 I therefore ran my server from an identical copy of the app, at .../audit/tool-process-b/appcopy (same commit; since deleted), so that the product's system-wide orphan matching would not cross sandboxes.
- ISOLATION NOTE FOR THE LEAD (2/2): my own earlier POST /api/cancel calls, from the shared-app server at 18:50:03, 18:54:48, 18:56:50 and 18:58:10, may have killed other auditors' cli.py jobs that were running then. Any unexplained exit -15 at those times should be attributed to finding tool-process-b-cross-instance-kill.
- Cleanup done: my server was stopped (it exited on SIGTERM). Seven leaked /tmp/fablegear_smart_skip_*.txt files from my interrupted runs were deleted after being recorded as evidence. No product code was edited.

**Per-tool safety matrix**

### Tag Tracks — BPM/key tag writes (/api/run/process, default smart-skip path and directory path)

| Check | Result |
|---|---|
| R — Report | partial. On success: an aggregate-only text report in <Archive>/Reports/Tag Tracks/tag_tracks_<ts>.txt plus the report modal. The path is shown as plain text, not a link. There is no per-file list of what changed or the old→new values. BPM detection failures are not reported. On cancel, quit, reload or kill: no report at all. On failure: a report pill. Archive/Logs/Tag Tracks is always empty because LOG_DIRS is never used. |
| M — Revert marker | no. No snapshot of prior tag values is taken. fg_processing_log gets one aggregate 'tag_tracks' row with file_path NULL (cli.py:905-945). It is not listed in /api/undo/operations (routes_undo.py:359), not in the timeline (job_dispatcher, MCP only), and not in database history. |
| U — Undo | no. /api/undo/operations/preview with type tag_tracks returns 400: "'tag_tracks' operations cannot be reverted by moving files". The only way back to BEFORE was my out-of-band reset_lib.py. |
| C — Cancel / interrupt | partial. Interrupt sends SIGTERM and the job exits -15 promptly. The in-flight file is not corrupted (Kaito.wav was intact). But there is no SIGTERM handler, no partial report, no journal row and no fg_content persist; the checkpoint is saved only every 25 files; the smart-skip temp file leaks. kill -9 of the server leaves an orphan CLI that wrote one more file than the UI saw. Page reload and /api/quit also kill the job. |
| S — Resume after restart | partial. The banner comes from localStorage only and shows age plus paths: no modes, done/remaining, start time or restore point. It is gone if WebView storage is cleared. /api/checkpoint/check returns exists:false for process because of a key mismatch. With fewer than 25 files there is no server checkpoint, so Resume just re-runs smart-skip and re-analyses done files. With a checkpoint, Resume skipped 25/40. Input changes are not detected (a new file was silently included). Changing mode reuses a stale checkpoint. |
| D — Dry run / preview | no in the UI. The API's dry_run=1 and the pipeline's 'Dry Run (preview only)' still write BPM/key tags (demonstrated). There is no preview of values. |
| B — Boundary | Writes FableGear DB fg_content (bulk_set_analysis upsert) and fg_processing_log. These DB-layer writes have no undo transaction or revert marker. It does not write the Rekordbox DB unless Rename mode is on. It also writes ~/rekordbox-toolkit/scan_index.json in directory mode. |

_Evidence:_ demonstrated: snaps before1/after1, before_dry/after_dry, before_pipe/after_pipe, before_cancel/after_cancel, before_kill/after_kill, before_kresume/after_kresume; out/run1_log.txt, dryrun_sse.txt, pipe_dry_sse.txt, cancel1_sse.json, kill1_log.txt, bulk_aggr_log.txt, bulk_resume_log.txt. Code: cli.py:2367-2901, routes_undo.py:321,359, static/chop_shop/pipeline.js:583-678.

### Tag Tracks — Normalize mode (normalize_mode passive/aggressive)

| Check | Result |
|---|---|
| R — Report | partial. On success the report is written to Reports/Normalize/normalize_<ts>.txt rather than Tag Tracks. It claims 'Key written: 10' even though normalization stripped those keys from the WAV/AIFF files. Normalization failures are miscategorised as 'Tag Write Failures', with wrong advice. |
| M — Revert marker | no. The original is moved to .bak only for the duration of the swap and then deleted (audio_processor.py:545-548). No archive copy and no journal row are kept. |
| U — Undo | no. The re-encode is permanent. The undo wizard has nothing to offer for it. |
| C — Cancel / interrupt | same as the tag-write path. The .bak swap is atomic per file, so the original survives a failure. |
| S — Resume after restart | The checkpoint config includes the normalize flag, otherwise the same as above. The UI banner restores normalize_mode from localStorage. |
| D — Dry run / preview | no. The UI has no preview (unlike the separate Normalize tool's preview). --dry-run suppresses normalize only. |
| B — Boundary | File-only, plus fg_content/fg_processing_log as above. |

_Evidence:_ demonstrated: norm1 run (10 files re-encoded; the TKEY written to Pressure.aiff, Kaito.wav and kick_01.wav was lost); the 'DJ Real - Anthem.aiff' API run lost TPE1/TALB/TBPM/TKEY (AFTER: None); the same file with APIC failed ('stream received no packets'); M4A always fails ('Filtering and streamcopy cannot be used together'). Code: audio_processor.py:438-456, 494-566.

### Tag Tracks — Rename mode (rename_mode passive/aggressive)

| Check | Result |
|---|---|
| R — Report | yes, one line in the report: 'Filename cleanup (passive): 0 renamed, 0 collisions handled, 1 quarantined, 6 errors'. The errors are not itemised in the report, only in the log. |
| M — Revert marker | partial. write_db(LOCAL_DB) takes a Rekordbox savepoint (master.backup_<ts>.db) before writing, and the renamer journals 'rename' rows. The savepoint is not linked to the job, and it uses the same naming as device-DB backups. |
| U — Undo | partial. Undo Wizard > Operations 'Return 1 file' moved kick_01.wav back. Running it a second time is safe (0 reverted, 1 blocked). The result is not byte-identical: the tag writes remain, and the 'No-Name tracks for Tagging/_quarantine_manifest.json' orphan stays behind with a stale entry. Restoring the savepoint via /api/undo/savepoint/restore overwrote the DEVICE DB with the LOCAL DB copy. The Savepoints tab itself crashes (undoLoadSavepoints is undefined). |
| C — Cancel / interrupt | The rename stage runs only after a root finishes tagging, so a cancel during tagging skips the rename stage entirely. |
| S — Resume after restart | rename_mode is restored from localStorage. The checkpoint config does not include rename_mode. |
| D — Dry run / preview | no |
| B — Boundary | CROSSES the boundary: a file tool writes the Rekordbox local master.db (cli.py:2726) and moves files into a library subfolder. Covered by a savepoint (M), but that savepoint's restore targets the wrong DB (U broken). |

_Evidence:_ demonstrated: rename3 run, snaps before_rename/after_rename, before_opsundo/after_opsundo, rename3_ops_*.png, savepoint restore turned device DB acc7fafdb836 (5 rows) into e18dd5aa2d6a (12 rows == local). Code: cli.py:2712-2736, db_connection.py:218-234, routes_undo.py:107-191.

### Tag Tracks — MusicBrainz enrichment (enrich_mode)

| Check | Result |
|---|---|
| R — Report | no. When enrichment is skipped (fpcalc missing) nothing appears in the log at INFO level and nothing in the report. A 'MusicBrainz enriched' line appears only when count > 0. |
| M — Revert marker | no (would overwrite title/artist/album with no journal) |
| U — Undo | no |
| C — Cancel / interrupt | as for tag writes |
| S — Resume after restart | enrich_mode is restored from localStorage |
| D — Dry run / preview | no |
| B — Boundary | file tags only |

_Evidence:_ demonstrated: enrich run log contains only 'ENRICH_MODE:passive'; no warning. Code: audio_processor.py:713-717 (log.debug), static/chop_shop/runners.js:89-96 (the toast only checks the API key, which ships configured: user_config.py:76). Live AcoustID lookups were not possible because fpcalc is absent.

### Tag Tracks — retry (/api/run/process-retry; report-modal 'Retry N failed tracks with Force'; card 'Retry errored tracks only')

| Check | Result |
|---|---|
| R — Report | yes, a report modal (exit 0). |
| M — Revert marker | no |
| U — Undo | no |
| C — Cancel / interrupt | same as tag writes; the retry path never checkpoints |
| S — Resume after restart | no (not saved to localStorage, unlike the main run) |
| D — Dry run / preview | no |
| B — Boundary | file tags plus fg_content |

_Evidence:_ demonstrated: the card's retry row can never become visible (CSS #process-retry-errored-row{display:none} vs inline display='', fablegear.css:5905, runners.js:20-34). The smart-skip path never emits FABLEGEAR_ERROR_SUMMARY (0 occurrences in corrupt1_sse.json). The report-modal retry of a 'normalisation failed' M4A ran with NORMALIZE_MODE:off and reported success (retry_3_after_force_retry.png). Code: routes_tools.py:345-353.

**Findings**

#### [CRITICAL · demonstrated] Normalize mode in Tag Tracks strips all ID3 metadata (artist, album, BPM, key, comments) from AIFF/WAV files, irreversibly

- **ID:** `tool-process-b-normalize-strips-aiff-wav-id3` · **Area:** chop-shop-tool · **Tool/surface:** Tag Tracks (normalize_mode) / audio_processor._normalise_file
- **Expected:** Re-encoding for loudness preserves every existing tag (ID3 chunk in AIFF/WAV, artwork, comments) and any tag Tag Tracks just wrote. Or, if it cannot, the tool refuses and says why.
- **Actual:** Every ID3 frame in an AIFF/WAV is dropped by the re-encode. The user's DJ metadata (artist, album, BPM, key, Mix In Key comments) is permanently lost, while the report and the archive claim the keys were written.
- **Evidence:** 1) Created 'Volumes/DJDRIVE/AIFF Test/DJ Real - Anthem.aiff' with ID3 TIT2/TPE1/TALB/TBPM=126/TKEY=4A/COMM. Ran GET /api/run/process?path=<AIFF Test>&bpm_mode=passive&key_mode=passive&normalize_mode=aggressive. The log shows 'Normalising DJ Real - Anthem.aiff: -21.8 LUFS → -8.0' and the report 'Loudness adjusted: 1 files'. mutagen AIFF tags AFTER: None; ffprobe shows only title and comment, from native AIFF chunks. Artist, album, BPM 126 and key 4A are gone. 2) UI run norm1 (normalize passive): the log says 'KEY written: 11B → Vera Lux - Pressure.aiff' and then normalises it; the same holds for Kaito_-_sunrise_FINAL_v2.wav and kick_01.wav. The snapshot afterwards shows {'_tags': None} for all three, yet the report says 'Key written: 10 files' and fg_content stores key 11B. 3) Reproduced with plain ffmpeg using the exact _normalise_file argv: out.aiff tags None. Adding '-write_id3v2 1' preserves TKEY. Cause: audio_processor.py:518-525 builds the command with '-map_metadata 0 -id3v2_version 3' but no '-write_id3v2 1' for wav/aiff. There is no backup after the .bak is deleted (line 548). The same code path backs the standalone Normalize tool.
- **Fix:** For .wav/.aif/.aiff add '-write_id3v2 1' (and keep '-id3v2_version 3'). After encoding, verify that the tag set of tmp_path is a superset of the source's (mutagen) before swapping; on mismatch, copy the tags across with mutagen or abort. Do not delete the .bak until verification passes, and keep the original in Archive/Originals/<run-id>/ so it can be undone. Add a regression test with an ID3-tagged AIFF and WAV.

#### [HIGH · demonstrated] Passive ("fill only missing") BPM/key mode overwrites existing M4A BPM and key

- **ID:** `tool-process-b-m4a-passive-overwrite` · **Area:** chop-shop-tool · **Tool/surface:** Tag Tracks (process_file)
- **Expected:** In passive mode an M4A with an existing tmpo/initialkey is left untouched for those fields, as with ID3 and FLAC.
- **Actual:** Existing M4A BPM and key are replaced with librosa estimates (118 → 162, a half/double-time error) with no undo.
- **Evidence:** Seeded 'Disco/Marlo & The Tens - Glitterball.m4a' with tmpo=118 (no key) and ran the UI defaults (BPM passive, key passive). The snapshot diff shows tmpo '118' → '162' and initialkey added (out/run1_log.txt: 'BPM written: 161.5 → ...m4a'). Run 'enrich': the same M4A fully tagged (tmpo 118 + initialkey 4A) was changed to tmpo 162 / key 5A in passive mode. The report counted it under 'written', not 'already had one'. The same overwrite happened in the dry-run, pipeline, resume and retry runs. Cause: audio_processor.py:988-996 _existing() checks only Vorbis keys or ID3 frame ids (tags.get('TBPM')/('TKEY')). For MP4Tags those keys never exist, so needs_bpm/needs_key (1002-1003) are always True. Smart-skip (helpers.py:1023-1025, scanner.py:283-300) is MP4-aware, but it only selects the file; the per-file decision then overwrites both fields. The card promises 'won't overwrite your Mix In Key work unless you ask it to' (fingerprinting.html:7, 21-22). No undo exists.
- **Fix:** Make _existing() format-aware: for MP4 check 'tmpo' and '----:com.apple.iTunes:initialkey'. Better, share one tag-presence helper with helpers._track_needs_tag_work, so smart-skip and the per-file decision cannot disagree. Add tests for passive mode on m4a/aac/alac with pre-existing values, and count overwrites separately in the report.

#### [HIGH · demonstrated] Tag Tracks writes (tags, re-encodes, fg_content) have no restore point and no undo; the run is invisible in the Undo Wizard

- **ID:** `tool-process-b-no-revert-marker-no-undo` · **Area:** undo-revert · **Tool/surface:** Tag Tracks / Undo Wizard
- **Expected:** Before the first write, a restore point is recorded (the per-file prior tag values, and the original audio for normalize) and linked to a run id. The Undo Wizard shows 'Revert Tag Tracks run at <time>', which restores byte-identical files and fg_content rows, and is safe to run twice.
- **Actual:** Nothing reversible is recorded. The only way back was my out-of-band reset script.
- **Evidence:** After run1 (10 files tagged): /api/undo/timeline [] (also with ?tool=process), /api/undo/operations {sessions: []}, /api/undo/database/history [], /api/undo/trash []. The only new savepoint, master.backup_20261005_184428, came from an unrelated startup backup at 18:44:28, not from Tag Tracks. fg_processing_log gained one row: ('tag_tracks', file_path NULL, metadata {files_processed:10, key_written:10, ...}), with no per-file paths and no before-values. POST /api/undo/operations/preview and /revert with {type:'tag_tracks'} return 400 "'tag_tracks' operations cannot be reverted by moving files". UI: Undo Wizard Timeline with filter 'Tag Tracks' shows 'No jobs found' (screens/after1_undo_timeline.png); Operations shows 'No journaled tool operations yet'. Code: routes_undo.py:40-48 (timeline reads job_dispatcher, which only mcp_server.py uses); routes_undo.py:321 and 359 (tag_tracks excluded); cli.py:905-945 (aggregate log row only); fablegear_database/database.py:407-433 bulk_set_analysis is not wrapped in a DatabaseUndoManager transaction. The timeline filter value is 'process' (undo_wizard.html:25) while the MCP job tool name is 'tag_tracks' (mcp_server.py:536), so even MCP runs never match.
- **Fix:** Give each run an id. Before each write, journal one fg_processing_log row per file with operation_type 'tag_tracks' and metadata {run_id, before:{TBPM,TKEY,tmpo,...}, after:{...}, sha_before}. Add 'tag_tracks' to _REVERTIBLE with a 'restore_tags' action (rewrite the prior frames, or delete frames that were absent). For normalize, keep the original audio in Archive and restore it from there. Record SSE tool runs in the job history (or a lightweight run table) so the timeline can show them, and align the filter values. Wrap the fg_content upserts in an undo transaction.

#### [HIGH · demonstrated] "Dry run" for Tag Tracks (API dry_run=1 and the Pipeline's 'Dry Run (preview only)') writes BPM/key tags

- **ID:** `tool-process-b-dry-run-writes-tags` · **Area:** chop-shop-tool · **Tool/surface:** Tag Tracks / Pipeline
- **Expected:** Dry run or preview performs no file writes and shows the BPM/key values that would be written.
- **Actual:** Dry run writes tags to every eligible file, including overwriting existing M4A BPM, and is journaled as a normal run.
- **Evidence:** After a library reset, GET /api/run/process?path=<Music Library>&bpm_mode=passive&key_mode=passive&normalize_mode=off&enrich_mode=off&rename_mode=off&dry_run=1 changed 10 files (TKEY added on 9 files; the M4A tmpo went 118→162). It also wrote a normal report and a 'tag_tracks' journal row with no dry-run marker (snap diff before_dry/after_dry). POST /api/run/pipeline {dry_run:true, steps:[{type:'process', config:{paths:[...], bpm_mode:'passive', ...}}]}: the log shows 'DRY_RUN:True' and 'DRY RUN — loudness normalisation suppressed. BPM/key tag writes will still occur', and the same 10 files changed (before_pipe/after_pipe). The pipeline UI labels this mode 'Pipeline — Dry Run (preview only)' and finishes with '✓ Preview complete. Uncheck Dry Run and run again to execute.' (static/chop_shop/pipeline.js:984, 1021-1022). Code: cli.py:2371-2374 and 4206-4213 (by design), routes_tools.py:289-290 and 453. Contrast routes_tools.py:507-511, where convert refuses dry-run to avoid 'silently transcoding files during a dry run'. The Tag Tracks card has no preview at all.
- **Fix:** Make --dry-run skip _write_tags and _write_enriched_tags. Compute and report the would-be values per file (old→new) without writing, and skip the fg_content persist. Until then, refuse dry_run for the process step in the pipeline, as convert does. Add a 'Preview' button to the Tag Tracks card that uses this mode.

#### [HIGH · demonstrated] Undo Wizard Savepoints and Trash tabs crash: undoLoadSavepoints / undoLoadTrash are not defined

- **ID:** `tool-process-b-undo-tabs-undefined` · **Area:** undo-revert · **Tool/surface:** Undo Wizard (static/shared/undo.js)
- **Expected:** The Savepoints tab lists /api/undo/savepoints with Restore buttons; the Trash tab lists /api/undo/trash with Restore.
- **Actual:** The tabs throw a ReferenceError and stay empty; the user cannot reach any DB restore point or pruned-file trash from the UI.
- **Evidence:** ui_undo.py opened the header #undo-wizard-btn and clicked each tab. Console: 'PAGEERROR: undoLoadSavepoints is not defined', 'PAGEERROR: undoLoadTrash is not defined'. Both tabs render only their blurb, with no list and no restore buttons (screens/tool-process-b/after1_undo_savepoints.png, after1_undo_trash.png). This was with 1 savepoint present, and 3 later. grep shows the names occur only at static/shared/undo.js:39-40, with no definition anywhere in static/ or templates/. The only revert marker Tag Tracks' rename stage creates (the DB savepoint) is therefore unreachable from the UI.
- **Fix:** Implement undoLoadSavepoints() and undoLoadTrash() following the pattern of undoLoadOperations(), calling GET /api/undo/savepoints, /api/undo/trash and /api/undo/trash/<folder>/files, with restore buttons for POST /api/undo/savepoint/restore and /api/undo/trash/restore. Add a Playwright smoke test that clicks every undo tab and asserts there are no page errors.

#### [HIGH · demonstrated] The savepoint Tag Tracks' rename stage creates is of the LOCAL Rekordbox DB, but restoring it overwrites the DEVICE (USB) master.db

- **ID:** `tool-process-b-savepoint-restore-wrong-db` · **Area:** undo-revert · **Tool/surface:** Tag Tracks rename_mode / POST /api/undo/savepoint/restore
- **Expected:** A savepoint records which DB it came from and restores only to that DB. Tool-created savepoints are labelled with the tool and run.
- **Actual:** Restoring Tag Tracks' revert marker replaces the USB export DB with the computer's collection DB.
- **Evidence:** The rename3 run (rename_mode=passive) created Savepoints/master.backup_20261005_190228_780785.db. Its sha256 e18dd5aa2d6a (12 content rows) equals home/Library/Pioneer/rekordbox/master.db (LOCAL_DB), because cli.py:2726 calls write_db(LOCAL_DB) and db_connection.py:218-234 backs up the target DB. The device DB Volumes/DJDRIVE/PIONEER/Master/master.db was acc7fafdb836 (5 rows). POST /api/undo/savepoint/restore {path: that savepoint} returned {ok:true}; afterwards the DEVICE DB sha was e18dd5aa2d6a (12 rows, now an exact copy of the local DB). routes_undo.py:132-156 always backs up and replaces DEVICE_DB. The Savepoints dir mixes local-DB and device-DB backups under the identical name pattern 'master.backup_<ts>.db' (the 184428 and 191224 files are device copies, the 190228 file is local), so nothing tells the user which is which. I restored the device DB out-of-band from my copy afterwards.
- **Fix:** Write a sidecar <backup>.json holding {source_db_path, kind: local|device, tool, run_id, created_at}, or encode the kind in the filename. Have /api/undo/savepoint/restore restore to source_db_path, and refuse when it is unknown or ambiguous. Show the source DB and the originating tool in the Savepoints UI. Have the CLI print the savepoint path in the SSE and in the report, so the run links to its restore point.

#### [MEDIUM · demonstrated] Cancel, quit, page reload and kill -9 leave partially tagged files with no report, no journal row, and (for under 25 files) no checkpoint

- **ID:** `tool-process-b-cancel-leaves-no-record` · **Area:** chop-shop-tool · **Tool/surface:** Tag Tracks (cmd_process) / POST /api/cancel / /api/quit
- **Expected:** An interrupt stops at a file boundary. It saves a checkpoint of done paths, persists results for the files already done, writes a partial report ('CANCELLED after N/M — these files were changed: …'), records the run in the journal, and removes temp files.
- **Actual:** Files already modified are left with no record of which ones changed. Nothing server-side says a run was interrupted.
- **Evidence:** cancel1: the UI Interrupt after 3 progress events ended with 'Exited with code -15'. 5 files had TKEY written (snap diff before_cancel/after_cancel). No Tag Tracks report file, no report modal, no new fg_processing_log row, fg_content unchanged, and no ~/.fablegear/checkpoints directory. Only localStorage rb_ckpt_process remained. The same outcome followed /api/quit mid-run (3 files written, exit -15) and a page reload mid-run (pg.reload, CLI PID 24307 gone within 4 s, 3 files written). Code: cmd_process installs no SIGTERM handler, so Python dies without running finally blocks. The checkpoint is saved only every 25 files (cli.py:2519-2522, 2676-2680). Results are persisted and the report emitted only at the end (cli.py:2557, 2711, 2896-2901). helpers.py:903-909 SIGTERMs the job when the SSE client disconnects. The smart-skip temp list leaks on every interruption (cli.py:2617-2630): 7 /tmp/fablegear_smart_skip_*.txt files, each listing full library paths, accumulated from my interrupted runs. The UI log also prints 'Interrupt signal sent — waiting…' after '✗ Exited with code -15'.
- **Fix:** In cmd_process, install signal handlers (SIGTERM, SIGINT, SIGPIPE/BrokenPipe) that set a cancel Event; check it between files; on cancel, save the checkpoint, persist the per-file journal, and emit a partial report with exit code 130. Save the checkpoint after every file, or on a 2-second timer, not every 25. Use try/finally for temp-file cleanup, or pass the list over stdin. Decouple the job from the SSE connection, or add a beforeunload warning while isRunning.

#### [MEDIUM · demonstrated] Resume banner is localStorage-only and carries no starting-point reference (no modes, done/remaining, start time, or restore point)

- **ID:** `tool-process-b-resume-banner-no-starting-point` · **Area:** session-resume · **Tool/surface:** Tag Tracks resume banner (_showToolResumeBanner)
- **Expected:** A server-side record of the interrupted run (started_at, roots, all modes, done and remaining counts, report path, restore point), surfaced in the banner and the Undo Wizard. Resume skips done files reliably, and the final report covers both sessions.
- **Actual:** The banner holds only age and paths, disappears with browser storage, and has no restore-point link. Resume relies on tag presence rather than on a record of completed work.
- **Evidence:** After a cancel or a kill -9 plus server restart, reloading the UI showed the banner: 'Interrupted run — 1m ago | <Music Library path> | Resume | Start Fresh' (screens/kill1_reload_banner.png, cancel1_reload_banner.png). It does not show which modes were used, how many files were done (4 of 10 after the kill), when the run started, or any restore point. Its Resume tooltip claims 'files already done are skipped', but with fewer than 25 files there is no server checkpoint, so Resume re-analysed the already-done files (cancel1_resume log: 'Smart Skip: 9/11 file(s) need work'). The same server after restart, opened in a fresh browser context, showed no banner at all (kill1_fresh_storage_no_banner.png). /api/checkpoint/check and /api/undo/timeline both returned empty, so nothing server-side survives a WebView storage reset. The resumed run's report does not mention the earlier interrupted session or the files it skipped per checkpoint (bulk_resume_log.txt: '15 files were analyzed' with no '25 skipped per checkpoint'). Code: static/chop_shop/pipeline.js:583-629 and 664-678; runners.js:148-158.
- **Fix:** On run start, write a server-side run record (~/.fablegear/runs/<run_id>.json or a DB table) with config, a roots manifest and the restore point; update done_paths per file. Build the banner from GET /api/checkpoint/check (fixed, see the companion finding) or a /api/runs/interrupted endpoint, and show modes, N/M done, start time and 'Revert to before this run'. Fold the prior session's counts into the resumed report.

#### [MEDIUM · demonstrated] /api/checkpoint/check?tool=process always returns exists:false even when a process checkpoint exists (config-key mismatch)

- **ID:** `tool-process-b-checkpoint-check-key-mismatch` · **Area:** session-resume · **Tool/surface:** GET /api/checkpoint/check
- **Expected:** The pre-flight check finds the same checkpoint the CLI will resume from.
- **Actual:** It always reports none, so the UI cannot show real progress or offer an informed resume/restart choice.
- **Evidence:** The bulk_cancel run (40 files, cancelled at 27) left ~/.fablegear/checkpoints/process/6178cc0e88bed2e6.json.gz, containing roots [Music Library, Bulk Crate], config {bpm:true,key:true,normalize:false,enrich:false,force:false}, and 25 done_paths. GET /api/checkpoint/check?tool=process&path=<Music Library>&path=<Bulk Crate> returned {"exists":false}. checkpoint._config_key('process', roots, {}) gives b31a8ba34345d187, while the CLI key is 6178cc0e88bed2e6. routes_tools.py:859-875 builds a config only for duplicates and passes {} for process, whereas the CLI uses the config dict at cli.py:2454-2458 and 2660-2663. The CLI log also prints 'Found checkpoint ... (25/0 done)', because 'total' is never saved.
- **Fix:** Move the per-tool checkpoint config construction into one shared function (e.g. checkpoint_config_for('process', args/modes)) used by both cli.py and the route. Have the route accept the same mode params the UI sends. Save 'total' in the checkpoint payload.

#### [MEDIUM · demonstrated] A new run silently resumes a stale checkpoint from a different mode: an aggressive (overwrite) run skipped 25 files done by an interrupted passive run

- **ID:** `tool-process-b-stale-checkpoint-mode-change` · **Area:** session-resume · **Tool/surface:** Tag Tracks (cmd_process _get_checkpoint)
- **Expected:** A run with different settings never inherits another run's progress. Clicking Run (rather than Resume) starts fresh or asks first.
- **Actual:** The stale checkpoint is silently applied; files are skipped and the report presents it as normal.
- **Evidence:** After a passive run was interrupted at 27/40 (checkpoint with 25 done_paths), I set BPM=Aggressive and Key=Aggressive and clicked 'Detect BPM & Key' (not Resume). The log shows 'Found checkpoint from 2026-10-05T18:54:45 (25/0 done) — resuming.' The report shows '[1/2] Music Library: 3 files were analyzed. 8 already done per checkpoint — skipped. [2/2] Bulk Crate: 15 analyzed, 17 already done per checkpoint — skipped' (out/bulk_aggr_log.txt). So the overwrite the user asked for was not applied to 25 files. Causes: the checkpoint key config holds only booleans {bpm, key, normalize, enrich, force} (cli.py:2660-2663), so passive and aggressive collide; the default checkpoint_action is 'resume' (cli.py:725, 4287-4294); and the UI's Run button never sends checkpoint_action (runners.js:139-158). Only 'Start Fresh' resets it (Start Fresh itself works: both the localStorage entry and the server checkpoint were removed).
- **Fix:** Include bpm_mode, key_mode, normalize_mode, enrich_mode, rename_mode and fix_octaves in the checkpoint config. Have the UI send checkpoint_action=reset on a normal Run and checkpoint_action=resume only from the Resume button. When a checkpoint exists and the Run button is pressed, prompt the user.

#### [MEDIUM · demonstrated] /api/cancel, /api/cancel/force and /api/quit kill every <install>/cli.py process system-wide, including jobs owned by another FableGear server or the MCP server

- **ID:** `tool-process-b-cross-instance-kill` · **Area:** chop-shop-tool · **Tool/surface:** helpers.terminate_managed_subprocesses / _list_orphaned_cli_pids
- **Expected:** Interrupt or quit affects only this server's own jobs, plus true orphans whose owning server is dead and whose runtime token belongs to this install's registry.
- **Actual:** One instance's Interrupt or Quit SIGTERMs another instance's in-flight Tag Tracks job, leaving that library half-tagged with no record.
- **Evidence:** Two of my Tag Tracks runs on port 6111 died with 'Exited with code -15' without any action from me. rename1 died at 18:59:34, the same second sbx-wizard/server.log records 'POST /api/quit'. rename2 died at 19:00:56, the same second sbx-dupes-b/server.log records 'POST /api/cancel'. All the sandboxes ran from the same install dir. After I moved my server to a separate copy of the app, no further unexplained kills happened. Code: helpers.py:348-418 pgrep/ps-matches any process running f'{REPO_ROOT}/cli.py' regardless of owner or runtime token; terminate_managed_subprocesses(include_orphans=True) (515-551) kills them; /api/cancel (routes_tools.py:1247-1250) and /api/quit (app.py:1742-1755) both use include_orphans=True. In a real install this reaches MCP-dispatched jobs too (mcp_server.py launches the same cli.py through job_dispatcher). Inferred for the MCP case.
- **Fix:** Restrict orphan kills to PIDs listed in active_subprocesses.json whose owner_pid is dead and whose runtime_token matches. On Linux check /proc/<pid>/environ for FABLEGEAR_RUNTIME_TOKEN or FABLEGEAR_SERVER_OWNER_PID; on macOS use the registry, or have the CLI write a per-PID lockfile with its owner. Never kill a cli.py whose owner server is alive and is not us.

#### [MEDIUM · demonstrated] After the server is killed (kill -9 or crash), the Tag Tracks CLI keeps running unobserved, writes another file, then dies on a broken pipe; the registry is left stale

- **ID:** `tool-process-b-orphan-after-server-kill` · **Area:** session-resume · **Tool/surface:** Tag Tracks (cli.py process) / helpers._stream
- **Expected:** The CLI notices that its owner server is gone (FABLEGEAR_SERVER_OWNER_PID is already in its env) or that stdout is closed, and then stops cleanly at a file boundary with checkpoint and journal. The server reconciles the registry at startup and surfaces the interrupted run.
- **Actual:** An unobserved write happens after the UI loses contact; the stale registry entry stays; nothing is recorded server-side.
- **Evidence:** kill1: SIGKILL of server PID 6764 after 3 progress events. 'ps' 0.3 s later showed the CLI 23251 re-parented to PID 1 (start_new_session=True, helpers.py:885-894). The UI saw 3 files, but the snapshot shows 4 changed (untitled track 03.mp3 was written after the server died). The CLI then exited, most likely on BrokenPipe at its next print. It saved no checkpoint, persisted nothing, and wrote no report. ~/.fablegear/runtime/active_subprocesses.json still listed PID 23251 after the restart. On restart, nothing server-side indicated an interrupted run.
- **Fix:** In the CLI's per-file loop, check os.getppid() or that FABLEGEAR_SERVER_OWNER_PID is alive, and treat BrokenPipeError on stdout as a cancel (save checkpoint, journal, exit). On app startup, reconcile active_subprocesses.json, convert dead entries into 'interrupted run' records, and show them in the UI.

#### [MEDIUM · demonstrated] Corrupt files are moved out of the library to Archive/Quarantine with no journal entry, no record of the original path, and no undo; the card says they are 'skipped'

- **ID:** `tool-process-b-quarantine-unjournaled` · **Area:** chop-shop-tool · **Tool/surface:** Tag Tracks (audio_processor.quarantine_file)
- **Expected:** Either skip and report, as the card says, or move with a journal row {from, to} that the Undo Wizard can revert, with the full original path in the report.
- **Actual:** An undisclosed, unjournaled file move. Restoring by hand needs the original path, which is recorded only in the transient log.
- **Evidence:** Added 'Music Library/House/Broken Upload - Damaged.mp3' (random bytes) and ran the UI defaults. The file was MOVED to 'FableGear Archive/Quarantine/Broken Upload - Damaged.mp3' (sha f5cb04b0 preserved). The report lists only the filename and the Quarantine folder ('Inspect in the Quarantine folder. Delete or restore manually.'), not the original House/ subfolder. No 'quarantine' row in fg_processing_log; /api/undo/operations unchanged; nothing to revert in the wizard. Any Rekordbox DB row for that path would be left pointing at a missing file. The card text says: 'If a file can't be read (corrupt, DRM, unsupported format), it's logged as an error and skipped' (fingerprinting.html:35). Code: audio_processor.py:138-170 and 1298-1301; cli.py:2505-2507.
- **Fix:** Call archive.log_operation('quarantine', dest, metadata={'from': src, 'run_id': ...}) and add 'quarantine' to _REVERTIBLE as move_back. Put the full original paths in the report. Update the card copy, or make quarantining opt-in. Optionally flag the Rekordbox DB row as missing.

#### [MEDIUM · demonstrated] Tag Tracks report is aggregate-only: no per-file changes, silent BPM failures, mis-counted overwrites, no persisted run log

- **ID:** `tool-process-b-report-quality` · **Area:** chop-shop-tool · **Tool/surface:** Tag Tracks report (cli.py _run_shared_report / cmd_process)
- **Expected:** A report listing every file touched with old→new values, all failures including BPM detection failures, and overwrites. The full log is persisted next to the report and linked from the UI.
- **Actual:** Only an aggregate summary; failures and overwrites are hidden; no persisted per-file log.
- **Evidence:** 1) The run1 report says only '10 files were analyzed. BPM written: 1 files. 1 already had one. Key written: 10 files.' The log has 8 'BPM 0.0 out of range' warnings. Those 8 files are not listed as errors or warnings in the report, errors=0, so they are not offered for retry. 2) No per-file list of which tags changed, from what to what. Combined with the missing undo, the user cannot revert by hand. 3) The M4A BPM overwrite is counted under 'written', not flagged as an overwrite. 4) <Archive>/Logs/Tag Tracks/ stays empty after about 15 runs: config.py LOG_DIRS (lines 85-95) is never written anywhere, so the per-file log exists only in the UI panel. 5) With normalize on, the report goes to Reports/Normalize/ instead of Tag Tracks. 6) The report path in the modal is plain text, not a link (modals.js:77-78). 7) Normalization failures are categorised as 'Tag Write Failures' with the advice 'Check file is not read-only, then re-run with Force tag-overwrite on' (cli.py:2849-2861).
- **Fix:** Write a per-file table (path, field, old, new, status) to Reports/Tag Tracks/<run_id>.csv and link it. Count 'detection failed' as a warning category and include it in the error summary and retry. Tee the CLI output to Logs/Tag Tracks/<run_id>.log. Separate the normalisation-failure category from tag-write failures. Make the report path clickable (/api/open-file).

#### [MEDIUM · demonstrated] 'Retry errored tracks only' option on the Tag Tracks card can never be shown (CSS display:none overrides the JS)

- **ID:** `tool-process-b-retry-row-hidden` · **Area:** chop-shop-tool · **Tool/surface:** Tag Tracks card (#process-retry-errored-row)
- **Expected:** After a run with retryable errors the card shows 'Retry errored tracks only (N from last run)'.
- **Actual:** The control is unreachable. The report-modal button is the only path to retry, and only after directory-mode runs.
- **Evidence:** After a directory-mode run with an error summary (Retry Test folder; the M4A 'normalisation failed'), #process-retry-errored-row was not visible. In the live page, setting _lastErrorSummary = {tag_failed:[...]} and calling _showRetryOption() gave {count:1, inlineDisplay:'', computed:'none', visible:false, badge:'1 from last run'}. fablegear.css:5905 has '#process-retry-errored-row { display: none; }', and runners.js:26 sets row.style.display = '', which merely removes the inline style. Separately, the default smart-skip path never prints FABLEGEAR_ERROR_SUMMARY (that is printed only in process_directory, audio_processor.py:1375-1407), so _lastErrorSummary stays null after default runs (0 occurrences in out/corrupt1_sse.json).
- **Fix:** Set row.style.display = 'flex' (or toggle a .visible class whose CSS overrides the default). Emit FABLEGEAR_ERROR_SUMMARY from the --paths-file/smart-skip branch of cmd_process as well. Persist the last error summary server-side so it survives a reload.

#### [MEDIUM · demonstrated] MusicBrainz enrichment silently does nothing when fpcalc is missing: no warning in the log, the report, or the UI

- **ID:** `tool-process-b-enrich-silent-noop` · **Area:** chop-shop-tool · **Tool/surface:** Tag Tracks enrich_mode / health_acoustid
- **Expected:** The run preflights fpcalc, the acoustid module and the key once, warns loudly in the log and the report ('MusicBrainz enrichment skipped: fpcalc not installed'), and the UI disables or flags the mode.
- **Actual:** The user selects enrichment, the run reports success, and nothing was enriched, with no indication why.
- **Evidence:** The UI run 'enrich' (MusicBrainz passive) on a sandbox without fpcalc: the only mention in the whole log is 'ENRICH_MODE:passive', and the report has no enrichment line. Code: audio_processor.py:713-717 logs the failed preflight at DEBUG and returns None per file. runners.js:89-96 warns only when acoustid_api_key_configured is false, but a built-in key ships by default (user_config.py:76), so no toast appears. The card badge says only 'Requires AcoustID API key' (fingerprinting.html:99, 109). collect_health() runs per file, spawning 'fpcalc -version' every time (inferred). Live lookups are impossible here because fpcalc is absent.
- **Fix:** Call collect_health() once in cmd_process when enrich_mode != off. If not ok, print '[WARN] … skipped: <reason>' and add a report line. Expose fpcalc_ok in /api/config and have the card show 'Requires fpcalc (brew install chromaprint)'. Cache the health result for the whole run.

#### [MEDIUM · demonstrated] Normalize mode always fails for M4A (stream-copy plus filter) and for AIFF/WAV with embedded artwork

- **ID:** `tool-process-b-normalize-m4a-and-art-fail` · **Area:** chop-shop-tool · **Tool/surface:** Tag Tracks normalize_mode (audio_processor._normalise_file / _get_ffmpeg_codec_args)
- **Expected:** M4A/AAC/ALAC are re-encoded with a matching codec, and artwork is preserved or ignored, so normalization works on common DJ formats.
- **Actual:** Every M4A and every artwork-bearing AIFF/WAV fails normalization.
- **Evidence:** norm1: 'ffmpeg failed for Marlo & The Tens - Glitterball.m4a: Error opening output file … Function not implemented'. Reproduced directly: ffmpeg -i in.m4a -af volume=3dB -codec:a copy … gives 'Filtergraph 'volume=3dB' was specified, but codec copy was selected. Filtering and streamcopy cannot be used together.' _get_ffmpeg_codec_args returns ['-codec:a','copy'] for every format other than mp3/aiff/wav/flac (audio_processor.py:455-456). An AIFF with an APIC frame failed with '[out#0/aiff] Nothing was written into output file, because at least one of its streams received no packets' (out/aiff_norm_sse.txt), because the cover stream is mapped into AIFF output. Both failures leave the original intact (safe), but they surface as 'Tag Write Failures'.
- **Fix:** Map the codec by source codec: aac → aac at the source bitrate, alac → alac. Use '-map 0:a' plus '-map 0:v? -c:v copy -disposition:v attached_pic' where the container supports it; otherwise drop video and re-attach the art with mutagen after encoding. Add a test matrix (mp3/flac/wav/aiff/m4a, with and without art).

#### [MEDIUM · demonstrated] Tag Tracks 'Rename mode' crosses into the Rekordbox DB and moves files; its savepoint is not linked to the run, and undo leaves orphans and keeps the tag changes

- **ID:** `tool-process-b-rename-mode-boundary` · **Area:** boundary · **Tool/surface:** Tag Tracks rename_mode (cli.py:2712-2736 → renamer.rename_directory with write_db(LOCAL_DB))
- **Expected:** A file tool that writes the Rekordbox DB and moves files says so up front, creates one restore point linked to the run, and its undo returns files and DB rows to the exact BEFORE state, cleaning up manifests and folders.
- **Actual:** The undo is partial and orphans are left behind. The DB savepoint is anonymous, and restoring it hits the wrong DB (see tool-process-b-savepoint-restore-wrong-db).
- **Evidence:** rename3 run: a savepoint master.backup_20261005_190228_780785.db was created before the DB write (good). kick_01.wav was moved from 'Sample Packs/' to a new 'Music Library/No-Name tracks for Tagging/' folder with a _quarantine_manifest.json. Journal rows 'rename' (revertible) and 'rename_batch' were added. Report line: 'Filename cleanup (passive): 0 renamed, 0 collisions handled, 1 quarantined, 6 errors'. The 6 errors were pyrekordbox update_content_path failing on the sandbox's missing rekordbox/share dir; the renames were then rolled back, which is a sandbox limitation, and the rollback is good behaviour. Undo Wizard > Operations > 'Return 1 file' restored the path (toast 'Returned 1 file.'), and a second revert was safe (0 reverted, 1 blocked). But kick_01.wav was not byte-identical (sha e06a69f0… vs original 6310b5f0…, because TKEY 10A remains), the empty 'No-Name tracks for Tagging' folder plus a manifest still claiming the file was quarantined were left behind, and the savepoint shows no relation to the run. The card's Rename selector says only 'rename files that changed this run'. It does not disclose DB writes or moves to a quarantine folder.
- **Fix:** Pass a run_id through to rename_directory and to _backup_db (sidecar metadata). Have the Operations undo also update the quarantine manifest and remove the empty folder. Offer a single 'Revert whole Tag Tracks run' that undoes renames, tags and DB rows together. Update the card copy to disclose the Rekordbox DB write and the No-Name quarantine move.

#### [LOW · demonstrated] Report-modal 'Retry N failed tracks with Force' on a normalization failure retries with normalization OFF and reports success

- **ID:** `tool-process-b-force-retry-ignores-normalize` · **Area:** chop-shop-tool · **Tool/surface:** POST /api/run/process-retry (report modal action)
- **Expected:** Retry repeats the failed operation (normalization), or is not offered for failures it cannot fix.
- **Actual:** A misleading success after a retry that never attempted the operation that failed.
- **Evidence:** The Retry Test folder (M4A plus MP3) with normalize passive gave the report modal 'SUGGESTED NEXT STEPS — Retry 1 failed track with Force' (screens/retry_2_report_modal_actions.png). Clicking it ran 'Retry mode: processing 1 specific file(s) — BPM_MODE:passive KEY_MODE:passive NORMALIZE_MODE:off' and ended '✓ Finished successfully' (retry_3_after_force_retry.png). The loudness was never adjusted, and the M4A BPM/key were rewritten again (because of the M4A passive bug). Code: routes_tools.py:345-353 hardcodes '--no-normalize' and '--force' ('--force' has no effect when explicit modes are passed, cli.py:2386-2395 and 2490); modals.js:111-131 offers the retry for tag_failed entries, which include 'normalisation failed'.
- **Fix:** Carry the failure type in FABLEGEAR_ERROR_SUMMARY. For normalisation failures, either pass the normalize mode through to process-retry or hide the retry button and suggest Convert. Drop the dead '--force' flag, or make it mean aggressive.

#### [LOW · demonstrated] Resume does not detect that inputs changed between sessions, and silently processes new files

- **ID:** `tool-process-b-resume-input-change` · **Area:** session-resume · **Tool/surface:** Tag Tracks resume (_resumeProcess)
- **Expected:** Resume compares a manifest (path, size, mtime) taken at the original start and warns about added, removed or modified files before continuing.
- **Actual:** A resumed run can touch files that did not exist when the user started the original run, with no warning.
- **Evidence:** After the kill -9 and restart, I copied 'Incoming/Newcomer - Fresh Cut.mp3' into Music Library/House and clicked Resume on the banner. The log shows 'Smart Skip: 10/12 file(s) need work' and '[7/10] Newcomer - Fresh Cut.mp3' with KEY written 7A. There was no notice that the folder content differed from the interrupted run (out/kill1_resume_log.txt). _resumeProcess just repopulates the form and calls runProcess() (pipeline.js:664-678). The localStorage checkpoint stores only paths and modes, with no file manifest.
- **Fix:** Store a manifest in the server-side run record at start. On resume, diff it and show 'N new / M missing / K modified since the interrupted run — continue with original set / include new / start fresh'.

#### [LOW · inferred] Tag Tracks card copy misstates behaviour (key notation, overwrite guarantee, error handling, enrichment requirements)

- **ID:** `tool-process-b-card-copy-inaccurate` · **Area:** chop-shop-tool · **Tool/surface:** templates/partials/physical_library/fingerprinting.html
- **Expected:** The card describes what the tool actually does.
- **Actual:** Several promises in the copy are false or incomplete.
- **Evidence:** fingerprinting.html:18-20 says the key 'is translated to whichever notation RekordBox uses (Camelot, Open Key, or standard — whatever's already in your database)', but audio_processor.py:334-348 always writes Camelot (LIBROSA_TO_CAMELOT), with no DB lookup. Live runs wrote '9A', '11B' and so on. Line 7 says it 'won't overwrite your Mix In Key work unless you ask it to', which is contradicted for M4A (demonstrated). Line 35 says an unreadable file is 'logged as an error and skipped', but it is moved to Quarantine (demonstrated). Lines 16-17 credit librosa for BPM, but essentia is preferred when installed (audio_processor.py:1008-1012). Lines 99 and 109 say enrichment needs only the API key, but fpcalc is also required. The BPM tag is rounded to an integer (161.5 → '162', audio_processor.py:916, 922, 933), which the card does not mention.
- **Fix:** Fix the code (M4A passive, quarantine journaling) or correct the copy: always Camelot, essentia then librosa, fpcalc required, the quarantine move, integer BPM.

#### [INFO · demonstrated] Directory-mode runs write ~/rekordbox-toolkit/scan_index.json, outside ~/.fablegear and the Archive

- **ID:** `tool-process-b-legacy-scan-index-path` · **Area:** other · **Tool/surface:** audio_processor.process_directory
- **Expected:** Tool caches live under ~/.fablegear (or the Archive), with consistent step keys.
- **Actual:** A stray legacy-path cache file, and inconsistent state-tracker keys.
- **Evidence:** The snapshot diff after the pipeline dry-run and the normalize runs shows ADDED/CHANGED home/rekordbox-toolkit/scan_index.json (audio_processor.py:1341). It is not covered by archive sync or any undo, and it uses the legacy product name. Minor: pipeline steps lack a 'type' key (routes_tools.py:586), so the state tracker records 'Tag Tracks' rather than 'process' in .fablegear_state.json for pipeline runs (observed in the state file).
- **Fix:** Move the scan index to ~/.fablegear/cache/scan_index.json and migrate the old one. Include 'type' in the pipeline's built step dicts.

#### [INFO · demonstrated] Confirmed-good behaviours (for calibration)

- **ID:** `tool-process-b-confirmed-good` · **Area:** chop-shop-tool · **Tool/surface:** Tag Tracks
- **Expected:** n/a
- **Actual:** n/a
- **Evidence:** (a) Passive smart-skip left a fully tagged ID3 MP3 (Nula - Night Drive.mp3, TBPM 122/TKEY 8A) and the FLAC's existing bpm=130 untouched (sha unchanged in run1). (b) Cancel followed by Resume ended byte-identical to an uninterrupted run (all 11 audio sha256 equal, after1 vs after_resume). (c) The file being processed at the moment of cancel was not corrupted (Kaito.wav sha unchanged). (d) 'Start Fresh' removed both localStorage rb_ckpt_process and ~/.fablegear/checkpoints/process/*.gz (bulk_fresh). (e) With a server checkpoint, Resume skipped completed work: 'Resuming: 25/40 files already done per checkpoint, skipping those.' (f) The rename journal revert works and is safe to run twice (second call: reverted 0, blocked 1, no damage). (g) The rename stage takes a DB savepoint before writing, and rolled back the file renames when the DB update failed. (h) Normalization failures (M4A, AIFF with art) left the originals intact via the .bak swap. (i) The CLI refuses to tag without the archive (cli.py:2421-2425).
- **Fix:** Keep these behaviours, and add regression tests for (a), (b), (d), (e) and (f).

## Chop Shop: Find Duplicates + Prune

_Auditor: `tool-duplicates-b`_

I audited Find Duplicates (Quick hash scan and Deep scan) and Prune end to end. The live work used two sandboxes on port 6112. Round 1 used the strict pyrekordbox-schema fixture. Round 2 rebuilt the DB with the nullable schema the repo's own tests describe as production-like. Both rounds were driven through the real Chop Shop UI with Playwright (scan, review, Move to Trash, Interrupt). I hashed every file and dumped DEVICE_DB, LOCAL_DB and the FableGear DB before and after each step. Verdict: the scan works, and keeper-in-DB re-threading works (playlist slots and cues moved to the keeper, savepoint API restores the DB rows byte-identically). The safety story around Prune does not hold, for six reasons:
(1) Pruning permanently deletes the FableGear DB rows, which are the Record Room's default library. Their playlist memberships cascade away, are never relinked to the keeper, and no backup covers them.
(2) The Undo Wizard's Trash and Savepoints tabs throw ReferenceError, so there is no UI revert at all.
(3) The trash-restore API dumps files flat into the music root, silently overwrites same-named files, and restores no DB state.
(4) A per-file DB error does not stop that file being moved, and the run still reports success.
(5) When the keeper is not in Rekordbox, the in-DB copy is deleted and its playlist slot is left dangling, with its cues orphaned.
(6) Nothing stops the user deleting every copy, the best copies, or the only surviving copy (stale report), and permanent delete has no confirmation.
Interrupt only works in the DB phase. After restart nothing is offered for Prune. The Duplicates resume banner is localStorage-only, carries no starting point, and its Resume switches a Deep run to Quick. Neither scan mode warns when it cannot actually look: Deep says 'No duplicates found' when fpcalc is missing, and Quick ignores files that have never been synced.

**Coverage**

- LIVE (UI via Playwright, headless Chromium, port 6112): Deep scan (match=all) with fpcalc absent; Quick scan on the music root; review phase (auto-selection, Select All Lower/Best, keeper+lower 'both'); Move to Trash; Delete permanently; Load an existing report (stale CSV, stress CSVs); Interrupt during prune DB phase; Interrupt during prune file phase; Interrupt during the Deep scan; resume banner after a failed Deep run plus its Resume button; Undo Wizard walk of all 5 tabs (Timeline, Operations, Database, Savepoints, Trash).
- LIVE (API, the same endpoints the UI or undo panel call): /api/undo/trash/restore (twice); /api/undo/savepoint/restore (twice); /api/checkpoint/check; /api/duplicates/load with the default path; prune stage+run via curl for the kill -9 test (staging 4001 paths through the UI was too slow to time the kill reliably).
- LIVE crash tests: kill -9 of the real server PID during the prune file phase (r5: 363/4000 moved; r6b: 303/4001 moved with a DB-backed file last). Server restart, then UI reload to look for any resume offer. kill -9 of the server during a Deep scan (CLI child orphaned, then died). Note: the first 'r6' kill hit a wrapper subshell PID, not the server. That run is excluded; r6b is the valid repeat.
- Evidence capture: sha256 of every file under the music root, Incoming and ~/.Trash, plus listings of the FableGear Archive and ~/.fablegear. Row dumps of DEVICE_DB (content/playlist songs/cues), LOCAL_DB and the FableGear DB (fg_content/fg_playlist_song/fg_processing_log). The five /api/undo/* GETs before and after every step. Scripts: $SP/audit/tool-duplicates-b/{setup_data.py,snap.py,diff.py,ui_run.py,undo_ui.py,order_probe.py,layout_probe.py}. Snapshots: $SP/audit/tool-duplicates-b/snap_*.json. SSE/log captures: $SP/audit/tool-duplicates-b/out/. Screenshots: /home/user/FableGear/docs/audits/2026-10-05/screens/tool-duplicates-b/ (71 files). Round-1 sandbox preserved at $SP/audit/tool-duplicates-b/round1_sbx_state. ($SP = /tmp/claude-0/-home-user-FableGear/9074577f-2e6a-5e16-bdce-ef5b26f1b16b/scratchpad)
- Sandbox data I added (not product code). Byte-identical copies, because the Quick scan only finds exact copies and fpcalc is absent: Techno/01 - Nula - Night Drive.mp3, House/02 - Marlo & The Tens - Glitterball.m4a, and Quiet/ + Loud/Nula - Night Drive (copy).mp3. DEVICE_DB rebuilt to hold the doomed copies, with a Rekordbox playlist 'Peak Time' and hot/memory cues seeded on the doomed tracks. FableGear playlist 'Warmup' created via POST /api/library/playlists. Round 2 built DEVICE_DB under tests/rekordbox_meta_support.relaxed_rekordbox_nullability, plus the two raw tables tests/test_pruner_associations.py creates, so it mirrors the production schema the tests describe. The stress CSVs (20k fake paths; 4000 tiny real files) exist only to make runs long enough to interrupt.
- NOT testable live: Deep acoustic fingerprinting, fuzzy matching and AcoustID enrichment. fpcalc/chromaprint is not installed, and I did not fake it. So the near-duplicate mp3 pair, the flac/aiff cross-format pair and the original-vs-extended-mix pair could not be grouped or kept apart live. Quick correctly does not group them (they are not byte-identical). The extended-mix protection (_FP_LENGTH_RATIO_MIN=0.90, duplicate_detector.py:799-804) is inferred only. Also not testable: the mid-fingerprint-loop cancel with real fpcalc, and resume of a real partial fingerprint checkpoint. With fpcalc missing, the 3706-file loop finishes in under 1 s. I proved the root causes instead: no checkpoint on SIGTERM (live), and nondeterministic work-list order across processes (live probe).
- Not covered: a running Rekordbox process (the RB-running guards were not exercised); macOS ~/.Trash semantics and cross-volume moves (inferred only); real master.db files from different Rekordbox versions. Whether production DBs lack djmdCloudExportSongPlaylist is unverified, so I state that trigger as fixture-derived.
- Out of assignment, seen in passing: (a) every UI open writes a new Archive/Reports/Audit/audit_<ts>.txt (20 created during this session); (b) GET /api/library/playlists/777001/tracks?db=device returned 'Playlist not found' because the route reads LOCAL_DB for db=device (routes_player.py:1206-1207); (c) the Undo Wizard panel renders clipped and mispositioned at 1440x900 (r2_undo_trash.png). These are listed as info findings for the owning auditors.

**Per-tool safety matrix**

### Find Duplicates (Quick hash scan / Deep acoustic scan) — #step-duplicates, GET /api/run/duplicates → cli.py duplicates

| Check | Result |
|---|---|
| R — Report | PARTIAL. Success with groups: Archive/Reports/Duplicates/duplicates_<ts>.txt (3-line summary) + duplicate_report_<ts>.csv (good: group/action/rank/path/size) + trash_rescue_report_<ts>.txt; the report modal shows the CSV path. Success with no groups: txt only, saying 'No duplicates found. Every file in this folder appears to be unique.' This also happens when 15/15 or 3706/3706 fingerprints failed (fpcalc missing) and when the Quick index is stale. Cancel (exit -15): nothing written. Failure (exit 1, bad path): nothing, log line only. Archive/Logs/Duplicates is never written. |
| M — Revert marker | N/A for audio files (they are not touched). The scan does write the FableGear DB (fg_processing_log 'duplicate_scan', fingerprints via bulk_set_fingerprints) and Music Library/.fablegear_state.json. None of these has a marker. That is benign, but it contradicts the 'Read-Only Scan' badge. |
| U — Undo | N/A (no audio mutation). No undo is needed or offered for the FableGear DB fingerprint/op-log writes. |
| C — Cancel / interrupt | DEMONSTRATED (r12b). Interrupt sends SIGTERM to the CLI and it dies with exit -15. There is no signal handler, and cmd_duplicates never passes a cancel_event (cli.py:845-851), so no checkpoint is saved (checkpoints/duplicates stays empty), /api/checkpoint/check returns exists:false, and no partial report is written. kill -9 of the server (r13): the CLI child is orphaned (ppid 1) and dies within 4 s, with no report or checkpoint; runtime/active_subprocesses.json keeps a stale entry after restart. Checkpoints are only written every 250 files (duplicate_detector.py:84), and failures count as 'completed'. |
| S — Resume after restart | DEMONSTRATED (r10/r10b). The only resume surface is a localStorage banner: 'Interrupted run — just now \| <paths> \| Resume \| Start Fresh'. It appears even when the run simply failed (exit 1). It shows no start time beyond relative age, no config (deep/fuzzy omitted), no done/remaining counts and no restore point. Clicking Resume on a Deep/fuzzy run issued /api/run/duplicates?...&scan_mode=quick, because _resumeDuplicates (pipeline.js:698-707) never restores scanMode. The UI never calls /api/checkpoint/check. A server-side resume slices files[completed:] (duplicate_detector.py:1132), but the work-list order changes between processes (list(set) at :447; order_probe.py shows three different orders), and the checkpoint key excludes the file list. Resume can therefore skip arbitrary unfingerprinted files, and changed inputs go undetected. |
| D — Dry run / preview | YES. The scan itself is the preview: CSV plus an in-card review of groups with keeper star, format, size, rank and an 'in DB' badge. No audio is touched. Gaps: rows show the filename only (three identical names in one group are indistinguishable), and there is no warning when the keeper is not in Rekordbox or is missing. |
| B — Boundary | Does not write the Rekordbox DB. It does write the FableGear DB (fingerprints, op log) and a dotfile in the music root, despite the 'Read-Only Scan' badge. Quick mode reads only FableGear-DB hashes, so un-synced files are invisible to it (r14: 3695 identical files reported unique). |

_Evidence:_ Screens: r0_deep_2_scan_done.png, r12b_*, r14_quick_stale_2_scan_done.png, r10b_resume_0b_banner.png, r10b_resume_0c_after_resume.png. Logs: out/r0_deep_log.txt (15x 'fpcalc not found' then 'No duplicates found'), out/r12b_scan_cancel_log.txt ('✗ Exited with code -15'), out/r13_kill_scan.txt. Commands: curl /api/checkpoint/check?tool=duplicates&...&match_mode=all → {"exists":false}; order_probe.py run with PYTHONHASHSEED=1/2/3 produced three different candidate orders. Code: cli.py:776-903, duplicate_detector.py:561-666 and 1012-1450, static/chop_shop/runners.js:322-358, static/chop_shop/pipeline.js:583-631 and 698-707.

### Prune (interactive Chop Shop) — #btn-prune-start → POST /api/prune/stage + GET /api/run/prune (in-process pruner.prune_files)

| Check | Result |
|---|---|
| R — Report | NO. No report file is written on success, cancel or failure: Archive/Reports has no Prune folder and Logs/Prune stays empty after 8 prunes. The SSE log in the terminal panel is the only output. The status line says '✓ Prune complete — N files moved to Trash. Check the report for details.' but no report modal opens and no file exists. Per-file 'prune' and 'prune_batch' rows go to the FableGear fg_processing_log, which no UI surfaces. |
| M — Revert marker | PARTIAL, UNLINKED. Before mutating, write_db copies the target master.db to Archive/Savepoints/master.backup_<ts>.db (DEVICE_DB when mounted). Files go to ~/.Trash/FableGear_Pruned_<ts>/, and each file is journaled to fg_processing_log. None of this is tied to the job: GET /api/undo/timeline stays [] (UI runs never reach job_dispatcher); /api/undo/operations excludes prune by design (routes_undo.py:317-321); the Database tab has no transaction; savepoint names carry neither the tool nor the source DB, and several land in the same minute. The FableGear DB deletions (fg_content plus cascaded playlist rows) have NO marker at all, and the Archive mirror (Archive/Database/fablegear.db) is re-synced after the prune without them. |
| U — Undo | NO via UI: the Undo Wizard Savepoints and Trash tabs throw 'ReferenceError: undoLoadSavepoints/undoLoadTrash is not defined'. Via API (r2): POST /api/undo/savepoint/restore brought DEVICE_DB content, playlist_songs and cues back identical to BEFORE (verified by row dump) and is itself undoable, since it backs up the current DB first. POST /api/undo/trash/restore returned bytes intact but to the wrong places: files land flat in the music root ('Nula - Night Drive (copy)__Loud.mp3', 'Marlo & The Tens - Glitterball.m4a' at root, not House/, Loud/, Disco/), an existing same-named file was silently overwritten (sha 58fd… → 1ae4…), and the FableGear rows and playlist 'Warmup' were not restored. Net state is NOT byte-identical to BEFORE. Running both reverts twice is safe: a no-op plus one extra savepoint, with an empty FableGear_Pruned_* folder left listed as 0 files. |
| C — Cancel / interrupt | DB phase (r3, 20k paths): Interrupt → 'Cancel requested — rolling back… No files were moved', exit 130, UI '⚠ Prune cancelled…'. Correct, but leaves an empty trash folder and a savepoint. File phase (r4): the cancel is ignored by design; the UI logs 'Prune will stop at the next safe checkpoint', force-resets after 5 s and stops listening at bulk_0412, while the server moved all 4000 files with no completion shown. kill -9 in the file phase (r5, r6b): 363/4000 and 303/4001 moved. The DB commit happens before the moves, so the Rekordbox row for Techno/Vera Lux - Pressure.flac was deleted while the FLAC file stayed on disk. Only the per-file journal rows survive. |
| S — Resume after restart | NONE (r5_after_restart_0_open.png). There is no checkpoint (/api/checkpoint/check rejects tool=prune), no banner and no record of the interrupted batch. Staging tokens live in memory and are lost on restart. The report CSV is re-read with live data, but a stale CSV whose keeper is missing still auto-selects and prunes the only remaining copy (r11). |
| D — Dry run / preview | NO in the UI. The review list shows which files will move, but not the DB consequences: which playlist slots and cues will be re-threaded or dropped, that the keeper is absent from Rekordbox, or that FableGear playlists will lose entries. The CLI 'prune' defaults to dry-run (cli.py:1114-1190) but the Chop Shop never exposes it. |
| B — Boundary | CROSSES. This file tool deletes Rekordbox DEVICE_DB rows, re-threads playlists, cues, tags and the rest, and backfills keeper metadata. That write is covered only by an unlinked savepoint whose restore UI is broken. It also deletes FableGear DB rows, cascading the Record Room's own playlist memberships, with no marker or undo. LOCAL_DB is never updated, so its rows point at trashed files (local_db unchanged in snap diffs). |

_Evidence:_ Snapshots and diffs: python diff.py before_prune after_prune (round 1), r2_before r2_after (round 2), r2_before r2_undo1, r2_undo1 r2_undo2, r3b_before r3b_after_cancel, r6b_before r6b_after. SSE: out/r2b_prune_sse.json, out/r4_interrupt_files_log.txt, out/r6b_kill_sse.txt. Screens: r2_prune_4_pruned.png, r2b_prune_4_pruned.png, r2_undo_savepoints.png, r2_undo_trash.png, r7_both_3_review.png, r8_best_3_review.png, r9_perm_4_pruned.png, r11_stale_3_review.png. Code: routes_tools.py:952-1244, chop_shop/pruner.py:601-939, routes_undo.py:107-314, static/shared/undo.js:36-40, static/chop_shop/dedupe.js:220-277 and 368-425.

### Prune (CLI / pipeline executor) — cli.py prune (inferred only, for contrast)

| Check | Result |
|---|---|
| R — Report | YES (inferred): Reports/Prune/prune_<ts>.txt via _emit_report on dry-run, no-op and success (cli.py:1166-1231). Nothing on an exception exit. |
| M — Revert marker | Same as interactive: write_db savepoint plus trash folder plus journal, unlinked (cli.py:1203-1214). |
| U — Undo | Same gaps as interactive; it shares prune_files. |
| C — Cancel / interrupt | No cancel hook (should_cancel is not passed, cli.py:1206-1213). SIGTERM kills mid-run with the same DB-commit-before-move desync. |
| S — Resume after restart | None. |
| D — Dry run / preview | YES: dry-run by default, --no-dry-run to execute (cli.py:1172-1190). |
| B — Boundary | Same cross-boundary DB writes as the interactive prune. |

_Evidence:_ inferred: cli.py:1114-1231

**Findings**

#### [CRITICAL · demonstrated] Prune permanently deletes the FableGear (Record Room default) DB rows and playlist memberships of pruned files; nothing relinks or restores them

- **ID:** `dupb-fg-playlists-cascade-lost` · **Area:** boundary · **Tool/surface:** Prune
- **Expected:** Pruning a duplicate should re-point the FableGear playlist memberships, cues and beatgrid rows to the keeper's fg_content row, the way the Rekordbox side re-threads. The FableGear DB should be snapshotted before the run, and that snapshot linked to the job for undo.
- **Actual:** The doomed file's fg_content row is deleted. Its Record Room playlist entries, cues and beatgrid cascade away. They are never moved to the keeper, and no backup, journal or undo path can bring them back. The 'Playlist protection … Your playlists stay intact' promise (duplicate_prune.html:25) is false for the app's own library.
- **Evidence:** Setup: FableGear playlist 'Warmup' with FG ids 10 (House/Nula - Night Drive.mp3), 15 (Disco/Glitterball.m4a) and 9 (House/Nula (copy).mp3), all doomed, while their keepers (ids 6, 11, 1) are in the same DB. UI prune (r2b, Move to Trash) → `python diff.py r2_before r2_after`: fg_db.content loses rows 9, 10, 12, 15 and fg_db.playlist_song loses [1,1,10,1], [2,1,15,2], [3,1,9,3]. GET /api/library/playlists/1/tracks → []. After both API reverts (savepoint + trash restore) the rows are still gone (diff r2_before r2_undo1). The Archive mirror Archive/Database/fablegear.db (synced_at 18:55:32) holds no rows 9/10/12/15 and no playlist songs. The same thing happened in round 1, where the Rekordbox delete failed but the FableGear rows were deleted anyway. Code: pruner.py:846-864 `_journal_prune` → archive.delete_content(rec.id) (database.py:435-448). fg_playlist_song, fg_cue and fg_beatgrid are ON DELETE CASCADE (schema.py:200-201, 247, 266) with foreign_keys=True (database.py:184).
- **Fix:** In _journal_prune, when keeper_map has a keeper, look up the keeper's fg_content id and run UPDATE fg_playlist_song/fg_cue/fg_beatgrid SET content_id=keeper (dedupe within playlist) before delete_content. Better still: relink the doomed row to a tombstone or mark it pruned instead of hard-deleting. Snapshot fablegear.db (sqlite backup API) before prune_files and record that snapshot in a per-job manifest that the Undo Wizard can restore. Add a test with a FableGear playlist containing the doomed copy.

#### [HIGH · demonstrated] Undo Wizard Savepoints and Trash tabs call undefined functions; there is no UI way to revert a prune

- **ID:** `dupb-undo-tabs-dead` · **Area:** undo-revert · **Tool/surface:** Undo Wizard (revert path for Prune)
- **Expected:** The Trash tab lists FableGear_Pruned_* folders with a restore action, and the Savepoints tab lists DB backups with a restore action. The template blurbs promise exactly this ('Files removed by the Prune tool are moved to Trash… Restore them').
- **Actual:** Both tabs are blank and throw. The only documented recovery path for Prune is unreachable from the UI, so users must go to Finder and the CLI.
- **Evidence:** $SP/audit/tool-duplicates-b/undo_ui.py r2_undo after a prune: tab savepoints → 'PAGEERROR: undoLoadSavepoints is not defined'; tab trash → 'PAGEERROR: undoLoadTrash is not defined'. Neither tab renders any button. typeof undoLoadSavepoints, undoLoadTrash, undoViewJobDetail and undoRestoreCheckpoint are all 'undefined'. Screens: r2_undo_savepoints.png, r2_undo_trash.png. Code: static/shared/undo.js:39-40 (calls), 279 and 284 (Timeline 'Details'/'Restore checkpoint' also call undefined functions), 319-320 (leftover comment). `git log -S "function undoLoadTrash"` points at 2df457d (2026-07-16, 'Add bidirectional Rekordbox sync adapter…'), which rewrote undo.js and dropped them. The backend endpoints still exist (/api/undo/savepoints, /savepoint/restore, /trash, /trash/<f>/files, /trash/restore).
- **Fix:** Restore undoLoadSavepoints/undoLoadTrash (plus the trash file list and restore confirm) and undoViewJobDetail/undoRestoreCheckpoint, using the createElement pattern of the hardened file. Add a smoke test that clicks every undo tab and asserts no pageerror (Playwright, or a jsdom check that every function referenced in undoSwitchTab is defined).

#### [HIGH · demonstrated] A file is moved even when its Rekordbox removal failed, and the run is reported as a success

- **ID:** `dupb-db-error-files-moved` · **Area:** chop-shop-tool · **Tool/surface:** Prune
- **Expected:** If a path's DB removal or re-threading fails, its file stays in place and the path is listed as failed. Any DB error makes the run a partial failure, reported clearly.
- **Actual:** Rekordbox keeps rows, playlist slots and cues pointing at files that now sit in the Trash. The user is told everything succeeded, so they will see 'missing file' tracks in Rekordbox.
- **Evidence:** Round 1 (DEVICE_DB built by fablegear_database.rekordbox_fixture, the pyrekordbox-0.4.4 schema). UI prune of 4 doomed files. SSE (out/r2_prune_sse.json): 3x 'DB ✗ … no such table: djmdCloudExportSongPlaylist', then 'DB ✗ … NOT NULL constraint failed: djmdCue.ContentID', 'Database commit OK (0 row(s) removed)', 'Moved ✓' x4, 'Errors: 4', done exit_code 0. UI status: '✓ Prune complete — 4 files moved to Trash' (r2_prune_4_pruned.png). diff before_prune after_prune: device_db content, playlist_songs and cues are identical (rows 1005/1015/1017/1023, playlist 'Peak Time' and 6 cues still reference the four files), while all four files are now in ~/.Trash. Code: pruner.py:815-818 catches a per-path DB exception and continues; the file loop at 868-895 moves every path except protected_paths; routes_tools.py:1200-1202 sets exit_code 1 only if files_moved == 0. The trigger here is the fixture schema: the repo's own tests create the two raw-SQL tables by hand (tests/test_pruner_associations.py:48-62), so production DBs may have them. The move-anyway behaviour applies to any per-path exception (constraint, lock, schema drift).
- **Fix:** Track a db_failed set in the DB loop and skip those paths in the file loop, the same way protected_paths is honoured. Return non-zero exit / 'partial' when errors exist, and show the error count in the status line. Guard the raw-SQL association tables with an existence check (SELECT name FROM sqlite_master) so a missing optional table is skipped rather than aborting the path. Add a test where one path's rethread raises and assert its file is not moved.

#### [HIGH · demonstrated] When the keeper is not in Rekordbox, the in-library copy is deleted anyway; its playlist slot dangles and its cues are orphaned

- **ID:** `dupb-keeper-not-in-rb` · **Area:** chop-shop-tool · **Tool/surface:** Prune
- **Expected:** If the keeper has no Rekordbox row, either import or relink (repoint the doomed row's FolderPath to the keeper, keeping its ID, playlists and cues) or skip the DB delete. The UI should warn 'keeper is not in your Rekordbox library'.
- **Actual:** The track with all the curation (playlist slot, cues) is deleted from Rekordbox, which leaves a dangling djmdSongPlaylist row and NULL-ContentID cues. The new keeper is not in Rekordbox at all.
- **Evidence:** Round 2, production-like nullable schema. Group: keeper 'Techno/01 - Nula - Night Drive.mp3' (PN-ranked, NOT in DEVICE_DB) vs doomed 'House/Nula - Night Drive.mp3' (ContentID 1017, in playlist 'Peak Time' slot 888001, hot cue 900001 + memory cue 900002). The review row showed 'in DB' on the doomed copy only and gave no warning. SSE: '⚠ Keeper not in DB (01 - Nula - Night Drive.mp3) — associations left intact', then 'DB ✓ Nula - Night Drive.mp3'. pyrekordbox check afterwards: 'slot 888001 TrackNo 1 ContentID 1017 -> NO SUCH CONTENT ROW (dangling)'; 'cues with NULL ContentID: [(900001,1,1000),(900002,0,5000)]'; GET /api/library/playlists?db=device still reports track_count 3. Code: pruner.py:614-617 returns early but the caller still runs db.session.delete(row) at :800. The Playlist safety gate (:793-799) only fires when there is no keeper_map entry, and the UI always supplies one. Keeper choice ignores DB membership: dedupe_sort_key = format tier, size, RARP, tags (pruner.py:265-288).
- **Fix:** In prune_files, when keeper_rows is empty, prefer relinking: update_content_path(doomed_row, keeper_path) and skip the delete. Alternatively, mark the path protected and skip both DB delete and file move. Surface 'keeper not in DB' as a badge or warning in _makeRow, and consider ranking in-DB / cue-rich copies higher (as smart_dedup.py does). Fix the log text 'associations left intact', which is untrue after the delete.

#### [HIGH · demonstrated] Trash restore puts every file in the music-library root under mangled names, silently overwrites existing files, and restores no DB state

- **ID:** `dupb-trash-restore-flattens-overwrites` · **Area:** undo-revert · **Tool/surface:** POST /api/undo/trash/restore (Prune's only file revert)
- **Expected:** Restore returns each file to its original path (from the journal), refuses or renames on conflict, and offers to restore the matching DB savepoint, so the library reaches exactly the pre-prune state.
- **Actual:** Restore scatters files into the root, can destroy an unrelated file, and leaves Rekordbox and FableGear unrestored.
- **Evidence:** After the round-2 prune: curl -X POST /api/undo/trash/restore {folder: FableGear_Pruned_20261005_185418} → {restored: 4}. diff r2_before r2_undo1: '- House/Nula - Night Drive.mp3', '- Loud/Nula - Night Drive (copy).mp3', '- Disco/Marlo & The Tens - Glitterball.m4a', '- House/Nula - Night Drive (copy).mp3' versus '+ Music Library/Nula - Night Drive.mp3', '+ …/Nula - Night Drive (copy)__Loud.mp3', '+ …/Marlo & The Tens - Glitterball.m4a' and so on, all at the root. Bytes are identical, at the wrong paths, so the restored DEVICE_DB rows still point at missing files. Overwrite probe: root 'Probe Overwrite.mp3' (sha 58fd6c236e92fcc3…) plus a trash folder holding a different file of the same name → restore returned ok and the root file became 1ae48f88aa3a00d2. The empty FableGear_Pruned_* folder is still listed (file_count 0). Code: routes_undo.py:256-314 (dest defaults to MUSIC_ROOT, target = dest/rel with rel = basename, shutil.move overwrites); pruner.py:884-888 stores flat basenames and renames collisions to '<stem>__<parent>'. The original paths do exist in fg_processing_log rows ('prune', file_path, metadata.moved_to) but are not used.
- **Fix:** Write a manifest.json into each FableGear_Pruned_* folder ({trash_name: original_path, keeper, db_savepoint, fg_ids}), or use the fg_processing_log rows, and restore to the original paths. Never overwrite: check exists() and abort or rename. Pair file restore with the savepoint restore and FableGear re-insert, behind one 'Undo this prune' action. Remove the empty folder after a full restore.

#### [HIGH · demonstrated] One-click destructive selections: keepers can be queued for deletion, every copy of a track can be deleted, and permanent delete has no confirmation

- **ID:** `dupb-no-guard-all-copies` · **Area:** chop-shop-tool · **Tool/surface:** Prune review UI
- **Expected:** A confirmation that names counts, permanence and the affected DB rows/playlists. A server-side refusal to remove the last existing copy of a group, or every member of a group, without an explicit override. 'Select All Best Copies' should not mean 'delete the best copies'.
- **Actual:** A user can trash or permanently delete the best copy, or all copies, with no warning.
- **Evidence:** r7_both: tick the keeper rows after 'Select All Lower' → '4 files queued for removal' → Move to Trash. Both 'Vera Lux - Pressure.aiff' (keeper, AIFF row deleted from DEVICE_DB) and '.flac' were moved, leaving no copy of the track in the library. No dialog (dialogs: []). r8_best: 'Select All Best Copies' → '1 file queued for removal' → Move to Trash moved '00 - keeper.mp3', the only copy still on disk (the others were shown as 'missing'). r9_perm: tick 'Delete permanently' → button reads 'Delete Permanently →' → one click → 'Deleted ✓ bulk_0401.mp3 / bulk_0402.mp3', no dialog, nothing in the Trash. Right after a scan the UI auto-selects every lower copy (dedupe.js:259-277), so the destructive action is one click away (r1_quick_3_review.png: '4 files queued for removal'). Code: dedupe.js:220-229 (Select All Best puts keep_paths into the removal set), 350-355 (the 'Confirmation flow — 3 spatially separated steps' comment has no code under it), 368-425 (executePrune has no confirm). The server has no check either (routes_tools.py:952-991, pruner.py:664+). Keeper rows render a green checkmark (r7_both_3_review.png), which reads as 'keep'.
- **Fix:** Remove or relabel 'Select All Best Copies', or make it 'Keep best copies' (= select lower). Disable checkboxes on KEEP rows unless a keeper is explicitly swapped. In api_prune_stage, validate against the CSV groups: reject any path not REVIEW_REMOVE, and reject when a group would have zero remaining on-disk members. Add a typed confirm for permanent delete and a summary confirm modal for any prune. Render keeper checkboxes in a neutral or danger colour.

#### [HIGH · demonstrated] A loaded or stale report with a missing keeper still auto-selects and prunes the only remaining copy

- **ID:** `dupb-stale-report-last-copy` · **Area:** chop-shop-tool · **Tool/surface:** Prune (Load an existing report)
- **Expected:** Before moving a duplicate, verify its keeper exists on disk (and ideally that it still hashes or fingerprints the same). Groups whose keeper is missing are blocked or re-ranked so that an existing file is kept.
- **Actual:** The last copy of a track is moved to the Trash, or deleted outright if 'Delete permanently' is ticked.
- **Evidence:** r11_stale: CSV group with KEEP=Techno/Vera Lux - Pressure.aiff (file no longer exists) and REVIEW_REMOVE=Techno/Vera Lux - Pressure.flac (exists). Loaded via 'Load an existing report'. The keeper row showed 'missing', yet the flac was auto-checked, 'Move to Trash' gave 'Moved ✓ Vera Lux - Pressure.flac', and `ls Techno/` afterwards had no Pressure file at all (r11_stale_3_review.png, r11_stale_4_pruned.png). Code: pruner.py:347-351 re-ranks by format tier first, so a missing AIFF (size 0) still outranks an existing FLAC. Neither trash_rescue_preflight (pruner.py:51-140) nor prune_files checks that the keeper exists.
- **Fix:** In load_report, rank exists_on_disk first in the sort key. In prune_files, skip any path whose keeper_map target does not exist, and log it as 'keeper missing — skipped'. Mark such groups in the UI and exclude them from Select All Lower.

#### [MEDIUM · demonstrated] The pre-prune savepoint exists but nothing ties it to the run: no timeline entry, no operations entry, ambiguous names

- **ID:** `dupb-no-linked-revert-marker` · **Area:** undo-revert · **Tool/surface:** Prune
- **Expected:** Each prune records a job (timeline) holding its savepoint path, trash folder, FableGear snapshot and counts, so the user sees 'Revert to before Prune at 18:54'.
- **Actual:** The user has to guess which of several identical-looking savepoints to restore, and the restore UI is broken anyway.
- **Evidence:** After each prune, a new Archive/Savepoints/master.backup_<ts>.db appears (e.g. master.backup_20261005_185418_171658.db). GET /api/undo/timeline stays [] (job_dispatcher history is only fed by MCP dispatch, job_dispatcher.py:99/554). /api/undo/operations stays {sessions: []} although fg_processing_log holds 'prune' rows (routes_undo.py:317-321, 352-358 exclude prune). /api/undo/database/history stays []. By the end of the session /api/undo/savepoints listed 9+ entries, several with the same display_time ('Oct 05, 2026 06:54 PM' x3: pre-prune, pre-restore, pre-restore), with no tool name and no indication of whether the copy is of DEVICE_DB or LOCAL_DB (db_connection.py:176-178 names every copy 'master.backup_<ts>.db').
- **Fix:** Have the prune worker create a job record (reuse job_dispatcher's SQLite history, or a prune manifest) holding savepoint_path (returned by _backup_db; expose it from write_db), trash_dir, fg snapshot and summary. Include the tool and source DB in the backup filename (master.backup_<ts>__prune__device.db). Add prune sessions to /api/undo/operations with a 'Revert' that does savepoint + files + FableGear together.

#### [MEDIUM · demonstrated] A crash during the file phase leaves Rekordbox rows already deleted for files still on disk; nothing is resumable or recorded

- **ID:** `dupb-kill-desync` · **Area:** session-resume · **Tool/surface:** Prune
- **Expected:** Interleave DB delete and file move per path (or use a two-phase log), and leave a durable 'prune in progress' record the app offers to finish or roll back on restart.
- **Actual:** Partially applied batches are silent. The doomed file becomes an untracked orphan on disk, outside Rekordbox.
- **Evidence:** r6b: staged 4001 paths (4000 tiny files, then DB-backed Techno/Vera Lux - Pressure.flac last) via the UI's endpoints, then `kill -9 <server pid>` after about 300 'Moved' lines. Result: 303 files in ~/.Trash/FableGear_Pruned_20261005_191052. SSE showed 'DB ✓ Vera Lux - Pressure.flac' and 'Database commit OK (1 row(s) removed)'. Afterwards the DEVICE_DB row for 1035 does not exist, while 'Techno/Vera Lux - Pressure.flac' is still on disk. After restart, /api/undo/timeline is [], /api/checkpoint/check rejects tool=prune, and the UI shows no banner (r5_after_restart_0_open.png). Only per-file journal rows and no prune_batch row exist. Code: pruner.py:821-823 commits all DB deletes before the file loop at 868-895.
- **Fix:** Write a prune manifest (paths, keeper map, savepoint, trash dir, per-path state) before starting and update it per path. On startup, detect an unfinished manifest and offer 'finish' or 'roll back (savepoint + move back)'. Alternatively, move the file first (to trash), then delete the DB row in a per-path savepoint, so a crash never leaves a deleted row with its file still present.

#### [MEDIUM · demonstrated] Interrupt during the file phase is ignored, and the UI stops listening while the prune silently finishes

- **ID:** `dupb-interrupt-filephase-misleading` · **Area:** chop-shop-tool · **Tool/surface:** Prune
- **Expected:** Either stop at the next file and report how many moved, or clearly say 'cannot stop now, N files remaining' and keep streaming until done. Cancel should target only the job the user is looking at.
- **Actual:** The user believes the prune was interrupted, but it completes in the background with no summary.
- **Evidence:** r4_interrupt_files: 4000-file prune; Interrupt clicked during 'Moving files…'. UI log: 'Interrupt sent to subprocesses. Prune will stop at the next safe checkpoint.' The SSE then stopped at 'Moved ✓ bulk_0412.mp3', and the status line stayed empty (out/r4_interrupt_files_log.txt). The server meanwhile moved all 4000 (fg_processing_log prune_batch id 4011: files_moved 4000). Code: pruner.py:843-844 (cancel after commit is ignored by design), static/shared/audit.js:45-73 (_forceResetRunState after 5 s closes the EventSource). /api/cancel also SIGTERMs every managed subprocess of any tool (routes_tools.py:1247-1273, no tool filter); the message 'Interrupt sent to subprocesses' shows something else was killed too.
- **Fix:** Honour should_cancel in the file loop. The per-file move is atomic, and the DB is already committed for all paths, so pair this with the per-path ordering fix. Keep the SSE open until 'done' for in-process jobs and skip the 5 s force-reset when prune_running is true. Scope /api/cancel by job id or tool.

#### [MEDIUM · demonstrated] The interactive prune writes no report on success, cancel or failure, yet the UI says 'Check the report for details'

- **ID:** `dupb-no-prune-report` · **Area:** chop-shop-tool · **Tool/surface:** Prune
- **Expected:** Archive/Reports/Prune/prune_<ts>.txt (or Logs/Prune) listing every path with its outcome (moved/skipped/DB error), keeper, playlists re-threaded, trash folder and savepoint. It should be written on success, cancel and failure, and linked from the status line.
- **Actual:** The only record is the transient terminal panel; the per-file journal is invisible in the UI.
- **Evidence:** After 8 UI prunes, `find 'FableGear Archive/Logs' 'FableGear Archive/Reports' -type f` lists only Reports/Audit/* and Reports/Duplicates/*. There is no Reports/Prune, and Logs/Prune is empty. The UI says '✓ Prune complete — 4 files moved to Trash. Check the report for details.' with no report modal (ui_run: 'REPORT MODAL after prune: None'). Code: routes_tools.py:1139-1219 never calls _emit_report or _write_report. The CLI prune does write Reports/Prune/prune_<ts>.txt (cli.py:1231).
- **Fix:** Collect the emitted lines and summary in the worker and write them via cli._write_report('Prune', …) in a finally block, including cancelled and failed states. Emit FABLEGEAR_REPORT_BEGIN/END/PATH so runCommand opens the report modal like the other tools.

#### [MEDIUM · demonstrated] Deep scan with fpcalc missing reports 'No duplicates found… unique' and exits successfully

- **ID:** `dupb-deep-false-allclear` · **Area:** chop-shop-tool · **Tool/surface:** Find Duplicates (Deep)
- **Expected:** Fail fast with 'fpcalc (chromaprint) not found — Deep scan unavailable' before scanning. Separately, when failures > 0, the report must say 'N of M files could not be analysed' rather than claiming every file is unique.
- **Actual:** A false all-clear is shown to a user whose duplicates were never compared.
- **Evidence:** r0_deep (match=all, 15 files) and r12 (3706 files): the log shows 'ERROR fpcalc not found — install chromaprint' for every file, then 'Fingerprint pass complete — 0 unique prints, 15 failures', then the report 'No duplicates found. Every file in this folder appears to be unique.' and '✓ Finished successfully' (r0_deep_2_scan_done.png, Reports/Duplicates/duplicates_20261005_184904.txt). Code: duplicate_detector.py:704-706 returns None per file; cli.py:879-883 emits the all-clear whenever groups are empty, regardless of failures.
- **Fix:** In cmd_duplicates (deep), check _ensure_acoustid_fpcalc() up front, exit 1 with a clear message, and have the UI show an install hint. Carry the failed count in ScanResult and print it in the report; make the exit code non-zero when every file failed.

#### [MEDIUM · demonstrated] Quick scan only sees files already in the FableGear DB, so un-synced exact copies are reported as 'unique'

- **ID:** `dupb-quick-stale-index` · **Area:** chop-shop-tool · **Tool/surface:** Find Duplicates (Quick)
- **Expected:** Quick mode walks the selected folders, hashes or size-matches any files missing from the index (or at least counts them), and reports 'N files not indexed — run Sync first'.
- **Actual:** Files outside the index are silently ignored, and the user gets an authoritative-sounding false negative.
- **Evidence:** r14_quick_stale: folder Music Library/zz_bulk with 3695 byte-identical files (sha256 sample: 100x 9675d32cba660715), not yet synced to fablegear.db. Quick scan → 'No duplicates found. Every file in this folder appears to be unique.' (r14_quick_stale_2_scan_done.png). Code: duplicate_detector.py:561-666 uses archive.find_duplicates_by_hash() over fg_content only, without walking the folder. The card text says 'from cached hashes', but the report text claims the whole folder was checked.
- **Fix:** Walk the roots in scan_duplicates_hash, compare against fg_content paths, and either hash the unindexed files (size-bucket first) or print FABLEGEAR warnings plus a report line with the unindexed count.

#### [MEDIUM · demonstrated] Duplicates resume banner: localStorage-only, shown for failures, no starting point, and Resume switches Deep to Quick

- **ID:** `dupb-resume-banner-wrong` · **Area:** session-resume · **Tool/surface:** Find Duplicates
- **Expected:** The banner comes from the server checkpoint (/api/checkpoint/check). It shows started-at, mode/match/threshold, done/remaining, and only appears for genuinely interrupted runs. Resume reproduces the exact config with checkpoint_action=resume.
- **Actual:** A misleading banner, and Resume launches a different scan tier from scratch.
- **Evidence:** r10_deep_fail: Deep/fuzzy scan with a nonexistent folder exits 1, leaving localStorage rb_ckpt_duplicates={paths, scanMode:'deep', matchMode:'fuzzy'}. r10b reload shows 'Interrupted run — just now | <paths> | Resume | Start Fresh' (r10b_resume_0b_banner.png); the Resume tooltip says 'files already done are skipped' although nothing ran. Clicking Resume issued '/api/run/duplicates?path=…&path=…&scan_mode=quick', with deep radio checked = false, and the saved ckpt was overwritten with scanMode 'quick' and matchMode 'exact'. /api/checkpoint/check → {exists:false}. Code: pipeline.js:583-631 (banner content: age plus paths only), 698-707 (_resumeDuplicates omits scanMode and fuzzy threshold), runners.js:342-349 (ckpt saved at start, cleared only on exit 0). /api/checkpoint/check is never called from static/.
- **Fix:** Call /api/checkpoint/check on card open with the stored config and render saved_at, completed/total and config. Restore scan_mode (click the radio) and fuzzy threshold in _resumeDuplicates, and pass checkpoint_action=resume. Clear the localStorage marker on non-interrupt failures (exit 1), or label it 'Last run failed'.

#### [MEDIUM · demonstrated] Interrupting a Deep scan saves no checkpoint and no partial report (SIGTERM is unhandled; cancel_event never wired)

- **ID:** `dupb-scan-cancel-no-checkpoint` · **Area:** session-resume · **Tool/surface:** Find Duplicates (Deep)
- **Expected:** SIGTERM sets a cancel event, so the scan saves its checkpoint and writes a partial report (exact groups found so far), as the module docstring in checkpoint.py describes.
- **Actual:** All fingerprint work since the last 250-file boundary is lost, with no partial results.
- **Evidence:** r12b: Interrupt 1.1 s into a 3706-file deep/all scan → '✗ Exited with code -15'. ~/.fablegear/checkpoints/duplicates is empty, /api/checkpoint/check gives exists:false, and no report is written. kill -9 of the server mid-scan (r13): the CLI child is reparented to pid 1 and exits within 4 s with no output files; runtime/active_subprocesses.json keeps the dead pid after restart. Code: cli.py has no signal handler, and cmd_duplicates calls scan_duplicates without cancel_event (cli.py:845-851). The 'cancelled → save checkpoint + partial groups' branch (duplicate_detector.py:1300-1310) is unreachable from the UI. Checkpoints are saved only every 250 files (:84).
- **Fix:** In cmd_duplicates, install signal.signal(SIGTERM/SIGINT) handlers that set a threading.Event, pass it as cancel_event, and on cancel write the partial CSV with a 'PARTIAL' marker and exit 130. Make /api/cancel wait briefly for a graceful exit before escalating.

#### [MEDIUM · demonstrated] Checkpoint resume slices a work list whose order changes per process, so it skips arbitrary files; failures count as done

- **ID:** `dupb-resume-order-nondeterministic` · **Area:** session-resume · **Tool/surface:** Find Duplicates (Deep, Tags/All or with scan index)
- **Expected:** Resume skips exactly the files already fingerprinted, retries failures, and detects changed inputs.
- **Actual:** After a restart, a resumed scan re-fingerprints some files and permanently skips others, silently missing duplicates.
- **Evidence:** Root cause shown live: `PYTHONHASHSEED=1/2/3 python order_probe.py` (calls duplicate_detector._candidate_pairs(tag_match=True) on the sandbox library) printed three different orders, starting 'Marlo…', 'Nula…' and '01 - Nula…'. Code: duplicate_detector.py:447 `result = list(candidates) + extra` (set iteration order), 1132 `files = files[completed:]` on resume. completed += 1 also counts fingerprint failures (1231-1239, 1281-1294); r12's log shows 'Checkpoint saved (3000 / 3706 files)' with an empty fp_map. The checkpoint key (checkpoint.py:58-63) covers roots and config but not the file list, so files added or removed between sessions are not detected. End-to-end resume could not be run (fpcalc absent).
- **Fix:** Persist the set of processed paths (or rely on the archive fingerprint cache, which already reuses known prints) instead of a count. Sort the candidate list deterministically. Store a hash of the sorted file list (path+size+mtime) in the checkpoint and warn or reset on mismatch. Do not count failures as completed.

#### [MEDIUM · demonstrated] Prune updates only DEVICE_DB; LOCAL_DB rows keep pointing at trashed files, and savepoint restore always targets DEVICE_DB

- **ID:** `dupb-local-db-ignored` · **Area:** boundary · **Tool/surface:** Prune
- **Expected:** The run states which Rekordbox DB it edits. It either updates every configured library that references the file, or warns that the other library will show missing files. A savepoint restore targets the DB the backup came from.
- **Actual:** A user whose Rekordbox runs from the local library gets missing-file entries for every pruned track, and the restore endpoint can cross-restore databases.
- **Evidence:** In every prune diff (before_prune→after_prune, r2_before→r2_after), local_db.content, playlist_songs and cues are 'identical' while the four files moved to the Trash. The sandbox LOCAL_DB (~/Library/Pioneer/rekordbox/master.db) still lists House/Nula - Night Drive.mp3 and the others. Code: routes_tools.py:1175 picks DEVICE_DB if it exists, else LOCAL_DB. The prune log says 'Removing from RekordBox database…' without naming which. routes_undo.py:131-156 always restores over DEVICE_DB, while backups from either DB share the name master.backup_<ts>.db (db_connection.py:176-178). Restoring a LOCAL_DB backup would therefore overwrite DEVICE_DB (inferred).
- **Fix:** Print the target DB path in the prune log. Either prune both DBs (each in its own write_db with its own savepoint) or block and warn when LOCAL_DB also references the doomed paths. Encode the source in savepoint filenames or a sidecar JSON and restore to that source.

#### [MEDIUM · inferred] Recovery folder is ~/.Trash on the boot volume, so pruning a DJ drive copies every file across volumes after the DB is already committed

- **ID:** `dupb-trash-cross-volume` · **Area:** chop-shop-tool · **Tool/surface:** Prune
- **Expected:** Duplicates on an external volume are moved to that volume's trash (/Volumes/X/.Trashes/<uid>) or to FableGear Archive/Quarantine on the same drive (fast rename, no extra space), with a free-space precheck.
- **Actual:** Large prunes may fill the internal disk and leave a partially applied, desynced state.
- **Evidence:** pruner.py:719 sets trash_dir = Path.home()/'.Trash'/f'FableGear_Pruned_{stamp}'; :888 calls shutil.move, which copies and deletes when crossing filesystems (/Volumes/DJDRIVE → internal disk). The DB commit at :822 happens before the moves, so ENOSPC or an I/O error midway produces 'Move ✗' for the remaining files, whose Rekordbox rows are already gone (the same desync shown live in dupb-kill-desync). The stamp has second resolution (:714), so two prunes in the same second share a folder. All sandbox paths were on one filesystem, so this was not exercised.
- **Fix:** Use Archive/Quarantine/Pruned_<ts> on the source volume (QUARANTINE_DIR already exists), or NSFileManager trashItem via pyobjc on macOS. Precheck free space, and include microseconds in the folder stamp.

#### [LOW · demonstrated] Review list: filename-only rows, stale list after a prune, CSV-supplied names trusted, ambiguous colours

- **ID:** `dupb-review-ui-ambiguity` · **Area:** chop-shop-tool · **Tool/surface:** Prune review UI
- **Expected:** Show the parent folder or relative path. Refresh groups from the server after a prune. Derive names from file_path.
- **Actual:** The user can misidentify which copy is kept, and sees stale state after a prune.
- **Evidence:** r1_quick_3_review.png: three rows all reading 'Nula - Night Drive (copy).mp3' (Quiet/House/Loud) are indistinguishable without hover (dedupe.js:183 shows entry.filename; the path is only in a title attribute). After a prune the pruned rows stay rendered with red checks, while the summary says 'Select files above to continue' (r2_prune_4_pruned.png; dedupe.js:414-418 never reloads). The filename column comes from the CSV, not the path (pruner.py:329): my probe CSV showed 'Nula - Night Drive.mp3' for a row whose path was zz_cancel_probe.mp3. The 'recoverable' stat shows '1 MB' for 0.5 MB. The scan bar under the card reads 'COMPLETE 0% complete 0 DONE' after a successful quick scan.
- **Fix:** Render a relative path (strip the music root) under each filename. Call _autoLoadDupeResults(pruneCsvPath) after a successful prune. Ignore the CSV filename column. Format MB with one decimal under 10 MB.

#### [LOW · demonstrated] The 'Move to Trash →' / 'Delete Permanently →' button label is near-invisible (white on #efefef)

- **ID:** `dupb-prune-button-unreadable` · **Area:** ui-layering · **Tool/surface:** Prune action bar
- **Expected:** A readable, clearly destructive button style (danger colour), stronger still in permanent mode.
- **Actual:** The primary destructive control's label is unreadable and does not look destructive.
- **Evidence:** layout_probe.py at 1440x900 and 1024x700: #btn-prune-start has computed color rgb(255,255,255) on background rgb(239,239,239), opacity 0.88 (contrast about 1.1:1). Screens layout_1024x700_actionbar.png, r7_both_3_review.png. There is no .btn-prune-start rule in static/fablegear.css (only .prune-action-bar at :1745). The button is not occluded: elementFromPoint hits it at both viewports. At 1024 wide its right edge sits exactly at the viewport edge (rect 916+108).
- **Fix:** Add a .btn-prune-start style (e.g. var(--danger) border and text, filled red when the permanent checkbox is on), and style the 'Delete permanently' label as a danger toggle.

#### [LOW · demonstrated] 'Load an existing report' defaults to a filename the scanner never writes, and there is no picker for past reports

- **ID:** `dupb-default-report-path` · **Area:** chop-shop-tool · **Tool/surface:** Find Duplicates / Prune
- **Expected:** List the latest reports in Reports/Duplicates and let the user pick one; default to the newest.
- **Actual:** Re-review after a reload means typing a full timestamped path.
- **Evidence:** GET /api/duplicates/load?page=0&per_page=5 (empty csv_path) → {"error":"Report not found: …/Reports/Duplicates/duplicate_report.csv"}. The scanner writes duplicate_report_<ts>.csv (cli.py:803-811). The placeholder shows a legacy '~/rekordbox-toolkit/duplicate_report.csv' (duplicate_prune.html:126). Code: routes_tools.py:99-110.
- **Fix:** Add GET /api/duplicates/reports (glob duplicate_report_*.csv, newest first) and a select element. Default _resolve_duplicates_report_path('') to the newest file.

#### [LOW · demonstrated] 'Read-Only Scan' badge, although the scan writes the FableGear DB and a dotfile into the music root

- **ID:** `dupb-readonly-badge-writes` · **Area:** boundary · **Tool/surface:** Find Duplicates
- **Expected:** The badge describes the side effects ('Reads audio; updates FableGear index'). Nothing is written into the user's music folder.
- **Actual:** Small but undisclosed writes, including into the library root.
- **Evidence:** diff after scans: '+ Volumes/DJDRIVE/Music Library/.fablegear_state.json' (mark_step_complete, helpers.py _stream → mark_step_complete(library_root,…)), and fg_processing_log '+ [.., "duplicate_scan", …]'. Deep mode also persists fingerprints (duplicate_detector.py:1405-1411). Badge text: duplicate_prune.html:9.
- **Fix:** Store step state under ~/.fablegear or the Archive instead of the music root, and adjust the badge copy.

#### [LOW · inferred] Keeper ranking prefers M4A over MP3 regardless of bitrate, and larger files (e.g. embedded artwork) over higher bitrate

- **ID:** `dupb-ranking-lossy` · **Area:** chop-shop-tool · **Tool/surface:** Find Duplicates / Prune ranking
- **Expected:** Rank lossy files by actual audio bitrate or duration-normalised audio size (mutagen info.bitrate), not container tier or file size.
- **Actual:** The wrong keeper is possible for cross-codec lossy pairs.
- **Evidence:** pruner.py:146-153 FORMAT_TIER puts .m4a=3 above .mp3=2; dedupe_sort_key (pruner.py:265-288) sorts by (tier, size_mb, RARP, tags). A 96 kbps AAC therefore beats a 320 kbps MP3, and within a format a low-bitrate file with a large cover image beats a clean high-bitrate one. Deep-scan pairs could not be exercised (no fpcalc). Live Quick-scan ranking was consistent with this key (PN-named copy kept over a RAW copy of identical bytes).
- **Fix:** Add audio bitrate (mutagen .info.bitrate) as the key after the lossless/lossy split, with size as a tiebreak only. Keep detector and pruner on the shared dedupe_sort_key.

#### [LOW · demonstrated] /api/prune/stage accepts arbitrary paths (not limited to the report or the library) and permanent=true

- **ID:** `dupb-stage-no-validation` · **Area:** chop-shop-tool · **Tool/surface:** POST /api/prune/stage
- **Expected:** Staged paths must be REVIEW_REMOVE entries of the referenced report and lie under the configured roots.
- **Actual:** Any file the server can reach can be trashed or deleted through this endpoint pair.
- **Evidence:** I staged 20,001 paths, mostly '/nonexistent/dupe_*.mp3' plus files outside the report, and they were accepted and processed (r3_cancel_db; 'DB — dupe_00379.mp3 (not in database — file only)'). Code: routes_tools.py:952-991 only checks isinstance(list). The keeper_map is built from the CSV, but paths are never checked against it, and no path_guard is applied. Exploitability is limited: there are no CORS headers, so a cross-site page cannot read the token.
- **Fix:** Intersect paths with cached['remove_paths'] (or keep plus remove when explicitly overridden) and run path_guard on each. Reject otherwise.

#### [INFO · demonstrated] Confirmed good: Interrupt during the prune DB phase rolls back cleanly (minor leftovers)

- **ID:** `dupb-cancel-dbphase-good` · **Area:** chop-shop-tool · **Tool/surface:** Prune
- **Expected:** Rollback with no orphan artefacts.
- **Actual:** Rollback works; one empty trash folder and one unneeded savepoint are left behind.
- **Evidence:** r3_cancel_db (20,001 staged paths, first one real: zz_cancel_probe.mp3): Interrupt at 1.17 s → '⚠ Cancel requested — rolling back pending database changes.', 'Prune cancelled before commit. No files were moved.', exit 130, UI '⚠ Prune cancelled. Re-open the report and re-stage…'. diff r3b_before r3b_after_cancel shows no music or DB changes. Leftovers: an empty ~/.Trash/FableGear_Pruned_20261005_185955 (listed by /api/undo/trash as file_count 0) and an extra savepoint master.backup_20261005_185955_028692.db.
- **Fix:** Create the trash folder lazily on the first move, and delete it on cancel or when empty.

#### [INFO · demonstrated] Confirmed good: with the keeper in Rekordbox, playlist slots and cues re-thread correctly, and the savepoint API restores DB rows exactly (repeatable)

- **ID:** `dupb-rethread-and-savepoint-good` · **Area:** undo-revert · **Tool/surface:** Prune / POST /api/undo/savepoint/restore
- **Expected:** Same.
- **Actual:** Works as designed when the keeper is in the DB and all association tables exist.
- **Evidence:** Round 2 (realistic schema): slot 888002 moved 1005→1009 (keeper House/02 - Glitterball.m4a) and 888003 moved 1015→1025 (keeper Quiet/Nula (copy)); cues 900003-900006 moved to the keepers; summary 'Playlist slots re-threaded: 2, Other associations re-threaded: 4'. POST /api/undo/savepoint/restore (master.backup_20261005_185418_171658.db) → diff r2_before r2_undo1 shows device_db content, playlist_songs and cues identical. A second identical restore changed nothing except one more pre-restore backup (diff r2_undo1 r2_undo2). The restore handles -wal/-shm sidecars (routes_undo.py:158-186).
- **Fix:** Keep this behaviour. Extend the same re-threading to the FableGear DB (see dupb-fg-playlists-cascade-lost), and expose this restore in the UI.

#### [INFO · inferred] A safer Rekordbox dedupe (dry-run, manifest-linked backup, --undo) exists but only in the CLI; the Chop Shop prune does not reuse its safety envelope

- **ID:** `dupb-smart-dedup-unwired` · **Area:** other · **Tool/surface:** smart_dedup.py / cli.py smart-dedup
- **Expected:** One shared, manifest-based write envelope for every Rekordbox-mutating tool.
- **Actual:** The Chop Shop prune uses the bare write_db backup with no manifest or undo link.
- **Evidence:** cli.py:1492-1579: a dry run by default; --write runs inside rekordbox_safe_write.safe_master_write with a per-run manifest in ~/.fablegear/rekordbox_dedup_manifests; --undo restores exactly that run's backup. smart_dedup.py:1-33 explains how it re-wires memberships and verifies before deleting. grep finds no route, template or JS reference to smart-dedup; /api/run/rekordbox-dedupe (routes_rekordbox.py:254) is a different command.
- **Fix:** Run prune_files inside safe_master_write(tag='prune') and reuse its manifest for an 'Undo last prune' action. That covers most of dupb-no-linked-revert-marker.

#### [INFO · demonstrated] Observed in passing (other owners): audit report written on every UI open; db=device playlist tracks read LOCAL_DB; Undo panel clipped

- **ID:** `dupb-passing-observations` · **Area:** other · **Tool/surface:** n/a
- **Expected:** n/a — handed off to the Record Room, layering and audit owners.
- **Actual:** See evidence.
- **Evidence:** (a) This session's UI opens created 20 'FableGear Archive/Reports/Audit/audit_<ts>.txt' files, one per page load. (b) GET /api/library/playlists?db=device lists 'Peak Time' (id 777001), but GET /api/library/playlists/777001/tracks?db=device → {"error":"Playlist not found"}, because the route imports LOCAL_DB for both sources (routes_player.py:1206-1207; the same pattern appears in the create/add routes at 1145 and 1231). (c) At 1440x900 the Undo Wizard panel renders mispositioned and clipped: tab labels cut ('TIONS'), body text cut ('re moved to Trash…'), as in r2_undo_trash.png.
- **Fix:** Route to the owning auditors: (a) do not write an audit report on passive page load, or dedupe them; (b) use _resolve_db(source) in the playlist track and write routes; (c) fix the undo panel width and positioning.

## Chop Shop: Rename

_Auditor: `tool-rename`_

Rename Files audit (round 3, sandbox rename-r3 on port 8113, configured; round-2 evidence reused and cited). Live, through the real UI: a dry run on the music root, a live multi-folder run (Edits/Disco/Techno/House), Undo Wizard → Operations → Return files, Probe Ambiguities, cancel mid-run, relaunch + resume from the banner, and kill -9 of the real server PID + relaunch + resume. Through the API: the second revert, the learned-rule manual/quarantine actions on /api/rename/preflight/apply, and a root-level live run.

What works: renames are byte-identical (sha256 unchanged). Collisions are numbered "(2)". A savepoint of the local master.db is taken before mutating. Rekordbox local rows are relinked one commit per file (pyrekordbox update_content_path has commit=True by default), so cancel and kill -9 left Rekordbox and FableGear consistent in round 3. The journal-based revert returns every file byte-identical (9 of 9, and 300 of 300), and running it twice is safe. After a relaunch, a resume banner does appear and resume finishes the job.

Main problems:
(1) Revert never touches Rekordbox. After "Returned 9 files.", 9 local master.db rows point at missing files (300 at scale), and later runs then silently relink 0 rows.
(2) The device DB on the drive is never relinked: 3 of 4 rows broke after the run.
(3) Learned manual-rename targets are not validated, so "../../X" or an absolute path moves a file outside the library. The log shows only the basename, and the rules persist and fire again on later runs.
(4) Preflight quarantine moves files right away, with no journal row and no Rekordbox update.
(5) Undo sessions are grouped by a 15-minute gap, not by job: four separate runs merged into one undoable lump.
(6) The dry run shows counts only.
(7) Cancel and crash write no report, and the resume banner has no done/remaining count, no restore point and no config.

Overall: the forward rename is solid, but rename and revert together are not safe for a Rekordbox library.

**Coverage**

- LIVE (round 3, sandbox sbx-rename-r3, port 8113): BEFORE/AFTER snapshots (sha256 of every file under Volumes and Incoming, ~/.fablegear, ~/Library/Pioneer, local and device Rekordbox rows decrypted, fg_content and fg_processing_log, renamer_learned.json, /api/undo/{timeline,operations,savepoints,trash,database/history}, /api/checkpoint/check). Scripts: /tmp/claude-0/-home-user-FableGear/9074577f-2e6a-5e16-bdce-ef5b26f1b16b/scratchpad/audit/tool-rename/r3/{snap.py,diff.py,consist.py,ui_rename.py,ui_undo.py,sse.sh}. Outputs: .../r3/out/*.json, *_log.txt, *_sse.json, *.sse
- LIVE via the real UI (Playwright + Chromium): dry run on the music root (d1_dry), live 4-folder run (l1_live_multi), Undo Wizard Operations tab: Preview undo, then Return files (u1), Probe Ambiguities (p1_probe), cancel mid-run (c1_cancel; no visible #scan-bar-interrupt in the headless viewport, so the driver sent POST /api/cancel, which is what the interrupt button calls, audit.js:50), relaunch + banner observation (s1_observe), resume from the banner (k1), kill -9 of the real server mid-run (k2_kill9), then relaunch + resume (k3_resume). Screenshots: /home/user/FableGear/docs/audits/2026-10-05/screens/tool-rename/r3/
- LIVE via the API: second revert (/api/undo/operations/preview + revert), preflight/apply with manual traversal targets and an out-of-root quarantine, live run on the music root (/api/run/rename?path=<Music Library>&no_dry_run=1). The root run stands in for the single-folder path that E4 makes a no-op in the UI; the Pipeline rename step builds the same CLI command (routes_tools.py:469-480).
- REUSED from round 2 (sandbox sbx-rename-r, port 7113; evidence in /tmp/claude-0/-home-user-FableGear/9074577f-2e6a-5e16-bdce-ef5b26f1b16b/scratchpad/audit/tool-rename/out/): chained rename A->B->C revert (before_chain/after_chain_rev1/after_chain_rev2); cancel during quarantine leaving one orphan (before_cancel/after_cancel, c1_cancel_log.txt); preflight quarantine of a Rekordbox-tracked file (after_pfq); missing rekordbox/share causing rollback of every rename (r3_live_multi_log.txt / r3_live_multi_sse.json); 'AC/DC' target erroring (trav.sse)
- Fixture note: the template has no ~/Library/Pioneer/rekordbox/share directory, which makes every pyrekordbox update_content_path call raise ENOENT. In round 3 I created it (a real Rekordbox install has it) before the runs. The ENOENT rollback behaviour comes from round 2.
- Harness note: restart.sh writes the PID of a `cd && nohup python` subshell into server.pid, so my first kill -9 (k1) hit only that wrapper and the run finished normally. k1 is therefore treated as a clean 'resume after cancel + relaunch'. The real kill -9 test (k2) used the actual Flask PID (7816, found via /proc/<pid>/cwd).
- NOT TESTED (and why): case-insensitive APFS case-only renames (the sandbox filesystem is case-sensitive; inferred from code); NFD/NFC filename mismatches against Rekordbox FolderPath (speculative); ANLZ PPTH rewrite by update_content_path and whether revert restores it (the fixture has no ANLZ files; inferred); the Rekordbox-running gate _require_rb_closed (no Rekordbox process on Linux; inferred); the Pipeline Wizard rename step through the UI (its confirm gate is hidden, E4); restoring the rename savepoint (E10: the restore targets the device DB, so I did not run it); fpcalc is not installed but is not used by Rename.
- Server stopped by PID (10330) at the end. No product code was edited.

**Per-tool safety matrix**

### Rename Files (/api/run/rename -> cli.py rename; UI #step-rename)

| Check | Result |
|---|---|
| R — Report | PARTIAL. On success: Archive/Reports/Rename/rename_<ts>.txt, with the path shown in the report modal. Counts only, no per-file old->new list. On cancel (exit -15) and kill -9: no report and no rename_batch journal row; the UI shows only '✗ Exited with code -15' or 'Connection error'. When rollbacks happen (missing ANLZ share), the run still ends '✓ Finished successfully' with '9 files had errors' (round 2). |
| M — Revert marker | PARTIAL. A local master.db savepoint is written before the first mutation (Savepoints/master.backup_<ts>.db), and every file move gets a fg_processing_log 'rename' row (from/to). However: the savepoint is not linked to the job, operations session, report or banner; it covers the local DB only (the device DB is neither written nor backed up); there is no entry in /api/undo/timeline (E1); and savepoints accumulate with no labels (5 after this audit). |
| U — Undo | FILES YES, DB NO. Undo Wizard -> Operations -> Return files restored 9/9 (and 300/300) files byte-identical, and a second revert is a no-op (0 reverted, 9 blocked). Rekordbox local rows are NOT reverted (9, then 300 rows left pointing at missing files). Leftovers: an orphan _quarantine_manifest.json, and fg_content status stays 'relinked'. Sessions are grouped by a 15-minute gap, not by job, so separate runs merge; a chain A->B->C needs two reverts. Learned rules and preflight quarantines are not undoable. |
| C — Cancel / interrupt | DATA CONSISTENT, NO REPORT. POST /api/cancel at about 90 files and kill -9 at about 60 files: Rekordbox and FableGear stayed consistent (per-file commits), and no cli.py was orphaned. The checkpoint lags (saved every 25: 75 of 90, 50 of 60, total=0). Round 2 had one quarantined file moved but neither relinked nor journaled (race between the move and the DB update). |
| S — Resume after restart | OFFERED, THIN. After relaunch the #step-rename banner shows 'Interrupted run — Nm ago', the roots, and Resume/Start Fresh. It comes from localStorage rb_ckpt_rename (E5); the server checkpoint also exists (completed=75, total=0). There is no done/remaining count, no live/dry flag, no restore point and no start time beyond 'Nm ago'. Resume re-walks every file (done_paths hold original names), silently picks up files added between sessions, and the final report counts files renamed before the interruption as 'already had clean names'. It finished correctly, with Rekordbox consistent. |
| D — Dry run / preview | COUNTS ONLY. The Dry Run checkbox is on by default, and the dry-run report lists only counts (8 renamed / 5 clean / 1 clash / 1 quarantined), with no per-file mapping and no quarantine destination. The Probe Ambiguities and preflight modal are hidden (E4). |
| B — Boundary | CROSSES INTO THE DB LAYER. This file tool writes Rekordbox local master.db (FolderPath/FileNameL, plus ANLZ PPTH through pyrekordbox) and the FableGear archive (fg_content relink + journal). The savepoint covers the local DB, but the undo path does not revert it. Device DB rows pointing at the same drive files are left broken and are not backed up. |

_Evidence:_ r3/out: before.json, d1_dry_log.txt, l1_live_multi_log.txt, after_l1.json, after_u1.json, after_u2.json, before_t.json, after_t.json, after_tu.json, before_c.json, c1_cancel_log.txt, after_c1.json, k1_resume_kill_log.txt, after_k1.json, after_k1u.json, before_k2.json, k2_kill9_log.txt, after_k2.json, k3_resume_log.txt, after_k3.json, root.sse, after_root.json; screens/tool-rename/r3/*.png

### Rename learned rules (/api/rename/preflight/apply; ~/.fablegear/renamer_learned.json)

| Check | Result |
|---|---|
| R — Report | NONE. Returns JSON only; the UI modal that would call it is hidden (E4). |
| M — Revert marker | NONE. 'quarantine' moves the file immediately, with no savepoint, no fg journal row, no Rekordbox update, and only a _quarantine_manifest.json entry. Manual and alias rules are written to renamer_learned.json with no backup. |
| U — Undo | NONE. Preflight quarantines don't appear in /api/undo/operations. Rules have no list or retract route (LearnedRules.retract exists but is not exposed), so a stale rule fires again on every later run. |
| C — Cancel / interrupt | N/A (synchronous request) |
| S — Resume after restart | Rules persist across restarts by design, with no expiry and no record of which run used them. |
| D — Dry run / preview | NONE. The quarantine action has no preview; manual targets show only on the next run. |
| B — Boundary | Quarantining a Rekordbox-tracked file breaks its master.db row (round 2, after_pfq: Techno/untitled track 03.mp3). The source path is not confined to the 'path' root (round 3 moved Incoming/Newcomer - Fresh Cut.mp3). |

_Evidence:_ r3/out/pf_trav.json, r3/out/pf_quar.json, r3/out/trav.sse, r3/out/root.sse, r3/out/after_t.json, r3/out/after_root.json; round 2 out/after_pfq.json

### Rename probe (/api/rename/probe; 'Probe Ambiguities' button)

| Check | Result |
|---|---|
| R — Report | Read-only JSON listing up to top_n ambiguous files with proposed filenames. |
| M — Revert marker | N/A (read-only) |
| U — Undo | N/A |
| C — Cancel / interrupt | N/A |
| S — Resume after restart | N/A |
| D — Dry run / preview | It is a preview, but it covers only the top-N ambiguous files, and its modal opens at 0x0 because #chop-card-stack is display:none (E4). |
| B — Boundary | Read-only, no DB writes. |

_Evidence:_ screens/tool-rename/r3/p1_probe_3_done.png; probe JSON for House returned 1 candidate

**Findings**

#### [HIGH · demonstrated] Rename revert moves files back but never reverts Rekordbox master.db: every relinked track becomes 'missing'

- **ID:** `tool-rename-undo-leaves-rekordbox-broken` · **Area:** undo-revert · **Tool/surface:** rename
- **Expected:** Reverting a rename restores the Rekordbox FolderPath/FileNameL (and ANLZ PPTH) that the run changed, in the same operation, so file names and the DB return together to the pre-run state.
- **Actual:** Files go back and the FableGear archive is relinked, but Rekordbox keeps the new names, so N tracks show as missing in Rekordbox. The success toast says nothing about it, and later renames can no longer relink those tracks.
- **Evidence:** Round 3. The UI run (l1_live_multi) relinked 9 local master.db rows. Undo Wizard -> Operations -> 'Return 9 files' showed the toast 'Returned 9 files.' (screens/tool-rename/r3/u1_2_preview.png, u1_3_after_return.png). Afterwards, consist.py on r3/out/after_u1.json gives: rb_local rows 15, missing-on-disk 10 (9 renamed paths + 1 fixture-broken track); fg_content missing 0; audio files byte-identical to BEFORE. At scale: reverting the 301-file Bulk2 session (API, 1.4 s) left 'rb_local rows 315 missing-on-disk 300' (r3/out/after_k1u.json). It compounds: the next run on House logged 'Final commit: 3 file(s) renamed, 0 rekordbox row(s) updated' (r3/out/trav.sse), because _update_db_path matches FolderPath exactly against the stale rows and silently returns 0. Code: routes_undo.py:468 and 574-581 (move_back = shutil.move + FableGear relink only); only op_type=='relocate' opens a Rekordbox write session and runs db_revert (routes_undo.py ~541-559, 586-597). The only DB rollback, the savepoint restore, targets the device DB (E10), and its UI tab is broken (E2).
- **Fix:** Journal the Rekordbox content IDs and the old/new paths for each rename (the metadata already has 'from'). In the rename revert, open write_db(LOCAL_DB) like the relocate branch does and call update_content_path(row, old_path) for each moved item, committing per batch. Warn when a forward run renames a file with no matching Rekordbox row. Link the pre-run savepoint to the session as a fallback.

#### [HIGH · demonstrated] Rename relinks only the local master.db; device DB rows for the same drive files are broken and not backed up

- **ID:** `tool-rename-device-db-not-relinked` · **Area:** boundary · **Tool/surface:** rename
- **Expected:** A file-layer rename on the drive updates (or at least warns about) every configured Rekordbox DB that references the renamed files, with a restore point for each DB it writes.
- **Actual:** The device library silently loses tracks: the rows point at the old filenames. The report says 'No errors.'
- **Evidence:** The sandbox device DB (Volumes/DJDRIVE/PIONEER/Master/master.db) has 4 rows referencing drive files. After the live UI run: 'rb_device rows 4 missing-on-disk 3' (Disco/Marlo & The Tens - Glitterball (Extended Mix).mp3, Disco/...Glitterball.m4a, Edits/Chain_Track_123456.mp3), from r3/out/after_l1.json. It recurred on the root run (r3/out/after_root.json). The log shows a single 'Write session opening on home/Library/Pioneer/rekordbox/master.db', and the only savepoint is of the local DB. Code: cli.py:3583-3584 `with write_db(LOCAL_DB) as db:`.
- **Fix:** Also relink matching rows in DEVICE_DB (or refuse/warn when the device DB references files under the selected roots), back up the device DB before the run, and include both DBs in the revert.

#### [HIGH · demonstrated] Learned 'exact rename' target is not validated: '../..' or an absolute path moves files outside the library, and the log hides it

- **ID:** `tool-rename-manual-target-path-escape` · **Area:** chop-shop-tool · **Tool/surface:** rename (/api/rename/preflight/apply + /api/run/rename)
- **Expected:** A manual target is a bare filename: the server strips or rejects path separators, '..' and absolute paths, confines the result to path.parent, and logs the full destination.
- **Actual:** Arbitrary relative or absolute destinations are honoured, the log shows only the basename, and DB rows get non-normalised paths.
- **Evidence:** POST /api/rename/preflight/apply with manual target '../../Escaped Kaito.wav' and an absolute target '<sandbox>/OutsideLibrary - Nula.mp3' returned ok (r3/out/pf_trav.json). The next live run on House logged 'Renamed: Kaito_-_sunrise_FINAL_v2.wav -> Escaped Kaito.wav' and 'Renamed: Nula - Night Drive.mp3 -> OutsideLibrary - Nula.mp3' (basenames only), and the report said '3 files were renamed to clean titles … No errors.' On disk the files were at Volumes/DJDRIVE/Escaped Kaito.wav (outside Music Library) and sbx-rename-r3/OutsideLibrary - Nula.mp3 (outside the drive) (r3/out/after_t.json, `ls`). In a later root run, Rekordbox and FableGear stored the literal path '.../Music Library/House/../../Escaped Kaito.wav' (r3/out/after_root.json). The revert brought both files back, but the FableGear row stayed at the un-normalised path (after_tu.json: fg_content missing-on-disk 1), because revert normalises the path (_safe_absolute_path) while the journal stored it raw. Code: the client sends the input raw (static/chop_shop/runners.js:621-630); the server stores it raw (routes_tools.py:791-799, renamer_learned.py:117-119); it is applied raw (renamer.py:1022 and 1069, `new_path = path.parent / new_name`). Round 2: a target like 'AC/DC - Night Drive.mp3' errors (trav.sse). Today this is reachable only through the API because the modal is hidden (E4); once E4 is fixed it is a free-text field.
- **Fix:** In add_manual_rename and in _rename_one: reject a target_name if Path(target).name != target or it contains os.sep, '..' or NUL; run it through _sanitize_filename; assert new_path.resolve().parent == path.resolve().parent. Log full old and new paths. Normalise paths before writing the journal and DBs.

#### [MEDIUM · demonstrated] Learned manual-rename and quarantine rules persist forever, re-fire on later runs, and cannot be listed, retracted or undone

- **ID:** `tool-rename-learned-rules-permanent` · **Area:** chop-shop-tool · **Tool/surface:** rename learned rules
- **Expected:** A manual rename applies once (or is visibly listed), and the user can review and delete learned rules; reverting a run tells the user the rules that drove it are still active.
- **Actual:** Rules are invisible, permanent and silently re-applied; reverting a run does not stop the next run repeating it.
- **Evidence:** The rules written at 05:19 (renamer_learned.json gained manual_renames for Kaito/Nula, r3/out/before_t.json) fired again on an unrelated root-level run at 05:26 ('Renamed: Kaito_-_sunrise_FINAL_v2.wav -> Escaped Kaito.wav' and '... -> OutsideLibrary - Nula.mp3' in r3/out/root.sse), after the earlier run had been reverted. Rules are keyed by absolute source path and never consumed. No route lists or retracts them: grep of routes_*.py/app.py finds only preflight apply; LearnedRules.retract (renamer_learned.py ~152) is not exposed. renamer_learned.json is not covered by savepoints or the operations journal.
- **Fix:** Expose GET/DELETE /api/rename/rules (wire up LearnedRules.retract), show active rules in the Rename card, consume one-shot manual renames after a successful apply, and record rule IDs in the journal metadata.

#### [MEDIUM · demonstrated] Preflight 'quarantine' moves a file immediately, from any path, with no journal row, no Rekordbox relink and no undo

- **ID:** `tool-rename-preflight-quarantine-unjournaled` · **Area:** chop-shop-tool · **Tool/surface:** rename (/api/rename/preflight/apply action=quarantine)
- **Expected:** Quarantine choices are applied during the live run (with Rekordbox relink and journal), or at least journaled and relinked, and confined to the selected root.
- **Actual:** It is an immediate, untracked move that breaks Rekordbox links and cannot be reverted from the Undo Wizard.
- **Evidence:** Round 3: POST with path=Music Library/Techno and source_path=<sandbox>/Incoming/Newcomer - Fresh Cut.mp3 (outside 'path') moved it to Music Library/No-Name tracks for Tagging/ at once (response in r3/out/pf_quar.json; Incoming emptied). No fg_processing_log row was added, and /api/undo/operations didn't change. Round 2: quarantining the Rekordbox-tracked Techno/untitled track 03.mp3 this way left its master.db row pointing at the old path (round-2 out/after_pfq.json: rb_local missing 'Techno/untitled track 03.mp3'). Code: routes_tools.py:806-809 calls quarantine_track(Path(source_path), root) with no containment check, no DB and no archive. Today this is reachable only through the API, because the preflight modal is hidden (E4).
- **Fix:** Record quarantine as a rule only and let the live run apply it through _rename_one (which relinks and journals), or call _sync_db_path_or_revert + _archive_rename here. Reject source paths outside 'path'.

#### [MEDIUM · demonstrated] Undo 'sessions' are built by a 15-minute gap, not by job: runs merge, reverted rows stay inside, and chains need several reverts

- **ID:** `tool-rename-undo-sessions-by-time-gap` · **Area:** undo-revert · **Tool/surface:** rename (/api/undo/operations)
- **Expected:** One session per job (job id / rename_batch), reverted in reverse order, marked as reverted afterwards, and linked to that job's report and savepoint.
- **Actual:** Time-clustered lumps: you cannot undo only the last run, reverted rows linger, and chained renames need repeated reverts.
- **Evidence:** Four separate runs (live multi-folder 05:17, traversal 05:19, cancelled 05:20, resumed 05:23) appeared as ONE session: ('rename', first_id 3, last_id 335, count 313) (r3/out/after_k1.json). After the first revert, the UI row still offered 'Preview undo' (u1 'after row 0'), and the API still reported revertible:true. The preview over ids 3..27 planned move_back twice for the same file (rows 13 and 26); the revert returned errors ['Nula - Drives - Night Drive (copy).mp3: filesystem error'] (after_tu step). Round 2: chain A->B (run 1) then B->C (run 2) merged; the first revert left the file at B (after_chain_rev1.json), and a second click was needed to reach A (after_chain_rev2.json). Rekordbox was left at C. Code: routes_undo.py:322 (_SESSION_GAP_SEC = 15*60) and 344-404.
- **Fix:** Write a job_id into each journal row's metadata (the rename_batch row already marks the end of a root), group sessions by job_id, process items newest-first and collapse chains, and hide or mark sessions whose rows all have matching undo_* rows.

#### [MEDIUM · demonstrated] Pre-run savepoint exists but is not referenced by the job, report, undo session or resume banner

- **ID:** `tool-rename-restore-point-unlinked` · **Area:** undo-revert · **Tool/surface:** rename
- **Expected:** The job records 'restore point: <savepoint> of <local DB> taken at <time>', shown in the report, the operations session and the resume banner, with a working restore to the same DB.
- **Actual:** The user cannot tell which backup belongs to which rename run, and the endpoint that would restore it targets the wrong DB.
- **Evidence:** Each live run logs 'Backup created: …/FableGear Archive/Savepoints/master.backup_<ts>.db' before mutating (l1 log 05:17:59). By the end, /api/undo/savepoints listed 5 unlabeled backups (051759, 051831 from an unrelated action, 051923, 052032, 052353; after_k1.json), with no tool, job or DB name. The report, the operations session and the resume banner never mention it. Each resume creates a NEW savepoint (k3 log: master.backup_20261006_052559…), so the pre-interruption point is not identified. Restoring it goes to the device DB (E10), and the Savepoints tab throws (E2).
- **Fix:** Return the backup path from write_db and include it in the rename_batch metadata, the FABLEGEAR_REPORT and the checkpoint payload. Tag savepoint filenames with tool, job and DB. Fix E10/E2.

#### [MEDIUM · demonstrated] Dry run / preview gives counts only: no per-file old -> new list and no quarantine destination

- **ID:** `tool-rename-dry-run-counts-only` · **Area:** chop-shop-tool · **Tool/surface:** rename
- **Expected:** The preview lists each file's current name -> proposed name (and the No-Name destination folder) before anything changes; the live report lists what changed.
- **Actual:** The user must run live to learn which files get which names; the report says only how many.
- **Evidence:** UI dry run on the music root: the modal and the report file (FableGear Archive/Reports/Rename/rename_20261006_051712.txt) contain only '15 audio files scanned. 8 files would be renamed… 5 already have clean names… 1 would get numbered suffixes… 1 unresolved files would be moved to No-Name tracks for Tagging.' (r3/out/d1_dry_log.txt). The dry-run log has no per-file lines (cli.py:3555-3577 prints only counts). The live report also lacks the mapping. Probe Ambiguities shows proposals for top-N files only, and its modal renders at 0x0 (p1_probe: modal_open_class true, rect 0x0, hidden ancestor chop-card-stack; E4).
- **Fix:** In dry run, log 'Would rename: <old> -> <new>' per RenameResult and add a per-file table to the report (capped, with the full list in Archive/Logs). Show the absolute quarantine destination.

#### [MEDIUM · demonstrated] Cancel and server crash write no report and no batch summary; checkpoint lags the real progress

- **ID:** `tool-rename-cancel-crash-no-report` · **Area:** chop-shop-tool · **Tool/surface:** rename
- **Expected:** An interrupted run leaves a partial report (what was renamed, the restore point, how to resume), and the checkpoint matches the journal.
- **Actual:** Nothing is written; the only record is the time-grouped operations journal.
- **Evidence:** c1: POST /api/cancel after about 60 files ended with '✗ Exited with code -15', no report modal, no new file in Reports/Rename (the listing still showed only 051712/051759/051923), no rename_batch row; the journal showed 90 renames while the checkpoint said completed=75, total=0 (r3/out/c1_cancel_log.txt, after_c1.json). k2: kill -9 of the real server (PID 7816) after 45-60 files: the UI showed 'Connection error — check the server is running.', no report, 60 renamed, checkpoint completed=50 (after_k2.json). No cli.py child survived. cli.py installs no SIGTERM handler; the checkpoint is saved every 25 results (cli.py:3541-3548). Confirmed good: Rekordbox and FableGear stayed consistent in both cases, because pyrekordbox update_content_path commits per call (commit=True default), which makes renamer's 500-file 'batch' commit cosmetic.
- **Fix:** Install a SIGTERM handler in cli.py that flushes the checkpoint and emits a partial report. Save the checkpoint per file (or derive progress from the journal). Write the report incrementally to Archive/Logs/rename.

#### [MEDIUM · demonstrated] Interrupt between the quarantine move and the DB update orphans the file: Rekordbox and FableGear point at the old path, and there is no journal or manifest entry

- **ID:** `tool-rename-cancel-race-orphan` · **Area:** chop-shop-tool · **Tool/surface:** rename
- **Expected:** Write-ahead intent (journal row or checkpoint entry) before each move, so an interrupted move can be completed or rolled back on resume.
- **Actual:** A kill in the window leaves an untracked moved file and broken DB links that neither resume nor undo can see.
- **Evidence:** Round 2 (sandbox rename-r): a cancel during a 1200-file quarantine run left 'rb_local missing-on-disk 1 [Music Library/Bulk/bulk_000102_trk.mp3]', with fg_content also missing the same path. The file was moved, but no journal row exists, and the resume did not repair it (round-2 out/after_cancel.json, after_resume.json via r3/consist.py). It was not reproduced in round 3 (0/90 and 0/60 inconsistent on rename actions), so it is a timing window. Code: renamer.py:1031-1057 does quarantine_track (move) -> _sync_db_path_or_revert -> _record_quarantine_manifest -> _archive_rename, with nothing durable before the move.
- **Fix:** Journal an 'intent' row (from/to, status=pending) before moving, finalise it after the DB relink, and on startup or resume reconcile pending rows.

#### [MEDIUM · demonstrated] Resume banner lacks starting-point details; resume re-scans everything, silently includes new files, and misreports the total

- **ID:** `tool-rename-resume-banner-and-semantics` · **Area:** session-resume · **Tool/surface:** rename
- **Expected:** The banner states when the run started, whether it was live, its roots and config, files done/remaining, and the restore point. Resume warns about inputs changed since, and the final report covers the whole session.
- **Actual:** A bare 'Interrupted run' prompt; resume is effectively a fresh run, and the report understates what this session changed.
- **Evidence:** After relaunch the banner shows only 'Interrupted run — 3m ago', the roots, and Resume/Start Fresh (screens/tool-rename/r3/s1_observe_0_open.png). Under it, the form still shows 'Music Library' with Dry Run checked. The state comes from localStorage rb_ckpt_rename (E5); the server checkpoint (completed=75, total=0) is not shown. There is no live/dry flag, no done/remaining count, and no restore point. Resume: 'Found checkpoint … (75/0 done) — resuming', then 'Renaming 301 files' (done_paths hold original names that no longer exist, cli.py:3535-3548). A file added between sessions (zz_new_between_sessions.mp3) was silently renamed. The final report said '211 renamed / 91 already had clean names' (k1), and after the crash '241 renamed / 61 already had clean names' (k3), counting the 90 and 60 files renamed before the interruption as 'already clean'. Confirmed good: both resumes finished, with Rekordbox and FableGear fully consistent (after_k1.json, after_k3.json).
- **Fix:** Show the server checkpoint info (/api/checkpoint/check) and the linked savepoint in the banner. Store renamed targets as well as sources in the checkpoint. Diff the file list against the checkpoint and warn on new or missing files. Merge pre-interruption counts into the final report.

#### [MEDIUM · demonstrated] Running Rename on the music root quarantines files to a folder outside the music library

- **ID:** `tool-rename-root-quarantine-outside-library` · **Area:** chop-shop-tool · **Tool/surface:** rename
- **Expected:** Quarantine goes to one predictable folder inside the configured music root, whatever folder was selected.
- **Actual:** The location depends on the selection; selecting the root pushes files outside the library that other tools scan.
- **Evidence:** Live run on <DJDRIVE>/Music Library via /api/run/rename (the single-root path that the API and Pipeline use) moved Techno/untitled track 03.mp3 to Volumes/DJDRIVE/No-Name tracks for Tagging/ (sibling of Music Library), with Rekordbox relinked there (r3/out/root.sse, after_root.json, `ls Volumes/DJDRIVE`). Multi-folder runs put it inside Music Library/No-Name tracks for Tagging (after_l1.json). Code: renamer.py:899-900 `_no_name_dir = library_root.resolve().parent / _NO_NAME_FOLDER`, with library_root = the scanned root (renamer.py:1320).
- **Fix:** Derive the No-Name folder from config music_root (or the first root itself), never from root.parent; show the absolute destination in the preview and report.

#### [MEDIUM · inferred] Case-only renames on case-insensitive APFS would be treated as collisions and get a bogus ' (2)' suffix

- **ID:** `tool-rename-case-only-macos` · **Area:** chop-shop-tool · **Tool/surface:** rename
- **Expected:** A case-only change is detected (same inode / samefile) and done via a temporary name, without a suffix.
- **Actual:** (Inferred) the file gets a spurious '(2)' and the report claims a clash.
- **Evidence:** On Linux (case-sensitive), 'dj case - lower title.mp3 -> DJ Case - Lower Title.mp3' renamed cleanly, Rekordbox was relinked, and undo restored it byte-identical (demonstrated, l1/u1). On macOS: renamer.py:1072 compares PosixPaths case-sensitively (not equal), then renamer.py:1081 `new_path.exists()` is True because it is the same file on a case-insensitive volume, so _resolve_filename_collision (renamer.py:880-896) yields 'DJ Case - Lower Title (2).mp3', counted as a name clash. Not testable in the sandbox.
- **Fix:** If new_path.exists() and os.path.samefile(path, new_path): rename via a temp name (path -> .tmp -> new_path) and treat it as 'renamed'.

#### [LOW · demonstrated] Root-level run renames sample-pack one-shots into 'Artist - Title' form

- **ID:** `tool-rename-sample-pack-renamed` · **Area:** chop-shop-tool · **Tool/surface:** rename
- **Expected:** Sample-pack folders are excluded, or flagged as non-track audio, as other library tools do.
- **Actual:** One-shots are renamed with a junk artist ('kick').
- **Evidence:** The root run logged 'Renamed: kick_01.wav -> kick - 01.wav' in Music Library/Sample Packs (r3/out/root.sse). The fixture's Rekordbox build excludes Sample Packs, but the renamer's walk (_walk_audio_files, renamer.py:958) does not.
- **Fix:** Skip known sample-pack folders and short one-shots, or require artist and title tags before renaming.

#### [LOW · demonstrated] When every Rekordbox relink fails (missing ANLZ share dir), files are rolled back but the run ends '✓ Finished successfully'

- **ID:** `tool-rename-rollback-reported-as-success` · **Area:** chop-shop-tool · **Tool/surface:** rename
- **Expected:** A run where every change was rolled back is reported as failed or partial, and the log says 'rolled back'.
- **Actual:** A success banner with buried error counts.
- **Evidence:** Round 2, pristine fixture without ~/Library/Pioneer/rekordbox/share: each 'Renamed: X -> Y' was followed by 'Database lookup/update failed … [Errno 2] No such file or directory: …/rekordbox/share'. _sync_db_path_or_revert (renamer.py:1183-1227) renamed each file back, which is correct and consistent. The run printed '9 files had errors' and still ended with '✓ Finished successfully' (round-2 out/r3_live_multi_log.txt). The log line 'Renamed:' is printed before the rollback, so it is misleading.
- **Fix:** Exit non-zero (or emit a 'partial' status) when errors > 0. Log the 'Renamed' line only after the DB sync succeeds, or log 'Rolled back' on revert.

#### [LOW · demonstrated] After undo: orphan quarantine manifest, stale fg status, and report wording that doesn't match behaviour

- **ID:** `tool-rename-undo-leftovers-and-copy` · **Area:** undo-revert · **Tool/surface:** rename
- **Expected:** Undo also removes or updates the quarantine manifest; messages match the tool and the real naming.
- **Actual:** Minor stale artefacts and inaccurate copy.
- **Evidence:** After reverting, Music Library/No-Name tracks for Tagging/_quarantine_manifest.json still lists 'untitled track 03.mp3' as quarantined, although it is back in Techno; .fablegear_state.json files remain; fg_content.processing_status stays 'relinked' (diff before -> after_u1). The revert-plan reason for an already-reverted rename reads 'file is no longer at the organized location' (organize wording). Reports say 'numbered suffixes … (e.g. title_1.mp3)' while the actual format is 'Title (2).mp3' (d1_dry_log.txt vs renamer.py:880-896).
- **Fix:** Remove reverted entries from _quarantine_manifest.json during undo, reset processing_status, and make the reason strings and the clash example tool-specific and accurate.

#### [INFO · demonstrated] Confirmed-good behaviour (forward rename, byte identity, idempotent revert, crash consistency)

- **ID:** `tool-rename-confirmed-good` · **Area:** chop-shop-tool · **Tool/surface:** rename
- **Expected:** n/a
- **Actual:** n/a
- **Evidence:** Forward rename keeps content byte-identical (every renamed path has the same sha256, diff before/after_l1). Collisions are numbered (dup_one.mp3 -> 'Dup Artist - Same Song (2).mp3'). Local Rekordbox FolderPath/FileNameL and FableGear rows are relinked per file. A local master.db savepoint is created before the first mutation. A report is written to FableGear Archive/Reports/Rename, and its path is shown in the report modal. Revert restores 9/9 and 300/300 files byte-identical, and a second revert is a no-op (0 reverted, 9 blocked; after_u2 diff shows no change). Cancel and kill -9 left Rekordbox and FableGear consistent with no orphan cli.py (after_c1/after_k2). The resume banner renders after relaunch, and resume completes. Failed DB syncs roll the file back (round 2). Live runs require Rekordbox closed (routes_tools.py:712-716 _require_rb_closed; inferred, not exercised).
- **Fix:** Keep these behaviours, and add regression tests around them when fixing the findings above.

## Chop Shop: Organize

_Auditor: `tool-organize`_

Organize Library (#step-organize, /api/run/organize, chop_shop/library_organizer.py), tested live in sandbox organize-r3 on port 8114. Runs: a UI dry run, then a UI live assimilate run over 2 sources with seeded edge cases (a same-name collision, a byte-identical duplicate, .DS_Store and ._ AppleDouble junk, a cover.jpg, a pre-existing empty folder). Then undo through the app's endpoints, a second revert, an integrate (copy) run triggered by a cross-site-headed GET, a 4-worker collision stress run, a UI Interrupt mid-run (on Linux, /api/cancel sends SIGTERM), a kill -9 of the server mid-run, restart.sh, resume from the UI banner, a byte-identical duplicate-to-trash run, and the Undo panel's Operations tab.
Verdict: with 1 worker and no interruption, the basic move/undo cycle is solid. Every audio file came back byte-identical, the pruned folders were recreated, the FableGear DB paths were restored, and reverting twice is a safe no-op.
Serious problems:
(1) CRITICAL: with parallel workers (the UI offers 2 and 4), files that map to the same destination overwrite each other. 2 of 120 distinct files were permanently lost while the report said "No errors". Undo then put the wrong audio into two original slots.
(2) HIGH: Organize relinks the FableGear DB but never touches Rekordbox master.db. Every moved track's FolderPath is silently broken (10/10 local and 4/4 device paths missing).
(3) HIGH: an Interrupt can leave a moved file with no journal row, so undo strands it.
(4) HIGH: the undo journal groups by "same type within 15 min", so 7 separate runs showed in the UI as one 309-file "Organize" session that cannot be reverted per run.
Also: integrate-mode copies cannot be undone and they re-point the FableGear library at the copies. The dry run does not predict collisions or duplicates. Trashed duplicates are invisible to the Trash API. Resume works only from browser localStorage (cite E5): /api/checkpoint/check says exists:false, and the banner has no starting-point details. The report is summary-only and makes false "cleaned up" / "left alone" claims. No restore point is taken before the run (the journal is written after each move).

**Coverage**

- LIVE via the real UI (Playwright + headless Chromium against the sandbox Flask server; script audit/tool-organize/ui_org.py):
- - dry run (r1); live assimilate run (r2), sources Music Library + Incoming, target Music Library
- - live run with Interrupt at 33+ files done (r5; the #scan-bar-interrupt selector was not visible, so the script used POST /api/cancel; the screenshot shows a visible Interrupt / Emergency Stop pair)
- - resume from the banner after restart (r6); kill -9 of the server mid-run, then resume (r7/r8)
- - banner check with a fresh browser profile (r8_nostorage)
- - Undo panel Operations tab plus Preview (r9; audit/tool-organize/undo_ui.py)
- LIVE via the API: integrate-mode live run sent as a GET with a cross-site Origin header (r3); 4-worker collision stress run with 120 files (r4); byte-identical duplicate to trash (DupSrc); /api/undo/operations, preview and revert (twice, plus a range revert); /api/checkpoint/check; /api/undo/trash, /savepoints, /timeline, /database/history
- SNAPSHOTS (sha256 of every file under Music Library and Incoming, dir lists, Archive tree, ~/.Trash, ~/.fablegear, fg_content and fg_processing_log rows, Rekordbox local and device master.db sha plus FolderPath existence, undo APIs): audit/tool-organize/s0_before.json, s1_afterdry.json, s2_afterlive.json, s3_afterundo.json, s4_afterundo2.json; diffs from audit/tool-organize/diff.py
- SCREENSHOTS in /home/user/FableGear/docs/audits/2026-10-05/screens/tool-organize/:
- - r1_dry_*, r2_live_*, r5_cancel_2_running.png (shows the Interrupt / Emergency Stop buttons), r6_resume_*, r7_kill_*, r8_*
- - r9_undo_operations.png, r9_undo_preview.png
- CODE-ONLY (inferred):
- - the mix threshold and --by scheme are not part of the resume state
- - the Undo-panel success toast ignores out.errors
- - dupe_trashed journal rows revert from Trash through Operations
- NOT TESTED / LIMITS:
- - --by scheme options and --by playlist mirror: the UI exposes no scheme selector, so these are reachable only via the API; not exercised
- - cross-volume moves: the sandbox is a single filesystem, so moves are renames; a real cross-drive move is copy+unlink and has a wider interrupt window
- - macOS Finder Trash semantics and exFAT AppleDouble metadata
- - Emergency Stop (SIGKILL of the CLI) as distinct from SIGTERM
- - WKWebView rendering
- - the browser-side exploit for the cross-site GET (only the server's acceptance was demonstrated)
- - fpcalc is absent, but Organize does not use it

**Per-tool safety matrix**

### Organize Library (step-organize, /api/run/organize)

| Check | Result |
|---|---|
| R — Report | Partial. Success and dry run: a summary-only report at <drive>/FableGear Archive/Reports/Organize/organize_<ts>.txt, shown in the report modal with its path. There is no per-file from-to list, no rename list, no trash path, no pruned or junk-deleted list, no Rekordbox impact and no link to the undo session. No Logs/Organize folder is created. Cancel (SIGTERM) and server kill -9 produce no report. Two claims are false: 'Empty source folders were cleaned up' is printed unconditionally (cli.py:3331) even when 41 or 32 emptied folders remain after resume, and 'were already at the destination — left alone' (cli.py:3321) is printed when the source copy was in fact moved to ~/.Trash. |
| M — Revert marker | None before the first mutation. No savepoint (/api/undo/savepoints unchanged by any organize run; the master.db savepoint at 05:21:52 came from the snapshot scheduler and is unlinked to organize), and /api/undo/timeline stays empty (E1). The only marker is fg_processing_log rows written after each move (library_organizer.py:672-684) plus one organize_batch row per source at the end. Those batch rows are excluded from /api/undo/operations, so a run has no start marker and runs merge by a 15-minute gap (routes_undo.py:322). |
| U — Undo | Sequential assimilate run: all 14 audio files returned byte-identical to the exact original paths, pruned folders recreated, fg_content paths restored, Rekordbox paths valid again (they were never updated). Not restored: .DS_Store and ._ junk (deleted for good), 11 empty target folders left behind, the empty ~/.Trash/FableGear_OrgDupes_* folder, fg_content processing_status changed scanned to relinked. Second revert: safe no-op (reverted 0, blocked 14). The revert itself cannot be undone ('undo_organize' is not revertible), and the session still shows revertible:true. Integrate (copy) runs cannot be undone: all 13 items blocked with 'original location is occupied'. After the 4-worker race, undo errored on 2 items and restored the wrong audio into 2 original slots. A file moved just before a SIGTERM has no journal row and is stranded. |
| C — Cancel / interrupt | /api/cancel sends SIGTERM (helpers.py:516); cli.py has no handler. Live: 41 moved, 40 journaled (1 move unjournaled), checkpoint at 25/41, no report, no batch row, 41 emptied source folders not pruned. Server kill -9: the CLI stopped at 32/80 (32 journaled), checkpoint 25, no report, 32 emptied folders left. No byte corruption in either case. |
| S — Resume after restart | Resume is offered only through localStorage rb_ckpt_organize. A fresh browser profile gets no banner although ~/.fablegear/checkpoints/organize/*.json.gz exists, and /api/checkpoint/check?tool=organize returns exists:false because of a config-key mismatch. The banner says only 'Interrupted run — Nm ago' and the source path; no target, mode, done/remaining counts or restore point. Resume completed the run with every file byte-intact. It skips done work only because moved files are gone from the source; the checkpoint lags by up to 24 files and logged '25/0 done'. A file added between sessions was silently included. The mix threshold is not restored (inferred). The report after resume covers only the resumed part, and the interrupted session's emptied folders are never pruned. See E5. |
| D — Dry run / preview | Yes, and it is the default (checkbox checked); it shows a PLAN line per file in the log plus a report. Fidelity gaps: 3 files planned to the same 'Nula/Drives/Nula - Night Drive.mp3' while the summary said '14 would be moved', with no clash or duplicate prediction (the live run renamed 2). Prunes, junk deletions, trash moves and Rekordbox impact are not listed. A dry run still writes .fablegear_state.json into the target and a report into the Archive. The live run has no confirm gate (window.confirm never called); the checkbox is the only safeguard. |
| B — Boundary | Crosses into the DB layer. It writes the FableGear DB (relink_content per moved file, library_organizer.py:677), but that write is not covered by any savepoint; it is reversed only incidentally by the operations revert, and in integrate mode it is not reversed at all. It never writes Rekordbox master.db (sha unchanged), so every moved track's FolderPath is broken with no warning. |

_Evidence:_ Snapshots s0-s4 and diff.py output in audit/tool-organize/; SSE captures out/r*_sse.json and out/r3_integrate_sse.txt; undo JSON in out/undo_*.json; screenshots under screens/tool-organize/ (r1, r2, r5, r6, r7, r8, r9).

**Findings**

#### [CRITICAL · demonstrated] Parallel workers overwrite colliding files: silent permanent data loss, and undo restores the wrong audio

- **ID:** `tool-organize-workers-race-overwrite` · **Area:** chop-shop-tool · **Tool/surface:** Organize Library
- **Expected:** Two different files never end up sharing one destination; every source file is either at a unique destination or reported as an error; undo returns each file to its own original path.
- **Actual:** With workers > 1, files that compute the same free slot overwrite each other. The loss is unreported (exit 0, 'No errors'), the overwritten audio is unrecoverable, and undo writes the survivor into the wrong owner's original path.
- **Evidence:** Built 120 distinct, same-size MP3s (30 groups x 4 folders, all tagged RaceArtist/RaceAlbum and named 'Race - TNN.mp3'; 120 unique sha256 values). Ran GET /api/run/organize?source=RaceSrc&target=RaceTarget&no_dry_run=1&workers=4 (the UI offers '4 workers'). Result: 118 files at the target, 0 left in the source, 2 distinct sha256 values gone (comm against race_before.sha). Report: '120 files were moved… 90 name clashes… No errors.' Journal: 120 rows but only 118 distinct destinations; RaceArtist/RaceAlbum/Race - T07_1.mp3 is journaled for both T07_d and T07_c, and Race - T01_2.mp3 for both T01_a and T01_b. The undo revert returned reverted:118 with errors ['Race - T07_1.mp3: filesystem error', 'Race - T01_2.mp3: filesystem error']. Afterwards T07_d/Race - T07.mp3 holds T07_c's audio (trailing marker '07c'), T01_a holds T01_b's audio, and T07_c and T01_b are missing. Cause: check-then-act race between the dest.exists() check / numbered-slot search in _resolve_dest (library_organizer.py:369-401) and shutil.move, which is os.rename and overwrites silently (library_organizer.py:651), under ThreadPoolExecutor (library_organizer.py:700). No test uses max_workers > 1.
- **Fix:** Reserve destinations atomically: compute all final destinations single-threaded before any I/O, using an in-memory set of claimed paths, and only parallelize the byte copy. Or claim each slot with os.open(O_CREAT|O_EXCL) / os.link and never use a rename that can replace an existing file (renameat2 RENAME_NOREPLACE on Linux; on macOS, link plus unlink or an exclusive create). Hash-verify after each move and store the sha in the journal row so undo can check it is moving the right content. Until fixed, remove the 2 and 4 worker options from the UI.

#### [HIGH · demonstrated] Organize relinks the FableGear DB but leaves every Rekordbox FolderPath broken, with no warning

- **ID:** `tool-organize-rekordbox-paths-broken` · **Area:** boundary · **Tool/surface:** Organize Library
- **Expected:** A file tool that moves library files either updates Rekordbox paths in the same job (behind a savepoint and journal so the change can be reverted), or clearly reports which Rekordbox tracks are now missing and offers a relink.
- **Actual:** After any assimilate run Rekordbox shows every moved track as missing. The FableGear library and Rekordbox now disagree, and the user is not told.
- **Evidence:** s1_afterdry vs s2_afterlive: local and device master.db sha unchanged. The Rekordbox local DB's 10 content rows for moved tracks went from exists=True to exists=False, and the device DB's 4/4 went True to False. The fg_content table was relinked for all 13 rows (processing_status='relinked'). The report and UI say nothing about Rekordbox; after a live run the UI only calls _promptSetLibraryRoot(target) (pipeline.js:1201-1206). Code: the only DB write is archive.relink_content (library_organizer.py:677); nothing opens master.db.
- **Fix:** After a live run, map the journal's from-to pairs onto DjmdContent.FolderPath and offer an 'Update Rekordbox paths' step. That step needs a master.db savepoint (M) and relocate-type journal rows so /api/undo/operations can revert it, and it must refuse while Rekordbox is running. At minimum, add a 'N Rekordbox tracks now point to moved files — run Fix Paths' line to the report and the completion modal.

#### [HIGH · demonstrated] Interrupt (SIGTERM) can land between a move and its journal row; that file is stranded by undo

- **ID:** `tool-organize-cancel-unjournaled-move` · **Area:** undo-revert · **Tool/surface:** Organize Library
- **Expected:** Every completed move has a journal row, even if the process is interrupted, so undo can return every file.
- **Actual:** A file moved just before the signal has no record; undo reports success and silently leaves it in the organized tree.
- **Evidence:** UI live run of 80 colliding 16MB WAVs (SlowSrc to SlowTarget); POST /api/cancel at done=33; the SSE ended with exit_code -15 after '[41/80] CONFLICT_RENAMED'. State: 41 WAVs at the target but only 40 fg_processing_log rows for SlowTarget; checkpoint completed=25; no report, no organize_batch row, 41 empty source folders. After resume and then a revert of the SlowTarget journal range (ids 288-367): reverted 80, blocked 0, errors []. But SlowTarget/Slow/Slow - Track_40.wav (trailing id 00000035) stayed behind, and the original slot SlowSrc/F035/Slow - Track.wav is empty. Code: /api/cancel sends SIGTERM (helpers.py:516, routes_tools.py:1249); cli.py has no SIGTERM handler; the move (library_organizer.py:651) and the journal write in _tally (library_organizer.py:672-684) are separate, non-atomic steps.
- **Fix:** Write a 'pending' journal row (src, intended dest) before the move and mark it done after. Undo should treat pending rows by checking which of src or dest exists. Install a SIGTERM handler that finishes the current file, flushes the checkpoint and writes a partial report before exiting. Save the checkpoint every file, or at least on signal.

#### [HIGH · demonstrated] Undo groups organize journal rows by type with a 15-minute gap, merging separate runs into one irreversible-per-run session

- **ID:** `tool-organize-undo-session-merge` · **Area:** undo-revert · **Tool/surface:** Organize Library
- **Expected:** Each tool run appears as its own revertible entry ('revert to before Organize at HH:MM'), and an entry that has been reverted shows that state.
- **Actual:** Running Organize twice within 15 minutes makes the runs indistinguishable in the Undo panel. 'Return N files' reverts all of them together, including copies made by a different mode, and a reverted run still looks revertible.
- **Evidence:** After 7 distinct organize runs between 05:22 and 05:33 (UI assimilate, API integrate, 4-worker race, UI cancelled plus resumed, UI killed plus resumed, duplicate test), /api/undo/operations returned ONE session {first_id:5, last_id:451, count:309, started 05:22:14}. The Undo panel's Operations tab (screens/tool-organize/r9_undo_operations.png) shows 'Organize — files moved · 309 files', and Preview offers 'Return 162 files · 147 blocked' (r9_undo_preview.png). Earlier, the integrate run (ids 35-47) merged into an assimilate run that had already been reverted (ids 5-19). After a full revert the session is still listed as revertible:true, because the undo_organize rows are filtered out (routes_undo.py:359). Code: routes_undo.py:322 (_SESSION_GAP_SEC) and the grouping loop at routes_undo.py:365-395; organize_batch rows (one per source, written at the end) are not used as boundaries.
- **Fix:** Give every job a run_id (write a start row before the first mutation and stamp it into each per-file row's metadata) and group sessions by run_id. Record reverts against the run so it shows as reverted, and offer redo. Show mode, sources and target in the session label.

#### [MEDIUM · demonstrated] Integrate (copy) mode cannot be undone, and silently re-points the FableGear library at the copies

- **ID:** `tool-organize-integrate-no-undo-fg-repointed` · **Area:** undo-revert · **Tool/surface:** Organize Library
- **Expected:** Copy mode leaves the library DB pointing at the originals (or asks first), and its undo removes the copies (as novelty_copy undo does, parking them in the Archive).
- **Actual:** The FableGear library silently follows the copies. If the user deletes the copy folder, believing it was 'just a copy', every FableGear track goes missing. Undo cannot remove the copies.
- **Evidence:** GET /api/run/organize?source=Music Library&target=Organized&no_dry_run=1&mode=integrate copied 13 files; the report said 'Source folders were not modified.' fg_content rows 1-13 now all point to /Volumes/DJDRIVE/Organized/... (processing_status 'relinked') while the originals still exist and Rekordbox still points at them. Undo preview on the session: the 13 integrate rows are all blocked with 'original location is occupied' (routes_undo.py:466), so the copies cannot be removed and the DB repoint cannot be reverted. Journal rows say action 'moved', the SSE log says 'MOVED' for every copy, and the Undo panel labels the session 'Organize — files moved'. Code: library_organizer.py:672-684 relinks for both modes; the undo plan has no copy branch for organize.
- **Fix:** In integrate mode, journal the rows as organize_copy (or record action 'copied') and do not relink. If relinking is wanted, make it an explicit option. Add a remove_copy revert branch, as already exists for novelty_copy (routes_undo.py:449-457). Fix the log and UI wording to say 'COPIED'.

#### [MEDIUM · demonstrated] Dry run does not predict clashes, duplicates, prunes or deletions; the live run has no confirm gate

- **ID:** `tool-organize-dryrun-misses-collisions` · **Area:** chop-shop-tool · **Tool/surface:** Organize Library
- **Expected:** The preview matches what the live run will do, including renames, duplicate trashing, folder prunes, junk deletion and the DB effect, and a destructive live run asks for confirmation.
- **Actual:** The preview understates the changes, and unchecking one box goes straight to moving files.
- **Evidence:** The UI dry run (r1) planned 'Nula - Night Drive.mp3 → Nula/Drives/Nula - Night Drive.mp3' three times (Promo, House, Backup) and summarized '14 would be moved', with no clash or duplicate lines. The live run (r2) then produced Nula - Night Drive_1.mp3 and _2.mp3 and reported '2 name clashes'. The dry run did not list the 7 folders the live run pruned, the 2 junk files it deleted, or that Rekordbox paths would break. The live run raised no confirm (window.confirm calls: [] in both runs); runOrganize (pipeline.js:1177-1207) goes straight to runCommand. Code: in dry-run mode _process returns before _resolve_dest (library_organizer.py:609-612), so collisions are never simulated.
- **Fix:** In dry run, simulate destination claims with an in-memory set (the same structure the race fix needs) and run _resolve_dest against it. List the clash renames, the files that would go to Trash, the folders that would be pruned and the junk files that would be deleted. Add a confirm step for live assimilate runs (rendered outside #chop-card-stack; see E4).

#### [MEDIUM · demonstrated] Byte-identical duplicates are kept as _1/_2 when a different file already holds the canonical name

- **ID:** `tool-organize-identical-dupe-missed` · **Area:** chop-shop-tool · **Tool/surface:** Organize Library
- **Expected:** Assimilate mode ('remove source duplicates') recognizes an identical file in any numbered slot.
- **Actual:** Duplicate copies pile up as _1, _2 … depending on scan order.
- **Evidence:** Seeded House/Nula - Night Drive.mp3 and Backup/Nula - Night Drive.mp3 (identical sha f12684de160c272e), plus a different same-size Promo/Nula - Night Drive.mp3 (55e58a1a…). After the assimilate run: Nula/Drives/Nula - Night Drive.mp3 = Promo version, Nula - Night Drive_1.mp3 = f12684de…, Nula - Night Drive_2.mp3 = f12684de… (s2_afterlive diff). Nothing went to Trash. Code: _resolve_dest (library_organizer.py:359-401) compares only against the canonical slot, never against existing _N siblings. When the canonical slot was free (DupSrc test), the identical duplicate was correctly trashed.
- **Fix:** Before choosing a new _N slot, compare the size and sha256 of the source against every existing stem_N sibling, or keep a per-destination-folder hash index for the run.

#### [MEDIUM · demonstrated] Trashed duplicates go to ~/.Trash/FableGear_OrgDupes_*, which the Trash API never lists; the report says 'left alone'

- **ID:** `tool-organize-dupe-trash-invisible` · **Area:** undo-revert · **Tool/surface:** Organize Library
- **Expected:** Files the tool moves to Trash appear in the Undo panel's Trash list with their run, and the report says they were moved to Trash, with the path.
- **Actual:** The trash folder is invisible to the Trash API (and the Trash tab is broken anyway, E2), and the report wording hides that the source file was moved away.
- **Evidence:** DupSrc/a and DupSrc/b held identical 'Dup - One.mp3' files. The live run logged 'Organizer duplicate-trash folder: ~/.Trash/FableGear_OrgDupes_20261006_053354' and 'SKIPPED Dup - One.mp3', and the report said '1 were already at the destination — left alone.' The file is now at ~/.Trash/FableGear_OrgDupes_20261006_053354/Dup - One.mp3, but GET /api/undo/trash returns []. _list_trash_folders accepts only names starting 'FableGear_Pruned_' (routes_undo.py:202, 236, 267). The OrgDupes folder is also created eagerly even when nothing is trashed: an empty FableGear_OrgDupes_20261006_052214 was left by the first run (library_organizer.py:574-579). Code-inferred: the dupe_trashed journal row has operation_type 'organize', so a revert through Operations would move it back.
- **Fix:** Include FableGear_OrgDupes_* (or a shared prefix) in _list_trash_folders and the restore whitelist. Report 'N exact duplicates moved to <trash path>'. Create the folder lazily.

#### [MEDIUM · demonstrated] Resume relies on localStorage only; the banner has no starting-point details; changed inputs and the interrupted run's leftovers are ignored

- **ID:** `tool-organize-resume-no-starting-point` · **Area:** session-resume · **Tool/surface:** Organize Library
- **Expected:** After a restart the app says when the run started, its sources, target and mode, how many files are done and remaining, and which journal session or restore point to roll back to. Resume detects changed inputs and finishes the interrupted run's cleanup.
- **Actual:** The banner shows only an age and a source path, and depends on WebView storage. The server endpoint denies the checkpoint exists. The resumed run's report covers only its own slice and makes a false cleanup claim.
- **Evidence:** After cancel and restart (r6), and after kill -9 and restart (r8): the banner says only 'Interrupted run — 5m ago / <SlowSrc path> / Resume / Start Fresh'. It is visible only after the script re-ran _showToolResumeBanner on the opened card (see E5 about hidden cards). With a fresh browser profile (r8_nostorage), no banner appears although ~/.fablegear/checkpoints/organize/d1ad88856112b7a8.json.gz exists. GET /api/checkpoint/check?tool=organize&path=SlowSrc returned {exists:false} both times: the route builds config only for duplicates (routes_tools.py:855-869), while the CLI key includes target and mode (cli.py:3222-3224). On resume the CLI logged 'Found checkpoint from 2026-10-06T05:25:42 (25/0 done)'. One WAV added to SlowSrc between sessions was silently included ('Organizing 40 files'). The report after resume said '40 files were moved' and 'Empty source folders were cleaned up', yet 41 (SlowSrc) and 32 (SlowSrc2) emptied folders from the interrupted sessions remain, because only folders emptied by the current process are pruned. Code-inferred: _saveToolCkpt('organize', …) (pipeline.js:1196) does not save mix_threshold, so a resume silently reverts to the 15-minute default; the scheme is not part of the checkpoint key either.
- **Fix:** Use the server checkpoint as the source of truth: fix the /api/checkpoint/check key so it includes target and mode, and render the banner from it. Store started_at, the full config (threshold, scheme, workers), total, done and the journal run_id, and show them in the banner. Persist the set of emptied folders so a resumed run prunes them. Warn when the source file set differs from the checkpoint.

#### [MEDIUM · demonstrated] A live, file-moving organize run is a plain GET with no Origin or CSRF check

- **ID:** `tool-organize-get-live-cross-site` · **Area:** chop-shop-tool · **Tool/surface:** Organize Library
- **Expected:** Destructive runs require a same-origin POST, or at least reject cross-site Origin / Sec-Fetch-Site values on mutating routes.
- **Actual:** Any GET reaching loopback with the right query string moves the user's library.
- **Evidence:** curl -G /api/run/organize with headers 'Origin: https://evil.example', 'Sec-Fetch-Site: cross-site', 'Sec-Fetch-Mode: no-cors' plus source, target, no_dry_run=1 and mode=integrate ran a live job (13 files copied, exit_code 0; out/r3_integrate_sse.txt). The only gate is the loopback check (app.py:209-228); api_organize (routes_tools.py:597-638) takes no_dry_run from the query string. The browser-side vector (a web page firing an <img>/no-cors request at localhost:<port>) is inferred, not demonstrated, and needs the port.
- **Fix:** Reject requests with Sec-Fetch-Site 'cross-site' or a foreign Origin on /api/run/* and other mutating routes. Better: start jobs with POST plus a per-session token and stream progress over a separate GET keyed by job id.

#### [MEDIUM · inferred] The Undo panel's 'Returned N files' success toast ignores the revert's errors array

- **ID:** `tool-organize-undo-ui-hides-errors` · **Area:** undo-revert · **Tool/surface:** Organize Library
- **Expected:** Partial failures are shown with the affected files, and the preview lists what will move where.
- **Actual:** A partly failed undo looks like a full success.
- **Evidence:** API demonstrated: after the race run, /api/undo/operations/revert returned {ok:true, reverted:118, errors:['Race - T07_1.mp3: filesystem error — check server logs', 'Race - T01_2.mp3: filesystem error — check server logs']}. UI code: undo.js:202 shows showToast(`Returned ${out.reverted} file…`, 'success') and never reads out.errors or out.blocked. The preview also shows counts only, with no per-file list (undo.js:178-181). The toast was not clicked live, because doing so would have reverted 162 files across merged runs.
- **Fix:** Show errors and blocked items in the result, using a warning toast style when errors is non-empty. Render the per-item plan (current path to returns_to, reason) in the preview.

#### [MEDIUM · demonstrated] No restore point or start marker before the first move; the journal is after-the-fact only

- **ID:** `tool-organize-no-pre-run-marker` · **Area:** undo-revert · **Tool/surface:** Organize Library
- **Expected:** Before mutating, the job records 'Organize started at T: sources, target, mode' plus a FableGear DB savepoint, and the report and Undo panel point to it.
- **Actual:** The user can find the run only through the merged Operations list, with no link from the report or the completion modal.
- **Evidence:** /api/undo/savepoints, /api/undo/trash, /api/undo/timeline and /api/undo/database/history were unchanged across every organize run (s1 vs s2 vs s3 diffs). The only artifacts are per-file fg_processing_log rows written after each move, plus organize_batch rows written at the end of each source (library_organizer.py:735-747), so an interrupted run never gets a batch row. The FableGear DB relinks have no savepoint (Archive/Database/fablegear.prev.db existed before the run and was not refreshed). The timeline is empty (E1).
- **Fix:** Write a start row (run_id, config) and snapshot fablegear.db before the first move. Put the run_id and an 'Undo this run' link in the report and the completion modal.

#### [LOW · demonstrated] Undo leaves empty organized folders and an empty trash folder, cannot restore deleted junk, and marks DB rows 'relinked'

- **ID:** `tool-organize-undo-orphans-junk` · **Area:** undo-revert · **Tool/surface:** Organize Library
- **Expected:** Undo restores the tree exactly: no leftover destination folders, and the junk files are recoverable (AppleDouble files carry Finder tags and xattrs on exFAT).
- **Actual:** Close to exact for the audio, but the folder tree and metadata files differ from BEFORE.
- **Evidence:** s1_afterdry vs s3_afterundo: all 14 audio files are back byte-identical, but 11 empty folders remain (Brick Wall, Marlo & The Tens/Club Nights, Newcomer, Nula/Drives, Orphaned Tracks/2026, Soft Hands, Vera Lux/Pressure EP). The empty ~/.Trash/FableGear_OrgDupes_20261006_052214 remains. Quiet/.DS_Store and 'Quiet/._Soft Hands - Whisper.mp3' are gone for good (unlinked by the prune, library_organizer.py:803; this is intended per tests/test_library_organizer_cleanup.py:102-117). fg_content processing_status went scanned to relinked. Preview of undo_organize returns "'undo_organize' operations cannot be reverted by moving files" (no redo). Revert twice: reverted 0, blocked 14, no changes (safe).
- **Fix:** On revert, prune destination folders the run created (journal the mkdirs). Move junk files into the run's trash folder instead of unlinking them, and journal that. Restore the previous processing_status.

#### [LOW · demonstrated] Runs, including dry runs, write .fablegear_state.json into the user's music/target folder

- **ID:** `tool-organize-state-file-in-library` · **Area:** chop-shop-tool · **Tool/surface:** Organize Library
- **Expected:** A preview touches nothing in the library.
- **Actual:** A hidden state file is written into the library root on every run.
- **Evidence:** s0_before vs s1_afterdry: '+ Music Library/.fablegear_state.json' after a dry run only. The same file appeared in Organized/ and RaceTarget/ ({library_root, steps_completed.organize.last_run, exit_code}). Written by _sse_response with library_root=target (routes_tools.py:637-638).
- **Fix:** Skip state writes for dry runs, or keep the state under ~/.fablegear keyed by root.

#### [INFO · demonstrated] Confirmed-good behaviour (sequential runs)

- **ID:** `tool-organize-confirmed-good` · **Area:** chop-shop-tool · **Tool/surface:** Organize Library
- **Expected:** -
- **Actual:** -
- **Evidence:** Behaviour confirmed with workers=1 (s0-s4 diffs):
- Moves preserve bytes; all 14 sha256 values match.
- A same-size different-content clash is renamed _1 rather than overwritten.
- The pre-existing empty 'Empty Crate/Sub' was left alone; Disco/ was kept because of cover.jpg; source roots are never removed.
- Every move is journaled the moment it lands.
- Undo restored all files to their exact original paths, recreated the pruned folders, restored fg_content paths, and is a safe no-op when run twice.
- Resume after cancel and after kill -9 completed with every file intact (80/80 sha match).
- Interrupt and Emergency Stop are visible during a run (r5_cancel_2_running.png).
- A live run without the archive is refused (_require_archive, cli.py:85-103, code).
- **Fix:** Keep these behaviours; add regression tests for the parallel-worker case.

## Chop Shop: Normalize Loudness

_Auditor: `tool-normalize`_

Round-3 live audit of Chop Shop "Normalize Loudness" (it runs GET /api/run/process?no_bpm=1&no_key=1&path=…&workers=N, i.e. cli.py process with --normalize-mode passive). Sandbox normalize-r3 on port 8115. I added 9 fully tagged bench files: MP3, FLAC 24-bit, WAV 24-bit, WAV 32-bit float, AIFF 16-bit with cover and Serato GEOB, untagged AIFF 24-bit, M4A, a no-headroom crest MP3 and a hot master. I also added 8 ten-minute MP3s so I could interrupt runs. I drove the real UI with Playwright (preview, confirm, run, Interrupt, Resume banner), took sha256/ffprobe/LUFS/tag snapshots before and after, tried the undo endpoints, re-ran the tool, cancelled during measure and during encode, killed the Flask server with kill -9 mid-run, and restarted.

Verdict: Normalize is the most destructive tool in the kit and it is not reversible. Originals are deleted as soon as the swap succeeds. There is no restore point, no per-file journal, and no undo path: the revert endpoint refuses 'normalize', 'tag_tracks' and 'process'. A search of the whole sandbox found 0 surviving copies of the 15 rewritten originals.

It also does damage beyond gain. WAV goes to 16-bit (24-bit and 32-bit float both). WAV and AIFF lose every ID3 tag, including cover art and Serato cue data. AIFF becomes little-endian AIFF-C 'sowt'. MP3 loses COMM and the Serato GEOB frame and is always re-encoded at 320k. 24-bit AIFF and every M4A always fail. Those failures are reported as "Tag Write Failures — BPM/key detection succeeded", and the offered "Retry with Force" overwrote BPM/key tags and never retried normalisation. The run summary says tracks "match the loudness target" when many land at -11.7 to -12.8 LUFS.

Interrupts are a second problem. Cancelling, or closing the window, during an encode leaves truncated tmpXXXX.mp3 files in the music folder; the next run scanned and processed them as tracks. Interrupted runs write no report and no journal row. Resume relies on localStorage only and skips the confirm dialog.

What works: no output was ever pushed above -1.0 dBTP, failed files were left byte-identical, re-running did not apply gain twice (0 re-encoded, hashes unchanged), FLAC kept its bit depth and cover, and master.db was never touched.

Harness note: restart.sh records the subshell PID, not the Python server's, so `kill -9 $(cat server.pid)` does not kill the server. My first 'kill -9' attempt was invalid; I redid it against the real PID (found via /proc/*/cwd).

**Coverage**

- LIVE (UI via Playwright, Chromium /opt/pw-browsers/chromium): Chop Shop > Normalize card. Added a folder, waited for the preview, accepted the confirm dialog, ran with 4 workers and with 2 workers, read the report modal, clicked Interrupt mid-run, reloaded after restart, clicked the Resume banner. No console errors or page errors in any run.
- LIVE before/after: sha256, ffprobe (codec, sample_fmt, bit depth, video/cover streams), ffmpeg loudnorm LUFS and true peak, and mutagen tag keys for all 22 files. Also listings of the FableGear Archive (Savepoints/Quarantine/Reports/Logs/Database/Checkpoints) and ~/.fablegear, plus GET /api/undo/operations|savepoints|trash|database/history|timeline. Files: audit/tool-normalize/r3_before.json, r3_after1.json, r3_after2.json, diff.py.
- LIVE undo: POST /api/undo/operations/revert and /preview with type tag_tracks / normalize / process were all refused. Searched the whole sandbox for any copy of the 15 original hashes (0 found).
- LIVE re-run on already-normalised files (double-gain check): 0 re-encoded, 16 skipped, every audio hash identical.
- LIVE preview: POST /api/normalize/preview on the library root (non-recursive, so 0 tracks found) and on a flat folder of the original bench files. Downloaded all 4 clips and measured them with ffmpeg loudnorm.
- LIVE cancel: three Interrupt-button runs. Two landed during measurement (no orphans); one landed during encode (2 truncated tmp*.mp3 left in the folder). Then restart.sh and a UI reload to see the resume banner, and Resume clicked (no confirm dialog shown).
- LIVE kill -9: SIGKILL of the real Flask server PID mid-encode. The in-flight swaps completed, the job then died silently, the UI showed 'Connection error'. Restart, then banner and /api/checkpoint/check checked.
- LIVE: the report modal's 'Retry N failed tracks with Force' path, via the exact POST /api/run/process-retry body the button sends (bpm/key passive from the Tag Tracks defaults), with tag and hash diffs before and after.
- LIVE: decoded-sample alignment of original vs normalised files (MP3 Bench Hot: lag 0, identical length; periodic synthetic tracks were inconclusive). AIFF form type read from the file header bytes. ffmpeg check: the AIFF muxer rejects pcm_s24le (exit 234) and accepts pcm_s24be.
- CODE: audio_processor.py:356-563 (measure, gain cap, codec args, _normalise_file), 1066-1103 (decision logic); cli.py:905-945 (_persist_process_results), 2650-2680 (checkpoint every 25 files), 2822-2852 (error categorisation); routes_tools.py:225-300 (/api/run/process), 330-352 (process-retry), 1248-1270 (cancel), 1301-1485 (preview); helpers.py:1136-1160 (SSE finally sends SIGTERM to the job's process group); routes_undo.py:344-360, 507-530; static/chop_shop/runners.js:228-250; static/chop_shop/pipeline.js:600-625 and 680-686; static/shared/modals.js:110-128; templates/partials/physical_library/normalizer.html.
- NOT TESTED: the Pipeline Wizard's normalize step; Tag Tracks normalize mode (E9 already covers it; the standalone path is the same code); aggressive/force_normalize mode; the disk-full branch; true peak = None (uncapped gain branch); the move(path->.bak)/move(tmp->path) window (too narrow to hit, so only inferred).
- NOT TESTABLE here: real CDJ or Rekordbox playback of AIFF-C 'sowt' or re-encoded MP3s; whether Rekordbox notices the changed audio or stale analysis/auto-gain; macOS WKWebView and APFS. fpcalc is absent but not used by Normalize.

**Per-tool safety matrix**

### Normalize Loudness (step-normalize -> /api/run/process normalize_mode=passive)

| Check | Result |
|---|---|
| R — Report | PARTIAL. On completion only: Archive/Reports/Normalize/normalize_<ts>.txt (784 bytes), linked from the report modal. It holds counts and errors only: no list of changed files, gains, before/after LUFS or originals. Logs/Normalize is always empty. Cancel or crash: no report (5 interrupted runs, 0 reports). Normalisation failures are mislabelled 'Tag Write Failures — BPM/key detection succeeded'. |
| M — Revert marker | NONE. No savepoint, trash or op-journal entry is made before mutating. The only row written is one aggregate fg_processing_log row (operation_type 'tag_tracks', in ~/.fablegear/fablegear.db) after a run completes, with no per-file data and no originals. The single savepoint present (master.backup_…053432) is a master.db copy made at page load, unrelated to Normalize. |
| U — Undo | NONE. .bak is unlinked right after the swap (audio_processor.py:543-546). /api/undo/operations/revert refuses type normalize/tag_tracks/process: "'normalize' operations cannot be reverted by moving files". 0 copies of the 15 originals exist anywhere. Revert-twice is N/A. |
| C — Cancel / interrupt | UNSAFE. Interrupt sends SIGTERM to the process group; there is no handler, so no cleanup runs. Cancel during encode left 2 truncated tmpXXXX.mp3 (7.8 MB, 194 s of 600 s) in the music folder. Closing or reloading the window does the same (SSE finally, helpers.py:1154-1160). No partial report, checkpoint or journal row. Files already swapped stay rewritten with no record. kill -9 of the server: in-flight swaps completed, then the job died silently. |
| S — Resume after restart | WEAK. The banner comes from localStorage rb_ckpt_normalize {paths, workers, ts} only. /api/checkpoint/check gives exists:false and the checkpoints dir is never created (saved every 25 files, never on SIGTERM). The banner shows age and path but no done/remaining, config or restore point. Resume skips the confirm dialog. Done files are skipped only by chance, because their loudness re-measures in tolerance or with no headroom. Orphan tmp files are processed as tracks. Changed inputs are not detected. |
| D — Dry run / preview | NO dry-run. The preview player is the only look-ahead. It is non-recursive (found 0 tracks in the real library root) and uses ffmpeg loudnorm (a dynamic limiter, TP -1.5) on a 10 s clip labelled -8 LUFS: measured -10.4 LUFS / -0.86 dBTP, while the real tool produced -11.8 LUFS for that file. |
| B — Boundary | Does not write master.db (sha unchanged). Writes FableGear DB ~/.fablegear/fablegear.db fg_processing_log (mislabelled 'tag_tracks', not revertable), ~/rekordbox-toolkit/scan_index.json, and <music folder>/.fablegear_state.json. Rekordbox analysis is left stale (inferred). The report's Retry-with-Force button turns a failed normalize into BPM/key tag writes on the same files. |

_Evidence:_ audit/tool-normalize/r3_before.json, r3_after1.json, r3_after2.json; out3/r3_main_log.txt, r3_cancel_log.txt, r3_cancel3_log.txt, r3_realkill_log.txt, retry_sse.txt; screens/tool-normalize/r3_main_3_done.png, r3_restart_after_kill_0_open.png, r3_restart_after_realkill_0_open.png

### Normalize loudness preview (/api/normalize/preview*)

| Check | Result |
|---|---|
| R — Report | N/A (status JSON only) |
| M — Revert marker | N/A (read-only on sources) |
| U — Undo | N/A. Writes clips to ~/.fablegear/previews (20 files after 5 previews, deleted only by TTL). |
| C — Cancel / interrupt | No cancel. It runs on a daemon thread. |
| S — Resume after restart | In-memory jobs are lost on restart, which is harmless. |
| D — Dry run / preview | It is the preview, but it misrepresents the tool: a different algorithm (loudnorm vs capped static gain), a hard-coded '-8.0' label, and non-recursive folder listing ('Need at least 2 tracks >= 2 min (found 0)' on the configured library root). |
| B — Boundary | No DB writes. |

_Evidence:_ out3/preview/{q,l}_{orig,norm}.mp3 measured: q_norm -10.41 LUFS / TP -0.86; real tool output for the same file -11.79 LUFS; routes_tools.py:1351-1361, 1367-1371

**Findings**

#### [CRITICAL · demonstrated] Normalize permanently rewrites audio with no restore point and no undo; originals are deleted immediately

- **ID:** `tool-normalize-no-restore-no-undo` · **Area:** undo-revert · **Tool/surface:** Normalize Loudness
- **Expected:** Before the first rewrite, move or hardlink each original into Archive/Trash/<job-id>/ (same-volume rename, so no extra space until purge). Journal per-file rows: path, original sha, new sha, gain, LUFS before/after. Show the job in the undo timeline as 'revert to before Normalize at <time>'. Restore should put originals back byte-identically.
- **Actual:** The originals are gone the moment each swap succeeds. Nothing the user can find identifies or restores them.
- **Evidence:** Main UI run (out3/r3_main_log.txt) rewrote 15 files. Diff r3_before.json vs r3_after1.json: no new entries in Savepoints, Quarantine or trash, and /api/undo/operations {sessions:[]}, /trash [], /timeline [] (E1), /database/history [] are all unchanged. POST /api/undo/operations/revert {type:'normalize'|'tag_tracks'|'process', first_id:1, last_id:1} returns "'normalize' operations cannot be reverted by moving files" (the same message comes back for each type). Hashing every file in the sandbox found 0 copies of any of the 15 original sha256s. Code: audio_processor.py:543-546 does shutil.move(path->.bak), move(tmp->path), bak.unlink(). routes_undo.py:344-360 whitelists only organize/rename/novelty_copy/convert/relocate. The UI confirm text only promises that the .bak exists 'during the operation'. The single savepoint master.backup_20261006_053432 is a master.db copy taken at page load, unrelated.
- **Fix:** In _normalise_file, replace bak.unlink() with a move into an archive trash folder keyed by job id. Write fg_processing_log rows with operation_type='normalize' per file (src, archived_original, sha_before, sha_after, gain_db, lufs_before, lufs_after). Add 'normalize' to the revert whitelist, with a plan that verifies current sha == sha_after before swapping back and is idempotent when run twice. Purge the trash only on explicit user action or by age.

#### [CRITICAL · demonstrated] WAV and AIFF lose every ID3 tag (title, artist, BPM, key, cover, Serato cues) on the standalone Normalize path

- **ID:** `tool-normalize-lossless-tags-stripped` · **Area:** chop-shop-tool · **Tool/surface:** Normalize Loudness
- **Expected:** Tags and cover art are carried over unchanged, as the card implies ('re-encoded losslessly'). Or the tool copies the tag block back with mutagen after the swap and checks it.
- **Actual:** All metadata is silently destroyed, and the original is deleted (see no-restore finding), so the loss is permanent.
- **Evidence:** r3 diff. 'Bench Wav - Boost 24bit.wav' TAGS LOST ['APIC:Cover','COMM::eng','GEOB:Serato Markers2','TALB','TBPM','TCON','TDRC','TIT2','TKEY','TPE1','TXXX:FABLE_TEST'], video_streams 1->0. 'Bench Aiff16 - Cover.aiff' TAGS LOST ['APIC:Cover','GEOB:Serato Markers2','TBPM','TIT2','TPE1']; the header shows an ID3 chunk before and none after. The pre-existing 'Vera Lux - Pressure.aiff' and the WAVs also came out with no ID3 chunk. This is the same code path E9 found for Tag Tracks normalize mode; the standalone tool is equally affected. Cause: audio_processor.py:529-536 passes '-map_metadata 0 -id3v2_version 3', which writes no ID3 for the wav/aiff muxers (needs -write_id3v2 1), and does not map the cover stream.
- **Fix:** After ffmpeg writes the tmp file, copy the original's tag container verbatim with mutagen (ID3 for MP3/WAV/AIFF, Vorbis comments plus pictures for FLAC, MP4 atoms for M4A) before the swap. Verify by comparing the tag key sets of source and tmp, and refuse the swap on any mismatch.

#### [CRITICAL · demonstrated] WAV is always re-encoded as 16-bit: 24-bit and 32-bit float lose resolution, contradicting the 'same bit depth' claim

- **ID:** `tool-normalize-wav-bitdepth-downgrade` · **Area:** chop-shop-tool · **Tool/surface:** Normalize Loudness
- **Expected:** Output keeps the source sample format (s24le/s32le/f32le), or the tool refuses.
- **Actual:** Irreversible reduction to 16-bit (truncation after a gain change). The original is deleted.
- **Evidence:** r3 diff. 'Bench Wav - Boost 24bit.wav' codec pcm_s24le->pcm_s16le, bits_raw 24->None, size 35.7 MB->23.8 MB. 'Bench Wav32f - Float.wav' pcm_f32le->pcm_s16le, size 47.6 MB->23.8 MB. Code: audio_processor.py:450-452 returns ['-codec:a','pcm_s16le'] for every .wav. normalizer.html:18 tells the user 'AIFF/WAV -> re-encoded losslessly at the same bit depth as the original'. FLAC kept 24-bit (confirmed good).
- **Fix:** Choose the codec from sf.info(path).subtype for WAV as already done for AIFF: PCM_24->pcm_s24le, PCM_32->pcm_s32le, FLOAT->pcm_f32le, DOUBLE->pcm_f64le. Add a post-encode check that the output subtype equals the input subtype before swapping.

#### [HIGH · demonstrated] Every 24-bit AIFF and every M4A/AAC/ALAC file always fails normalisation

- **ID:** `tool-normalize-aiff24-m4a-always-fail` · **Area:** chop-shop-tool · **Tool/surface:** Normalize Loudness
- **Expected:** 24-bit AIFF, the most common lossless DJ format, normalises losslessly. M4A is either supported (re-encode AAC, or ALAC losslessly) or excluded up front with a clear 'unsupported format' message.
- **Actual:** These files can never be normalised. The failure is reported as a tag-write problem (see the retry finding).
- **Evidence:** Main run errors: 'Bench Aiff24 - No Tags.aiff', 'Bench Mid - Capped 24bit.aiff', 'Bench M4a - Boost.m4a', 'Marlo & The Tens - Glitterball.m4a', all 'normalisation failed'. The log shows '[out#0/aiff] Could not write header (incorrect codec parameters ?)' and "Filtergraph 'volume=7.5900dB' was specified, but codec copy was selected". Standalone check: ffmpeg -c:a pcm_s24le to .aiff exits 234; pcm_s24be exits 0. Code: audio_processor.py:446 picks the little-endian pcm_s24le for AIFF (the AIFF muxer only takes big-endian 24-bit), and :455-456 returns ['-codec:a','copy'] for anything else while '-af volume' is set. Good: the originals stayed byte-identical (sha 'same').
- **Fix:** AIFF: use pcm_s24be / pcm_s16be (big-endian; this also fixes the AIFC conversion below). M4A: detect the codec. For ALAC use -c:a alac; for AAC either re-encode at the source bitrate with a warning or skip with an explicit 'unsupported for in-place normalize' entry. Never pair '-codec:a copy' with an audio filter.

#### [HIGH · demonstrated] Normalize failures are labelled 'Tag Write Failures'; the offered 'Retry with Force' rewrites BPM/key tags and never retries normalisation

- **ID:** `tool-normalize-retry-force-wrong-remedy` · **Area:** chop-shop-tool · **Tool/surface:** Normalize Loudness -> report modal 'Retry N failed tracks with Force'
- **Expected:** Normalisation failures get their own category with the real cause (unsupported codec / ffmpeg error). Any retry re-attempts normalisation and never writes BPM/key.
- **Actual:** The user is told BPM/key detection succeeded (it never ran). The one-click remedy mutates unrelated metadata with detected values, here overwriting an existing BPM, with no undo.
- **Evidence:** Report text (normalize_20261006_053445.txt and screens/tool-normalize/r3_main_3_done.png): 'Tag Write Failures (4) — BPM/key detection succeeded, but writing the tag to the file failed … re-run with Force tag-overwrite on', with a 'Retry 4 failed tracks with Force' button. I sent the exact request the button sends (POST /api/run/process-retry {paths, bpm_mode:'passive', key_mode:'passive'}); output in out3/retry_sse.txt. Result: 'Bench M4a - Boost.m4a' tmpo 124->117; 'Glitterball.m4a' gained BPM 162 / key 5A; the untagged 24-bit AIFF gained TBPM 123 / TKEY 11B. 3 of 4 hashes changed and no file was normalised (routes_tools.py:345-352 hardcodes --no-normalize --force). Code: cli.py:2822-2852 puts 'normalisation failed' into tag_fail_results; static/shared/modals.js:110-128 builds the retry button.
- **Fix:** Add a 'normalize_failed' bucket to FABLEGEAR_ERROR_SUMMARY and the report, carrying the ffmpeg stderr tail. Hide 'Retry with Force' for runs where BPM and key were off. If a retry is offered for normalize failures, route it to /api/run/process with normalize only.

#### [HIGH · demonstrated] Interrupt or window close during encode leaves truncated tmpXXXX.mp3 files in the library, which later runs treat as real tracks

- **ID:** `tool-normalize-cancel-orphan-tmp-tracks` · **Area:** chop-shop-tool · **Tool/surface:** Normalize Loudness
- **Expected:** An interrupted run leaves no stray files in the music library. Temp output goes to a hidden or ignored name (e.g. .fg-normalize-*.part) or the archive's work dir, and cancel stops between files after cleanup.
- **Actual:** Truncated audio files with a real .mp3 extension stay in the library, then get scanned, normalised, and could be imported or deduped as tracks.
- **Evidence:** r3_cancel3: Interrupt clicked at 58 s while ffmpeg was encoding Mix 7/8. Log: '✗ Exited with code -15'. Long/ was left with tmp9rnxqipi.mp3 (7,780,438 B) and tmpnqxch6fi.mp3 (7,807,605 B), mode 0600; ffprobe duration 194.5 s of a 600 s source; no cli.py remained to clean up. scanner.scan_directory(Long) returned 10 tracks including both tmp files. The next normalize run processed them: '[6/10] tmp9rnxqipi.mp3' (out3/r3_realkill_log.txt). Cause: audio_processor.py:509-512 runs mkstemp(dir=path.parent) inside the user's folder; cli.py has no SIGTERM handler, so the finally block never runs; /api/cancel (routes_tools.py:1248-1250) and the SSE generator's finally on client disconnect (helpers.py:1154-1160) both SIGTERM the process group.
- **Fix:** Install a SIGTERM handler in cli.py that sets a stop flag; workers finish or abort the current file and unlink tmp. Name temps with a dot prefix and a .part suffix that scanner.py ignores. On startup, sweep known temp patterns under the roots. Also: closing the window should not silently kill a destructive job; detach it, or warn before unload.

#### [HIGH · demonstrated] Cancelled or crashed runs leave rewritten files with no report, journal or checkpoint naming them

- **ID:** `tool-normalize-interrupt-no-record` · **Area:** session-resume · **Tool/surface:** Normalize Loudness
- **Expected:** Every swapped file is journaled as it happens, so an interrupted run can still say which files were rewritten and where their originals are. A partial report is written on cancel.
- **Actual:** The user cannot tell which files an interrupted run changed.
- **Evidence:** Five interrupted runs (r3_cancel, r3_resume_kill, r3_cancel2, r3_cancel3, r3_realkill) rewrote Long Mix 2, 3, 4, 5, 7 and 8 (24,002,479 B, mode 0600, new mtimes). Reports/Normalize still holds only the 2 completed-run reports. fg_processing_log gained no row for any of them (only rows from completed runs plus the retry). ~/.fablegear/checkpoints was never created; /api/checkpoint/check?tool=process&path=…/Long returns {exists:false}. In r3_cancel the UI log shows 'Normalising Long Mix 3…' with no completion line, yet the file was swapped. kill -9 of the real server PID (r3_realkill): in-flight swaps of Mix 7/8 landed at 05:49:53-54 as the server died, then the job died silently; UI: 'Connection error — check the server is running.' Code: cli.py:2668-2674 saves a checkpoint every 25 results only; _persist_process_results (cli.py:905-945) and the report run only after a root completes.
- **Fix:** Journal per file inside the worker, right after the swap (the same row the undo needs). Flush the checkpoint per file for destructive tools. Emit a partial report from a SIGTERM/stop-flag path.

#### [HIGH · demonstrated] MP3 rewrite drops COMM and Serato GEOB frames, re-encodes cover art, and upsizes every MP3 to 320k

- **ID:** `tool-normalize-mp3-tags-reencode` · **Area:** chop-shop-tool · **Tool/surface:** Normalize Loudness
- **Expected:** Cue/beatgrid frames (Serato GEOB, etc.), comments and cover art survive unchanged. Bitrate stays at or near the source's (a 128k source gains nothing at 320k).
- **Actual:** DJ cue data and comments are lost permanently, cover art is re-encoded, and files bloat with an extra lossy generation.
- **Evidence:** 'Bench Hot - Loud Master.mp3': TAGS LOST ['COMM::eng','GEOB:Serato Markers2'], tags added ['TXXX:comment'], APIC changed (50955->69901 chars repr). 'Nula - Night Drive (copy).mp3' 128k->320k (129,250->322,974 B); 'Soft Hands - Whisper.mp3' 192k->320k. Code: audio_processor.py:441-442 always uses libmp3lame -b:a 320k; :529-536 uses -map_metadata 0, which does not carry GEOB/private frames and maps COMM to TXXX. Good: decoded length identical and cross-correlation lag 0 on Bench Hot, so no sample offset observed in ffmpeg decode.
- **Fix:** After the encode, copy the full ID3 frame set from the original with mutagen (ID3(orig).save(tmp)). Match the source bitrate (or VBR quality). Consider skipping MP3 attenuations under ~1 dB, since a lossy generation costs more than the level change.

#### [MEDIUM · demonstrated] Summary and log claim '-> -8.0' / 'match the loudness target' when capped files land 3-5 dB short; the -8 'DJ standard' contradicts the project's own findings

- **ID:** `tool-normalize-claims-target-met` · **Area:** chop-shop-tool · **Tool/surface:** Normalize Loudness
- **Expected:** Each file reports its achieved LUFS and whether it was capped. The summary separates 'reached target' from 'raised but capped by true peak'. The card's target guidance matches the project's own findings.
- **Actual:** Users are told files match the target when they don't.
- **Evidence:** Log: 'Normalising Bench Flac - Quiet 24bit.flac: -31.3 LUFS -> -8.0 (gain: +19.5 dB)'. Measured result -11.79 LUFS. WAV and AIFF16 -11.66, kick_01 -8.86, Long Mix -12.6/-12.8. Summary: '15 tracks were re-encoded to match the loudness target.' The report contains no per-file achieved loudness. normalizer.html:7 says 'Target is −8.0 LUFS — the DJ standard'. docs/loudness_findings.md §3/§6 measured that 98% of real tracks cannot reach -8 with gain alone and recommends -11; that is not applied and not mentioned in the UI.
- **Fix:** Log the measured after-LUFS (already computed in result.loudness_after) instead of TARGET_LUFS. Add 'capped' and 'reached' counts and a per-file table to the report. Revisit the -8 default per loudness_findings.md.

#### [MEDIUM · demonstrated] Resume after cancel or crash is a localStorage banner with no starting-point reference; Resume skips the destructive-op confirm

- **ID:** `tool-normalize-resume-localstorage-only` · **Area:** session-resume · **Tool/surface:** Normalize Loudness
- **Expected:** Resume carries a server-side record: when the run started, roots and config, done vs remaining, files changed since, and the restore point. Resume re-confirms a destructive operation.
- **Actual:** A browser-local path and timestamp only. It is lost if WebView storage is cleared, and it re-launches a destructive job without confirmation.
- **Evidence:** After cancel and restart.sh, and again after kill -9 and restart: the Normalize card shows '⏸ Interrupted run — 2m ago | <path> | Resume | Start Fresh' (screens/tool-normalize/r3_restart_after_cancel_0_open.png, r3_restart_after_kill_0_open.png, r3_restart_after_realkill_0_open.png). Its source is localStorage rb_ckpt_normalize {paths, workers, ts}; the server says exists:false (relates to E5). It shows no done/remaining, no config, and no restore point (none exists). Clicking Resume started a run with dialogs: [] (pipeline.js:680-686 calls runNormalize(true), which skips confirm). The Resume tooltip promises 'files already done are skipped', but the run re-measured all 8 files and skipped done ones only because they were now in tolerance or had no headroom. Changed inputs are not detected: the orphan tmp files were picked up as new tracks.
- **Fix:** Build the banner from a server-side job journal (see the interrupt finding) and show 'N of M rewritten, originals in Trash/<job>' plus a 'Revert this run' action. Keep the confirm on Resume. Record input mtimes/sizes in the checkpoint to detect changes.

#### [MEDIUM · demonstrated] Loudness preview fails on a normal library and, when it works, demonstrates a different algorithm than the tool runs

- **ID:** `tool-normalize-preview-misleading` · **Area:** chop-shop-tool · **Tool/surface:** Normalize preview (/api/normalize/preview)
- **Expected:** The preview recurses like the tool's scanner and renders exactly what _normalise_file would produce (same capped gain), labelled with the predicted LUFS and a 'capped/no headroom' note.
- **Actual:** It either shows nothing or promises an outcome the tool can't deliver.
- **Evidence:** With the configured 'Music Library' root (tracks only in subfolders) the card shows 'Need at least 2 tracks ≥ 2 min (found 0).' (r3_main): routes_tools.py:1367-1371 uses folder.iterdir() with no recursion. On a flat folder of the original bench files, the quietest clip 'Normalized −8 LUFS' measured -10.41 LUFS and TP -0.86 dBTP, which exceeds the tool's -1.0 dBTP ceiling. The real tool produced -11.79 LUFS for that same file. The preview uses loudnorm=I=-8:TP=-1.5:LRA=11, a dynamic limiter (routes_tools.py:1351-1361), while the tool applies static gain capped by true peak. The label is hard-coded to lufs -8.0. Clips accumulate in ~/.fablegear/previews (20 files after 5 previews; TTL cleanup only).
- **Fix:** Reuse scanner.scan_directory for candidates. Compute gain with _capped_gain_db from a full-track _measure_lufs, then apply 'volume=<gain>dB' to the clip. Show predicted after-LUFS and whether it is capped.

#### [MEDIUM · demonstrated] 16-bit AIFF is silently converted to little-endian AIFF-C 'sowt'

- **ID:** `tool-normalize-aiff-to-aifc-sowt` · **Area:** chop-shop-tool · **Tool/surface:** Normalize Loudness
- **Expected:** Format preserved: plain big-endian AIFF.
- **Actual:** The container and encoding change, so the file is no longer what the user exported or bought.
- **Evidence:** Header bytes: original 'Bench Aiff16 - Cover.aiff' form 'AIFF'; after normalising, form 'AIFC' with compression 'sowt'. Same for 'Vera Lux - Pressure.aiff' (ffprobe pcm_s16be->pcm_s16le). Code: audio_processor.py:446 uses pcm_s16le. Whether older CDJs or Rekordbox play AIFF-C sowt was not testable here (speculative).
- **Fix:** Use pcm_s16be / pcm_s24be for .aif/.aiff, which also fixes the 24-bit failure.

#### [LOW · demonstrated] Every rewritten file's permissions change from 0644 to 0600

- **ID:** `tool-normalize-file-mode-0600` · **Area:** chop-shop-tool · **Tool/surface:** Normalize Loudness
- **Expected:** Original mode, owner and (optionally) times preserved.
- **Actual:** Restricted mode on every normalised track.
- **Evidence:** All 15 files rewritten in the main run, and the Long Mix files, went from 0644 to 0600. Cause: tempfile.mkstemp (audio_processor.py:509) creates the file 0600, and shutil.move keeps that mode. Impact on shared drives/NAS or another user account is speculative.
- **Fix:** shutil.copymode(original, tmp) before the swap, or os.chmod to the original st_mode.

#### [LOW · demonstrated] Normalize writes a mislabelled aggregate FableGear DB row and state files; Rekordbox analysis is left stale with no warning

- **ID:** `tool-normalize-boundary-db-writes` · **Area:** boundary · **Tool/surface:** Normalize Loudness
- **Expected:** The DB write is labelled 'normalize', carries per-file rows usable by undo, and is covered by the same revert marker. The UI suggests re-analysing changed tracks in Rekordbox.
- **Actual:** A DB-layer side effect of a file tool that has no revert marker, plus no re-analysis guidance.
- **Evidence:** master.db (device and local) sha unchanged, so Normalize does not write the Rekordbox DB. ~/.fablegear/fablegear.db fg_processing_log gained (1,'tag_tracks',…,'{"files_processed": 20, … "normalized": 15, "errors": 4}') per completed run, with no per-file data and no revert cover (cli.py:905-945). The drive-side Archive/Database/fablegear.db was unchanged, so the two archives diverge. New files: ~/rekordbox-toolkit/scan_index.json and <music folder>/.fablegear_state.json (records exit_code -15 for cancels). Inferred, not testable: Rekordbox's stored analysis (waveform, auto-gain, file size) for re-encoded tracks goes stale and the UI does not suggest re-analysis.
- **Fix:** Fold into the per-file normalize journal (no-restore finding). Write to the active archive DB consistently. After a run, list the changed tracks that are present in Rekordbox and advise re-analysis.

#### [LOW · demonstrated] One-shot samples in Sample Packs are normalised like tracks

- **ID:** `tool-normalize-sample-packs-included` · **Area:** chop-shop-tool · **Tool/surface:** Normalize Loudness
- **Expected:** Short one-shots and sample-pack folders are excluded, or at least listed for confirmation.
- **Actual:** Samples are boosted and rewritten in place.
- **Evidence:** 'Sample Packs/kick_01.wav' went from -33.4 LUFS to -8.86 LUFS (+24.5 dB), with tags created ('Created new tag block for tagless file'). There is no exclusion when the library root is chosen, while the Rekordbox fixture and other tools treat Sample Packs as non-tracks.
- **Fix:** Skip files under a minimum duration (the preview already uses 2 min) or folders matching the sample-pack exclusions used elsewhere.

#### [INFO · demonstrated] Confirmed good: true-peak cap holds, failures leave originals intact, re-running does not apply gain twice

- **ID:** `tool-normalize-good-peak-and-idempotence` · **Area:** chop-shop-tool · **Tool/surface:** Normalize Loudness
- **Expected:** n/a
- **Actual:** n/a
- **Evidence:** No output above -1.0 dBTP (FLAC/WAV/AIFF exactly -1.0; MP3 at or below -2.09). Hot master attenuated -5.55 to -8.00 LUFS (TP -0.27 to -2.67). The no-headroom crest MP3 was skipped with a clear 'Needs a limiter' log line. Failed M4A and 24-bit AIFF files stayed byte-identical. No .bak was ever left behind. Re-run on the normalised library: '0 tracks were re-encoded … 16 were already at the right level' and every audio sha was unchanged (r3_after1 vs r3_after2). FLAC kept 24-bit and its cover stream. File extensions are preserved.
- **Fix:** Keep these properties when adding the journal/trash and the codec fixes. Add regression tests for re-run idempotence.

#### [INFO · demonstrated] Harness: restart.sh records the subshell PID, so 'kill -9 $(cat server.pid)' does not kill the Flask server

- **ID:** `tool-normalize-harness-restart-pid` · **Area:** other · **Tool/surface:** audit harness (sbx-*/restart.sh)
- **Expected:** server.pid holds the python PID.
- **Actual:** Other auditors' 'kill -9 after restart' tests may not have killed the server.
- **Evidence:** After restart.sh, server.pid=17793 but the Flask process was 17794, a child of the `cd … && nohup python app.py &` subshell. Killing 17793 left 17794 serving on 8115, and the next restart.sh failed to bind (new pid 19174 died) while the old server kept running. I redid the crash test against the real PID found via /proc/*/cwd. The initial launch_sandbox.sh PID is correct; only restart.sh is affected.
- **Fix:** In restart.sh, use `( cd "$DST/app" && exec env HOME=… nohup python app.py ) &` or `cd` first and then background python directly, so $! is the python PID.

## Chop Shop: Convert Format

_Auditor: `tool-convert-c`_

Ran Convert Format (#step-convert, /api/run/convert, then cli.py cmd_convert, then audio_processor._convert_file) live in sandbox convert-c on port 6116. I drove it through the real Chop Shop UI with Playwright. Runs covered: whole library to AIFF and to MP3 with 4 workers, a 40-track FLAC batch with Interrupt, kill -9 of the server then Resume, and a format switch between sessions. Before/after evidence: sha256 of every file, mutagen/ffprobe tags, both Rekordbox master.db files (decrypted), fablegear.db rows and every /api/undo/* endpoint.

Verdict: this is the most dangerous tool in the Chop Shop. Every conversion permanently deletes the original (the .bak is unlinked). The card says originals go to Quarantine and that metadata is preserved; both claims are false.
- Converting to AIFF wipes every ID3 tag (BPM, key, artist, album, genre, label, cover art).
- Other target formats remap BPM, key, comment and label into non-standard frames, and drop M4A BPM entirely.
- With the default 4 workers, two sources with the same stem race. One lossless original is silently destroyed and the report says "No errors ... nothing lost" (reproduced 3 out of 3).

Recovery and resume:
- There is no restore point, and the undo API refuses convert. The Undo Wizard Savepoints and Trash tabs throw a ReferenceError, so savepoint and trash restore can't be reached from the UI at all.
- Cancel is a bare SIGTERM. It leaves partial tmpXXXX.mp3 files in the music folder (the library sync later imports one as a real track). It writes no checkpoint and no report.
- Resume is a localStorage-only banner with no format, progress or restore point.

Boundary: the tool relinks the FableGear DB (fg_content) with no revert marker. It never touches Rekordbox master.db or the device DB, so every converted track breaks there (app audit path integrity went from 90.9% to 18.2%).

Confirmed good: the path guard, safe skip of same-stem collisions when running sequentially, the pipeline refusing a dry-run convert, cleanup of temp files when ffmpeg fails, per-file journal rows, and the Operations tab honestly labelling convert as not reversible.

**Coverage**

- LIVE via real UI (Playwright headless Chromium, 1440x900): Chop Shop > Convert card; pre-filled pills observed; ran AIFF/4 workers (r1), MP3/4 workers (r2), batch MP3/1 worker + Interrupt button (r3), Resume banner (r5, r8), kill -9 server mid-run (r6, r9), fresh-profile banner check (r10), format switch to AIFF (r11), bad path (r14) and home-folder guard (r15); Undo Wizard all 5 tabs (undo_ui.py r1). Scripts: <SP>/audit/tool-convert-c/{ui_run.py,undo_ui.py,snap.py,matrix.py,race.sh,reset.sh,reset2.sh,explainer.py}; SSE captures in <SP>/audit/tool-convert-c/out/; state snapshots in <SP>/audit/tool-convert-c/snaps/ (s1_before, s2_after_aiff, s3_before_mp3, s4_after_mp3, s5_before_cancel, s6_after_cancel, s7_before_kill, s8_before_switch); screenshots in /home/user/FableGear/docs/audits/2026-10-05/screens/tool-convert-c/. <SP> = /tmp/claude-0/-home-user-FableGear/9074577f-2e6a-5e16-bdce-ef5b26f1b16b/scratchpad
- LIVE via the same endpoint the UI calls (curl/urllib on /api/run/convert): race reproduction 3x with workers=4 and 1x with workers=1 (race.sh); full tag carry-over matrix of 5 source formats to 4 targets (matrix.py, out/tag_matrix.json); 24-bit FLAC/WAV to AIFF failure isolation; 4-worker cancel orphan count; /api/undo/operations/preview and revert x2; /api/checkpoint/check; /api/run/pipeline dry_run with a convert step; /api/library/db/sync after convert
- Sandbox prep: before the baseline I enriched the synthetic files' tags (TBPM/TKEY/COMM/TPUB/TCON/APIC on mp3, wav-ID3, aiff-ID3; BPM/INITIALKEY/COMMENT/LABEL/picture on flac; tmpo/initialkey/cmt/covr on m4a) so tag carry-over could be measured. I generated a 40 x 150 s pink-noise 24-bit FLAC 'Batch' folder to make runs slow enough to cancel or kill. The batch copy and app copy were deleted afterwards to save space. Regenerate with the ffmpeg anoisesrc loop: d=150, -c:a flac.
- Isolation note: midway I found that /api/cancel in ANY sandbox server SIGTERMs every cli.py under the shared app dir. Another auditor's (sbx-process-b) cancel at 18:56:50 killed my resumed run (exit -15). From then on I ran my server from a byte-identical rsync copy of the app (diff -rq clean) at <SP>/audit/tool-convert-c/appcopy (since deleted) so my cancels and kills could not hit other auditors' CLIs, or theirs mine. My one earlier UI cancel (18:54:18, run r3, from the shared app dir) may have SIGTERMed another auditor's cli.py running at that moment.
- Server killed at end. Port 6116 is down and no cli.py processes of mine remain.
- NOT testable here: Rekordbox-running guard (no Rekordbox process on Linux). CDJ/Rekordbox playback of the AIFF-C 'sowt' outputs (no hardware). WKWebView localStorage persistence semantics (Chromium storage_state used as a stand-in). AcoustID (fpcalc absent; not used by convert). The narrow crash window between moving the original to .bak and moving the temp file into place was not hit; inferred only.
- Inferred from code only (no live repro): no SIGTERM handler in cli.py; Emergency Stop (/api/cancel/force SIGKILL) would leave the same partial temp files; mp3-to-wav free-space preflight ratio (1.3x) underestimates; MCP does not expose convert (so the Timeline can never show it).

**Per-tool safety matrix**

### Convert Format (#step-convert; /api/run/convert -> cli.py convert -> audio_processor._convert_file)

| Check | Result |
|---|---|
| R — Report | PARTIAL.<br>- Success: yes. Writes Archive/Reports/Convert/convert_<ts>.txt (131 bytes, counts only: no per-file old/new paths, no note that originals were deleted). The path is shown in the report modal.<br>- ffmpeg failure: yes, but exit code stays 0 and the report blames 'corrupt or DRM-protected inputs ... not a FableGear failure' even for FableGear's own codec bug.<br>- Validation failure (bad path, guard refusal): no report.<br>- Cancel (SIGTERM) or server kill -9: no report and no convert_batch journal row.<br>- There is no Archive/Logs/Convert dir (config.py LOG_DIRS has no Convert). |
| M — Revert marker | NO.<br>- No master.db savepoint: /api/undo/savepoints unchanged before vs after.<br>- No copy of the originals: the .bak is unlinked at audio_processor.py:680; Quarantine and Trash stay empty.<br>- No fablegear.db snapshot and no DatabaseUndoManager transaction: /api/undo/database/history is [] after the run.<br>- Only per-file fg_processing_log 'convert' rows with metadata {from, format}. These are forensic, not restorable.<br>- The job never appears in /api/undo/timeline, which is [] after every run. |
| U — Undo | NO.<br>- POST /api/undo/operations/preview and /revert with type=convert both return HTTP 400 "'convert' operations cannot be reverted by moving files". Calling revert twice gives the same 400, so it is safe and idempotent, but nothing is restored.<br>- The Operations tab lists the session as 'not reversible (files were re-encoded)'.<br>- The originals are gone, so a byte-identical restore is impossible.<br>- The Savepoints and Trash tabs crash (undoLoadSavepoints / undoLoadTrash are undefined). |
| C — Cancel / interrupt | INCONSISTENT.<br>- The Interrupt button sends POST /api/cancel, which SIGTERMs the process group. The CLI has no handler and dies immediately (exit -15).<br>- In-flight ffmpeg outputs are left as tmpXXXXXXXX.<target-ext> in the music folder: 1 orphan with 1 worker, 4 with 4 workers. They are 0600 and carry the source tags. A later /api/library/db/sync imported one (54 s of a 150 s track) as a real track.<br>- No checkpoint is written (it only saves every 25 files), no report, no batch row.<br>- Files converted before the cancel have already lost their originals.<br>- The readout says 'COMPLETE'.<br>- kill -9 of the server: the detached CLI finished its current file, then died at the next stdout write (BrokenPipe). State was consistent: checkpoint saved at done=25, 31 journal rows, no report. |
| S — Resume after restart | PARTIAL / MISLEADING.<br>- After a restart, the card shows the localStorage banner 'Interrupted run - Nm ago' (age measured from the start) plus the paths, with Resume and Start Fresh.<br>- The banner has no format, no done/remaining and no restore point.<br>- It is missing in a fresh profile even though the server checkpoint (5b445ae9313b672a.json.gz, 25/40) exists.<br>- It appears for a run that failed validation and never touched a file.<br>- /api/checkpoint/check?tool=convert always returns exists:false, because the route hashes config={} while the CLI uses {format}.<br>- Resume re-runs the stored paths and format. Done work is skipped only because the rescan finds the new files already in the target format; checkpoint done_paths (deleted source paths) never match, despite the log line 'Resuming: 25/40 files already converted, skipping those'.<br>- Changing format between sessions orphans the old checkpoint and re-encodes the already-converted lossy MP3s (28 MP3 to AIFF, live). |
| D — Dry run / preview | NO. The card has no preview mode. A pipeline dry run containing a convert step is refused with HTTP 400 'has no preview mode' (good). Starting a run shows no confirm dialog: window.confirm was never called. |
| B — Boundary | CROSSES, NOT COVERED.<br>- The file tool writes the FableGear DB: relink_converted updates fg_content path, format, size and hash and clears the fingerprint; fg_processing_log gets rows. None of this has a revert marker. In the parallel race it leaves stale hashes and a row pointing at a deleted file.<br>- It never updates Rekordbox master.db or the device DB: 8 local and 4 device DjmdContent rows end up pointing at deleted files. The app's own audit went from 90.9% to 18.2% path integrity, with 9 'orphaned' new files.<br>- Rekordbox cues and playlists remain attached to dead rows.<br>- No Rekordbox-running guard on /api/run/convert or for convert in the pipeline. |

_Evidence:_ All demonstrated unless noted.
- Snapshot diffs: snaps/s1_before vs s2_after_aiff, s3_before_mp3 vs s4_after_mp3, s5_before_cancel vs s6_after_cancel.
- SSE captures: out/r3_cancel_sse.json, out/r6_kill9_sse.json, out/r8_resume_after_kill_sse.json, out/r11_switch_aiff_sse.json, out/r12_par_cancel_sse.txt.
- Screenshots in screens/tool-convert-c: r1_undo_*.png, r4_after_cancel_restart_0b_banner.png, r7_after_kill_restart_0b_banner.png, r6_kill9_2_server_killed.png, r3_cancel_3_done.png, r14b_badpath_reload_0_open.png.
- Code: routes_undo.py:318-321; audio_processor.py:601-603, 614, 670-680; cli.py:2957, 3010-3028, 3076-3077, 3100-3101; routes_tools.py:643-660, 846-878; static/chop_shop/pipeline.js:598-626.
- No SIGTERM handler in cli.py: inferred (grep).

**Findings**

#### [CRITICAL · demonstrated] Convert permanently deletes every original although the UI promises they are moved to Quarantine

- **ID:** `tool-convert-c-originals-destroyed` · **Area:** chop-shop-tool · **Tool/surface:** Convert Format
- **Expected:** Either originals are kept (moved to Archive/Quarantine/<run-id>/ with the original relative path) and restorable from the Undo Wizard, or the UI states plainly before the run that originals will be permanently deleted, with an explicit confirmation.
- **Actual:** Originals are unlinked right after each file converts. The UI explainer says the opposite. No confirmation, no preview, no restore point. Combined with the tag loss below, the user's source files and their metadata are unrecoverable.
- **Evidence:** 1. Configured sandbox. POST /api/library/db/sync, then snapshot snaps/s1_before.json.
2. UI: Chop Shop > Convert, target AIFF, 4 workers, Start Converting. No confirm dialog appeared (window.confirm never called).
3. After the run (snaps/s2_after_aiff.json) 9 originals are gone: Brick Wall - Clipper.mp3, Glitterball.m4a, Kaito_-_sunrise_FINAL_v2.wav, kick_01.wav and others.
4. 'FableGear Archive/Quarantine' is empty. /api/undo/trash is []. /api/undo/savepoints is unchanged (only the onboarding savepoint).
5. Same result for MP3 (snaps/s4_after_mp3.json).

Card text, screenshot screens/tool-convert-c/convert_card_explainers.png:
- templates/partials/physical_library/file_converter.html:27: 'After conversion, the original files are moved to the FableGear Archive Quarantine folder, so you can recover them if needed.'
- Line 6: 'Metadata is preserved.'

Code: audio_processor.py:670-680 moves the original to <name>.<ext>.bak, then bak.unlink(). The comment says '⚠ PERMANENT OPERATION'.
- **Fix:** In _convert_file, replace bak.unlink() with a move into QUARANTINE_DIR/Convert/<timestamp>/<path relative to the library root>. Journal that quarantine path in the 'convert' fg_processing_log row as metadata.original_kept_at. Add 'convert' to _REVERTIBLE in routes_undo.py with a revert action that moves the original back and deletes or quarantines the converted file. Until that ships, correct file_converter.html:6 and :27, and add a confirm modal: 'N files will be re-encoded and the originals permanently deleted'.

#### [CRITICAL · demonstrated] Converting to AIFF wipes all tags (BPM, key, artist, album, genre, label, artwork); a later library sync then erases BPM/key from the FableGear DB too

- **ID:** `tool-convert-c-aiff-tag-wipe` · **Area:** chop-shop-tool · **Tool/surface:** Convert Format
- **Expected:** 'All metadata (BPM, key ...) is copied to the new file' (file_converter.html:27). The AIFF output should carry an ID3 chunk equivalent to the source.
- **Actual:** The AIFF output has no ID3 tags. With the originals deleted, BPM, key, artist, album, genre, label and artwork are lost permanently. The next Record Room sync also erases the copy that only survived in fablegear.db. AIFF is also the pipeline's default convert format (static/chop_shop/pipeline.js:443-451, routes_tools.py:517).
- **Evidence:** UI run r1 (AIFF, 4 workers). Every new .aiff reads {'_tags': None} in mutagen (snaps/s2_after_aiff.json).

ffprobe on 'Loud/Brick Wall - Clipper.aiff' shows only title=Clipper and comment=... (AIFF NAME/ANNO chunks). Before, the source had TBPM 128, TKEY 8A, TPE1, TCON, TPUB, COMM and APIC.

Tag matrix (out/tag_matrix.json, matrix.py, through /api/run/convert): mp3, m4a and wav sources converted to AIFF all keep 0 of bpm/key/artist/album/genre/label/art.

Cascade:
1. After converting Loud/ to AIFF, fg_content row 9 still holds bpm=128, key=8A, genre=Techno.
2. POST /api/library/db/sync.
3. Row 9 now has bpm=None, key=None, genre=None.

Cause: audio_processor.py:638-644 runs ffmpeg -map_metadata 0 -id3v2_version 3. The AIFF muxer only writes ID3 with -write_id3v2 1, which is never passed.
- **Fix:** For AIFF pass -write_id3v2 1 -id3v2_version 3. Better: after ffmpeg, copy tags with mutagen from source to destination using an explicit mapping (TBPM/TKEY/COMM/TPUB/APIC, vorbis BPM/INITIALKEY/LABEL/COMMENT/picture, MP4 tmpo/----:initialkey/covr). Then verify by re-reading the output and refuse to delete or quarantine the original if BPM or key went missing. Add a regression test per source/target pair.

#### [CRITICAL · demonstrated] With the default 4 workers, two sources sharing a stem race to the same target; one lossless original is silently destroyed and the report says 'nothing lost'

- **ID:** `tool-convert-c-parallel-stem-race` · **Area:** chop-shop-tool · **Tool/surface:** Convert Format
- **Expected:** At most one source per target. Collisions are detected up front and skipped or renamed, the original is never lost, and the report lists each collision.
- **Actual:** The FLAC original is unlinked and its MP3 overwritten by the AIFF's MP3. The DB holds a stale hash and a dangling row. The user is told 'No errors' and 'nothing lost'.
- **Evidence:** The library has 'Techno/Vera Lux - Pressure.aiff' and 'Techno/Vera Lux - Pressure.flac'.

UI run r2: MP3, workers=4 (the UI default, labelled 'recommended').
- Both originals are removed. Only one 'Vera Lux - Pressure.mp3' exists, mode 0600, built from the AIFF (its COMM is 'peak time', not the FLAC's).
- SSE shows '✓ Vera Lux - Pressure.flac: Converted to mp3', then 'UNIQUE constraint failed: fg_content.file_path ... Archive update failed', then '✓ Vera Lux - Pressure.aiff: Converted to mp3'.
- The report says '5 of 11 files converted ... No errors' and '(nothing lost)'.
- fg_content row 3 (the old flac) is relinked to Pressure.mp3 with file_hash 8031f9047bf5 and size 282567. The real file is c83f9943d848, 282533 bytes.
- Row 2 still points at the deleted Pressure.aiff. Only 4 of 5 conversions are journalled.

race.sh reproduced it 3 out of 3 with workers=4. With workers=1: 'Vera Lux - Pressure.flac: Vera Lux - Pressure.mp3 already exists', and the FLAC is kept.

Code: audio_processor.py:601-603 checks new_path.exists(), then encodes, then shutil.move(tmp, new_path) at line 672, which overwrites. This is a TOCTOU race across the ThreadPoolExecutor at cli.py:3053.
- **Fix:** Before submitting jobs, group tracks by path.with_suffix(target_ext), compared case-insensitively. Skip and report every group with more than one member, and any target that already exists. In _convert_file, create the destination atomically, for example os.link(tmp, new_path) (fails if it exists) followed by unlinking the tmp, or os.open(new_path, O_CREAT|O_EXCL) as a reservation, instead of shutil.move. Count the archive-update failure as an error, not a success.

#### [HIGH · demonstrated] Cross-format conversion remaps BPM/key/comment/label into non-standard frames and drops M4A BPM and all WAV-target DJ tags

- **ID:** `tool-convert-c-tag-mapping-loss` · **Area:** chop-shop-tool · **Tool/surface:** Convert Format
- **Expected:** BPM, key, comment, label and artwork land in the frames Rekordbox and CDJs read (TBPM/TKEY/COMM/TPUB/APIC; vorbis BPM/INITIALKEY/COMMENT/LABEL plus a picture block).
- **Actual:** DJ-critical tags end up in frames DJ software ignores, or are dropped. The originals are deleted, so the loss is permanent.
- **Evidence:** out/tag_matrix.json, via /api/run/convert with workers=1:
- FLAC to MP3: 'TXXX:BPM', 'TXXX:INITIALKEY', 'TXXX:COMMENT', 'TXXX:LABEL'. No TBPM, TKEY, COMM or TPUB.
- MP3 or AIFF to FLAC: vorbis keys 'tbpm', 'tkey', 'publisher', 'description' instead of BPM, INITIALKEY, LABEL, COMMENT.
- M4A to MP3 or FLAC: tmpo (BPM) dropped entirely; key becomes TXXX:initialkey; junk TXXX major_brand, minor_version and compatible_brands added.
- Any source to WAV: only RIFF INFO title/artist/album/genre/comment; BPM, key, label and art lost. ffprobe on TagMatrix_wav/src_mp3.wav confirms.
- Any MP3 output: COMM becomes TXXX:comment.
- Only WAV-ID3 or AIFF-ID3 sources to MP3 keep TBPM/TKEY.

Code: audio_processor.py:638-644 relies on ffmpeg's -map_metadata 0 defaults.
- **Fix:** Do the tag copy with mutagen after ffmpeg, using an explicit cross-format mapping table, and verify it as described in the AIFF finding. Also pass -map 0:a (plus a handled attached picture) so stream selection is deterministic.

#### [HIGH · demonstrated] No restore point of any kind and no undo; convert runs never appear in the Undo Timeline

- **ID:** `tool-convert-c-no-restore-point` · **Area:** undo-revert · **Tool/surface:** Convert Format / Undo Wizard
- **Expected:** Before the first mutation, create a restore point referenced by the job: original files kept, plus a fablegear.db snapshot or transaction record. The Timeline should show 'Convert run at <time>: revert to before' and the revert should be byte-identical.
- **Actual:** Nothing to revert to. The Savepoints tab blurb says 'FableGear takes a snapshot of your database before every write operation', which is false for this tool.
- **Evidence:** Before and after every run:
- /api/undo/savepoints: identical (only the onboarding master.backup_20261005_184232_461711.db).
- /api/undo/trash: [].
- /api/undo/database/history: [].
- /api/undo/timeline: [].

/api/undo/operations shows a 'convert' session with "revertible": false.

POST /api/undo/operations/preview, and /revert twice, with {type: convert, first_id 5, last_id 13} each return HTTP 400 "'convert' operations cannot be reverted by moving files".

UI: screens/tool-convert-c/r1_undo_timeline.png shows 'No jobs found'. r1_undo_operations.png shows 'not reversible (files were re-encoded)'.

The Timeline filter offers 'Convert' (undo_wizard.html:30), but the Timeline reads job_dispatcher.get_history (routes_undo.py:40-48). Only MCP-dispatched jobs write there, and MCP does not expose convert.

Code: routes_undo.py:318-321 sets _REVERTIBLE without 'convert'.
- **Fix:** Quarantine originals (see the originals-destroyed finding) and record a run id in every 'convert' journal row. Log UI-run jobs (the _stream subprocesses) into the same history store the Timeline reads, with a pointer to the run's quarantine folder and to a pre-run fablegear.db copy. Implement a 'convert' revert: restore the original, relink fg_content to it, and remove or quarantine the output.

#### [HIGH · demonstrated] Undo Wizard Savepoints and Trash tabs throw ReferenceError, so savepoint and trash restore are unreachable from the UI

- **ID:** `tool-convert-c-undo-wizard-savepoints-trash-dead` · **Area:** undo-revert · **Tool/surface:** Undo Wizard
- **Expected:** Savepoints and Trash tabs list entries with Restore buttons wired to the existing endpoints.
- **Actual:** Both tabs are dead. The only UI path to roll back the Rekordbox DB or restore pruned files is gone. This blocks the undo step for every tool, not only convert.
- **Evidence:** undo_ui.py clicks #undo-tab-savepoints and gets PAGEERROR 'undoLoadSavepoints is not defined' (static/shared/undo.js:39). Line 40 calls undoLoadTrash, which is also undefined.

The tabs render only their blurbs, with no list and no buttons (screens/tool-convert-c/r1_undo_savepoints.png, r1_undo_trash.png).

No JS anywhere calls /api/undo/savepoint/restore or /api/undo/trash/restore (grep of static/ and templates/).

git: 494d3bc (2026-06-22) defined both functions; d24749c 'Update undo.js' (2026-06-25) removed them (301 to 146 lines). Still missing on 1840750.
- **Fix:** Restore undoLoadSavepoints and undoLoadTrash from 494d3bc (adapted to the current routes: /api/undo/savepoints, /api/undo/savepoint/restore, /api/undo/trash, /api/undo/trash/<folder>/files, /api/undo/trash/restore). Add a smoke test that clicks every Undo tab and fails on any pageerror.

#### [HIGH · demonstrated] Convert breaks every converted track in Rekordbox (local and device master.db) while silently relinking only the FableGear DB

- **ID:** `tool-convert-c-rekordbox-not-relinked` · **Area:** boundary · **Tool/surface:** Convert Format
- **Expected:** Either relink DjmdContent.FolderPath/FileType for each converted file in the Rekordbox DB (with a savepoint first, Rekordbox closed), or refuse and warn that converted tracks will go missing in Rekordbox. Never leave the two databases disagreeing silently.
- **Actual:** The FableGear DB says the track is the .aiff. Rekordbox and the USB device DB say the old file, which no longer exists. Cues and playlists in Rekordbox are orphaned. Nothing tells the user.
- **Evidence:** snaps/s2_after_aiff.json:
- rb_local: 11 rows, 9 point at missing files (before: 1, the fixture's Old Location row).
- rb_device: 4 of 4 point at deleted files.
- fg_content rows 1 and 4-11 are relinked to .aiff with processing_status 'relinked'.

The app's own Rekordbox audit after an AIFF run (Reports/Audit/audit_20261005_190726.txt) shows 'Path integrity: 18.2% (2/11)'. Before it was 90.9% (audit_20261005_184611.txt). It also shows 'Missing files: 9' and 'Orphaned - on disk but not in DB (9)', all the new .aiff files.

Code:
- cli.py:3010-3028 calls only _fg_archive.relink_converted and log_operation.
- Nothing opens master.db.
- /api/run/convert (routes_tools.py:643-660) has no _require_rb_closed.
- The pipeline's _WRITE_STEP_TYPES omits convert (routes_tools.py:380).

The explainer promises 'cue points, hot cues, loops' are kept. Those live in Rekordbox DjmdCue/ANLZ, which stay attached to the now-dead rows. Cue survival is inferred.
- **Fix:** Treat convert as a DB-touching operation:
1. Require Rekordbox closed.
2. Take a master.db savepoint and reference it in the run record.
3. After each file, update_content_path and FileType in a write_db session.
4. Journal it so revert can undo both.

At minimum, show a pre-run warning and a post-run 'N tracks need relocating in Rekordbox' with a link to Fix Paths.

#### [HIGH · demonstrated] Convert card is pre-filled with the whole music library and starts with no confirmation or preview; adding a subfolder converts the entire library

- **ID:** `tool-convert-c-prefilled-library-no-confirm` · **Area:** chop-shop-tool · **Tool/surface:** Convert Format
- **Expected:** An irreversible, file-destroying tool starts empty, or at least asks for confirmation with the resolved file count and scope. Nested or overlapping roots are deduplicated.
- **Actual:** A user who adds one folder silently converts and deletes originals across the entire library.
- **Evidence:** Fresh browser profile, open the Convert card. The pills already contain '<music_root>' (ui_run.py prints the pre-filled pill list; screenshot screens/tool-convert-c/r3_cancel_1_configured.png shows 'Music Library' plus 'Batch').

Run r3: the user typed only the Batch folder and clicked Start Converting.
- The CLI got both roots ('across 2 source folder(s)', 'Found 91 audio files' = 51 + 40, with overlapping roots double-counted).
- It converted 'Vera Lux - Pressure.aiff' and 'Sample Packs/kick_01.wav' outside Batch before the cancel landed.
- No confirm dialog (window.__confirms is []).

Code:
- static/record_room/usb_export.js:230-245 pre-fills 'convert-pills' with cfg.music_root on every load.
- static/chop_shop/runners.js:380-393 runs immediately.
- The card has no preview mode.
- **Fix:** Remove 'convert-pills' (and the other destructive tools) from the pre-fill list. Add a pre-run confirm modal showing roots, file count by source format, target format, 'originals will be <kept in Quarantine | deleted>' and the 'Rekordbox links will break' note. Collapse roots that are inside another selected root.

#### [MEDIUM · demonstrated] Interrupt leaves partial tmpXXXX.<target-ext> files in the music folder; library sync imports them as real tracks; no checkpoint or report on cancel

- **ID:** `tool-convert-c-cancel-orphans` · **Area:** chop-shop-tool · **Tool/surface:** Convert Format
- **Expected:** Cancel stops after the current file, or deletes in-flight temp outputs. It saves a checkpoint and writes a partial report.
- **Actual:** The process dies mid-encode and partial audio files stay among the user's tracks. Progress and the record of what was converted are lost.
- **Evidence:** Run r3 (UI Interrupt, workers=1, at done=5):
- SSE ends with {done: true, exit_code: -15} 1 s after the click.
- 'Music Library/Batch/tmpi_6855vc.mp3' is left behind: 2,158,897 bytes, mode 0600, tags 'Batch Track 40'.
- No ~/.fablegear/checkpoints/convert.
- No Archive/Reports/Convert.

r12: the same with workers=4 leaves 4 orphans: tmp_zf04j3l.mp3, tmpir6mvolr.mp3, tmpis_keeqg.mp3, tmpysvsccrj.mp3.

After POST /api/library/db/sync, fg_content has row 73 'tmpi_6855vc.mp3' with title 'Batch Track 40', duration 53.9 s (the real track is 150 s). It became a truncated duplicate in the Record Room.

Code:
- audio_processor.py:614 uses mkstemp(suffix=target_ext, dir=path.parent).
- Its finally block (694-696) never runs on SIGTERM because cli.py installs no signal handler.
- The scanner does not skip 'tmp*' names (config.py:184 SKIP_PREFIXES).

Good: on an ffmpeg failure the finally does clean up. The 24-bit failures left no tmp files.
- **Fix:** Install SIGTERM/SIGINT handlers in cli.py that set a cancel event. Have the convert loops check it between files, save the checkpoint, and emit a partial report. Name temp outputs with a skipped prefix ('.fg-convert-<uuid>.part' or 'INCOMPLETE~...') so scanners ignore them. On next start, sweep orphaned '.fg-convert-*' files. Run ffmpeg via Popen so the handler can terminate it and unlink the tmp.

#### [MEDIUM · demonstrated] Resume banner is localStorage-only and carries no format, progress or restore point; missing after storage loss, shown for runs that never started

- **ID:** `tool-convert-c-resume-banner-thin` · **Area:** session-resume · **Tool/surface:** Convert Format
- **Expected:** A server-side run record covering when it started, roots, format, done vs remaining, and the restore point to roll back to. It is surfaced regardless of browser storage and only for runs that actually mutated files.
- **Actual:** A browser-local hint with paths and age only. It disagrees with the server checkpoint in both directions.
- **Evidence:** After cancel plus server restart, the banner reads 'Interrupted run - 1m ago / <paths> / Resume / Start Fresh' (screens/tool-convert-c/r4_after_cancel_restart_0b_banner.png). After kill -9, see r7_after_kill_restart_0b_banner.png.
- No target format, no workers, no done/remaining. The server checkpoint knows 25/40, but the UI never reads it.
- No restore point.
- 'Nm ago' is measured from the run's start (ts set in runConvert), not from the interruption.

Fresh profile (r10) with server checkpoint 5b445ae9313b672a.json.gz present (25/40): banner count 0.

Bad path run (r14, 'PATH is not a directory', exit 1, nothing touched): on reload, the banner says 'Interrupted run - just now' (r14b_badpath_reload_0_open.png).

Code: static/chop_shop/pipeline.js:583-626 (localStorage rb_ckpt_convert only). runners.js:389-392 clears it only on exit 0.
- **Fix:** Drive the banner from the server: the CLI checkpoint plus a run record listing started_at, roots, config, done/remaining, restore point id and report path. Fetch it on card open. Clear it on validation failures (exit codes 1/2 before any mutation). Show the format and progress in the banner, plus a 'Roll back to before this run' action once convert has a restore point.

#### [MEDIUM · demonstrated] Convert checkpoint never actually skips work: done_paths store deleted source paths; saves only every 25 files; /api/checkpoint/check never finds it

- **ID:** `tool-convert-c-checkpoint-ineffective` · **Area:** session-resume · **Tool/surface:** Convert Format
- **Expected:** Resume skips by stable identity and reports cumulative progress. The pre-flight check finds the checkpoint.
- **Actual:** The checkpoint is cosmetic. Resume works only because conversion is idempotent per extension. Progress before the first 25-file boundary is never saved.
- **Evidence:** Run r6: kill -9 at done=30. The checkpoint has completed 25, total 40 and 25 done_paths (all .flac paths, now deleted). The journal has 31 'convert' rows.

Resume (r8):
- Log: 'Resuming: 25/40 files already converted, skipping those.'
- The per-checkpoint skip line ('N file(s) already converted per checkpoint') never appears.
- The report says '9 of 40 files converted ... 31 skipped - already MP3'. The skip came from the rescan seeing .mp3 files, not from the checkpoint.
- The first session's 31 conversions vanish from the final report.

/api/checkpoint/check?tool=convert&path=<Batch> returns {exists:false}, also with &format=mp3. The CLI key is 5b445ae9313b672a (config {format: mp3}); the route key is a050c3ab939a324b (config {}).

Code:
- cli.py:2957 passes {format}; routes_tools.py:846-878 builds config only for duplicates.
- cli.py:3036-3040 matches done_paths against scanned paths, which are the new extensions.
- cli.py:3076, 3100 save only when done % 25 == 0.
- **Fix:** Store both source and output paths, and on resume treat a file as done if its output exists and is journalled. Save after every file (the payload is small), or on the cancel handler. Include 'format' in api_checkpoint_check's config for tool=convert. Have the final report sum prior-session results from the checkpoint.

#### [MEDIUM · demonstrated] Changing the target format between sessions re-encodes already-converted lossy outputs and orphans the old checkpoint, without warning

- **ID:** `tool-convert-c-format-switch-reencode` · **Area:** session-resume · **Tool/surface:** Convert Format
- **Expected:** Detect that a previous convert run on these roots was interrupted with a different format. Warn, and offer to finish it or start fresh. Never re-encode files this tool produced in an earlier session.
- **Actual:** The original FLACs were deleted in session 1, and session 2 replaced the MP3s with AIFFs made from them. The result is a lossy generation inside a 'lossless' container, plus total tag loss.
- **Evidence:** 1. Batch of 40 24-bit FLACs, convert to MP3, kill -9 at done=27. Result: 28 mp3 + 12 flac; checkpoint 5b445ae9313b672a saved.
2. Restart, new profile, user picks AIFF for Batch (run r11).
3. SSE shows 28 '.mp3: Converted to aiff' lines: the 320k MP3s were re-encoded to 26 MB AIFF-C files with no tags.
4. All 12 remaining FLACs failed (24-bit AIFF bug).
5. Final folder: 28 aiff + 12 flac. The old MP3 checkpoint is still on disk, orphaned.
6. The report: '28 of 40 files converted to AIFF. 12 could not be converted - corrupt files or DRM-protected inputs'.
- **Fix:** Keep a per-root 'last convert run' record. On start, if a different-format run is unfinished on overlapping roots, block with a choice. Skip files whose fg_processing_log shows they were produced by convert, unless the user opts in. Warn on any lossy-to-lossless conversion.

#### [MEDIUM · demonstrated] Any 24-bit source fails to convert to AIFF (pcm_s24le in an AIFF muxer), and the report blames the user's files

- **ID:** `tool-convert-c-24bit-aiff-fail` · **Area:** chop-shop-tool · **Tool/surface:** Convert Format
- **Expected:** 24-bit sources convert to big-endian 24-bit AIFF (pcm_s24be).
- **Actual:** Every 24-bit WAV promo and 24-bit FLAC fails, and the error is attributed to the user's files.
- **Evidence:** FlacArtTest folder:
- flac24_art and flac24_noart: '✗ ffmpeg failed: Conversion failed!'
- flac16_art: '✓ Converted to aiff'

Wav24Test/promo_24bit.wav to AIFF: '✗ ffmpeg failed'. The server log shows '[out#0/aiff] Could not write header (incorrect codec parameters ?)' and maps 'flac -> pcm_s24le'.

Report: '1 could not be converted - corrupt files or DRM-protected inputs ... These are bad inputs, not a FableGear failure.' SSE exit_code is 0.

The 12 24-bit FLACs in run r11 failed the same way.

Code: audio_processor.py:625-628 chooses codec 'pcm_s24le' when '24' is in the subtype, else 'pcm_s16le'.
- **Fix:** Use pcm_s16be / pcm_s24be (and pcm_s32be when needed) for AIFF. Classify ffmpeg failures as 'conversion error' rather than 'bad input' unless a decode probe of the source also fails. Return a non-zero exit, or at least a warning state, when any file errored.

#### [MEDIUM · demonstrated] 24-bit sources converted to WAV are silently truncated to 16-bit while the UI calls WAV 'lossless'

- **ID:** `tool-convert-c-wav-bitdepth-truncation` · **Area:** chop-shop-tool · **Tool/surface:** Convert Format
- **Expected:** Preserve source bit depth (pcm_s24le for 24-bit, pcm_s32le/f32 as appropriate).
- **Actual:** Silent, irreversible bit-depth reduction.
- **Evidence:** TagMatrix_flac/src_flac.flac is PCM_24. After converting to WAV, soundfile reports TagMatrix_wav/src_flac.wav as 'WAV PCM_16'.

Code: audio_processor.py:633-634 always uses -codec:a pcm_s16le for WAV.

The card says 'WAV - lossless, uncompressed. Best for: archival, mastering'. The original is deleted afterwards.
- **Fix:** Mirror the AIFF logic, with correct endianness per container. Pick the codec from sf.info(...).subtype (PCM_16, PCM_24, PCM_32, FLOAT). Verify that the output subtype matches the source before the original is removed.

#### [MEDIUM · demonstrated] The file tool writes the FableGear DB (fg_content relink) with no revert marker, and can leave it inconsistent

- **ID:** `tool-convert-c-fgdb-write-unrevertible` · **Area:** boundary · **Tool/surface:** Convert Format
- **Expected:** DB-side effects of a file tool are covered by the same restore point as its file changes.
- **Actual:** The FableGear DB mutation is not revertible.
- **Evidence:** s1_before vs s2_after_aiff: fg_content rows 1 and 4-11 change file_path, format, file_size, file_hash and processing_status ('scanned' to 'relinked'), and acoustic_fingerprint is cleared.

/api/undo/database/history stays [] (relink_converted calls update_content directly, database.py:510-549 and 275-297; no DatabaseUndoManager record). No fablegear.db copy is taken for the run. Archive/Database/fablegear.db exists only as a one-time first-run seed from startup_sync_check (archive_sync.py:339-360), is not tied to the job, and has no restore UI.

In the race case, row 2 is left pointing at a deleted file and row 3 has the wrong hash (see the parallel-stem-race finding).
- **Fix:** Wrap the per-run relinks in a recorded transaction (DatabaseUndoManager), or snapshot fablegear.db at run start and reference it from the run record. Include fg_content in the convert revert.

#### [MEDIUM · demonstrated] /api/cancel (and /api/quit) SIGTERM every cli.py under the same install, including runs owned by another server instance

- **ID:** `tool-convert-c-cancel-kills-other-instances` · **Area:** other · **Tool/surface:** Interrupt / Emergency Stop
- **Expected:** Cancel targets only the subprocesses this server spawned, or explicitly listed orphans from its own registry.
- **Actual:** Any FableGear CLI process from the same install path is terminated. For convert this means mid-file death and orphaned temp files in someone else's run.
- **Evidence:** My resumed convert run (r5, port 6116) ended with exit_code -15 at 18:56:50. My server.log has no /api/cancel at that time. sbx-process-b/server.log:3030-3031 shows 'POST /api/cancel' at 18:56:50.

Code:
- helpers.py:348-386 (_list_orphaned_cli_pids) pgreps for '<REPO_ROOT>/cli.py' across all processes.
- terminate_managed_subprocesses(include_orphans=True) is called by /api/cancel (routes_tools.py:1250) and /api/quit (app.py:1749-1751).

After I moved my server to a separate copy of the app, no further cross-kills happened.

In production this means a UI Interrupt also kills cli.py jobs started by the MCP server (job_dispatcher) or by the user in a terminal.
- **Fix:** Scope orphan discovery to the server's own registry entries (owner_pid == os.getpid(), or a per-instance token passed through env and matched via /proc environ or ps). Never pgrep-kill by install path alone.

#### [LOW · demonstrated] 'AIFF' outputs are actually AIFF-C with little-endian 'sowt' PCM

- **ID:** `tool-convert-c-aifc-sowt` · **Area:** chop-shop-tool · **Tool/surface:** Convert Format
- **Expected:** Plain AIFF with big-endian PCM, the format Pioneer documents.
- **Actual:** AIFF-C/sowt.
- **Evidence:** The header of the converted 'Brick Wall - Clipper.aiff' reads 'FORM....AIFCFVER' (od -c), with codec_name=pcm_s16le per ffprobe. A native fixture AIFF reads 'FORM....AIFF'.

Code: audio_processor.py:628 sets pcm_s16le.

Whether Rekordbox, older CDJs or other players handle AIFC/sowt was not tested (no hardware), so the compatibility risk is speculative.
- **Fix:** Use pcm_s16be / pcm_s24be. This is the same fix as the 24-bit AIFF finding.

#### [LOW · demonstrated] Converted files are created with mode 0600 (owner-only) instead of the original's permissions

- **ID:** `tool-convert-c-file-mode-0600` · **Area:** chop-shop-tool · **Tool/surface:** Convert Format
- **Expected:** The output inherits the source file's mode (and ideally its ownership and xattrs).
- **Actual:** Owner-only files. Other macOS user accounts, shared or network library setups, or tools running as another user can't read them.
- **Evidence:** After run r2, find -printf '%m': every converted file is 600 (Glitterball.mp3, Kaito_-_sunrise_FINAL_v2.mp3, kick_01.mp3, Vera Lux - Pressure.mp3). Untouched originals are 644. The same holds for every .aiff and .wav output in the tag matrix.

Cause: tempfile.mkstemp (audio_processor.py:614) creates 0600, and shutil.move preserves it.
- **Fix:** Before the swap, shutil.copymode(path, tmp_path), or os.chmod(tmp_path, stat.S_IMODE(os.stat(path).st_mode)).

#### [LOW · demonstrated] The Chop Shop readout says 'COMPLETE' after a cancel or a server crash

- **ID:** `tool-convert-c-readout-complete-on-abort` · **Area:** chop-shop-tool · **Tool/surface:** Convert Format (scan readout)
- **Expected:** 'Interrupted after N of M (originals of those N already replaced)' or 'Server connection lost'.
- **Actual:** Success wording for aborted runs.
- **Evidence:** screens/tool-convert-c/r3_cancel_3_done.png: readout 'COMPLETE 5% complete, 5 done, 86 remaining', while the log says '✗ Exited with code -15'.

screens/tool-convert-c/r6_kill9_2_server_killed.png: 'COMPLETE 75%', while the log says 'Connection error - check the server is running.'

The Interrupt and Emergency Stop buttons in the tool panel stay visible after exit.

Code: static/shared/scan_bar.js:55-65 finishScanBar calls _chopReadoutFinish (line 116-121), which always sets 'Complete'.
- **Fix:** Pass the exit code or error state into finishScanBar / _chopReadoutFinish and render Interrupted, Failed or Connection lost states. Hide the in-tool Interrupt and Emergency Stop buttons on exit.

#### [LOW · demonstrated] Convert report is counts-only, misclassifies collisions and failures, and omits what the user most needs to know

- **ID:** `tool-convert-c-report-thin` · **Area:** chop-shop-tool · **Tool/surface:** Convert Format
- **Expected:** A per-file manifest (source, output, status, reason, original kept at ...) usable as the run's audit trail and revert input. Errors in the archive journal count as warnings.
- **Actual:** Summary counts only.
- **Evidence:** Reports/Convert/convert_20261005_184619.txt (131 bytes): 'Done converting. 9 of 11 files converted to AIFF. 2 skipped - already AIFF, or an AIFF already exists (nothing lost). No errors.'
- No per-file old to new list.
- No statement that originals were deleted.
- No list of collision skips (Pressure.flac was a collision, lumped in with 'already AIFF').
- No tag-loss or bit-depth warnings.
- The race run reports 'No errors' despite an archive failure and a destroyed FLAC.
- 24-bit failures are blamed on 'bad inputs'.

No Archive/Logs/Convert directory exists (config.py:86-94).
- **Fix:** Emit a CSV/JSON manifest next to the text report in Archive/Reports/Convert/<run-id>/ and link it from the report modal. Add a Convert entry to LOG_DIRS. Count archive-update failures and output-verification failures as errors.

#### [LOW · demonstrated] Smaller issues: overlapping roots double-counted, state file littered into source folders, sample packs converted, lossy-to-lossless upconversion without warning, static '8 cores' hint, no Rekordbox-running guard

- **ID:** `tool-convert-c-misc-low` · **Area:** chop-shop-tool · **Tool/surface:** Convert Format
- **Expected:** Deduplicated roots. State kept in the library root or archive. Optional exclusion of sample packs. A warning on lossy-to-lossless. A real core count. A Rekordbox-closed check.
- **Actual:** As described in the evidence.
- **Evidence:** Demonstrated:
- 'Music Library' + 'Music Library/Batch' gives 'Found 91 audio files' (51 + 40) and an inflated progress total (r3).
- .fablegear_state.json is written into whichever folder is first (routes_tools.py:659 library_root = paths[0]); seen in Techno/, TagMatrix_*/ and FlacArtTest/.
- 'Sample Packs/kick_01.wav' is converted to MP3, which adds encoder padding to a one-shot.
- MP3 to AIFF grew 244 KB to 1.06 MB with no notice.
- file_converter.html shows the static text '(your Mac has 8 cores)'; the host has 4.

Inferred: no _require_rb_closed in api_convert (routes_tools.py:643-660). The mp3-to-wav free-space ratio of 1.3 (audio_processor.py:465) underestimates the real 4-11x.
- **Fix:** Normalise roots (drop children of selected roots). Write step state to the configured library root only. Honour excluded_dirs and add a 'skip Sample Packs' default. Warn on lossy source with a lossless target. Fill the core count from /api/system. Add _require_rb_closed. Use per-pair size ratios based on bitrate.

#### [INFO · demonstrated] Confirmed-good behaviour

- **ID:** `tool-convert-c-confirmed-good` · **Area:** chop-shop-tool · **Tool/surface:** Convert Format
- **Expected:** n/a
- **Actual:** Works as intended.
- **Evidence:** 1. The path guard refuses the home folder ('Refusing to run the converter on .../home: this is your entire home folder', exit 2; r15). A nonexistent path is refused ('PATH is not a directory', exit 1; r14).
2. Sequential (workers=1) collision handling is safe: 'Vera Lux - Pressure.flac: Vera Lux - Pressure.mp3 already exists' and the FLAC is kept (race.sh 1).
3. A pipeline dry run with a convert step is refused with HTTP 400 'has no preview mode'.
4. On ffmpeg failure the temp file is removed and the original untouched (24-bit failures left no tmp).
5. Each converted file gets a 'convert' fg_processing_log row with metadata.from and format, and a convert_batch row on clean completion.
6. relink_converted keeps the fg_content id, BPM and key and refreshes size, hash and format (non-race case).
7. The undo revert endpoint refuses convert idempotently (400 twice).
8. The Operations tab honestly labels convert 'not reversible'.
9. After a server kill -9 the detached CLI stops at the next stdout write and leaves consistent state (checkpoint at 25, 31 journal rows, no tmp file in that run).
- **Fix:** Keep these behaviours, and cover them with regression tests when fixing the findings above.

## Chop Shop: Novelty Scanner

_Auditor: `tool-novelty-c`_

I audited the Novelty Scanner (card #step-novelty → GET /api/run/novelty → `cli.py novelty` → chop_shop/novelty_scanner.py) live on sandbox novelty-c (port 6117). I drove it through the real UI with Playwright and diffed sha256 snapshots before and after each step. Paths below use $SP = /tmp/claude-0/-home-user-FableGear/9074577f-2e6a-5e16-bdce-ef5b26f1b16b/scratchpad and $SB = $SP/sbx-novelty-c. Probe scripts and snapshots are in $SP/audit/tool-novelty-c/; screenshots are in /home/user/FableGear/docs/audits/2026-10-05/screens/tool-novelty-c/.

Confirmed working:
- The tool copies and never moves. Every source and Incoming hash was unchanged after all runs, and copy2 kept bytes and mtime.
- Dry Run is on by default.
- It never writes the Rekordbox databases: the local and device master.db hashes were unchanged across every run and revert.
- Every copy gets a journal row as it lands, and the Undo Wizard > Operations tab can park the copies in Archive/Undone Copies. Library files that existed before came back byte-identical.
- Running the revert a second time is safe and changes nothing.

Verdict: weak on safety for a rescue tool. The serious defects are integrity bugs that let the tool report a successful rescue when tracks were never copied, or were copied truncated or overwritten:
- (a) A new run silently uses a stale checkpoint and skips tracks it never copied (75 of 120 skipped). The report does not mention it.
- (b) Cancelling or interrupting mid-copy leaves a truncated, unjournaled file in the library. A later filename-mode run reports it as "confirmed already present".
- (c) With workers>1, files racing for the same destination overwrite each other (60 reported copied, 58 on disk).
- (d) Filename mode drops non-Latin characters when normalizing names, so all such tracks collide. Unrelated recordings are skipped as "confirmed already present".

Revert markers and resume are weak:
- No restore point is tied to a job. Undo groups every novelty run within 15 minutes of the previous one into one session (14 runs became one session of 1394 files). The Timeline never shows novelty runs.
- The resume banner lives only in browser localStorage and shows no starting point. Resume drops the comparison mode, and re-runs and resumes create byte-identical `_1` duplicates.
- Interrupt sends SIGTERM to a process with no signal handler: no partial report, the Chop Shop readout still says "Complete", and it also kills cli.py processes the UI did not start.
- Undo leaves empty folders behind.

Other problems found:
- The resume banner renders the source folder name as raw HTML, so script runs in the app's page.
- Copy time grows quadratically with the number of files in a folder (480 files took 223 s, against 2.5 s for the dry run).
- A dry run still creates folders, including a mistyped destination and a folder inside ~/Library.

**Coverage**

- LIVE via Playwright, driving the real UI. Each step was checked against before/after snapshots (sha256 of every file in the sandbox, the FableGear DB processing log and content rows, and the /api/undo/* endpoints):
- r1: dry run in fingerprint mode (sources Incoming + BackupDrive).
- r2: live filename mode copying into the library.
- r3 a/b: two identical live runs with a separate Copy-To folder.
- r4: interrupt mid-run on 120 files.
- r5: server restart, then UI reload with saved and with cleared browser storage.
- r6: Resume from the banner after the interrupt.
- r7c: kill -9 of the real server pid mid-run, restart, then r8 Resume.
- r9: re-run after a truncated copy.
- r10/r11: interrupt, move the copy target away, restart, Resume.
- r12: dry run with non-Latin filenames.
- r13: dry run with a mistyped destination and a Copy-To under ~/Library.
- r14: forbidden source path (guard failure).
- r15: fresh run that silently used a stale checkpoint.
- u1/u2/u3: Undo Wizard > Operations preview and revert, including revert twice, the timezone display check, and Savepoints/Trash tab errors.
- xss.py: HTML injection through the resume banner.
- LIVE through the same endpoint the UI calls (urllib/curl):
- killcopy.py: a 691 MB WAV interrupted mid-copy. I sent SIGTERM to my own CLI process group only; this is the same signal /api/cancel sends.
- workers=8 race test on 60 same-named files. This is the parameter the Pipeline sends.
- LIVE CLI with the exact argv the route builds: timing for 120 vs 480 files (live and dry).
- Pure-function calls: _normalized_filename_key and _dest_candidates (candidate explosion).
- Code inspection for: no signal handling in cli.py; the orphan-kill sweep in helpers.py; Pipeline novelty step wiring; the dead RB-block message; the _fp_similarity method.
- NOT testable here:
- fpcalc is absent, so fingerprint confirmation always failed and every candidate was treated as novel. The real _fp_similarity behaviour (including whether a truncated copy's prefix would 'confirm' presence) is unverified.
- A running Rekordbox process.
- Browse… buttons (osascript pickers) and pywebview drag-and-drop.
- WKWebView rendering.
- The Pipeline Wizard's novelty step was read in code, not run.
- Deliberately limited: I called /api/cancel only once. It SIGTERMs every cli.py under the shared app directory, so it would kill other auditors' jobs. In fact sbx-convert-c's cancel at 18:54:18 killed one of my jobs (exit -15 with no output). Other auditors' results may be contaminated the same way.
- Sandbox inputs I added (my own sandbox only): $SB/Sources/{BackupDrive, BigBackup (120 files), HugeSrc (691 MB WAV), Flat480, SameName (60 different recordings all named take.wav), JPHome, JPBackup}.
- Final state: I reverted all journaled copies through the UI. Server stopped.

**Per-tool safety matrix**

### Novelty Scanner (#step-novelty → /api/run/novelty → cli.py novelty → chop_shop/novelty_scanner.py)

| Check | Result |
|---|---|
| R — Report | PARTIAL. On success it writes Archive/Reports/Novelty Scan/novelty_<ts>.txt, and the report modal shows the path. The content is counts only: no per-file list, no destinations, and no reason or matching file for a skip. It says 'confirmed already present' even in filename mode, and does not mention that fpcalc was missing. The parallel path (workers>1) logs no per-file lines at all. On cancel (exit -15), kill -9 or guard failure (exit 1, raw traceback) no report is written. There is no Archive/Logs/Novelty folder, and the per-file log is only the SSE stream, which is gone when the page closes. Dry runs write the same counts-only report. |
| M — Revert marker | NO. Nothing is created before the first copy. Per-file `novelty_copy` journal rows go into fg_processing_log AFTER each copy finishes (novelty_scanner.py:510-521), plus a `novelty_scan` summary row per source root. No job id links them. /api/undo/timeline stayed [] for all 15 runs, because SSE runs bypass job_dispatcher. Undo groups rows by type with a 15-minute gap (routes_undo.py:322,344-401), so 14 runs into 9 targets became ONE session of 1394 files (first_id 5, last_id 1424). |
| U — Undo | PARTIAL. Operations > Preview undo > 'Return N files' moves each journaled copy into Archive/Undone Copies/<stamp>/. Library files that existed before were byte-identical afterwards (sha256 checked). The result is not identical to before, though:<br>- Empty folders remain: Artist X/Album Y, Newcomer, Orphaned Tracks/2026, Live Sets & Mixes/2026, and every created copy target (Perf_Flat480 alone has 481 empty dirs).<br>- An unjournaled truncated file remains.<br>- .fablegear_state.json was added to the library.<br>- Undone Copies flattens the folder layout (567 files got a __<id> suffix).<br>Running the revert twice is safe (0 reverted, 3 blocked, no file changes). The revert itself cannot be undone. Paths journaled twice cause silent errors: the preview said 1335, 1322 were reverted, and 13 OSErrors were never shown in the UI. |
| C — Cancel / interrupt | POOR.<br>- Interrupt sends SIGTERM and cli.py has no handler, so the run exits -15.<br>- No partial report and no novelty_scan row.<br>- The checkpoint is saved only every 25 files: 75 recorded against 97 actually copied.<br>- The Chop Shop readout still says 'Complete'.<br>- SIGTERM during a copy left 14 MB of a 691 MB WAV in the library, with no journal row.<br>- kill -9 of the server: the child died on a broken pipe at its next print (42 copied, 40 logged, checkpoint at 25), and the UI shows 'Connection error'.<br>- /api/cancel also kills every other cli.py from the same install, including terminal or MCP jobs and other instances. |
| S — Resume after restart | PARTIAL / UNSAFE.<br>What the user sees:<br>- The banner comes from localStorage only and reads 'Interrupted run — Xm ago' plus the source paths.<br>- It shows no copy target, comparison mode, done/remaining count, absolute start time or restore point.<br>- With cleared WebView storage there is no banner, even though the server checkpoint exists.<br>- /api/checkpoint/check?tool=novelty returns exists:false while the checkpoint exists (config key mismatch).<br>What Resume does:<br>- It refills sources, compare and copy-to, but NOT the comparison mode. A filename-mode run therefore resumed in fingerprint mode, started from 0 and made 97 byte-identical _1 duplicates.<br>- A fingerprint-mode resume skipped the 25 checkpointed files but re-copied 17 that were done but not checkpointed.<br>- It does not check whether inputs changed. With the copy target moved away, 50 tracks were never re-copied.<br>- A stale checkpoint was silently used by a later fresh run: 75 of 120 tracks skipped. The resumed report ('95 / 70 / 45 tracks scanned') never mentions the earlier session or the skipped tracks. |
| D — Dry run / preview | YES (default on), but PARTIAL:<br>- It shows counts only, plus per-file names in the transient log. No destination paths are computed.<br>- It ignores checkpoints, so the preview (120) does not match what a live run with a stale checkpoint does (45).<br>- It has side effects: it creates the compare folder and the Copy-To folder (including a mistyped one, or one inside ~/Library), writes .fablegear_state.json into the compare folder, and writes a report plus journal rows.<br>- It does not warn when the compare folder has 0 tracks. |
| B — Boundary | Confirmed good for Rekordbox: neither master.db nor the device DB changed (sha256) across all runs and reverts, so no savepoint is needed. FableGear DB writes are limited to fg_processing_log: novelty_copy rows (covered by Operations undo) and novelty_scan rows (not undoable, harmless). No fg_content rows are added, so copied tracks do not appear in the Record Room until a sync or import. dest and copy_to skip path_guard (only sources are guarded), so the tool can create folders and copy audio into ~/Library/Pioneer/rekordbox. |

_Evidence:_ All cells demonstrated live except the orphan-kill mechanism (inferred from helpers.py:348-383, routes_tools.py:1247-1272, with one live circumstantial hit).
Snapshots: $SP/audit/tool-novelty-c/snap_*.json, diffed with diff.py.
UI and log captures: r*.log.txt / r*.report.txt.
Key screenshots: r4_cancel_2_done.png ('Complete' at 81%), r5_reload_after_cancel_0_card.png (banner), u1_preview_preview.png, u2_merged_preview.png, u3_final_preview.png.
Code: cli.py:3362-3374 (mkdir before dry-run check), 3404-3424 (checkpoint every 25, key includes match_mode), 706-740 (default resume); routes_tools.py:665-697, 830-879; static/chop_shop/runners.js:449; static/chop_shop/pipeline.js:598-631, 724-734; routes_undo.py:322, 429-437, 595-604; static/shared/undo.js:182-206.

**Findings**

#### [HIGH · demonstrated] A fresh run silently uses a stale checkpoint and skips tracks it never copied, and the report hides it

- **ID:** `tool-novelty-c-stale-checkpoint-silent-skip` · **Area:** session-resume · **Tool/surface:** Novelty Scanner
- **Expected:** A new run should either start fresh, or clearly offer to resume the earlier session, showing its date and done count and confirming that the done files still exist at the target. The report should state how many tracks were skipped from an earlier session.
- **Actual:** Old checkpoints are consumed without asking. Tracks that were never copied, or whose copies were removed, are treated as done. The report lists only the remaining subset, so the user believes the rescue is complete and may wipe the backup drive.
- **Evidence:** 1. r4: I ran BigBackup (120 files) → Copy-To Rescue in filename mode and interrupted it at 97 copies. A checkpoint was saved: $SB/home/.fablegear/checkpoints/novelty/5d0830a8d0ada5c9.json.gz, completed=75, config {dest, copy_to Rescue, match_mode filename}.
2. r6: Resume from the banner restarted in fingerprint mode (a different checkpoint key), so this checkpoint was left orphaned.
3. I then reverted all copies through the Undo Wizard. Rescue was empty.
4. r15, 22 minutes later, with a fresh browser profile (no banner): the same form was filled by hand and run live. The log says: '19:12:16 Found checkpoint from 2026-10-05T18:50:54 (75/0 done) — resuming' and 'Source tracks to evaluate: 45'. Rescue holds 45 of 120 tracks.
5. The report reads: '45 tracks scanned on source… 45 novel tracks copied', with exit 0 and the modal titled 'Complete'. The 75 skipped tracks are never mentioned.
Same mechanism in r10/r11: interrupt at 61 copies (checkpoint 50), the copy target was moved away, server restarted, Resume clicked. Result: 'Found checkpoint (50/0 done)', 70 of 120 tracks in the target, report '70 tracks scanned'.
Code: cli.py:3404-3412 loads done_paths whenever a checkpoint exists for (roots, dest, copy_to, match_mode). The default --checkpoint-action is resume (cli.py:4458-4465), and the UI never sends checkpoint_action (static/chop_shop/runners.js:438-453).
- **Fix:** - Make resume explicit: the UI should call /api/checkpoint/check (fixed to send the same config) and pass checkpoint_action=resume only when the user picks Resume; otherwise pass reset.
- When resuming, verify each done_path's recorded destination still exists with the same size/hash (store src → dest + size in the checkpoint) and re-queue any that are missing.
- Put 'Resumed session from <saved_at>: N already done (verified M), K re-queued' in the report and in the total.
- Expire checkpoints, or require confirmation for any older than one session.

#### [HIGH · demonstrated] Interrupting a copy leaves a truncated, unjournaled file in the library, which a later run reports as 'confirmed already present'

- **ID:** `tool-novelty-c-truncated-copy-confirmed-present` · **Area:** chop-shop-tool · **Tool/surface:** Novelty Scanner
- **Expected:** A copy should be atomic (temp file, then rename) and verified. An interrupted copy should leave no partial file, or should be journaled and cleaned up. A presence check should never accept a file whose size or hash differs from the source.
- **Actual:** The only library copy of a 40-minute recording is a 49-second stub. The tool then tells the user it is already owned, so the user may delete the original.
- **Evidence:** 1. killcopy.py started GET /api/run/novelty with source HugeSrc (one WAV, 691,200,144 bytes, 40 min), the library as compare target, filename mode, no Copy-To, live.
2. As soon as the destination file appeared, it sent SIGTERM to that CLI's process group. This is the same signal /api/cancel sends; I scoped it to my own pid.
3. Left on disk: 'Music Library/Live Sets & Mixes/2026/Huge Artist - Long Live Set.wav' at 14,155,776 of 691,200,144 bytes. ffprobe reports 49 s.
4. fg_processing_log has 0 rows for it, so it is invisible to Undo > Operations (the preview lists no such item). The SSE ended with exit_code -15.
5. r9: re-ran the same scan in the UI (filename mode). The log says '[1/1] SKIPPED Huge Artist - Long Live Set.wav' and the report says '1 tracks confirmed already present — skipped'.
6. After the final full Undo revert the truncated file is still in the library.
Code: _copy_novel copies straight to the final path with shutil.copy2 and no size/hash check (novelty_scanner.py:336-341). The journal row is written only after the copy succeeds (510-521). cli.py has no SIGTERM handler.
- **Fix:** - Copy to '<dest>.fgpart', fsync, compare the size (and ideally sha256 or a fast hash) with the source, then os.replace to the final name. Journal an intent row before the copy and mark it done afterwards.
- Install a SIGTERM/SIGINT handler in cli.py that sets a cancel event, finishes or rolls back the current file, flushes the checkpoint and writes a partial report.
- On startup or resume, sweep *.fgpart files.
- In filename mode, at least require the size (or the fingerprint/duration) to match before calling a track present.

#### [HIGH · demonstrated] With workers>1, same-named tracks race for one destination and distinct recordings are silently overwritten

- **ID:** `tool-novelty-c-parallel-copy-race-overwrites` · **Area:** chop-shop-tool · **Tool/surface:** Novelty Scanner (route param workers; Pipeline novelty step defaults to workers row 4)
- **Expected:** Each novel track should end up as a distinct file. Collision-renaming must be atomic under concurrency.
- **Actual:** Concurrent workers pick the same free name and copy2 overwrites. The report and journal claim every track was copied.
- **Evidence:** 1. Created $SB/Sources/SameName/Session 01..60/take.wav: 60 different recordings (60 unique sha256), untagged, so all map to <copy_to>/Orphaned Tracks/2026/take.wav.
2. Ran curl -N 'localhost:6117/api/run/novelty?source=…/SameName&dest=…/Music Library&copy_to=…/RaceTest&match_mode=fingerprint&no_dry_run=1&workers=8'.
3. The report says '60 novel tracks copied', but RaceTest holds 58 files (58 unique hashes).
4. The journal has 60 rows over 58 distinct paths; take_11.wav and take_30.wav were each journaled twice. Two unique recordings were lost from the rescue target.
5. In parallel mode the SSE log has 0 '[i/N] COPIED' lines, so nothing shows the loss.
Code: novelty_scanner.py:324-338 checks dest.exists() and then calls copy2 with no lock or O_EXCL, while the ThreadPoolExecutor branch runs at 523-545. routes_tools.py:566-581 shows the Pipeline passes workers through; pipeline.js renders workersRow(4) for novelty.
- **Fix:** - Reserve the destination atomically: os.open(dest, O_CREAT|O_EXCL) in a retry loop over _1.._N, or hold a lock per destination directory around choose-name-and-copy. Combine this with the temp-file-and-rename fix.
- Log per-file results in the parallel branch as well.
- Add a test with N same-named files and workers=8.

#### [HIGH · demonstrated] Filename mode marks unrelated recordings as 'confirmed already present' (non-Latin names collapse to an empty key; same-name different audio)

- **ID:** `tool-novelty-c-filename-mode-false-present` · **Area:** chop-shop-tool · **Tool/surface:** Novelty Scanner
- **Expected:** Follow the tool's own promise ('When in doubt, it copies'): filename-only matches should never be reported as 'confirmed', and non-ASCII names must not collide.
- **Actual:** Any library containing one non-Latin filename causes every non-Latin-named track on the backup to be skipped. Same-titled but different versions are skipped too. The report presents these as confirmed.
- **Evidence:** 1. _normalized_filename_key (novelty_scanner.py:344-348) keeps only [a-z0-9]. Live calls gave: '東京.mp3' → '', '大阪の夜.mp3' → '', 'Ωμέγα.flac' → '', 'Beyoncé - Halo.mp3' → 'beyonc halo' (identical to 'Beyonc - Halo').
2. r12 (UI dry run, filename mode): compare folder JPHome contains only 東京.mp3; source JPBackup contains 大阪の夜.mp3 (900 Hz), Ωμέγα.flac (1200 Hz) and 01.wav. Log: 'SKIPPED Ωμέγα.flac', 'SKIPPED 大阪の夜.mp3'. Report: '2 tracks confirmed already present — skipped'.
3. r2 (live): BackupDrive/'Brick Wall - Clipper.flac' (20 s, 333 Hz sine, unique) was SKIPPED because the library has 'Brick Wall - Clipper.mp3', a different recording.
4. The UI option is labelled only 'Filename compare only (faster)'.
- **Fix:** - Normalize with unicodedata NFKD and casefold, keeping all Unicode letters and digits (\w with re.UNICODE).
- Treat an empty key as 'no match'.
- In filename mode, also require the size (or the duration within ±1 s) to match.
- Change the report wording to 'filename match (unverified)' and list those files.
- Relabel the option: 'Filename only — may skip different versions with the same name'.

#### [MEDIUM · demonstrated] No restore point linked to a run: undo groups all novelty runs within 15 minutes into one session, and the Timeline never shows novelty

- **ID:** `tool-novelty-c-no-job-revert-marker` · **Area:** undo-revert · **Tool/surface:** Undo Wizard > Operations / Timeline
- **Expected:** Each run gets a job id stamped on its journal rows (written at start), so the undo timeline shows 'Revert Novelty run at <time> → <copy target> (N files)' and can revert exactly that run.
- **Actual:** Undo is all-or-nothing across any novelty runs less than 15 minutes apart. The novelty_scan summary rows are not linked to anything. The Timeline does not know the run happened.
- **Evidence:** 1. After r2, then r3a and r3b (separate runs at 18:46:55, 18:49:39 and 18:49:53), /api/undo/operations returned one session: {count 9, first_id 5, last_id 21, type novelty_copy}. It included run 1, which had already been undone (shown as blocked).
2. By the end, 14 runs into 9 different targets were one session: {count 1394, first_id 5, last_id 1424, started 18:46:55, ended 19:09:32}.
3. The UI offers only 'Return 1335 files' (u3_final_preview.png). A single run cannot be reverted alone.
4. /api/undo/timeline returned [] in every snapshot (snap_*.json), and the Timeline tab shows 'No jobs found' (u1_preview_timeline.png). The Timeline filter has no Novelty option.
5. No savepoint, trash folder or 'before novelty run' marker is created.
Code: routes_undo.py:322 and 344-401 (grouping by type and time gap); helpers.py:858-933 (SSE runs never touch job_dispatcher).
- **Fix:** - Create a run id in cmd_novelty (or the route) and write a 'novelty_run_start' row with config and roots before the first copy.
- Put run_id in each novelty_copy row's metadata and in novelty_scan.
- Group /api/undo/operations by run_id (fall back to the time gap only for legacy rows).
- Register SSE runs with job_dispatcher history, or record them in the same table the Timeline reads.

#### [MEDIUM · demonstrated] Resume loses the comparison mode and re-runs from 0; resume and re-runs create byte-identical _1 duplicates

- **ID:** `tool-novelty-c-resume-drops-mode-duplicates` · **Area:** session-resume · **Tool/surface:** Novelty Scanner resume banner
- **Expected:** Resume should restore the exact configuration, including mode and workers. Already-copied files should be recognized (the destination exists with the same size/hash) and skipped, not duplicated.
- **Actual:** Every resume or re-run adds byte-identical duplicates, and with a filename-mode run the whole job restarts.
- **Evidence:** Resume after an interrupted filename-mode run (r6):
- The run had been interrupted at 97 copies; the checkpoint key includes match_mode=filename.
- The banner Resume set the dropdown back to fingerprint ('form after resume click: match_mode= fingerprint'), because _saveToolCkpt does not store matchMode (runners.js:449) and _resumeNovelty (pipeline.js:724-734) does not restore it.
- The CLI found no checkpoint ('Source tracks to evaluate: 120'), so Rescue ended with 217 files: 97 *_1.mp3 files, byte-identical to their originals.
- The old filename-mode checkpoint stayed on disk and was later consumed (see tool-novelty-c-stale-checkpoint-silent-skip).
Resume after kill -9 in fingerprint mode (r7c → r8):
- The checkpoint held 25 files while 42 had been copied, because it saves every 25 (cli.py:3416-3424).
- Resume skipped 25 and re-copied 17 as _1 duplicates: Rescue4 has 137 files for 120 tracks.
Re-running the same scan with a Copy-To (r3b):
- Created 'Newcomer - Fresh Cut_1.mp3', 'Track 01_1.wav' and 'Unique Song_1.mp3' with sha256 identical to the first copies.
- _copy_novel renames on collision without a hash check (novelty_scanner.py:324-334). The copy_to folder is never compared against.
- **Fix:** - Save matchMode (and workers) in rb_ckpt_novelty and restore it in _resumeNovelty.
- Journal before saving the checkpoint, or save the checkpoint every file (it is tiny).
- In _copy_novel, if the destination exists and its size+sha256 equals the source, return 'skipped (already copied)' instead of writing _N, as library_organizer._resolve_dest already does.
- Also index copy_to as a 'present' reference.

#### [MEDIUM · demonstrated] Interrupt/kill: no signal handling, no partial report or summary row, checkpoint not flushed, readout says 'Complete'

- **ID:** `tool-novelty-c-cancel-no-handler` · **Area:** chop-shop-tool · **Tool/surface:** Novelty Scanner / scan bar
- **Expected:** SIGTERM should set a cancel flag; the run then finishes or rolls back the in-flight file, flushes the checkpoint with the exact done set, writes a partial report and a summary row ('cancelled at 97/120'), and exits with a distinct code. The UI should say 'Interrupted', not 'Complete'.
- **Actual:** The process dies at once. Resume state lags by up to 24 files and nothing durable records the cancelled run except per-file journal rows.
- **Evidence:** 1. r4: live run, BigBackup (120 files) → Rescue, filename mode. POST /api/cancel 4 s in returned {terminated: 1, pids [20838]}.
2. Log: '[97/120] COPIED …' then '✗ Exited with code -15'. No report modal; Reports/Novelty Scan had no new file; no novelty_scan row (fg_processing_log grew by exactly 97 novelty_copy rows).
3. The checkpoint held completed=75 while 97 were done.
4. The readout in r4_cancel_2_done.png says 'COMPLETE', 81% complete, '97 done / 23 remaining', '97 edited'. _chopReadoutFinish always sets 'Complete' (scan_bar.js:116-122).
5. kill -9 of the real server pid (r7c): the child CLI was gone 1.5 s later (broken pipe at its next print). 42 copies on disk against 40 logged, checkpoint 25, and the UI showed 'Connection error — check the server is running.'
6. cli.py contains no 'signal' handling.
- **Fix:** - Add signal.signal(SIGTERM/SIGINT) in cli.py main for long-running tools. Check the event in scan_novel's loop, flush the checkpoint, call _emit_report with a 'Cancelled' header, and log a novelty_scan row with status='cancelled'.
- In runCommand's done handler, pass the exit code to finishScanBar and _chopReadoutFinish, and show 'Interrupted' or 'Failed'.
- For server death, have the CLI handle BrokenPipeError: keep going headless and write the report to disk, or stop cleanly with a checkpoint.

#### [MEDIUM · demonstrated] Resume banner has no starting-point reference, exists only in localStorage, and the server checkpoint check is broken for novelty

- **ID:** `tool-novelty-c-resume-banner-no-starting-point` · **Area:** session-resume · **Tool/surface:** Novelty Scanner resume banner / /api/checkpoint/check
- **Expected:** A server-backed record should say: 'Novelty run started <abs time>: sources → copy target (mode); 75/120 done (25 remaining); copies journaled as run <id>; to roll back use Undo > Operations > run <id>'. It should survive WebView storage loss and show the same in the CLI and UI.
- **Actual:** A vague localStorage hint, a broken server check, and reports that do not identify the starting point.
- **Evidence:** 1. After the server restart, the reload showed the banner 'Interrupted run — 1m ago | $SB/Sources/BigBackup | Resume | Start Fresh' (r5_reload_after_cancel_0_card.png). It has no absolute time, no Compare/Copy-To targets, no mode, no done/remaining count (the checkpoint knew 75) and no restore point.
2. r5_reload_fresh_profile with cleared storage: no banner, while ~/.fablegear/checkpoints/novelty/5d0830a8d0ada5c9.json.gz existed.
3. curl 'localhost:6117/api/checkpoint/check?tool=novelty&path=$SB/Sources/BigBackup' returned {"exists":false} while that checkpoint (and later fb46eaf350ded115) existed. The route builds config {} for novelty (routes_tools.py:858-875), but the CLI keys on {dest, copy_to, match_mode} (cli.py:3406-3408). No UI code calls /api/checkpoint/check anyway.
4. Reports from resumed runs say '95 tracks scanned' or '70 tracks scanned' with no reference to the earlier session.
5. A guard-rejected live run (r14, exit 1) also leaves rb_ckpt_novelty set, because it is cleared only when ec===0 (runners.js:453). The banner then offers 'Resume' for a run that never started.
- **Fix:** - Have /api/checkpoint/check accept dest, copy_to and match_mode for novelty, or better, list every checkpoint for a tool with its config.
- Build the banner from the server, not localStorage, and show the config, done/total, saved_at and the run id (see the revert-marker finding).
- Clear the local marker on guard or validation failures.
- Add a 'Resumed from <saved_at> (N done)' line to the report.

#### [MEDIUM · inferred] Interrupt (/api/cancel) also SIGTERMs cli.py jobs this UI did not start (other instances, terminal or MCP jobs)

- **ID:** `tool-novelty-c-cancel-kills-foreign-cli` · **Area:** chop-shop-tool · **Tool/surface:** /api/cancel (Interrupt) and /api/cancel/force
- **Expected:** Interrupt stops only the job this UI started (tracked request_id / process group), or asks before touching orphans.
- **Actual:** One Interrupt click on the Novelty card can kill a Tag Tracks or Prune job started from Terminal, or an MCP-dispatched job (mcp_server.py → job_dispatcher → cli.py), mid-write.
- **Evidence:** Code: api_cancel calls terminate_managed_subprocesses(force=False, include_orphans=True) (routes_tools.py:1247-1272). That calls _list_orphaned_cli_pids (helpers.py:348-383), which runs pgrep -f '<REPO_ROOT>/cli.py' and signals every match whether or not this server owns it.
Live circumstantial evidence:
- My run r7_kill9 started at 18:54:18 on port 6117 and ended with only '✗ Exited with code -15', with no CLI output and before my own kill fired.
- At exactly that second, $SP/sbx-convert-c/server.log shows '[05/Oct/2026 18:54:18] "POST /api/cancel HTTP/1.1" 200' from a different server instance.
- After that I stopped calling /api/cancel, because other auditors' cli.py jobs share the same app directory.
- **Fix:** - Default include_orphans=False for /api/cancel.
- Only treat as orphans the processes in this server's own registry whose owner_pid is dead (runtime_token match).
- Scope by tool, or by the request_id the UI got when it started the run (return it in the first SSE event).

#### [MEDIUM · demonstrated] Report is counts-only, says 'confirmed' in filename mode, hides the missing-fpcalc fallback, and has no durable per-file log

- **ID:** `tool-novelty-c-report-content` · **Area:** chop-shop-tool · **Tool/surface:** Novelty Scanner report
- **Expected:** The report should be an auditable manifest: per-file action (copied → dest, skipped → matched path and method, error → reason) as CSV/TXT in Archive/Reports, a log in Archive/Logs/Novelty, an explicit 'fingerprint unavailable — N tracks copied without confirmation' warning, and honest wording for filename matches.
- **Actual:** After the page closes, the only record of which tracks were left on the backup drive is the journal of copies. Nothing lists the 'present' decisions that the user relies on before wiping a drive.
- **Evidence:** 1. Every report is counts only, e.g. novelty_20261005_184655.txt: '3 novel tracks copied to … 2 tracks confirmed already present — skipped'. It has no file names, no destinations (Artist X/Album Y, Orphaned Tracks/2026…) and no matched file or reason per skip.
2. r1 (fingerprint mode, no fpcalc): the log shows 'ERROR duplicate_detector — fpcalc not found' and 'Could not fingerprint Nula - Night Drive.mp3 — treating as novel'. The summary says 'fingerprinted: 2 / 4', and the report says '5 novel tracks would be copied' with exit 0 and no warning. A byte-identical copy of a library track is therefore planned as novel.
3. With workers>1 (race test), the log has no per-file lines at all.
4. There is no Archive/Logs/Novelty folder (LOG_DIRS in config.py:87-95 lacks Novelty). The per-file decisions live only in the SSE stream.
- **Fix:** - Write novelty_<ts>.csv alongside the txt, with columns src, action, dest/matched, method, reason.
- Add 'Novelty' to LOG_DIRS and tee the run log there.
- Count real fingerprint successes separately from attempts.
- When fpcalc is missing in fingerprint mode, warn at the top of the report (or refuse and suggest filename mode).

#### [MEDIUM · demonstrated] Dry run creates folders (including a mistyped destination and ~/Library paths); Compare/Copy-To are not path-guarded; guard errors show a raw traceback

- **ID:** `tool-novelty-c-dryrun-side-effects-unguarded-targets` · **Area:** chop-shop-tool · **Tool/surface:** Novelty Scanner
- **Expected:** A preview should touch nothing. A non-existent Compare folder should be an error ('did you mean …?'). Copy-To and Compare should be guarded like sources (no ~/Library, /, /Volumes). Validation errors should be a clean one-line [ERROR].
- **Actual:** A preview creates folders, a typo makes every track novel (and a live run copies into a new typo folder), and audio can be copied into app-data trees.
- **Evidence:** 1. r13 (Dry Run checked): Compare = '$SB/Volumes/DJDRIVE/Music Libary' (typo), Copy-To = '$SB/home/Library/Pioneer/rekordbox/NoveltyDrop'. Both directories were created; the typo folder also got .fablegear_state.json. The report said 'Compared against: 0 tracks' and '1 novel tracks would be copied to …/home/Library/Pioneer/rekordbox/NoveltyDrop' with no warning.
2. Code: cli.py:3362-3374 calls mkdir on dest and copy_to before the dry-run branch. path_guard is applied to sources only (novelty_scanner.py:407).
3. The first dry run also wrote 'Music Library/.fablegear_state.json' into the user's library (helpers.py:923 mark_step_complete on the dest root).
4. r14 (live, Source = $HOME): the UI log shows a Python traceback ending 'ValueError: Refusing to run the novelty scanner on …/home'. Exit 1, no report, and the Copy-To folder 'GuardTest' had already been created.
- **Fix:** - In dry-run mode skip all mkdir and the state-file write.
- In live mode, refuse when dest does not exist, rather than creating it.
- Run forbidden_source_reason (or a stricter write-target guard) on dest and copy_to.
- Call _guard_or_exit(sources) in cmd_novelty before any filesystem change.
- Warn when the destination index size is 0.

#### [MEDIUM · demonstrated] Undo is not structure-identical: empty created folders, unjournaled leftovers and flattened 'Undone Copies' remain; the revert cannot be undone

- **ID:** `tool-novelty-c-undo-leaves-orphans` · **Area:** undo-revert · **Tool/surface:** Undo Wizard > Operations (novelty_copy)
- **Expected:** Reverting a run brings the tree back to its pre-run shape: it removes directories the run created (if empty), cleans partial files, and records the revert so it can itself be restored (keeping the folder layout in the recovery area).
- **Actual:** Every revert leaves skeleton folders in the library and copy targets. Recovered copies lose their layout and cannot be returned from the UI.
- **Evidence:** 1. After the UI revert u1 (3 files) the library contents matched by hash, but these dirs remained: Music Library/Artist X/Album Y, Newcomer, Orphaned Tracks/2026 (diff.py before_live1 after_undo1).
2. After the final revert (1322 files), compared with BEFORE:
- EXTRA files: 'Music Library/Live Sets & Mixes/2026/Huge Artist - Long Live Set.wav' (truncated, unjournaled) and Music Library/.fablegear_state.json.
- Leftover empty dirs: Rescue (121 dirs), Rescue2-4 (121 each), Perf_Flat480 (481), New Finds (6), RaceTest, Rescue6 (71), plus the created roots 'Music Libary', GuardTest and ~/Library/Pioneer/rekordbox/NoveltyDrop.
3. Undone Copies/2026-10-05_191112 holds 1322 files in one flat folder, 567 of them renamed '__<journal id>'. There is no UI to put them back or purge them, and undo_novelty_copy is not in _REVERTIBLE.
4. After a revert, the Operations list still shows the same session as 'Novelty — tracks copied in · 9 files' with 'Preview undo'. Re-checking reports '0 can be returned · 3 blocked (moved again since…)'.
- **Fix:** - Journal created directories (or compute them from the dest paths) and rmdir them bottom-up when empty after the revert.
- Preserve paths relative to copy_root under Undone Copies/<stamp>/.
- Add undo_novelty_copy as revertible (move back), plus a 'Purge' action.
- Mark sessions as reverted in the list.

#### [MEDIUM · demonstrated] Copying is quadratic in folder size: each novel file re-scans its whole parent subtree (recursively)

- **ID:** `tool-novelty-c-quadratic-copy` · **Area:** chop-shop-tool · **Tool/surface:** Novelty Scanner
- **Expected:** Linear time: read metadata for just the one file.
- **Actual:** A rescue of a typical flat 'Downloads' or 'All Tracks' folder on a backup drive takes hours to days, against a stated target of 500,000+ tracks.
- **Evidence:** 1. _copy_novel calls scan_directory(src.parent) for every copied file, just to get one TrackInfo (novelty_scanner.py:313-316). scan_directory walks the tree recursively (scanner.py:355-385).
2. Measured with the exact CLI argv the route builds (live, filename mode, Copy-To scratch):
- BigBackup, 120 files in 120 subfolders: 19.0 s.
- Flat480, 480 files in one folder: 223.5 s.
- Flat480 as a dry run: 2.5 s.
3. 4× the files took 11.7× the time, close to quadratic. Extrapolated, a flat 5k-file folder takes about 6.5 h.
4. A loose file at the root of a drive triggers a recursive metadata scan of the whole drive for every root-level file.
5. The dry run skips destination computation, so it gives no hint of this cost.
- **Fix:** - Use scanner.extract_metadata(src) (or whatever single-file reader scan_directory uses) instead of scan_directory(src.parent), or cache one scan per directory.
- Add a timing regression test that checks 1000 files in one folder scales linearly.

#### [MEDIUM · inferred] Fingerprint mode: a source track with scan-index metadata becomes a candidate against every untagged library file, fingerprinted with no cache

- **ID:** `tool-novelty-c-fingerprint-candidate-explosion` · **Area:** chop-shop-tool · **Tool/surface:** Novelty Scanner (fingerprint mode)
- **Expected:** Candidates should be bounded by duration and BPM; fingerprints should be cached per path (as duplicate_detector's cache does); similarity should use decoded raw fingerprints (popcount over aligned ints, with offset search) or acoustid.compare.
- **Actual:** Once Tag Tracks has indexed the source but not all of the library, a scan fingerprints the whole library once per source track.
- **Evidence:** Pure-function check (live):
- _dest_candidates(124.0, '8A', 300.0, {5000 dest paths with no metadata}, []) returned 5000 candidates.
- The same call with no source metadata returned 0 candidates.
Code:
- Any destination entry without metadata is added for any source that has metadata (novelty_scanner.py:194-198).
- _confirmed_in_dest then runs fingerprint_file on the source and on every candidate, per source track, with no memoization (263-292). That is N_src × N_dest fpcalc runs.
- _fp_similarity compares compressed fingerprint strings character by character (249-260), which is not a meaningful Chromaprint similarity. Different encodes of the same audio will mostly be copied (safe but useless); whether a truncated prefix could reach ≥0.85 and be 'confirmed' is untested (speculative).
I could not run this live because fpcalc is not installed.
- **Fix:** - Treat entries without metadata as candidates only via a filename or duration match.
- Compute the destination duration cheaply (mutagen) when building the index.
- Memoize fingerprint_file results per path for the run, or reuse duplicate_detector's cache.
- Replace _fp_similarity with decoded-fingerprint bit comparison, and add a test with a truncated copy.

#### [MEDIUM · demonstrated] Resume banner renders source folder names as raw HTML, so script runs in the app's page

- **ID:** `tool-novelty-c-banner-html-injection` · **Area:** session-resume · **Tool/surface:** Chop Shop tool resume banner (all tools)
- **Expected:** Paths are inserted with textContent.
- **Actual:** A crafted folder name on a source drive runs script in FableGear's page after any interrupted run. That page can call destructive local endpoints such as prune or savepoint restore.
- **Evidence:** 1. _showToolResumeBanner inserts pathsText into innerHTML without escaping (static/chop_shop/pipeline.js:609-621, '<div class="trb-paths">${pathsText}</div>').
2. xss.py stored exactly what runNovelty → _saveToolCkpt('novelty', …) persists for a live run, with source $SB/Sources/<img src=x onerror="document.title='XSS-from-folder-name'">, then reloaded.
3. After the reload, document.title == 'XSS-from-folder-name' and the banner HTML contains a real <img> element.
4. macOS folder names may legally contain < > " =, for example on a found or shared backup drive.
- **Fix:** - Build the banner with createElement and textContent (as undo.js already does).
- Audit the other innerHTML sinks that take file or folder names (the openCard glossary is static; check others).
- Add a test with a '<img onerror>' folder name.

#### [MEDIUM · demonstrated] Undo Wizard Savepoints and Trash tabs throw ReferenceError, and the tab row is clipped at 1440px

- **ID:** `tool-novelty-c-undo-tabs-broken` · **Area:** undo-revert · **Tool/surface:** Undo Wizard
- **Expected:** All tabs load their lists, and all tab buttons can be reached.
- **Actual:** Savepoint restore and pruned-file trash restore cannot be reached from the UI.
- **Evidence:** 1. Clicking the Savepoints and Trash tabs raised PAGEERROR 'undoLoadSavepoints is not defined' and 'undoLoadTrash is not defined'. Both tab bodies show only the blurb (u1_preview_tab_savepoints.png, u1_preview_tab_trash.png).
2. undo.js:39-40 calls these functions, but they are defined nowhere in static/ or templates/ (grep). undoViewJobDetail and undoRestoreCheckpoint, used by Timeline buttons, are also undefined.
3. At 1440×900 the tab row is clipped: 'SA…' is visible and Trash is off-panel (u1_revert_twice_preview.png).
This is cross-cutting rather than novelty-specific (novelty creates no savepoints or trash), but it is the same undo surface.
- **Fix:** - Implement (or restore from git history) undoLoadSavepoints, undoLoadTrash, undoViewJobDetail and undoRestoreCheckpoint using the existing /api/undo/savepoints, /trash, /job endpoints.
- Let the tab row wrap or scroll.
- Add a Playwright smoke test that clicks every tab and fails on pageerror.

#### [LOW · demonstrated] Undo UI wording is wrong for novelty copies, and revert errors are silently dropped

- **ID:** `tool-novelty-c-undo-wording-errors-hidden` · **Area:** undo-revert · **Tool/surface:** Undo Wizard > Operations
- **Expected:** Novelty-specific copy ('Remove N copied files (parked in Archive/Undone Copies)', 'already removed') and a visible error count with details.
- **Actual:** The wording is misleading and partial failures are invisible.
- **Evidence:** 1. For novelty_copy the preview says 'N files can be returned', the button says 'Return N files', and after the first revert 'blocked (moved again since, or the original slot is taken)'. The action actually removes the copies to Archive/Undone Copies, and the blocked items were simply already undone (u1_revert_twice_preview.png; undo.js:182-206).
2. Final revert: the preview said 1335 revertible, but only 1322 undo rows were written. The server log has 13 'OSError during undo of …/Rescue6/… No such file or directory', caused by double-journaled paths.
3. The toast uses only out.reverted and ignores out.errors and out.blocked (undo.js:202).
- **Fix:** - Use per-op-type labels for the summary, button and blocked reason (the plan items already carry action='remove_copy' and a reason).
- Show out.errors in the toast or detail.
- De-duplicate plan items by current path.

#### [LOW · demonstrated] Undo Operations times are shifted by the UTC offset

- **ID:** `tool-novelty-c-undo-time-utc` · **Area:** undo-revert · **Tool/surface:** Undo Wizard > Operations
- **Expected:** Local wall-clock time of the run.
- **Actual:** On a US Mac every journal session shows a time 7–8 hours later than it really was, which makes picking the right session even harder (see the grouping finding).
- **Evidence:** 1. fg_processing_log.completed_at is SQLite CURRENT_TIMESTAMP, which is UTC with no zone ('2026-10-05 18:46:55').
2. _undoFormatTime does new Date(iso), which parses that as local time (undo.js:322-325).
3. With a Playwright context set to timezone_id='America/Los_Angeles', the row showed 'Oct 5, 6:46 PM'. The run actually happened at 11:46 AM PDT (18:46 UTC).
- **Fix:** Store ISO-8601 with 'Z' (or datetime('now') plus an explicit zone), or append 'Z' when parsing values with no zone in the UI.

#### [LOW · demonstrated] Card text and dead UI do not match behavior (folder layout, scan index, Rekordbox block, Pipeline has no Copy-To)

- **ID:** `tool-novelty-c-ui-copy-inaccurate` · **Area:** chop-shop-tool · **Tool/surface:** templates/partials/physical_library/novelty.html
- **Expected:** The UI text matches the behavior, and the Pipeline has the same options as the card.
- **Actual:** Misleading guidance about where files land and what the scan index does.
- **Evidence:** 1. The explainer says 'Already-structured files keep their folder layout' (novelty.html:24). In r2, BackupDrive/Old Stuff/Track 01.wav landed in Music Library/Orphaned Tracks/2026/Track 01.wav, and untagged files always go to Orphaned Tracks/<year>.
2. The explainer says tracks with scan-index data 'skip straight past Chromaprint' (line 17). In code, metadata makes more candidates (see the candidate-explosion finding).
3. The '#novelty-rb-block' message ('Close Rekordbox before running a live scan', lines 93-95) is never toggled: there is no checkRbBlock call, and the route has no Rekordbox check. The tool does not need one.
4. The novelty_scanner docstring says the destination index is built from 'scan_index.json and/or the rekordbox DB', but only scan_index and the filesystem are used.
5. The Pipeline novelty step offers no Copy-To (pipeline.js:483-495; routes_tools.py:566-581), so Pipeline runs always copy into the compared library.
- **Fix:** - Rewrite the explainers.
- Remove the dead Rekordbox message (or wire it consistently).
- Add copy_to to the Pipeline novelty config and the /api/run/pipeline builder.

#### [INFO · demonstrated] Confirmed good: copies never move or modify the source, never touch Rekordbox, and the revert is idempotent

- **ID:** `tool-novelty-c-boundary-good` · **Area:** boundary · **Tool/surface:** Novelty Scanner
- **Expected:** n/a
- **Actual:** Works as designed.
- **Evidence:** Across 15 UI/API/CLI runs and 4 reverts:
- Every pre-existing file under $SB/Sources and $SB/Incoming kept its sha256 ('Sources unchanged: True').
- Copies were byte- and mtime-identical (Newcomer - Fresh Cut.mp3 5535d5178ad5…, mtime 17:59:10.661 on both).
- The local master.db (9033b2fe38df…) and device master.db (d3c1a1cf75e3…) were unchanged, and no savepoint was created or needed.
- FableGear DB changes were limited to fg_processing_log (novelty_copy and novelty_scan rows); fg_content stayed at 11 rows, so copied tracks do not appear in the Record Room until a sync or Import.
- A second revert returned {reverted 0, blocked 3}, with no file changes.
- Dry Run is checked by default, and the live label clearly names the copy target.
- **Fix:** Keep these as regression tests. Consider telling the user after a live run into the home library that the new tracks still need Import or a sync to appear in the Record Room.

#### [INFO · demonstrated] Every UI page load runs a silent Audit that writes an Audit report and updates the library state file

- **ID:** `tool-novelty-c-page-load-audit-report` · **Area:** other · **Tool/surface:** runSilentAudit (static/shared/audit.js:9-27)
- **Expected:** Background audits that change nothing should not create a report file each time, or should be rate-limited.
- **Actual:** One report file per page load or app launch.
- **Evidence:** 1. Each Playwright page load added 'FableGear Archive/Reports/Audit/audit_<ts>.txt'. Snapshot diffs show audit_184531, 184620, 184649, 184735, 184816, 184843, 184907, … one per load.
2. Each load also rewrote 'Music Library/.fablegear_state.json' (steps_completed.audit).
3. This is noise in the Archive and a write to the library root on every app open.
- **Fix:** Write silent-audit reports only when results change or once a day, or keep them in Logs with rotation.

## Chop Shop: Pipeline Wizard

_Auditor: `tool-pipeline`_

Round 3 live audit of the Chop Shop Pipeline Wizard (static/chop_shop/pipeline.js, POST /api/run/pipeline in routes_tools.py:366, _stream_pipeline in helpers.py:1103). Sandbox pipeline-r3 on port 8118, driven through the real UI with headless Chromium. I took sha256, tag, DB and undo-API snapshots before and after every run. Covered: dry run of 9 step types; a live 5-step run followed by reverts through the undo endpoints; a failing middle step; Cancel mid-run; kill -9 of the server mid-run, then relaunch and re-run; a confirm-mode checkpoint, relaunch, then Resume.

Overall verdict: the pipeline has no working session model and no run-level undo.
- In the default (auto) mode it never writes a checkpoint, so a crash or cancel offers nothing to resume. The only mode that saves a checkpoint (confirm between steps) is the one that locks the UI (E4).
- "Resume" re-runs every step from step 1, even though the UI shows it starting at step N. A re-applied Rename step moved a file out of the library on its second pass.
- "Dry Run (preview only)" is on by default, yet it wrote tags to every file and wrote a new row into the FableGear DB from the Import step.
- There is no restore point for the run as a whole. Only the organize and rename moves can be reverted. Tag writes, normalisation and the Import DB row cannot. Revert order is wrong for chained moves of the same file.
- A kill -9 left the cli.py child running unattended and still writing files. A Cancel left a truncated temp audio file inside the music library.

Confirmed good: steps run in order; a failing step stops the run cleanly and is marked failed in the UI; each step writes its own report under FableGear Archive/Reports/<Tool> (Link Playlists excepted); DB-writing steps take a master.db savepoint before writing; convert and relocate are refused in dry run; reverting organize and rename twice is safe.

**Coverage**

- LIVE (round 3, sandbox sbx-pipeline-r3, port 8118, driven through the UI): dry-run pipeline audit, process, rename, duplicates, prune, import, link, organize, novelty with snapshots r3_s01 to r3_s02 (scripts and outputs: /tmp/claude-0/-home-user-FableGear/9074577f-2e6a-5e16-bdce-ef5b26f1b16b/scratchpad/audit/tool-pipeline/r3_t1_dry.py, out/r3t1_dry.json, snaps/r3_s0*.json).
- LIVE: 'Load recommended order' with the default Dry Run, which is refused because of relocate (screens/tool-pipeline/r3/r3t1b_recommended_dry_01_recommended_dry_refused.png).
- LIVE: live auto-mode run process, rename, import, link, organize, then reverts through /api/undo/operations/revert (organize and rename, each twice) and /api/undo/database/revert (import), then re-hash against BEFORE (r3_t2_live.py, snaps r3_s10/s11/s12, out/r3_undo_*.json).
- LIVE: kill -9 of the server while Tag Tracks was running (19 files, including 8 long mp3s): checked orphaned cli.py processes, restarted, reloaded the UI with the same browser profile (same localStorage), checked the resume banner, /api/checkpoint/check and ~/.fablegear/checkpoints, then re-ran the same pipeline and counted per-file actions (r3_t3_kill.py, r3_t3b_relaunch.py, out/r3t3_kill*.json, out/r3_t3b.log, snaps r3_s20 to s24).
- LIVE: confirm-between-steps run (rename, process) up to the step-1 checkpoint, then restart.sh, Resume banner, Resume, Run, with the request body captured (r3_t4_resume.py, out/r3_t4.log, snaps r3_s30 to s33).
- LIVE: failing middle step (import of a typo path) and Cancel mid Tag Tracks through #tfm-interrupt-btn (r3_t5_fail_cancel.py, out/r3_t5.log, snaps r3_s40/s41).
- LIVE via API: dry run of the 'normalize' step (it has no UI button) and dry run of convert, which is refused (out/r3_normdry.sse, snaps r3_s50/s51).
- ROUND 2 artifacts reused as corroboration (same scripts, earlier sandbox sbx-pipeline-r; moved to prev_r2/ under the same label dir): full recommended live run including relocate; cancel run t4 (exit -15, no report).
- CODE: pipeline.js (checkpoint save, resume and finish logic), routes_tools.py:366-592, helpers.py:1103-1190, state_tracker.py and state_tracker.js, routes_undo.py (_SESSION_GAP_SEC, ORDER BY id, database/revert), audio_processor.py:500-545 (normalise temp file).
- NOT TESTED: prune actually deleting or trashing files. fpcalc (chromaprint) is absent and exact-hash matching found 0 duplicate groups, so prune had nothing to act on. Convert run live inside the pipeline (E8 covers convert itself). Novelty run live. AcoustID enrichment (fpcalc absent). Closing the window or navigating away mid-run: the SSE generator's finally sends SIGTERM to the step (helpers.py:1150-1160); this is inferred only. The Rekordbox-running gate (no Rekordbox process on Linux). WKWebView-specific behaviour.
- Housekeeping: my sandbox server was stopped by PID; nothing is left listening on port 8118. Note that restart.sh writes the PID of its backgrounding subshell to server.pid, not the PID of python app.py, so `kill $(cat server.pid)` after a restart.sh does not stop the server. I located the real PID through /proc/<pid>/cwd.

**Per-tool safety matrix**

### Pipeline Wizard (auto mode, the default)

| Check | Result |
|---|---|
| R — Report | PARTIAL. Each step writes its own report: Archive/Reports/{Audit,Tag Tracks or Normalize,Rename,Duplicates,Prune,Import,Organize,Novelty Scan,Relocate}/*.txt. Link Playlists writes none. There is no pipeline-level report. The UI keeps only the last step's FABLEGEAR_REPORT block as one 'Pipeline Summary' pill with reportPath null (pipeline.js:1028, 1140). Archive/Logs/<tool> folders are created but hold 0 files. A cancelled step writes no report. On failure, the failed step's report may be missing. |
| M — Revert marker | NONE for the run as a whole. Per step: rename and organize write op-journal rows; rename, import, link and relocate each write an unlabelled master.backup_*.db in Archive/Savepoints. Nothing references the pipeline run or job. /api/undo/timeline stays [] (E1). |
| U — Undo | PARTIAL. Organize (11 files) and rename (1 file) revert through /api/undo/operations/revert, and a second revert is blocked safely. Tag and normalise writes cannot be undone (E9). The Import FG DB row is refused by /api/undo/database/revert. After the reverts, all 11 audio files still differ from BEFORE. |
| C — Cancel / interrupt | Stops the current step (exit -15) and skips the rest, but the UI says 'had an error', not 'stopped by user'. No checkpoint, no report for the cancelled step. Left a truncated temp audio file (tmp_8hukl03.flac) in Music Library/Techno. |
| S — Resume after restart | NONE. Auto mode never saves sb_pipe_checkpoint (only confirm mode does, pipeline.js:1059). After kill -9 and relaunch: no banner, /api/checkpoint/check returns exists:false, ~/.fablegear/checkpoints does not exist. The orphaned cli.py kept writing for at least 21s after the crash. |
| D — Dry run / preview | Default ON, labelled 'preview only', but NOT side-effect free. Process wrote key tags to all 11 files and BPM to the m4a. Import inserted an fg_content row plus an 'Imported 1 files' transaction. A dry run containing relocate or convert is refused with HTTP 400, which includes the recommended pipeline. |
| B — Boundary | Steps write the Rekordbox DB (rename, relocate, import and link open master.db write sessions, each with a savepoint) and the FableGear DB (process: fg_content and fg_processing_log; import: fg_content plus transaction_history). Savepoints are unlabelled, and restore targets the device DB (E10). The FG DB import write cannot be reverted. |

_Evidence:_ r3_t1_dry.py / r3_t2_live.py / r3_t3*.py / r3_t5_fail_cancel.py; snaps r3_s01-s51; screens/tool-pipeline/r3/*.png

### Pipeline Wizard (confirm between steps, plus Resume)

| Check | Result |
|---|---|
| R — Report | Same per-step reports. The gate summary never renders (E4). |
| M — Revert marker | None at run level. The localStorage checkpoint holds steps and config only; no restore point. |
| U — Undo | Same as auto mode. A re-run inside the same 15 minutes merges with the original run in /api/undo/operations, and chained moves revert in the wrong order (two reverts needed). |
| C — Cancel / interrupt | Gate Stop button is in a hidden container (E4); isRunning stays true. |
| S — Resume after restart | The banner appears after relaunch ('Checkpoint — resume at step 2 of 2: "Tag Tracks" / Saved just now · Live Run'), but Resume re-sends all steps from step 1 (request body ['rename','process']) and forces confirm mode back on (pipeline.js:71). The checkpoint lives only in localStorage. |
| D — Dry run / preview | Restored from the checkpoint's dryRun flag. |
| B — Boundary | Same as auto mode. |

_Evidence:_ out/r3_t4.log; screens r3t4_confirm_01_stuck_after_step1.png, r3t4_resume_01_resume_banner.png, r3t4_resume_02_after_resume_click.png

### step: audit (in pipeline)

| Check | Result |
|---|---|
| R — Report | Archive/Reports/Audit/audit_*.txt |
| M — Revert marker | n/a (read-only) |
| U — Undo | n/a |
| C — Cancel / interrupt | not tested |
| S — Resume after restart | n/a |
| D — Dry run / preview | Report only. Also writes .fablegear_state.json. |
| B — Boundary | Reads both DBs |

_Evidence:_ r3_s01->s02

### step: process / Tag Tracks (in pipeline)

| Check | Result |
|---|---|
| R — Report | Reports/Tag Tracks/ or Reports/Normalize/ when normalize_mode is on |
| M — Revert marker | none |
| U — Undo | none (E9) |
| C — Cancel / interrupt | SIGTERM mid-normalise leaves a tmp*.<ext> file in the track's folder |
| S — Resume after restart | Re-run is mostly idempotent: passive BPM/key skips tagged files and loudness inside the 0.5 LU tolerance is skipped, so no double gain was observed. The AIFF key tag is rewritten on every run. |
| D — Dry run / preview | WRITES tags (11 of 11 files changed) and FG DB rows |
| B — Boundary | Writes fablegear.db fg_content and fg_processing_log |

_Evidence:_ r3_s01->s02; out/r3t3_kill_relaunch.json (rerun: no 'Normalising' line for the 5 files normalised before the crash)

### step: rename (in pipeline)

| Check | Result |
|---|---|
| R — Report | Reports/Rename/*.txt (terse; says 'check the log above') |
| M — Revert marker | op-journal rows plus master.db savepoint |
| U — Undo | Reverts work. Chained rename then quarantine needs two reverts (ORDER BY id). The quarantine manifest is left stale. |
| C — Cancel / interrupt | not isolated |
| S — Resume after restart | NOT idempotent: a second pass quarantined the file the first pass had renamed |
| D — Dry run / preview | No changes |
| B — Boundary | Opens a RB write session (0 rows updated in the sandbox) |

_Evidence:_ out/r3_t4.log; r3_s31->s32->s33

### step: import (in pipeline)

| Check | Result |
|---|---|
| R — Report | Reports/Import/import_*.txt or preview_import_*.txt |
| M — Revert marker | master.db savepoint plus FG transaction with affected_records [] |
| U — Undo | Refused: "'import' transactions don't carry per-record history" |
| C — Cancel / interrupt | not tested |
| S — Resume after restart | n/a |
| D — Dry run / preview | WRITES the FG DB (new fg_content row for Incoming/Newcomer - Fresh Cut.mp3, plus a transaction_history entry) |
| B — Boundary | Writes the FG DB. The RB sync failed with IntegrityError and rolled back, but the step still exited 0. |

_Evidence:_ r3_s01->s02 undo_db_history; out/r3t2_live.json

### step: link (in pipeline)

| Check | Result |
|---|---|
| R — Report | No report file (dry run or live) |
| M — Revert marker | master.db savepoint (live) |
| U — Undo | Savepoint restore only (E10 caveat) |
| C — Cancel / interrupt | not tested |
| S — Resume after restart | n/a |
| D — Dry run / preview | No writes ('DRY RUN — no playlist rows will be written') |
| B — Boundary | RB write session |

_Evidence:_ out/r3t1_dry.json, r3t2_live.json

### step: organize (in pipeline)

| Check | Result |
|---|---|
| R — Report | Reports/Organize/*.txt |
| M — Revert marker | op-journal rows |
| U — Undo | 11 of 11 moved back; second revert blocked safely |
| C — Cancel / interrupt | not reached (cancelled earlier) |
| S — Resume after restart | Re-run moves whatever is in the source, including stray tmp files |
| D — Dry run / preview | No moves |
| B — Boundary | Updates FG DB paths |

_Evidence:_ out/r3_undo_org1.json, r3_undo_org2.json

### step: duplicates / prune / novelty (in pipeline)

| Check | Result |
|---|---|
| R — Report | Reports/Duplicates, Reports/Prune, Reports/Novelty Scan |
| M — Revert marker | not exercised (0 duplicate groups; novelty dry run only) |
| U — Undo | not exercised |
| C — Cancel / interrupt | not tested |
| S — Resume after restart | not tested |
| D — Dry run / preview | No file changes; processing-log rows written |
| B — Boundary | fg_processing_log only |

_Evidence:_ r3_s01->s02 (fpcalc absent, so exact-hash matching only)

### step: relocate / convert / normalize (in pipeline)

| Check | Result |
|---|---|
| R — Report | relocate: Reports/Relocate (round 2 live run); normalize via API: Reports/Tag Tracks |
| M — Revert marker | relocate: savepoint; convert: none (E8) |
| U — Undo | relocate: op-journal type 'relocate'; convert: refused (E8) |
| C — Cancel / interrupt | not tested |
| S — Resume after restart | not tested |
| D — Dry run / preview | relocate and convert: HTTP 400 refusal. normalize: no audio writes (good), but it has no UI button. |
| B — Boundary | relocate writes the RB DB |

_Evidence:_ out/r3_normdry.sse; curl outputs in this report

**Findings**

#### [HIGH · demonstrated] Pipeline 'Resume' re-runs every completed step from step 1; a re-applied Rename moved a file out of the library

- **ID:** `tool-pipeline-resume-reruns-completed-steps` · **Area:** session-resume · **Tool/surface:** Pipeline Wizard
- **Expected:** Resume starts at completedIdx+1, shows earlier steps as done or skipped, and checks that inputs have not changed since the checkpoint.
- **Actual:** Resume restarts the whole pipeline. Completed steps are applied again, and Rename is not idempotent: its second pass quarantined a file the first pass had renamed.
- **Evidence:** r3_t4_resume.py (out/r3_t4.log). Confirm-mode live run of rename then process. After step 1, localStorage sb_pipe_checkpoint = {steps:[rename,process], completedIdx:0, dryRun:false}. Ran restart.sh and reloaded. Banner: 'Checkpoint — resume at step 2 of 2: "Tag Tracks" / Saved just now · Live Run' (screens/tool-pipeline/r3/r3t4_resume_01_resume_banner.png). Clicking Resume focuses step 2 and forces 'Confirm between steps' back on (r3t4_resume_02_after_resume_click.png). With confirm unchecked, the captured request was RESUMED REQUEST steps: ['rename','process']. The second Rename logged 'Rename complete: 0 renamed, 4 skipped, 0 collisions handled, 1 quarantined'. Snapshot diff r3_s31 to r3_s32: REMOVED Music Library/Sample Packs/kick - 01.wav, ADDED Volumes/DJDRIVE/No-Name tracks for Tagging/kick - 01.wav. The first pass had renamed kick_01.wav to 'kick - 01.wav'. Code: resumeFromCheckpoint rebuilds all steps (pipeline.js:47-78) and keeps no start index; confirm mode loops from `let i = 0` (pipeline.js:1035); auto mode posts every step (pipeline.js:1105).
- **Fix:** Store startIdx (and a run id) in the checkpoint and send only steps[startIdx:]; mark earlier steps 'done' in the sidebar; don't force confirm mode on resume (pipeline.js:71) until E4 is fixed; before resuming, compare a file manifest (path/size/mtime) captured at checkpoint time and warn if the inputs changed.

#### [HIGH · demonstrated] Default (auto) pipeline never saves a checkpoint: after a crash, cancel or failure nothing is offered to resume

- **ID:** `tool-pipeline-no-checkpoint-auto-mode` · **Area:** session-resume · **Tool/surface:** Pipeline Wizard
- **Expected:** Any interrupted run (crash, cancel, failed step) leaves a server-side record (run id, start time, roots and config, steps done and remaining, restore point) and is offered for resume after relaunch.
- **Actual:** No record survives. The only code path that writes a checkpoint is the confirm mode that locks the UI, and even that is browser localStorage only.
- **Evidence:** r3_t3_kill.py / r3_t3b_relaunch.py. Live auto run of rename, process and organize over 19 files. kill -9 of the server when Tag Tracks had 4 actions done; UI log: '[Connection error] network error ✗ Exited with code 1'. ls ckpt: None, rb_ckpt keys: []. After restart: ~/.fablegear/checkpoints does not exist; /api/checkpoint/check?tool=process|rename|organize all return {"exists":false}; visible resume banners []; pipe-resume-banner hidden (r3t3_kill_relaunch_01_relaunch_wizard.png). .fablegear_state.json lists only 'audit' and 'Rename Files', with no record of the interrupted Tag Tracks step. Cancel (r3_t5) and failing-step (r3_t5) runs also left 'FAIL ckpt: None' / 'CANCEL ckpt: None' and no banner. Code: _savePipeCheckpoint is called only at pipeline.js:1059 (confirm mode), and confirm mode is unusable (E4). The checkpoint lives only in localStorage (pipeline.js:756-771).
- **Fix:** Write a server-side pipeline run record before step 1 and update it on every step_end (e.g. ~/.fablegear/checkpoints/pipeline/<run_id>.json); have /api/checkpoint/check support tool=pipeline; render the banner from the server record; also write it on cancel and failure.

#### [HIGH · demonstrated] Pipeline 'Dry Run (preview only)', which is on by default, writes audio tags and the FableGear DB, including a new Import row

- **ID:** `tool-pipeline-dry-run-writes` · **Area:** chop-shop-tool · **Tool/surface:** Pipeline Wizard (process + import steps)
- **Expected:** A run labelled 'preview only' changes nothing but report files.
- **Actual:** Tags are written to every file, and the Import preview inserts a FableGear DB row and a transaction that /api/undo/database/revert refuses to revert.
- **Evidence:** r3_t1_dry.py with dry-run-2 checked. Diff r3_s01 to r3_s02: all 11 audio files CHANGED (e.g. Vera Lux - Pressure.flac gains initialkey 11B; Glitterball.m4a gains bpm 162). fgdb.fg_content has keys written for 11 rows plus a NEW row [23, Incoming/Newcomer - Fresh Cut.mp3]. /api/undo/database/history gains {operation_type:'import', description:'Imported 1 files from 1 drives', affected_records: []}. fg_processing_log gains 'tag_tracks ok key_written 11' and 'import ok new 1'. The CLI itself logs 'DRY RUN — loudness normalisation suppressed. BPM/key tag writes will still occur unless --no-bpm / --no-key are set.' Round 2 produced the identical diff (prev_r2/snaps/s01_before_dry vs s02_after_dry). Per step type: audit, rename, duplicates, prune, link, organize and novelty made no file changes; normalize via the API made no audio writes (r3_s50 to s51). Extends E9 with the Import dry-run DB write.
- **Fix:** When dry_run is set, route the process step to a pure analysis/preview path (no _write_tags) and the import step to a no-write preview (no importer.commit and no history record); add a regression test that hashes the library and DB before and after a dry-run pipeline.

#### [HIGH · demonstrated] No 'revert to before this pipeline run' marker; per-step undo is partial and not linked to the run

- **ID:** `tool-pipeline-no-run-level-revert` · **Area:** undo-revert · **Tool/surface:** Pipeline Wizard
- **Expected:** Before step 1 the pipeline creates a restore point (file manifest plus hashes, DB snapshots) recorded against a run id, and the undo UI offers 'Revert pipeline run at <time>', which restores byte-identical files and DB rows.
- **Actual:** The user must find and revert each step's artefacts separately. Tag, normalise and import changes cannot be reverted, and nothing ties the savepoints or journal rows to the pipeline run.
- **Evidence:** r3_t2_live.py: live process, rename, import, link, organize. Afterwards /api/undo/operations shows two unrelated sessions (organize ids 7-17, rename id 4); /api/undo/savepoints shows three unlabelled master.backup_*.db files (taken by rename, import and link); /api/undo/timeline returns [] (E1). Reverts: organize {reverted:11}, again {blocked:11}; rename {reverted:1}, again {blocked:1}; database/revert of the import transaction returns {"error":"'import' transactions don't carry per-record history and can't be reverted this way."}. Diff r3_s10 (BEFORE) to r3_s12 (after all reverts): all 11 audio files still CHANGED (tags and normalisation, E9), fg_content still has the imported row, and .fablegear_state.json and the archive DB copies remain. The savepoints back up the local master.db, but restore targets the device DB (E10).
- **Fix:** Generate a run_id in api_pipeline; pass it to every CLI step (env FABLEGEAR_RUN_ID) so that op-journal rows, savepoints and DB transactions carry it; snapshot the tag and audio state of every file a step will mutate (or hard-link copies into Archive/Trash/<run_id>) before mutating; expose /api/undo/run/<run_id>/preview and /revert.

#### [MEDIUM · demonstrated] After the server is killed (app crash) the step's cli.py keeps running unattended and keeps mutating files; the run ends half-applied with no record

- **ID:** `tool-pipeline-orphan-continues-after-crash` · **Area:** chop-shop-tool · **Tool/surface:** Pipeline Wizard
- **Expected:** Either the job dies with the app and is recorded as interrupted, or it continues as a tracked job that the relaunched app reconnects to and reports.
- **Actual:** The work continues invisibly for a while and then stops. The library is left half-processed, with no report, checkpoint or UI indication.
- **Evidence:** r3_t3_kill.py. After `kill -9 <server pid>`, cli_procs at 2s showed '21364 1 9 .../venv/bin/python $S/app/cli.py process ...' (PPID 1) and at 10s '21364 1 21 ...'. Diff r3_s21 (at kill) to r3_s22 (10s later): BPM tags written to Longform Drone 01/03/07/08, Soft Hands - Whisper.mp3 and kick - 01.wav re-encoded, temp file Longform/tmpgylvx1ra.mp3 created. Final state r3_s24: 8 of 19 files modified, 11 untouched, and the organize step never ran. ~/.fablegear/runtime/active_subprocesses.json still listed pid 21364 after restart. Code: helpers.py:1141 Popen(start_new_session=True) detaches the child from the server's lifetime.
- **Fix:** On startup, read the subprocess registry, report surviving or dead job PIDs and mark their runs interrupted; make the CLI exit cleanly on stdout EOF or BrokenPipe, or have the server use a parent-death signal; persist a run record (see the no-checkpoint finding) so relaunch can say exactly which files were done.

#### [MEDIUM · demonstrated] Cancel during normalisation leaves a truncated temp audio file inside the music library, and later steps then treat it as a track

- **ID:** `tool-pipeline-cancel-temp-file` · **Area:** chop-shop-tool · **Tool/surface:** Pipeline Wizard (process step)
- **Expected:** Cancel leaves the library exactly as it was, or as it was after the last completed file. Temp files are invisible to scans and cleaned up on SIGTERM.
- **Actual:** A partial audio file with real tags is left among the tracks, where later Tag, Organize or Import steps will pick it up.
- **Evidence:** r3_t5_fail_cancel.py: clicked #tfm-interrupt-btn while Tag Tracks was normalising. Diff r3_s40 to r3_s41: ADDED 'Music Library/Techno/tmp_8hukl03.flac' (246,841 bytes; the original Vera Lux - Pressure.flac is 488,347 bytes and was untouched). `find $S/Volumes -name 'tmp*'` still lists it. The next run (API normalize dry run) reported files_processed 11, which counts the tmp file as a library track. Code: audio_processor.py:509 `tempfile.mkstemp(suffix=suffix, dir=path.parent)` creates a file with an audio extension next to the track; cleanup is in try/finally, but cli.py installs no SIGTERM handler, so /api/cancel's SIGTERM skips it. Also seen after kill -9 (tmpgylvx1ra.mp3).
- **Fix:** Write temps with a non-audio suffix (e.g. '.fgtmp') or into Archive/Tmp on the same volume; install a SIGTERM handler in cli.py that raises SystemExit so finally blocks run; sweep stray temp files at the start of each run.

#### [MEDIUM · demonstrated] User cancel is reported as a step error; no report and no summary of what was already applied

- **ID:** `tool-pipeline-cancel-reported-as-error` · **Area:** chop-shop-tool · **Tool/surface:** Pipeline Wizard
- **Expected:** Cancel is shown as 'stopped by you after step 1 of 3; step 2 partially applied to N files', with a partial report and a checkpoint.
- **Actual:** Cancel looks like a tool failure, and the user is not told that step 1 was fully applied and step 2 partly applied.
- **Evidence:** r3_t5 cancel: EV {'step_end': 2, 'step_name': 'Tag Tracks', 'exit_code': -15}, {'done': True, 'exit_code': -15, 'failed_step': 'Tag Tracks'}. Log tail: '✗ Pipeline stopped — "Tag Tracks" had an error. | Interrupt signal sent — waiting for process to exit…'. No FABLEGEAR_REPORT_PATH for step 2; 'CANCEL ckpt: None'; reopening the wizard shows no banner. Round 2 t4 showed the same (prev_r2/out/t4_cancel.json). The 'Pipeline stopped by user' branch (pipeline.js:1017) is reachable only from the confirm gate, which is hidden (E4).
- **Fix:** Treat a negative or SIGTERM exit after /api/cancel as stopped=true; emit a pipeline summary event listing per-step exit codes and report paths; have the CLI flush a partial report from its signal handler.

#### [MEDIUM · demonstrated] Re-run merges with the earlier run in the undo journal, and chained moves of one file revert in the wrong order (two reverts needed)

- **ID:** `tool-pipeline-undo-chain-order` · **Area:** undo-revert · **Tool/surface:** Pipeline Wizard (rename step) / routes_undo.py
- **Expected:** One revert restores the original name, by processing journal rows newest-first. Sessions are grouped by run id, not by a time gap. The quarantine manifest is updated on revert.
- **Actual:** The first revert only partly works and gives a misleading reason. The user has to revert repeatedly, and the manifest goes stale.
- **Evidence:** After the resumed run (tool-pipeline-resume-reruns-completed-steps), /api/undo/operations showed ONE rename session {count:2, first_id:3, last_id:5, sample:['kick - 01.wav','kick - 01.wav']}, merging the original run and the resumed run (routes_undo.py:322 _SESSION_GAP_SEC = 15*60). Preview: id 3 {ok:false, reason:'file is no longer at the organized location'}, id 5 {move_back}. Revert #1 gave {reverted:1, blocked:1}: kick back at 'Sample Packs/kick - 01.wav'. Revert #2 gave {reverted:1, blocked:1}: kick_01.wav restored. Revert #3 gave {blocked:2}. 'No-Name tracks for Tagging/_quarantine_manifest.json' still lists the file. /api/undo/operations keeps showing reverted sessions as revertible:true. Cause: plan rows are processed in ascending order (routes_undo.py:337 ORDER BY id).
- **Fix:** Order revert plans by id DESC and evaluate each item against the state after the newer items are reverted; group sessions by run_id; remove manifest entries on revert; mark sessions as reverted.

#### [MEDIUM · demonstrated] Pipeline keeps only the last step's report in the UI, writes no run report, and the Link step writes no report file

- **ID:** `tool-pipeline-reports-collapsed` · **Area:** chop-shop-tool · **Tool/surface:** Pipeline Wizard
- **Expected:** One pipeline run report (run id, start and end, config, per-step exit code, counts and report path) saved in Archive/Reports/Pipeline and linked from the summary pill. Every step writes a report.
- **Actual:** Step reports are scattered across tool folders with nothing linking them, and the UI shows only the final step.
- **Evidence:** r3_t1 dry run of 9 steps: sessionReports = [['Pipeline — Dry Run (preview only)', 480, 'Novel Track Scan complete…', null]], i.e. only the 9th step. r3_t2 live run: [['Running Pipeline', 115, 'Done organizing…', null]]. Per-step files exist for 8 of 9 step types in Archive/Reports/<Tool>/, but none for Link Playlists in either the dry or live run. Tag Tracks reports go to Reports/Normalize/ when normalize_mode is on. Archive/Logs/{Audit,Duplicates,Import,Normalize,Prune,Relocate,Tag Tracks} exist but contain 0 files. Rename report body: '6 files had errors — check the log above.' (no file list). Code: reportBuffer is reset on every FABLEGEAR_REPORT_BEGIN (pipeline.js:1140); reportPath is hard-coded null (pipeline.js:1028).
- **Fix:** Accumulate a per-step report array keyed by step index; write Reports/Pipeline/pipeline_<ts>.txt from _stream_pipeline (it already sees every FABLEGEAR_REPORT_PATH); make the Link step emit a report; include failing file names in reports.

#### [MEDIUM · demonstrated] The recommended pipeline cannot be previewed: Dry Run is on by default but relocate is refused, so the whole preview fails

- **ID:** `tool-pipeline-recommended-cannot-preview` · **Area:** chop-shop-tool · **Tool/surface:** Pipeline Wizard
- **Expected:** The recommended order is previewable, with steps that have no preview shown as 'will be skipped in preview' or previewed read-only.
- **Actual:** The user must delete a recommended step or run live without any preview.
- **Evidence:** r3_t1_dry.py part b: 'Load recommended order' gives ['process','rename','duplicates','prune','relocate','import','link','organize'] (runners.js:412). Run with the default Dry Run. Log: 'Pipeline error: Step 'Fix Broken Paths' (relocate) has no preview mode — remove it from this dry run, or run the pipeline live. ✗ Exited with code 1' (screens/tool-pipeline/r3/r3t1b_recommended_dry_01_recommended_dry_refused.png). Code: routes_tools.py:524-529 (relocate) and 507-512 (convert).
- **Fix:** In dry-run mode, skip non-previewable steps with a clear 'not previewed' line instead of a 400, or add a read-only relocate preview (list the rows that would change).

#### [MEDIUM · demonstrated] Pipeline resume banner has no starting-point reference and lives only in localStorage

- **ID:** `tool-pipeline-resume-banner-no-starting-point` · **Area:** session-resume · **Tool/surface:** Pipeline Wizard
- **Expected:** 'You were running <steps> on <roots>, started <time>; steps 1-k done (reports…); restore point <savepoint or trash id>; inputs unchanged or changed since.'
- **Actual:** Only the next step's name and the save age are shown. Clearing WebView storage loses it.
- **Evidence:** Banner text after relaunch: 'Checkpoint — resume at step 2 of 2: "Tag Tracks" / Saved just now · Live Run' (r3t4_resume_01_resume_banner.png). Checkpoint payload (localStorage sb_pipe_checkpoint): {steps:[{type,_config}], completedIdx, dryRun, ts}. It has no run start time, no roots summary, no completed or remaining file counts, no restore point and no report links. Nothing server-side: /api/checkpoint/check has no 'pipeline' tool (routes_tools.py:851).
- **Fix:** Back the banner with the server-side run record proposed above, and include a 'Revert to before this run' action next to Resume.

#### [LOW · demonstrated] Import step reports success (exit 0, '0 errors') while its Rekordbox sync rolled back; the pipeline marks it done and continues

- **ID:** `tool-pipeline-import-silent-rb-failure` · **Area:** boundary · **Tool/surface:** Pipeline Wizard (import step)
- **Expected:** A rolled-back Rekordbox write makes the step fail (or warn), and the pipeline stops or flags it.
- **Actual:** The step is marked successful and later steps run on the assumption that the tracks are in Rekordbox.
- **Evidence:** out/r3t2_live.json step 3: 'ERROR key_mapper — Failed to resolve/create key row for 'Bm'', 'IntegrityError: NOT NULL constraint failed: djmdKey.usn', 'ERROR db_connection — Exception during write session — rolling back', 'Synced 0 tracks to Rekordbox', 'Multi-drive import complete: 1 total, 1 new … 0 errors', then step_end exit_code 0. Identical in round 2. The djmdKey.usn failure may be specific to the synthetic fixture DB. The pipeline-relevant point is that a rolled-back DB write still yields exit 0 and a green step.
- **Fix:** Count sync failures as errors in importer_database and exit non-zero when any DB write session rolls back; surface 'synced N of M' in the step report.

#### [LOW · demonstrated] Pipeline step completion is journalled under display names (never shown on tool cards), and dry runs and failures are recorded as runs

- **ID:** `tool-pipeline-step-state-mislabelled` · **Area:** session-resume · **Tool/surface:** Pipeline Wizard
- **Expected:** Step state is keyed by tool type, distinguishes dry run, live, failed and interrupted, and is shown in the UI.
- **Actual:** The state is written but never surfaced, and when it is it is misleading.
- **Evidence:** After r3_t3, .fablegear_state.json steps_completed = {'audit': {...}, 'Rename Files': {...exit_code 0}}. After the API normalize dry run it gained 'Normalize'. helpers.py:1180 calls mark_step_complete(_pipe_root, step.get('type', name), exit_code), but the built steps carry no 'type' (routes_tools.py:502, 586), so the key is the display name. state_tracker.js:11 STATE_STEP_MAP expects 'process', 'organize' and so on, so pipeline runs never mark the tool cards. Dry runs and failed steps are journalled the same way, and an interrupted step is not journalled at all.
- **Fix:** Add 'type': stype to each built step; record dry_run and interrupted states; let applyStateToUI show 'previewed' vs 'applied'.

#### [LOW · demonstrated] Files rewritten by normalisation lose group/other read permission (0644 becomes 0600)

- **ID:** `tool-pipeline-normalized-file-perms` · **Area:** chop-shop-tool · **Tool/surface:** Pipeline Wizard (process step)
- **Expected:** Rewritten tracks keep their original mode.
- **Actual:** Normalised tracks become owner-only. This can break access on shared or NAS volumes or for other user accounts.
- **Evidence:** `ls -la Music Library/Techno` after the r3_t5 cancel run: '-rw------- Vera Lux - Pressure.aiff', '-rw------- untitled track 03.mp3' (both normalised), while the un-normalised 'Vera Lux - Pressure.flac' is '-rw-r--r--'. Cause: mkstemp creates the temp file with mode 0600 (audio_processor.py:509), and that temp then replaces the original.
- **Fix:** Copy the original's mode with shutil.copymode(bak, path) or os.chmod after the move.

#### [INFO · demonstrated] Failing middle step stops the run cleanly (confirmed good), but there is no resume or rollback of the earlier steps

- **ID:** `tool-pipeline-failing-step-good` · **Area:** chop-shop-tool · **Tool/surface:** Pipeline Wizard
- **Expected:** Same clean stop, plus 'fix and resume from step 2' and 'revert steps 1..k'.
- **Actual:** Clean stop only.
- **Evidence:** r3_t5 failing run of process, import(typo path), rename: EV step_end 1 exit 0; step_end 2 exit 1 'PATH is not a directory'; done {exit_code 1, failed_step 'Import Tracks'}; step 3 not started; step classes ['pipe-step done','pipe-step failed','pipe-step']; isRunning False; log '✗ Pipeline stopped — "Import Tracks" had an error.' (r3t5_fail_01_fail_done.png). There is no checkpoint ('FAIL ckpt: None'), no banner on reopen, and step 1's tag writes stay applied with no rollback offer.
- **Fix:** Covered by the run-record and run-revert fixes above.

#### [INFO · demonstrated] 'normalize' step type is supported by the backend and config UI but cannot be added in the wizard

- **ID:** `tool-pipeline-normalize-step-unreachable` · **Area:** chop-shop-tool · **Tool/surface:** Pipeline Wizard
- **Expected:** Either expose it or remove the dead path.
- **Actual:** Dead or hidden step type. Normalisation is reachable only through Tag Tracks' normalize_mode.
- **Evidence:** The wizard p1 has no pipelineAddStep('normalize') button (template lists audit, convert, duplicates, import, link, novelty, organize, process, prune, relocate, rename; probe 'normalize button present: 0'). routes_tools.py:455-466 and pipeline.js _pipeWizConfigHTML still handle it. An API dry run of a normalize step wrote no audio (r3_s50 to s51: only a report, fg_processing_log and state changed), which is good.
- **Fix:** Remove the normalize branch or add a button. Either way, keep its dry run non-writing.

## Lead auditor's own click-through (sandbox port 5090)

# Lead's own first-hand findings (sandbox port 5090, Chromium 1440x900)

L1 [demonstrated, medium] USB Export modal ignores Escape. Opened via "Export to USB" in Record Room; Escape pressed;
   modal + #le-export-backdrop remain and occlude 35+ controls (quit, room switcher, settings, undo, drives).
   Only × / Cancel close it. Every other surface handles Escape (boot.js, db_rail.js, drives.js, info.js, ui_extras.js);
   usb_export.js:184 handles Escape only for the playlist-create input.
   Screens: screens/lead/1440_01c_export_usb.png, 1440_01d_after_escapes.png

L2 [demonstrated, medium] ⌘1/⌘2 (Ctrl on Linux) room hotkeys (static/shared/launcher.js:14-27) switch rooms underneath an open
   modal. With USB Export open in Record Room, Ctrl+2 swaps the workspace to Chop Shop while the Record Room modal
   stays on top (its accent flips to Chop Shop magenta). The user is now in a room whose UI is blocked by a dialog
   from the other room. Screen: screens/lead/hotkey_usb_export.png. Also the staging panel persists across the switch
   (likely intentional — it is the cross-room hand-off), overlapping the Chop Shop tool strip: screens/lead/hotkey_staging_panel.png.
   Fix: hotkey handler should no-op (or close the modal first) when any modal/backdrop is open.

L3 [demonstrated, low] USB Export empty-state is wrong: with 11 tracks loaded and 0 playlists it says
   "Load your library first, then reopen Export." (usb_export.js:41). The condition is "no playlists in tree", not "library not loaded".

L4 [demonstrated, low] Undo Wizard tab row is clipped at 1440 wide: tabs Timeline / Operations / Database / "SA…" — the 4th+ tabs run
   off the panel edge with no scroll affordance. Screen: screens/lead/hotkey_undo_wizard.png.

L5 [inferred, info/boundary] Record Room source-location grouping code (_leSourceLocationForPath, leRenderSourceLocations)
   lives in static/chop_shop/tool_modal.js:362-420 — Record Room behavior defined in a Chop Shop file.

L6 [inferred, boundary] Record Room exposes an "All Music" filesystem-browse mode (index.html:903 data-mode="fs",
   library_mode.js:90 → /api/library/fs-browse) with a "Filesystem" breadcrumb sidebar — Finder-style navigation inside the
   database-first room. (Confirm intent vs Record Room auditor profile.)

L7 [demonstrated, low] Every track without artwork 404s on /api/library/tracks/<id>/art (console errors), and the miss is never cached: each art URL was requested 13x across ~7 page loads (server.log). Use 204/placeholder + client-side negative cache.

L8 [demonstrated, info] Startup with no config runs 4 health checks that each log "has not been configured yet" errors
   (server.log) — noise on every first launch.

## Unmerged branch origin/feature/install-wizard (fabe8a4, d2e3ba2 — 2026-08-19/20, session_01KJcM91KC7ddmA5WwHPfCzc). NOT on main.
Standalone pywebview GUI replacing setup.sh's Terminal bootstrap (installer_wizard.py 460 lines + templates/installer_wizard.html 1007 lines, 31 tests).
W1 [inferred, high-if-merged] No cancel and no clean exit while installing: stream_command() (installer_wizard.py:260-282) Popen()s brew/pip and never
   terminates the child when the SSE client disconnects or the window closes. Closing the window mid-pip-install
   ends the wizard process but leaves brew/pip running orphaned, writing into venv/ — next launch can see a half-built venv.
W2 [inferred, medium] /api/complete (installer_wizard.py:379-385) touches .fablegear_ready unconditionally — the server never checks that the
   brew and python sequences actually ended in [SEQUENCE_OK]. Partially mitigated because launch.sh _setup_needed() also re-checks brew formulas + venv/bin/activate,
   but not pip package completeness.
W3 [inferred, medium] Exit routes: there is a Retry (pendingRetry, html:865-978) but no Back, no Cancel, no "Finish later"; the only exit is closing the
   window (see W1). No checkpoint file — re-running restarts at the checks step (acceptable because brew/pip are idempotent, but nothing tells the user that).

L9 [demonstrated, high] Undo Wizard "Timeline" is structurally empty for every tool run from the UI.
   routes_undo.py:40-48 reads job_dispatcher.get_history(); get_history() returns [] when _db_path is None (job_dispatcher.py:566-569, 729-731).
   _db_path is only set by job_dispatcher.init()/reconfigure(), which are called ONLY from mcp_server.py:109/337 — never from app.py.
   UI tools run through helpers._sse_response (/api/run/*), which never calls job_dispatcher.dispatch (only mcp_server.py does, :381-1025).
   Live: on sandbox 5090, GET /api/run/audit → `data: {"done": true, "exit_code": 0}`; then GET /api/undo/timeline → `[]`;
   /api/undo/operations → {"sessions":[]}. Screenshot of the wizard saying "No jobs found": screens/lead/hotkey_undo_wizard.png.
   Consequence: the one surface that should answer "what did I do last session and where can I revert to" is blank in the desktop app.

L10 [inferred from code, high] Two update channels with different targets. launch.sh:106-160 is release-gated (ff-only / realign to
   GitHub "latest release" tag; never downgrades a descendant). The in-app Update button, /api/update/apply (app.py:733+), whose docstring says
   "Pull the latest release", actually runs `git pull origin main --ff-only` — i.e. untagged main HEAD — then relaunches launch.sh, which sees
   HEAD as a descendant of the tag and "stays on current build". So one click moves a release user onto whatever is on main, including
   commits pushed straight to main without a PR (8 such commits since Aug 1, e.g. ac9e260 corrupted CSS, abbb8ff pyright-red), and they then stop
   tracking releases until a newer tag passes them. Latest release is v1.1.30 = 01d39ca (Aug 14); main is 1840750 (Sep 2).
   Fix: have /api/update/apply fetch tags and ff-merge to the latest release tag (same logic as launch.sh), or call launch.sh's path.

