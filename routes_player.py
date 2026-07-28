"""
routes_player.py — ── The Media Pit ──

Flask Blueprint: library, playlist, tracks, audio serving, and playback.
Handles all read/write operations on the Rekordbox library tree, plus
in-process audio playback for the desktop UI.
"""

from pathlib import Path
import os
import platform
import mimetypes
import threading
import time
import uuid

_SYSTEM = platform.system()

def _is_user_mount(mountpoint: str) -> bool:
    if _SYSTEM == "Darwin":
        return mountpoint.startswith("/Volumes/") or mountpoint.startswith("/Volumes")
    if _SYSTEM == "Windows":
        return (len(mountpoint) == 3 and mountpoint[1] == ":"
                and mountpoint[2] in ("/", "\\")
                and mountpoint[0].upper() not in ("A", "B"))
    return mountpoint.startswith("/media/") or mountpoint.startswith("/mnt/")

from flask import Blueprint, jsonify, request, send_file

from helpers import (
    _EXPORT_JOBS,
    _EXPORT_LOCK,
    _MAX_EXPORT_JOBS,
    _detect_pioneer_drive_layout,
    _evict_old_jobs,
    _run_export,
    get_connected_volumes,
)

bp = Blueprint("player", __name__)

# ── connected helpers ───────────────────────────────────────────────────

def get_connected_volumes() -> list[dict]:
    """Return list of user-mountable volumes with name/path.
    Used by filesystem scan and export routes.
    """
    import psutil  # noqa: PLC0415

    volumes = []
    try:
        for part in psutil.disk_partitions():
            mountpoint = part.mountpoint
            if not _is_user_mount(mountpoint):
                continue
            name = mountpoint.rstrip("/\\") if _SYSTEM == "Windows" else Path(mountpoint).name
            volumes.append({
                "name": name or mountpoint,
                "path": mountpoint,
            })
    except Exception:
        pass  # Graceful fallback for any psutil issues
    return volumes

# ── Library payload helpers ───────────────────────────────────────────────────

def _library_track_payload(track, *, track_no=None):
    import datetime  # noqa: PLC0415

    date_added = None
    stock_date = getattr(track, "StockDate", None)
    if stock_date and isinstance(stock_date, (datetime.date, datetime.datetime)):
        try:
            date_added = stock_date.isoformat()
        except Exception:
            date_added = None

    raw_rating = int(getattr(track, "Rating", 0) or 0)
    stars = 0 if raw_rating == 0 else max(1, min(5, round(raw_rating / 51)))

    color_id = int(getattr(track, "ColorID", 0) or 0)

    genre_name = ""
    try:
        genre_name = track.Genre.Name if track.Genre else ""
    except Exception:
        pass

    label_name = ""
    try:
        label_name = track.Label.Name if track.Label else ""
    except Exception:
        pass

    comment = str(getattr(track, "Comment", "") or "").strip()
    play_count = int(getattr(track, "DJPlayCount", 0) or 0)

    return {
        "id":         str(track.ID),
        "title":      track.Title or "",
        "artist":     track.Artist.Name if track.Artist else "",
        "album":      track.Album.Name if getattr(track, "Album", None) else "",
        "genre":      genre_name,
        "label":      label_name,
        "bpm":        round(track.BPM / 100, 1) if track.BPM else None,
        "key":        track.Key.Name if track.Key else None,
        "key_id":     int(track.KeyID) if track.KeyID else None,
        "duration":   track.Length if track.Length else None,
        "date_added": date_added,
        "file_path":  track.FolderPath or "",
        "rating":     stars,
        "color":      color_id,
        "play_count": play_count,
        "comment":    comment,
        "track_no":   track_no,
    }


def _norm_text(value):
    return " ".join(str(value or "").strip().lower().split())


def _track_identity_signature(track):
    """Logical identity used to avoid duplicate playlist entries across alt content rows."""
    title = _norm_text(getattr(track, "Title", ""))
    artist_name = ""
    try:
        artist_name = _norm_text(track.Artist.Name if track.Artist else "")
    except Exception:
        artist_name = ""

    duration = int(getattr(track, "Length", 0) or 0)
    file_name = _norm_text(Path(str(getattr(track, "FolderPath", "") or "")).name)
    return (artist_name, title, duration, file_name)


def _playlist_tree_payload(db):
    rows = db.get_playlist().all()
    songs_by_playlist = {}
    nodes_by_id = {}
    roots = []

    for song in db.get_playlist_songs().all():
        playlist_id = str(song.PlaylistID)
        songs_by_playlist[playlist_id] = songs_by_playlist.get(playlist_id, 0) + 1

    for playlist in rows:
        attribute = int(getattr(playlist, "Attribute", 0) or 0)
        node = {
            "id": str(playlist.ID),
            "name": playlist.Name or "",
            "type": "folder" if attribute == 1 else "playlist",
            "track_count": songs_by_playlist.get(str(playlist.ID), 0),
            "children": [],
            "parent_id": str(getattr(playlist, "ParentID", "") or ""),
            "seq": int(getattr(playlist, "Seq", 0) or 0),
        }
        nodes_by_id[node["id"]] = node

    def _sort_key(node):
        return (node.get("seq", 0), node["name"].lower())

    for node in nodes_by_id.values():
        parent_id = node.pop("parent_id")
        if parent_id and parent_id in nodes_by_id:
            nodes_by_id[parent_id]["children"].append(node)
        else:
            roots.append(node)

    def _finalize(nodes):
        ordered = sorted(nodes, key=_sort_key)
        for node in ordered:
            node["children"] = _finalize(node["children"])
            node.pop("seq", None)
        return ordered

    return _finalize(roots)


# ── Filesystem track helper ───────────────────────────────────────────────────

_FS_AUDIO_EXTS = frozenset({
    ".aiff", ".aif", ".aifc", ".wav", ".flac", ".mp3",
    ".m4a", ".m4p",
})
_FS_TAG_LIMIT = 500   # stop reading mutagen beyond this many tracks in one folder
_FS_RECURSIVE_LIMIT = 5000  # hard cap for recursive scans
_FS_DRIVE_SCAN_TIMEOUT = 15.0  # keep whole-drive novelty scans responsive


