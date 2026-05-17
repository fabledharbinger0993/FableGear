import hashlib
import json
import logging
import re
import sqlite3
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional

MAX_WORKERS = 4
LIVE_JOBS_FILENAME = ".live_jobs.json"
CHECKPOINT_RESULT_TRUNCATE = 800
DB_FILENAME = "fablegear_jobs.db"
RESULTS_DIRNAME = "results"
TOOL_NAME_RE = re.compile(r"^[a-z0-9_]+$")


@dataclass
class JobRecord:
    job_id: str
    tool: str
    state: Literal["pending", "running", "done", "error"]
    scope: Optional[str]
    cli_args: List[str]
    dispatched_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    result: Optional[str] = None
    exit_code: Optional[int] = None
    checkpoint_path: Optional[str] = None
    result_blob_path: Optional[str] = None


_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="fablegear")
_jobs: Dict[str, JobRecord] = {}
_jobs_lock = threading.Lock()

_archive_checkpoints_dir: Optional[Path] = None
_repo_dir: Optional[Path] = None
_db_path: Optional[Path] = None
_persistence_dir: Optional[Path] = None
_db_lock = threading.Lock()
_log = logging.getLogger("fablegear.job_dispatcher")


def init(repo_dir: Path, archive_checkpoints_dir: Optional[Path]) -> None:
    """
    Called once at MCP server startup.
    repo_dir                  — Path to the FableGear repo root (for cli.py)
    archive_checkpoints_dir   — Path to Archive/Checkpoints/; None if not yet configured.
                                 Can be updated later via reconfigure().
    """
    global _repo_dir, _archive_checkpoints_dir
    _repo_dir = repo_dir
    _archive_checkpoints_dir = archive_checkpoints_dir
    if _archive_checkpoints_dir:
        try:
            _archive_checkpoints_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Fall back to alternate persistence roots if configured path is unavailable.
            _archive_checkpoints_dir = None
    _setup_persistence(startup=True)


def reconfigure(archive_checkpoints_dir: Path) -> None:
    """
    Call after configure_paths() succeeds so checkpoints start writing
    to the newly configured archive location.
    """
    global _archive_checkpoints_dir
    _archive_checkpoints_dir = archive_checkpoints_dir
    try:
        _archive_checkpoints_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        _archive_checkpoints_dir = None
    _setup_persistence(startup=False)


def dispatch(
    tool: str,
    cli_args: List[str],
    scope: Optional[str] = None,
    timeout: int = 600,
) -> str:
    """
    Dispatch a CLI job to the thread pool.
    Returns a JSON string (the job handle) immediately.
    The job runs in the background; poll with get_status(job_id).

    Args:
        tool:      Tool name, matches the MCP tool function name (e.g. "tag_tracks").
        cli_args:  Arguments passed to cli.py (e.g. ["process", "/Volumes/Passport/..."]).
        scope:     The primary path the tool operates on — used as the checkpoint key.
                   Pass None for tools that don't have a meaningful path scope.
        timeout:   Subprocess timeout in seconds.
    """
    job_id = uuid.uuid4().hex[:10]
    now_iso = _now()
    normalized_scope = _canonical_scope(scope)

    record = JobRecord(
        job_id=job_id,
        tool=tool,
        state="pending",
        scope=normalized_scope,
        cli_args=cli_args,
        dispatched_at=now_iso,
    )

    with _jobs_lock:
        _jobs[job_id] = record

    _db_upsert_job(record)
    _db_insert_event(record.job_id, "dispatched", record.state, {"tool": tool, "scope": normalized_scope})
    _broadcast(record)
    _write_live_jobs()

    _executor.submit(_run_job, job_id, timeout)

    return json.dumps(
        {
            "job_id": job_id,
            "tool": tool,
            "state": "pending",
            "scope": normalized_scope,
            "dispatched_at": now_iso,
            "message": (
                f"Job dispatched. Use get_job_status('{job_id}') to poll progress, "
                "or watch the FableGear companion window if it's open."
            ),
        },
        indent=2,
    )


