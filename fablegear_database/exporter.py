"""
fablegear_database.exporter — Pioneer-compatible export functionality.

Exports FableGear database to Pioneer-compatible formats for:
- CDJ3000 compatibility
- Direct hardware communication
- Rekordbox interoperability
- Professional DJ workflow support
"""

import logging
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from datetime import datetime

from pyrekordbox import AnlzFile
from pyrekordbox.anlz import structs
from pyrekordbox.anlz.tags import PPTHAnlzTag, PQTZAnlzTag, PCOBAnlzTag, PCO2AnlzTag
from construct import Container

from .database import FableGearDatabase, ContentRecord

log = logging.getLogger(__name__)

# Patch pyrekordbox Construct models at runtime to fix duplicate key name errors
try:
    if hasattr(structs, "AnlzCuePoint") and structs.AnlzCuePoint.subcons[0].name == "type":
        structs.AnlzCuePoint.subcons[0].name = "tag_type"
    if hasattr(structs, "AnlzCuePoint2") and structs.AnlzCuePoint2.subcons[0].name == "type":
        structs.AnlzCuePoint2.subcons[0].name = "tag_type"
except Exception as e:
    log.warning("Could not patch pyrekordbox construct Struct definitions: %s", e)


class PioneerExporter:
    """
    Exports FableGear database to Pioneer-compatible formats.
    
    Enables CDJ3000 and other Pioneer hardware to work directly
    with FableGear databases, bypassing Rekordbox dependency.
    """
    
    def __init__(self, database: FableGearDatabase):
        """
        Initialize the Pioneer exporter.
        
        Args:
            database: FableGear database instance
        """
        self.database = database
    
    def export_to_pioneer_xml(self, output_path: Path) -> bool:
        """
        Export database to Pioneer XML format.
        
        Args:
            output_path: Path for output XML file
            
        Returns:
            True if export succeeded
        """
        try:
            # Get all content records
            records = self.database.get_all_content(limit=100000)
            
            # Create Pioneer-compatible XML structure
            xml_content = self._generate_pioneer_xml(records)
            
            # Write to file
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(xml_content)
            
            log.info("Exported %d records to Pioneer XML: %s", len(records), output_path)
            return True
            
        except Exception as exc:
            log.error("Failed to export to Pioneer XML: %s", exc)
            return False
    
    def export_to_rekordbox_db(self, output_path: Path) -> bool:
        """
        Export database to Rekordbox-compatible SQLite format.
        
        Args:
            output_path: Path for output database file
            
        Returns:
            True if export succeeded
        """
        try:
            # Create new database
            conn = sqlite3.connect(output_path)
            cursor = conn.cursor()
            
            # Enable foreign keys
            cursor.execute("PRAGMA foreign_keys = ON")
            
            # Create Rekordbox-compatible schema
            self._create_rekordbox_schema(cursor)
            
            # Export content
            records = self.database.get_all_content(limit=100000)
            for record in records:
                self._insert_rekordbox_content(cursor, record)
            
            conn.commit()
            conn.close()
            
            log.info("Exported %d records to Rekordbox DB: %s", len(records), output_path)
            return True
            
        except Exception as exc:
            log.error("Failed to export to Rekordbox DB: %s", exc)
            return False
    
    def export_playlists(self, output_path: Path) -> bool:
        """
        Export playlists to Pioneer-compatible format.
        
        Args:
            output_path: Path for output file
            
        Returns:
            True if export succeeded
        """
        try:
            # Get playlist data from database
            # This would need playlist tables in the schema
            # For now, create a placeholder
            
            playlist_data = {
                "version": "1.0",
                "exported_at": datetime.now().isoformat(),
                "playlists": [],
            }
            
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(playlist_data, f, indent=2)
            
            log.info("Exported playlists to: %s", output_path)
            return True
            
        except Exception as exc:
            log.error("Failed to export playlists: %s", exc)
            return False
    
    def _generate_pioneer_xml(self, records: List[ContentRecord]) -> str:
        """
        Generate Pioneer-compatible XML from records.
        
        Args:
            records: List of ContentRecords
            
        Returns:
            XML string
        """
        xml_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<DJ_PLAYLISTS>',
            '  <VERSION>1.0</VERSION>',
            '  <PRODUCT NAME="FableGear" VERSION="1.0"/>',
            '  <COLLECTION>',
        ]
        
        for record in records:
            xml_lines.append(f'    <TRACK Location="{record.file_path}">')
            if record.title:
                xml_lines.append(f'      <TITLE>{record.title}</TITLE>')
            if record.artist:
                xml_lines.append(f'      <ARTIST>{record.artist}</ARTIST>')
            if record.album:
                xml_lines.append(f'      <ALBUM>{record.album}</ALBUM>')
            if record.bpm:
                xml_lines.append(f'      <BPM>{record.bpm}</BPM>')
            if record.key:
                xml_lines.append(f'      <KEY>{record.key}</KEY>')
            if record.duration:
                xml_lines.append(f'      <TOTALTIME>{record.duration}</TOTALTIME>')
            xml_lines.append('    </TRACK>')
        
        xml_lines.extend([
            '  </COLLECTION>',
            '</DJ_PLAYLISTS>',
        ])
        
        return "\n".join(xml_lines)
    
    def _create_rekordbox_schema(self, cursor: sqlite3.Cursor) -> None:
        """
        Create Rekordbox-compatible database schema.
        
        Args:
            cursor: SQLite cursor
        """
        # Create djmdContent table (simplified version)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS djmdContent (
                ID INTEGER PRIMARY KEY AUTOINCREMENT,
                FolderPath TEXT,
                Title TEXT,
                Artist TEXT,
                Album TEXT,
                AverageBpm REAL,
                Tonality TEXT,
                Length REAL,
                ImagePath TEXT
            )
        """)
        
        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_folder_path ON djmdContent(FolderPath)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_title ON djmdContent(Title)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_artist ON djmdContent(Artist)")
    
    def _insert_rekordbox_content(self, cursor: sqlite3.Cursor, record: ContentRecord) -> None:
        """
        Insert record into Rekordbox-compatible format.
        
        Args:
            cursor: SQLite cursor
            record: ContentRecord to insert
        """
        cursor.execute("""
            INSERT INTO djmdContent (
                FolderPath, Title, Artist, Album, AverageBpm, Tonality, Length
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            record.file_path,
            record.title,
            record.artist,
            record.album,
            record.bpm,
            record.key,
            record.duration,
        ))

    def export_track_anlz(self, content_id: int, target_root: Path, relative_audio_path: str, device_content_id: Optional[int] = None) -> bool:
        """
        Generate and save Pioneer compatible ANLZ (.DAT and .EXT) files for a track.
        
        Args:
            content_id: Track database ID
            target_root: Target directory of the export (e.g. USB mount point)
            relative_audio_path: The relative path of the track on the device
            device_content_id: Optional track ID on the destination device (for ANLZ folder naming)
            
        Returns:
            True if files were successfully created
        """
        try:
            # 1. Fetch content record with relations
            tracks = self.database.get_content_with_relations([content_id])
            if not tracks:
                log.error("Track with ID %d not found in database", content_id)
                return False
            track = tracks[0]

            # 2. Derive export paths
            # Subdirectory path: PIONEER/USBANLZ/P{prefix}/{hex_id}/
            path_id = device_content_id if device_content_id is not None else content_id
            sub_dir1 = f"P{path_id // 2048:03d}"
            sub_dir2 = f"{path_id:08X}"
            anlz_dir = target_root / "PIONEER" / "USBANLZ" / sub_dir1 / sub_dir2
            anlz_dir.mkdir(parents=True, exist_ok=True)

            dat_path = anlz_dir / "ANLZ0000.DAT"
            ext_path = anlz_dir / "ANLZ0000.EXT"

            # 3. Generate ANLZ0000.DAT
            dat_file = AnlzFile()
            dat_file.file_header = Container(
                type="PMAI",
                len_header=28,
                len_file=28,
                u1=1, u2=0, u3=0, u4=0
            )

            # A. PPTH Tag
            pth_data = structs.AnlzTag.build({
                'type': 'PPTH',
                'len_header': 16,
                'len_tag': 18,
                'content': {'len_path': 2, 'path': ''}
            })
            pth_tag = PPTHAnlzTag(pth_data)
            pth_tag.set(relative_audio_path)
            dat_file.tags.append(pth_tag)

            # B. PQTZ Beat Grid Tag
            if track.beatgrid:
                entries = []
                for b in track.beatgrid:
                    entries.append({
                        'beat': b.beat_number,
                        'tempo': int(round(b.bpm * 100)),
                        'time': int(b.time_msec)
                    })
                qtz_data = structs.AnlzTag.build({
                    'type': 'PQTZ',
                    'len_header': 24,
                    'len_tag': 24 + 8 * len(entries),
                    'content': {
                        'entry_count': len(entries),
                        'entries': entries
                    }
                })
                qtz_tag = PQTZAnlzTag(qtz_data)
                dat_file.tags.append(qtz_tag)

            # C. PCOB Tags (Memory cues & Hot cues separate)
            memory_cues = [c for c in track.cues if c.kind in (0, 3)] # Memory or Active Loop
            hot_cues = [c for c in track.cues if c.kind in (1, 2)] # Hot Cue or Loop
            
            if memory_cues:
                mc_entries = []
                for cue in memory_cues:
                    kind_type = 'loop' if cue.kind == 3 else 'single'
                    mc_entries.append({
                        'len_header': 12,
                        'len_entry': 56,
                        'hot_cue': 0,
                        'status': 'enabled',
                        'order_first': 0xffff,
                        'order_last': 0xffff,
                        'type': kind_type,
                        'time': cue.in_msec,
                        'loop_time': cue.out_msec if cue.out_msec is not None else 0xFFFFFFFF
                    })
                mc_data = structs.AnlzTag.build({
                    'type': 'PCOB',
                    'len_header': 24,
                    'len_tag': 24 + 56 * len(mc_entries),
                    'content': {
                        'cue_type': 'memory',
                        'unk': 0,
                        'count': len(mc_entries),
                        'memory_count': len(mc_entries),
                        'entries': mc_entries
                    }
                })
                dat_file.tags.append(PCOBAnlzTag(mc_data))

            if hot_cues:
                hc_entries = []
                for cue in hot_cues:
                    kind_type = 'loop' if cue.kind == 2 else 'single'
                    slot = cue.slot if cue.slot is not None else 0
                    hc_entries.append({
                        'len_header': 12,
                        'len_entry': 56,
                        'hot_cue': slot + 1,
                        'status': 'enabled',
                        'order_first': 0xffff,
                        'order_last': 0xffff,
                        'type': kind_type,
                        'time': cue.in_msec,
                        'loop_time': cue.out_msec if cue.out_msec is not None else 0xFFFFFFFF
                    })
                hc_data = structs.AnlzTag.build({
                    'type': 'PCOB',
                    'len_header': 24,
                    'len_tag': 24 + 56 * len(hc_entries),
                    'content': {
                        'cue_type': 'hotcue',
                        'unk': 0,
                        'count': len(hc_entries),
                        'memory_count': -1,
                        'entries': hc_entries
                    }
                })
                dat_file.tags.append(PCOBAnlzTag(hc_data))

            dat_file.update_len()
            dat_file.save(dat_path)

            # 4. Generate ANLZ0000.EXT
            ext_file = AnlzFile()
            ext_file.file_header = Container(
                type="PMAI",
                len_header=28,
                len_file=28,
                u1=1, u2=0, u3=0, u4=0
            )

            # A. PPTH Tag
            pth_data_ext = structs.AnlzTag.build({
                'type': 'PPTH',
                'len_header': 16,
                'len_tag': 18,
                'content': {'len_path': 2, 'path': ''}
            })
            pth_tag_ext = PPTHAnlzTag(pth_data_ext)
            pth_tag_ext.set(relative_audio_path)
            ext_file.tags.append(pth_tag_ext)

            # B. PCO2 Tags (Memory cues & Hot cues separate)
            if memory_cues:
                mc2_entries = []
                for cue in memory_cues:
                    kind_type = 2 if cue.kind == 3 else 1 # 2=loop, 1=point
                    comment = cue.comment or ""
                    comment_bytes = comment.encode("utf-16-be")
                    len_comment = len(comment_bytes)
                    len_entry = 96 + len_comment # 96 is a safe baseline entry size
                    
                    r, g, b = 0, 0, 0
                    color_code = 0
                    if cue.color:
                        color_code = 0x40
                        try:
                            c = cue.color.lstrip("#")
                            r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
                        except Exception:
                            pass

                    mc2_entries.append({
                        'len_header': 12,
                        'len_entry': len_entry,
                        'hot_cue': 0,
                        'type': kind_type,
                        'time': cue.in_msec,
                        'loop_time': cue.out_msec if cue.out_msec is not None else 0xFFFFFFFF,
                        'color_id': 0,
                        'loop_enumerator': 0,
                        'loop_denominator': 0,
                        'len_comment': len_comment,
                        'comment': comment,
                        'color_code': color_code,
                        'color_red': r,
                        'color_green': g,
                        'color_blue': b
                    })
                mc2_data = structs.AnlzTag.build({
                    'type': 'PCO2',
                    'len_header': 20,
                    'len_tag': 20 + sum(e['len_entry'] for e in mc2_entries),
                    'content': {
                        'type': 'memory',
                        'count': len(mc2_entries),
                        'unknown': 0,
                        'entries': mc2_entries
                    }
                })
                ext_file.tags.append(PCO2AnlzTag(mc2_data))

            if hot_cues:
                hc2_entries = []
                for cue in hot_cues:
                    kind_type = 2 if cue.kind == 2 else 1
                    slot = cue.slot if cue.slot is not None else 0
                    comment = cue.comment or ""
                    comment_bytes = comment.encode("utf-16-be")
                    len_comment = len(comment_bytes)
                    len_entry = 96 + len_comment
                    
                    r, g, b = 0, 0, 0
                    color_code = 0
                    if cue.color:
                        color_code = 0x40
                        try:
                            c = cue.color.lstrip("#")
                            r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
                        except Exception:
                            pass

                    hc2_entries.append({
                        'len_header': 12,
                        'len_entry': len_entry,
                        'hot_cue': slot + 1,
                        'type': kind_type,
                        'time': cue.in_msec,
                        'loop_time': cue.out_msec if cue.out_msec is not None else 0xFFFFFFFF,
                        'color_id': 0,
                        'loop_enumerator': 0,
                        'loop_denominator': 0,
                        'len_comment': len_comment,
                        'comment': comment,
                        'color_code': color_code,
                        'color_red': r,
                        'color_green': g,
                        'color_blue': b
                    })
                hc2_data = structs.AnlzTag.build({
                    'type': 'PCO2',
                    'len_header': 20,
                    'len_tag': 20 + sum(e['len_entry'] for e in hc2_entries),
                    'content': {
                        'type': 'hotcue',
                        'count': len(hc2_entries),
                        'unknown': 0,
                        'entries': hc2_entries
                    }
                })
                ext_file.tags.append(PCO2AnlzTag(hc2_data))

            ext_file.update_len()
            ext_file.save(ext_path)

            log.info("Successfully generated ANLZ files for content %d at %s", content_id, anlz_dir)
            return True

        except Exception as exc:
            log.error("Failed to generate Pioneer ANLZ files for content %d: %s", content_id, exc)
            return False