def _fs_track_payload(path: Path) -> dict:
    """Minimal metadata for a filesystem audio file (no rekordbox required).
    Reads ID3/Vorbis/FLAC tags via mutagen (fast for local files).
    Falls back to filename-only on any read error.
    """
    payload: dict = {
        "source":      "filesystem",
        "path":        str(path),
        "filename":    path.name,
        "title":       path.stem,
        "artist":      "",
        "album":       "",
        "genre":       "",
        "bpm":         None,
        "key":         None,
        "duration_s":  None,
    }
    try:
        import mutagen  # noqa: PLC0415
        f = mutagen.File(str(path), easy=True)
        if f:
            payload["title"]  = (f.get("title")  or [path.stem])[0]
            payload["artist"] = (f.get("artist") or [""])[0]
            payload["album"]  = (f.get("album")  or [""])[0]
            payload["genre"]  = (f.get("genre")  or [""])[0]
            # BPM: easy=True maps TBPM→"bpm" for MP3, and vorbis/flac use "bpm" directly
            raw_bpm = (f.get("bpm") or [""])[0]
            if raw_bpm:
                try:
                    payload["bpm"] = str(int(round(float(raw_bpm))))
                except (ValueError, TypeError):
                    payload["bpm"] = raw_bpm
            # Key: easy=True maps TKEY→"initialkey"
            raw_key = (f.get("initialkey") or [""])[0]
            if raw_key:
                payload["key"] = raw_key
            if hasattr(f, "info") and hasattr(f.info, "length"):
                payload["duration_s"] = round(f.info.length)
    except Exception:
        pass
    return payload


def _real_path_str(path: Path) -> str | None:
    try:
        return os.path.realpath(str(path))
    except OSError:
        return None


def _is_primary_os_drive(path: Path) -> bool:
    real_path = _real_path_str(path)
    if not real_path:
        return False
    if _SYSTEM == "Windows":
        return Path(real_path).drive.upper() == "C:"
    return real_path == os.path.realpath("/")


def _collect_audio_files(
    root: Path,
    *,
    limit: int,
    deadline: float | None = None,
) -> tuple[list[Path], bool]:
    """Collect visible audio files without following whole-disk aliases forever."""
    files: list[Path] = []
    truncated = False
    try:
        for walk_root, dirs, walk_files in os.walk(root, followlinks=False):
            if deadline is not None and time.monotonic() >= deadline:
                truncated = True
                break
            dirs[:] = sorted((d for d in dirs if not d.startswith(".")), key=str.lower)
            for name in sorted(walk_files, key=str.lower):
                if name.startswith("."):
                    continue
                if Path(name).suffix.lower() not in _FS_AUDIO_EXTS:
                    continue
                files.append(Path(walk_root) / name)
                if len(files) >= limit:
                    truncated = True
                    break
            if truncated:
                break
    except (PermissionError, OSError):
        return files, truncated
    return files, truncated


def _enumerate_drive_audio(
    limit: int = _FS_RECURSIVE_LIMIT,
    *,
    per_volume_limit: int | None = None,
    skip_primary_os_drive: bool = True,
):
    all_volumes = get_connected_volumes()
    if skip_primary_os_drive:
        all_volumes = [v for v in all_volumes if not _is_primary_os_drive(Path(v["path"]))]

    # The configured music root is always scanned, even when it lives on the
    # OS drive that skip_primary_os_drive excludes — otherwise a home-folder
    # library is invisible to the split view.
    scan_roots: list[tuple[Path, str, str]] = []  # (root, drive_name, drive_path)
    seen_roots: set[str] = set()
    try:
        from config import MUSIC_ROOT as _MR  # noqa: PLC0415
        mr = Path(str(_MR))
        if mr.is_dir():
            scan_roots.append((mr, mr.name or "Music", str(mr)))
            resolved_music = _real_path_str(mr)
            if resolved_music:
                seen_roots.add(resolved_music)
    except Exception:
        pass
    for vol in all_volumes:
        root = Path(vol["path"])
        resolved = _real_path_str(root)
        if not resolved:
            continue
        if resolved in seen_roots or not root.is_dir():
            continue
        seen_roots.add(resolved)
        scan_roots.append((root, vol["name"], vol["path"]))

    entries = []
    total_estimate = 0
    truncated = False
    limit_per_vol = per_volume_limit or limit
    deadline = time.monotonic() + _FS_DRIVE_SCAN_TIMEOUT

    for root, drive_name, drive_path in scan_roots:
        remaining = limit - len(entries)
        if remaining <= 0:
            truncated = True
            break
        audio_files, root_truncated = _collect_audio_files(
            root,
            limit=min(limit_per_vol, remaining),
            deadline=deadline,
        )
        entries.extend((path, drive_name, drive_path) for path in audio_files)
        total_estimate += len(audio_files)
        if root_truncated or len(entries) >= limit or time.monotonic() >= deadline:
            truncated = True
        if time.monotonic() >= deadline:
            break

    return entries, total_estimate, truncated, all_volumes
# ── Library track routes ──────────────────────────────────────────────────────

def _resolve_db(db_param):
    """Return the DB path for a ?db= query param.  'device' → DEVICE_DB, else LOCAL_DB."""
    from config import LOCAL_DB, DEVICE_DB  # noqa: PLC0415
    if db_param and str(db_param).lower() in ("device",):
        return DEVICE_DB
    return LOCAL_DB


_FABLEGEAR_DB = None
_FG_SYNC = {"running": False, "phase": "idle", "done": 0, "total": 0,
            "result": None, "error": None}


def _fablegear_db(create: bool = False):
    """Open (once) the FableGear database — the primary Record Room source.

    Read paths call this with the default ``create=False``: if the library
    has not been built yet the function returns ``None`` rather than creating
    the file, so merely *viewing* the Record Room never materialises a
    database. Only explicit write paths (import / sync / onboarding) pass
    ``create=True``. Callers must handle a ``None`` return.
    """
    global _FABLEGEAR_DB
    if _FABLEGEAR_DB is not None:
        return _FABLEGEAR_DB
    from fablegear_database.database import FableGearDatabase  # noqa: PLC0415
    if not create and not FableGearDatabase.default_db_path().exists():
        return None
    _FABLEGEAR_DB = FableGearDatabase(create=create)
    return _FABLEGEAR_DB


def _fablegear_track_payload(rec):
    """Map a FableGear ContentRecord onto the Record Room track payload shape."""
    return {
        "id":         str(rec.id),
        "title":      rec.title or rec.file_name or "",
        "artist":     rec.artist or "",
        "album":      rec.album or "",
        "genre":      rec.genre or "",
        "label":      rec.label or "",
        "bpm":        round(rec.bpm, 1) if rec.bpm else None,
        "key":        rec.key,
        "key_id":     None,
        "duration":   rec.duration,
        "date_added": rec.created_at,
        "file_path":  rec.file_path or "",
        "rating":     rec.rating or 0,
        "color":      None,
        "play_count": 0,
        "comment":    rec.comment or "",
        "track_no":   rec.track_number,
        "drive":      rec.drive,
        "in_rekordbox": bool(rec.in_rekordbox),
    }


