#!/usr/bin/env python3
"""
Diagnostic: swap iron.dsp.onset_envelope (log-magnitude spectral flux) for a broadband
RMS-energy flux feature, and re-run iron.tempo.detect_tempo's full existing pass structure
(harmonic-sum scoring, genre-band correction, breakdown bar-fit) unchanged on top of it --
against the real 130-track Rekordbox-verified disco/soul/nu-disco set used throughout
docs/IRON_RESEARCH.md's "2:3 compound-meter" investigation.

This is NOT a proposal to build a multi-feature-agreement voting mechanism (the thing
external AI review converged on recommending, citing Zapata/Davies/Gomez 2014 and
essentia's actual RhythmExtractor2013(multifeature) design). It's simpler and cheaper: does
just REPLACING the onset feature already fix most of the disco-cluster ambiguity on its
own? Answer, from this script's own run (see docs/IRON_RESEARCH.md for the full table):
yes, dramatically -- and it's not merely "differently biased," it scores higher than
spectral_flux on the control group too. The 8 real regressions it introduces are the
reason this hasn't been merged into iron/tempo.py outright; see the doc for what a next
attempt (arbitration between the two features, closer to the actual published design)
would need to check.

Usage:
    python3 scripts/experiment_energy_flux_onset.py
    python3 scripts/experiment_energy_flux_onset.py --ground-truth path/to/ground_truth.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from iron import dsp


def energy_flux(y: np.ndarray, sr: int, n_fft: int = 2048, hop_length: int = 512) -> np.ndarray:
    """
    Broadband RMS-energy novelty: frame-to-frame increase in raw (non-log-compressed)
    signal energy, summed across the whole frame rather than per frequency bin.

    Deliberately coarser than dsp.onset_envelope's log-magnitude spectral flux -- no
    per-bin shape information at all, just "is this frame louder than the last one." A
    genuinely different feature, not a retuned version of the existing one: this is the
    same broadband-energy-flux mechanism Zapata/Davies/Gomez (2014) use as one of
    essentia's several independent onset detection functions.
    """
    frames = dsp.frame_signal(y, n_fft, hop_length)
    if frames.shape[0] < 2:
        return np.zeros(frames.shape[0])
    energy = np.sum(frames.astype(np.float64) ** 2, axis=1)
    flux = np.maximum(np.diff(energy), 0.0)
    return np.concatenate([[0.0], flux])


def complex_domain_flux(y: np.ndarray, sr: int, n_fft: int = 2048, hop_length: int = 512) -> np.ndarray:
    """
    Complex-domain onset detection (Duxbury/Bello): predicts each STFT bin's next complex
    value from constant-magnitude + constant-phase-increment, and scores the deviation
    from that prediction. Sensitive to phase discontinuities that pure magnitude flux
    ignores entirely -- another of the independent features in the same published family.
    """
    S = dsp.stft(y, n_fft=n_fft, hop_length=hop_length)
    if S.shape[0] < 3:
        return np.zeros(S.shape[0])
    mag = np.abs(S)
    phase = np.angle(S)
    n_frames = S.shape[0]
    cd = np.zeros(n_frames)
    for t in range(2, n_frames):
        pred_phase = 2 * phase[t - 1] - phase[t - 2]
        pred = mag[t - 1] * np.exp(1j * pred_phase)
        cd[t] = np.sum(np.abs(S[t] - pred))
    return cd


_FEATURES = {
    "spectral_flux": dsp.onset_envelope,  # current production feature, for reference
    "energy_flux": energy_flux,
    "complex_domain": complex_domain_flux,
}


def tempo_match(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * b


def run_full_pipeline(ground_truth_path: Path, feature_name: str) -> None:
    """
    Monkeypatch iron.dsp.onset_envelope to `feature_name`'s function BEFORE importing
    iron.tempo / iron.api (both call it as dsp.onset_envelope(...), so patching the
    attribute on the dsp module object is enough -- no changes to iron/ source needed for
    this diagnostic), then run iron.analyze()'s real end-to-end pipeline (all 4 existing
    tempo.py passes, unchanged) across every track in ground_truth_path.
    """
    dsp.onset_envelope = _FEATURES[feature_name]
    import iron  # import AFTER the monkeypatch

    rows = json.loads(ground_truth_path.read_text())
    results = []
    for i, row in enumerate(rows, 1):
        path = row["path"]
        try:
            ir = iron.analyze(path, bpm_min=75.0, bpm_max=160.0, want=("bpm",))
            iron_bpm = ir.bpm
        except Exception:
            iron_bpm = None
        results.append({"path": path, "rb_bpm": row["bpm"], "iron_bpm": iron_bpm})
        print(f"[{i}/{len(rows)}] {Path(path).name[:55]:55s} {feature_name}={iron_bpm}", file=sys.stderr)

    pairs = [(r["iron_bpm"], r["rb_bpm"]) for r in results if r["iron_bpm"] is not None]
    n = len(pairs)
    if n == 0:
        print(f"{feature_name}: no comparable pairs")
        return
    exact = sum(1 for a, b in pairs if abs(a - b) <= 0.6) / n
    pct1 = sum(1 for a, b in pairs if tempo_match(a, b, 0.01)) / n
    mirex = sum(1 for a, b in pairs if tempo_match(a, b, 0.04)) / n
    print(f"\n{feature_name:15s} vs Rekordbox  n={n:4d}  exact={exact:.1%}  "
          f"within_1%={pct1:.1%}  MIREX={mirex:.1%}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=Path("anvil_iron_test_tracks/rekordbox_fresh_ground_truth.json"),
        help="JSON list of {path, bpm, camelot} rows (gitignored real-library scratch data; "
             "not shipped in the repo -- rebuild your own via the Rekordbox master.db diff "
             "workflow described in docs/IRON_RESEARCH.md if this path doesn't exist).",
    )
    parser.add_argument(
        "--feature", choices=sorted(_FEATURES), default="energy_flux",
        help="Which onset-detection feature to substitute for dsp.onset_envelope.",
    )
    args = parser.parse_args()

    if not args.ground_truth.exists():
        print(f"Ground truth file not found: {args.ground_truth}", file=sys.stderr)
        print("This is real-library scratch data (gitignored), not committed to the repo -- "
              "see docs/IRON_RESEARCH.md for how to regenerate it.", file=sys.stderr)
        sys.exit(1)

    run_full_pipeline(args.ground_truth, args.feature)


if __name__ == "__main__":
    main()
