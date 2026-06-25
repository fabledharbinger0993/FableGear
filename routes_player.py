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
import uuid

_SYSTEM = platform.system()

def is_safe_path(path: str, allowed_roots: list[str]) -> bool:
    """Validate that the path is contained within at least one allowed root."""
    try:
        resolved_path = Path(path).resolve()
        for root in allowed_roots:
            if resolved_path.is_relative_to(Path(root).resolve()):
                return True
        return False
    except Exception:
        return False

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


def _library_canonical_path_conflicts(db):
    """Return (tracks_scanned, conflict_groups) for canonical-path integrity checks."""

    def _norm(value):
        return " ".join(str(value or "").strip().lower().split())

    tracks = db.get_content().all()
    grouped = {}
    for track in tracks:
        title = _norm(getattr(track, "Title", ""))
        artist_name = ""
        try:
            artist_name = _norm(track.Artist.Name if track.Artist else "")
        except Exception:
            artist_name = ""

        duration = int(getattr(track, "Length", 0) or 0)
        path = str(getattr(track, "FolderPath", "") or "").strip()

        if not title or not path:
            continue

        signature = (artist_name, title, duration)
        grouped.setdefault(signature, []).append(track)

    conflicts = []
    for signature, rows in grouped.items():
        distinct_paths = {
            str(getattr(row, "FolderPath", "") or "").strip()
            for row in rows
            if str(getattr(row, "FolderPath", "") or "").strip()
        }
        if len(distinct_paths) <= 1:
            continue

        items = []
        for row in rows:
            row_path = str(getattr(row, "FolderPath", "") or "").strip()
            if not row_path:
                continue
            playlist_refs = db.get_playlist_songs(ContentID=row.ID).all()
            items.append({
                "content_id": str(row.ID),
                "path": row_path,
                "exists_on_disk": os.path.isfile(row_path),
                "playlist_ref_count": len(playlist_refs),
            })

        artist_name, title, duration = signature
        conflicts.append({
            "signature": {
                "artist": artist_name,
                "title": title,
                "duration": duration,
            },
            "path_count": len(distinct_paths),
            "entries": items,
        })

    conflicts.sort(key=lambda g: (g["path_count"], len(g["entries"])), reverse=True)
    return len(tracks), conflicts


# ── Filesystem track helper ───────────────────────────────────────────────────

_FS_AUDIO_EXTS = frozenset({
    ".aiff", ".aif", ".aifc", ".wav", ".flac", ".mp3",
    ".m4a", ".m4p", ".alac", ".ogg", ".opus",
})
_FS_TAG_LIMIT = 500   # stop reading mutagen beyond this many tracks in one folder
_FS_RECURSIVE_LIMIT = 5000  # hard cap for recursive scans


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


def _enumerate_drive_audio(
    limit: int = _FS_RECURSIVE_LIMIT,
    *,
    per_volume_limit: int | None = None,
    skip_primary_os_drive: bool = True,
):
    from user_config import discover_music_roots

    def _is_system_drive(path: Path) -> bool:
        if platform.system() == "Windows":
            return path.drive.upper() == "C:"
        return path.parts == ("/",)

    all_volumes = get_connected_volumes()
    if skip_primary_os_drive:
        all_volumes = [v for v in all_volumes if not _is_system_drive(Path(v["path"]))]

    volume_roots = discover_music_roots(all_volumes)
    entries = []
    total_estimate = 0
    truncated = False
    limit_per_vol = per_volume_limit or limit

    for vol in all_volumes:
        roots = volume_roots.get(vol["path"], [Path(vol["path"])])
        vol_count = 0
        
        for root in roots:
            if vol_count >= limit_per_vol: break
            
            # Focused scan: only walk the configured music roots
            for p in root.rglob("*"):
                if vol_count >= limit_per_vol or len(entries) >= limit:
                    truncated = True
                    break
                
                if p.is_file() and p.suffix.lower() in _FS_AUDIO_EXTS:
                    entries.append((p, vol["name"], vol["path"]))
                    vol_count += 1
        
        total_estimate += vol_count

    return entries, total_estimate, truncated, all_volumes
# ── Library track routes ──────────────────────────────────────────────────────

def _resolve_db(db_param):
    """Return the DB path for a ?db= query param.  'device' → DJMT_DB, else LOCAL_DB."""
    from config import LOCAL_DB, DJMT_DB  # noqa: PLC0415
    if db_param and str(db_param).lower() in ("device", "djmt"):
        return DJMT_DB
    return LOCAL_DB