def _resolve_local_content(db, track_id):
    """Resolve a track id to a Rekordbox-local ``DjmdContent``.

    The Record Room's default source is the FableGear database, which uses a
    different id space than the Rekordbox local DB where playlists live. When an
    id isn't a Rekordbox id, fall back to the FableGear track's file path and
    match the local track by ``FolderPath`` (exact match only — never a fuzzy
    guess, so we never add the wrong track)."""
    track = db.get_content(ID=track_id)
    if track is not None:
        return track
    try:
        fg = _fablegear_db()
        rec = fg.get_content_by_id(int(track_id)) if fg else None
    except (ValueError, TypeError):
        rec = None
    path = (getattr(rec, "file_path", "") or "").strip() if rec else ""
    if not path:
        return None
    return db.get_content(FolderPath=path).first()


@bp.route("/api/library/tracks")
def api_library_tracks():
    source = (request.args.get("db") or "").lower()

    # The Rekordbox databases remain reachable as explicit, demoted sources.
    if source in ("local", "device"):
        from db_connection import read_db  # noqa: PLC0415
        _DB = _resolve_db(source)
        try:
            with read_db(_DB) as db:
                tracks = [_library_track_payload(t) for t in db.get_content().all()]
                return jsonify(tracks)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # Default / primary: FableGear's own database (source "", "undefined",
    # "fablegear"). This is the database-first Record Room library.
    try:
        db = _fablegear_db()  # read-only: None when the library isn't built yet
        if db is None:
            resp = jsonify([])
            resp.headers["X-FableGear-Library"] = "missing"
            return resp
        rows = db.get_all_content(limit=100000, order_by="artist")
        return jsonify([_fablegear_track_payload(r) for r in rows])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/db/sync", methods=["POST"])
def api_library_db_sync():
    """Reconcile the FableGear database against the music library (background)."""
    if _FG_SYNC["running"]:
        return jsonify({"error": "sync already running"}), 409

    def _run():
        _FG_SYNC.update(running=True, phase="scanning", done=0, total=0,
                        result=None, error=None)
        try:
            from config import MUSIC_ROOT  # noqa: PLC0415
            from fablegear_database.importer import FileImporter  # noqa: PLC0415
            from fablegear_database.sync import DatabaseSync  # noqa: PLC0415
            db = _fablegear_db(create=True)  # sync is an explicit write/seed op
            sync = DatabaseSync(db, importer=FileImporter(db))
            _FG_SYNC.update(phase="reconciling")
            _FG_SYNC["result"] = sync.reconcile([Path(str(MUSIC_ROOT))])
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI
            _FG_SYNC["error"] = str(exc)
        finally:
            _FG_SYNC.update(running=False, phase="done")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"started": True})


@bp.route("/api/library/db/sync-status")
def api_library_db_sync_status():
    return jsonify(_FG_SYNC)


