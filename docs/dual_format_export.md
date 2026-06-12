# Dual-Format USB Export — Campaign Roadmap

**Goal:** one stick, prepared by FableGear, that boots on the entire mixed
fleet — original CDJ-3000s (DeviceSQL) *and* OMNIS-DUO-era OneLibrary
devices — without rekordbox in the loop. No open tool does this today.
This is the project's flagship contribution to the open DJ-tooling
ecosystem, in the spirit of pyrekordbox.

**Honesty header:** this is a reverse-engineering campaign, not a feature.
Each phase below is labeled by evidence level. Nothing advances to "write"
until its "read" phase is verified against real hardware. Probe first,
write later.

---

## The format split

| | DeviceSQL | OneLibrary |
|---|---|---|
| File | `PIONEER/rekordbox/export.pdb` | `exportLibrary.db` |
| Container | Custom binary page DB | SQLite |
| Devices | CDJ-3000, XDJ, 2000NXS2 era | OMNIS-DUO and newer |
| Open *read* support | crate-digger (Java), rekordcrate (Rust) — community RE | SQLite tooling reads the container; schema knowledge is partial |
| Open *write* support | **None known** | **None known** |
| pyrekordbox support | None (parses master.db, not export.pdb) | None |

Both formats also require a correct **ANLZ tree** (`PIONEER/USBANLZ/…` —
beat grids, waveforms, cues) and benefit from settings files. pyrekordbox
*can* parse ANLZ and MySettings — that part of the stack is already open.

---

## Phase A — Recognize & validate (SHIPPED, demonstrated)

`chop_shop/usb_inspector.py` + `cli.py usb-inspect <mount>`.
Read-only: detects both formats, validates the DeviceSQL header against
community-documented structure, opens OneLibrary candidates read-only,
counts ANLZ tracks, renders a fleet-compatibility verdict.

**Next action for Marshall:** run `usb-inspect` against a rekordbox-prepared
stick from each device family and commit the (sanitized) reports to
`docs/format_samples/`. Real-hardware ground truth anchors every later phase.

## Phase B — Deep read (achievable, weeks)

1. OneLibrary schema dump: `sqlite3 exportLibrary.db .schema` from a real
   export; map tables to master.db concepts (content, playlists, cues).
   *SQLite makes this mostly archaeology, not cryptanalysis.*
2. DeviceSQL full parse: port or wrap rekordcrate/crate-digger table
   walking, or implement the documented page/row format in Python.
   Verify by diffing parsed output against the same library's master.db.

## Phase C — Write OneLibrary (achievable, the strategic beachhead)

SQLite is writable with standard tooling once the schema and invariants
are mapped. Strategy: **template + transform** — start from a known-good
rekordbox-produced `exportLibrary.db`, clone its schema, populate from
master.db via pyrekordbox, byte-compare structure against a rekordbox
export of the same playlist set, then hardware-test on the OMNIS-DUO.
Risk: hidden integrity fields (hashes, version stamps). The diff harness
exists precisely to surface them.

## Phase D — Write DeviceSQL (hard, the summit)

From-scratch PDB writing is the hardest step. Two strategies, in order:

1. **Template patching:** take a rekordbox-written `export.pdb` and learn
   the minimal byte-level deltas for adding/removing tracks. Narrower than
   a full writer; may be enough for FableGear's real use case (refreshing
   an existing gig stick).
2. **Full writer:** implement page allocation, row groups, and string
   tables per the crate-digger documentation. Verify with rekordcrate as
   an independent parser before any hardware test.

Hardware testing protocol for C and D: sacrificial USB stick, never the
gig stick; verify on one player before the fleet; keep a rekordbox-made
control stick at every gig until trust is earned.

## Phase E — ANLZ + assembly (mostly open already)

Generate/copy ANLZ trees (pyrekordbox parses these; FableGear already
computes beat/key data), write settings files, assemble the full PIONEER
tree, and extend the existing USB Export modal with a format selector:
**CDJ-3000 / OneLibrary / Dual**.

---

## Standing on shoulders (and crediting them)

crate-digger & dysentery (Deep Symmetry / James Elliott), rekordcrate
(Jan Holthuis), pyrekordbox (Dylan Jones). FableGear's contribution is
the missing piece — open *writers* — and every phase should upstream
format discoveries back to those projects' documentation where welcome.
