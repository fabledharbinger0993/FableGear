# FableGear Health Audit Report
**Date:** 2026-07-31
**Auditor:** Claude (Sonnet 5), run directly against source — not routed through a separate Opus paste-in step
**Repo audited:** `~/FableGear` (live checkout, `main` @ `5dc5317`) — *not* `~/Downloads/FableGear-main`, which has no real commit history and is a stale scaffold copy
**Overall Status:** CAUTION — no release blockers found; several fixable gaps
**Artifact Coverage:** HIGH (ruff, pyright, pip-audit, pytest+coverage all ran cleanly; 559 tests, 148 files type-checked)

## Executive Summary

FableGear is in materially better shape on the axis that matters most for this project — **data safety** — than the audit templates' hypothetical "FAIL" examples implied. Destructive operations (dedup pruning, duplicate cleanup) route through an explicit Trash-rescue mechanism with a preflight gate (`chop_shop/pruner.py`), the Rekordbox-closed invariant is checked in code before writes (not just assumed), and database writes go through consistent context-managed transactions. Nothing here should block a release.

The real risk is **silent failure paths**: ruff's default rule set (this ruff version enables bandit/bugbear-class checks by default, not just pyflakes) found **420 blind `except:` blocks and 77 bare `try/except/pass` blocks** across the codebase. The audit can't tell which of those ~500 sit on the critical write/delete paths — that's the one thing worth the author's own eyes before trusting Axis 4 fully. Second most actionable: **CI only builds and publishes a release zip on a version tag** — the 559 passing tests, ruff, and pyright never run automatically on a PR or push, so regressions can merge unnoticed until manually caught.

---

## Axis 1: Code Quality & Type Safety
**Status:** CAUTION
**Confidence:** HIGH

### Findings
1. `ruff check .` (defaults — `ruff.toml` only sets line-length/target-version and ignores E701/E702, it does not restrict the rule set) found **2,087 violations**. Top codes: `BLE001` blind-except (420), `UP006/UP045/UP035` deprecated typing syntax e.g. `Dict`/`Optional[X]` instead of `dict`/`X | None` (685 combined), `RUF100` stale `# noqa` comments (336), `I001` unsorted imports (191), `S110` try/except/pass (77), `DTZ005` naive `datetime.now()` without timezone (49), `PLW1510` `subprocess.run()` without explicit `check=` (38), `F401` unused imports (53), `F841` unused locals (13).
2. `pyright` found **424 type errors across 148 analyzed files** — meaningful gap in the type-safety net for a codebase this size.
3. `BLE001` + `S110` together (~500 sites) is the standout: broad/bare exception handling is pervasive enough that it's a house style, not isolated slips. Combined with `PLW1510` (subprocess failures not checked), this is the most plausible mechanism for a real bug (corrupted write, failed ffmpeg/fpcalc call) to fail silently instead of surfacing to the user or logs.
4. `DTZ005` (49 naive-datetime call sites) is worth a second look specifically because checkpoint/backup/Trash-rescue folder names are timestamp-based (`FableGear_Pruned_{stamp}`) — a naive-local-time vs UTC mismatch wouldn't corrupt data, but could make timestamped artifacts harder to correlate across machines/timezones.

### Root Cause
Largely incremental accretion in a single-author, fast-iteration codebase (`cli.py` alone is 4,432 lines). Not evidence of carelessness — the Trash-rescue and Rekordbox-closed guards (see Axis 4) show deliberate defensive design elsewhere — but the broad-except pattern likely started as pragmatic error suppression during development and was never swept back through.

### Impact
- Blocker: No
- Severity: Medium — doesn't cause data loss on its own, but reduces the odds that a real failure gets logged or surfaced
- Affected component: Cross-cutting (all modules)