@bp.route("/api/library/fs-browse")
def api_library_fs_browse():
    """Browse a directory for audio files — filesystem-first, no rekordbox needed."""
    
    # 1. Standard imports needed for this route
    from config import MUSIC_ROOT as _MR
    import shutil

    # 2. Extract inputs
    path_str = request.args.get("path", "")
    recursive = request.args.get("recursive", "0").lower() in ("1", "true", "yes")

    # 3. Security check — any real folder is browseable (not just the
    # configured library root or /Volumes); see forbidden_browse_reason().
    if path_str:
        try:
            from path_guard import forbidden_browse_reason  # noqa: PLC0415
        except ImportError:  # imported via the chop_shop package
            from chop_shop.path_guard import forbidden_browse_reason  # noqa: PLC0415
        if forbidden_browse_reason(Path(path_str)) is not None:
            return jsonify({"error": "Access denied"}), 403

    # 4. Resolve path
    try:
        p = Path(path_str).resolve() if path_str else None
    except Exception:
        return jsonify({"error": "Invalid path"}), 400

    # ── Volume-root sentinel — return drive picker payload ──────────────────
    if _SYSTEM == "Windows":
        volumes_root = None          # Windows: synthesised from drive letters
        _is_vol_root = not path_str
    elif _SYSTEM == "Darwin":
        volumes_root = Path("/Volumes")
        _is_vol_root = not path_str or Path(path_str).resolve() == volumes_root
    else:
        # Linux: prefer /media/<user>, fall back to /mnt
        import getpass as _gp  # noqa: PLC0415
        _user_media = Path("/media") / _gp.getuser()
        volumes_root = _user_media if _user_media.is_dir() else Path("/media")
        _is_vol_root = not path_str or Path(path_str).resolve() in (volumes_root, Path("/mnt"))

    if _is_vol_root:
        from user_config import discover_music_roots  # noqa: PLC0415

        volumes = []

        # Build the list of root dirs to scan — platform-specific
        if _SYSTEM == "Windows":
            import string as _str  # noqa: PLC0415
            scan_roots = [
                Path(f"{d}:\\") for d in _str.ascii_uppercase
                if d not in ("A", "B") and Path(f"{d}:\\").exists()
            ]
            vroot_str = "Drives"
        else:
            scan_roots = [
                Path(volume["path"])
                for volume in get_connected_volumes()
                if Path(volume["path"]).is_dir() and not _is_primary_os_drive(Path(volume["path"]))
            ]
            scan_roots.sort(key=lambda root: root.name.lower())
            vroot_str = str(volumes_root)

        discovered = discover_music_roots([Path(v) for v in scan_roots if Path(v).is_dir()])
        discovered_by_path = {item["path"]: item for item in discovered}

        try:
            for vol in scan_roots:
                if not vol.is_dir() or (hasattr(vol, "name") and vol.name.startswith(".")):
                    continue
                discovered_info = discovered_by_path.get(str(vol), {})
                audio_estimate = int(discovered_info.get("audio_count", 0))
                total_gb = free_gb = None
                try:
                    usage = shutil.disk_usage(vol)
                    total_gb = round(usage.total / 1e9, 1)
                    free_gb = round(usage.free / 1e9, 1)
                except Exception:
                    pass
                has_pioneer_db = (vol / "PIONEER" / "rekordbox" / "master.db").exists()
                vol_name = vol.name if vol.name else str(vol).rstrip("/\\")
                volumes.append({
                    "name": vol_name,
                    "path": str(vol),
                    "audio_estimate": audio_estimate,
                    "total_gb": total_gb,
                    "free_gb": free_gb,
                    "has_pioneer_db": has_pioneer_db,
                    "recommended_home": bool(discovered_info.get("recommended_home")),
                    "recommended_archive_root": discovered_info.get("recommended_archive_root", ""),
                    "recommended_backup_dir": discovered_info.get("recommended_backup_dir", ""),
                })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        volumes.sort(key=lambda item: (-int(item.get("audio_estimate", 0)), item.get("name", "").lower()))

        if recursive:
            total_tracks = sum(int(v.get("audio_estimate", 0)) for v in volumes)
            truncated = total_tracks > _FS_RECURSIVE_LIMIT
            grouped_tracks: list[dict] = []
            track_paths: list[tuple[Path, str, str]] = []
            deadline = time.monotonic() + _FS_DRIVE_SCAN_TIMEOUT
            for vol in volumes:
                if len(track_paths) >= _FS_RECURSIVE_LIMIT:
                    break
                vol_path = Path(vol["path"])
                if int(vol.get("audio_estimate", 0)) <= 0:
                    continue
                remaining = _FS_RECURSIVE_LIMIT - len(track_paths)
                audio_files, vol_truncated = _collect_audio_files(
                    vol_path,
                    limit=remaining,
                    deadline=deadline,
                )
                track_paths.extend((item, vol["name"], vol["path"]) for item in audio_files)
                if vol_truncated or time.monotonic() >= deadline:
                    truncated = True
                if time.monotonic() >= deadline:
                    break

            tag_limit = _FS_TAG_LIMIT if not truncated else min(_FS_TAG_LIMIT, len(track_paths))
            for item, drive_name, drive_path in track_paths[:tag_limit]:
                payload = _fs_track_payload(item)
                payload["drive_name"] = drive_name
                payload["drive_path"] = drive_path
                grouped_tracks.append(payload)

            return jsonify({
                "path": vroot_str,
                "is_volumes_root": True,
                "music_root": str(_MR),
                "parent": None,
                "volumes": volumes,
                "subdirs": [],
                "tracks": grouped_tracks,
                "track_count": total_tracks,
                "truncated": truncated,
                "recursive": True,
                "grouped_by_drive": True,
                "recommended_music_root": discovered[0]["path"] if discovered else "",
            })

        return jsonify({
            "path":           vroot_str,
            "is_volumes_root": True,
            "music_root":     str(_MR),
            "parent":         None,
            "volumes":        volumes,
            "subdirs":        [],
            "tracks":         [],
            "recommended_music_root": discovered[0]["path"] if discovered else "",
        })

    # ── Normal path browse ───────────────────────────────────────────────────
    try:
        p = Path(path_str).resolve()
    except Exception:
        return jsonify({"error": "Invalid path"}), 400
    if not p.exists() or not p.is_dir():
        return jsonify({"error": f"Not a directory: {path_str}"}), 400

    music_root = str(_MR)
    parent = str(p.parent) if str(p) != str(p.anchor) else None

    if recursive:
        # Walk the whole tree with the same timeout/symlink guards used by the
        # multi-drive novelty scan path so a huge folder browse cannot block the UI.
        deadline = time.monotonic() + _FS_DRIVE_SCAN_TIMEOUT
        try:
            tracks, truncated = _collect_audio_files(
                p,
                limit=_FS_RECURSIVE_LIMIT,
                deadline=deadline,
            )
        except PermissionError:
            return jsonify({"error": "Permission denied"}), 403
        total_tracks = len(tracks)

        tag_limit = _FS_TAG_LIMIT if not truncated else min(_FS_TAG_LIMIT, len(tracks))
        track_payloads = [_fs_track_payload(t) for t in tracks[:tag_limit]]
        return jsonify({
            "path":           str(p),
            "music_root":     music_root,
            "in_music_root":  str(p).startswith(music_root),
            "parent":         parent,
            "subdirs":        [],   # omitted in recursive mode — sidebar stays navigable
            "tracks":         track_payloads,
            "track_count":    total_tracks,
            "truncated":      truncated,
            "recursive":      True,
        })

    # ── Non-recursive (default) ───────────────────────────────────────────────
    subdirs = []
    tracks_flat: list[Path] = []
    try:
        items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        for item in items:
            if item.name.startswith("."):
                continue
            if item.is_dir():
                try:
                    audio_count = sum(1 for f in item.iterdir()
                                      if not f.name.startswith(".") and f.suffix.lower() in _FS_AUDIO_EXTS)
                except PermissionError:
                    audio_count = 0
                subdirs.append({"name": item.name, "path": str(item), "audio_count": audio_count})
            elif item.suffix.lower() in _FS_AUDIO_EXTS:
                tracks_flat.append(item)
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403

    total_tracks = len(tracks_flat)
    truncated = total_tracks > _FS_TAG_LIMIT
    track_payloads = [_fs_track_payload(t) for t in tracks_flat[:_FS_TAG_LIMIT]]

    return jsonify({
        "path":           str(p),
        "music_root":     music_root,
        "in_music_root":  str(p).startswith(music_root),
        "parent":         parent,
        "subdirs":        subdirs,
        "tracks":         track_payloads,
        "track_count":    total_tracks,
        "truncated":      truncated,
        "recursive":      False,
    })


@bp.route("/api/library/split-data")
def api_library_split_data():
    """Integrated three-library view (FableGear | Rekordbox | Novelty):
    • fablegear — every track in the FableGear database (the Record Room source)
    • rekordbox — every track in the rekordbox library database
    • novelty   — filesystem audio (pooled across all connected drives) missing
                  from at least one database, flagged with membership booleans:
                  in_fablegear=False → "blue", in_rekordbox=False → "yellow",
                  in neither → "green".

    One shared filesystem scan feeds the novelty column, so it is exactly
    "what's on disk minus what each database already knows about".
    """
    from db_connection import read_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB, MUSIC_ROOT as _MR  # noqa: PLC0415

    music_root = str(_MR)

    # ── Column 1: FableGear database ─────────────────────────────────────────
    fablegear_tracks: list = []
    fg_error = None
    fg_path_set: set = set()
    fg_name_set: set = set()
    try:
        fgdb = _fablegear_db()  # read-only: None when the library isn't built yet
        fg_rows = fgdb.get_all_content(limit=100000, order_by="artist") if fgdb else []
        fablegear_tracks = [_fablegear_track_payload(r) for r in fg_rows]
        for r in fg_rows:
            fp = (r.file_path or "").strip()
            if fp:
                fg_path_set.add(fp)
                fg_name_set.add(Path(fp).name.lower())
    except Exception as exc:
        fg_error = str(exc)

    # ── Column 2: Rekordbox library (all DB tracks) ──────────────────────────
    rekordbox_tracks: list = []
    rb_error = None
    db_path_set: set = set()
    db_name_set: set = set()
    try:
        with read_db(_DB) as db:
            rekordbox_tracks = [_library_track_payload(t) for t in db.get_content().all()]
        for track in rekordbox_tracks:
            fp = (track.get("file_path") or "").strip()
            if fp:
                db_path_set.add(fp)
                db_name_set.add(Path(fp).name.lower())
    except Exception as exc:
        rb_error = str(exc)

    if fg_error and rb_error:
        return jsonify({"error": f"both databases unavailable — FableGear: {fg_error} · rekordbox: {rb_error}"}), 500

    # ── Column 3: novelty — on disk, missing from at least one database ──────
    entries, fs_total, truncated, volumes = _enumerate_drive_audio()
    tag_limit = _FS_TAG_LIMIT if not truncated else min(_FS_TAG_LIMIT, len(entries))

    novelty: list = []
    for item, drive_name, drive_path in entries[:tag_limit]:
        path_str = str(item)
        name_lc = item.name.lower()
        in_rb = path_str in db_path_set or name_lc in db_name_set
        in_fg = path_str in fg_path_set or name_lc in fg_name_set
        if in_rb and in_fg:
            continue
        payload = _fs_track_payload(item)
        novelty.append({
            "path":          path_str,
            "filename":      item.name,
            "title":         payload.get("title") or item.stem,
            "drive_name":    drive_name,
            "in_fablegear":  in_fg,
            "in_rekordbox":  in_rb,
        })

    return jsonify({
        "music_root":       music_root,
        "fablegear":        fablegear_tracks,
        "fablegear_count":  len(fablegear_tracks),
        "fablegear_error":  fg_error,
        "rekordbox":        rekordbox_tracks,
        "rekordbox_count":  len(rekordbox_tracks),
        "rekordbox_error":  rb_error,
        "novelty":          novelty,
        "novelty_count":    len(novelty),
        "fs_scanned":       fs_total,
        "truncated":        truncated,
        "volumes":          volumes,
    })


