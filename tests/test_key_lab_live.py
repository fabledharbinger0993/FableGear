from scripts.experiments.key_lab_live import _correctness, _mcnemar


def test_paired_key_scores_count_missing_predictions_as_misses():
    rows = [
        {"key": "Am", "model": ("8A", 0.8), "prev": None},
        {"key": "C", "model": None, "prev": ("8B", 0.8)},
        {"key": "G", "model": ("9B", 0.8), "prev": None},
    ]

    model = _correctness(rows, "model")
    previous = _correctness(rows, "prev")

    assert model.tolist() == [True, False, True]
    assert previous.tolist() == [False, True, False]
    assert _mcnemar(model, previous) == (2, 1, 1.0)
