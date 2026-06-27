"""
FableGear / app.py  —  thin factory

Registers blueprints, applies Flask extensions, runs startup side-effects,
and keeps the small set of core routes that do not belong in any blueprint.

Blueprint layout:
  routes_player.py    — The Media Pit    (/api/library/*, /api/playback/*, /audio/*)
  routes_tools.py     — The Butcher Shop (/api/run/process*, /api/run/organize,
                                          /api/run/duplicates, /api/normalize/*, etc.)
  routes_rekordbox.py — The Zombie Machine (/api/run/audit, /api/run/import,
                                            /api/run/link, /api/run/relocate,
                                            /api/migrate-pioneer-db)
  routes_mobile.py    — The Overlord    (/api/mobile/*, /api/connectivity)
"""

import json
import mimetypes
import os
import platform
import psutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
from werkzeug.exceptions import NotFound

_SYSTEM = platform.system()  # "Darwin" | "Windows" | "Linux"

from flask import Flask, Response, jsonify, render_template, render_template_string, request, send_file

# ── Shared helpers (base layer — no circular imports) ─────────────────────────
from helpers import (
    REPO_ROOT,
    limiter,
    sock,
    _rb_is_running,
    _backup_info,
    _release_info,
    _sse_response,
    get_step_status,
    list_running_managed_subprocesses,
    terminate_managed_subprocesses,
)

_REPO_ROOT = REPO_ROOT   # local alias for legacy references below

# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(
    __name__,
    template_folder=str(REPO_ROOT / "templates"),   # index.html + partials/
    static_folder=str(REPO_ROOT / "static"),
)


_LEGACY_STATIC_ALIASES = {
    "RB_LOGO.png": "FableGear-logo.png",
    "icon-audit.png": "icon-settings.png",
    "icon-convert.png": "icon-converter.png",
    "icon-fablego.png": "icon-logo-fablegear.png",
    "icon-fg-drives.png": "icon-drives.png",
    "icon-fg-files.png": "icon-queue.png",
    "icon-fg-library.png": "icon-record-room.png",
    "icon-fg-rb-tools.png": "icon-settings.png",
    "icon-fg-tools.png": "icon-chop-shop.png",
    "icon-filename.png": "icon-renamer.png",
    "icon-find-duplicate.png": "icon-deduper.png",
    "icon-folder.png": "icon-drives.png",
    "icon-import.png": "icon-queue.png",
    "icon-interrupt-plus-stop-wizard.png": "icon-settings.png",
    "icon-link.png": "icon-settings.png",
    "icon-move.png": "icon-settings.png",
    "icon-normalize.png": "icon-normalizer.png",
    "icon-prune.png": "icon-deduper.png",
    "icon-rekki.png": "icon-studio.png",
    "icon-restart-from-interrupt.png": "icon-settings.png",
    "icon-restart-step.png": "icon-settings.png",
    "icon-site-key.png": "icon-settings.png",
    "icon-skip-to-next-step.png": "icon-settings.png",
    "icon-start-wizard.png": "icon-settings.png",
    "icon-tag.png": "icon-track-tagger.png",
    "icon-track.png": "icon-track-tagger.png",
    "icon-welcome-info.png": "icon-studio.png",
}


def _resolve_legacy_static_path(filename: str) -> Path | None:
    """Resolve old static filenames after the icon pack rename/restructure."""
    name = Path(filename).name
    candidates = [
        REPO_ROOT / "static" / name,
        REPO_ROOT / "static" / "images" / name,
    ]

    aliased = _LEGACY_STATIC_ALIASES.get(name)
    if aliased:
        candidates.extend([
            REPO_ROOT / "static" / aliased,
            REPO_ROOT / "static" / "images" / aliased,
        ])

    for path in candidates:
        if path.is_file():
            return path
    return None


def _send_static_with_legacy_fallback(filename: str):
    """Serve /static/<file>; fall back to renamed icon assets when needed."""
    try:
        return Flask.send_static_file(app, filename)
    except NotFound:
        legacy_path = _resolve_legacy_static_path(filename)
        if legacy_path:
            mime, _ = mimetypes.guess_type(str(legacy_path))
            return send_file(str(legacy_path), mimetype=mime)
        raise


app.send_static_file = _send_static_with_legacy_fallback

# Attach lazy-init extensions to the app instance
limiter.init_app(app)
sock.init_app(app)

# Cache-bust token — changes every server start so WKWebView picks up new assets
import time as _time
_CACHE_BUST = str(int(_time.time()))

@app.context_processor
def inject_cache_bust():
    return {"cb": _CACHE_BUST}


# ── Network boundary ──────────────────────────────────────────────────────────
# main.py binds 0.0.0.0 so FableGo can reach the mobile API over Tailscale/LAN.
# That must NOT expose the desktop tool routes (/api/run/prune, /api/run/rename,
# etc.) to the network: they are unauthenticated by design because the desktop
# UI talks to them over loopback only.
#
# Policy, first match wins:
#   1. Loopback (127.0.0.1 / ::1)        → allow  (desktop UI, pywebview)
#   2. /api/mobile/*                      → allow  (blueprint + sock handlers
#                                                   enforce their own Bearer auth)
#   3. /api/connectivity                  → allow  (handler enforces loopback itself)
#   4. GET /static/*                      → allow  (public assets: css/js/icons)
#   5. Valid "Authorization: Bearer <mobile_token>"
#                                         → allow  (deliberate remote access)
#   6. allow_lan_ui: true in ~/.fablegear/config.json
#                                         → allow  (explicit owner opt-out)
#   7. otherwise                          → 403

def _lan_ui_allowed() -> bool:
    """Owner opt-out: {"allow_lan_ui": true} in ~/.fablegear/config.json."""
    try:
        import json as _json
        cfg_path = Path.home() / ".fablegear" / "config.json"
        return bool(_json.loads(cfg_path.read_text()).get("allow_lan_ui", False))
    except Exception:
        return False