@bp.route("/api/library/db/import", methods=["POST"])
def api_library_db_import():
    """Import specific files into the FableGear database (drag-to-import).

    Body: {"paths": ["/abs/file.mp3", ...]}
    """
    body = request.get_json(force=True, silent=True) or {}
    paths = [str(p).strip() for p in body.get("paths", []) if str(p).strip()]
    if not paths:
        return jsonify({"error": "paths list is required"}), 400

    try:
        from fablegear_database.importer import FileImporter  # noqa: PLC0415
        db = _fablegear_db(create=True)  # drag-to-import is an explicit write op
        stats = FileImporter(db).import_paths([Path(p) for p in paths])
        if stats.get("new_files", 0) > 0 or stats.get("updated_files", 0) > 0:
            try:
                from fablegear_database.undo import DatabaseUndoManager
                undo_mgr = DatabaseUndoManager(db)
                undo_mgr.record_import(
                    imported_count=stats["new_files"] + stats["updated_files"],
                    root_paths=[Path(p) for p in paths],
                )
            except Exception as exc:
                log.warning("Failed to record import in transaction history: %s", exc)
        # Callers that need to chain a follow-up action (e.g. add the freshly
        # imported track to a playlist right after a drag-drop) need the
        # content_id — import_paths() only returns counts, so look each path
        # back up. Cheap: one indexed query per path, and this route only ever
        # handles a handful of drag-dropped files at a time.
        content_ids = {}
        for p in paths:
            record = db.get_content_by_path(p)
            if record is not None and record.id is not None:
                content_ids[p] = record.id
        stats["content_ids"] = content_ids
        return jsonify(stats)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/integrity/canonical-paths/plan")
