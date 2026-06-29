"""
Tests for the RF FeatureEnvelope extension (TRL 3->4 block).

Proves:
  - RFFeatures validates and round-trips through the envelope contract
  - RF is additive: existing video/acoustic envelopes still validate identically
  - schema_version is unchanged (no repo-wide bump)
  - common band strings accepted (band is a free string in v1.0.0)
  - field constraints hold (no emission_anomaly_score; ranges enforced)
  - modality/payload mismatch still rejected
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from kanatir.pipelines.common.envelope import (
    SCHEMA_VERSION,
    AcousticFeatures,
    FeatureEnvelope,
    Modality,
    PrivacyBlock,
    RFFeatures,
    VideoFeatures,
)


def _privacy() -> PrivacyBlock:
    return PrivacyBlock(gate_passed=True, pii_present=True, pii_scrubbed=True,
                        actions=["rf_id_hash:7"], audit_event_id=42)


def _rf_features(**over) -> RFFeatures:
    base = dict(
        window_s=2.0, band="wifi_2g4", emitter_count=5, new_emitter_rate=0.5,
        unknown_emitter_rate=0.2, rssi_mean=-65.0, rssi_variance=12.0,
        channel_occupancy=0.3, probe_density=1.2, burst_rate=0.8,
    )
    base.update(over)
    return RFFeatures(**base)


def _rf_envelope(**over) -> FeatureEnvelope:
    return FeatureEnvelope(
        modality=Modality.RF,
        source_sensor_id="rap-01",
        capture_ts=datetime(2026, 6, 24, 12, 0, tzinfo=UTC),
        privacy=_privacy(),
        features=_rf_features(**over),
    )


def test_rf_envelope_validates_and_roundtrips():
    env = _rf_envelope()
    raw = env.to_json()
    back = FeatureEnvelope.from_json(raw)
    assert back.modality == Modality.RF
    assert isinstance(back.features, RFFeatures)
    assert back.features.emitter_count == 5
    assert back.features.band == "wifi_2g4"


def test_schema_version_unchanged():
    # The RF block is additive; no repo-wide schema bump.
    assert SCHEMA_VERSION == "1.0.0"
    assert _rf_envelope().schema_version == "1.0.0"


def test_no_emission_anomaly_score_field():
    # ADE owns anomaly scoring; the envelope carries observations only.
    assert "emission_anomaly_score" not in RFFeatures.model_fields


@pytest.mark.parametrize(
    "band",
    ["wifi_2g4", "wifi_5g", "ble", "sdr_900", "sdr_2g4"],
)
def test_common_band_strings_accepted(band):
    env = _rf_envelope(band=band)
    assert env.features.band == band


def test_existing_video_envelope_still_validates():
    env = FeatureEnvelope(
        modality=Modality.VIDEO,
        source_sensor_id="cvp-01",
        capture_ts=datetime(2026, 6, 24, 12, 0, tzinfo=UTC),
        privacy=_privacy(),
        features=VideoFeatures(frame_w=1920, frame_h=1080, detections=[]),
    )
    assert isinstance(env.features, VideoFeatures)
    assert env.schema_version == "1.0.0"


def test_existing_acoustic_envelope_still_validates():
    env = FeatureEnvelope(
        modality=Modality.ACOUSTIC,
        source_sensor_id="app-01",
        capture_ts=datetime(2026, 6, 24, 12, 0, tzinfo=UTC),
        privacy=_privacy(),
        features=AcousticFeatures(sample_rate=16000, window_s=1.0,
                                  yamnet_top=[("Wind", 0.4)], mfcc_mean=[0.1] * 13),
    )
    assert isinstance(env.features, AcousticFeatures)


def test_modality_payload_mismatch_rejected():
    with pytest.raises(ValidationError):
        FeatureEnvelope(
            modality=Modality.VIDEO,  # declares video
            source_sensor_id="x",
            capture_ts=datetime(2026, 6, 24, 12, 0, tzinfo=UTC),
            privacy=_privacy(),
            features=_rf_features(),  # but hands RF payload
        )


def test_negative_emitter_count_rejected():
    with pytest.raises(ValidationError):
        _rf_features(emitter_count=-1)


def test_channel_occupancy_out_of_range_rejected():
    with pytest.raises(ValidationError):
        _rf_features(channel_occupancy=1.5)


def test_negative_rssi_variance_rejected():
    with pytest.raises(ValidationError):
        _rf_features(rssi_variance=-3.0)