@app.before_request
def _enforce_network_boundary():
    if request.remote_addr in ("127.0.0.1", "::1"):
        return
    if request.path.startswith("/api/mobile/"):
        return  # mobile surface authenticates itself (Bearer, incl. websocket)
    if request.path == "/api/connectivity":
        return  # handler performs its own loopback check
    if request.method in ("GET", "HEAD") and request.path.startswith("/static/"):
        return
    # Deliberate remote access with the FableGo token (same secret, same
    # constant-time comparison as the mobile blueprint).
    try:
        from routes_mobile import _read_mobile_token  # noqa: PLC0415
        import hmac as _hmac_mod  # noqa: PLC0415
        token = _read_mobile_token()
        auth = request.headers.get("Authorization", "")
        if token and auth.startswith("Bearer ") and _hmac_mod.compare_digest(auth[7:], token):
            return
    except Exception:
        pass
    if _lan_ui_allowed():
        return
    app.logger.warning(
        "Blocked non-loopback request from %s for %s %s",
        request.remote_addr, request.method, request.path,
    )
    return jsonify({
        "error": "forbidden",
        "message": "This endpoint is loopback-only. Set allow_lan_ui in "
                   "~/.fablegear/config.json or send the FableGo Bearer token "
                   "for remote access.",
    }), 403

# ── Blueprints ────────────────────────────────────────────────────────────────

from routes_player     import bp as player_bp      # noqa: E402
from routes_tools      import bp as tools_bp        # noqa: E402
from routes_rekordbox  import bp as rekordbox_bp    # noqa: E402
from routes_mobile     import bp as mobile_bp       # noqa: E402
from routes_undo       import bp as undo_bp         # noqa: E402

app.register_blueprint(player_bp)
app.register_blueprint(tools_bp)
app.register_blueprint(rekordbox_bp)
app.register_blueprint(mobile_bp)
app.register_blueprint(undo_bp)

# ── Startup side-effects ──────────────────────────────────────────────────────

from brew_updater import (                          # noqa: E402
    start_background_checker as _start_brew_checker,
    check_now as _brew_check_now,
    get_status as _brew_get_status,
)
_start_brew_checker()

from update_checker import (                        # noqa: E402
    start_background_checker as _start_update_checker,
    get_status as _update_get_status,
)
_start_update_checker()

try:
    from config import ensure_archive_structure     # noqa: PLC0415
    ensure_archive_structure()
except Exception:
    pass  # Drive not mounted yet — non-fatal


# ── Health check cache ────────────────────────────────────────────────────────
# Findings are refreshed at startup and whenever /api/health is called.
# /api/status includes only the severity summary (counts) to keep polling cheap.

_health_lock = threading.Lock()
_health_cache: list[dict] = []   # list of HealthFinding.as_dict()
_health_refreshed_at: float = 0.0

_HEALTH_TTL = 60.0  # seconds before cache is considered stale


def _refresh_health_cache(force: bool = False) -> list[dict]:
    """
    Re-run all health checks, apply safe auto-heals, and update the cache.
    Throttled to _HEALTH_TTL unless force=True.
    Thread-safe.
    """
    import time as _time  # noqa: PLC0415
    global _health_cache, _health_refreshed_at

    with _health_lock:
        if not force and (_time.time() - _health_refreshed_at) < _HEALTH_TTL:
            return _health_cache

        try:
            from health import run_health_checks, auto_heal_safe  # noqa: PLC0415
            findings = run_health_checks()
            healed = auto_heal_safe(findings)
            if healed:
                # Re-run after healing to remove resolved findings
                findings = run_health_checks()
            _health_cache = [f.as_dict() for f in findings]
        except Exception as exc:
            import logging as _logging  # noqa: PLC0415
            _logging.getLogger(__name__).warning("Health check failed: %s", exc)
            _health_cache = []

        _health_refreshed_at = _time.time()
        return _health_cache


def _health_summary(findings: list[dict]) -> dict:
    counts = {"critical": 0, "warn": 0, "info": 0}
    for f in findings:
        s = f.get("severity", "info")
        counts[s] = counts.get(s, 0) + 1
    counts["total"] = sum(counts.values())
    return counts


# Run once at startup in a background thread (non-blocking)
threading.Thread(target=_refresh_health_cache, kwargs={"force": True},
                 daemon=True, name="health-startup").start()


# ── Splash route ──────────────────────────────────────────────────────────────

_SPLASH_HTML = """\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>FableGear</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body {
      width: 100%; height: 100%;
      background: #07070f;
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    video {
      width: 100%; height: 100%;
      object-fit: contain;
      opacity: 1;
      transition: opacity 0.5s ease;
    }
    video.fade-out { opacity: 0; }
  </style>
</head>
<body>
  <video id="splash" autoplay playsinline>
    <source src="/static/fablegear-splash.mp4" type="video/mp4">
  </video>
  <script>
    var v = document.getElementById('splash');
    var done = false;
    function finish() {
      if (done) return;
      done = true;
      v.classList.add('fade-out');
      setTimeout(function() {
        fetch('/api/setup-status')
          .then(function(r) { return r.json(); })
          .then(function(s) {
            window.location.replace(s.setup_complete ? '/' : '/onboarding');
          })
          .catch(function() { window.location.replace('/onboarding'); });
      }, 550);
    }
    v.addEventListener('ended', finish);
    v.addEventListener('error', function() { window.location.replace('/'); });
    setTimeout(finish, 35000);
  </script>
</body>
</html>
"""


# ── Core routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    from flask import redirect as _redirect  # noqa: PLC0415
    from user_config import config_exists   # noqa: PLC0415
    try:
        if not config_exists():
            return _redirect("/onboarding")
        _st = _load_setup_state(repair=True)
        if not _st.get("setup_complete"):
            return _redirect("/onboarding")
    except Exception:
        pass
    return render_template("index.html")


@app.route("/splash")
def splash():
    return render_template_string(_SPLASH_HTML)


@app.route("/api/status")
@limiter.exempt
def api_status():
    from user_config import get_drive_status  # noqa: PLC0415
    findings = _refresh_health_cache()        # returns cached unless stale
    return jsonify({
        "rb_running": _rb_is_running(),
        "backup":     _backup_info(),
        "release":    _release_info(),
        "drives":     get_drive_status(),
        "health":     _health_summary(findings),
        "volumes":    _mounted_volumes(),
    })


@app.route("/api/health")
@limiter.exempt
def api_health():
    """
    Return the full list of health findings, refreshed on every call
    (up to once per _HEALTH_TTL seconds).  The frontend calls this once
    at startup and on-demand when the user opens the health panel.
    """
    force = request.args.get("force", "").lower() in ("1", "true", "yes")
    findings = _refresh_health_cache(force=force)
    return jsonify({
        "findings": findings,
        "summary":  _health_summary(findings),
    })


