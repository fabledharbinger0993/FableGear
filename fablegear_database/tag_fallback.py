"""Key/BPM fallback from an audio file's own tags.

Shared by the OneLibrary writer (database row) and the exporter's ANLZ stage (beat grid),
so a track that was only tagged is treated the same by both. See fill_missing_from_tags.
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def fill_missing_from_tags(track) -> None:
    """Fill a track's missing key and BPM from the audio file's own tags.

    FableGear's library takes key/BPM from Rekordbox's database, so a track that was
    only tagged (Tag Tracks writes Iron's result into the file) has nothing in the
    library and would reach the USB with no key. The file is what gets copied to the
    stick, so whatever the file says should travel with it. The library value always
    wins: a key Rekordbox analysed is never overridden by a tag.

    In-memory only (the database row is not changed). Silent on failure: an unreadable
    file just means no fallback, never a failed export.
    """
    if track.key and track.bpm:
        return
    path = Path(track.file_path) if track.file_path else None
    if path is None or not path.is_file():
        return
    try:
        from anvil import read_fields

        fields = read_fields(path)
        file_key, file_bpm = fields.initial_key, fields.bpm
    except Exception as exc:
        # Anvil refuses some containers (e.g. ID3v2.2 tags); mutagen can still read them.
        log.debug("tag fallback: Anvil could not read %s (%s); trying mutagen", path, exc)
        file_key, file_bpm = mutagen_key_bpm(path)
    if not track.key and file_key:
        track.key = file_key
    if not track.bpm and file_bpm:
        track.bpm = file_bpm


def mutagen_key_bpm(path: Path) -> tuple[str | None, float | None]:
    """Read-only key/BPM from any tag mutagen understands. (None, None) on any failure."""
    try:
        from mutagen import File as MutagenFile

        try:
            tags = getattr(MutagenFile(str(path)), "tags", None)
        except Exception:
            tags = None
        if not tags:
            # Read an ID3 tag directly: independent of MPEG frame sync, and mutagen
            # upgrades v2.2 frame ids (TKE/TBP) to TKEY/TBPM on load.
            from mutagen.id3 import ID3

            tags = ID3(str(path))

        def text(*names: str) -> str | None:
            for name in names:
                try:
                    value = tags[name]
                except (KeyError, ValueError):
                    continue
                if hasattr(value, "text"):          # ID3 frame
                    value = value.text
                value = value[0] if isinstance(value, (list, tuple)) and value else value
                if isinstance(value, bytes):
                    value = value.decode("utf-8", "ignore")
                value = str(value).strip()
                if value:
                    return value
            return None

        key = text("TKEY", "initialkey", "----:com.apple.iTunes:initialkey")
        raw_bpm = text("TBPM", "bpm", "tmpo")
        try:
            bpm = float(raw_bpm) if raw_bpm else None
        except ValueError:
            bpm = None
        return key, bpm
    except Exception as exc:
        log.debug("tag fallback: mutagen could not read %s (%s)", path, exc)
        return None, None