def api_library_integrity_canonical_paths_plan():
    """Build a read-only consolidation plan for canonical path cleanup.

    This is the "Database Library" duplicate view: it groups DjmdContent
    records by artist + title + duration and flags groups that disagree on
    FolderPath, regardless of whether any of those paths still resolve on
    disk. It never fingerprints or reads an audio file — see
    database_dedup.py for the full physical-vs-database distinction.
    """
    from db_connection import read_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB  # noqa: PLC0415
    from database_dedup import build_plan  # noqa: PLC0415

    try:
        try:
            max_groups = int(request.args.get("max_groups", 50))
        except (TypeError, ValueError):
            max_groups = 50
        max_groups = max(1, min(500, max_groups))

        with read_db(_DB) as db:
            plan = build_plan(db, max_groups=max_groups)

        return jsonify({
            "ok": True,
            "read_only": True,
            "total_tracks_scanned": plan["total_tracks_scanned"],
            "total_conflict_groups": plan["total_conflict_groups"],
            "planned_groups": plan["planned_groups"],
            "plans": plan["plans"],
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/integrity/canonical-paths/execute", methods=["POST"])
def api_library_integrity_canonical_paths_execute():
    """
    Consolidate canonical-path duplicates: re-wire every playlist membership
    that pointed at a losing DjmdContent record onto the surviving record,
    then delete the losing records. No audio file is ever moved or deleted —
    only redundant database rows.

    Body (all optional):
      {"max_groups": 50, "signatures": [{"artist": ..., "title": ..., "duration": ...}, ...]}

    "signatures" restricts execution to the listed groups (matched by
    artist + title + duration) — pass the "signature" objects returned by
    the /plan endpoint to consolidate only what the user reviewed. Omit it
    to consolidate every non-ambiguous group found. Ambiguous groups (two+
    records tied on both disk-existence and playlist reference count) are
    always left for manual resolution.

    Requires Rekordbox to be closed and creates a timestamped database
    backup before writing (enforced by db_connection.write_db()).
    """
    from db_connection import write_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB  # noqa: PLC0415
    from database_dedup import build_plan, execute_plan  # noqa: PLC0415

    data = request.get_json(silent=True) or {}
    signatures = data.get("signatures")
    if signatures is not None:
        if not isinstance(signatures, list):
            return jsonify({"error": "signatures must be a list"}), 400
        cleaned: list[dict] = []
        for sig in signatures:
            if not isinstance(sig, dict):
                return jsonify({"error": "each signature must be an object"}), 400
            try:
                duration = int(sig.get("duration", 0) or 0)
            except (TypeError, ValueError):
                return jsonify({"error": "signature.duration must be an integer"}), 400
            cleaned.append({"artist": sig.get("artist"), "title": sig.get("title"), "duration": duration})
        signatures = cleaned

    try:
        max_groups = int(data.get("max_groups", 500))
    except (TypeError, ValueError):
        max_groups = 500
    max_groups = max(1, min(2000, max_groups))

    log_lines: list[str] = []

    try:
        with write_db(_DB) as db:
            plan = build_plan(db, max_groups=max_groups)
            summary = execute_plan(
                db, plan, signatures=signatures, log_fn=log_lines.append,
            )
            db.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({
        "ok": True,
        "total_conflict_groups": plan["total_conflict_groups"],
        "log": log_lines,
        **summary,
    })


@bp.route("/api/library/tracks/<track_id>/stream")
def api_library_track_stream(track_id):
    source = (request.args.get("db") or "").lower()

    # FableGear DB: numeric IDs, default source
    if source not in ("local", "device"):
        try:
            db = _fablegear_db()  # read-only: None when the library isn't built yet
            rec = db.get_content_by_id(int(track_id)) if db else None
            if rec is not None:
                file_path = (rec.file_path or "").strip()
                if not file_path or not os.path.isfile(file_path):
                    return jsonify({"error": f"Audio file not found on disk: {file_path}"}), 404
                mime, _ = mimetypes.guess_type(file_path)
                return send_file(file_path, mimetype=mime or "audio/mpeg", conditional=True)
            # Not a FableGear id — fall through to the Rekordbox lookup so tracks
            # from a Rekordbox playlist still stream on the default source.
        except (ValueError, TypeError):
            pass
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # Rekordbox fallback
    from db_connection import read_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB  # noqa: PLC0415

    try:
        with read_db(_DB) as db:
            track = db.get_content(ID=track_id)
            if track is None:
                return jsonify({"error": f"Track {track_id!r} not found in DB"}), 404
            file_path = str(track.FolderPath or "").strip()

        if not file_path:
            return jsonify({"error": f"Track {track_id!r} has no file path in DB"}), 404
        if not os.path.isfile(file_path):
            return jsonify({"error": f"Audio file not found on disk: {file_path}"}), 404

        mime, _ = mimetypes.guess_type(file_path)
        return send_file(file_path, mimetype=mime or "audio/mpeg", conditional=True)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Hot cues / loops (Record Room deck performance state) ─────────────────────
#
# Backed by fg_cue, which already exists and is exercised by the Rekordbox
# bidirectional sync path (fablegear_database/rekordbox_sync.py) — these routes
# are the first thing to expose it to the deck UI itself. Only FableGear-native
# tracks (numeric content_id) are supported: a Rekordbox-sourced track's hot
# cues/loops live in Rekordbox's own database, not fg_cue, and writing to that
# is out of scope here.
#
# kind: 0 = Memory cue, 1 = Hot cue, 2 = Loop, 3 = Active loop (mirrors fg_cue).
# slot: 0-3 for hot cues (Record Room ships 4 pads/deck, not Rekordbox's 8 —
# matches the "not too busy" sizing call in the performance-mode audit).

def _cues_db_and_content(track_id):
    """Resolve track_id to (db, content_id), or (None, None) if unsupported/missing."""
    try:
        content_id = int(track_id)
    except (ValueError, TypeError):
        return None, None
    db = _fablegear_db()
    if db is None or db.get_content_by_id(content_id) is None:
        return None, None
    return db, content_id


@bp.route("/api/library/tracks/<track_id>/cues")
def api_library_track_cues(track_id):
    """List hot cues + loops for a FableGear-native track. Empty list for any
    track this doesn't support (Rekordbox-sourced, or library not built)."""
    db, content_id = _cues_db_and_content(track_id)
    if db is None:
        return jsonify([])
    try:
        return jsonify([c.to_dict() for c in db.get_cues_for_content(content_id)])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/tracks/<track_id>/cues", methods=["POST"])
def api_library_track_cues_set(track_id):
    """
    Set or clear one hot-cue/loop slot. Body:
      {"kind": 1, "slot": 0, "in_msec": 5230, "out_msec": null,
       "color": "#ff9d42", "comment": null}
    Omit / pass null for in_msec to clear that (kind, slot) pair instead.
    Every other cue for this track is left untouched — read-modify-write
    around bulk_upsert_cues, which the DB layer only offers as a full replace.
    """
    db, content_id = _cues_db_and_content(track_id)
    if db is None:
        return jsonify({"error": "hot cues are only supported for FableGear-native tracks with a built library"}), 404

    body = request.get_json(force=True, silent=True) or {}
    kind = body.get("kind")
    slot = body.get("slot")
    if kind not in (0, 1, 2, 3):
        return jsonify({"error": "kind must be 0 (memory), 1 (hotcue), 2 (loop), or 3 (active loop)"}), 400

    from fablegear_database.database import CueRecord  # noqa: PLC0415
    try:
        existing = db.get_cues_for_content(content_id)
        kept = [c for c in existing if not (c.kind == kind and c.slot == slot)]
        if body.get("in_msec") is not None:
            kept.append(CueRecord(
                kind=kind, slot=slot,
                in_msec=int(body["in_msec"]),
                out_msec=int(body["out_msec"]) if body.get("out_msec") is not None else None,
                color=body.get("color"),
                comment=body.get("comment"),
            ))
        db.bulk_upsert_cues(content_id, kept)
        return jsonify({"ok": True, "cues": [c.to_dict() for c in kept]})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/playlists", methods=["GET"])
def api_library_playlists():
    source = (request.args.get("db") or "").lower()
    # FableGear-native playlists are the default Record Room source (they hold
    # the user's own library). Rekordbox databases stay reachable as demoted,
    # explicit sources.
    if source not in ("local", "device"):
        fg = _fablegear_db()  # read-only: None when the library isn't built yet
        if fg is None:
            return jsonify([])
        try:
            return jsonify(fg.list_playlists())
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    from db_connection import read_db  # noqa: PLC0415
    _DB = _resolve_db(source)  # 'device' → DEVICE_DB, everything else → LOCAL_DB
    if not _DB or not os.path.exists(_DB):
        return jsonify([])  # no Rekordbox DB yet → empty tree, never an error

    try:
        with read_db(_DB) as db:
            return jsonify(_playlist_tree_payload(db))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/playlists", methods=["POST"])
def api_library_create_playlist():
    from db_connection import write_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB  # noqa: PLC0415

    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    node_type = str(data.get("type", "playlist")).strip().lower() or "playlist"
    parent_id = str(data.get("parent_id", "")).strip()
    source = (request.args.get("db") or data.get("db") or "").lower()

    if not name:
        return jsonify({"error": "name required"}), 400
    if node_type not in {"playlist", "folder"}:
        return jsonify({"error": "type must be playlist or folder"}), 400

    if source not in ("local", "device"):
        fg = _fablegear_db(create=True)  # creating a playlist is an explicit write
        try:
            pid = fg.create_playlist(name, parent_id=parent_id or None, playlist_type=node_type)
            return jsonify({"ok": True, "id": str(pid), "name": name, "type": node_type}), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    try:
        with write_db(_DB) as db:
            parent = None
            if parent_id:
                parent = db.get_playlist(ID=parent_id)
                if parent is None:
                    return jsonify({"error": "parent playlist not found"}), 404

            if node_type == "folder":
                playlist = db.create_playlist_folder(name, parent=parent)
            else:
                playlist = db.create_playlist(name, parent=parent)
            db.commit()
            return jsonify({
                "ok": True,
                "id": str(playlist.ID),
                "name": playlist.Name or name,
                "type": node_type,
            }), 201
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/playlists/<playlist_id>/tracks")
def api_library_playlist_tracks(playlist_id):
    source = (request.args.get("db") or "").lower()
    if source not in ("local", "device"):
        fg = _fablegear_db()
        if fg is None:
            return jsonify([])
        try:
            if fg.get_playlist(playlist_id) is None:
                return jsonify({"error": "Playlist not found"}), 404
            return jsonify([_fablegear_track_payload(r) for r in fg.get_playlist_songs(playlist_id)])
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    from db_connection import read_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB  # noqa: PLC0415

    try:
        with read_db(_DB) as db:
            playlist = db.get_playlist(ID=playlist_id)
            if playlist is None:
                return jsonify({"error": "Playlist not found"}), 404
            if int(getattr(playlist, "Attribute", 0) or 0) == 1:
                return jsonify([])

            songs = db.get_playlist_songs(PlaylistID=playlist.ID).order_by("TrackNo").all()
            tracks = []
            for song in songs:
                track = song.Content
                if track is None:
                    continue
                tracks.append(_library_track_payload(track, track_no=getattr(song, "TrackNo", None)))
            return jsonify(tracks)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/playlists/<playlist_id>/tracks", methods=["POST"])
def api_library_add_tracks_to_playlist(playlist_id):
    from db_connection import write_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB  # noqa: PLC0415

    data = request.get_json(silent=True) or {}
    track_ids = data.get("track_ids")
    if not isinstance(track_ids, list):
        single_track_id = str(data.get("track_id", "")).strip()
        track_ids = [single_track_id] if single_track_id else []

    track_ids = [str(track_id).strip() for track_id in track_ids if str(track_id).strip()]
    if not track_ids:
        return jsonify({"error": "track_ids required"}), 400

    source = (request.args.get("db") or data.get("db") or "").lower()
    if source not in ("local", "device"):
        fg = _fablegear_db()
        if fg is None or fg.get_playlist(playlist_id) is None:
            return jsonify({"error": "Playlist not found"}), 404
        added, skipped, missing = 0, [], []
        for tid in track_ids:
            try:
                if fg.add_song(playlist_id, tid):
                    added += 1
                else:
                    skipped.append(tid)  # already present
            except (LookupError, ValueError, TypeError):
                missing.append(tid)
                skipped.append(tid)
        return jsonify({"ok": True, "added": added, "skipped": skipped, "missing": missing}), 201

    try:
        with write_db(_DB) as db:
            playlist = db.get_playlist(ID=playlist_id)
            if playlist is None:
                return jsonify({"error": "Playlist not found"}), 404
            if int(getattr(playlist, "Attribute", 0) or 0) == 1:
                return jsonify({"error": "Cannot add tracks to a folder"}), 400

            existing_ids = set()
            existing_signatures = set()
            for song in db.get_playlist_songs(PlaylistID=playlist.ID).all():
                existing_ids.add(str(getattr(song, "ContentID", "")))
                song_track = getattr(song, "Content", None)
                if song_track is not None:
                    existing_signatures.add(_track_identity_signature(song_track))

            added = 0
            skipped = []
            missing = []
            for track_id in track_ids:
                track = _resolve_local_content(db, track_id)
                if track is None:
                    # Not in the Rekordbox library (by id or by path) — report it
                    # distinctly so the UI can say "import it first" rather than
                    # the misleading "already in playlist".
                    missing.append(track_id)
                    skipped.append(track_id)
                    continue

                signature = _track_identity_signature(track)
                if str(track.ID) in existing_ids or signature in existing_signatures:
                    skipped.append(track_id)
                    continue
                try:
                    db.add_to_playlist(playlist, track, track_no=None)
                    existing_ids.add(str(track.ID))
                    existing_signatures.add(signature)
                    added += 1
                except Exception:
                    skipped.append(track_id)
            db.commit()
            return jsonify({"ok": True, "added": added, "skipped": skipped, "missing": missing}), 201
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/playlists/<playlist_id>", methods=["PUT"])
def api_library_rename_playlist(playlist_id):
    from db_connection import write_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB  # noqa: PLC0415

    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "name required"}), 400

    source = (request.args.get("db") or data.get("db") or "").lower()
    if source not in ("local", "device"):
        fg = _fablegear_db()
        if fg is None or fg.get_playlist(playlist_id) is None:
            return jsonify({"error": "Playlist not found"}), 404
        fg.rename_playlist(playlist_id, name)
        return jsonify({"ok": True, "id": str(playlist_id), "name": name})

    try:
        with write_db(_DB) as db:
            playlist = db.get_playlist(ID=playlist_id)
            if playlist is None:
                return jsonify({"error": "Playlist not found"}), 404
            db.rename_playlist(playlist, name)
            db.commit()
            return jsonify({"ok": True, "id": str(playlist.ID), "name": name})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/playlists/<playlist_id>", methods=["DELETE"])