def _run_job(job_id: str, timeout: int) -> None:
    with _jobs_lock:
        record = _jobs[job_id]
        record.state = "running"
        record.started_at = _now()

    _db_upsert_job(record)
    _db_insert_event(record.job_id, "state_change", record.state, {"timeout": timeout})
    _broadcast(record)
    _write_live_jobs()

    cmd = [sys.executable, str(_repo_dir / "cli.py"), *record.cli_args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(_repo_dir),
        )
        output = (result.stdout + result.stderr).strip()
        exit_ok = result.returncode == 0
        with _jobs_lock:
            record.state = "done" if exit_ok else "error"
            record.exit_code = result.returncode
            record.result = output
            record.result_blob_path = _write_result_blob(record)

    except subprocess.TimeoutExpired:
        with _jobs_lock:
            record.state = "error"
            record.result = f"Timed out after {timeout}s."
            record.exit_code = -1
            record.result_blob_path = _write_result_blob(record)

    except Exception as exc:
        with _jobs_lock:
            record.state = "error"
            record.result = f"Dispatch error: {exc}"
            record.exit_code = -1
            record.result_blob_path = _write_result_blob(record)

    completed = _now()
    with _jobs_lock:
        record.completed_at = completed
        started_at = record.started_at or completed
        record.duration_seconds = round(
            (datetime.fromisoformat(completed) - datetime.fromisoformat(started_at)).total_seconds(),
            1,
        )
        if record.state == "done":
            record.checkpoint_path = _write_checkpoint(record)

    _db_upsert_job(record)
    _db_insert_event(
        record.job_id,
        "state_change",
        record.state,
        {
            "exit_code": record.exit_code,
            "checkpoint_path": record.checkpoint_path,
            "result_blob_path": record.result_blob_path,
        },
    )
    _broadcast(record)
    _write_live_jobs()


def _write_checkpoint(record: JobRecord) -> Optional[str]:
    """
    Write a completion checkpoint JSON to Archive/Checkpoints/.
    Returns the path written, or None if the archive dir is unavailable.

    Filename format:
        {tool}_{scope_hash8}_{yyyymmddTHHMMSS}.json

    scope_hash8 is the first 8 hex chars of sha256(scope).
    For tools with no scope, uses "global" as the hash token.
    """
    candidate_dirs = _checkpoint_dirs()
    if not candidate_dirs:
        return None

    primary_scope = record.scope or ""
    hash_tok = hashlib.sha256(primary_scope.encode()).hexdigest()[:8] if primary_scope else "global"
    ts = record.completed_at.replace(":", "").replace("-", "").replace("T", "T")[:15]
    filename = f"{record.tool}_{hash_tok}_{ts}_{record.job_id}.json"
    payload = {
        "job_id": record.job_id,
        "tool": record.tool,
        "state": record.state,
        "scope": primary_scope,
        "scope_hash": hash_tok,
        "dispatched_at": record.dispatched_at,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "duration_seconds": record.duration_seconds,
        "exit_code": record.exit_code,
        "result_summary": (record.result or "")[:CHECKPOINT_RESULT_TRUNCATE],
    }

    scope_variants = [primary_scope]
    for alias in _checkpoint_scope_aliases(record):
        if alias not in scope_variants:
            scope_variants.append(alias)

    primary_written_path: Optional[str] = None
    for checkpoint_dir in candidate_dirs:
        for idx, scoped in enumerate(scope_variants):
            scope_hash = hashlib.sha256(scoped.encode()).hexdigest()[:8] if scoped else "global"
            scoped_payload = dict(payload)
            scoped_payload["scope"] = scoped
            scoped_payload["scope_hash"] = scope_hash
            if idx > 0:
                scoped_payload["scope_alias_of"] = primary_scope
            scoped_filename = f"{record.tool}_{scope_hash}_{ts}_{record.job_id}.json"
            path = checkpoint_dir / scoped_filename
            try:
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(scoped_payload, indent=2) + "\n", encoding="utf-8")
                tmp.replace(path)
                if idx == 0 and primary_written_path is None:
                    primary_written_path = str(path)
            except OSError:
                continue
    return primary_written_path


def _write_live_jobs() -> None:
    """
    Atomically write all current job states to .live_jobs.json in the
    checkpoints dir. Stage 2 companion window polls this file when no
    WebSocket connection is established yet.
    Silently skips if checkpoints dir is unavailable.
    """
    candidate_dirs = _checkpoint_dirs()
    if not candidate_dirs:
        return
    try:
        with _jobs_lock:
            snapshot = {jid: asdict(r) for jid, r in _jobs.items()}
        payload = json.dumps(snapshot, indent=2) + "\n"
        for checkpoint_dir in candidate_dirs:
            try:
                path = checkpoint_dir / LIVE_JOBS_FILENAME
                tmp = path.with_suffix(".tmp")
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(path)
                break
            except OSError:
                continue
    except OSError:
        pass


