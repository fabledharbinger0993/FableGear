"""
Quench -- loudness levelling for FableGear.

Quench measures how loud a track is (EBU R128, via ffmpeg's loudnorm filter) and, when
asked, rewrites it to a target level without changing what the file is: same container,
same bit depth, tags and cover art carried across, true peak kept under a ceiling.

It is the third of FableGear's own tools. Iron listens for tempo and key, Anvil reads and
writes tags, Quench sets level. Forge runs all three.

    from pathlib import Path
    import quench

    before = quench.measure(Path("track.mp3"))     # Loudness(-11.3 LUFS, -0.2 dBTP)
    result = quench.level(Path("track.mp3"), target=-8.0, tolerance=1.0)
    result.levelled, result.skip_reason           # True, "" -- or False, "no_headroom"

No third-party loudness or DSP library sits underneath this: ffmpeg does the measurement
and the gain, Quench does the deciding and the safe swap-into-place.
"""

# Submodules are re-exported explicitly, the same convention anvil/__init__.py uses.
from quench import api, codecs, errors, schema
from quench.api import (
    DEFAULT_TRUE_PEAK_CEILING_DBTP,
    apply_gain,
    capped_gain_db,
    level,
    measure,
    plan_gain,
)
from quench.codecs import NORMALISABLE_SUFFIXES, codec_args, has_room_for, lossless_codec_args
from quench.errors import MeasureFailed, NotEnoughSpace, QuenchError, RewriteFailed, UnsupportedFormat
from quench.schema import MIN_GAIN_DB, GainPlan, Loudness, QuenchResult

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_TRUE_PEAK_CEILING_DBTP",
    "MIN_GAIN_DB",
    "NORMALISABLE_SUFFIXES",
    "GainPlan",
    "Loudness",
    "MeasureFailed",
    "NotEnoughSpace",
    "QuenchError",
    "QuenchResult",
    "RewriteFailed",
    "UnsupportedFormat",
    "api",
    "apply_gain",
    "capped_gain_db",
    "codec_args",
    "codecs",
    "errors",
    "has_room_for",
    "level",
    "lossless_codec_args",
    "measure",
    "plan_gain",
    "schema",
]
