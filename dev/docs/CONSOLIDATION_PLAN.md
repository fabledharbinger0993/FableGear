# Consolidation Plan (Staged, Reversible)

Goal:
- Eliminate parallel/orphaned implementations.
- Enforce archive persistence as a contract, not optional side-effect.
- Collapse onboarding to one canonical flow with fail-loud behavior.

Constraints:
- No destructive deletions in phase 1.
- Every step must be reversible.
- Keep Record Room at database depth, Chop Shop at filesystem depth.

---

## Phase 0: Safety Baseline (Low Risk)

1. Add regression tests before behavior changes.
- Add caller wiring tests for all Chop Shop tool entrypoints (CLI handlers).
- Add tests that enforce archive log append for live operations.

Could break:
- Existing tests if caller wiring is currently inconsistent.

Rollback:
- Revert test file only.

---

## Phase 1: Runtime De-duplication via Feature Gating (Low Risk)

1. Deck stack: stop loading stale module path first (do not delete file yet).
- Candidate: remove `performance_ui.js` include from `templates/index.html` once parity verified.
- Keep `deck.js` as active runtime.
- Keep `deck_control.js` quarantined (not loaded) for one release cycle.

Evidence:
- dual-load: `templates/index.html:779`, `templates/index.html:791`
- stale DOM contract in old module: `static/record_room/performance_ui.js:56`
- active DOM in new panel: `templates/partials/record_room/deck.html:4`

Could break:
- Any hidden feature still using `window.FablePerformance` from `deck_control.js`.

Rollback:
- Re-enable old script include.

2. Duplicate detection: route commands through single authoritative implementation.
- Pick one canonical module and make the other an adapter only.
- Preferred: keep CLI command surface stable; implement db-first internals behind canonical command.

Evidence:
- runtime command uses legacy module: `cli.py:432`, `cli.py:883`
- db-first module exists but unwired: `chop_shop/duplicate_detector_database.py:48`

Could break:
- CSV format and pruning assumptions if group schema changes.

Rollback:
- Flip command import back to previous module.

---

## Phase 2: Archive Contract Enforcement (Medium Risk)

## Decision
Replace optional `archive=None` with enforced archive injection for live mutations and live scans.

Contract target:
- Live tool invocation must have archive object.
- Missing archive is a hard failure (explicit exception) in live mode.
- Dry-run may skip archive row writes only where no mutation occurs.

Implementation steps:

1. Introduce shared Archive service interface (single adapter).
- Centralize in one service module, e.g. `services/archive_service.py`.
- Wrap current `FableGearDatabase` methods used by tools:
  - `log_operation`
  - `get_content_by_path`
  - `relink_content`
  - `delete_content`

2. Update CLI caller contract first.
- Resolve archive once at startup; fail-loud when unavailable for live commands.
- Preserve read-only dry-run behavior where applicable.

3. Update tool signatures.
- Change from `archive=None` to `archive` for live execution paths.
- Raise explicit error if caller violates contract.

4. Add operation-level assertions.
- For each live tool action, verify at least one `fg_processing_log` append occurred.
- On zero appends, return non-zero exit / explicit error.

5. Add route-level policy.
- `routes_tools.py`, `routes_rekordbox.py`, and MCP dispatch paths should fail if underlying CLI command reports archive contract violation.

Could break:
- Environments where archive DB cannot initialize (permissions/path issues).
- Existing automation that relied on silent success without persistence.

Rollback:
- Temporary compatibility flag (`FABLEGEAR_ALLOW_ARCHIVE_OPTIONAL=1`) for one release.

---

## Phase 3: Onboarding Canonicalization (Medium Risk)

## Decision
Web onboarding (`/onboarding`) becomes the single canonical first-run state machine.

Implementation steps:

1. Keep `setup.sh` for dependency/bootstrap only.
- No semantic setup completion in shell layer besides readiness sentinel.

2. Keep CLI `setup` as maintenance/recovery path only.
- Mark as “reconfigure” helper and align with onboarding state file updates.

3. Remove silent bypass.
- Replace broad `except: pass` in `/` and `/onboarding` gates with:
  - explicit error logging
  - deterministic fallback to `/onboarding`

Evidence:
- silent bypass risk: `app.py:346-353`

4. Unify state checks in one function.
- Single `is_setup_complete()` used by `/`, onboarding routes, and launcher status API.

Could break:
- Existing users with malformed state files might be hard-routed to onboarding until fixed.

Rollback:
- Temporary compatibility fallback with warning banner.

---

## Phase 4: Orphan Quarantine then Removal (Higher Risk)

1. Quarantine period (one release).
- Move stale modules into `static/_quarantine/` or disable by import map.
- Emit startup warning if stale files are still referenced.

2. Hard removal after zero-reference verification.
- Remove only when:
  - no template include references
  - no JS imports
  - no command imports
  - no tests depend on module

Candidates:
- `static/record_room/performance_ui.js`
- `static/record_room/deck_control.js` (if no remaining consumer)
- duplicate engine secondary module once canonical adapter in place

Could break:
- Untracked external scripts/tests depending on old globals.

Rollback:
- Revert removal commit or restore quarantined files.

---

## Phase 5: Contract Visibility (Low Risk)

1. Add and maintain `docs/INTEGRATION_CONTRACTS.md` as release gate checklist.
2. CI check:
- fail build if:
  - stale module is loaded in templates
  - live CLI tool path has no archive wiring test
  - onboarding gate contains broad exception swallow in setup path

Could break:
- CI initially until legacy references are cleaned.

Rollback:
- Start in warning mode for one release.

---

## Recommended Execution Order (Risk-Ordered)

1. Add archive wiring regression tests.
2. Disable stale deck module include.
3. Enforce archive contract in CLI and tool interfaces.
4. Collapse onboarding gate behavior.
5. Canonicalize duplicate engine internals.
6. Quarantine/remove orphaned modules.
7. Turn CI contract checks to required.

This order maximizes safety and makes regressions visible before structural deletions.
