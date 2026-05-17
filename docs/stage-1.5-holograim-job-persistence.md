# FableGear Stage 1.5 Draft

## Goal
Add durable job persistence using Holograim-style SQLite storage without changing Stage 1 behavior:
- Stage 1 remains the execution engine (thread pool, checkpoints, ws_bus broadcast).
- Stage 1.5 adds query-fast, restart-safe metadata persistence.
- Large output stays on disk; SQLite stores indexed metadata + pointers.

## Why This Helps
Current risk is session volatility and large tool results in transient chat/workspace storage.
Stage 1.5 solves this by:
- keeping job state in SQLite,
- writing full output to files on mounted volumes,
- storing only summaries and file references in DB.

## Storage Plan
- Primary artifacts: use configured ARCHIVE_ROOT from FableGear config.
- SQLite file: {ARCHIVE_ROOT}/Checkpoints/fablegear_jobs.db
- Current behavior: no automatic cross-volume fallback. If ARCHIVE_ROOT is unavailable, persistence is disabled until configuration is restored.

## Data Model

### Table: jobs
Tracks one row per dispatched job.

```sql
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  tool TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending','running','done','error')),
  scope TEXT,
  scope_hash TEXT,
  cli_args_json TEXT NOT NULL,
  dispatched_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  duration_seconds REAL,
  exit_code INTEGER,
  result_summary TEXT,
  result_blob_path TEXT,
  checkpoint_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### Table: job_events
Append-only state transitions for timeline and auditing.

```sql
CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  state TEXT,
  payload_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(job_id) REFERENCES jobs(job_id)
);
```

### Table: dependency_checks
Optional visibility into check_checkpoint decisions.

```sql
CREATE TABLE IF NOT EXISTS dependency_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tool TEXT NOT NULL,
  scope TEXT,
  scope_hash TEXT,
  checkpoint_found INTEGER NOT NULL,
  checkpoint_path TEXT,
  checked_at TEXT NOT NULL
);
```

### Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_jobs_state_dispatched ON jobs(state, dispatched_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_tool_scopehash_completed ON jobs(tool, scope_hash, completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_job_events_job_created ON job_events(job_id, created_at DESC);
```

## Write Policy
- On dispatch: insert jobs row with pending + event row.
- On transition to running: update jobs row + event row.
- On terminal state done/error: update jobs row + event row.
- On done: checkpoint JSON remains authoritative artifact on disk.
- Full stdout/stderr is written to a result blob file and referenced by result_blob_path.

## Result Blob Policy
- Directory: Archive/Checkpoints/results/YYYY/MM/DD/
- Filename: {job_id}_{tool}.log
- jobs.result_summary: first 800 chars (same as Stage 1 checkpoint summary).
- jobs.result_blob_path: absolute path to full output file.

## Concurrency + Reliability
- SQLite PRAGMAs:
  - journal_mode=WAL
  - synchronous=NORMAL
  - busy_timeout=5000
  - foreign_keys=ON
- Single writer discipline from dispatcher thread context.
- Short transactions only.
- Atomic file writes for checkpoint and live jobs remain unchanged.

## Integration Points

### job_dispatcher.py
Add internal persistence hooks:
- _db_init()
- _db_upsert_job(record)
- _db_insert_event(job_id, event_type, state, payload)
- _db_record_dependency_check(...)
- _write_result_blob(record)

Call sequence:
1. dispatch() -> save pending to DB
2. _run_job() running transition -> DB update
3. _run_job() terminal transition -> blob write + DB update + checkpoint write

No behavior change to existing MCP return payloads.

### mcp_server.py
No mandatory tool surface changes for Stage 1.5.
Optional additions after stabilization:
- get_job_history(limit, tool, state, scope)
- get_job_output(job_id)

## Retention Policy
- Keep jobs rows for 90 days.
- Keep job_events rows for 180 days.
- Keep checkpoint JSON indefinitely unless user opts in to pruning.
- Keep result blobs for 30 days (or pin per job).
- Daily cleanup task can purge expired rows/files safely.

## Recovery Behavior
On server startup:
1. Open DB.
2. Mark stale pending/running jobs as error with reason "server_restart" if no active worker.
3. Rebuild in-memory cache from recent DB rows (optional, bounded window).

## Rollout Plan
1. Add DB bootstrap and schema migration helper.
2. Wire dispatcher writes at pending/running/done/error transitions.
3. Write result blobs and store paths.
4. Add startup reconciliation for stale jobs.
5. Add optional query tools only after stability window.

## Validation Checklist
- Dispatch returns immediately with job_id.
- Job appears in DB as pending, then running.
- On success: done state, checkpoint_path populated, result_blob_path exists.
- On timeout/error: error state, exit_code=-1 or command code, event trail present.
- Restart mid-job marks stale running jobs as error:server_restart.
- list_jobs output remains backward compatible.

## Non-Goals (Stage 1.5)
- No pywebview UI implementation changes.
- No change to existing dependency gate logic.
- No Rekordbox write-path behavior changes.

## Decision Log
- Prefer configured ARCHIVE_ROOT as single source of truth for artifact and DB locality.
- Keep checkpoints as canonical human-readable artifacts; SQLite is index/telemetry layer.
- Keep checkpoints as canonical human-readable artifacts; SQLite is index/telemetry layer.
