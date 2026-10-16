"""
fablegear / quench / errors.py

Quench's exception hierarchy. Same convention as anvil/errors.py and iron/errors.py: every
failure mode a caller might reasonably branch on gets its own type, so classification never
depends on matching substrings in a message.
"""

from __future__ import annotations


class QuenchError(Exception):
    """Base for every Quench failure. Catch this to catch anything Quench raises."""


class UnsupportedFormat(QuenchError):
    """Quench has no lossless-or-equivalent rewrite recipe for this container (e.g. AAC/M4A)."""


class MeasureFailed(QuenchError):
    """ffmpeg's loudnorm pass produced no usable integrated loudness (corrupt, silent, unreadable)."""


class NotEnoughSpace(QuenchError):
    """The filesystem cannot hold the working copy a safe rewrite needs."""


class RewriteFailed(QuenchError):
    """ffmpeg failed to write the levelled file, or the output did not verify."""