def api_library_delete_playlist(playlist_id):
    from db_connection import write_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB  # noqa: PLC0415

    source = (request.args.get("db") or "").lower()
    if source not in ("local", "device"):
        fg = _fablegear_db()
        if fg is None or fg.get_playlist(playlist_id) is None:
            return jsonify({"error": "Playlist not found"}), 404
        fg.delete_playlist(playlist_id)
        return jsonify({"ok": True, "id": str(playlist_id), "status": "deleted"})

    try:
        with write_db(_DB) as db:
            playlist = db.get_playlist(ID=playlist_id)
            if playlist is None:
                return jsonify({"error": "Playlist not found"}), 404
            db.delete_playlist(playlist)
            db.commit()
            return jsonify({"ok": True, "id": str(playlist_id), "status": "deleted"})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/playlists/<playlist_id>/tracks", methods=["DELETE"])
def api_library_remove_tracks_from_playlist(playlist_id):
    from db_connection import write_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB  # noqa: PLC0415

    data = request.get_json(silent=True) or {}
    track_ids = data.get("track_ids")
    if not isinstance(track_ids, list):
        return jsonify({"error": "track_ids required"}), 400

    track_ids = [str(track_id).strip() for track_id in track_ids if str(track_id).strip()]
    if not track_ids:
        return jsonify({"error": "track_ids required"}), 400

    source = (request.args.get("db") or data.get("db") or "").lower()
    if source not in ("local", "device"):
        fg = _fablegear_db()
        if fg is None or fg.get_playlist(playlist_id) is None:
            return jsonify({"error": "Playlist not found"}), 404
        removed, missing = 0, []
        for tid in track_ids:
            try:
                n = fg.remove_song(playlist_id, tid)
                removed += n
                if n == 0:
                    missing.append(tid)
            except (ValueError, TypeError):
                missing.append(tid)
        return jsonify({"ok": True, "removed": removed, "missing": missing})

    try:
        with write_db(_DB) as db:
            playlist = db.get_playlist(ID=playlist_id)
            if playlist is None:
                return jsonify({"error": "Playlist not found"}), 404
            if int(getattr(playlist, "Attribute", 0) or 0) == 1:
                return jsonify({"error": "Cannot remove tracks from a folder"}), 400

            removed = 0
            missing = []
            for track_id in track_ids:
                songs = db.get_playlist_songs(PlaylistID=playlist.ID, ContentID=track_id).all()
                if not songs:
                    missing.append(track_id)
                    continue
                for song in songs:
                    db.remove_from_playlist(playlist.ID, song.ID)
                    removed += 1

            db.commit()
            return jsonify({"ok": True, "removed": removed, "missing": missing})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/playlists/<playlist_id>/tracks/order", methods=["PUT"])