def _broadcast(record: JobRecord) -> None:
    """
    Broadcast tool state to any registered WebSocket clients.
    No-op if ws_bus has no registered clients or import fails.
    Designed to be a stub here; Stage 2 wires the actual clients.
    """
    try:
        import ws_bus  # noqa: PLC0415

        payload = json.dumps(
            {
                "event": "tool_state",
                "job_id": record.job_id,
                "tool": record.tool,
                "state": record.state,
                "scope": record.scope,
                "duration_seconds": record.duration_seconds,
                "timestamp": _now(),
            }
        )
        ws_bus.broadcast(payload)
    except Exception:
        pass


def get_status(job_id: str) -> Optional[Dict]:
    """Return the JobRecord dict for job_id, or None if not found."""
    with _jobs_lock:
        record = _jobs.get(job_id)
    return asdict(record) if record else None


def list_all(state_filter: Optional[str] = None) -> List[Dict]:
    """Return all job records, optionally filtered by state string."""
    with _jobs_lock:
        records = list(_jobs.values())
    result = [asdict(r) for r in records]
    if state_filter:
        result = [r for r in result if r["state"] == state_filter]
    return sorted(result, key=lambda r: r["dispatched_at"], reverse=True)


def find_checkpoint(tool: str, scope: str = "") -> Optional[Dict]:
    """
    Return the most recent completed checkpoint for tool + scope, or None.
    Searches Archive/Checkpoints/ by filename pattern.
    Scope matching is by sha256 hash so paths don't need to be exact strings.
    """
    candidate_dirs = _checkpoint_dirs()
    if not candidate_dirs:
        return None

    if not TOOL_NAME_RE.fullmatch(tool):
        _db_record_dependency_check(
            tool=tool,
            scope=_canonical_scope(scope),
            checkpoint_found=False,
            checkpoint_path=None,
        )
        return None

    normalized_scope = _canonical_scope(scope)
    hash_tok = hashlib.sha256(normalized_scope.encode()).hexdigest()[:8] if normalized_scope else "global"
    pattern = f"{tool}_{hash_tok}_*.json"
    matches = []
    for checkpoint_dir in candidate_dirs:
        try:
            matches.extend(checkpoint_dir.glob(pattern))
        except OSError:
            continue
    timed_matches = []
    for path in matches:
        try:
            timed_matches.append((path, path.stat().st_mtime))
        except OSError:
            continue
    timed_matches.sort(key=lambda item: item[1], reverse=True)

    for path, _mtime in timed_matches:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("state") == "done":
                _db_record_dependency_check(
                    tool=tool,
                    scope=normalized_scope,
                    checkpoint_found=True,
                    checkpoint_path=str(path),
                )
                return data
        except Exception:
            continue
    _db_record_dependency_check(
        tool=tool,
        scope=normalized_scope,
        checkpoint_found=False,
        checkpoint_path=None,
    )
    return None


def get_history(
    limit: int = 50,
    tool: Optional[str] = None,
    state: Optional[str] = None,
    scope: Optional[str] = None,
) -> List[Dict]:
    """Return persisted job history from SQLite, newest first."""
    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500

    with _db_lock:
        conn = _db_connect()
        if conn is None:
            return []
        try:
            where = []
            params: List[object] = []
            if tool:
                where.append("tool = ?")
                params.append(tool)
            if state:
                where.append("state = ?")
                params.append(state)
            if scope:
                where.append("scope_hash = ?")
                params.append(_scope_hash(scope))

            where_clause = ""
            if where:
                where_clause = " WHERE " + " AND ".join(where)

            query = (
                "SELECT job_id, tool, state, scope, dispatched_at, started_at, completed_at, "
                "duration_seconds, exit_code, result_summary, result_blob_path, checkpoint_path "
                "FROM jobs"
                f"{where_clause} "
                "ORDER BY dispatched_at DESC "
                "LIMIT ?"
            )
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error:
            _log.exception("job persistence history query failed")
            return []
        finally:
            conn.close()