@bp.route("/api/library/tracks")
def api_library_tracks():
    from db_connection import read_db  # noqa: PLC0415
    _DB = _resolve_db(request.args.get("db"))

    try:
        with read_db(_DB) as db:
            tracks = [_library_track_payload(track) for track in db.get_content().all()]
            return jsonify(tracks)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/fs-browse")
def api_library_fs_browse():
    """Browse a directory for audio files — filesystem-first, no rekordbox needed."""
    
    # 1. Standard imports needed for this route
    from config import MUSIC_ROOT as _MR
    import shutil

    # 2. Extract inputs
    path_str = request.args.get("path", "")
    recursive = request.args.get("recursive", "0").lower() in ("1", "true", "yes")

    # 3. Security Check (Must happen after _MR is defined)
    if path_str and not is_safe_path(path_str, [str(_MR), "/Volumes", "/media"]):
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
            scan_roots = sorted(volumes_root.iterdir()) if volumes_root and volumes_root.exists() else []
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
            for vol in volumes:
                if len(track_paths) >= _FS_RECURSIVE_LIMIT:
                    break
                vol_path = Path(vol["path"])
                if int(vol.get("audio_estimate", 0)) <= 0:
                    continue
                try:
                    for item in vol_path.rglob("*"):
                        if item.name.startswith("."):
                            continue
                        if item.is_file() and item.suffix.lower() in _FS_AUDIO_EXTS:
                            track_paths.append((item, vol["name"], vol["path"]))
                            if len(track_paths) >= _FS_RECURSIVE_LIMIT:
                                break
                except PermissionError:
                    continue

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
        # Walk the whole tree, collecting audio files up to the hard cap.
        tracks: list[Path] = []
        try:
            for item in sorted(p.rglob("*"), key=lambda x: x.name.lower()):
                if item.name.startswith("."):
                    continue
                if item.is_file() and item.suffix.lower() in _FS_AUDIO_EXTS:
                    tracks.append(item)
                    if len(tracks) >= _FS_RECURSIVE_LIMIT:
                        break
        except PermissionError:
            return jsonify({"error": "Permission denied"}), 403

        # Count remaining tracks for the truncation message (cheap: just keep
        # scanning filenames without reading tags).
        total_tracks = len(tracks)
        truncated = total_tracks >= _FS_RECURSIVE_LIMIT
        if truncated:
            try:
                total_tracks = sum(
                    1 for item in p.rglob("*")
                    if not item.name.startswith(".")
                    and item.is_file()
                    and item.suffix.lower() in _FS_AUDIO_EXTS
                )
            except Exception:
                total_tracks = _FS_RECURSIVE_LIMIT  # best-effort

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
    """Integrated three-library view:
    • all_music  — every audio file pooled across all connected drives (filesystem,
                   independent of rekordbox), grouped by drive
    • rekordbox  — every track in the rekordbox library database
    • unimported — filesystem audio files (from the all_music scan) whose path is
                   NOT present in the rekordbox database (i.e. not yet imported)

    The filesystem scan is shared between all_music and unimported, so the third
    column is exactly "what's on disk minus what rekordbox knows about".
    """
    from db_connection import read_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB, MUSIC_ROOT as _MR  # noqa: PLC0415

    music_root = str(_MR)

    # ── Column 2: Rekordbox library (all DB tracks) ──────────────────────────
    try:
        with read_db(_DB) as db:
            rekordbox_tracks = [_library_track_payload(t) for t in db.get_content().all()]
    except Exception as exc:
        return jsonify({"error": f"rekordbox DB unavailable: {exc}"}), 500

    db_path_set: set = set()
    db_name_set: set = set()
    for track in rekordbox_tracks:
        fp = (track.get("file_path") or "").strip()
        if fp:
            db_path_set.add(fp)
            db_name_set.add(Path(fp).name.lower())

    # ── Columns 1 & 3: filesystem scan pooled across every connected drive ───
    entries, fs_total, truncated, volumes = _enumerate_drive_audio()
    tag_limit = _FS_TAG_LIMIT if not truncated else min(_FS_TAG_LIMIT, len(entries))

    all_music: list = []
    unimported: list = []
    for item, drive_name, drive_path in entries[:tag_limit]:
        payload = _fs_track_payload(item)
        payload["drive_name"] = drive_name
        payload["drive_path"] = drive_path
        all_music.append(payload)
        # Not in rekordbox if neither the exact path nor the filename is known.
        if str(item) not in db_path_set and item.name.lower() not in db_name_set:
            unimported.append({
                "path":       str(item),
                "filename":   item.name,
                "title":      payload.get("title") or item.stem,
                "drive_name": drive_name,
            })

    return jsonify({
        "music_root":        music_root,
        "all_music":         all_music,
        "all_music_count":   fs_total,
        "rekordbox":         rekordbox_tracks,
        "rekordbox_count":   len(rekordbox_tracks),
        "unimported":        unimported,
        "unimported_count":  len(unimported),
        "truncated":         truncated,
        "volumes":           volumes,
    })


