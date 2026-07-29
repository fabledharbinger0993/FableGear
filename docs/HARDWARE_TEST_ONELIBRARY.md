# Hardware test — does a CDJ-3000 read OneLibrary-only media?

**Status: ✅ ANSWERED — YES.** Confirmed on real hardware 2026-07-29 by Marshall:
FableGear-exported tracks loaded and played on a CDJ-3000 without issues.
FableGear writes no DeviceSQL, so that stick was OneLibrary-only by construction.

**Consequence: Phase D (the DeviceSQL writer, "the summit") is deferred
indefinitely.** `onelibrary_writer.py` is the independence path, and it works on
the hardware that matters. See §6 and §7 — independence is now gated on
**analysis quality**, not on file formats.

The protocol below is retained for re-testing against other firmware revisions
and device families (OMNIS-DUO, XDJ-AZ, OPUS-QUAD, and older 2000NXS2-era gear,
which is the one place a pdb may still be required).

---

## 1. Why this test decides the roadmap

`docs/dual_format_export.md` calls writing DeviceSQL (`export.pdb`) "Phase D —
the summit": from-scratch page allocation, row groups, and string tables, with
no known open-source writer to lean on. It is the single largest remaining
engineering item on the independence track.

The ground-truth capture in
`tests/fixtures/rekordbox_ground_truth_export/GAP_ANALYSIS.md` suggests that
summit may not need climbing. On a real Rekordbox-prepared drive:

| Store | Tracks | Apparent role |
|---|---|---|
| `exportLibrary.db` (OneLibrary) | **413** | complete library |
| `USBANLZ/` | 412 | full analysis for the whole library |
| `export.pdb` (DeviceSQL) | **57** | partial/legacy subset (~520 KB) |

Verified by brute-scanning all 127 pages of the pdb — 57 track rows exist,
period. Modern Rekordbox appears to treat OneLibrary as the real library and
writes only a partial DeviceSQL file.

**If a CDJ-3000 plays fine from OneLibrary-only media, Phase D can be deferred
indefinitely and FableGear's existing `onelibrary_writer.py` is already the
independence path.** If it cannot, Phase D is mandatory and should be resourced
now. Everything else on the independence track is downstream of this answer.

> This is an inference from **one** captured export. That is exactly why it must
> be hardware-tested rather than acted on.

---

## 2. Safety rules — read before starting

1. **Use a sacrificial USB stick.** Never the gig stick. This test writes a full
   `PIONEER/` tree.
2. **Bring a Rekordbox-made control stick to the same player**, and verify it
   works *first*. Without a control, "the player didn't load it" is ambiguous
   between "OneLibrary-only is unsupported" and "this player/firmware/stick is
   unhappy for an unrelated reason."
3. **Record the firmware version** (CDJ-3000: `MENU/UTILITY` → version). Pioneer
   changed export handling across firmware; an answer without a firmware number
   is not reusable.
4. Format the stick **FAT32 or exFAT** as the player expects. The ground-truth
   drive was FAT32 and Rekordbox did *not* reformat it.

---

## 3. Build the test media

FableGear has no DeviceSQL writer, so **any export it produces is
OneLibrary-only by construction** — that is precisely the condition under test.

From the repo root, with the FableGear archive populated:

```bash
python3 cli.py export-onelibrary /Volumes/TESTSTICK/PIONEER/rekordbox/exportLibrary.db --stage-audio --with-anlz --playlist "Hardware Test"
```

Flags that matter, and why:

| Flag | Why it is not optional |
|---|---|
| `--stage-audio` | Copies the audio onto the stick and points the library at it. Without this the DB references paths that do not exist on the drive and the player has nothing to load — a guaranteed false negative. |
| `--with-anlz` | Writes beat grids and waveforms. Without it the CDJ must re-analyze, which muddies "did it read my library" with "did it re-analyze from scratch." |
| `--playlist` | Keep the set small (10–20 tracks). A failed 400-track export wastes an hour. |

Use a **mixed-format** playlist (MP3 + AIFF at minimum). Format-specific failures
are a real and separately-interesting outcome.

---

## 4. Pre-flight — verify before you walk to the player

Do not carry an unverified stick to the hardware. A packaging mistake caught here
costs a minute; caught at the player it costs a trip and produces an ambiguous
result.

```bash
python3 cli.py export-audit /Volumes/TESTSTICK
```

Required before proceeding:

- [ ] Verdict reads **`OneLibrary only`** — confirms the test condition. If it
      says `DUAL-FORMAT`, a pdb got onto the stick from somewhere and the test is
      invalid.
- [ ] ANLZ line shows `with beat grid` and `with waveform` counts equal to the
      track count, and `0 missing .DAT`.
- [ ] Library cross-match shows every ANLZ track matched in the archive.
- [ ] No `⚠` encryption findings that mention a missing required token.

Then eject cleanly (**`diskutil eject`**, not a yank) so the SQLite WAL is
checkpointed. An un-checkpointed OneLibrary DB can look empty to the player and
is a classic false negative.

---

## 5. At the player — what to record

Verify the **control stick first**, then insert the test stick.

Record each of these verbatim; partial success is the most informative outcome
and the easiest to lose track of:

| # | Observation | Note exactly what happened |
|---|---|---|
| 1 | Does the player mount the device at all? | |
| 2 | Does it show a **rekordbox** browse mode, or only a raw folder view? | Folder-only = library not read. |
| 3 | Are playlists visible with correct names? | |
| 4 | Are track titles/artists/BPM/key shown from the library (not re-derived)? | |
| 5 | Does a track load and play? | |
| 6 | **Is the beat grid correct** — does the quantized loop stay in time over ~1 min? | The real prize. |
| 7 | Are waveforms drawn immediately, or does it re-analyze? | Re-analysis = ANLZ not accepted. |
| 8 | Do cues/hot cues appear (if exported)? | |
| 9 | Any on-screen error text | Verbatim. |
| 10 | Firmware version | |

Repeat on a second player if available — a single unit can have its own fault.

---

## 6. Decision matrix

| Outcome | Reading | Action |
|---|---|---|
| Full browse + correct grids + waveforms | **OneLibrary-only is sufficient on this firmware.** | **Defer Phase D indefinitely.** Redirect that effort to analysis quality (see §7). Promote `onelibrary_writer.py` to the supported export path. |
| Browses and plays, but re-analyzes / grids wrong | Library accepted, **ANLZ rejected**. | Phase D still not required. Fix the ANLZ writer — compare byte-for-byte against the fixture's `parsed/anlz_tags.json`. |
| Mounts, but folder view only | Library **not** read. | Either OneLibrary alone is insufficient, or our DB is malformed. Before concluding Phase D is mandatory: copy a *Rekordbox-made* `exportLibrary.db` onto the same stick and retest. If Rekordbox's own OneLibrary-only stick also fails to browse → the format genuinely needs a companion pdb → **Phase D is mandatory.** If Rekordbox's works and ours does not → our writer is wrong, which is a far cheaper fix. |
| Does not mount at all | Likely filesystem/partitioning, not format. | Re-format, re-export, retest before drawing any format conclusion. |

The third row's disambiguation step is the important one. **Do not conclude
"Phase D is mandatory" without running it** — it is the difference between a
schema bug and months of DeviceSQL work.

---

## 7. If the answer is "yes"

Independence stops being gated on file formats and becomes gated on **analysis
quality**, because from that point FableGear's own numbers are what reach the
player. Measured against 12,687 Rekordbox ground-truth beat grids from a real
library, current BPM detection sits at ~70% (MIREX ±4%) — meaning roughly 3 in
10 tracks would carry a wrong grid onto a gig stick. That becomes the top
priority the moment this test passes.

---

## 8. Results log

### Run 1 — 2026-07-29, CDJ-3000 — ✅ PASS

- **Result:** tracks exported from FableGear loaded and played on a CDJ-3000
  without issues.
- **Test condition holds:** FableGear has no DeviceSQL writer, so the media was
  OneLibrary-only by construction.
- **Outcome row from §6:** row 1 — *OneLibrary-only is sufficient.*
- **Decision:** **Phase D deferred indefinitely.** Effort redirects to analysis
  quality (§7).

Not separately captured on this run, and worth confirming opportunistically at
the next soundcheck — none of these change the Phase D decision, they only
sharpen how much of the ANLZ path is trusted:

- [ ] Beat grid held over ~1 min of quantized looping (vs. just playing cleanly)
- [ ] Waveforms drawn immediately vs. the player re-analyzing on load
- [ ] Firmware version, so this result is reusable against future firmware

### Run N — template

```
Date:
Player model / firmware:
Stick filesystem / size:
Export command used:
export-audit verdict:
Control stick verified working:  yes / no

Observations 1-10:

Outcome row from §6:
Decision:
```
