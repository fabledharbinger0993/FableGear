# Integration Contracts (Living Manifest)

Purpose:
- Declare canonical module per subsystem.
- Declare public interface and consumers.
- Make drift/orphans detectable during review.

Last reviewed: 2026-07-01

---

## Record Room Library (Database-Depth Interactive Layer)

Canonical module:
- `routes_player.py` (Record Room API surface)
- `fablegear_database/*` (persistent source of truth)

Public API:
- `GET /api/library/tracks` (primary source defaults to FableGear DB)
- `POST /api/library/db/sync`
- `GET /api/library/db/sync-status`

Consumers:
- `static/record_room/library_mode.js`
- `templates/index.html`

Contract:
- Record Room reads/writes database records.
- Record Room must not mutate filesystem directly.

---

## Deck/Player UI

Canonical module:
- `static/record_room/deck.js`
- `templates/partials/record_room/deck.html`

Public API (window globals used by UI):
- `window.deckLoadTrack`
- `window.deckPlay`
- `window.deckPause`
- `window.deckTogglePanel`

Consumers:
- `templates/index.html` (script include)
- Record Room interaction handlers in `static/record_room/library_mode.js`

Contract:
- Exactly one active deck runtime must be loaded in `templates/index.html`.
- Any legacy deck runtime must be disabled or removed.

---

## Chop Shop Tool Execution (Filesystem-Depth Mutations)

Canonical module:
- CLI command handlers in `cli.py`

Public API:
- `python3 cli.py dead-files`
- `python3 cli.py relocate`
- `python3 cli.py duplicates`
- `python3 cli.py prune`
- `python3 cli.py organize`
- `python3 cli.py novelty`
- `python3 cli.py rename`

Consumers:
- `routes_tools.py` (SSE/UI calls CLI)
- `routes_rekordbox.py` (DB-maintenance calls CLI)
- `mcp_server.py` (tool dispatch to CLI)
- `pipeline_wizard/*` (registry/executor command composition)

Contract:
- CLI is the authoritative tool-calling surface.
- Alternate callers must route through CLI (or enforce equivalent archive contract).

---

## Archive Persistence Bridge

Canonical module:
- `fablegear_database/database.py` (`FableGearDatabase`)

Public API used by tools:
- `log_operation(...)`
- `get_content_by_path(...)`
- `relink_content(...)`
- `delete_content(...)`
- `count_operations(...)`

Consumers:
- Chop Shop modules in `chop_shop/*.py`
- CLI archive injector in `cli.py` (`_archive()`)
- Record Room read model in `routes_player.py`

Contract:
- Live tool operations must append to `fg_processing_log`.
- Mutations that change path/content identity must update `fg_content` linkage.
- Archive write failure is contract failure (must not be silent in live mode).

---

## Duplicate Detection

Canonical module (current runtime):
- `chop_shop/duplicate_detector.py`

Secondary module (non-canonical until wired):
- `chop_shop/duplicate_detector_database.py`

Public API:
- `scan_duplicates(...)`
- report writers consumed by prune flows

Consumers:
- `cli.py` (`duplicates`, `rekordbox-dedupe`)
- `routes_tools.py` and `routes_rekordbox.py` indirectly via CLI

Contract:
- Only one duplicate engine path should be authoritative at command level.
- Any secondary implementation must be adapter/internal only, not parallel runtime surface.

---

## Onboarding / First-Run

Canonical module (target):
- `app.py` onboarding routes and setup-state checks

Supporting modules:
- `setup.sh` (dependency bootstrap)
- `launch.sh` (sentinel/bootstrap handoff)
- `cli.py setup` / `user_config.py` (reconfigure path)

Public API:
- `GET /onboarding`
- `POST /api/onboarding/save-config`
- setup status endpoints in `app.py`

Consumers:
- `templates/onboarding.html`
- launcher/front-door route `/`

Contract:
- One canonical setup state machine must gate entry to `/`.
- Setup-check failures must be fail-loud (no broad swallow-and-continue).

---

## Change Control Checklist

For any subsystem change, reviewers must verify:
- Canonical module unchanged or manifest updated.
- New consumer declared.
- No parallel runtime implementation introduced.
- Archive contract maintained for live tool operations.
- Regression tests updated (`tests/test_archive_wiring.py` and related).
