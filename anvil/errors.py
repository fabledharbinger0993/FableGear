"""
fablegear / anvil / errors.py

Anvil's exception hierarchy.

Every failure mode a caller might reasonably branch on gets its own type.
This exists specifically to replace substring matching against error prose --
see fablegear_database/importer.py::_CORRUPT_ERROR_MARKERS, which today
classifies corruption by searching for the phrase "mutagen open failed" in a
message string. A wording change in a dependency can silently break that;
catching a type cannot.
"""

from __future__ import annotations


class AnvilError(Exception):
    """Base for every Anvil failure. Catch this to catch anything Anvil raises."""


class UnsupportedFormat(AnvilError):
    """
    The file is not a container Anvil handles.

    Distinct from CorruptHeader: this means "recognised the bytes, they are not
    ours", the equivalent of mutagen's File() returning None rather than raising.
    """


class CorruptHeader(AnvilError):
    """
    The container or tag structure is malformed beyond what Anvil will repair.

    Anvil is deliberately lenient about real-world encoder bugs (see the
    non-synchsafe frame size fallback in id3.py). This is raised only when the
    structure cannot be interpreted at all.
    """


class NoTagBlock(AnvilError):
    """The file is a supported container but carries no tag block to read."""


class WriteVerificationFailed(AnvilError):
    """
    A write completed but reading the value back did not return what was written.

    The original file is untouched when this raises -- verification happens
    after the atomic rename, so the failure means the new file is wrong, not
    that the old one was damaged.
    """
