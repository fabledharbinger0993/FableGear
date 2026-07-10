# Stage 4 — One Canonical Onboarding Wizard (implementation mandate)

## North star

First launch of FableGear must walk the user through ONE wizard that ends with:
a configured library, explicit permissions granted (nothing assumed), an
optional AI/MCP integration, and a FableGear database seeded from the sources
the user chose. The user stays in control at every step — every capability is
opt-in, every step explains what it unlocks, and nothing is scanned, read,
written, or imported until the user says so.

## Ground truth (verified in code — do not re-derive)

Three first-run surfaces exist today, and they are NOT equal:

1. **`setup.sh`** (opened by `launch.sh` in Terminal) — dependency bootstrap
   ONLY: Homebrew, formulas, Python venv. It asks no configuration questions.
   It is fine as-is. Leave it alone.
2. **`cli.py setup`** — terminal config wizard. Keep for headless use; it
   writes the same `~/.fablegear/config.json` via `user_config`. Not touched
   in this stage.
3. **Web `/onboarding`** ([templates/onboarding.html](../templates/onboarding.html)) —
   THE canonical flow. Current steps:
   - `step-0` dependency check (`/api/onboarding/dep-check`)
   - `step-1` install app to /Applications + dock
   - `step-2` library scan (drive-scan consent gate → discovered Rekordbox
     DBs, DJ drive DB, XML, home library folder, archive home)
   - `step-3` read-access consent (what it unlocks)
   - `step-4` write-access consent (backup guarantee)
   - `step-5` done → `/api/onboarding/save-config` writes config.json +
     setup state (`setup_complete`, `db_read`, `db_write`, scan consent)

   The `/` gate now fails loud (fixed in Stage 1) — unreadable state routes
   to the wizard.

Existing primitives to REUSE (verify signatures before calling — do not invent):
- MCP lifecycle: `/api/mcp/status`, `/api/mcp/enable`, `/api/mcp/disable`,
  `/api/mcp/start`, `/api/mcp/stop`, `/api/mcp/config-snippet?client=…`;
  config keys `mcp_enabled` / `mcp_port` in `user_config` (port autodetect
  via `find_available_mcp_port`).
- Source discovery: `user_config.discover_music_roots(mounts: list[Path])`
  → list of dicts (path, label, volume, audio_count, recommended_* fields).
  NOTE its real signature — Stage 3 already fixed one caller that guessed.
- FableGear DB seeding: `FileImporter.import_files(root_paths)` (bulk, logs
  to fg_processing_log) and `/api/library/db/sync` + `/api/library/db/sync-status`
  (background reconcile of MUSIC_ROOT with progress polling).
- Setup state: `_load_setup_state(repair=True)` / save path inside
  `api_onboarding_save_config` — extend the state dict, don't fork it.

## What Stage 4 delivers

### A. New step: "AI integration (MCP)" — after write consent, before finish
- Explains in one paragraph what the MCP server is (Claude/AI copilots can
  drive FableGear tools over localhost) and that it is OFF by default.
- One primary choice: **Enable AI integration** / **Not now** (skippable,
  default = off; no dark patterns).
- If enabled: call `/api/mcp/enable` (persists `mcp_enabled` + picks a free
  port), then show the copyable client config snippet from
  `/api/mcp/config-snippet` with a client picker (claude-code / claude-desktop).
- Record the decision in setup state (`mcp_opted_in: bool`) so the summary
  step and Settings page can show it.

### B. New step: "Build your FableGear library" — after the MCP step
- Runs `discover_music_roots` over connected volumes (+ the chosen home
  library folder from step-2) and lists each candidate source with its
  audio count and volume name, checkbox per source (the recommended home
  gets pre-checked; external drives default UNCHECKED — user control).
- Explains the model in one line: Rekordbox stays untouched; FableGear
  builds its own database alongside it.
- **Import selected** kicks off seeding via a new
  `POST /api/onboarding/import-sources {"paths": [...]}` that runs
  `FileImporter.import_files` on a background thread with the same
  progress-dict pattern as `_FG_SYNC` (poll endpoint returns
  running/phase/done/total/result). Show live progress in the step;
  **Skip for now** is always available and prominent.
- On completion show counts (new / updated / skipped) — honest numbers,
  from the importer's stats dict.

### C. Wiring & consistency
- Step order becomes: deps → install → library scan → read → write →
  **AI integration** → **build library** → done.
- `save-config` must run BEFORE the import step needs config (music_root
  et al.) — either move the config save to fire when leaving the library-scan
  review, or have import-sources accept explicit paths (chosen: explicit
  paths — no hidden dependency on save ordering; the endpoint validates
  each path is a directory).
- The finish step summarizes every decision: paths, read/write, scan
  consent, MCP on/off + port, sources imported (count) — the user sees
  exactly what was set up.

## Constraints
- Match the existing onboarding aesthetic (ob-step / ob-card / ob-btn
  classes, neon theme) — no new design system.
- Never auto-start a scan or import; consent gates stay.
- Do not break `cli.py setup` or existing save-config contract; only add
  keys, never rename existing ones.
- Every new backend action logs to the archive when it writes
  (import-sources already does via FileImporter).
- Preview-verify the full wizard flow in the browser before calling it done
  (steps advance, MCP snippet renders, import progress polls). Tests for the
  new endpoint (paths validation + background thread stats).

## Definition of done
A first-run user can: see deps install → put the app in /Applications →
consent to a drive scan → pick their library → grant read/write with full
explanation → opt in (or not) to AI integration and get a working MCP
config snippet → select discovered music sources and watch them import
into FableGear.db → land in the app with a summary of every choice. All
skippable; all decisions persisted; nothing silent.
