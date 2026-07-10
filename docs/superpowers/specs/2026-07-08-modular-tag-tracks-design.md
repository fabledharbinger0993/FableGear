# Modular Tag Tracks — design spec

**Date:** 2026-07-08
**Status:** Draft for review
**Author:** brainstorming session (Claude + user)

## 1. Context & vision

FableGear's **Chop Shop** is, in effect, a friendlier and *stable* MusicBrainz Picard:
Picard is the reference tool for fingerprint-based tagging, but it crashes routinely on
large libraries. FableGear's differentiator is surviving a real DJ library (the user's is
~71k tracks) without falling over. The **Record Room** is the companion — a file-path
doctor and a designed eventual replacement for Rekordbox. Both lean on the shared archive
DB as cross-tool memory: each tool produces reports the others consume.

Today the Chop Shop exposes several overlapping standalone tools — **Tag Tracks**
(BPM + Key, with an AcoustID/MusicBrainz enrichment toggle), **Rename Files**, and
**Balance Loudness**. Under the hood these already share machinery:
`audio_processor.process_directory(...)` accepts `detect_bpm`, `detect_key`, `normalise`,
`enrich_tags`, `force`, and `max_workers` — but the UI hard-disables normalize
(`no_normalize=1` on every run) and keeps rename/normalize as separate tabs.

This spec folds those capabilities into a single **modular, multi-effect Tag Tracks tool**:
a checklist of independently toggleable effects, each with its own overwrite control, run in
one pass over the library.

## 2. Goals

- Turn Tag Tracks into a checklist of effects: **BPM, Key, Normalize loudness, AcoustID
  enrich, Rename-from-tags** — each independently on/off.
- Give each writing effect its **own** force/overwrite control (replacing today's single
  global "Force overwrite all existing tags").
- **Fold in** Balance Loudness and Rename as effects; **remove** their standalone Chop Shop
  tabs and their separate Pipeline Wizard steps. Keep the underlying engines.
- Add an **AcoustID force-overwrite** so MusicBrainz data can update tags that already have
  values.
- Add **give-back**: Picard-style submission of fingerprint↔MusicBrainz-recording links for
  confidently matched tracks, gated behind explicit review.
- Add a **Workers** control (parallel files per pass).
- Make **stability-at-scale** and **journal-as-you-go** first-class requirements.

## 3. Non-goals

- **No** full MusicBrainz contribution pipeline for unmatched/unique tracks (adding new
  recordings to MusicBrainz, moderation, etc.). Deferred to a future spec — see §8.
- **No** change to the BPM/Key detection algorithms themselves.
- **No** change to the Record Room / Rekordbox side beyond consuming the new journal fields.

## 4. The effect model

The Tagger's OPTIONS panel becomes a checklist. Effects run in a **fixed order** so each
feeds the next:

```
decode → BPM → Key → Normalize → AcoustID enrich (+ optional submit) → Rename-from-tags
```

| # | Effect | On/off | Overwrite control | Source today |
|---|--------|--------|-------------------|--------------|
| 1 | BPM detection | ✓ | Re-detect even if a BPM tag exists | exists |
| 2 | Key detection | ✓ | Re-detect even if a key tag exists | exists |
| 3 | Normalize loudness | ✓ | Target LUFS (always re-encodes to target) | `normalise=` (UI-disabled) |
| 4 | AcoustID enrich | ✓ | **Overwrite existing title/artist/album** (new) | `enrich_tags=` |
| 4a | ↳ Submit to AcoustID | ✓ | — (matched links only) | new |
| 5 | Rename from tags | ✓ | Overwrite already-clean filenames? | `chop_shop/renamer_learned.py` |

Global controls: **Workers** count (maps to `process_directory(max_workers=…)`), plus the
existing Dry-Run affordance.

Rename runs **last** so it renames off the freshly-written tags (including any AcoustID
enrichment from step 4).

## 5. Fold-ins & removals

- **Balance Loudness** standalone tab → removed. Capability becomes the Normalize effect.
  The loudness engine (`_measure_lufs` / `_normalise_file` in `audio_processor.py`) is kept
  unchanged; only the UI entry point moves.
- **Rename Files** standalone tab → removed. The learned-rename engine
  (`chop_shop/renamer_learned.py`) and its reversible-operations support
  (`chop_shop/reversible_operations.py`) are kept and invoked as the Rename effect.
- **Pipeline Wizard** currently lists Rename and Normalize as separate steps
  (`templates/partials/physical_library/pipeline_wizard.html`). Those two steps are removed
  there too, so the wizard stays consistent with the new tool surface.

## 6. AcoustID: force-overwrite + give-back

### 6.1 Force-overwrite
`_write_enriched_tags(path, meta, force=…)` already supports overwrite; today the UI never
sets it for enrichment. Add a dedicated **"Overwrite existing tags with MusicBrainz data"**
toggle under the enrich effect that passes `force=True` into `_write_enriched_tags`
**independently** of the BPM/Key force flags.

### 6.2 Submission (Picard-style, matched links only)
- Uses `acoustid.submit(apikey, userkey, data, timeout=None)` (confirmed available in the
  installed pyacoustid).
- **Reuses the fingerprint + duration already computed during the enrich lookup** — no second
  fingerprint pass.
- Scope: **only tracks AcoustID confidently matched** (score ≥ 0.60, has a recording MBID).
  Each submission links `fingerprint + duration + mbid`. Unmatched/unique tracks are **not**
  submitted (see §3 non-goals and §8).
