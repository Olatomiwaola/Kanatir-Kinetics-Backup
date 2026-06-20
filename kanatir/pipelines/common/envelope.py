"""
Feature Envelope — the canonical contract every pipeline publishes to the bus.

This is the load-bearing interoperability artifact of Sprint 3-4. Every feature
topic (features.video, features.acoustic, features.rf, ...) carries envelopes of
this shape. Downstream consumers (MSFE fusion) bind to SCHEMA_VERSION and may
reject or branch on envelopes whose version they do not understand.

Design rules:
  - Versioned from envelope #1. Bump SCHEMA_VERSION on any breaking change.
  - Modality-agnostic outer frame; modality-specific payload in `features`.
  - A `privacy` block is MANDATORY and records that the privacy gate ran and
    what it did. An envelope with privacy.gate_passed == False must never be
    published (the gate is fail-closed; see common.privacy_gate).
  - No raw PII ever travels in an envelope. Only post-scrub features and hashes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

# Bump on any breaking change to envelope structure. Consumers gate on this.
SCHEMA_VERSION = "1.0.0"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Modality(StrEnum):
    VIDEO = "video"
    ACOUSTIC = "acoustic"
    RF = "rf"
    ENVIRONMENTAL = "environmental"
    MOBILITY = "mobility"


class GeoRef(BaseModel):
    """Where the sensor is (or where the feature is georeferenced to)."""

    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lon: float | None = Field(default=None, ge=-180.0, le=180.0)
    site_id: str | None = None  # logical site/zone label when no fix is available


class PrivacyBlock(BaseModel):
    """
    Records the privacy-gate decision for this envelope. Mandatory.

    gate_passed: the privacy gate completed successfully (fail-closed: if the
                 gate raised, the frame/window is dropped and no envelope exists).
    pii_present: PII was detected in the source (faces, plates, speech, ...).
    pii_scrubbed: scrubbing was applied (blur, hash, suppression).
    actions: human-readable list of what the gate did, e.g. ["face_blur:2",
             "plate_hash:1"]. Mirrors the audit-log action string.
    audit_event_id: the PGC audit_events.event_id written at the gate. Links the
                    envelope to its tamper-evident audit record.
    """

    gate_passed: bool
    pii_present: bool = False
    pii_scrubbed: bool = False
    actions: list[str] = Field(default_factory=list)
    audit_event_id: int | None = None

    @field_validator("gate_passed")
    @classmethod
    def _must_pass(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError(
                "PrivacyBlock.gate_passed must be True for any published envelope "
                "(privacy gate is fail-closed)"
            )
        return v


# --- Modality-specific feature payloads -------------------------------------


class Detection(BaseModel):
    """One tracked object in a video frame (post-blur)."""

    track_id: int
    cls: str  # class label, e.g. "person", "car", "uav"
    confidence: float = Field(ge=0.0, le=1.0)
    bbox_xyxy: tuple[float, float, float, float]  # pixel coords, x1,y1,x2,y2


class VideoFeatures(BaseModel):
    modality: Literal[Modality.VIDEO] = Modality.VIDEO
    frame_w: int = Field(gt=0)
    frame_h: int = Field(gt=0)
    detections: list[Detection] = Field(default_factory=list)


class AcousticFeatures(BaseModel):
    modality: Literal[Modality.ACOUSTIC] = Modality.ACOUSTIC
    sample_rate: int = Field(gt=0)
    window_s: float = Field(gt=0.0)
    # Top YAMNet class scores for the window: [(label, score), ...]
    yamnet_top: list[tuple[str, float]] = Field(default_factory=list)
    # Mean MFCC vector over the window (length = n_mfcc, typically 13 or 40).
    mfcc_mean: list[float] = Field(default_factory=list)


# Discriminated union: pydantic picks the model by the `modality` field.
FeaturePayload = Annotated[
    VideoFeatures | AcousticFeatures,
    Field(discriminator="modality"),
]


# --- The envelope ------------------------------------------------------------


class FeatureEnvelope(BaseModel):
    """The object on features.* topics."""

    schema_version: str = SCHEMA_VERSION
    envelope_id: str = Field(default_factory=lambda: str(uuid4()))
    modality: Modality
    source_sensor_id: str
    geo: GeoRef = Field(default_factory=GeoRef)

    capture_ts: datetime  # when the source media was captured
    ingest_ts: datetime = Field(default_factory=_utcnow)  # when the pipeline emitted

    privacy: PrivacyBlock
    features: FeaturePayload

    @field_validator("features")
    @classmethod
    def _modality_matches(cls, v: FeaturePayload, info) -> FeaturePayload:
        declared = info.data.get("modality")
        if declared is not None and v.modality != declared:
            raise ValueError(
                f"envelope modality '{declared}' != features modality '{v.modality}'"
            )
        return v

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str | bytes) -> FeatureEnvelope:
        return cls.model_validate_json(raw)
