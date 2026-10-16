# Loudness normalisation — measured findings and the limiter question

**Status:** investigation complete. Recommends a config change, and recommends
**against** shipping a limiter on current evidence.
**Date:** 2026-07-29. Measured on 40 real tracks from a working DJ library.

---

## 1. Background

Fixing the true-peak clipping bug (PR #130) had a visible consequence: with
gain correctly capped, `process --normalize` became close to a no-op. On a
40-track sample, **37 of 39 tracks that wanted a boost now correctly skip**
because they have no headroom under the -1.0 dBTP ceiling.

That looked like "we need a limiter." The measurements say otherwise.

## 2. The library, measured

| | |
|---|---|
| Median integrated loudness | **-10.8 LUFS** |
| Median true peak | **-0.10 dBTP** |

These are modern commercial masters: already loudness-maximised, already
peaking at full scale. There is no headroom to give away.

## 3. Why -8 LUFS is unreachable

`TARGET_LUFS` defaults to -8.0. That is **~3 dB above the library's own
median**. Normalising *up* past the median of your material means every track
needs a boost it has no headroom for.

Sweeping the target across the same 40 tracks (ceiling -1.0 dBTP, tolerance
±0.5 LUFS):

| target | already in tolerance | gain alone works | **needs a limiter** |
|---:|---:|---:|---:|
| **-8.0** (current default) | 2% | 0% | **98%** |
| -9.0 | 8% | 2% | 90% |
| -10.0 | 38% | 8% | 55% |
| -11.0 | 30% | 40% | 30% |
| -12.0 | 10% | 70% | 20% |
| **-13.0** | 18% | 78% | **5%** |
| -14.0 | 2% | 95% | 2% |

At -13 LUFS, **96% of the library is handled by gain alone** — no dynamics
processing, no re-encode damage, nothing destructive.

## 4. Three limiter approaches, all tested on real music

Source track: `I=-10.35 LUFS, TP=-0.10 dBTP, LRA=18.90 LU`. Target -8 LUFS,
ceiling -1.0 dBTP.

| approach | result | verdict |
|---|---|---|
| **gain only** (shipped, capped) | skipped — no headroom | safe, but can't reach target |
| **`loudnorm` two-pass** | `I=-11.3` (got *quieter*), `TP=-0.5` (overshot ceiling), `LRA=18.9 → 10.8` | ✗ fails on all three counts |
| **gain + `alimiter`** | `I=-9.9` (closer), `LRA=16.9` (dynamics preserved), `TP=+0.8 dBFS` | ✗ overshoots ceiling |

Notes on the failures, since both are instructive:

- **`loudnorm`** is built for broadcast delivery (-23 LUFS, wide LRA). Its
  dynamic mode compressed LRA to fit `LRA=11`, which *lowered* integrated
  loudness rather than raising it. Wrong tool for DJ loudness.
- **`alimiter`** preserved dynamics far better (LRA 16.9 vs 10.8) and is the
  more promising direction, but it limits **sample** peak while the ceiling is
  **true** peak. Intersample peaks plus MP3 re-encode overshoot pushed the
  output to +0.8 dBFS — still clipping, just less.

## 5. What was ruled out

**Non-destructive gain metadata** — storing a per-track gain value applied at
playback, the way Rekordbox's track gain works — would be the ideal answer:
lossless, reversible, no re-encode. **It is not available.** The OneLibrary
`content` table we write has no gain, loudness, volume, or peak column
(checked directly against the schema in `onelibrary_writer.py`). Rekordbox's
own `DjmdContent` exposes only `SamplerGain`, which is not playback gain.

So there is no non-destructive path through the export format today.

## 6. Recommendations

**1. Lower the default `target_lufs` from -8.0 to about -11.0.**
Nothing else changes. At -11 the split is 30% already in tolerance, 40%
handled by safe gain, 30% needing more — versus 98% unreachable at -8. A DJ
library wants *consistent* loudness, not *maximum* loudness; the CDJ has its
own gain staging and a channel fader. Targeting at or below the material's
median is what makes normalisation a level-matching operation instead of a
mastering operation.

This is a user-facing config change that alters how a library sounds, so it
should be a deliberate decision, not silently applied. Existing configs keep
their value; the change is to the default and to the setup wizard's guidance.

**2. Do not ship a limiter yet.** None of the three approaches met the bar of
"reach the target *and* stay under the ceiling." The `alimiter` path is the
viable candidate, but doing it correctly needs oversampled true-peak detection,
a headroom margin sized for codec overshoot, and a post-encode verify-and-retry
loop — real DSP work deserving its own scoped effort and listening tests, not a
flag bolted onto the current path.

**3. If a limiter is built, verify it the way this investigation did**:
re-measure the *output* file's true peak independently after re-encode. Every
approach here looked correct in theory and failed on measurement.

## 7. Reproducing

The sweep in §3 is over `(integrated_loudness, true_peak)` pairs measured with
`ffmpeg -af loudnorm=print_format=json`. For a track to be safely normalised by
gain alone: `want = target - lufs`, `headroom = ceiling - true_peak`, and it
fits when `want <= 0` (attenuation is always safe) or `headroom >= want`.