@bp.route("/api/library/integrity/canonical-paths/plan")
def api_library_integrity_canonical_paths_plan():
    """Build a read-only consolidation plan for canonical path cleanup."""
    from db_connection import read_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB  # noqa: PLC0415

    try:
        try:
            max_groups = int(request.args.get("max_groups", 50))
        except (TypeError, ValueError):
            max_groups = 50
        max_groups = max(1, min(500, max_groups))

        with read_db(_DB) as db:
            tracks_scanned, conflicts = _library_canonical_path_conflicts(db)

        plans = []
        for group in conflicts[:max_groups]:
            entries = group.get("entries") or []
            if len(entries) < 2:
                continue

            keeper = max(
                entries,
                key=lambda e: (
                    1 if e.get("exists_on_disk") else 0,
                    int(e.get("playlist_ref_count", 0) or 0),
                    -len(str(e.get("path") or "")),
                    str(e.get("path") or "").lower(),
                ),
            )
            remove_candidates = [
                e for e in entries if str(e.get("content_id")) != str(keeper.get("content_id"))
            ]
            estimated_rethread = sum(
                int(e.get("playlist_ref_count", 0) or 0) for e in remove_candidates
            )

            plans.append({
                "signature": group.get("signature") or {},
                "keeper": keeper,
                "remove_candidates": remove_candidates,
                "estimated_playlist_slots_to_rethread": estimated_rethread,
            })

        return jsonify({
            "ok": True,
            "read_only": True,
            "total_tracks_scanned": tracks_scanned,
            "total_conflict_groups": len(conflicts),
            "planned_groups": len(plans),
            "plans": plans,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/library/tracks/<track_id>/stream")
def api_library_track_stream(track_id):
    from db_connection import read_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB  # noqa: PLC0415

    try:
        with read_db(_DB) as db:
            track = db.get_content(ID=track_id).one_or_none()
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


@bp.route("/api/library/playlists", methods=["GET"])
def api_library_playlists():
    from db_connection import read_db  # noqa: PLC0415
    _DB = _resolve_db(request.args.get("db"))

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

    if not name:
        return jsonify({"error": "name required"}), 400
    if node_type not in {"playlist", "folder"}:
        return jsonify({"error": "type must be playlist or folder"}), 400

    try:
        with write_db(_DB) as db:
            parent = None
            if parent_id:
                parent = db.get_playlist(ID=parent_id).one_or_none()
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
    from db_connection import read_db  # noqa: PLC0415
    from config import LOCAL_DB as _DB  # noqa: PLC0415

    try:
        with read_db(_DB) as db:
            playlist = db.get_playlist(ID=playlist_id).one_or_none()
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

    try:
        with write_db(_DB) as db:
            playlist = db.get_playlist(ID=playlist_id).one_or_none()
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
            for track_id in track_ids:
                track = db.get_content(ID=track_id).one_or_none()
                if track is None:
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
            return jsonify({"ok": True, "added": added, "skipped": skipped}), 201
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

    try:
        with write_db(_DB) as db:
            playlist = db.get_playlist(ID=playlist_id).one_or_none()
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

    try:
        with write_db(_DB) as db:
            playlist = db.get_playlist(ID=playlist_id).one_or_none()
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

    try:
        with write_db(_DB) as db:
            playlist = db.get_playlist(ID=playlist_id).one_or_none()
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

    try:
        with write_db(_DB) as db:
            playlist = db.get_playlist(ID=playlist_id).one_or_none()
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
            track = db.get_content(ID=track_id).one_or_none()
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
