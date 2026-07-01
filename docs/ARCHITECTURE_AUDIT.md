# FableGear Architecture Audit (Evidence-Backed)

Date: 2026-07-01  
Branch: `audit/architecture-consolidation-2026-07-01`

Scope audited:
- Player/Deck
- Onboarding
- Archive/Reports DB wiring
- Duplicate detection
- Library source model

Method:
- Traced actual control/data flow only (imports, command handlers, Flask routes, `<script>` includes).
- Every finding below includes file:line evidence.
- No deletions or behavior changes were made in this pass.

---

## 1) Player/Deck Subsystem

### Authoritative implementation (runtime-effective)
- Deck UI markup is the modern Record Room deck panel in `templates/partials/record_room/deck.html` (IDs `deck-*`):
  - `templates/partials/record_room/deck.html:4`
  - `templates/partials/record_room/deck.html:38`
  - `templates/partials/record_room/deck.html:92`
- Current runtime deck behavior is in `static/record_room/deck.js`, which binds to `deck-*` IDs and exports deck globals:
  - `static/record_room/deck.js:394`
  - `static/record_room/deck.js:474`
  - `static/record_room/deck.js:183`

### Parallel/orphaned implementations coexisting
- `templates/index.html` loads both:
  - old module path: `static/record_room/performance_ui.js` (`type="module"`)
    - `templates/index.html:779`
  - current deck controller: `static/record_room/deck.js`
    - `templates/index.html:791`
- `performance_ui.js` imports `DeckManager` from `deck_control.js`:
  - `static/record_room/performance_ui.js:1`
  - `static/record_room/deck_control.js:189`

### Proof that old layer is loaded-but-broken
- `performance_ui.js` binds to `li-*` controls under `#li-panel`:
  - `static/record_room/performance_ui.js:56`
  - `static/record_room/performance_ui.js:63`
  - `static/record_room/performance_ui.js:104`
- `bindOnce()` aborts if `#li-panel` is absent:
  - `static/record_room/performance_ui.js:56`
- No `li-panel` markup exists in templates (search hit count = 0).
- Current deck markup contains only `deck-*` IDs, not `li-*`:
  - `templates/partials/record_room/deck.html:4`
  - `templates/partials/record_room/deck.html:38`
  - `templates/partials/record_room/deck.html:121`

### Dangling references
- `templates/partials/record_room/deck.html` still claims it is “Driven by static/record_room/deck.js” while `performance_ui.js` is also loaded globally:
  - `templates/partials/record_room/deck.html:3`
  - `templates/index.html:779`
  - `templates/index.html:791`

Conclusion:
- Effective deck runtime is `deck.js` + `deck.html`.
- `performance_ui.js` + `deck_control.js` is a stale parallel generation still loaded, currently inert because required DOM no longer exists.

---

## 2) Onboarding Subsystem

### Three first-run flows verified
1. Shell setup flow (`setup.sh`) called by launcher when sentinel missing:
   - `launch.sh:19`
   - `launch.sh:44`
   - `setup.sh:1`
2. CLI setup wizard (`python3 cli.py setup`):
   - `cli.py:155`
   - `cli.py:157`
3. Web onboarding (`/onboarding`, `/api/onboarding/*`):
   - `app.py:1188`
   - `app.py:1205`
   - `app.py:1373`

### Canonical gate currently intended
- `/` should redirect to `/onboarding` when config/state incomplete:
  - `app.py:345`
  - `app.py:347`
  - `app.py:350`

### Why wizard can silently never appear
- Index gate wraps onboarding checks in broad `try/except: pass`, then returns `index.html`:
  - `app.py:346`
  - `app.py:352`
  - `app.py:353`
- Onboarding route itself also has broad `try/except: pass`, reducing failure visibility:
  - `app.py:1193`
  - `app.py:1201`

### Conflict vectors between flows
- `setup.sh` controls dependency/bootstrap + `.fablegear_ready`, not the full app config contract:
  - `setup.sh:7`
  - `setup.sh:217`
- CLI `setup` writes config via `interactive_setup`, separate from onboarding state file semantics:
  - `cli.py:157`
  - `user_config.py:145`
  - `user_config.py:197`
- Web onboarding writes both config and setup state (`setup_complete: true`):
  - `app.py:1373`
  - `app.py:1416`

Conclusion:
- Intended canonical UX is web onboarding, but broad exception suppression in route gates allows fallback to `/` without surfaced failure, creating “wizard disappears” symptoms.

---

## 3) Archive/Reports DB Wiring (Persistence Contract)

## Chop Shop tools with optional archive side-effect
All seven audited tools expose optional archive and guard writes behind `if archive is not None`:
- `chop_shop/dead_file_scanner.py:122`, `chop_shop/dead_file_scanner.py:171`
- `chop_shop/duplicate_detector.py:872`, `chop_shop/duplicate_detector.py:1213`
- `chop_shop/library_organizer.py:302`, `chop_shop/library_organizer.py:461`
- `chop_shop/novelty_scanner.py:319`, `chop_shop/novelty_scanner.py:489`
- `chop_shop/pruner.py:381`, `chop_shop/pruner.py:553`
- `chop_shop/relocator.py:301`, `chop_shop/relocator.py:423`
- `chop_shop/renamer.py:1088`, `chop_shop/renamer.py:1208`

### Where archive is injected today
- CLI lazily creates archive DB:
  - `cli.py:60`
  - `cli.py:63`
  - `cli.py:69`
