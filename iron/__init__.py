"""
Iron -- in-house audio analysis for FableGear.

Iron listens to audio so Anvil doesn't have to. It detects tempo and musical key from raw
PCM and hands the results to Anvil as ordinary candidate values -- Anvil's own README states
the contract: "Anvil cannot tell whether a value came from Iron or from a caller typing it
in by hand, and treats both the same way."

    from iron import analyze

    result = analyze(path)
    result.bpm, result.bpm_confidence
    result.initial_key, result.key_confidence

    import anvil
    anvil.write_fields(path, result.to_track_fields())

No third-party MIR or beat-tracking library sits underneath this: tempo and key detection
are built directly on numpy (see iron/dsp.py). See iron/README.md for why, and for the
accuracy/rollout plan while this is still being validated against essentia's measured
baseline.
"""

# Submodules are re-exported explicitly, the same convention anvil/__init__.py uses.
from iron import api, dsp, errors, key, schema, tempo
from iron.api import analyze
from iron.errors import DecodeFailed, IronError, UnsupportedFormat
from iron.schema import IronResult

__version__ = "0.1.0"

__all__ = [
    "DecodeFailed",
    "IronError",
    "IronResult",
    "UnsupportedFormat",
    "analyze",
    "api",
    "dsp",
    "errors",
    "key",
    "schema",
    "tempo",
]
