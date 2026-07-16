import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from datetime import datetime

from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables
from pyrekordbox.db6.tables import DjmdContent, DjmdCue, DjmdArtist, DjmdAlbum, DjmdGenre, DjmdLabel, DjmdKey

from .database import FableGearDatabase, ContentRecord, CueRecord, BeatGridRecord

log = logging.getLogger(__name__)

HEX_TO_COLOR_ID = {
    "#ff007f": "1",  # Pink
    "#ff0000": "2",  # Red
    "#ffaa00": "3",  # Orange
    "#ffff00": "4",  # Yellow
    "#00ff00": "5",  # Green
    "#00f3ff": "6",  # Aqua / Cyan
    "#0000ff": "7",  # Blue
    "#7f00ff": "8",  # Purple
}
COLOR_ID_TO_HEX = {v: k for k, v in HEX_TO_COLOR_ID.items()}


def _get_or_create_artist(name: str, db: Rekordbox6Database) -> Optional[str]:
    if not name or not name.strip():
        return None
    name = name.strip()
    existing = db.get_artist(Name=name).first()
    if existing:
        return str(existing.ID)
    try:
        artist = db.add_artist(name=name)
        return str(artist.ID)
    except Exception:
        existing = db.get_artist(Name=name).first()
        return str(existing.ID) if existing else None


def _get_or_create_album(name: str, db: Rekordbox6Database) -> Optional[str]:
    if not name or not name.strip():
        return None
    name = name.strip()
    existing = db.get_album(Name=name).first()
    if existing:
        return str(existing.ID)
    try:
        album = db.add_album(name=name)
        return str(album.ID)
    except Exception:
        existing = db.get_album(Name=name).first()
        return str(existing.ID) if existing else None


def _get_or_create_genre(name: str, db: Rekordbox6Database) -> Optional[str]:
    if not name or not name.strip():
        return None
    name = name.strip()
    existing = db.get_genre(Name=name).first()
    if existing:
        return str(existing.ID)
    try:
        genre = db.add_genre(name=name)
        return str(genre.ID)
    except Exception:
        existing = db.get_genre(Name=name).first()
        return str(existing.ID) if existing else None


def _get_or_create_label(name: str, db: Rekordbox6Database) -> Optional[str]:
    if not name or not name.strip():
        return None
    name = name.strip()
    existing = db.get_label(Name=name).first()
    if existing:
        return str(existing.ID)
    try:
        label = db.add_label(name=name)
        return str(label.ID)
    except Exception:
        existing = db.get_label(Name=name).first()
        return str(existing.ID) if existing else None


