"""Unit tests — M9 fusion.py acoustic_meta population + RF/video regression."""

from __future__ import annotations

from datetime import UTC, datetime

from kanatir.core.msfe.fusion import fuse_window
from kanatir.pipelines.common.envelope import (
    AcousticFeatures,
    Detection,
    FeatureEnvelope,
    Modality,
    PrivacyBlock,
    RFFeatures,
    VideoFeatures,
)


def _now():
    return datetime.now(UTC)


def _acoustic(top, sid="a1", eid=None):
    kw = {"envelope_id": eid} if eid else {}
    return FeatureEnvelope(
        modality=Modality.ACOUSTIC,
        source_sensor_id=sid,
        capture_ts=_now(),
        ingest_ts=_now(),
        privacy=PrivacyBlock(gate_passed=True, audit_event_id=1),
        features=AcousticFeatures(
            sample_rate=16000, window_s=1.0, yamnet_top=top, mfcc_mean=[0.0] * 13
        ),
        **kw,
    )


def _video(eid=None):
    kw = {"envelope_id": eid} if eid else {}
    return FeatureEnvelope(
        modality=Modality.VIDEO,
        source_sensor_id="v1",
        capture_ts=_now(),
        ingest_ts=_now(),
        privacy=PrivacyBlock(gate_passed=True, audit_event_id=2),
        features=VideoFeatures(
            frame_w=640,
            frame_h=480,
            detections=[
                Detection(track_id=1, cls="car", confidence=0.7, bbox_xyxy=(0, 0, 10, 10))
            ],
        ),
        **kw,
    )


def _rf(eid=None):
    kw = {"envelope_id": eid} if eid else {}
    return FeatureEnvelope(
        modality=Modality.RF,
        source_sensor_id="r1",
        capture_ts=_now(),
        ingest_ts=_now(),
        privacy=PrivacyBlock(gate_passed=True, audit_event_id=3),
        features=RFFeatures(
            window_s=1.0,
            band="wifi_2g4",
            emitter_count=10,
            new_emitter_rate=1.0,
            unknown_emitter_rate=0.5,
            rssi_mean=-50.0,
            rssi_variance=5.0,
            channel_occupancy=0.3,
            probe_density=2.0,
            burst_rate=1.0,
        ),
        **kw,
    )


def test_acoustic_present_populates_meta():
    o = fuse_window([_acoustic([("Siren", 0.8), ("Vehicle", 0.3)]), _video()])
    assert o.acoustic_meta is not None
    assert o.acoustic_meta.top_label == "Siren"
    assert o.n_modalities == 2


def test_no_acoustic_leaves_meta_none():
    o = fuse_window([_video(), _rf()])
    assert o.acoustic_meta is None
    assert o.n_modalities == 2


def test_video_only_meta_none():
    o = fuse_window([_video()])
    assert o.acoustic_meta is None


def test_multi_acoustic_tiebreak_lowest_envelope_id():
    # Equal top scores -> lowest envelope_id wins (deterministic).
    o = fuse_window(
        [_acoustic([("Siren", 0.8)], eid="bbb"), _acoustic([("Train", 0.8)], eid="aaa")]
    )
    assert o.acoustic_meta.top_label == "Train"


def test_multi_acoustic_highest_score_wins():
    o = fuse_window(
        [_acoustic([("Siren", 0.9)], eid="zzz"), _acoustic([("Train", 0.5)], eid="aaa")]
    )
    assert o.acoustic_meta.top_label == "Siren"


def test_rf_video_masses_unchanged_by_meta_addition():
    # Regression: a video+RF object's belief/classification must be unaffected by
    # the acoustic_meta machinery (it stays None and masses come only from V+RF).
    o = fuse_window([_video(), _rf()])
    # belief masses sum ~1, classification mirrors top hypothesis
    assert abs(sum(o.belief.masses.values()) - 1.0) < 1e-6
    assert o.classification == o.belief.top_hypothesis
    assert o.acoustic_meta is None
