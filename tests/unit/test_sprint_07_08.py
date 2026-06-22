"""
Sprint 7-8 (M4) unit tests for the Anomaly Detection Engine.

No broker, no Redis, no torch. sklearn is used only by the IsolationForest
tests; the ML-free-import invariant test asserts the package and its contract /
feature / baseline / ensemble modules import with the ML detector deps absent.

Fixtures build real FusedObjects against the committed kanatir.core.msfe.fused
contract.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from kanatir.core.ade.anomaly import (
    ANOMALY_SCHEMA_VERSION,
    AnomalyRecord,
    BaselineState,
)
from kanatir.core.ade.baseline import AdaptiveBaseline
from kanatir.core.ade.detectors import Detector
from kanatir.core.ade.detectors.isolation_forest import IsolationForestDetector
from kanatir.core.ade.detectors.scaffolded import GNNDetector, LSTMAutoencoderDetector
from kanatir.core.ade.ensemble import AnomalyEnsemble
from kanatir.core.ade.features import FEATURE_DIM, FEATURE_NAMES, extract_features
from kanatir.core.msfe.fused import (
    UNKNOWN,
    BeliefMass,
    Contributor,
    FusedObject,
)
from kanatir.pipelines.common.envelope import Modality

np = pytest.importorskip("numpy")


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _contributor(eid: int, modality: Modality, sensor: str) -> Contributor:
    return Contributor(
        envelope_id=f"env-{eid}",
        modality=modality,
        source_sensor_id=sensor,
        capture_ts=datetime.now(UTC),
        audit_event_id=eid,
    )


def _fused(masses: dict[str, float], conflict_k: float, contributors: list[Contributor],
           ws: datetime | None = None, we: datetime | None = None) -> FusedObject:
    ws = ws or datetime.now(UTC)
    we = we or (ws + timedelta(seconds=60))
    belief = BeliefMass(masses=masses, conflict_k=conflict_k)
    mods = {c.modality for c in contributors}
    return FusedObject(
        window_start=ws,
        window_end=we,
        belief=belief,
        classification=belief.top_hypothesis,
        confidence=belief.top_confidence,
        contributors=contributors,
        n_modalities=len(mods),
        is_multimodal=len(mods) >= 2,
    )


def _multimodal_ground() -> FusedObject:
    return _fused(
        masses={"UAV": 0.1, "GROUND": 0.7, "AMBIENT": 0.1, UNKNOWN: 0.1},
        conflict_k=0.05,
        contributors=[
            _contributor(13, Modality.VIDEO, "vid-01"),
            _contributor(4, Modality.ACOUSTIC, "file-01"),
        ],
    )


def _empty_video_ignorance() -> FusedObject:
    # Empty video -> mass collapses to UNKNOWN (the Sprint 5-6 synthetic case).
    return _fused(
        masses={"UAV": 0.0, "GROUND": 0.0, "AMBIENT": 0.0, UNKNOWN: 1.0},
        conflict_k=0.0,
        contributors=[_contributor(1, Modality.VIDEO, "vid-01")],
    )


# --------------------------------------------------------------------------- #
# contract
# --------------------------------------------------------------------------- #
def test_anomaly_record_versioned_from_record_one():
    rec = AnomalyRecord(
        fused_id="f1", window_start=datetime.now(UTC), window_end=datetime.now(UTC),
        classification="GROUND", anomaly_score=0.2, is_anomaly=False,
        baseline_state=BaselineState.ACTIVE, conflict_k=0.1,
        contributors=[_contributor(13, Modality.VIDEO, "vid-01")],
    )
    assert rec.anomaly_schema_version == ANOMALY_SCHEMA_VERSION == "1.0.0"


def test_anomaly_record_round_trips_json():
    rec = AnomalyRecord(
        fused_id="f1", window_start=datetime.now(UTC), window_end=datetime.now(UTC),
        classification="GROUND", anomaly_score=0.2, is_anomaly=False,
        baseline_state=BaselineState.WARMUP, conflict_k=0.1,
        contributors=[_contributor(13, Modality.VIDEO, "vid-01")],
    )
    back = AnomalyRecord.from_json(rec.to_json())
    assert back.anomaly_id == rec.anomaly_id
    assert back.baseline_state is BaselineState.WARMUP


def test_anomaly_record_rejects_inverted_window():
    now = datetime.now(UTC)
    with pytest.raises(ValueError):
        AnomalyRecord(
            fused_id="f1", window_start=now, window_end=now - timedelta(seconds=1),
            classification="GROUND", anomaly_score=0.1, is_anomaly=False,
            baseline_state=BaselineState.ACTIVE, conflict_k=0.0,
            contributors=[_contributor(1, Modality.VIDEO, "vid-01")],
        )


# --------------------------------------------------------------------------- #
# lineage preservation (gate criterion, mirrors M3)
# --------------------------------------------------------------------------- #
def test_lineage_audit_event_ids_survive_into_record():
    obj = _multimodal_ground()  # audit_event_ids 13 + 4
    ens = AnomalyEnsemble(detectors=[IsolationForestDetector()])
    ens.fit(np.tile(extract_features(obj), (50, 1)))
    rec = ens.process(obj)
    assert set(rec.audit_event_ids) == {13, 4}
    assert len(rec.contributors) == 2


# --------------------------------------------------------------------------- #
# feature extractor — positional stability is the whole point
# --------------------------------------------------------------------------- #
def test_feature_vector_dim_matches_names():
    obj = _multimodal_ground()
    v = extract_features(obj)
    assert v.shape == (FEATURE_DIM,) == (len(FEATURE_NAMES),)


def test_feature_vector_positionally_stable_regardless_of_dict_order():
    # Same masses, different dict insertion order -> identical vector.
    a = _fused({"UAV": 0.1, "GROUND": 0.7, "AMBIENT": 0.1, UNKNOWN: 0.1}, 0.05,
               [_contributor(1, Modality.VIDEO, "v")])
    b = _fused({UNKNOWN: 0.1, "AMBIENT": 0.1, "GROUND": 0.7, "UAV": 0.1}, 0.05,
               [_contributor(1, Modality.VIDEO, "v")])
    assert np.allclose(extract_features(a), extract_features(b))


def test_feature_vector_missing_unknown_key_is_zero_not_crash():
    # masses without an explicit UNKNOWN key must read 0.0 there, not raise.
    obj = _fused({"UAV": 0.2, "GROUND": 0.7, "AMBIENT": 0.1}, 0.0,
                 [_contributor(1, Modality.VIDEO, "v")])
    v = extract_features(obj)
    unknown_idx = FEATURE_NAMES.index(f"mass_{UNKNOWN}")
    assert v[unknown_idx] == 0.0


def test_conflict_k_is_a_feature():
    obj = _fused({"GROUND": 0.9, UNKNOWN: 0.1}, 0.33,
                 [_contributor(1, Modality.VIDEO, "v")])
    v = extract_features(obj)
    assert v[FEATURE_NAMES.index("conflict_k")] == pytest.approx(0.33)


# --------------------------------------------------------------------------- #
# adaptive baseline — warmup, foldback, sustained-anomaly resistance
# --------------------------------------------------------------------------- #
def test_baseline_warmup_never_flags_and_reports_warmup():
    b = AdaptiveBaseline(window=50, warmup=10, z_threshold=3.0)
    for _ in range(5):
        score, is_anom, state = b.score(1.0)
        assert state is BaselineState.WARMUP
        assert is_anom is False


def test_baseline_becomes_active_after_warmup():
    b = AdaptiveBaseline(window=50, warmup=10, z_threshold=3.0)
    for _ in range(10):
        b.score(1.0)
    _, _, state = b.score(1.0)
    assert state is BaselineState.ACTIVE


def test_baseline_flags_clear_excursion():
    b = AdaptiveBaseline(window=200, warmup=20, z_threshold=3.0)
    rng = np.random.default_rng(0)
    for _ in range(60):
        b.score(float(rng.normal(10.0, 1.0)))
    score, is_anom, state = b.score(100.0)  # way out
    assert state is BaselineState.ACTIVE
    assert is_anom is True
    assert score > 0.5


def test_baseline_does_not_absorb_sustained_anomaly():
    # A sustained high value must keep flagging — confirmed-normal-only foldback
    # means it is never folded into the baseline, so it cannot mask itself.
    b = AdaptiveBaseline(window=200, warmup=20, z_threshold=3.0)
    for _ in range(60):
        b.score(10.0)
    flags = [b.score(100.0)[1] for _ in range(20)]
    assert all(flags), "sustained anomaly was absorbed into the baseline"


# --------------------------------------------------------------------------- #
# detectors
# --------------------------------------------------------------------------- #
def test_isolation_forest_implements_protocol_and_scores():
    det = IsolationForestDetector()
    assert isinstance(det, Detector)
    assert det.is_ready is False
    X = np.random.default_rng(0).normal(0, 1, size=(100, FEATURE_DIM))
    det.fit(X)
    assert det.is_ready is True
    s = det.score(X[0])
    assert isinstance(s, float) and s >= 0.0


def test_isolation_forest_scores_outlier_higher_than_inlier():
    det = IsolationForestDetector()
    rng = np.random.default_rng(1)
    X = rng.normal(0, 1, size=(200, FEATURE_DIM))
    det.fit(X)
    inlier = det.score(np.zeros(FEATURE_DIM))
    outlier = det.score(np.full(FEATURE_DIM, 50.0))
    assert outlier > inlier


def test_scaffolded_detectors_are_not_ready_and_refuse_to_score():
    for det in (LSTMAutoencoderDetector(), GNNDetector()):
        assert isinstance(det, Detector)
        assert det.is_ready is False
        with pytest.raises(RuntimeError):
            det.score(np.zeros(FEATURE_DIM))
        with pytest.raises(NotImplementedError):
            det.fit(np.zeros((10, FEATURE_DIM)))


# --------------------------------------------------------------------------- #
# ensemble — skips scaffolds, single-path baseline decision, explainable scores
# --------------------------------------------------------------------------- #
def test_ensemble_skips_scaffolded_detectors():
    ens = AnomalyEnsemble(detectors=[
        IsolationForestDetector(), LSTMAutoencoderDetector(), GNNDetector(),
    ])
    obj = _multimodal_ground()
    ens.fit(np.tile(extract_features(obj), (50, 1)))
    assert [d.name for d in ens.ready_detectors] == ["isolation_forest"]


def test_ensemble_produces_valid_record_with_explainable_scores():
    ens = AnomalyEnsemble(detectors=[IsolationForestDetector()])
    obj = _multimodal_ground()
    ens.fit(np.tile(extract_features(obj), (50, 1)))
    rec = ens.process(obj)
    assert isinstance(rec, AnomalyRecord)
    assert rec.fused_id == obj.fused_id
    assert "isolation_forest" in rec.detector_scores
    assert "_combined_scalar" in rec.detector_scores
    assert rec.conflict_k == pytest.approx(0.05)


def test_ensemble_conflict_is_input_not_override():
    # High conflict during baseline WARMUP must NOT force a flag — conflict is a
    # tracked input, not a hardcoded override (TRL-6 decision).
    ens = AnomalyEnsemble(detectors=[IsolationForestDetector()],
                          baseline=AdaptiveBaseline(window=50, warmup=10))
    obj = _fused({"GROUND": 0.5, UNKNOWN: 0.5}, conflict_k=0.99,
                 contributors=[_contributor(1, Modality.VIDEO, "v")])
    ens.fit(np.tile(extract_features(obj), (50, 1)))
    rec = ens.process(obj)  # first object -> baseline WARMUP
    assert rec.baseline_state is BaselineState.WARMUP
    assert rec.is_anomaly is False  # conflict 0.99 did NOT override


def test_empty_video_ignorance_processes_without_error():
    ens = AnomalyEnsemble(detectors=[IsolationForestDetector()])
    obj = _empty_video_ignorance()
    ens.fit(np.tile(extract_features(obj), (50, 1)))
    rec = ens.process(obj)
    # Per the committed BeliefMass.top_hypothesis: argmax is over the *specific*
    # hypotheses excluding UNKNOWN, so all-ignorance (every specific mass 0.0)
    # tiebreaks to the first hypothesis, UAV, at confidence 0.0 — the exact
    # synthetic-media artifact documented in the Sprint 5-6 record. Assert that
    # contract behavior, not an idealized UNKNOWN.
    assert rec.classification == "UAV"
    assert rec.detector_scores["_combined_scalar"] >= 0.0
    assert set(rec.audit_event_ids) == {1}


# --------------------------------------------------------------------------- #
# the invariant: core import pulls no ML
# --------------------------------------------------------------------------- #
def test_ade_contract_modules_import_without_ml():
    # Importing the package + its ML-free modules must succeed with sklearn and
    # torch BLOCKED from import. We run a subprocess with those modules poisoned.
    code = (
        "import builtins, sys\n"
        "_real = builtins.__import__\n"
        "def _blocked(name, *a, **k):\n"
        "    top = name.split('.')[0]\n"
        "    if top in ('sklearn', 'torch'):\n"
        "        raise ImportError('blocked: ' + name)\n"
        "    return _real(name, *a, **k)\n"
        "builtins.__import__ = _blocked\n"
        "import kanatir.core.ade\n"
        "import kanatir.core.ade.anomaly\n"
        "import kanatir.core.ade.features\n"
        "import kanatir.core.ade.baseline\n"
        "import kanatir.core.ade.ensemble\n"
        "import kanatir.core.ade.detectors\n"
        "import kanatir.core.ade.detectors.isolation_forest\n"
        "import kanatir.core.ade.detectors.scaffolded\n"
        "print('OK')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, f"ML-free import failed:\n{out.stderr}"
    assert "OK" in out.stdout