### Recommendations
1. **High priority, low effort:** Grep for `BLE001`/`S110` sites within `chop_shop/`, `fablegear_database/`, and `routes_undo.py` specifically (the destructive/write paths) and either narrow the exception type or add a log line — don't attempt all 500 at once.
2. **Medium:** Add `check=True` (or explicit handling) to the 38 `subprocess.run()` calls flagged by `PLW1510`, prioritizing ffmpeg/fpcalc invocations in `audio_processor.py`/`health_acoustid.py`.
3. **Low:** `RUF100`'s 336 stale `# noqa` comments and the `UP0xx` typing-syntax modernization are pure cleanup — safe to batch-fix with `ruff check --fix` in a dedicated commit, no logic risk.

### Code Review Questions
- Which of the 420 `BLE001` sites are inside `fablegear_database/database.py`, `chop_shop/pruner.py`, or `routes_undo.py`? Those are the ones worth reading line-by-line.
- What test would catch this? A test that forces a write/delete operation to raise mid-pipeline and asserts the failure is logged/surfaced rather than swallowed.

---

## Axis 2: Dependency Health
**Status:** CAUTION
**Confidence:** HIGH

### Findings
1. `pip-audit` found **40 known vulnerabilities across 12 packages**. Critically, **none are in the core domain stack** (pyrekordbox, librosa, mutagen, chromaprint bindings) — the scan came back clean there.
2. The two packages FableGear pins **directly** in `requirements.txt` are both floating with only a lower bound: `mcp>=1.0` (resolved to 1.27.0, vulnerable) and `yt-dlp>=2024.1` (resolved to 2026.3.17, vulnerable, 8 findings). No upper bound means both drifted into vulnerable territory on their own.
3. Most of the 40 findings (19 of them: `starlette`, `python-multipart`, `pydantic-settings`, `pyjwt`) are **transitive dependencies pulled in by `mcp`**, not anything FableGear's own code touches directly. Real-world exposure depends on `mcp_server.py`'s transport: it defaults to **stdio** (Claude Desktop/Cursor, not network-reachable), but `--transport sse` is a documented, supported mode — so this isn't purely academic if anyone runs it that way.
4. `cryptography` 47.0.0 has a bundled-OpenSSL CVE (fix in 48.0.1) — worth a routine bump regardless of exploitability here.
5. `idna` DoS (long-domain-string resource exhaustion) and the `pyjwt`/`starlette` findings are mostly relevant to network-facing JWT/HTTP-server code paths FableGear doesn't appear to exercise for its core Rekordbox/audio workflows.

### Root Cause
Floating (`>=`-only) version specs on the two directly-pinned packages. Not a supply-chain compromise — just no upper-bound discipline.

### Impact
- Blocker: No
- Severity: Medium (the two direct pins) / Low (the 19 transitive `mcp`-only findings, given stdio is the default transport)
- Affected component: `mcp_server.py` (optional MCP integration), `downloader.py`/yt-dlp path

### Recommendations
1. **High priority, low effort:** `pip install --upgrade mcp yt-dlp cryptography` and re-run `pip-audit` to confirm clean — these are drop-in upgrades, not breaking API changes at this version distance.
2. **Medium:** Pin `mcp` and `yt-dlp` with both floor and ceiling (or at least document a re-audit cadence) so they don't drift again silently.
3. **Low:** If `--transport sse` is ever used outside trusted-localhost contexts, upgrade `mcp` to ≥1.28.1 specifically for the WebSocket origin-validation fix (PYSEC-2026-3483) before exposing it beyond localhost.

---

## Axis 3: Architecture & Design
**Status:** PASS/CAUTION (mixed)
**Confidence:** MEDIUM