def get_output(job_id: str, max_chars: int = 0) -> Optional[Dict]:
    """Return job output details including persisted result blob content when available."""
    output: Optional[str] = None
    source = "none"

    with _jobs_lock:
        in_mem = _jobs.get(job_id)

    record: Optional[Dict] = None
    if in_mem is not None:
        record = asdict(in_mem)
        if in_mem.result:
            output = in_mem.result
            source = "memory"

    if record is None:
        with _db_lock:
            conn = _db_connect()
            if conn is None:
                return None
            try:
                row = conn.execute(
                    """
                    SELECT job_id, tool, state, scope, dispatched_at, started_at, completed_at,
                           duration_seconds, exit_code, result_summary, result_blob_path, checkpoint_path
                    FROM jobs
                    WHERE job_id = ?
                    """,
                    (job_id,),
                ).fetchone()
                if row is None:
                    return None
                record = dict(row)
            except sqlite3.Error:
                _log.exception("job persistence output query failed for job_id=%s", job_id)
                return None
            finally:
                conn.close()

    blob_path = record.get("result_blob_path") if record else None
    if output is None and blob_path:
        try:
            blob_candidate = Path(blob_path).resolve(strict=False)
            if _is_allowed_blob_path(blob_candidate):
                output = blob_candidate.read_text(encoding="utf-8")
                source = "blob"
        except OSError:
            output = None

    if output is None:
        output = record.get("result_summary") if record else None
        source = "summary"

    if isinstance(output, str) and max_chars and max_chars > 0:
        output = output[:max_chars]

    response = dict(record)
    response["output_source"] = source
    response["output"] = output
    return response


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _scope_hash(scope: Optional[str]) -> str:
    normalized = _canonical_scope(scope)
    return hashlib.sha256(normalized.encode()).hexdigest()[:8] if normalized else "global"


def _canonical_scope(scope: Optional[str]) -> str:
    if not scope:
        return ""
    try:
        return str(Path(scope).expanduser().resolve(strict=False))
    except Exception:
        return str(scope).strip()


def _resolve_persistence_dir() -> Optional[Path]:
    preferred: List[Path] = []
    if _archive_checkpoints_dir is not None:
        preferred.append(_archive_checkpoints_dir)

    for candidate in preferred:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


def _setup_persistence(startup: bool) -> None:
    global _db_path, _persistence_dir
    checkpoints_dir = _resolve_persistence_dir()
    if checkpoints_dir is None:
        _persistence_dir = None
        _db_path = None
        return
    _persistence_dir = checkpoints_dir
    _db_path = checkpoints_dir / DB_FILENAME
    _db_init()
    if startup:
        _db_mark_stale_jobs()