- CLI passes archive to tool calls in live paths:
  - Dead files: `cli.py:237`
  - Relocate: `cli.py:397`
  - Duplicates: `cli.py:482`
  - Prune: `cli.py:765`, `cli.py:981`
  - Organize: `cli.py:1569`
  - Novelty: `cli.py:1697`
  - Rename (non-dry-run): `cli.py:1816`

### Tool × caller archive connection matrix
Legend:
- Direct = caller passes `archive=` itself
- Indirect = caller shells/dispatched into CLI, CLI passes `archive=`
- Dropped = path exists that executes without archive persistence
- N/A = caller does not invoke that tool

| Tool | CLI caller | routes_tools.py | routes_rekordbox.py | mcp_server.py | pipeline_wizard |
|---|---|---|---|---|---|
| dead_file_scanner | Direct (`cli.py:237`) | Indirect via `cli.py dead-files` (`routes_rekordbox.py:223`) | Indirect (`routes_rekordbox.py:223`) | N/A | N/A |
| duplicate_detector (duplicates) | Direct (`cli.py:482`) | Indirect via `cli.py duplicates` (`routes_tools.py:388`) | Indirect via `cli.py rekordbox-dedupe` (`routes_rekordbox.py:243`) | Indirect via dispatch to `cli.py duplicates` (`mcp_server.py:441`) | Indirect (registry command strings; executor subprocess) (`pipeline_wizard/tool_registry.py:153`, `pipeline_wizard/executor.py:95`) |
| pruner | Direct (`cli.py:765`, `cli.py:981`) | Indirect via pipeline `prune` (`routes_tools.py:406`) | Indirect via `rekordbox-dedupe` flow (`cli.py:999`) | Dropped (no dedicated MCP prune tool) | Potentially dropped (registry includes no `prune`) |
| relocator | Direct (`cli.py:397`) | Indirect via `cli.py relocate` (`routes_tools.py:423`) | Indirect (`routes_rekordbox.py:139`) | Indirect (`mcp_server.py:684`) | Indirect (`pipeline_wizard/tool_registry.py:103`) |
| library_organizer | Direct (`cli.py:1569`) | Indirect (`routes_tools.py:523`) | N/A | Indirect (`mcp_server.py:743`) | Indirect (`pipeline_wizard/tool_registry.py:191`) |
| novelty_scanner | Direct (`cli.py:1697`) | Indirect (`routes_tools.py:584`) | N/A | Indirect (`mcp_server.py:495`) | Indirect (`pipeline_wizard/tool_registry.py:243`) |
| renamer | Direct in live mode only (`cli.py:1816`) | Indirect (`routes_tools.py:624`) | N/A | Indirect (`mcp_server.py:795`) | Indirect (`pipeline_wizard/tool_registry.py:174`) |

### Drop points / weak guarantees
- Optional contract: each tool can run with `archive=None` silently (no fail-loud).
- `rename` archive logging only occurs in non-dry-run path (intentional), so dry-run emits no persistence rows:
  - `cli.py:1793`
  - `cli.py:1816`
- Pipeline Wizard executor command composition is inconsistent with CLI argument forms (`--path` generic append), creating potential execution mismatches independent of archive:
  - `pipeline_wizard/executor.py:116`

Conclusion:
- CLI path is mostly wired.
- Contract is still optional and non-failing, so persistence can be dropped without hard error.

---

## 4) Duplicate Detection Subsystem

### Coexisting implementations
- Legacy/live implementation used by runtime commands: `chop_shop/duplicate_detector.py`:
  - `cli.py:432`
  - `cli.py:816`
- New database-first implementation file exists but not called by CLI routes:
  - `chop_shop/duplicate_detector_database.py:48`
  - `chop_shop/duplicate_detector_database.py:253`

### Proof of non-authoritative db-first module
- No CLI command imports `duplicate_detector_database` directly.
- Runtime dedupe commands (`duplicates`, `rekordbox-dedupe`) both call legacy `scan_duplicates`:
  - `cli.py:432`
  - `cli.py:883`
- db-first status is documented in implementation markdown but not wired to command surface:
  - `IMPLEMENTATION_COMPLETE.md:11`

Conclusion:
- Authoritative runtime duplicate engine remains `duplicate_detector.py`.
- `duplicate_detector_database.py` is present as parallel/unintegrated generation.

---

## 5) Library Source Subsystem (Record Room data model)

### Authoritative model
- Record Room defaults to FableGear SQLite (`FableGearDatabase`) as primary source:
  - `routes_player.py:365`
  - `routes_player.py:413`
  - `routes_player.py:419`
- Rekordbox DB sources (`local`, `device`) remain as explicit demoted alternates:
  - `routes_player.py:400`
  - `routes_player.py:403`

### UI evidence
- Source toggles explicitly expose `fablegear`, `local`, `device`:
  - `templates/index.html:848`
  - `templates/index.html:850`
  - `templates/index.html:852`
- JS source switcher enforces only these DB source modes:
  - `static/record_room/library_mode.js:117`

Conclusion:
- Architecture intent (Record Room as DB-depth interface) is already present in code, but consistency is undermined elsewhere by optional persistence guarantees and parallel stale UI layers.

---

## Systemic Root Cause Summary

1. Parallel generations are left loaded in runtime (deck stack, duplicate stack), so stale code survives and silently competes with canonical paths.
2. Cross-tool persistence is an optional side-effect (`archive=None`) instead of a required contract.
3. Onboarding gate failures are swallowed (`try/except: pass`), allowing silent bypass of first-run enforcement.
4. Caller surfaces are fragmented (CLI, Flask routes, MCP, Pipeline Wizard) and rely on indirect assumptions rather than explicit interface contracts.

This combination explains feature disappearance/regression without obvious hard failures.