### Findings
1. No circular imports detected by the AST-based import scan (0 parse errors, no self-reference cycles surfaced). Confidence is medium rather than high because the script's cycle check is a single-pass AST walk, not a true graph cycle detector — worth a `pydeps`-style check if this axis needs to be fully certain.
2. `job_dispatcher.py` **already implements per-job checkpointing** (`checkpoint_path`, `_write_checkpoint()`, `checkpoint.py`) — this is more mature than the audit template's own speculative "no per-track checkpointing" assumption. Whether it's per-track or coarser-grained wasn't verified from artifacts alone and is worth a direct read if resume-on-crash granularity matters for the 246k-track case.
3. `ws_bus.py` is a minimal 25-line broadcaster (`register`/`unregister`/`broadcast`) with **no inbound-message handling in the file itself**, and it's the one core module sitting at **0% test coverage** in the pytest run. Whatever handles inbound WebSocket messages lives elsewhere (likely `app.py`'s route registration) and wasn't traced in this pass.
4. Several modules exceed the guide's own 500-line "consider splitting" threshold: `cli.py` (4,432 lines — 8.8x), `app.py` (1,826), `chop_shop/duplicate_detector.py` (1,695), `routes_player.py` (1,541), `routes_tools.py` (1,464), `helpers.py` (1,424), `chop_shop/renamer.py` (1,399), `audio_processor.py` (1,361), `fablegear_database/database.py` (1,322), `mcp_server.py` (1,228).
5. The Record Room (`fablegear_database/`, `routes_player.py`, `library_browser/`) vs. Chop Shop (`chop_shop/`) separation holds at the package-directory level, matching the documented architecture.

### Root Cause
`cli.py`'s size is typical of a CLI that's accreted subcommands over time rather than a design flaw per se. `ws_bus.py`'s low coverage is more concerning given it's the WebSocket transport backbone.

### Impact
- Blocker: No
- Severity: Low (module size) / Medium (`ws_bus.py` untested inbound path — unclear behavior under a dropped/reconnected client)

### Recommendations
1. **Medium:** Trace where inbound WebSocket messages are actually handled (search `app.py` for the `/ws` route or equivalent) and add a test — this directly answers the "does it discard inbound messages" question the audit template raised but this static pass couldn't resolve.
2. **Low, ongoing:** `cli.py` is the one file worth planning a subcommand-per-module split for, purely for future maintainability — not urgent.

### Code Review Questions
- Is `job_dispatcher.py`'s checkpoint per-track or per-job? If per-job only, a crash after 200k/246k tracks still loses most progress.
- Where does the Flask/websocket layer route inbound client messages, and is there a test for reconnect-after-drop?

---

## Axis 4: Data Safety
**Status:** PASS (with one caveat)
**Confidence:** MEDIUM-HIGH — verified by reading the actual source, not just grep output

### Findings
1. **Destructive file operations are not permanent deletes.** `chop_shop/pruner.py` moves pruned duplicates to `~/.Trash/FableGear_Pruned_{timestamp}/` and `chop_shop/library_organizer.py` does the same (`~/.Trash/FableGear_OrgDupes_{timestamp}/`) for org-time duplicate cleanup — both explicitly documented in-file as "NOT a permanent delete."
2. `pruner.py` additionally implements a **`TrashRescueRequired` preflight gate**: the prune operation raises and refuses to proceed until the user has addressed a prior Trash-rescue state. This is a deliberately engineered safety mechanism, not an incidental side effect.
3. The **Rekordbox-must-be-closed** invariant is enforced in code (`_rb_is_running()`), checked before writes in `app.py`, `helpers.py`, `routes_tools.py`, and `chop_shop/db_migrator.py` — not merely documented as a manual precondition.
4. Database writes consistently go through context-managed connections with explicit `conn.commit()` (`fablegear_database/database.py`, `exporter.py`, `onelibrary_writer.py`, `rekordbox_fixture.py`) — a uniform transactional pattern, not ad-hoc per-call-site handling.
5. The plain `.unlink()` calls found elsewhere (`checkpoint.py`, `state_tracker.py`, `importer.py`, temp-file cleanup in `audio_processor.py`/`cli.py`) are on internal progress/checkpoint/temp files, not user audio — appropriate scope for a real delete.
6. **Caveat:** Axis 1's ~500 broad/bare `except` blocks mean some fraction of these safety mechanisms could have failure paths that are silently swallowed rather than surfaced. This audit can't distinguish, from static artifacts alone, whether any of those 420+77 sit inside the Trash-rescue gate, the transaction-commit paths, or the Rekordbox-closed check itself. That's the single item worth the author's direct read before calling this axis fully closed.