class PioneerHandshake:
    """
    Pioneer hardware communication layer.
    
    Enables direct communication with CDJ3000 and other Pioneer
    hardware for library browsing and playback without Rekordbox.
    """
    
    def __init__(self, database: FableGearDatabase):
        """
        Initialize the Pioneer handshake.
        
        Args:
            database: FableGear database instance
        """
        self.database = database
        self._connected = False
    
    def connect(self, device_address: str) -> bool:
        """
        Connect to Pioneer hardware device.
        
        Args:
            device_address: Network address or USB identifier
            
        Returns:
            True if connection succeeded
        """
        # This would implement actual Pioneer communication protocol
        # For now, placeholder
        log.info("Attempting to connect to Pioneer device: %s", device_address)
        self._connected = True
        return True
    
    def disconnect(self) -> None:
        """Disconnect from Pioneer hardware."""
        if self._connected:
            log.info("Disconnecting from Pioneer device")
            self._connected = False
    
    def send_library_data(self, limit: int = 1000) -> bool:
        """
        Send library data to connected Pioneer device.
        
        Args:
            limit: Maximum number of tracks to send
            
        Returns:
            True if send succeeded
        """
        if not self._connected:
            log.error("Not connected to Pioneer device")
            return False
        
        try:
            # Get records from database
            records = self.database.get_all_content(limit=limit)
            
            # Convert to Pioneer protocol format
            # This would implement the actual Pioneer communication protocol
            log.info("Sending %d tracks to Pioneer device", len(records))
            
            return True
            
        except Exception as exc:
            log.error("Failed to send library data: %s", exc)
            return False
    
    def receive_playback_data(self) -> Optional[Dict[str, Any]]:
        """
        Receive playback data from Pioneer device.
        
        Returns:
            Dictionary with playback data or None
        """
        if not self._connected:
            log.error("Not connected to Pioneer device")
            return None
        
        # This would implement actual Pioneer data reception
        # For now, placeholder
        return {
            "playing": False,
            "track_id": None,
            "position": 0,
            "bpm": 0,
        }