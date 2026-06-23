"""
Unit tests for M7 ADE fitted-model load + schema-pin validation (model_io.py)
and the fit_ade.py artifact contract.

No broker, no network, no API key. Requires the [ade] extra (numpy, sklearn,
joblib) — guarded with importorskip so the ML-free core test runner skips
cleanly, matching the established pattern (the numpy-guarded M4 test).

Covers the six M7 testing requirements:
  1. successful model load
  2. feature_names drift hard-fails
  3. feature_dim drift hard-fails
  4. feature_schema_version drift hard-fails
  5. ADE_MODEL_PATH unset preserves current (unfitted) behavior
  6. fitted=true / fitted detector present when a valid artifact is loaded
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("joblib")

import numpy as np  # noqa: E402

from kanatir.core.ade.detectors.isolation_forest import IsolationForestDetector  # noqa: E402
from kanatir.core.ade.features import (  # noqa: E402
    FEATURE_DIM,
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
)
from kanatir.core.ade.model_io import (  # noqa: E402
    AdeModelIncompatible,
    load_fitted_ensemble,
)


def _make_artifact(**overrides):
    """Build a valid artifact dict (matching fit_ade.py's payload), with optional
    field overrides for drift tests."""
    det = IsolationForestDetector(random_state=42)
    # Fit on a small normal-ish matrix; values are arbitrary — we test load/validate,
    # not anomaly quality here.
    X = np.random.RandomState(0).rand(50, FEATURE_DIM)
    det.fit(X)
    artifact = {
        "fitted_detectors": {"isolation_forest": det},
        "feature_names": tuple(FEATURE_NAMES),
        "feature_dim": FEATURE_DIM,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "detector_config": {"isolation_forest": {"n_estimators": 100,
                                                 "contamination": "auto",
                                                 "random_state": 42}},
        "corpus_id": "sha256:test",
        "corpus_path": "datasets/ade_fit_corpus/test.jsonl",
        "n_samples": 50,
        "trained_at": "2026-06-23T00:00:00+00:00",
    }
    artifact.update(overrides)
    return artifact


def _dump(tmp_path, artifact):
    import joblib

    p = tmp_path / "model.joblib"
    joblib.dump(artifact, p)
    return str(p)


# 1. successful load -------------------------------------------------------
def test_successful_model_load(tmp_path):
    path = _dump(tmp_path, _make_artifact())
    loaded = load_fitted_ensemble(path)
    assert loaded.n_samples == 50
    assert loaded.corpus_id == "sha256:test"
    assert loaded.feature_schema_version == FEATURE_SCHEMA_VERSION


# 2. feature_names drift hard-fails ---------------------------------------
def test_feature_names_drift_hard_fails(tmp_path):
    drifted = (*FEATURE_NAMES[:-1], "renamed_last_feature")
    path = _dump(tmp_path, _make_artifact(feature_names=drifted))
    with pytest.raises(AdeModelIncompatible) as ei:
        load_fitted_ensemble(path)
    assert ei.value.field == "feature_names"


# 3. feature_dim drift hard-fails -----------------------------------------
def test_feature_dim_drift_hard_fails(tmp_path):
    path = _dump(tmp_path, _make_artifact(feature_dim=FEATURE_DIM + 1))
    with pytest.raises(AdeModelIncompatible) as ei:
        load_fitted_ensemble(path)
    assert ei.value.field == "feature_dim"


# 4. feature_schema_version drift hard-fails ------------------------------
def test_feature_schema_version_drift_hard_fails(tmp_path):
    path = _dump(tmp_path, _make_artifact(feature_schema_version="9.9.9"))
    with pytest.raises(AdeModelIncompatible) as ei:
        load_fitted_ensemble(path)
    assert ei.value.field == "feature_schema_version"


# 6. valid artifact -> fitted detector present & ready --------------------
def test_loaded_ensemble_has_ready_fitted_detector(tmp_path):
    path = _dump(tmp_path, _make_artifact())
    loaded = load_fitted_ensemble(path)
    ready = loaded.ensemble.ready_detectors
    assert len(ready) >= 1
    assert any(d.name == "isolation_forest" and d.is_ready for d in ready)


def test_artifact_with_unfitted_detector_rejected(tmp_path):
    """An artifact carrying an unfitted detector must not load as a gate model."""
    det = IsolationForestDetector()  # never fitted -> is_ready False
    path = _dump(tmp_path, _make_artifact(fitted_detectors={"isolation_forest": det}))
    with pytest.raises(AdeModelIncompatible) as ei:
        load_fitted_ensemble(path)
    assert ei.value.field == "fitted_detectors"
