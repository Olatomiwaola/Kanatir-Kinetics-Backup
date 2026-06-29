"""Unit tests — M9 masked feature view (column selection + M7 reproducibility)."""

from __future__ import annotations

import numpy as np

from kanatir.core.ade.detectors.masked_view import (
    M7_EXCLUDED_FEATURES,
    M7_KEPT_INDICES,
    M9_EXCLUDED_FEATURES,
    M9_FEATURE_VIEW,
    M9_KEPT_INDICES,
    MaskedFeatureView,
)


class _Echo:
    """Minimal Detector-protocol stand-in: records the column count it was fit
    on, and on score returns the length of the (masked) vector it received."""

    name = "echo"

    def __init__(self):
        self._cols = None

    @property
    def is_ready(self):
        return self._cols is not None

    def fit(self, X):
        self._cols = X.shape[1]

    def score(self, x):
        return float(x.shape[0])


def test_m9_view_drops_n_modalities_keeps_acoustic():
    # index 6 (n_modalities) excluded; appended acoustic indices 8..15 kept.
    assert 6 not in M9_KEPT_INDICES
    for i in (8, 9, 10, 11, 12, 13, 14, 15):
        assert i in M9_KEPT_INDICES
    assert M9_EXCLUDED_FEATURES == ("n_modalities",)
    assert M9_FEATURE_VIEW == "m9_acoustic_event_aware"
    assert len(M9_KEPT_INDICES) == 15


def test_m9_fit_sees_only_kept_columns():
    mv = MaskedFeatureView(
        _Echo(),
        kept_indices=M9_KEPT_INDICES,
        excluded_features=M9_EXCLUDED_FEATURES,
        feature_view=M9_FEATURE_VIEW,
    )
    X = np.arange(16 * 4, dtype=float).reshape(4, 16)
    mv.fit(X)
    assert mv.inner_detector._cols == 15


def test_m9_score_masks_to_kept_columns():
    mv = MaskedFeatureView(
        _Echo(),
        kept_indices=M9_KEPT_INDICES,
        excluded_features=M9_EXCLUDED_FEATURES,
        feature_view=M9_FEATURE_VIEW,
    )
    X = np.arange(16 * 2, dtype=float).reshape(2, 16)
    mv.fit(X)
    assert mv.score(np.arange(16, dtype=float)) == 15.0


def test_m9_selects_correct_column_values():
    # Verify the masked view picks exactly the kept indices, in order.
    captured = {}

    class _Capture(_Echo):
        def score(self, x):
            captured["x"] = np.asarray(x).copy()
            return 0.0

    mv2 = MaskedFeatureView(
        _Capture(),
        kept_indices=M9_KEPT_INDICES,
        excluded_features=M9_EXCLUDED_FEATURES,
        feature_view=M9_FEATURE_VIEW,
    )
    mv2.fit(np.zeros((2, 16)))
    full = np.arange(16, dtype=float)  # value == index
    mv2.score(full)
    assert list(captured["x"]) == list(M9_KEPT_INDICES)


def test_m7_view_still_works_on_eight_dim_vector():
    # Reproducibility regression: the M7 view (default) must still select its 7
    # columns from an 8-dim vector unchanged.
    mv = MaskedFeatureView(_Echo())  # defaults to M7 constants
    mv.fit(np.arange(8 * 2, dtype=float).reshape(2, 8))
    assert mv.inner_detector._cols == 7
    assert M7_KEPT_INDICES == (0, 1, 2, 3, 4, 5, 7)
    assert M7_EXCLUDED_FEATURES == ("n_modalities",)


def test_inner_name_surfaced():
    mv = MaskedFeatureView(
        _Echo(),
        kept_indices=M9_KEPT_INDICES,
        excluded_features=M9_EXCLUDED_FEATURES,
        feature_view=M9_FEATURE_VIEW,
    )
    assert mv.name == "echo"  # lineage/reporting key unchanged
