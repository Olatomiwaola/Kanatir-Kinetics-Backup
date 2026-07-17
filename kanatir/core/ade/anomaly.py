"""
Anomaly Record — the canonical output contract ADE publishes to `anomalies.raw`.

The mirror of the Sprint 5-6 FusedObject, one gate downstream: where the
FusedObject is what MSFE produces after Dempster-Shafer fusion, the
AnomalyRecord is what ADE produces after scoring a FusedObject through the
detector ensemble and the adaptive baseline.

Design rules (parallel to fused.py, deliberately):
  - Versioned from record #1. Downstream consumers (CSAT, next gate) gate on
    ANOMALY_SCHEMA_VERSION and may reject/branch on versions they don't grok.
  - ML-free import. This module is the contract only; it must import with no
    sklearn/torch present so `import kanatir.core.ade.anomaly` works on a
    core-only install and in CI. No detector logic lives here.
  - Privacy lineage is preserved, not re-derived. ADE consumes only the
    contributors already carried on the FusedObject and copies them through
    verbatim, so the PGC audit trail stays unbroken from raw capture ->
    envelope -> fused object -> anomaly. No raw PII ever enters ADE.
  - Explainable by construction. `detector_scores` records each detector's raw
    contribution and which path drove the decision; `conflict_k` is surfaced as
    a first-class field, not buried. At TRL 3->6 an un-explainable flag is a
    flag an operator learns to ignore.
  - `baseline_state` distinguishes a WARMUP "no anomaly" (baseline not yet
    confident) from an ACTIVE "no anomaly" (confident). Closes the epistemic
    hole an honest TRL writeup can't paper over.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from kanatir.core.msfe.fused import Contributor, GeoRef, SourceTrackRef

# Bump on any breaking change to the anomaly-record structure. Consumers gate here.
#
# 1.1.0 (M5.1, TRL 3->4): adds the OPTIONAL `source_track_refs` field, propagated
# VERBATIM from FusedObject.source_track_refs (source_sensor_id + ByteTrack
# track_id references present in the fused window). ADE re-publishes the refs
# unchanged so CSAT can distinguish anomaly OBSERVATIONS from distinct video-track
# REFERENCES; ADE performs no track<->record association. Optional with a None
# default: None means no video-track-reference information was available on the
# fused object (RF-only, acoustic-only, or a video window with no usable track
# ids) — never an empty list, so the "unavailable" vs "present" distinction
# survives to CSAT (distinct_video_track_ref_count = null, never 0). A multi-ref
# record does NOT imply one physical object. 1.0.0-shaped payloads still parse
# (field defaults to None). ADE moves its forward fused_schema_version gate from
# exact-match to major-match in M5.1 (see kanatir.core.ade.__main__).
ANOMALY_SCHEMA_VERSION = "1.1.0"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class BaselineState(StrEnum):
    """Whether the adaptive baseline was confident when this record was scored."""

    WARMUP = "warmup"  # baseline still filling its window; score not yet reliable
    ACTIVE = "active"  # baseline has enough confirmed-normal history to score


class AnomalyRecord(BaseModel):
    """The object on the `anomalies.raw` topic."""

    anomaly_schema_version: str = ANOMALY_SCHEMA_VERSION
    anomaly_id: str = Field(default_factory=lambda: str(uuid4()))

    # Backref to the FusedObject that triggered scoring. The join key for anyone
    # who wants to walk the lineage forward or back.
    fused_id: str

    # Carried verbatim from the FusedObject — not re-derived.
    window_start: datetime
    window_end: datetime
    geo: GeoRef = Field(default_factory=GeoRef)
    classification: str  # FusedObject.classification (belief.top_hypothesis)

    # Scoring outputs.
    anomaly_score: float = Field(ge=0.0, le=1.0)  # baseline-normalized ensemble score
    is_anomaly: bool
    baseline_state: BaselineState

    # First-class diagnostics. conflict_k is surfaced directly (it is also a
    # tracked input to the baseline, but a reviewer should not have to dig into
    # the feature vector to see sensor disagreement). detector_scores carries
    # each detector's raw score plus the ensemble/baseline intermediates for
    # explainability — keys are detector names, values raw floats.
    conflict_k: float = Field(ge=0.0, le=1.0)
    detector_scores: dict[str, float] = Field(default_factory=dict)

    # FULL lineage carried through — every contributing envelope's
    # audit_event_id survives into the anomaly record.
    contributors: list[Contributor] = Field(min_length=1)

    # M5.1 (TRL 3->4): OPTIONAL source-local video-track references, propagated
    # VERBATIM from FusedObject.source_track_refs. None = no video-track-reference
    # information available on the fused object (RF-only, acoustic-only, or video
    # with no usable track ids); a populated list = references present. NEVER an
    # empty list for the no-information case — the None-vs-populated distinction is
    # load-bearing downstream (CSAT distinct_video_track_ref_count = null, never 0).
    # Carried through, never associated: ADE performs no track<->record join, and a
    # multi-ref record does NOT imply one physical object.
    source_track_refs: list[SourceTrackRef] | None = None

    detected_ts: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _coherence(self) -> AnomalyRecord:
        if self.window_end < self.window_start:
            raise ValueError("window_end precedes window_start")
        return self

    @property
    def audit_event_ids(self) -> list[int]:
        """The PGC audit_event_ids that flowed into this anomaly, lineage intact."""
        return [c.audit_event_id for c in self.contributors if c.audit_event_id is not None]

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str | bytes) -> AnomalyRecord:
        return cls.model_validate_json(raw)
