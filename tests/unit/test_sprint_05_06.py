"""
Sprint 5-6 unit tests — MSFE fusion core. No broker, no Redis, no ML deps.

Covers the M3 gate logic:
  - Dempster-Shafer combination math (agreement, conflict, ignorance, identity)
  - Evidence mapping per modality (incl. empty-detections -> ignorance)
  - Windowed correlation grouping
  - End-to-end fuse_window -> valid versioned FusedObject
  - Fused-object contract invariants (lineage, version, multimodal flags)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kanatir.core.msfe.dempster_shafer import (
    combine_all,
    combine_pair,
    normalize_bba,
    vacuous,
)
from kanatir.core.msfe.evidence import acoustic_to_mass, envelope_to_mass, video_to_mass
from kanatir.core.msfe.fused import (
    FUSED_SCHEMA_VERSION,
    UNKNOWN,
    BeliefMass,
    FusedObject,
)
from kanatir.core.msfe.fusion import correlate, fuse_window
from kanatir.pipelines.common.envelope import (
    AcousticFeatures,
    Detection,
    FeatureEnvelope,
    GeoRef,
    Modality,
    PrivacyBlock,
    VideoFeatures,
)

T0 = datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC)


def _video_env(cls: str, conf: float, sensor="vid-01", ts=T0, site="zone-A"):
    return FeatureEnvelope(
        modality=Modality.VIDEO,
        source_sensor_id=sensor,
        geo=GeoRef(site_id=site),
        capture_ts=ts,
        privacy=PrivacyBlock(gate_passed=True, audit_event_id=13),
        features=VideoFeatures(
            frame_w=640,
            frame_h=480,
            detections=[Detection(track_id=1, cls=cls, confidence=conf,
                                  bbox_xyxy=(0, 0, 10, 10))],
        ),
    )


def _video_empty(sensor="vid-01", ts=T0, site="zone-A"):
    return FeatureEnvelope(
        modality=Modality.VIDEO,
        source_sensor_id=sensor,
        geo=GeoRef(site_id=site),
        capture_ts=ts,
        privacy=PrivacyBlock(gate_passed=True, audit_event_id=14),
        features=VideoFeatures(frame_w=640, frame_h=480, detections=[]),
    )


def _acoustic_env(label: str, score: float, sensor="aud-01", ts=T0, site="zone-A"):
    return FeatureEnvelope(
        modality=Modality.ACOUSTIC,
        source_sensor_id=sensor,
        geo=GeoRef(site_id=site),
        capture_ts=ts,
        privacy=PrivacyBlock(gate_passed=True, audit_event_id=4),
        features=AcousticFeatures(
            sample_rate=16000,
            window_s=1.0,
            yamnet_top=[(label, score)],
            mfcc_mean=[0.0] * 13,
        ),
    )


# --- Dempster-Shafer math ---------------------------------------------------


def test_vacuous_is_identity():
    m = {"UAV": 0.7, UNKNOWN: 0.3}
    combined, k = combine_pair(m, vacuous())
    assert k == pytest.approx(0.0)
    assert combined["UAV"] == pytest.approx(0.7)
    assert combined[UNKNOWN] == pytest.approx(0.3)


def test_agreement_reinforces():
    # Two sensors both lean UAV -> combined UAV belief exceeds either input.
    m1 = {"UAV": 0.6, UNKNOWN: 0.4}
    m2 = {"UAV": 0.6, UNKNOWN: 0.4}
    combined, k = combine_pair(m1, m2)
    assert k == pytest.approx(0.0)  # no conflicting singletons
    assert combined["UAV"] > 0.6
    assert combined["UAV"] == pytest.approx(0.84, abs=1e-6)  # (.36+.24+.24)/1


def test_conflict_is_reported_not_hidden():
    # One says UAV, other says GROUND -> high conflict K, normalized result.
    m1 = {"UAV": 0.9, UNKNOWN: 0.1}
    m2 = {"GROUND": 0.9, UNKNOWN: 0.1}
    combined, k = combine_pair(m1, m2)
    assert k > 0.7  # most of the mass-product is conflicting
    assert sum(combined.values()) == pytest.approx(1.0, abs=1e-6)


def test_total_conflict_falls_back_to_ignorance():
    m1 = {"UAV": 1.0}
    m2 = {"GROUND": 1.0}
    combined, k = combine_pair(m1, m2)
    assert k == pytest.approx(1.0)
    assert combined == {UNKNOWN: 1.0}


def test_normalize_clamps_and_fills_ignorance():
    out = normalize_bba({"UAV": 0.3, "bogus": 0.5, "GROUND": -0.2})
    assert "bogus" not in out
    assert "GROUND" not in out  # negative dropped
    assert out["UAV"] == pytest.approx(0.3)
    assert out[UNKNOWN] == pytest.approx(0.7)


def test_combine_all_empty_is_vacuous():
    combined, k = combine_all([])
    assert combined == {UNKNOWN: 1.0}
    assert k == 0.0


# --- Evidence mapping -------------------------------------------------------


def test_empty_video_is_ignorance_not_ambient():
    # The Sprint 3-4 live sample: detections=[]. Must NOT assert AMBIENT.
    m = video_to_mass(_video_empty())
    assert m == {UNKNOWN: 1.0}


def test_video_uav_detection_maps_to_uav():
    m = video_to_mass(_video_env("drone", 0.8))
    assert m["UAV"] == pytest.approx(0.8)
    assert m[UNKNOWN] == pytest.approx(0.2)


def test_acoustic_drone_maps_to_uav_with_discount():
    m = acoustic_to_mass(_acoustic_env("Drone buzz", 0.8))
    assert m["UAV"] == pytest.approx(0.8 * 0.85)


def test_acoustic_unmatched_is_vacuous():
    m = acoustic_to_mass(_acoustic_env("Mystery sound", 0.9))
    assert m == {UNKNOWN: 1.0}


def test_dispatch_unknown_modality_vacuous():
    env = _acoustic_env("wind", 0.5)
    # force an unmapped modality
    object.__setattr__(env, "modality", Modality.RF)
    assert envelope_to_mass(env) == {UNKNOWN: 1.0}


# --- Correlation ------------------------------------------------------------


def test_correlate_groups_same_window_same_site():
    a = _video_env("car", 0.7, ts=T0)
    b = _acoustic_env("Vehicle engine", 0.7, ts=T0)
    object.__setattr__(a, "ingest_ts", T0)
    object.__setattr__(b, "ingest_ts", T0 + timedelta(seconds=1))
    groups = correlate([a, b], window=timedelta(seconds=2))
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_correlate_splits_distant_times():
    # Correlation keys on ingest_ts, so spread the ingest_ts to force a split.
    a = _video_env("car", 0.7, ts=T0)
    b = _acoustic_env("Vehicle engine", 0.7, ts=T0)
    object.__setattr__(a, "ingest_ts", T0)
    object.__setattr__(b, "ingest_ts", T0 + timedelta(seconds=10))
    groups = correlate([a, b], window=timedelta(seconds=2))
    assert len(groups) == 2


def test_correlate_splits_different_sites():
    a = _video_env("car", 0.7, ts=T0, site="zone-A")
    b = _acoustic_env("Vehicle engine", 0.7, ts=T0, site="zone-B")
    groups = correlate([a, b], window=timedelta(seconds=2))
    assert len(groups) == 2


# --- End-to-end fusion ------------------------------------------------------


def test_fuse_multimodal_agreement_produces_valid_object():
    a = _video_env("car", 0.7, ts=T0)
    b = _acoustic_env("Vehicle engine", 0.7, ts=T0 + timedelta(seconds=1))
    fused = fuse_window([a, b])
    assert isinstance(fused, FusedObject)
    assert fused.fused_schema_version == FUSED_SCHEMA_VERSION
    assert fused.classification == "GROUND"
    assert fused.is_multimodal is True
    assert fused.n_modalities == 2
    assert fused.confidence > 0.7  # agreement reinforced
    # lineage preserved
    ids = {c.audit_event_id for c in fused.contributors}
    assert ids == {13, 4}
    # round-trips
    assert FusedObject.from_json(fused.to_json()).fused_id == fused.fused_id


def test_fuse_empty_video_plus_acoustic_leans_acoustic():
    # Camera silent (ignorance), mic hears a drone -> UAV from the one sensor.
    a = _video_empty(ts=T0)
    b = _acoustic_env("Drone propeller", 0.9, ts=T0)
    fused = fuse_window([a, b])
    assert fused.classification == "UAV"
    assert fused.is_multimodal is True


def test_fuse_conflict_carries_high_k():
    a = _video_env("drone", 0.9, ts=T0)            # UAV
    b = _acoustic_env("Vehicle engine", 0.9, ts=T0)  # GROUND
    fused = fuse_window([a, b])
    assert fused.belief.conflict_k > 0.5


def test_fuse_single_source_is_not_multimodal():
    fused = fuse_window([_video_env("car", 0.8, ts=T0)])
    assert fused.n_modalities == 1
    assert fused.is_multimodal is False


def test_fused_object_rejects_mismatched_classification():
    bm = BeliefMass(masses={"UAV": 0.6, "GROUND": 0.0, "AMBIENT": 0.0, UNKNOWN: 0.4},
                    conflict_k=0.0)
    with pytest.raises(ValueError):
        FusedObject(
            window_start=T0, window_end=T0, belief=bm,
            classification="GROUND",  # wrong: top is UAV
            confidence=0.6,
            contributors=[],  # also invalid, but classification check fires in model
            n_modalities=1, is_multimodal=False,
        )


def test_belief_mass_must_sum_to_one():
    with pytest.raises(ValueError):
        BeliefMass(masses={"UAV": 0.2, UNKNOWN: 0.2}, conflict_k=0.0)


# --- Sprint 5-6 fix: correlate on ingest_ts, not capture_ts -----------------


def test_correlate_uses_ingest_ts_not_capture_ts():
    """
    File-replayed sources carry media-internal capture_ts that may be far apart
    even when both were published to the bus at the same wall-clock moment.
    Correlation must key on ingest_ts so they co-window.
    """
    # Two envelopes: capture_ts 1 hour apart (different file timelines), but
    # ingest_ts within 1 second (published together).
    now = datetime(2026, 6, 20, 16, 14, 0, tzinfo=UTC)
    v = _video_env("car", 0.7, ts=T0)                  # capture_ts = T0 (noon)
    a = _acoustic_env("Vehicle engine", 0.7, ts=T0 + timedelta(hours=1))  # +1h
    # stamp ingest_ts close together (simulate same-moment publish)
    object.__setattr__(v, "ingest_ts", now)
    object.__setattr__(a, "ingest_ts", now + timedelta(seconds=1))

    groups = correlate([v, a], window=timedelta(seconds=2))
    assert len(groups) == 1            # co-windowed despite 1h capture_ts gap
    fused = fuse_window(groups[0])
    assert fused.is_multimodal is True
    assert fused.n_modalities == 2
    assert {c.modality for c in fused.contributors} == {Modality.VIDEO, Modality.ACOUSTIC}