class RekordboxSyncAdapter:
    """
    Adapter that orchestrates bidirectional synchronization between FableGear's
    local SQLite database and Rekordbox's master SQLite database (LOCAL_DB).
    """

    def __init__(self, fg_db: FableGearDatabase):
        self.fg_db = fg_db

    def sync_bidirectional(self, rekordbox_db_path: Path, dry_run: bool = False) -> Dict[str, Any]:
        """
        Reconcile and merge track metadata, cues, and loops.
        """
        stats = {
            "tracks_imported_to_rekordbox": 0,
            "tracks_imported_to_fablegear": 0,
            "tracks_updated_in_rekordbox": 0,
            "tracks_updated_in_fablegear": 0,
            "cues_synchronized": 0,
            "cues_deleted": 0,
            "details": [],
            "errors": []
        }

        # 1. Open Rekordbox Database Transaction
        from db_connection import write_db, read_db
        db_context = read_db if dry_run else write_db

        try:
            # Load all FableGear tracks with relations
            fg_tracks = self.fg_db.get_content_with_relations()
            fg_by_path = {t.file_path: t for t in fg_tracks}

            with db_context(rekordbox_db_path) as rdb:
                # Load all Rekordbox tracks
                rdb_tracks = rdb.get_content().all()
                rdb_by_path = {t.FolderPath: t for t in rdb_tracks}

                # Pre-fetch and index all Rekordbox cues
                rdb_cues = rdb.get_cue().all()
                cues_by_content_id = {}
                for cue in rdb_cues:
                    cues_by_content_id.setdefault(cue.ContentID, []).append(cue)

                # --- PASS 1: Reconcile matched tracks ---
                all_paths = set(fg_by_path.keys()).intersection(rdb_by_path.keys())
                for path in all_paths:
                    fg_rec = fg_by_path[path]
                    rdb_row = rdb_by_path[path]

                    fg_updated = False
                    rdb_updated = False

                    # Title
                    if fg_rec.title and fg_rec.title != rdb_row.Title:
                        rdb_row.Title = fg_rec.title
                        rdb_updated = True
                        stats["details"].append(f"Update Title in Rekordbox for {path}")
                    elif not fg_rec.title and rdb_row.Title:
                        fg_rec.title = rdb_row.Title
                        fg_updated = True
                        stats["details"].append(f"Merge Title to FableGear for {path}")

                    # Artist
                    rdb_artist = rdb_row.Artist.Name if rdb_row.Artist else ""
                    if fg_rec.artist and fg_rec.artist != rdb_artist:
                        rdb_row.ArtistID = _get_or_create_artist(fg_rec.artist, rdb)
                        rdb_updated = True
                        stats["details"].append(f"Update Artist in Rekordbox for {path}")
                    elif not fg_rec.artist and rdb_artist:
                        fg_rec.artist = rdb_artist
                        fg_updated = True
                        stats["details"].append(f"Merge Artist to FableGear for {path}")

                    # Album
                    rdb_album = rdb_row.Album.Name if rdb_row.Album else ""
                    if fg_rec.album and fg_rec.album != rdb_album:
                        rdb_row.AlbumID = _get_or_create_album(fg_rec.album, rdb)
                        rdb_updated = True
                        stats["details"].append(f"Update Album in Rekordbox for {path}")
                    elif not fg_rec.album and rdb_album:
                        fg_rec.album = rdb_album
                        fg_updated = True
                        stats["details"].append(f"Merge Album to FableGear for {path}")

                    # BPM
                    rdb_bpm = rdb_row.BPM / 100.0 if rdb_row.BPM else 0.0
                    if fg_rec.bpm and abs(fg_rec.bpm - rdb_bpm) > 0.01:
                        rdb_row.BPM = int(round(fg_rec.bpm * 100))
                        rdb_updated = True
                        stats["details"].append(f"Update BPM in Rekordbox for {path}")
                    elif not fg_rec.bpm and rdb_bpm > 0:
                        fg_rec.bpm = rdb_bpm
                        fg_updated = True
                        stats["details"].append(f"Merge BPM to FableGear for {path}")

                    # Key (ScaleName)
                    from key_mapper import resolve_key_id
                    rdb_key = rdb_row.Key.ScaleName if rdb_row.Key else ""
                    if fg_rec.key and fg_rec.key != rdb_key:
                        rdb_row.KeyID = resolve_key_id(fg_rec.key, rdb)
                        rdb_updated = True
                        stats["details"].append(f"Update Key in Rekordbox for {path}")
                    elif not fg_rec.key and rdb_key:
                        fg_rec.key = rdb_key
                        fg_updated = True
                        stats["details"].append(f"Merge Key to FableGear for {path}")

                    # Rating
                    rdb_rating = rdb_row.Rating or 0
                    if fg_rec.rating != rdb_rating:
                        if fg_rec.rating > 0:  # FableGear has edit
                            rdb_row.Rating = fg_rec.rating
                            rdb_updated = True
                            stats["details"].append(f"Update Rating in Rekordbox for {path}")
                        else:  # Rekordbox has edit
                            fg_rec.rating = rdb_rating
                            fg_updated = True
                            stats["details"].append(f"Merge Rating to FableGear for {path}")

                    # Color
                    rdb_color = COLOR_ID_TO_HEX.get(rdb_row.ColorID, "")
                    if fg_rec.color and fg_rec.color != rdb_color:
                        rdb_row.ColorID = HEX_TO_COLOR_ID.get(fg_rec.color.lower(), None)
                        rdb_updated = True
                        stats["details"].append(f"Update Color in Rekordbox for {path}")
                    elif not fg_rec.color and rdb_color:
                        fg_rec.color = rdb_color
                        fg_updated = True
                        stats["details"].append(f"Merge Color to FableGear for {path}")

                    # Comment / Commnt
                    rdb_comment = rdb_row.Commnt or ""
                    if fg_rec.comment and fg_rec.comment != rdb_comment:
                        rdb_row.Commnt = fg_rec.comment
                        rdb_updated = True
                        stats["details"].append(f"Update Comment in Rekordbox for {path}")
                    elif not fg_rec.comment and rdb_comment:
                        fg_rec.comment = rdb_comment
                        fg_updated = True
                        stats["details"].append(f"Merge Comment to FableGear for {path}")

                    # Genre
                    rdb_genre = rdb_row.Genre.Name if rdb_row.Genre else ""
                    if fg_rec.genre and fg_rec.genre != rdb_genre:
                        rdb_row.GenreID = _get_or_create_genre(fg_rec.genre, rdb)
                        rdb_updated = True
                        stats["details"].append(f"Update Genre in Rekordbox for {path}")
                    elif not fg_rec.genre and rdb_genre:
                        fg_rec.genre = rdb_genre
                        fg_updated = True
                        stats["details"].append(f"Merge Genre to FableGear for {path}")

                    # Label
                    rdb_label = rdb_row.Label.Name if rdb_row.Label else ""
                    if fg_rec.label and fg_rec.label != rdb_label:
                        rdb_row.LabelID = _get_or_create_label(fg_rec.label, rdb)
                        rdb_updated = True
                        stats["details"].append(f"Update Label in Rekordbox for {path}")
                    elif not fg_rec.label and rdb_label:
                        fg_rec.label = rdb_label
                        fg_updated = True
                        stats["details"].append(f"Merge Label to FableGear for {path}")

                    # Save track updates
                    if fg_updated:
                        stats["tracks_updated_in_fablegear"] += 1
                        if not dry_run:
                            self.fg_db.upsert_content([fg_rec])

                    if rdb_updated:
                        stats["tracks_updated_in_rekordbox"] += 1

                    # --- Sync Cues & Loops for matched track ---
                    fg_cues = fg_rec.cues or []
                    rdb_track_cues = cues_by_content_id.get(rdb_row.ID, [])

                    # Match / reconcile cues
                    reconciled_cues = self._sync_track_cues(
                        rdb_row.ID, rdb_row.UUID, fg_cues, rdb_track_cues, rdb, dry_run
                    )
                    stats["cues_synchronized"] += reconciled_cues["synchronized"]
                    stats["cues_deleted"] += reconciled_cues["deleted"]
                    stats["details"].extend(reconciled_cues["details"])

                # --- PASS 2: Tracks only in FableGear -> Import to Rekordbox ---
                fg_only_paths = set(fg_by_path.keys()) - set(rdb_by_path.keys())
                for path in fg_only_paths:
                    fg_rec = fg_by_path[path]
                    stats["details"].append(f"Import new track from FableGear to Rekordbox: {path}")
                    stats["tracks_imported_to_rekordbox"] += 1

                    if not dry_run:
                        # Add Content row
                        import_path = Path(path)
                        is_aif = import_path.suffix.lower() == ".aif"
                        if is_aif:
                            import_path = import_path.with_suffix(".aiff")

                        kwargs = {
                            "Title": fg_rec.title or import_path.stem,
                            "ArtistID": _get_or_create_artist(fg_rec.artist, rdb) if fg_rec.artist else None,
                            "AlbumID": _get_or_create_album(fg_rec.album, rdb) if fg_rec.album else None,
                            "GenreID": _get_or_create_genre(fg_rec.genre, rdb) if fg_rec.genre else None,
                            "LabelID": _get_or_create_label(fg_rec.label, rdb) if fg_rec.label else None,
                            "KeyID": resolve_key_id(fg_rec.key, rdb) if fg_rec.key else None,
                            "BPM": int(round(fg_rec.bpm * 100)) if fg_rec.bpm else None,
                            "Length": int(fg_rec.duration) if fg_rec.duration else None,
                            "Rating": fg_rec.rating or 0,
                            "Commnt": fg_rec.comment or "",
                            "ColorID": HEX_TO_COLOR_ID.get(fg_rec.color.lower(), None) if fg_rec.color else None
                        }

                        new_row = rdb.add_content(import_path, **kwargs)
                        if is_aif:
                            setattr(new_row, "FolderPath", str(Path(path)))

                        # Synchronize its cues immediately
                        if fg_rec.cues:
                            reconciled_cues = self._sync_track_cues(
                                new_row.ID, new_row.UUID, fg_rec.cues, [], rdb, dry_run
                            )
                            stats["cues_synchronized"] += reconciled_cues["synchronized"]
                            stats["details"].extend(reconciled_cues["details"])

                # --- PASS 3: Tracks only in Rekordbox -> Import to FableGear ---
                rdb_only_paths = set(rdb_by_path.keys()) - set(fg_by_path.keys())
                for path in rdb_only_paths:
                    rdb_row = rdb_by_path[path]
                    stats["details"].append(f"Import new track from Rekordbox to FableGear: {path}")
                    stats["tracks_imported_to_fablegear"] += 1

                    if not dry_run:
                        p = Path(path)
                        fg_rec = ContentRecord(
                            file_path=path,
                            file_name=p.name,
                            file_size=p.stat().st_size if p.is_file() else 0,
                            title=rdb_row.Title,
                            artist=rdb_row.Artist.Name if rdb_row.Artist else "",
                            album=rdb_row.Album.Name if rdb_row.Album else "",
                            bpm=rdb_row.BPM / 100.0 if rdb_row.BPM else 0.0,
                            key=rdb_row.Key.ScaleName if rdb_row.Key else "",
                            rating=rdb_row.Rating or 0,
                            comment=rdb_row.Commnt or "",
                            color=COLOR_ID_TO_HEX.get(rdb_row.ColorID, "")
                        )
                        rid = self.fg_db.insert_content(fg_rec)

                        # Sync cues back to FableGear
                        rdb_track_cues = cues_by_content_id.get(rdb_row.ID, [])
                        if rdb_track_cues:
                            reconciled_cues = self._sync_track_cues(
                                rdb_row.ID, rdb_row.UUID, [], rdb_track_cues, rdb, dry_run, fg_content_id=rid
                            )
                            stats["cues_synchronized"] += reconciled_cues["synchronized"]
                            stats["details"].extend(reconciled_cues["details"])

                if not dry_run:
                    # Flush/commit SQLAlchemy session changes
                    rdb.session.commit()

        except Exception as e:
            log.error("Failed bidirectional Rekordbox synchronization: %s", e)
            stats["errors"].append(str(e))

        return stats

    def _sync_track_cues(
        self,
        rdb_content_id: str,
        rdb_content_uuid: str,
        fg_cues: List[CueRecord],
        rdb_cues: List[DjmdCue],
        rdb: Rekordbox6Database,
        dry_run: bool,
        fg_content_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Reconcile cue points for a single track between FableGear and Rekordbox databases.
        """
        cstats = {"synchronized": 0, "deleted": 0, "details": []}
        
        fg_cues_updated = list(fg_cues)
        fg_modified = False

        # Index FableGear cues
        fg_memory = []
        fg_hot = {}
        for cue in fg_cues:
            if cue.kind in (0, 3):  # memory
                fg_memory.append(cue)
            else:  # hot cue
                fg_hot[cue.slot] = cue

        # Index Rekordbox cues
        rdb_memory = []
        rdb_hot = {}
        for cue in rdb_cues:
            if cue.Kind == 0:  # memory
                rdb_memory.append(cue)
            elif cue.Kind > 0:  # hotcue
                slot = cue.Kind - 1
                rdb_hot[slot] = cue

        # --- Reconcile Memory Cues ---
        matched_rdb_mem = set()
        for fgc in fg_memory:
            match = None
            for rc in rdb_memory:
                if rc.ID not in matched_rdb_mem and abs(fgc.in_msec - rc.InMsec) <= 5:
                    match = rc
                    break

            if match:
                matched_rdb_mem.add(match.ID)
                # Check comment or loop properties update
                rdb_loop = match.OutMsec > 0 or match.ActiveLoop == 1
                fg_loop = fgc.kind == 3
                loop_mismatch = (fgc.out_msec or -1) != match.OutMsec if fg_loop else rdb_loop
                comment_mismatch = (fgc.comment or "") != (match.Comment or "")

                if loop_mismatch or comment_mismatch:
                    cstats["details"].append(f"Update memory cue at {fgc.in_msec}ms")
                    cstats["synchronized"] += 1
                    if not dry_run:
                        match.Comment = fgc.comment or ""
                        if fg_loop:
                            match.OutMsec = fgc.out_msec if fgc.out_msec is not None else -1
                            match.ActiveLoop = 1
                            match.Kind = 4
                        else:
                            match.OutMsec = -1
                            match.ActiveLoop = 0
                            match.Kind = 0
            else:
                # Add to Rekordbox
                cstats["details"].append(f"Add memory cue at {fgc.in_msec}ms to Rekordbox")
                cstats["synchronized"] += 1
                if not dry_run:
                    new_id = str(uuid.uuid4())
                    cue_row = DjmdCue(
                        ID=new_id,
                        ContentID=rdb_content_id,
                        ContentUUID=rdb_content_uuid,
                        UUID=str(uuid.uuid4()),
                        InMsec=fgc.in_msec,
                        OutMsec=fgc.out_msec if fgc.kind == 3 and fgc.out_msec is not None else -1,
                        InFrame=0, InMpegFrame=0, InMpegAbs=0, OutFrame=0, OutMpegFrame=0, OutMpegAbs=0,
                        Kind=4 if fgc.kind == 3 else 0,
                        Color=-1, ColorTableIndex=0,
                        ActiveLoop=1 if fgc.kind == 3 else 0,
                        Comment=fgc.comment or "",
                        BeatLoopSize=0, CueMicrosec=0, InPointSeekInfo="", OutPointSeekInfo="",
                        usn=0, rb_local_usn=0
                    )
                    rdb.session.add(cue_row)

        # Cues in Rekordbox but not matched -> import back to FableGear
        unmatched_rdb_mem = [rc for rc in rdb_memory if rc.ID not in matched_rdb_mem]
        for rc in unmatched_rdb_mem:
            cstats["details"].append(f"Sync memory cue at {rc.InMsec}ms to FableGear")
            cstats["synchronized"] += 1
            fg_modified = True
            fg_cues_updated.append(CueRecord(
                kind=3 if (rc.ActiveLoop == 1 or rc.OutMsec > 0) else 0,
                in_msec=rc.InMsec,
                out_msec=rc.OutMsec if rc.OutMsec > 0 else None,
                comment=rc.Comment
            ))

        # --- Reconcile Hot Cues ---
        for slot in range(8):
            fgc = fg_hot.get(slot)
            rc = rdb_hot.get(slot)

            if fgc and rc:
                # Both have pad slot: reconcile properties
                fg_loop = fgc.kind == 2
                rdb_loop = rc.OutMsec > 0
                loop_mismatch = (fgc.out_msec or -1) != rc.OutMsec if fg_loop else rdb_loop
                comment_mismatch = (fgc.comment or "") != (rc.Comment or "")
                
                rc_color = f"#{rc.Color & 0xFFFFFF:06X}" if rc.Color != -1 else ""
                color_mismatch = fgc.color and fgc.color.lower() != rc_color.lower()

                if fgc.in_msec != rc.InMsec or loop_mismatch or comment_mismatch or color_mismatch:
                    cstats["details"].append(f"Update hotcue slot {slot} (pad {chr(65+slot)})")
                    cstats["synchronized"] += 1
                    if not dry_run:
                        rc.InMsec = fgc.in_msec
                        rc.Comment = fgc.comment or ""
                        if fgc.color:
                            try:
                                rc.Color = int(fgc.color.lstrip("#"), 16)
                            except Exception:
                                pass
                        else:
                            rc.Color = -1
                        
                        if fg_loop:
                            rc.OutMsec = fgc.out_msec if fgc.out_msec is not None else -1
                            rc.Kind = 4
                        else:
                            rc.OutMsec = -1
                            rc.Kind = slot + 1
            elif fgc:
                # Add to Rekordbox
                cstats["details"].append(f"Add hotcue slot {slot} to Rekordbox")
                cstats["synchronized"] += 1
                if not dry_run:
                    new_id = str(uuid.uuid4())
                    cue_color = -1
                    if fgc.color:
                        try:
                            cue_color = int(fgc.color.lstrip("#"), 16)
                        except Exception:
                            pass
                    
                    cue_row = DjmdCue(
                        ID=new_id,
                        ContentID=rdb_content_id,
                        ContentUUID=rdb_content_uuid,
                        UUID=str(uuid.uuid4()),
                        InMsec=fgc.in_msec,
                        OutMsec=fgc.out_msec if fgc.kind == 2 and fgc.out_msec is not None else -1,
                        InFrame=0, InMpegFrame=0, InMpegAbs=0, OutFrame=0, OutMpegFrame=0, OutMpegAbs=0,
                        Kind=4 if fgc.kind == 2 else (slot + 1),
                        Color=cue_color, ColorTableIndex=0, ActiveLoop=0,
                        Comment=fgc.comment or "",
                        BeatLoopSize=0, CueMicrosec=0, InPointSeekInfo="", OutPointSeekInfo="",
                        usn=0, rb_local_usn=0
                    )
                    rdb.session.add(cue_row)
            elif rc:
                # Sync hotcue to FableGear
                cstats["details"].append(f"Sync hotcue slot {slot} to FableGear")
                cstats["synchronized"] += 1
                fg_modified = True
                fg_cues_updated.append(CueRecord(
                    kind=2 if rc.OutMsec > 0 else 1,
                    slot=slot,
                    in_msec=rc.InMsec,
                    out_msec=rc.OutMsec if rc.OutMsec > 0 else None,
                    comment=rc.Comment,
                    color=f"#{rc.Color & 0xFFFFFF:06X}" if rc.Color != -1 else ""
                ))

        if fg_modified and not dry_run and fg_content_id is not None:
            self.fg_db.bulk_upsert_cues(fg_content_id, fg_cues_updated)

        return cstats
