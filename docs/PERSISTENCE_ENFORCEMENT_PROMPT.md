# High-Value Prompt: Enforce Tool Interconnectedness and Persistence

Use this prompt with any implementation agent working on FableGear.

---

You are implementing persistence-enforced architecture in FableGear.

Mission:
- Eliminate silent, optional cross-tool wiring.
- Enforce persistent Archive logging and state relinking for every live Chop Shop tool action.
- Preserve architectural split:
  - Record Room = database-depth interactive layer.
  - Chop Shop = filesystem-depth mutation layer.

Non-negotiable contract:
1. Live tool execution must append to fg_processing_log.
2. Any path/content mutation must update fg_content linkage.
3. Missing archive service is a hard failure in live mode (no silent fallback).
4. Dry-run behavior may avoid mutation writes but must still preserve explicit status semantics.

Implementation scope:
- Callers:
  - cli.py command handlers for dead-files, relocate, duplicates, prune, organize, novelty, rename.
  - Indirect callers via routes_tools.py, routes_rekordbox.py, mcp_server.py, pipeline wizard.
- Tool modules:
  - chop_shop/dead_file_scanner.py
  - chop_shop/duplicate_detector.py
  - chop_shop/library_organizer.py
  - chop_shop/novelty_scanner.py
  - chop_shop/pruner.py
  - chop_shop/relocator.py
  - chop_shop/renamer.py
- Archive implementation:
  - fablegear_database/database.py

Required changes:
1. Convert archive from optional side-effect to enforced contract for live runs.
2. Introduce one canonical Archive service adapter and use it everywhere.
3. Add fail-loud checks in CLI entrypoints before invoking live tool logic.
4. Keep non-destructive defaults and backup/undo behavior unchanged.
5. Remove or disable stale parallel runtime paths only after zero-reference proof.

Test requirements:
- Keep tests/test_archive_wiring.py green.
- Add/maintain tests that fail when caller->tool archive linkage is dropped.
- Add at least one end-to-end live-mode test that asserts:
  - a processing log row is appended,
  - fg_content is relinked/updated when paths change,
  - command exits non-zero if archive unavailable in live mode.

Evidence/reporting requirements:
- For every code change, cite file:line evidence of prior behavior and new behavior.
- Include a short migration note for any behavior made strict (especially archive availability).
- Do not perform destructive deletes in this pass; quarantine first.

Definition of done:
- No live Chop Shop operation can succeed without persisted archive evidence.
- Record Room can trust fg_content + fg_processing_log as authoritative history.
- Contract is enforced by tests, not convention.
