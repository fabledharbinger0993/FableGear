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

## Phase B — Deep read (IN PROGRESS — partial, honestly scoped)

`chop_shop/anlz_reader.py` + `chop_shop/pioneer_settings.py` +
`chop_shop/devicesql_reader.py` + `chop_shop/export_auditor.py`, wired at
`cli.py anlz-read / pioneer-settings / pdb-read / export-audit`. Read-only,
persists through `FableGearDatabase.log_operation` / `bulk_log_operations`.

What's demonstrated (byte layout confirmed against
`docs/format_samples/usb_format_inspection.md`'s real numbers, not guessed):
ANLZ `PPTH` (embedded path) and `PQTZ` (full beat grid) decode; `PWV6` /
`PWV7` / `PWVC` (3-band waveform) header fields decode (pixel data itself is
intentionally not interpreted); the DeviceSQL PDB's full file header
(`page_size`, `num_tables`, `next_unused_page`, table pointer array), the
generic page/row-group/string-table walk, and the tracks-table row layout
(`id`, `artist_id`, `album_id`, `key_id`, `tempo`/bpm, `title`,
`analyze_path`, `file_path`) — all implemented and unit-tested against Deep
Symmetry's published DeviceSQL format spec
(https://djl-analysis.deepsymmetry.org/rekordbox-export-analysis/exports.html,
the same reference crate-digger/rekordcrate are built from), not guessed
from memory. See `chop_shop/devicesql_reader.py` module docstring for the
exact provenance of every offset. Pioneer settings files via pyrekordbox's
real parser for the four known filenames.

What's still open, and deliberately NOT faked:
- **The row walker IS now hardware-verified — via a real 13.4MB export.pdb
  found on a connected drive, not committed to this repo.** It correctly
  extracted 1,900+ real rows (titles, plausible BPMs, real ANLZ/file paths
  matching the drive's actual folder structure) and, in the process, caught
  and fixed two real bugs neither the synthetic tests nor an earlier,
  empty 41-page real sample had exposed: (1) an index page's entries must
  NOT be followed during a full-table scan — every entry in the real
  file's tracks-table index page pointed at a page already reachable via
  the table's own page chain, so following them in addition doubled every
  such row; (2) real, long-lived libraries contain some track_ids with
  multiple presence-marked rows (traced to real (page,offset) locations,
  most plausibly stale slots surviving edit-history reuse — the format
  never describes automatic compaction) — `read_pdb` reports these
  faithfully rather than silently deduplicating, since that's a policy
  choice for callers to make, not a format fact. See
  `chop_shop/devicesql_reader.py`'s HONESTY LIMIT for the full account.
  Not yet verified: the populated-index-page entry-decode path itself
  (unused now that full-scan doesn't call it) and artist/key name
  resolution (below). No fixture binary is committed to this repo — this
  was a one-time manual check, not something CI re-verifies — so a
  regression here wouldn't be caught automatically; committing a sanitized
  real sample (see below) would fix that.
- **artist/key NAME resolution is unimplemented.** The tracks table row
  only carries `artist_id`/`key_id` foreign keys (populated on
  `TrackRow`); resolving them to human-readable names requires walking the
  artists (type 2) and keys (type 5) tables, whose row layouts were not
  documented on the reference page consulted for the tracks table and are
  a natural next step using the same generic page/row-group walker.
- **PCOB/PCO2/PSSI (cues, song structure) are presence+size only.** No
  byte-level layout for these is verified against a fixture in this repo,
  so `anlz_reader.py` records their presence and tag size and stops there
  rather than inventing field offsets.
- **No real hardware binaries are committed.** `docs/format_samples/`
  contains only the `usb_format_inspection.md` report (redacted), not the
  underlying `.DAT`/`.EXT`/`.2EX`/`.pdb` settings files themselves.
  Every parser's unit tests run against synthetic, spec-derived byte
  buffers (clearly labeled as such in each test file) — they verify the
  parsing *logic*, not fidelity to real hardware output. The moment a
  sanitized real export is committed under `docs/format_samples/`, add a
  real-fixture integration test — this is now the single highest-value
  next step, since it's what would upgrade the row walker (and everything
  built on it, including any future Phase D writer) from "spec-verified"
  to actually trusted.
- OneLibrary (`exportLibrary.db`) schema dump is still not started — see
  original scope below.

Original Phase B scope (unstarted parts retained for reference):
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