### Root Cause
The Trash-rescue and Rekordbox-closed mechanisms read as intentional, considered design — likely driven directly by the R.A.R.P. incident/constraint mentioned in project context, not an oversight being audited into existence for the first time.

### Impact
- Blocker: No
- Severity: Low, contingent on the Axis 1 caveat — if any of the ~500 broad excepts wrap the Trash-rescue gate or a commit call, that would elevate a specific instance to Medium/High
- Affected component: `chop_shop/pruner.py`, `chop_shop/library_organizer.py`, `fablegear_database/database.py`, `helpers.py`

### Recommendations
1. **High priority, low effort:** Cross-reference the `BLE001`/`S110` site list against `chop_shop/pruner.py`, `chop_shop/library_organizer.py`, `fablegear_database/database.py`, and `helpers.py` specifically — this closes the one open question on this axis.
2. **Nice-to-have:** A test asserting that `TrashRescueRequired` is actually raised (not swallowed) when the preflight condition is met, end-to-end through the public `prune` entry point rather than just the internal preflight function.

---

## Axis 5: Performance & Correctness
**Status:** Not fully assessable from static artifacts
**Confidence:** LOW

### Findings
1. The audit script doesn't collect runtime/profiling artifacts, so questions about memory usage on 246k tracks, whether librosa/fingerprinting runs async vs. blocking the UI thread, and WebSocket reconnect behavior under network glitches are **not answered by this pass** — flagging as an explicit gap rather than guessing.
2. The one static signal available: `PLW1510` (38 `subprocess.run()` calls without `check=`) means some external-tool failures (ffmpeg, fpcalc) could return non-zero and be silently ignored rather than surfaced as a job failure — this overlaps with the Axis 1 finding and is the most concrete performance/correctness-adjacent risk this audit can point to.

### Recommendations
1. This axis needs a manual or profiled pass (e.g., run BPM tagging against a few thousand tracks and watch memory/CPU, or read `audio_processor.py` and `health_acoustid.py` directly for threading/async structure) — not something a static script can resolve. Flagging as a follow-up rather than fabricating a status.

---

## Axis 6: Operational Readiness
**Status:** CAUTION
**Confidence:** HIGH

### Findings
1. Secrets scan: **clean** — no hardcoded API keys, passwords, or credential patterns found in any `.py` file.
2. Onboarding scripts exist (`install.sh`, `setup.sh`, `launch.sh`) alongside `config.py`/`user_config.py`/`ruff.toml`/`.mcp.json`.
3. **CI/CD only runs on version-tag push**, and only to build and publish the release zip (`build-release.yml`). It does **not** run `pytest`, `ruff`, or `pyright` on pull requests or ordinary pushes — meaning the 559 passing tests and the 2,087 ruff findings / 424 pyright errors above are only ever checked manually, never gating a merge.
4. Test suite: **559 passed, 0 failed, 46 warnings** in 14.9s — a healthy, fast-running suite for this size of project. Coverage is uneven: most business-logic modules with dedicated tests sit at 95–100% (`test_undo_path_safety.py`, `test_snapshot_storage.py`, etc.), but `ws_bus.py` (0%), `user_config.py` (31%), and `update_checker.py` (66%) stand out as undertested — notably `ws_bus.py` is the same file flagged in Axis 3 for its unverified inbound-message path.
5. Overall project coverage: 48% (25,065 statements, 12,935 missed) — expected for a project this size where infrastructure/CLI glue code is naturally harder to cover than pure logic.

### Root Cause
CI was built for the release/distribution use case (tag → zip → GitHub Release) rather than as a merge gate — a reasonable initial choice for a two-person project, but one that's now cheap to extend given the test suite already exists and passes.

### Impact
- Blocker: No
- Severity: Medium — the gap doesn't cause data loss by itself, but it means regressions in the axes above (silent excepts, type errors, dependency drift) can land without any automated signal until someone runs the tools by hand
- Affected component: `.github/workflows/`

