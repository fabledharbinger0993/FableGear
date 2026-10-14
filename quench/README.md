# Quench

Loudness levelling for FableGear. Quench measures how loud a track is and, when asked,
rewrites it to a target level without changing what the file is.

```python
from pathlib import Path
import quench

before = quench.measure(Path("track.mp3"))
before.integrated_lufs, before.true_peak_dbtp      # -11.3, -0.2

result = quench.level(Path("track.mp3"), target=-8.0, tolerance=1.0)
result.levelled                                    # True when the file on disk was replaced
result.skip_reason                                 # "" | "in_tolerance" | "no_headroom" | "unsupported_format"
```

Quench is the third of FableGear's own tools, next to Iron (tempo and key) and Anvil
(tag I/O). Forge runs all three on a file: Iron listens, Anvil writes what Iron heard,
Quench sets the level.

## What it does

- **Measure**: integrated loudness (LUFS) and true peak (dBTP) from ffmpeg's `loudnorm`
  filter, EBU R128, one subprocess pass. `measure(path, start=, duration=)` measures a
  window, which the Normalize preview uses.
- **Plan**: `plan_gain` works out the gain to reach the target, capped so the true peak
  never crosses the ceiling. A boost is reduced to the headroom that exists but never
  inverted into a cut; with no headroom the plan is 0 dB and nothing is rewritten.
- **Apply**: `apply_gain` runs ffmpeg's `volume` filter with the recipes in
  `quench/codecs.py`: MP3 stays MP3 at 320k, WAV and AIFF keep their bit depth (AIFF is
  written big-endian so every player reads it), FLAC keeps its bit depth. Tags and cover
  art come across with `-map_metadata 0` and `-c:v copy`. The output is verified with
  ffprobe, then swapped in: original to `.bak`, new file into place, `.bak` deleted, with
  a restore if the swap fails.

## What it refuses

AAC/M4A. Re-encoding is lossy, and ffmpeg's MP4 writer drops freeform atoms (cue and
beat-grid markers, MusicBrainz ids), so a "levelled" m4a would quietly lose DJ metadata.
`level()` reports `skip_reason="unsupported_format"` instead.

## Dependencies

ffmpeg and ffprobe, which FableGear already requires. `quench/codecs.py` reads the source
bit depth with soundfile when it is available and assumes 16-bit otherwise. No loudness
or DSP library, no tag library.

## Errors

`QuenchError` is the base. `MeasureFailed`, `UnsupportedFormat`, `NotEnoughSpace` and
`RewriteFailed` are the branches a caller might take. `level()` catches all of them for
one file and reports in `result.error`, so a batch keeps going.