def _db_connect() -> Optional[sqlite3.Connection]:
    if _db_path is None:
        return None
    try:
        conn = sqlite3.connect(str(_db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    except sqlite3.Error as exc:
        _log.warning("job persistence DB connect failed at %s: %s", _db_path, exc)
        return None


def _db_init() -> None:
    with _db_lock:
        conn = _db_connect()
        if conn is None:
            return
        try:
            conn.executescript(
                """
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

                CREATE TABLE IF NOT EXISTS job_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  job_id TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  state TEXT,
                  payload_json TEXT,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                );

                CREATE TABLE IF NOT EXISTS dependency_checks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  tool TEXT NOT NULL,
                  scope TEXT,
                  scope_hash TEXT,
                  checkpoint_found INTEGER NOT NULL,
                  checkpoint_path TEXT,
                  checked_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_state_dispatched ON jobs(state, dispatched_at DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_tool_scopehash_completed ON jobs(tool, scope_hash, completed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_job_events_job_created ON job_events(job_id, created_at DESC);
                """
            )
            conn.commit()
        except sqlite3.Error:
            _log.exception("job persistence schema initialization failed")
        finally:
            conn.close()


def _db_upsert_job(record: JobRecord) -> None:
    with _db_lock:
        conn = _db_connect()
        if conn is None:
            return
        now = _now()
        try:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, tool, state, scope, scope_hash, cli_args_json,
                    dispatched_at, started_at, completed_at, duration_seconds,
                    exit_code, result_summary, result_blob_path, checkpoint_path,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    state=excluded.state,
                    scope=excluded.scope,
                    scope_hash=excluded.scope_hash,
                    cli_args_json=excluded.cli_args_json,
                    dispatched_at=excluded.dispatched_at,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at,
                    duration_seconds=excluded.duration_seconds,
                    exit_code=excluded.exit_code,
                    result_summary=excluded.result_summary,
                    result_blob_path=excluded.result_blob_path,
                    checkpoint_path=excluded.checkpoint_path,
                    updated_at=excluded.updated_at
                """,
                (
                    record.job_id,
                    record.tool,
                    record.state,
                    record.scope,
                    _scope_hash(record.scope),
                    json.dumps(record.cli_args),
                    record.dispatched_at,
                    record.started_at,
                    record.completed_at,
                    record.duration_seconds,
                    record.exit_code,
                    (record.result or "")[:CHECKPOINT_RESULT_TRUNCATE],
                    record.result_blob_path,
                    record.checkpoint_path,
                    now,
                    now,
                ),
            )
            conn.commit()
        except sqlite3.Error:
            _log.exception("job persistence upsert failed for job_id=%s", record.job_id)
        finally:
            conn.close()


def _db_insert_event(job_id: str, event_type: str, state: Optional[str], payload: Dict) -> None:
    with _db_lock:
        conn = _db_connect()
        if conn is None:
            return
        try:
            conn.execute(
                """
                INSERT INTO job_events (job_id, event_type, state, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, event_type, state, json.dumps(payload), _now()),
            )
            conn.commit()
        except sqlite3.Error:
            _log.exception("job persistence event insert failed for job_id=%s", job_id)
        finally:
            conn.close()


def _db_record_dependency_check(
    tool: str,
    scope: str,
    checkpoint_found: bool,
    checkpoint_path: Optional[str],
) -> None:
    with _db_lock:
        conn = _db_connect()
        if conn is None:
            return
        try:
            conn.execute(
                """
                INSERT INTO dependency_checks (
                    tool, scope, scope_hash, checkpoint_found, checkpoint_path, checked_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    tool,
                    scope,
                    _scope_hash(scope),
                    1 if checkpoint_found else 0,
                    checkpoint_path,
                    _now(),
                ),
            )
            conn.commit()
        except sqlite3.Error:
            _log.exception(
                "job persistence dependency check insert failed for tool=%s scope=%s",
                tool,
                scope,
            )
        finally:
            conn.close()


def _db_mark_stale_jobs() -> None:
    with _db_lock:
        conn = _db_connect()
        if conn is None:
            return
        now = _now()
        try:
            stale_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT job_id FROM jobs WHERE state IN ('pending', 'running')"
                ).fetchall()
            ]
            conn.execute(
                """
                UPDATE jobs
                SET state='error',
                    completed_at=COALESCE(completed_at, ?),
                    exit_code=COALESCE(exit_code, -1),
                    result_summary=CASE
                        WHEN result_summary IS NULL OR result_summary = ''
                        THEN 'server_restart: job state reconciled on startup'
                        ELSE result_summary
                    END,
                    updated_at=?
                WHERE state IN ('pending', 'running')
                """,
                (now, now),
            )
            if stale_ids:
                conn.executemany(
                    """
                    INSERT INTO job_events (job_id, event_type, state, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            jid,
                            "state_change",
                            "error",
                            json.dumps({"reason": "server_restart"}),
                            now,
                        )
                        for jid in stale_ids
                    ],
                )
            conn.commit()
        except sqlite3.Error:
            _log.exception("job persistence stale-job reconciliation failed")
        finally:
            conn.close()


def _write_result_blob(record: JobRecord) -> Optional[str]:
    candidate_dirs = _checkpoint_dirs()
    if not candidate_dirs:
        return None
    when = datetime.now(timezone.utc)
    for checkpoints_dir in candidate_dirs:
        try:
            blob_dir = checkpoints_dir / RESULTS_DIRNAME / when.strftime("%Y") / when.strftime("%m") / when.strftime("%d")
            blob_dir.mkdir(parents=True, exist_ok=True)
            blob_path = blob_dir / f"{record.job_id}_{record.tool}.log"
            tmp_path = blob_path.with_suffix(".tmp")
            tmp_path.write_text((record.result or "") + "\n", encoding="utf-8")
            tmp_path.replace(blob_path)
            return str(blob_path)
        except OSError:
            continue
    return None


def _checkpoint_dirs() -> List[Path]:
    dirs: List[Path] = []
    for candidate in (_archive_checkpoints_dir, _persistence_dir):
        if candidate is None or candidate in dirs:
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            if candidate.exists() and candidate.is_dir():
                dirs.append(candidate)
        except OSError:
            continue
    return dirs


def _is_allowed_blob_path(blob_path: Path) -> bool:
    for checkpoint_dir in _checkpoint_dirs():
        allowed_root = (checkpoint_dir / RESULTS_DIRNAME).resolve(strict=False)
        try:
            blob_path.relative_to(allowed_root)
            return True
        except ValueError:
            continue
    return False


def _checkpoint_scope_aliases(record: JobRecord) -> List[str]:
    aliases: List[str] = []
    if record.tool == "organize_library" and len(record.cli_args) >= 3 and record.cli_args[0] == "organize":
        source_scope = _canonical_scope(record.cli_args[1])
        target_scope = _canonical_scope(record.cli_args[2])
        for scoped in (source_scope, target_scope):
            if scoped and scoped != (record.scope or ""):
                aliases.append(scoped)
    return aliases