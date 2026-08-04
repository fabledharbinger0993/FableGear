# FableGear Health & Architecture Audit — Prompt for Claude Opus

You are reviewing a Flask/pywebview DJ library management toolkit called **FableGear**. Your role is to conduct a rigorous, multi-axis audit focused on:

1. **Code Quality & Safety** — Type safety, error handling, invariants
2. **Dependency Health** — Vulnerabilities, outdated packages, supply chain risks  
3. **Architecture & Design** — Module coupling, complexity, testability, data flow
4. **Data Safety** — Database operations, file I/O, irreversible actions, recovery
5. **Performance & Correctness** — Concurrency, I/O patterns, resource leaks
6. **Operational Readiness** — Deployment, configuration, monitoring, documentation

## Context

**Project:** FableGear (Guthrie Entertainment LLC)  
**Language:** Python 3  
**Framework:** Flask (backend) + pywebview (desktop wrapper)  
**Domain:** Rekordbox library management for macOS DJs  
**Criticality:** Handles irreplaceable shared audio library data (246k+ tracks)  
**Infrastructure:** Local server on `localhost:5001`, WebSocket communication, MCP server capability

## Key Architecture

**Record Room** — Database operations (Rekordbox library management)
- Library Browser & Player
- Library Audit (path reconciliation)
- Duplicate consolidation (database-level dedup)
- Import, Fix Paths, Link Playlists
- Pioneer USB Export

**Chop Shop** — Audio file operations (destructive, filesystem-level)
- Tag Tracks (BPM, key via librosa)
- Find & Prune Duplicates (acoustic fingerprinting via Chromaprint)
- Rename, Organize, Normalize Loudness, Convert Format
- Novelty Scanner (fingerprint-based track discovery)

**Pipeline Wizard** — Chained execution with step-by-step confirmation  
**Health Monitor** — Pre-operation safety checks  
**FableGo** — Mobile companion (local network, Tailscale support)

## Critical Design Constraints (from project docs)

- **Rekordbox must be closed** for any write operation (enforced check required)
- **No breaking audio file deletes** without user confirmation + backups
- **Database writes are irreversible** if Rekordbox rolls back in-flight changes
- **Shared library**: Two DJs (Marshall Guthrie + Cameron Kelly) share ~246k-track library
- **Irreplaceable data**: R.A.R.P. recovery anchor at Sep 8, 2025 XML export (sealed)

---

## Analysis Framework

For each category below, review the audit artifacts and report:

### 1. CODE QUALITY
**Check:**
- [ ] Type safety: Are critical paths (DB writes, file ops) properly typed?
- [ ] Error handling: Do database mutations catch constraint violations?
- [ ] Exception specificity: Are generic `except Exception` patterns masking bugs?
- [ ] Invariants: Are pre-conditions (Rekordbox closed?) validated before writes?
- [ ] Resource cleanup: Do file handles, DB connections use context managers?

**Artifacts to review:**
- `ruff_check.json` — linting violations
- `pyright.json` / `mypy/` — type coverage gaps
- `module_inventory.txt` — entry point analysis

**Red flags:**
- Bare `except:` or broad `except Exception:`
- Database operations outside transactions
- File deletions without rollback capability
- Type checking skipped in audio/database modules

---

### 2. DEPENDENCY HEALTH
**Check:**
- [ ] Vulnerability scan: Any CVEs in `pip-audit` output?
- [ ] Outdated packages: Are critical deps pinned or floating?
- [ ] Transitive risks: Do third-party deps (librosa, pyrekordbox, mutagen) have known issues?
- [ ] Supply chain: Are GitHub-hosted deps using commit hashes or tags?
- [ ] License conflicts: Are all deps MIT-compatible (project is MIT)?

**Artifacts to review:**
- `pip_audit.txt` — vulnerability report
- `dependency_tree.txt` — all transitive deps
- `requirements*.txt` — version specs

**Key deps to flag:**
- `librosa` — audio analysis (scientific stack, large)
- `pyrekordbox` — database abstraction (critical path)
- `mutagen` — metadata (shared library touch point)
- `chromaprint` / `ffmpeg` — system executables (must be installed)

---

### 3. ARCHITECTURE & DESIGN
**Check:**
- [ ] Module coupling: Do Record Room and Chop Shop have clear boundaries?
- [ ] Dependency inversion: Do tools depend on abstractions or concrete DB/file APIs?
- [ ] Testability: Can core logic be tested without Flask/pywebview?
- [ ] Concurrency: How do parallel jobs interact? Any race conditions?
- [ ] MCP server: Is the MCP abstraction sustainable? Does it duplicate Flask routes?

**Artifacts to review:**
- `module_inventory.txt` — module structure
- `import_analysis.txt` — dependency graph
- Source files: `job_dispatcher.py`, `state_tracker.py`, `ws_bus.py`

**Known issues (from context):**
- `job_dispatcher.py` emits only coarse job-level states (no per-track events)
- `health_acoustid.py` is preflight-only (no match confidence score)
- WebSocket handler discards inbound messages
- No per-track checkpointing (crashes lose progress)

---

