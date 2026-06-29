"""Unit tests — M9 model artifact compatibility (the seal boundary).

These assert that the schema-pin validation in model_io behaves exactly as the
block requires:
  - an m9 artifact (1.1.0 / 16-dim / m9 feature_names) loads cleanly;
  - an M7-shaped artifact (1.0.0 / 8-dim) is REJECTED against the 1.1.0 module,
    proving the sealed M7 artifact stays frozen at its commit and is never
    forward-loaded into the new runtime.

The test builds artifacts in a tmp dir using the live featurizer constants, so it
exercises the real _validate_pins contract rather than a fixture.
"""

from __future__ import annotations

import pytest

from kanatir.core.ade.detectors.isolation_forest import IsolationForestDetector
from kanatir.core.ade.detectors.masked_view import (
    M9_EXCLUDED_FEATURES,
    M9_FEATURE_VIEW,
    M9_KEPT_INDICES,
    MaskedFeatureView,
)
from kanatir.core.ade.features import (
    FEATURE_DIM,
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
)
from kanatir.core.ade.model_io import AdeModelIncompatible, load_fitted_ensemble

joblib = pytest.importorskip("joblib")


def _fit_m9_detector():
    import numpy as np

    inner = IsolationForestDetector(n_estimators=20, random_state=42)
    det = MaskedFeatureView(
        inner,
        kept_indices=M9_KEPT_INDICES,
        excluded_features=M9_EXCLUDED_FEATURES,
        feature_view=M9_FEATURE_VIEW,
    )
    # 16-dim fit matrix; values arbitrary but well-formed.
    rng = np.random.default_rng(0)
    det.fit(rng.random((40, FEATURE_DIM)))
    return det


def _m9_payload():
    return {
        "fitted_detectors": {"isolation_forest": _fit_m9_detector()},
        "feature_names": tuple(FEATURE_NAMES),
        "feature_dim": FEATURE_DIM,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "n_samples": 40,
        "corpus_id": "sha256:test",
        "feature_view": M9_FEATURE_VIEW,
    }


def test_m9_artifact_loads_under_current_module(tmp_path):
    p = tmp_path / "ade_isoforest_m9.joblib"
    joblib.dump(_m9_payload(), p)
    loaded = load_fitted_ensemble(str(p))
    assert loaded.feature_schema_version == "1.1.0"
    assert loaded.n_samples == 40
    ready = [d.name for d in loaded.ensemble.ready_detectors]
    assert "isolation_forest" in ready


def test_m7_shaped_artifact_rejected_on_feature_names(tmp_path):
    payload = _m9_payload()
    # Re-pin to the M7 8-dim shape: must be rejected against the 1.1.0 module.
    payload["feature_names"] = (
        "mass_UAV",
        "mass_GROUND",
        "mass_AMBIENT",
        "mass_UNKNOWN",
        "conflict_k",
        "confidence",
        "n_modalities",
        "belief_entropy",
    )
    payload["feature_dim"] = 8
    payload["feature_schema_version"] = "1.0.0"
    p = tmp_path / "fake_m7.joblib"
    joblib.dump(payload, p)
    with pytest.raises(AdeModelIncompatible) as ei:
        load_fitted_ensemble(str(p))
    assert ei.value.field == "feature_names"


def test_version_drift_alone_rejected(tmp_path):
    payload = _m9_payload()
    payload["feature_schema_version"] = "9.9.9"
    p = tmp_path / "bad_ver.joblib"
    joblib.dump(payload, p)
    with pytest.raises(AdeModelIncompatible) as ei:
        load_fitted_ensemble(str(p))
    assert ei.value.field == "feature_schema_version"