- **New config field `acoustid_user_key`** — the user's personal acoustid.org account key,
  distinct from the application `acoustid_api_key`. Added to `user_config.py` defaults,
  `config.py`, the `/api/config` get/set path in `app.py`, and the Settings UI. If absent,
  the Submit effect is disabled with an explanatory hint (mirrors the existing "AcoustID API
  key not set" warning pattern in `runners.js`).
- **Review before send:** submissions are collected during the pass and presented for
  explicit confirmation before `acoustid.submit` is called (batched). Never silent — honors
  the project's user-control principle.

## 7. Cross-cutting requirements

### 7.1 Stability at scale (the Picard-beating requirement)
- **Streaming iteration:** the pass walks files one at a time (as `process_directory`
  already does); the full library is never held in memory.
- **Resumable:** long passes (a full-library enrich+submit is multi-hour at AcoustID's
  3 req/s) use the existing `checkpoint.py` slot mechanism
  (`~/.fablegear/checkpoints/<tool>/<key>.json`) so a stopped run resumes where it left off.
- **Per-file error isolation:** one unreadable/corrupt file records an error on its
  `ProcessResult` and the pass continues (current behavior — preserved).
- **Rate-limit pacing:** AcoustID lookups and submissions are paced (existing
  `pause_seconds`) to stay under 3 req/s; submissions are batched.
- **Bounded concurrency:** the Workers control is capped to a sane maximum to avoid
  exhausting file handles / memory / API quota.

### 7.2 Journal-as-you-go
The `tag_tracks` row written via `archive.log_operation("tag_tracks", …)` (cli.py:633)
currently records only `files_processed / analysis_persisted / bpm_written / key_written /
errors`. Extend the metadata with **per-effect counts**: `normalized`, `enrich_written`,
`acoustid_submitted`, `renamed`, `renames_deferred` (routed to preflight). This fixes the
current gap where enrichment results are invisible in the archive, and it is what lets the
Record Room and other tools consume what the Chop Shop produced.

## 8. Rename integration detail

Running rename inside a batch pass with **preflight-for-risky-names** behavior:

- A file is renamed only when its tag-derived target name **differs** from its current
  filename (already-clean names are left untouched).
- Confident renames (filename derivable from tags via the known-artist / producer-alias
  dictionaries) are applied automatically through the reversible-operations layer.
- Risky/unknown filenames are **not** auto-applied; they are collected and surfaced through
  the existing rename preflight modal (`openRenamePreflightModal` in `runners.js`) so the
  user can confirm a name, teach a producer alias, or quarantine a no-name track.
- Deferred renames are journaled as `renames_deferred` so the count is visible.

## 9. Surfaces touched

- `audio_processor.py` — split the single `force` into per-effect flags (`force_bpm`,
  `force_key`, `enrich_overwrite`); add the rename step and the AcoustID submit step; thread
  new counts onto `ProcessResult`.
- `config.py`, `user_config.py`, `app.py` (`/api/config`), Settings UI — new
  `acoustid_user_key` field.
- Tagger UI + `static/chop_shop/runners.js` — effect checklist, per-effect force toggles,
  normalize target, submit toggle + review modal, workers count; stop forcing
  `no_normalize=1`.
- `templates/index.html` — remove the Normalize and Rename step-tabs.
- `templates/partials/physical_library/pipeline_wizard.html` — remove the Normalize and
  Rename steps.
- `cli.py` — flag parity (`--enrich-overwrite`, `--submit`, `--rename`, `--workers`,
  un-disable `--normalize`); extend the `tag_tracks` journal metadata.
- `chop_shop/renamer_learned.py`, `chop_shop/reversible_operations.py` — invoked as the
  rename effect (no behavioral change expected; wiring only).
- `checkpoint.py` — reused for resumable passes (no change expected).

## 10. Error handling

- Per-file errors are isolated on `ProcessResult.errors`; the pass never aborts on a single
  file.
- Missing prerequisites degrade gracefully: no `acoustid_api_key` → enrich skipped with a
  warning (existing); no `acoustid_user_key` → submit disabled with a hint; no `fpcalc` →
  enrich/submit skipped.
- AcoustID API/network errors are caught per file and logged; submission failures are
  reported in the review/summary, not fatal.

## 11. Testing strategy

- Unit: per-effect flag routing in `process_file` (each effect on/off × force on/off writes
  exactly the expected tags/filenames and nothing else).
- Unit: `enrich_overwrite=False` leaves populated fields untouched; `=True` replaces them.
- Unit: submission payload built only for matched tracks with an MBID; reuses the lookup
  fingerprint; disabled when `acoustid_user_key` is empty.
- Integration: a mixed fixture library runs a multi-effect pass, produces correct journal
  counts, and resumes correctly from a mid-run checkpoint.
- Regression: removing the Normalize/Rename tabs does not break the Pipeline Wizard or the
  rename preflight modal.

## 12. Phasing

One spec, three implementable slices:

1. **Modular effect UI + per-effect force + fold Normalize** — checklist, stop
   `no_normalize=1`, per-effect force flags, remove Balance Loudness tab, journal counts.
2. **Fold Rename with preflight** — invoke the learned-rename engine as the last effect,
   auto-confident + preflight-risky, remove Rename tab, wizard cleanup.
3. **AcoustID force-overwrite + matched-links submission** — enrich overwrite toggle,
   `acoustid_user_key` config + Settings, review-before-send submission reusing the lookup
   fingerprint.

## 13. Open questions / risks

- Rename overwrite semantics: should the Rename effect ever rewrite an already-clean
  filename, or only touch files whose names don't match the tag-derived target? (Leaning:
  only rename when the derived name differs.)
- Workers cap: what maximum is safe given AcoustID's 3 req/s limit when enrich/submit are
  active? (Enrich effectively serializes on the API regardless of worker count.)
- MusicBrainz submission etiquette: confirm the review screen makes it obvious what is being
  sent, so a large library can't accidentally flood the open DB.
