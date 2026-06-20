"""
Sprint 3-4 unit tests: envelope contract + fail-closed privacy gate.

These deliberately avoid torch/TF/cv2 so they run in CI on the lightweight
install. The gate's PGC audit call is patched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest import mock

import pytest
from pydantic import ValidationError

from kanatir.pipelines.common.envelope import (
    SCHEMA_VERSION,
    AcousticFeatures,
    Detection,
    FeatureEnvelope,
    Modality,
    PrivacyBlock,
    VideoFeatures,
)
from kanatir.pipelines.common.privacy_gate import (
    PrivacyGateError,
    ScrubResult,
    run_privacy_gate,
)


def _video_envelope() -> FeatureEnvelope:
    return FeatureEnvelope(
        modality=Modality.VIDEO,
        source_sensor_id="cam-01",
        capture_ts=datetime.now(UTC),
        privacy=PrivacyBlock(gate_passed=True, pii_present=True, pii_scrubbed=True),
        features=VideoFeatures(
            frame_w=1920,
            frame_h=1080,
            detections=[
                Detection(track_id=1, cls="person", confidence=0.9,
                          bbox_xyxy=(0, 0, 10, 20))
            ],
        ),
    )


# --- envelope contract -------------------------------------------------------


def test_video_envelope_roundtrip():
    env = _video_envelope()
    raw = env.to_json()
    back = FeatureEnvelope.from_json(raw)
    assert back.modality is Modality.VIDEO
    assert back.schema_version == SCHEMA_VERSION
    assert back.features.detections[0].cls == "person"


def test_acoustic_envelope_roundtrip():
    env = FeatureEnvelope(
        modality=Modality.ACOUSTIC,
        source_sensor_id="mic-01",
        capture_ts=datetime.now(UTC),
        privacy=PrivacyBlock(gate_passed=True),
        features=AcousticFeatures(
            sample_rate=16000, window_s=0.96,
            yamnet_top=[("Speech", 0.8)], mfcc_mean=[0.1, 0.2],
        ),
    )
    back = FeatureEnvelope.from_json(env.to_json())
    assert back.features.modality is Modality.ACOUSTIC
    assert back.features.yamnet_top[0][0] == "Speech"


def test_privacy_block_rejects_unpassed_gate():
    # An envelope must never carry gate_passed=False.
    with pytest.raises(ValidationError):
        PrivacyBlock(gate_passed=False)


def test_modality_payload_mismatch_rejected():
    with pytest.raises(ValidationError):
        FeatureEnvelope(
            modality=Modality.VIDEO,
            source_sensor_id="x",
            capture_ts=datetime.now(UTC),
            privacy=PrivacyBlock(gate_passed=True),
            features=AcousticFeatures(sample_rate=16000, window_s=0.96),
        )


def test_confidence_bounds_enforced():
    with pytest.raises(ValidationError):
        Detection(track_id=1, cls="car", confidence=1.4, bbox_xyxy=(0, 0, 1, 1))


# --- fail-closed privacy gate ------------------------------------------------


@mock.patch("kanatir.pipelines.common.privacy_gate.record_event", return_value=42)
def test_gate_passes_and_returns_block(mock_rec):
    def scrub() -> ScrubResult:
        return ScrubResult(pii_present=True, pii_scrubbed=True,
                           actions=["face_blur:2"], payload_to_hash=b"frame")

    block = run_privacy_gate(
        actor="cvp", sensor_id="cam-01", data_modality="video", scrub=scrub,
    )
    assert block.gate_passed is True
    assert block.audit_event_id == 42
    assert "face_blur:2" in block.actions
    mock_rec.assert_called_once()


@mock.patch("kanatir.pipelines.common.privacy_gate.record_event")
def test_gate_fails_closed_on_scrub_error(mock_rec):
    def scrub() -> ScrubResult:
        raise RuntimeError("detector crashed")

    with pytest.raises(PrivacyGateError):
        run_privacy_gate(
            actor="cvp", sensor_id="cam-01", data_modality="video", scrub=scrub,
        )
    # No audit event should be written if scrub failed.
    mock_rec.assert_not_called()


@mock.patch("kanatir.pipelines.common.privacy_gate.record_event",
            side_effect=RuntimeError("db down"))
def test_gate_fails_closed_on_audit_error(mock_rec):
    def scrub() -> ScrubResult:
        return ScrubResult(pii_present=False, pii_scrubbed=False)

    # If the audit write fails, the gate must fail closed (no envelope).
    with pytest.raises(PrivacyGateError):
        run_privacy_gate(
            actor="cvp", sensor_id="cam-01", data_modality="video", scrub=scrub,
        )
