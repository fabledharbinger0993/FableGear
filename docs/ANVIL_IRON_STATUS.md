# Anvil + Iron: status and next steps

**Date:** 2026-08-20
**State:** Both packages are on `main` (fast-forward merge from `claude/fable-5-working-tree-xe8kte`, commit `9376103`). Neither is wired into the live app yet — `audio_processor.py`, `waveform_generator.py`, and every other caller still use mutagen/essentia/librosa exactly as before. This is additive: nothing existing changed behavior.

---

## 1. What was done

### Anvil (tag I/O) — built in a prior session, verified in this one

Anvil replaces mutagen (GPL-2.0 — a licensing risk for a for-sale app) with a scoped,
from-spec ID3v2.3/v2.4 reader/writer for MP3, WAV, and AIFF. It was already complete when
this session started (`64ede7b`, `b1dad6d`, `d7e1149`); this session:

- Refreshed `~/FableGear` from `origin/main` (fast-forwarded past 2 commits) and preserved an
  in-progress local CSS edit as a stash rather than discarding it in the conflict that
  followed (`stash@{0}`, `pre-pull-autostash` — still sitting there, likely superseded by
  the CSS fix that landed in the same pull; worth a look before deciding to drop it).
- Ran `anvil.dryrun` (its own read-only survey tool) against two real folders on the
  `OSOS EXTENDED` volume: a 200-file sample of `_FROM_BACKUP_UNSORTED` and a full pass over
  `Music`. Confirmed it's genuinely read-only, and got a real read on library condition:
  90.7% of the backup folder carries an ID3 tag (75% for `Music`), mixed v2.3/v2.4, three
  text encodings in play, heavy Traktor/MusicBrainz/Picard fingerprints already in the TXXX
  frames (the exact data a blanket `remove("TXXX")` would have destroyed).

### Iron (tempo + key detection) — built from scratch this session

Iron formalizes the BPM/key detection that used to live entirely inside
`audio_processor.py` (essentia `RhythmExtractor2013` + librosa `beat_track` fallback for
tempo; librosa `chroma_cqt` + Krumhansl-Schmuckler for key) — and, per a mid-session scope
change, does it **without depending on essentia or librosa at all**, for the same reason
Anvil doesn't depend on mutagen: essentia is AGPL-3.0/commercial dual-licensed, a real risk
for proprietary software, and the goal is DJ-purpose-built tools rather than general MIR
libraries wearing a DJ UI.

**Package** (`iron/`): `dsp.py` (numpy-only STFT/spectral-flux/chroma/autocorrelation
primitives) → `key.py` (chroma → KS correlation → Camelot; the correlation math was already
original code, only the chroma extraction needed replacing) → `tempo.py` (onset detection →
autocorrelation → **harmonic-sum scoring** + **DJ-genre-band octave correction**, the two
techniques that resolve the tempo-doubling ambiguity that is the single hardest part of this
problem) → `schema.py`/`api.py`/`errors.py` (`analyze()`, `IronResult`, hand-off to
`anvil.TrackFields`) → `dryrun.py` (read-only survey, composed with `anvil.read_fields()` for
a real would-write/would-keep report) → `scripts/benchmark_iron_tempo.py` (reproduces the
exact/1%/MIREX accuracy metrics against a live Rekordbox DB or a CSV).

**What got tested, and found, along the way:**
- Validated against synthesized fixtures (kick+hi-hat patterns with realistic timing
  humanization and a bar-level accent, not bare metronome clicks — a perfectly regular pulse
  train is mathematically ambiguous between its true period and every integer divisor of it,
  which is a real trap for a synthetic test, not just this algorithm).
- First pass had real bugs, all found and fixed by testing against a BPM sweep (70–210 BPM):
  sub-frame quantization error (fixed with parabolic peak interpolation), raw autocorrelation
  genuinely favoring a subharmonic over the true tempo on some inputs (fixed with harmonic-sum
  scoring), a rounding-boundary bug that missed a good candidate lag by one frame, and a
  missing ×4 octave-correction ratio.
- **Current accuracy on the synthetic sweep: 12/13 tempos within 2%.** The 13th (190 BPM, a
  1/5-submultiple ambiguity) is a documented `xfail` in `tests/test_iron_tempo.py`, not
  silently dropped.
- 107 tests pass across both packages (from the live repo's actual venv, Python 3.13); ruff
  clean on both `iron/` and `anvil/`.

**What Iron has *not* been validated against:** real music, or the ground truth that matters
— essentia's own measured baseline (91.4% exact-BPM / 94.8% within-1% / 98.3% MIREX, against
12,687 real Rekordbox beat grids, per `requirements_optional.txt`). The synthetic sweep is a
sanity check that the algorithm isn't obviously broken, not evidence it's ready to replace
essentia. `scripts/benchmark_iron_tempo.py` exists specifically to close this gap and has not
been run yet.

---

## 2. Current state, precisely

| | On `main`? | Wired into the app? | Validated against real music? |
|---|---|---|---|
| Anvil (tag I/O) | Yes | No — nothing calls `anvil.write_fields()` from `audio_processor.py` or anywhere else yet | Yes — interop-tested against mutagen (`test_anvil_mutagen_interop.py`), which *is* the validation for a byte-format reader/writer |
| Iron (tempo/key) | Yes | No | No — synthetic only |
| `essentia`/`librosa`/`mutagen`/`chromaprint` | Still all in `requirements.txt` | Still the live path everywhere | — |

Nothing a user does today touches Anvil or Iron. This is deliberately additive so it can be
validated and cut over on its own timeline, per the plan's validate-first gate.

---

## 3. What needs to come next

### 3.1 — Immediate: validate Iron against real ground truth (blocking everything else)