def api_library_reorder_playlist_tracks(playlist_id):
    """Reorder tracks in a playlist. Body: {track_ids: [id, id, ...]} in desired order."""
    from db_connection import write_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB  # noqa: PLC0415

    data = request.get_json(silent=True) or {}
    track_ids = data.get("track_ids")
    if not isinstance(track_ids, list) or not track_ids:
        return jsonify({"error": "track_ids required (ordered list)"}), 400

    track_ids = [str(t).strip() for t in track_ids if str(t).strip()]

    source = (request.args.get("db") or data.get("db") or "").lower()
    if source not in ("local", "device"):
        fg = _fablegear_db()
        if fg is None or fg.get_playlist(playlist_id) is None:
            return jsonify({"error": "Playlist not found"}), 404
        updated = fg.reorder_playlist(playlist_id, track_ids)
        return jsonify({"ok": True, "updated": updated})

    try:
        with write_db(_DB) as db:
            playlist = db.get_playlist(ID=playlist_id)
            if playlist is None:
                return jsonify({"error": "Playlist not found"}), 404
            if int(getattr(playlist, "Attribute", 0) or 0) == 1:
                return jsonify({"error": "Cannot reorder tracks in a folder"}), 400

            songs = db.get_playlist_songs(PlaylistID=playlist.ID).all()
            songs_by_content = {}
            for song in songs:
                content_id = str(getattr(song, "ContentID", "") or "")
                if not content_id:
                    continue
                songs_by_content.setdefault(content_id, []).append(song)

            dupes = [cid for cid, ss in songs_by_content.items() if len(ss) > 1]
            if dupes:
                return jsonify({"error": "Playlist contains duplicate tracks; reorder requires per-row identifiers"}), 400

            expected = set(songs_by_content.keys())
            if len(set(track_ids)) != len(track_ids) or set(track_ids) != expected:
                return jsonify({"error": "track_ids must include every track in the playlist exactly once"}), 400

            for new_pos, content_id in enumerate(track_ids, start=1):
                songs_by_content[content_id][0].TrackNo = new_pos

            updated = len(track_ids)
            db.commit()
            return jsonify({"ok": True, "updated": updated})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/tracks/<track_id>", methods=["PATCH"])
def api_library_patch_track(track_id):
    from db_connection import write_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB  # noqa: PLC0415

    data = request.get_json(silent=True) or {}
    if "title" not in data:
        return jsonify({"error": "title field required"}), 400

    new_title = str(data.get("title", "")).strip()
    if not new_title:
        return jsonify({"error": "title cannot be empty"}), 400

    try:
        with write_db(_DB) as db:
            track = db.get_content(ID=track_id)
            if track is None:
                return jsonify({"error": "Track not found"}), 404
            track.Title = new_title
            db.commit()
            return jsonify({"ok": True, "track": _library_track_payload(track)})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Library USB export ───────────────────────────────────────────────────────

@bp.route("/api/library/export/drives")
def api_library_export_drives():
    try:
        import psutil  # noqa: PLC0415
        drives = []
        for part in psutil.disk_partitions():
            mountpoint = part.mountpoint
            if not _is_user_mount(mountpoint):
                continue
            try:
                usage = psutil.disk_usage(mountpoint)
                drive_info = _detect_pioneer_drive_layout(mountpoint)
                name = mountpoint.rstrip("/\\") if _SYSTEM == "Windows" else Path(mountpoint).name
                drives.append({
                    "path": mountpoint,
                    "name": name,
                    "free_bytes": usage.free,
                    "total_bytes": usage.total,
                    **drive_info,
                })
            except (PermissionError, OSError):
                continue
        return jsonify(drives)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/export", methods=["POST"])
def api_library_export_start():
    data = request.get_json(silent=True) or {}
    playlist_ids = data.get("playlist_ids") or []
    drive_path = str(data.get("drive_path") or "").strip()

    if not playlist_ids:
        return jsonify({"error": "playlist_ids required"}), 400
    if not drive_path:
        return jsonify({"error": "drive_path required"}), 400

    # 1. Security Check: Path Traversal Protection
    target_path = Path(drive_path).resolve()
    # Assuming get_connected_volumes() or a similar helper returns list of mount paths
    all_volumes = [Path(v["path"]) for v in get_connected_volumes()]
    if not any(target_path.is_relative_to(m) for m in all_volumes):
        return jsonify({"error": "Invalid export destination"}), 403

    # 2. Pioneer Structure Check
    drive_info = _detect_pioneer_drive_layout(drive_path)
    if not drive_info.get("pioneer"):
        return jsonify({"error": f"No Pioneer export structure detected on {drive_path}"}), 400
    if not drive_info.get("export_supported"):
        return jsonify({"error": drive_info.get("export_error")}), 400

    job_id = str(uuid.uuid4())
    with _EXPORT_LOCK:
        _evict_old_jobs(_EXPORT_JOBS, _MAX_EXPORT_JOBS)
        _EXPORT_JOBS[job_id] = {
            "job_id": job_id,
            "status": "running",
            "tracks_total": 0,
            "tracks_done": 0,
            "current_track": "",
            "errors": [],
        }

    threading.Thread(
        target=_run_export,
        args=(job_id, [str(pid) for pid in playlist_ids], drive_path),
        daemon=True,
        name=f"library-export-{job_id[:8]}",
    ).start()

    return jsonify({"job_id": job_id}), 202

@bp.route("/api/library/export/<job_id>")
def api_library_export_status(job_id):
    with _EXPORT_LOCK:
        job = _EXPORT_JOBS.get(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)
