"""
Unit tests for M7 MaskedFeatureView (n_modalities exclusion at the detector
boundary). No broker/network. Requires [ade] (numpy, sklearn) — importorskip
so the ML-free runner skips clean.

Covers the five requirements:
  1. fit on 8-col X -> inner detector receives 7 cols
  2. score on 8-vec -> inner detector receives 7 cols
  3. changing n_modalities does NOT change the score (all else equal)
  4. changing conflict_k CAN change the score
  5. FEATURE_SCHEMA_VERSION unchanged
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("sklearn")

import numpy as np  # noqa: E402

from kanatir.core.ade.detectors.isolation_forest import IsolationForestDetector  # noqa: E402
from kanatir.core.ade.detectors.masked_view import (  # noqa: E402
    M7_EXCLUDED_FEATURES,
    M7_KEPT_INDICES,
    MaskedFeatureView,
)
from kanatir.core.ade.features import FEATURE_DIM, FEATURE_NAMES, FEATURE_SCHEMA_VERSION  # noqa: E402


class _SpyDetector:
    """Records the column-width it is fit/scored on, to prove masking happened."""

    name = "spy"

    def __init__(self) -> None:
        self.fit_ncols = None
        self.score_ncols = None
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready

    def fit(self, X) -> None:
        self.fit_ncols = np.asarray(X).shape[1]
        self._ready = True

    def score(self, x) -> float:
        self.score_ncols = np.asarray(x).reshape(-1).shape[0]
        return 0.0


# 1. fit passes 7 cols to inner -------------------------------------------
def test_fit_masks_to_seven_columns():
    spy = _SpyDetector()
    mv = MaskedFeatureView(spy)
    X = np.random.RandomState(0).rand(20, FEATURE_DIM)
    mv.fit(X)
    assert spy.fit_ncols == len(M7_KEPT_INDICES) == 7
    assert FEATURE_DIM == 8


# 2. score passes 7 cols to inner -----------------------------------------
def test_score_masks_to_seven_columns():
    spy = _SpyDetector()
    mv = MaskedFeatureView(spy)
    mv.fit(np.random.RandomState(0).rand(20, FEATURE_DIM))
    mv.score(np.random.RandomState(1).rand(FEATURE_DIM))
    assert spy.score_ncols == 7


# 3. n_modalities (index 6) does not affect the score ---------------------
def test_n_modalities_does_not_change_score():
    mv = MaskedFeatureView(IsolationForestDetector(random_state=42))
    rng = np.random.RandomState(7)
    mv.fit(rng.rand(200, FEATURE_DIM))
    base = rng.rand(FEATURE_DIM)
    a = base.copy(); a[6] = 1.0   # n_modalities = 1
    b = base.copy(); b[6] = 5.0   # n_modalities = 5
    assert mv.score(a) == mv.score(b)
    assert "n_modalities" in M7_EXCLUDED_FEATURES
    assert FEATURE_NAMES[6] == "n_modalities"  # the index we're masking is correct


# 4. conflict_k (index 4) CAN affect the score ----------------------------
def test_conflict_k_can_change_score():
    mv = MaskedFeatureView(IsolationForestDetector(random_state=42))
    rng = np.random.RandomState(11)
    mv.fit(rng.rand(200, FEATURE_DIM))
    base = rng.rand(FEATURE_DIM)
    a = base.copy(); a[4] = 0.0
    b = base.copy(); b[4] = 0.9   # large conflict_k swing
    assert FEATURE_NAMES[4] == "conflict_k"
    # conflict_k is kept in the view, so an extreme swing should move the score.
    assert mv.score(a) != mv.score(b)


# 5. feature schema version unchanged -------------------------------------
def test_feature_schema_version_unchanged():
    assert FEATURE_SCHEMA_VERSION == "1.0.0"
    assert FEATURE_DIM == 8
    assert FEATURE_NAMES[6] == "n_modalities"
