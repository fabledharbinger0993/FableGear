# -*- coding: utf-8 -*-
# Author: FableGear (Claude + Marshall Guthrie)
# Date:   2026-07-17
"""
fablegear_database.device_identity — small companion files that sit
alongside ``exportLibrary.db`` on a real Pioneer device tree.

Two files, two very different confidence levels:

``RBFLTR.DAT`` (``PIONEER/rekordbox/RBFLTR.DAT``)
    A small binary file embedding plaintext device/firmware identity
    ("PIONEER DJ", "CDJ-3000", a firmware version string) plus four
    structured "FCND" records whose byte-level meaning is NOT publicly
    documented anywhere found (checked directly — see HONESTY LIMIT).
    Because its role is device-CLASS boilerplate (the same CDJ-3000 target
    produces the same bytes regardless of library content — verified by
    finding byte-identical copies of this file's structure pattern
    described the same way across the campaign's real samples), it is
    copied VERBATIM from a real, working device export rather than
    regenerated field-by-field. Same principle as the menuItem/category/
    sort boilerplate in onelibrary_writer.py: don't decode what you don't
    need to, don't guess at what you can't verify.

``djprofile.nxs`` (``PIONEER/djprofile.nxs``)
    A tiny (160-byte) device/DJ profile file. Directly inspected (hex
    dump) on a real device: a header of unknown-but-fixed meaning, then a
    16-byte slot at a fixed offset holding a null-terminated ASCII display
    name (a 15-character owner name plus the terminator on the real
    sample), then zero padding to the full 160 bytes. This is
    understood well enough to regenerate: the name slot's location and
    width are directly observed, and every other byte is preserved
    unchanged from the working sample. The 16-byte width is kept FIXED
    (truncate/pad, never resize) specifically so this module never has to
    know whether any header byte encodes a length dependent on the name —
    if one does, keeping the slot width constant means it's never wrong.

HONESTY LIMIT: neither of these has been decoded from a public spec, and
neither output has been tested on physical Pioneer hardware. RBFLTR.DAT is
byte-identical to a real working sample (as strong a claim as this module
can honestly make); djprofile.nxs preserves every byte except the name
slot, whose location/width were directly observed, not guessed.

Public interface:
    write_rbfltr(pioneer_root) -> Path
    write_dj_profile(pioneer_root, display_name) -> Path
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Captured verbatim, read-only, from a real, working PIONEER/rekordbox/
# RBFLTR.DAT on a CDJ-3000-formatted device export (not committed as a
# binary fixture — see docs/format_samples/). 232 bytes.
_RBFLTR_BYTES = bytes.fromhex(
    "464d4149000000740000 00e800000000".replace(" ", "")
    + "5049 4f4e 4545 5220 444a 2020 2020 2020".replace(" ", "")
    + "2020 2020 2020 2020 2020 2020 2020 2020".replace(" ", "")
    + "4344 4a2d 3330 3030 2020 2020 2020 2020".replace(" ", "")
    + "2020 2020 2020 2020 2020 2020 2020 2020".replace(" ", "")
    + "332e 3031 2020 2020 2020 2020 2020 2020".replace(" ", "")
    + "2020 2020 2020 2020 2020 2020 2020 2020".replace(" ", "")
    + "0000 0004 4643 4e44 0000 0014 0000 001c".replace(" ", "")
    + "0000 0006 0006 0002 0000 2fbc 0000 35d4".replace(" ", "")
    + "4643 4e44 0000 0014 0000 0024 0000 000c".replace(" ", "")
    + "0011 0004 0000 0010 0000 000f 0000 000e".replace(" ", "")
    + "0000 0012 4643 4e44 0000 0014 0000 001c".replace(" ", "")
    + "0000 0007 0000 0002 0000 0003 0000 0000".replace(" ", "")
    + "4643 4e44 0000 0014 0000 0018 0000 000f".replace(" ", "")
    + "0011 0001 0000 0000".replace(" ", "")
)
assert len(_RBFLTR_BYTES) == 232, f"RBFLTR template is {len(_RBFLTR_BYTES)} bytes, expected 232"

# djprofile.nxs: 160 bytes total. Bytes [0x00:0x20) are an unexplained but
# fixed header (preserved verbatim from the real sample). The name slot is
# exactly 16 bytes at [0x20:0x30) — null-terminated ASCII, zero-padded.
# Everything from 0x30 onward is zero padding in the real sample.
_DJPROFILE_HEADER = bytes.fromhex(
    "002e29050000017e0d4ef2a500000000"
    "000000000000000000000000" "12782223"
)
assert len(_DJPROFILE_HEADER) == 0x20, f"djprofile header is {len(_DJPROFILE_HEADER)} bytes, expected 32"

_DJPROFILE_TOTAL_SIZE = 160
_DJPROFILE_NAME_SLOT = 16
_DJPROFILE_NAME_MAX_CHARS = _DJPROFILE_NAME_SLOT - 1  # room for the null terminator


def write_rbfltr(pioneer_root: Path) -> Path:
    """
    Write PIONEER/rekordbox/RBFLTR.DAT as a byte-identical copy of a real,
    working CDJ-3000 device export's file.

    Parameters
    ----------
    pioneer_root : Path
        The PIONEER/ directory root (i.e. target_root/PIONEER, not the USB
        mount root itself).

    Returns
    -------
    Path
        The written file's path.
    """
    target = Path(pioneer_root) / "rekordbox" / "RBFLTR.DAT"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_RBFLTR_BYTES)
    log.info("Wrote RBFLTR.DAT (device-identity template, verbatim) to %s", target)
    return target


def write_dj_profile(pioneer_root: Path, display_name: str = "FableGear") -> Path:
    """
    Write PIONEER/djprofile.nxs with display_name in the observed 16-byte
    name slot, preserving every other byte from a real working sample.

    display_name is encoded as ASCII and truncated to 15 characters (the
    slot is 16 bytes including the null terminator) if longer — silently
    truncating rather than raising, since this is cosmetic device-profile
    text, not anything structurally load-bearing.

    Parameters
    ----------
    pioneer_root : Path
        The PIONEER/ directory root (i.e. target_root/PIONEER).
    display_name : str
        Name to show in the device's DJ profile slot. Defaults to
        "FableGear" (9 characters — well within the 15-character slot)
        rather than leaving the real sample's original owner name in
        place. Set your own with the CLI's --dj-name.

    Returns
    -------
    Path
        The written file's path.
    """
    name_bytes = display_name.encode("ascii", errors="replace")[:_DJPROFILE_NAME_MAX_CHARS]
    name_slot = name_bytes + b"\x00" * (_DJPROFILE_NAME_SLOT - len(name_bytes))

    body = _DJPROFILE_HEADER + name_slot
    body += b"\x00" * (_DJPROFILE_TOTAL_SIZE - len(body))

    target = Path(pioneer_root) / "djprofile.nxs"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    log.info("Wrote djprofile.nxs (display name: %r) to %s", display_name, target)
    return target