Run `scripts/benchmark_iron_tempo.py` against a real Rekordbox database:

```bash
python3 scripts/benchmark_iron_tempo.py --rekordbox-db /path/to/master.db
```

This is the actual decision input — not a formality. Three realistic outcomes and what each
implies:

- **Close to essentia's numbers (high 80s/90s% exact):** Iron is a credible primary path.
  Move to §3.2.
- **Meaningfully behind essentia but well ahead of librosa's 13.4%:** Usable as the librosa
  *replacement* (i.e., the fallback path when essentia is unavailable, which is already most
  installs — essentia's wheel coverage is narrow per `requirements_optional.txt`) even before
  it's ready to unseat essentia itself. This is a legitimate, valuable intermediate state.
- **Not meaningfully better than librosa:** The harmonic-sum/genre-band approach needs more
  work before Iron is worth shipping over the status quo — likely candidates: tightening the
  genre bands against real data instead of hand-picked centers, adding a second onset-detection
  function (Iron currently uses one spectral-flux function; essentia's accuracy edge comes
  substantially from combining several), or dynamic-programming beat tracking instead of
  single-peak autocorrelation.

Whatever the number is, expect it to differ by genre — a benchmark broken down by BPM range
(and, if taggable, by genre) will be more actionable than one aggregate percentage.

### 3.2 — Wire Iron + Anvil in, behind a flag, without removing anything

Once Iron clears whatever bar comes out of §3.1:

- Add an opt-in path in `audio_processor.py` (or a new thin call site) that runs
  `iron.analyze()` + `anvil.write_fields()` instead of the current essentia/librosa/mutagen
  path, gated by a config flag or CLI flag — not a silent default switch.
- `chop_shop/waveform_generator.py` also imports librosa directly (audio load + its own
  separate `beat_track()` call for beat-aligned waveform markers). Its decode step can move to
  the same ffmpeg-subprocess approach `iron.api._decode()` already uses; its beat markers can
  come from `iron.analyze()` instead of a second, independent librosa call. This is a small,
  self-contained cutover once Iron is trusted for tempo.
- Run both paths side by side on a real library for a while (health.py-style comparison
  logging: "Iron said X, essentia said Y, they agreed/disagreed") before making Iron the
  default. This mirrors how essentia's own adoption was staged behind a health check
  (`beat_tracker_degraded`) rather than a flag-day switch.

### 3.3 — Only after Iron is trusted as primary: remove the old dependencies

- Drop `essentia` from `requirements_optional.txt` and `librosa` from `requirements.txt`.
- Delete the now-dead code in `audio_processor.py`: `_detect_bpm_essentia`, `_detect_bpm`,
  `_detect_key`, `_essentia_available`, `_fold_octave`, and the `KS_MAJOR`/`KS_MINOR`/`NOTES`/
  `LIBROSA_TO_CAMELOT` constants (all superseded by `iron/`).
- Update `health.py`'s `_check_beat_tracker()` / `beat_tracker_degraded` finding — it currently
  monkeypatches and checks `audio_processor._essentia_available`; once that function is gone,
  this health check either gets retired (Iron has no optional-dependency degradation mode to
  warn about) or repointed at whatever Iron-specific quality signal replaces it.
- Same for the mutagen side once Anvil is wired in: `audio_processor.py`'s `_write_tags`,
  `_write_enriched_tags`, `extract_embedded_art`, and the `mutagen`/`mutagen.id3` imports;
  `fablegear_database/importer.py`'s `_CORRUPT_ERROR_MARKERS` substring-matching (Anvil's typed
  exception hierarchy replaces the need for this); drop `mutagen` from `requirements.txt`.

### 3.4 — Separate project, not blocking the above: fingerprinting (chromaprint)

Explicitly deferred, per the approved plan — flagged here so it isn't forgotten, not because
it's next in line:

- Chromaprint currently powers two different things that need different treatment:
  - **Internal dedup/novelty matching** (`fablegear_database/fingerprinter.py`,
    `chop_shop/duplicate_detector.py`, `chop_shop/novelty_scanner.py`) — an in-house
    perceptual-hash replacement is feasible here because it only has to match itself, the
    same way Anvil and Iron only had to be internally correct, not byte-compatible with a
    third party.
  - **AcoustID metadata enrichment** (`health_acoustid.py`) — **stays on real chromaprint
    permanently**, confirmed by the user. Matching AcoustID's public database inherently
    requires a spec-compliant fingerprint; FableGear has its own registered AcoustID API key
    and community registration, so this integration is not being replaced.
- This is a different algorithm family (perceptual hashing, not tempo/key DSP) with a
  13-file blast radius. Treat it as its own planning pass when it's time, not an extension of
  Iron's existing scope.

### 3.5 — Smaller, lower-priority items noticed along the way

- **`downbeat_offset`/`time_signature`** exist on `anvil.TrackFields` but nothing populates
  them — not essentia/librosa today, not Iron either. Iron's tempo detector already computes
  an onset envelope and could extend to beat-phase locking to fill `downbeat_offset` as a real
  product improvement beyond parity; noted as a stretch goal in `iron/tempo.py`'s docstring,
  not started.
- **The `stash@{0}` in `~/FableGear`** (a CSS edit that conflicted with `01d39ca`'s own CSS
  fix during this session's repo refresh) is still sitting there, unresolved. Worth a look —
  likely obsolete since the same file was independently fixed upstream, but it's the user's
  work and shouldn't be dropped silently.
- **Nothing in this session was pushed to `origin`.** `main` here is 6 commits ahead of
  `origin/main` locally. Pushing (and whether `claude/fable-5-working-tree-xe8kte` should be
  deleted or left as-is on the remote once its commits are on `main`) is a separate decision.