@app.route("/api/health/fix", methods=["POST"])
def api_health_fix():
    """Execute a health fix action by finding ID."""
    data = request.get_json(silent=True) or {}
    finding_id = data.get("id", "").strip()
    action = data.get("action", "").strip()
    if not finding_id:
        return jsonify({"error": "id is required"}), 400

    try:
        from health import run_health_checks  # noqa: PLC0415
        findings = run_health_checks()
        target = next((f for f in findings if f.id == finding_id), None)
        if not target:
            return jsonify({"error": f"Finding '{finding_id}' not found or already resolved"}), 404

        if action == "move_backup_dir":
            new_dir = data.get("path", "").strip()
            if not new_dir:
                return jsonify({"error": "path is required for move_backup_dir"}), 400
            from user_config import load_user_config, save_user_config  # noqa: PLC0415
            new_path = Path(new_dir)
            new_path.mkdir(parents=True, exist_ok=True)
            cfg = load_user_config()
            cfg["backup_dir"] = str(new_path)
            save_user_config(cfg)
            # Reload config module so BACKUP_DIR and friends pick up the new path
            import importlib, config as _config_mod  # noqa: PLC0415, E401
            importlib.reload(_config_mod)
            _refresh_health_cache(force=True)
            return jsonify({"ok": True, "message": f"Backup directory moved to {new_path}"})

        if action == "create_backup_dir" and target.auto_fixable and target.auto_fix_fn:
            target.auto_fix_fn()
            import importlib, config as _config_mod  # noqa: PLC0415, E401
            importlib.reload(_config_mod)
            _refresh_health_cache(force=True)
            return jsonify({"ok": True, "message": "Backup directory created"})

        if target.auto_fixable and target.auto_fix_fn:
            target.auto_fix_fn()
            _refresh_health_cache(force=True)
            return jsonify({"ok": True, "message": f"Fixed: {target.title}"})

        return jsonify({"error": "This finding cannot be auto-fixed"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/config")
def api_config():
    """Expose the configured default paths so the UI can pre-fill forms."""
    from helpers import _current_fablegear_mode, _backup_dir  # noqa: PLC0415
    try:
        from config import (  # noqa: PLC0415
            DJMT_DB, LOCAL_DB, MUSIC_ROOT, ARCHIVE_ROOT, SAVEPOINTS_DIR, QUARANTINE_DIR, REPORTS_DIR,
            BACKUP_DIR, ARCHIVE_ENABLED, _archive_mode, _custom_archive,
        )
        from user_config import load_user_config as _luc  # noqa: PLC0415
        _ucfg = _luc()
        current_mode = _current_fablegear_mode()
        return jsonify({
            "music_root":       str(MUSIC_ROOT),
            "local_db":         str(LOCAL_DB),
            "djmt_db":          str(DJMT_DB),
            "backup_dir":       str(BACKUP_DIR),
            "archive_root":     str(ARCHIVE_ROOT),
            "quarantine":       str(QUARANTINE_DIR),
            "reports":          str(REPORTS_DIR),
            "archive_mode":     _archive_mode,
            "custom_archive":   _custom_archive,
            "archive_enabled":  ARCHIVE_ENABLED,
            "excluded_dirs":    _ucfg.get("excluded_dirs", []),
            "mode":             current_mode,
            "configured":       True,
        })
    except Exception:
        current_mode = _current_fablegear_mode()
        return jsonify({
            "music_root":      "",
            "djmt_db":         "",
            "backup_dir":      str(_backup_dir()),
            "archive_root":    "",
            "quarantine":      "",
            "reports":         "",
            "archive_mode":    "auto",
            "custom_archive":  "",
            "archive_enabled": True,
            "mode":            current_mode,
            "configured":      False,
        })


@app.route("/api/state", methods=["POST"])
def api_state():
    """Return the steps_completed dict for a given library root."""
    data = request.get_json(force=True, silent=True) or {}
    library_root = data.get("library_root", "").strip()
    if not library_root:
        return jsonify({}), 200
    return jsonify(get_step_status(library_root))


@app.route("/api/setup-archive", methods=["POST"])
def api_setup_archive():
    """Create the FableGear Archive folder structure on the DJ drive."""
    try:
        from config import ensure_archive_structure  # noqa: PLC0415
        ensure_archive_structure()
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/settings", methods=["POST"])
def api_settings():
    """Save archive mode and custom path to user config."""
    try:
        from user_config import archive_root_for_music_root, load_user_config, CONFIG_PATH  # noqa: PLC0415
        import json as _json
        data = request.get_json(force=True) or {}
        cfg = load_user_config()
        raw_archive_mode = data.get("archive_mode")
        archive_mode = str(
            raw_archive_mode if raw_archive_mode not in (None, "") else cfg.get("archive_mode", "auto")
        ).strip() or "auto"
        if archive_mode not in {"auto", "custom", "none"}:
            return jsonify({"ok": False, "error": "Invalid archive mode. Must be one of: auto, custom, none"}), 400
        custom_archive_dir = str(data.get("custom_archive_dir", cfg.get("custom_archive_dir", ""))).strip()
        if archive_mode == "custom" and not custom_archive_dir:
            return jsonify({"ok": False, "error": "Custom archive path cannot be empty when archive_mode is custom"}), 400
        cfg["archive_mode"] = archive_mode
        cfg["custom_archive_dir"] = custom_archive_dir if archive_mode == "custom" else ""
        if archive_mode == "auto" and str(cfg.get("music_root", "")).strip():
            cfg["backup_dir"] = str(archive_root_for_music_root(cfg["music_root"]) / "Savepoints")
        elif archive_mode == "custom":
            cfg["backup_dir"] = str(Path(custom_archive_dir) / "Savepoints")
        else:
            cfg["backup_dir"] = str(cfg.get("backup_dir", "")).strip() or str(Path.home() / ".fablegear" / "backups")
        if "excluded_dirs" in data:
            cfg["excluded_dirs"] = [d for d in data["excluded_dirs"] if isinstance(d, str) and d.strip()]
        if "mode" in data and data["mode"] in ("rural", "suburban"):
            cfg["mode"] = data["mode"]
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            _json.dump(cfg, f, indent=2)
        return jsonify({"ok": True, "note": "Restart FableGear for changes to take effect."})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── Update routes ─────────────────────────────────────────────────────────────

@app.route("/api/update/status")
def api_update_status():
    """Return the cached GitHub release check result (never blocks)."""
    return jsonify(_update_get_status())


@app.route("/api/update/apply", methods=["POST"])
def api_update_apply():
    """
    Pull the latest release in-place, then relaunch FableGear.

    Flow:
      1. Refuse if a scan/subprocess is running or Rekordbox is open.
      2. Run ``git pull --ff-only`` in the repo root.
      3. On success, spawn a detached helper that waits for the port to free,
         then re-runs launch.sh. Finally SIGTERM self so the helper can bind.
      4. Frontend polls /api/update/status until it responds, then reloads.
    """
    active_pids = list_running_managed_subprocesses()
    if active_pids:
        return jsonify({
            "ok": False,
            "error": (
                "A scan is still running — cancel or finish it before updating. "
                f"Active PID(s): {', '.join(str(p) for p in active_pids)}"
            ),
        }), 409

    launch_sh = REPO_ROOT / "launch.sh"
    if not (REPO_ROOT / ".git").exists() or not launch_sh.exists():
        return jsonify({
            "ok": False,
            "error": "Not a git install — download the new release manually.",
        }), 400

    try:
        status_check = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if status_check.returncode == 0 and status_check.stdout.strip():
            return jsonify({
                "ok": False,
                "error": "Working tree has uncommitted changes — commit or stash them before updating.",
            }), 409

        pull = subprocess.run(
            ["git", "pull", "origin", "main", "--ff-only"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "git is not installed."}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "git pull timed out — check your connection."}), 504
    except Exception as exc:
        return jsonify({"ok": False, "error": f"git pull failed: {exc}"}), 500

    if pull.returncode != 0:
        err = (pull.stderr or pull.stdout or "").strip() or "git pull failed"
        return jsonify({"ok": False, "error": err}), 500

    # Requirements may have changed with the release — reinstall before the
    # relaunch (launch.sh no longer pulls or reinstalls on every open; this
    # permission-gated path is now the only updater). Non-fatal: a pip
    # hiccup shouldn't strand the user on a half-updated install.
    for req_file in ("requirements_ui.txt", "requirements.txt"):
        req_path = REPO_ROOT / req_file
        if not req_path.exists():
            continue
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade",
                 "--quiet", "-r", str(req_path)],
                cwd=str(REPO_ROOT),
                capture_output=True,
                timeout=600,
                check=False,
            )
        except Exception as exc:
            app.logger.warning("update: pip install for %s failed — %s", req_file, exc)

    def _relaunch() -> None:
        import time
        time.sleep(0.7)
        try:
            subprocess.Popen(
                ["bash", "-c", 'sleep 2 && exec bash "$0"', str(launch_sh)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
                cwd=str(REPO_ROOT),
            )
        finally:
            os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_relaunch, daemon=True).start()
    return jsonify({"ok": True, "output": pull.stdout.strip()})


