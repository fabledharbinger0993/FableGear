"""Scoring rules for scripts/benchmark_key_bpm_vs_rekordbox.py.

Only the pure scoring helpers are tested here; the engines need real audio and are exercised
by running the script on a real manifest (see the script's docstring).
"""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "benchmark_key_bpm_vs_rekordbox.py"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("benchmark_key_bpm_vs_rekordbox", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._init_tables(Path(_SCRIPT).resolve().parent.parent)
    return mod


@pytest.mark.parametrize(
    "raw, scale, expected",
    [
        ("Cm", None, "5A"),  # Rekordbox notation
        ("F#m", None, "11A"),
        ("Bb", None, "6B"),  # flats normalise to the detector's sharps
        ("Abm", None, "1A"),
        ("Amin", None, "8A"),  # iron.key's own names
        ("F#maj", None, "2B"),
        ("A", "minor", "8A"),  # essentia: note + scale
        ("C", "major", "8B"),
        ("8A", None, "8A"),  # already Camelot
        ("nonsense", None, None),
        (None, None, None),
    ],
)
def test_key_to_camelot(bench, raw, scale, expected):
    assert bench.key_to_camelot(raw, scale) == expected


@pytest.mark.parametrize(
    "det, ref, expected",
    [
        ("8A", "8A", "exact"),
        ("9A", "8A", "fifth"),
        ("7A", "8A", "fifth"),
        ("8B", "8A", "relative"),  # A minor vs C major
        ("5B", "8A", "other"),
        ("5A", "8B", "parallel"),  # C minor vs C major
        ("8A", "11A", "other"),
        (None, "8A", None),
        ("8A", None, None),
    ],
)
def test_key_relation(bench, det, ref, expected):
    assert bench.key_relation(det, ref) == expected


def test_bpm_relation_classes(bench):
    assert bench.bpm_relation(125.0, 125.0) == "exact"
    assert bench.bpm_relation(125.4, 125.0) == "exact"
    assert bench.bpm_relation(125.9, 125.0) == "within1"
    assert bench.bpm_relation(127.0, 125.0) == "mirex"
    assert bench.bpm_relation(250.0, 125.0) == "double"
    assert bench.bpm_relation(62.5, 125.0) == "half"
    assert bench.bpm_relation(187.5, 125.0) == "3:2"
    assert bench.bpm_relation(83.3, 125.0) == "2:3"
    assert bench.bpm_relation(90.0, 125.0) == "miss"
    assert bench.bpm_relation(None, 125.0) is None


def test_summarize_counts_declines_and_weights(bench):
    rows = [
        {
            "engine": "iron",
            "ref_bpm": 120.0,
            "bpm_class": "exact",
            "ref_camelot": "8A",
            "key_camelot": "8A",
            "key_class": "exact",
            "elapsed_s": 1.0,
        },
        {
            "engine": "iron",
            "ref_bpm": 120.0,
            "bpm_class": "double",
            "ref_camelot": "8A",
            "key_camelot": "9A",
            "key_class": "fifth",
            "elapsed_s": 3.0,
        },
        {
            "engine": "iron",
            "ref_bpm": 120.0,
            "bpm_class": None,
            "ref_camelot": "8A",
            "key_camelot": None,
            "key_class": None,
            "elapsed_s": 2.0,
        },
    ]
    s = bench.summarize(rows, ["iron"])["iron"]
    assert s["bpm"]["n"] == 3
    assert s["bpm"]["exact_0.6"] == pytest.approx(1 / 3, abs=1e-4)
    assert s["bpm"]["mirex_4pct"] == pytest.approx(1 / 3, abs=1e-4)
    assert s["bpm"]["classes"]["no_output"] == 1
    assert s["key"]["detected"] == 2
    assert s["key"]["exact_of_total"] == pytest.approx(1 / 3, abs=1e-4)
    assert s["key"]["exact_of_detected"] == pytest.approx(1 / 2, abs=1e-4)
    assert s["key"]["mirex_weighted"] == pytest.approx((1.0 + 0.5) / 3, abs=1e-4)
    assert s["mean_s_per_track"] == 2.0
