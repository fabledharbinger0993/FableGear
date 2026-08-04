"""
chop_shop.tag_cleaning — normalize messy tag values into clean folder/group keys.

Ported from the OSOS Discos label-organizer analysis. DJ libraries accumulate
decades of tag rot: labels that are really URLs (``beatport.com``), copyright
prefixes (``℗ 2019 Defected``), Camelot keys written into the artist field
(``8A``), genre words masquerading as labels, and the same label spelled five
ways. These helpers turn a raw tag string into either a clean canonical value or
``None`` (meaning "no usable value — route to the Unclassified bucket").

The cleaners are the derivation engine behind the Organize tool's choosable
grouping schemes (``--by label``, ``--by artist``, …): the folder name for each
level comes from ``clean_value(key, raw)``.

Design notes
------------
- **Junk → None, never a guess.** A value we can't trust becomes ``None`` so the
  caller can bucket it, rather than creating a garbage ``beatport.com/`` folder.
- **Merge maps are opt-in.** Library-specific consolidations (``tequila trax`` →
  ``TQL``) are real and useful, but they are *this library's* opinion, not a
  universal truth, so ``clean_label`` takes an optional ``merge_map`` instead of
  baking them in. Callers load a per-library map from config.
"""

from __future__ import annotations

import re

# Values that are present but meaningless — treat as "no value".
JUNK_VALUES = {
    "cdname", "unknown", "n/a", "n/a.", "0", "-", "none", "",
    "[no label]", "[no_label]", "no label", "no_label",
    "unknown artist", "unknown album", "various", "various artists",
}

# A label field that is really a URL/blog is junk (beatport.com, foo.blogspot.…).
_URL_PAT = re.compile(
    r"(\.(pw|net|com|org|io|fm|ru|de|uk|fr|pl|be|nl)$"
    r"|\.blogspot\.|^www\.|^https?://)", re.IGNORECASE
)

# Leading "℗ 2019 " / "© 2019 " copyright-year prefix to strip off a label.
_COPYRIGHT_YEAR = re.compile(r"^[℗©]\s*\d{4}\s+(.+)")

# Genre words that sometimes get dumped into the label field. Available for an
# opt-in stricter label filter; off by default to avoid rejecting real labels
# that are legitimately named after a genre (e.g. the "Techno" imprint).
GENRE_WORDS = {
    "dance", "blues", "house", "techno", "jazz", "soul", "funk", "rock", "pop",
    "electronic", "hip-hop", "hip hop", "r&b", "classical", "country", "reggae",
    "disco", "ambient", "instrumental", "a cappella", "vocal", "gospel",
    "latin", "world", "folk", "metal", "punk", "indie", "alternative",
}

# Camelot wheel keys (1A–12B), any case — reject when found in the artist field.
_CAMELOT = {f"{n}{side}" for n in range(1, 13) for side in ("A", "B")}


def _base_clean(raw) -> str | None:
    """Common first pass: stringify, strip, drop empty/junk sentinels."""
    if raw is None:
        return None
    val = str(raw).strip()
    if not val or val.lower() in JUNK_VALUES:
        return None
    return val


def clean_label(raw, *, merge_map: dict | None = None,
                reject_genre_words: bool = False) -> str | None:
    """Clean a record-label value into a canonical folder key, or ``None``.

    - Strips a leading ``℗/© YYYY`` copyright prefix.
    - Rejects URLs/blogs and ``(c)``/``℗``/``©`` boilerplate.
    - Applies an optional per-library ``merge_map`` (lowercased,
      underscores→spaces key) to consolidate spelling variants.
    """
    val = _base_clean(raw)
    if val is None:
        return None

    m = _COPYRIGHT_YEAR.match(val)
    if m:
        val = m.group(1).strip()

    if _URL_PAT.search(val):
        return None
    low = val.lower()
    if low.startswith("(c)") or val.startswith("℗") or val.startswith("©"):
        return None
    if reject_genre_words and low in GENRE_WORDS:
        return None

    if merge_map:
        canonical = merge_map.get(low.replace("_", " ").strip())
        if canonical:
            return canonical

    return val or None


def clean_artist(raw) -> str | None:
    """Clean an artist value, rejecting Camelot keys mis-tagged as the artist."""
    val = _base_clean(raw)
    if val is None:
        return None
    if val.upper() in _CAMELOT:
        return None
    return val


def clean_album(raw) -> str | None:
    """Clean an album value into a folder key, or ``None``."""
    return _base_clean(raw)


def clean_title(raw) -> str | None:
    """Clean a title value into a folder key, or ``None``."""
    return _base_clean(raw)


def clean_genre(raw) -> str | None:
    """Clean a genre value into a folder key, or ``None``."""
    return _base_clean(raw)


# Dispatch table so the organizer can derive any scheme key uniformly.
_CLEANERS = {
    "label": clean_label,
    "artist": clean_artist,
    "album": clean_album,
    "title": clean_title,
    "genre": clean_genre,
}


def clean_value(key: str, raw, *, merge_map: dict | None = None) -> str | None:
    """Clean ``raw`` for grouping scheme ``key``. Unknown keys pass through
    ``_base_clean``. ``merge_map`` is only consulted for the ``label`` key."""
    fn = _CLEANERS.get(key)
    if fn is clean_label:
        return clean_label(raw, merge_map=merge_map)
    if fn is not None:
        return fn(raw)
    return _base_clean(raw)