# ── Homebrew routes ───────────────────────────────────────────────────────────

@app.route("/api/brew/status")
def api_brew_status():
    """Return the cached brew-outdated status (never blocks)."""
    return jsonify(_brew_get_status())


@app.route("/api/brew/check", methods=["POST"])
def api_brew_check():
    """Trigger an immediate brew-outdated check and return the result."""
    status = _brew_check_now()
    return jsonify(status)


@app.route("/api/run/brew-upgrade")
def api_brew_upgrade():
    """SSE stream of ``brew upgrade <packages>`` for known-outdated packages."""
    outdated = _brew_get_status().get("outdated", [])
    names = [p["name"] for p in outdated if p.get("name")]
    if not names:
        def _nothing():
            yield "data: No outdated FableGear packages found.\n\n"
            yield "data: [DONE]\n\n"
        return Response(
            _nothing(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    cmd = ["brew", "upgrade"] + names
    return _sse_response(cmd)


# ── Folder path resolution ────────────────────────────────────────────────────

@app.route("/api/finder-selection")
def api_finder_selection():
    """Return the path of the currently selected item in Finder (macOS only)."""
    source = request.args.get("source", "")

    if _SYSTEM == "Darwin":
        _finder_script = """\
tell application "Finder"
    set sel to selection
    if (count of sel) > 0 then
        return POSIX path of (item 1 of sel as alias)
    end if
end tell"""
        try:
            r = subprocess.run(
                ["osascript", "-e", _finder_script],
                capture_output=True, text=True, timeout=60,
            )
            app.logger.debug("[finder-selection] rc=%d stdout=%r stderr=%r",
                             r.returncode, r.stdout, r.stderr)
            if r.returncode == 0 and r.stdout.strip():
                return jsonify({"path": r.stdout.strip().rstrip("/")})
        except Exception as exc:
            app.logger.debug("[finder-selection] exception: %s", exc)

        if source == "drop":
            app.logger.debug("[finder-selection] source=drop, returning null")
            return jsonify({"path": None})

        try:
            r = subprocess.run(
                ["osascript", "-e", "POSIX path of (choose folder)"],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0:
                return jsonify({"path": r.stdout.strip().rstrip("/")})
        except Exception:
            pass

    return jsonify({"path": None})


@app.route("/api/pick-folder")
def api_pick_folder():
    """Open the native folder-chooser dialog. macOS uses osascript; other platforms
    rely on pywebview's js_api.pick_folder() called directly from the frontend."""
    if _SYSTEM == "Darwin":
        try:
            result = subprocess.run(
                ["osascript", "-e", "POSIX path of (choose folder)"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                return jsonify({"path": result.stdout.strip().rstrip("/")})
        except Exception:
            pass
    return jsonify({"path": None})


# ── Volume helpers ─────────────────────────────────────────────────────────────

_AUDIO_BEARING_SKIP = frozenset({"Macintosh HD", "Recovery", "VM", "Preboot", "Update", "Data"})


def _is_user_mount(mountpoint: str) -> bool:
    """Return True if this partition is an external/user-accessible drive on any platform."""
    if _SYSTEM == "Darwin":
        return mountpoint.startswith("/Volumes/")
    if _SYSTEM == "Windows":
        # Drive roots: C:\, D:\, etc.  Skip legacy floppy A/B.
        return (len(mountpoint) == 3 and mountpoint[1] == ":"
                and mountpoint[2] in ("/", "\\")
                and mountpoint[0].upper() not in ("A", "B"))
    # Linux: /media or /mnt
    return mountpoint.startswith("/media/") or mountpoint.startswith("/mnt/")


def _drive_name(mountpoint: str) -> str:
    if _SYSTEM == "Windows":
        return mountpoint.rstrip("/\\")   # "C:", "D:", …
    return Path(mountpoint).name


def _is_browseable_path(p: Path) -> bool:
    """Security check: only allow paths the user legitimately owns."""
    s = str(p)
    home = str(Path.home())
    if _SYSTEM == "Darwin":
        return s.startswith("/Volumes/") or s.startswith(home)
    if _SYSTEM == "Windows":
        return len(s) >= 3 and s[1] == ":" and s[2] in ("/", "\\")
    return s.startswith("/media/") or s.startswith("/mnt/") or s.startswith(home)


def _mounted_volumes() -> list:
    """Return info about user-accessible mounts. Platform-aware."""
    vols = []
    try:
        from config import MUSIC_ROOT as _MR  # noqa: PLC0415
        music_root_str = str(_MR)
        for part in psutil.disk_partitions(all=False):
            mp = part.mountpoint
            if not _is_user_mount(mp):
                continue
            name = _drive_name(mp)
            if name in _AUDIO_BEARING_SKIP:
                continue
            try:
                usage = psutil.disk_usage(mp)
                free_gb = round(usage.free / 1e9, 1)
                total_gb = round(usage.total / 1e9, 1)
            except Exception:
                free_gb = total_gb = None
            pioneer_db = Path(mp) / "PIONEER" / "Master" / "master.db"
            vols.append({
                "name":           name,
                "mountpoint":     mp,
                "fstype":         part.fstype,
                "free_gb":        free_gb,
                "total_gb":       total_gb,
                "has_pioneer_db": pioneer_db.exists(),
                "is_music_root":  music_root_str.startswith(mp),
                "is_read_only":   "ro" in {o.strip() for o in (part.opts or "").split(",")},
            })
    except Exception:
        pass
    return vols


@app.route("/api/fs/stream")
def api_fs_stream():
    """Stream an audio file by absolute path (filesystem mode — no rekordbox required).
    Security: path must resolve under a trusted root as determined by _is_browseable_path()
    (external-volume roots or the user's home directory).
    """
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"error": "Forbidden"}), 403
    path_str = request.args.get("path", "")
    if not path_str:
        return jsonify({"error": "path required"}), 400
    try:
        p = Path(path_str).resolve()
    except Exception:
        return jsonify({"error": "Invalid path"}), 400
    if not _is_browseable_path(p):
        return jsonify({"error": "Forbidden"}), 403
    if not p.exists() or not p.is_file():
        return jsonify({"error": "File not found"}), 404
    AUDIO_EXTS = {".aiff", ".aif", ".wav", ".flac", ".mp3", ".m4a", ".m4p", ".ogg", ".opus", ".alac"}
    if p.suffix.lower() not in AUDIO_EXTS:
        return jsonify({"error": "Not an audio file"}), 400
    mime, _ = mimetypes.guess_type(str(p))
    return send_file(str(p), mimetype=mime or "audio/mpeg", conditional=True)


@app.route("/api/fs/list")
def api_fs_list():
    """Lightweight directory listing for the in-app file browser panel."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"error": "Forbidden"}), 403
    AUDIO_EXTS = {
        ".aiff", ".aif", ".aifc", ".wav", ".flac", ".mp3",
        ".m4a", ".m4p", ".mp4", ".m4v", ".alac", ".ogg", ".opus",
    }
    if _SYSTEM == "Windows":
        default_root = "C:\\"
    elif _SYSTEM == "Darwin":
        default_root = "/Volumes"
    else:
        default_root = "/media"
    audio_only = request.args.get("audio_only", "0") == "1"
    path_str = (request.args.get("path") or "").strip() or default_root
    try:
        p = Path(path_str).resolve()
    except Exception:
        return jsonify({"error": "Invalid path"}), 400
    if not _is_browseable_path(p):
        return jsonify({"error": "Forbidden"}), 403
    if not p.exists() or not p.is_dir():
        return jsonify({"error": f"Not a directory: {path_str}"}), 400

    def _dir_has_audio(d: Path, depth: int = 3) -> bool:
        if depth <= 0:
            return False
        try:
            for child in d.iterdir():
                if child.name.startswith("."):
                    continue
                if child.is_file() and child.suffix.lower() in AUDIO_EXTS:
                    return True
                if child.is_dir() and _dir_has_audio(child, depth - 1):
                    return True
        except (PermissionError, OSError):
            pass
        return False

    try:
        entries = []
        for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if item.name.startswith("."):
                continue
            is_dir = item.is_dir()
            is_audio = not is_dir and item.suffix.lower() in AUDIO_EXTS
            if audio_only:
                if is_dir and not _dir_has_audio(item):
                    continue
                if not is_dir and not is_audio:
                    continue
            entries.append({
                "name":     item.name,
                "path":     str(item),
                "is_dir":   is_dir,
                "is_audio": is_audio,
            })
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403
    return jsonify({
        "path":    str(p),
        "parent":  str(p.parent) if str(p) != str(p.parent) else None,
        "entries": entries,
    })


# ── Staging queue ─────────────────────────────────────────────────────────────

@app.route("/api/staging", methods=["GET"])
def api_staging_get():
    from staging import get_items  # noqa: PLC0415
    return jsonify(get_items())


@app.route("/api/staging/add", methods=["POST"])
def api_staging_add():
    from staging import add_items  # noqa: PLC0415
    data = request.get_json(silent=True) or {}
    paths = data.get("paths", [])
    if not isinstance(paths, list):
        return jsonify({"error": "paths must be a list"}), 400
    return jsonify(add_items(paths))


@app.route("/api/staging/remove", methods=["POST"])
def api_staging_remove():
    from staging import remove_item  # noqa: PLC0415
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    if not path:
        return jsonify({"error": "path required"}), 400
    return jsonify(remove_item(path))


@app.route("/api/staging/clear", methods=["POST"])
def api_staging_clear():
    from staging import clear_items  # noqa: PLC0415
    return jsonify(clear_items())


@app.route("/api/staging/batch", methods=["GET"])
def api_staging_batch_list():
    from staging import list_batches  # noqa: PLC0415
    return jsonify(list_batches())


@app.route("/api/staging/batch/save", methods=["POST"])
def api_staging_batch_save():
    from staging import save_batch  # noqa: PLC0415
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    return jsonify(save_batch(name))


@app.route("/api/staging/batch/load", methods=["POST"])
def api_staging_batch_load():
    from staging import load_batch  # noqa: PLC0415
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    return jsonify(load_batch(name))


@app.route("/api/staging/batch/delete", methods=["POST"])
def api_staging_batch_delete():
    from staging import delete_batch  # noqa: PLC0415
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    return jsonify(delete_batch(name))


# ── Setup / state persistence ─────────────────────────────────────────────────

_FABLEGEAR_STATE = Path.home() / ".fablegear" / "fablegear-state.json"

_SETUP_STATE_DEFAULTS = {
    "setup_complete": False,
    "db_read": None,
    "db_write": None,
    "drive_scan": False,
}


def _normalize_setup_state(raw: dict | None) -> dict:
    state = dict(_SETUP_STATE_DEFAULTS)
    if not isinstance(raw, dict):
        return state

    state["setup_complete"] = bool(raw.get("setup_complete", False))
    state["drive_scan"] = bool(raw.get("drive_scan", False))

    for key in ("db_read", "db_write"):
        value = raw.get(key)
        state[key] = value if isinstance(value, bool) or value is None else None

    return state


def _load_setup_state(*, repair: bool = True) -> dict:
    raw = None
    should_write = False

    try:
        if _FABLEGEAR_STATE.exists():
            raw = json.loads(_FABLEGEAR_STATE.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                should_write = True
        else:
            raw = {}
            should_write = True
    except Exception:
        raw = {}
        should_write = True

    state = _normalize_setup_state(raw)

    if not should_write and isinstance(raw, dict) and raw != state:
        should_write = True

    if repair and should_write:
        try:
            _FABLEGEAR_STATE.parent.mkdir(parents=True, exist_ok=True)
            _FABLEGEAR_STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass

    return state


@app.route("/api/setup-status")
def api_setup_status():
    """Return whether the welcome wizard has been completed."""
    state = _load_setup_state(repair=True)
    return jsonify({
        "setup_complete": bool(state.get("setup_complete")),
        "db_read":        state.get("db_read"),
        "db_write":       state.get("db_write"),
    })


@app.route("/api/config/set-music-root", methods=["POST"])
def api_set_music_root():
    """Update music_root in ~/.fablegear/config.json."""
    try:
        from user_config import load_user_config, save_user_config  # noqa: PLC0415
        data = request.get_json(silent=True) or {}
        path = str(data.get("path", "")).strip()
        if not path:
            return jsonify({"error": "path is required"}), 400
        cfg = load_user_config()
        cfg["music_root"] = path
        save_user_config(cfg)
        return jsonify({"ok": True, "music_root": path})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/drives/autodetect")
def api_drives_autodetect():
    """
    Scan mounted drives for Pioneer DB files and music library roots.
    Returns candidate paths for device_db and music_root so the user
    can confirm and apply them with one click via /api/drives/apply-fix.
    Works on macOS, Windows, and Linux.
    """
    from user_config import discover_music_roots  # noqa: PLC0415

    device_db_candidates: list[str] = []

    mounts = _mounted_volumes()
    if not mounts:
        return jsonify({"device_db": [], "music_root": [], "music_root_details": [], "recommended_music_root": ""})

    for mount in mounts:
        vol = Path(mount["mountpoint"])
        if not vol.is_dir():
            continue
        # Pioneer device DB
        candidate_db = vol / "PIONEER" / "Master" / "master.db"
        if candidate_db.exists():
            device_db_candidates.append(str(candidate_db))
    music_root_details = discover_music_roots([Path(m["mountpoint"]) for m in mounts if Path(m["mountpoint"]).is_dir()])
    music_root_candidates = [item["path"] for item in music_root_details]

    return jsonify({
        "device_db":   device_db_candidates,
        "music_root":  music_root_candidates,
        "music_root_details": music_root_details,
        "recommended_music_root": music_root_details[0]["path"] if music_root_details else "",
    })


@app.route("/api/drives/apply-fix", methods=["POST"])
def api_drives_apply_fix():
    """
    Patch config.json with corrected drive paths.
    Accepts any subset of: local_db, device_db, music_root.
    Safe to call with partial updates — only provided keys are changed.
    """
    try:
        from user_config import CONFIG_FILE, REQUIRED_KEYS, DEFAULTS  # noqa: PLC0415
        import json as _json  # noqa: PLC0415

        data = request.get_json(silent=True) or {}
        patchable = {"local_db", "device_db", "music_root", "backup_dir"}
        patch = {k: str(v).strip() for k, v in data.items()
                 if k in patchable and str(v).strip()}
        if not patch:
            return jsonify({"error": "No valid keys provided"}), 400

        # Load existing config if present, else start from empty dict
        existing: dict = {}
        if CONFIG_FILE.exists():
            try:
                existing = _json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

        existing.update(patch)

        # Validate all required keys are now present
        missing = [k for k in REQUIRED_KEYS if not existing.get(k)]
        if missing:
            return jsonify({
                "ok": False,
                "error": f"Still missing required keys: {', '.join(missing)}",
                "missing": missing,
            }), 400

        # Apply defaults for optional keys
        for key, default in DEFAULTS.items():
            if key not in existing:
                existing[key] = default

        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            _json.dumps(existing, indent=2) + "\n", encoding="utf-8"
        )
        return jsonify({"ok": True, "patched": list(patch.keys())})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/drives/first-aid", methods=["POST"])
def api_drives_first_aid():
    """Open Disk Utility for a mounted drive so the user can run First Aid."""
    if _SYSTEM != "Darwin":
        return jsonify({
            "ok": False,
            "error": "Disk Utility First Aid is only available on macOS.",
        }), 400

    data = request.get_json(silent=True) or {}
    mountpoint = str(data.get("mountpoint", "")).strip()
    if not mountpoint:
        return jsonify({"ok": False, "error": "mountpoint is required"}), 400

    try:
        requested_mount = str(Path(mountpoint).resolve())
    except Exception:
        return jsonify({"ok": False, "error": "Invalid mountpoint"}), 400

    allowed_mounts = {str(Path(v.get("mountpoint", "")).resolve()) for v in _mounted_volumes() if v.get("mountpoint")}
    if requested_mount not in allowed_mounts:
        return jsonify({"ok": False, "error": "Unknown mounted drive"}), 400

    try:
        subprocess.Popen(["open", "-a", "Disk Utility", requested_mount])
        return jsonify({
            "ok": True,
            "message": "Disk Utility opened. Run First Aid on the drive before retrying write access.",
        })
    except Exception:
        app.logger.exception("Could not open Disk Utility for %s", requested_mount)
        return jsonify({"ok": False, "error": "Could not open Disk Utility"}), 500


# ── First-run onboarding ──────────────────────────────────────────────────────

@app.route("/onboarding")
def onboarding():
    """Serve the first-run setup wizard."""
    from flask import redirect as _redirect  # noqa: PLC0415
    from user_config import config_exists   # noqa: PLC0415
    try:
        if config_exists():
            state = _load_setup_state(repair=True)
            # `?reconfigure=1` lets a completed user re-enter the wizard (e.g. to
            # change permissions or paths); otherwise a finished setup bounces home.
            if state.get("setup_complete") and not request.args.get("reconfigure"):
                return _redirect("/")
    except Exception:
        pass
    return render_template("onboarding.html")


@app.route("/api/onboarding/dep-check")
def api_onboarding_dep_check():
    """Return dependency check results for the onboarding wizard."""
    from user_config import check_dependencies  # noqa: PLC0415
    deps = check_dependencies()
    return jsonify({
        "deps": deps,
        "all_ok": all(d["ok"] for d in deps),
    })


@app.route("/api/onboarding/install-deps", methods=["POST"])
def api_onboarding_install_deps():
    """Open a Terminal window running setup.sh to install system dependencies."""
    setup_sh = REPO_ROOT / "setup.sh"
    if not setup_sh.exists():
        return jsonify({"error": "setup.sh not found"}), 404
    try:
        subprocess.Popen(["open", "-a", "Terminal", str(setup_sh)])
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _pin_to_dock(app_path: str) -> None:
    """Add *app_path* to the macOS Dock persistent-apps list."""
    import plistlib  # noqa: PLC0415
    dock_plist = Path.home() / "Library" / "Preferences" / "com.apple.dock.plist"
    try:
        with open(dock_plist, "rb") as _f:
            dock = plistlib.load(_f)
        for item in dock.get("persistent-apps", []):
            url = item.get("tile-data", {}).get("file-data", {}).get("_CFURLString", "")
            if url == app_path:
                subprocess.run(["killall", "Dock"], capture_output=True, check=False)
                return
        dock.setdefault("persistent-apps", []).append({
            "tile-data": {
                "file-data": {
                    "_CFURLString": app_path,
                    "_CFURLStringType": 0,
                },
                "file-label": "FableGear",
            },
            "tile-type": "file-tile",
        })
        with open(dock_plist, "wb") as _f:
            plistlib.dump(dock, _f)
        subprocess.run(["killall", "Dock"], capture_output=True, check=False)
    except Exception:
        pass  # Non-fatal — Dock pinning is cosmetic


@app.route("/api/onboarding/install-app", methods=["POST"])
def api_onboarding_install_app():
    """Build FableGear.app in ~/Applications using osacompile and optionally pin to Dock."""
    import tempfile  # noqa: PLC0415
    import shutil   # noqa: PLC0415

    data = request.get_json(silent=True) or {}
    add_to_dock = bool(data.get("dock", True))
    force = bool(data.get("force", False))

    install_dir = Path.home() / "Applications"
    app_path = install_dir / "FableGear.app"
    launch_sh = REPO_ROOT / "launch.sh"
    icon_src = REPO_ROOT / "static" / "icon-app-dock.png"

    install_dir.mkdir(parents=True, exist_ok=True)

    # setup.sh already builds FableGear.app during dependency install. If it is
    # present, don't rebuild it with a different mechanism — just pin to Dock if
    # requested. A `force` flag allows an explicit rebuild when needed.
    if app_path.exists() and not force:
        if add_to_dock:
            _pin_to_dock(str(app_path))
        return jsonify({
            "ok": True, "path": str(app_path), "existed": True, "rebuilt": False,
        })

    # Compile a fresh .app pointing at this install's launch.sh
    script_content = f'do shell script "bash \'{launch_sh}\' > /dev/null 2>&1 &"'
    tmp_as = Path(tempfile.mktemp(suffix=".applescript"))
    try:
        tmp_as.write_text(script_content, encoding="utf-8")
        result = subprocess.run(
            ["osacompile", "-o", str(app_path), str(tmp_as)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip() or "osacompile failed"}), 500
    finally:
        tmp_as.unlink(missing_ok=True)

    # Apply icon (non-fatal if sips/iconutil unavailable)
    if icon_src.exists():
        try:
            iconset_dir = Path(tempfile.mkdtemp()) / "fg.iconset"
            iconset_dir.mkdir()
            for size in (16, 32, 64, 128, 256, 512):
                subprocess.run(
                    ["sips", "-z", str(size), str(size), str(icon_src),
                     "--out", str(iconset_dir / f"icon_{size}x{size}.png")],
                    capture_output=True, check=False,
                )
                double = size * 2
                subprocess.run(
                    ["sips", "-z", str(double), str(double), str(icon_src),
                     "--out", str(iconset_dir / f"icon_{size}x{size}@2x.png")],
                    capture_output=True, check=False,
                )
            icns_out = app_path / "Contents" / "Resources" / "applet.icns"
            subprocess.run(
                ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_out)],
                capture_output=True, check=False,
            )
            shutil.rmtree(str(iconset_dir.parent), ignore_errors=True)
            # The applet template ships an Assets.car whose compiled AppIcon
            # outranks applet.icns — remove it so the custom icon wins.
            (app_path / "Contents" / "Resources" / "Assets.car").unlink(missing_ok=True)
            app_path.touch()
        except Exception:
            pass

    if add_to_dock:
        _pin_to_dock(str(app_path))

    return jsonify({"ok": True, "path": str(app_path), "existed": False, "rebuilt": True})


@app.route("/api/onboarding/scan-library")
def api_onboarding_scan_library():
    """Scan local machine and mounted volumes for Rekordbox assets."""
    from user_config import scan_for_rekordbox_assets  # noqa: PLC0415
    return jsonify(scan_for_rekordbox_assets())


@app.route("/api/onboarding/check-fda")
def api_onboarding_check_fda():
    """Check if the local Rekordbox DB is readable (Full Disk Access indicator)."""
    local_db = Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"
    can_read = False
    if local_db.exists():
        try:
            with open(local_db, "rb") as _f:
                _f.read(16)
            can_read = True
        except (PermissionError, OSError):
            pass
    return jsonify({
        "can_read": can_read,
        "db_path": str(local_db),
        "db_exists": local_db.exists(),
    })


@app.route("/api/onboarding/open-fda-prefs", methods=["POST"])
def api_onboarding_open_fda_prefs():
    """Open System Preferences > Privacy & Security > Full Disk Access."""
    try:
        subprocess.Popen(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"]
        )
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/onboarding/save-config", methods=["POST"])
def api_onboarding_save_config():
    """Save confirmed paths to config.json and mark setup complete."""
    from user_config import DEFAULTS, archive_root_for_music_root, save_user_config  # noqa: PLC0415

    data = request.get_json(silent=True) or {}
    required = {"local_db", "device_db", "music_root"}
    missing = [k for k in required if not str(data.get(k, "")).strip()]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    archive_mode = str(data.get("archive_mode", "auto")).strip() or "auto"
    if archive_mode not in {"auto", "custom", "none"}:
        return jsonify({"error": "Invalid archive mode. Must be one of: auto, custom, none"}), 400
    custom_archive_dir = str(data.get("custom_archive_dir", "")).strip()
    if archive_mode == "custom" and not custom_archive_dir:
        return jsonify({"error": "Custom archive path cannot be empty when archive_mode is custom"}), 400
    backup_dir = str(data.get("backup_dir", "")).strip()
    if not backup_dir:
        if archive_mode == "custom":
            backup_dir = str(Path(custom_archive_dir) / "Savepoints")
        elif archive_mode == "auto":
            backup_dir = str(archive_root_for_music_root(str(data["music_root"]).strip()) / "Savepoints")
        else:
            backup_dir = str(Path.home() / ".fablegear" / "backups")

    cfg: dict = {
        "local_db":   str(data["local_db"]).strip(),
        "device_db":  str(data["device_db"]).strip(),
        "music_root": str(data["music_root"]).strip(),
        "backup_dir": backup_dir,
        "archive_mode": archive_mode,
        "custom_archive_dir": custom_archive_dir if archive_mode == "custom" else "",
    }
    for key, default in DEFAULTS.items():
        cfg.setdefault(key, default)

    try:
        save_user_config(cfg)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    state = {
        "setup_complete": True,
        "db_read":  bool(data.get("db_read", True)),
        "db_write": bool(data.get("db_write", True)),
        # Consent to scan connected drives/volumes for music-specific formats,
        # granted (or declined) during onboarding — the only place that asks.
        "drive_scan": bool(data.get("drive_scan", False)),
    }
    _FABLEGEAR_STATE.parent.mkdir(parents=True, exist_ok=True)
    _FABLEGEAR_STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return jsonify({"ok": True})


@app.route("/api/quit", methods=["POST"])
def api_quit():
    """Shut the server down cleanly after sending the response."""
    def _shutdown():
        import time
        time.sleep(0.2)
        try:
            terminate_managed_subprocesses(force=False, include_orphans=True)
            time.sleep(0.2)
            terminate_managed_subprocesses(force=True, include_orphans=True)
        finally:
            os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=_shutdown, daemon=True).start()
    return jsonify({"ok": True})


# ── MCP (AI agent access) routes ─────────────────────────────────────────────

@app.route("/api/mcp/status")
def api_mcp_status():
    """Return current MCP server status and config."""
    from user_config import load_user_config, MCP_PORT_DEFAULT  # noqa: PLC0415
    try:
        cfg = load_user_config()
    except Exception:
        cfg = {}

    try:
        from mcp_server import get_embedded_status  # noqa: PLC0415
        status = get_embedded_status()
    except Exception:
        status = {"running": False, "host": None, "port": None, "dev_mode": False, "url": None}

    status["enabled"] = cfg.get("mcp_enabled", False)
    status["autostart"] = cfg.get("mcp_autostart", False)
    status["expose"] = cfg.get("mcp_expose", False)
    status["configured_port"] = cfg.get("mcp_port", MCP_PORT_DEFAULT)
    return jsonify(status)


@app.route("/api/mcp/start", methods=["POST"])
def api_mcp_start():
    """Start the embedded MCP server."""
    from user_config import load_user_config, enable_mcp, save_user_config, find_available_mcp_port  # noqa: PLC0415
    from mcp_server import start_embedded, is_running  # noqa: PLC0415

    if is_running():
        return jsonify({"ok": True, "message": "Already running"})

    try:
        cfg = load_user_config()
    except Exception:
        cfg = {}

    if not cfg.get("mcp_enabled"):
        cfg = enable_mcp(cfg)
        save_user_config(cfg)

    port = find_available_mcp_port(cfg.get("mcp_port", 5002))
    host = "0.0.0.0" if cfg.get("mcp_expose") else "127.0.0.1"
    token = cfg.get("mcp_token", "")

    if cfg.get("mcp_port") != port:
        cfg["mcp_port"] = port
        save_user_config(cfg)

    ok = start_embedded(host=host, port=port, token=token)
    if ok:
        return jsonify({"ok": True, "port": port, "host": host})
    return jsonify({"ok": False, "error": "MCP server failed to start"}), 500


@app.route("/api/mcp/stop", methods=["POST"])
def api_mcp_stop():
    """Stop the embedded MCP server."""
    from mcp_server import stop_embedded  # noqa: PLC0415
    was_running = stop_embedded()
    return jsonify({"ok": True, "was_running": was_running})


@app.route("/api/mcp/enable", methods=["POST"])
def api_mcp_enable():
    """Enable MCP and configure it. Body: {autostart?, expose?}"""
    from user_config import load_user_config, enable_mcp, save_user_config  # noqa: PLC0415
    try:
        cfg = load_user_config()
    except Exception:
        cfg = {}

    data = request.get_json(silent=True) or {}
    cfg = enable_mcp(
        cfg,
        autostart=bool(data.get("autostart", False)),
        expose=bool(data.get("expose", False)),
    )
    save_user_config(cfg)
    return jsonify({
        "ok": True,
        "mcp_enabled": True,
        "mcp_port": cfg["mcp_port"],
        "mcp_autostart": cfg["mcp_autostart"],
        "mcp_token": cfg["mcp_token"],
    })


@app.route("/api/mcp/disable", methods=["POST"])
def api_mcp_disable():
    """Disable MCP. Stops the server if running."""
    from user_config import load_user_config, save_user_config  # noqa: PLC0415
    from mcp_server import stop_embedded  # noqa: PLC0415

    stop_embedded()
    try:
        cfg = load_user_config()
        cfg["mcp_enabled"] = False
        cfg["mcp_autostart"] = False
        save_user_config(cfg)
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/api/mcp/config-snippet")
def api_mcp_config_snippet():
    """Return a ready-to-paste config snippet for the given client.
    Query param: ?client=claude-desktop|claude-code|cursor|generic
    """
    from user_config import load_user_config, mcp_config_snippet  # noqa: PLC0415
    client = request.args.get("client", "generic")
    try:
        cfg = load_user_config()
    except Exception:
        return jsonify({"error": "FableGear not configured"}), 500
    snippet = mcp_config_snippet(client, cfg)
    return jsonify({"client": client, "snippet": snippet})


# ── After-request headers ─────────────────────────────────────────────────────

@app.after_request
def disable_cache_on_static_files(response):
    """Disable caching for static files; add CSP for defense-in-depth."""
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self' ws://localhost:* wss://localhost:*; "
        "font-src 'self' data:; "
        "object-src 'none'; "
        "base-uri 'self';"
    )

    return response


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print()
    print("  ┌──────────────────────────────────┐")
    print("  │  FableGear · http://localhost:5001  │")
    print("  └──────────────────────────────────┘")
    print()
    app.run(host="127.0.0.1", port=5001, debug=False)