### Recommendations
1. **High priority, low effort:** Add a second GitHub Actions workflow that runs `pytest`, `ruff check`, and `pyright` on every push/PR to `main`. Given the tooling and config already exist and pass locally, this is close to a copy-paste of what `fablegear_audit.sh` already runs.
2. **Medium:** Add a `pytest --cov` test targeting `ws_bus.py`'s register/unregister/broadcast plus whatever handles inbound messages, closing the same gap identified in Axis 3.

---

## Risk Matrix

| Area | Risk | Blocker? | Severity | Owner / Mitigation |
|------|------|----------|----------|---------------------|
| ~500 broad/bare `except` blocks, unverified against write/delete paths | Medium | No | Medium | Cross-reference `BLE001`/`S110` sites against `pruner.py`, `library_organizer.py`, `database.py`, `helpers.py` |
| No CI test/lint gate (tag-only release workflow) | Medium | No | Medium | Add a push/PR workflow running `pytest`, `ruff`, `pyright` |
| `mcp` and `yt-dlp` floating version pins, resolved to vulnerable versions | Medium | No | Medium | `pip install --upgrade mcp yt-dlp cryptography`; re-run `pip-audit` |
| `ws_bus.py` at 0% test coverage; inbound WebSocket handling unverified | Medium | No | Medium | Trace inbound-message handling in `app.py`; add tests |
| 38 `subprocess.run()` calls without `check=` (ffmpeg/fpcalc) | Low-Medium | No | Low | Add `check=True` or explicit return-code handling, prioritizing audio-tool calls |
| pyright: 424 type errors across 148 files | Low | No | Low | Incremental cleanup, no urgency |
| `cli.py` at 4,432 lines | Low | No | Low | Consider a subcommand-per-module split, not urgent |
| Naive `datetime.now()` in 49 sites (timestamped Trash/checkpoint folders) | Low | No | Low | Switch to timezone-aware timestamps where names are compared/sorted across machines |
| 19 transitive CVEs via `mcp`'s SSE/websocket dependency chain | Low | No | Low | Relevant only if `--transport sse` is used outside localhost; upgrade `mcp` regardless |

---

## Release Readiness Checklist

### Must Fix (Blockers)
- None found.

### Should Fix (Before v1.0)
- [ ] Verify none of the ~500 broad/bare `except` blocks sit inside the Trash-rescue gate, DB-commit paths, or `_rb_is_running()` check
- [ ] Add a CI workflow that runs tests/lint/typecheck on every push/PR, not just on release tags
- [ ] Upgrade `mcp`, `yt-dlp`, `cryptography` and re-run `pip-audit`

### Nice to Have
- [ ] Add `check=True` to the 38 unchecked `subprocess.run()` calls
- [ ] Test coverage for `ws_bus.py` and its inbound-message path
- [ ] Batch-fix the `RUF100`/`UP0xx` cosmetic ruff findings via `ruff check --fix`
- [ ] Split `cli.py` if it keeps growing

---

## Next Steps
1. **Immediate:** Spot-check the broad-except sites in the four data-safety-critical files listed above — this is the one finding in this whole audit that could theoretically escalate to a real risk, and it's a targeted, bounded read (not a rewrite).
2. **Before next release:** Wire up a test/lint CI gate; bump the three vulnerable direct/near-direct dependencies.
3. **Follow-up audit:** Re-run `fablegear_audit.sh` after the above; this report can serve as the baseline for comparison.

---

## What this audit did *not* cover (be aware)
- No live profiling/memory testing on a large (100k+ track) library was performed — Axis 5 is a static-artifact-only pass.
- Concurrency between two simultaneous users (Marshall + Cameron) was not exercised; only single-writer transactional patterns were inspected statically.
- This was run by Claude directly reading the source in one pass, combining the automated script's artifacts with targeted manual verification of the specific claims that mattered (Trash mechanism, Rekordbox-closed check, transaction pattern) — not the manual copy-artifacts-into-a-fresh-Opus-chat workflow the templates describe. That means less risk of transcription/omission error, but also means there was no independent second reviewer.