### 4. DATA SAFETY
**Check:**
- [ ] Backup strategy: Are backups created before destructive ops?
- [ ] Rollback: Can failed operations be undone?
- [ ] Atomicity: Are multi-step mutations wrapped in transactions?
- [ ] Shared library: Are concurrent writes from two DJs handled?
- [ ] File recovery: Deleted files → Trash (recoverable) or gone forever?

**Artifacts to review:**
- `db_mutations.txt` — INSERT/UPDATE/DELETE patterns
- `file_operations.txt` — os.remove, shutil.rmtree usage
- Source: `rekordbox_safe_write.py`, `checkpoint.py`, `health.py`

**Questions to answer:**
- Does the database backup run *before* every write, or selectively?
- Are file deletions moved to Trash or permanently removed?
- Can a mid-pipeline crash resume from the last completed track?
- What happens if Rekordbox opens mid-operation?

---

### 5. PERFORMANCE & CONCURRENCY
**Check:**
- [ ] Long-running ops: Do jobs report progress or hang silently?
- [ ] Memory: Can 246k tracks fit in memory, or is pagination used?
- [ ] Threads/async: Are jobs CPU-bound, I/O-bound, or both?
- [ ] WebSocket: Can clients reconnect after network glitches?
- [ ] Audio processing: Does librosa scale to 200k+ tracks?

**Artifacts to review:**
- Source: `job_dispatcher.py`, `audio_processor.py`, `health_acoustid.py`
- Test results: `pytest_output.txt` (if available)

**Stress points:**
- Acoustic fingerprinting 246k tracks (fpcalc is slow)
- Full library re-normalization (re-encode every file)
- Duplicate detection across external drives

---

### 6. OPERATIONAL READINESS
**Check:**
- [ ] Deployment: Can it start without manual setup, or does install.sh paper over gaps?
- [ ] Configuration: Are secrets kept out of git? (scan results?)
- [ ] Logging: Is there operational visibility into failures?
- [ ] Monitoring: Are long-running jobs observable from outside?
- [ ] Docs: Can a new DJ (not Marshall/Cameron) deploy and use it?

**Artifacts to review:**
- `install.sh`, `setup.sh`, `launch.sh` — deployment automation
- `secrets_scan.txt` — hardcoded credentials check
- `documentation.txt` — README, docs/ inventory
- `config_files.txt` — user_config.py, config.py
- `ci_workflows.txt` — GitHub Actions reliability

---

## Your Audit Output

For each section, provide:

1. **Status** — PASS / CAUTION / FAIL
2. **Findings** — 3–5 key observations (specific, not generic)
3. **Questions** — What would you review in the code to confirm?
4. **Recommendations** — Actionable next steps (prioritized)

## Explicit Non-Goals

- Do not suggest rewrites or major architectural changes unless clearly justified
- Do not demand 100% test coverage (but flag missing critical paths)
- Do not assume the latest versions are always better (flag trade-offs)
- Do not be prescriptive about which linter to use if the current choice works

## Constraints

- Assume Rekordbox is closed for write ops (monitor is responsible)
- Assume the shared library is a deliberate design (not a bug to fix)
- Assume FableGear is a serious tool, not a toy (safety is paramount)
- Assume the audit artifacts may be incomplete (tools might not be installed)

---

## Final Output Format

```
# FableGear Health Audit Report
**Audit Date:** [today]
**Reviewer:** Claude Opus
**Confidence:** [HIGH/MEDIUM/LOW based on artifact coverage]

## Executive Summary
[1–2 paragraph overview of project health]

### 1. Code Quality
**Status:** [PASS/CAUTION/FAIL]
**Findings:**
- [specific finding with line/module reference]
- [...]

**Recommendation Priority:**
1. [High-impact, low-effort fixes]
2. [Medium-impact, medium-effort changes]
3. [Nice-to-haves]

### 2. Dependency Health
[Same structure...]

### 3. Architecture & Design
[Same structure...]

### 4. Data Safety
[Same structure...]

### 5. Performance & Concurrency
[Same structure...]

### 6. Operational Readiness
[Same structure...]

## Risk Matrix
| Area | Risk Level | Blocker? | Owner |
|------|-----------|----------|-------|
| Database writes | [HIGH/MED/LOW] | [Yes/No] | [pyrekordbox integration] |
| Shared library concurrency | ... | ... | ... |
| [etc] | | | |

## Next Steps
1. [Highest-priority action]
2. [Second action]
3. [Follow-up review]
```

---

## How to Use This Audit

**If you're the code owner (Marshall):**
- Focus on the Recommendation sections and Risk Matrix
- Ignore nice-to-haves if they don't align with your roadmap
- Dispute findings that don't match your design intent

**If you're reviewing before a major release:**
- Treat FAIL and BLOCKER findings as gates
- Implement High-priority recommendations before shipping
- Add a regression test for each bug found

**If you're onboarding new contributors:**
- Use this audit as a baseline for code health expectations
- Feed findings into contributor docs (e.g., "Always use context managers for DB connections")

---

## Audit Artifacts

Below, include the full output from each phase:

1. Linting results (ruff, pyright/mypy)
2. Dependency tree
3. Module inventory
4. Import graph analysis
5. Database mutation patterns
6. File operation patterns
7. Secrets scan
8. Test coverage results
9. Platform compatibility notes
10. Any other relevant diagnostics

**[INSERT AUDIT ARTIFACTS HERE]**

---

**End of Prompt**
