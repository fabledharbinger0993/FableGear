"""Key/BPM fallback from an audio file's own tags.

Shared by the OneLibrary writer (database row) and the exporter's ANLZ stage (beat grid),
so a track that was only tagged is treated the same by both. See fill_missing_from_tags.
"""

import logging
import math
from pathlib import Path

log = logging.getLogger(__name__)

# A tag is outside input we do not control, so it is validated before it can reach the export.
MIN_BPM, MAX_BPM = 20.0, 400.0
MAX_KEY_LEN = 20

# One export touches each file twice (database row, then beat grid). Remember what a file said,
# keyed by identity (path, mtime, size) so an edited file is re-read. Bounded so a huge library
# cannot grow it without limit.
_CACHE: dict[tuple[str, int, int], tuple[str | None, float | None]] = {}
_CACHE_MAX = 20000


def valid_bpm(value) -> float | None:
    """The BPM as a float if it is a plausible tempo, else None (NaN, inf, 0, 9999, text)."""
    try:
        bpm = float(value)
    except (TypeError, ValueError):
        return None
    return bpm if math.isfinite(bpm) and MIN_BPM <= bpm <= MAX_BPM else None


def valid_key(value) -> str | None:
    """The key text if it is short and non-empty, else None."""
    if value is None:
        return None
    key = str(value).strip()
    return key if key and len(key) <= MAX_KEY_LEN else None


def _read_tags(path: Path) -> tuple[str | None, float | None]:
    """(key, bpm) from the file's tags: Anvil first, mutagen for whatever Anvil did not give."""
    key = bpm = None
    try:
        from anvil import read_fields

        fields = read_fields(path)
        key, bpm = valid_key(fields.initial_key), valid_bpm(fields.bpm)
    except Exception as exc:
        # Anvil refuses some containers (e.g. ID3v2.2 tags) and does not know every tag spelling.
        log.debug("tag fallback: Anvil could not read %s (%s)", path, exc)
    if key is None or bpm is None:
        m_key, m_bpm = mutagen_key_bpm(path)
        key = key if key is not None else valid_key(m_key)
        bpm = bpm if bpm is not None else valid_bpm(m_bpm)
    return key, bpm


def fill_missing_from_tags(track) -> None:
    """Fill a track's missing key and BPM from the audio file's own tags.

    FableGear's library takes key/BPM from Rekordbox's database, so a track that was
    only tagged (Forge writes Iron's result into the file) has nothing in the
    library and would reach the USB with no key. The file is what gets copied to the
    stick, so whatever the file says should travel with it. The library value always
    wins: a key Rekordbox analysed is never overridden by a tag.

    In-memory only (the database row is not changed). Silent on failure: an unreadable
    file just means no fallback, never a failed export. Implausible tag values (a BPM of
    NaN, 0 or 9999, an over-long key) are ignored rather than exported.
    """
    if track.key and track.bpm:
        return
    path = Path(track.file_path) if track.file_path else None
    if path is None or not path.is_file():
        return
    try:
        st = path.stat()
        ident = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return
    if ident not in _CACHE:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[ident] = _read_tags(path)
    file_key, file_bpm = _CACHE[ident]
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

        key = text("TKEY", "TXXX:INITIALKEY", "TXXX:KEY", "initialkey", "key",
                   "----:com.apple.iTunes:initialkey")
        raw_bpm = text("TBPM", "TXXX:BPM", "bpm", "tmpo")
        return key, valid_bpm(raw_bpm)
    except Exception as exc:
        log.debug("tag fallback: mutagen could not read %s (%s)", path, exc)
        return None, None
