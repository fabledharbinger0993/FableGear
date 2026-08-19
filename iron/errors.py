"""
fablegear / iron / errors.py

Iron's exception hierarchy. Same convention as anvil/errors.py: every failure mode a caller
might reasonably branch on gets its own type, so classification never depends on matching
substrings in a message.
"""

from __future__ import annotations


class IronError(Exception):
    """Base for every Iron failure. Catch this to catch anything Iron raises."""


class UnsupportedFormat(IronError):
    """The file's extension is not one Iron attempts to decode."""


class DecodeFailed(IronError):
    """The audio could not be decoded (corrupt file, unreadable stream, ffmpeg failure)."""
