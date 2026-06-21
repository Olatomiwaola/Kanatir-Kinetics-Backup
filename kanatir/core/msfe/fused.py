"""
Fused Object — the canonical output contract MSFE publishes to `fused.objects`.

This is the load-bearing artifact of Sprint 5-6, the mirror of the Sprint 3-4
FeatureEnvelope: where the envelope is what sensor pipelines *produce*, the
FusedObject is what MSFE *produces* after correlating co-windowed evidence from
two or more heterogeneous feature streams and combining it via Dempster-Shafer.

Design rules (parallel to envelope.py, deliberately):
  - Versioned from object #1. Downstream consumers (ADE, next gate) gate on
    FUSED_SCHEMA_VERSION and may reject/branch on versions they don't understand.
  - Carries the Dempster-Shafer result *and its diagnostics* — in particular the
    conflict mass K. We never silently normalize conflict away; high K is itself
    a signal (disagreeing sensors) that ADE may want downstream.
  - Privacy lineage is preserved, not re-derived. MSFE consumes only gate-passed
    envelopes; the fused object records the contributing audit_event_ids so the
    PGC audit trail remains unbroken from raw capture through fusion. No raw PII
    ever enters MSFE, so none can leave it.
  - Modality-agnostic. `contributors` can hold any Modality; today MSFE is fed
    by video + acoustic, but rf/environmental/mobility attach with no schema
    change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from kanatir.pipelines.common.envelope import GeoRef, Modality

# Bump on any breaking change to the fused-object structure. Consumers gate here.
FUSED_SCHEMA_VERSION = "1.0.0"

# The Dempster-Shafer frame of discernment for M3. The hypotheses MSFE fuses
# belief over. Kept small and explicit for the gate; extend in a later sprint
# when ADE needs a finer object taxonomy.
#   UAV     — uncrewed aerial system
#   GROUND  — ground vehicle / person / surface object
#   AMBIENT — environmental / non-threat ambient activity
# UNKNOWN carries mass on the full frame Theta = ignorance ("could be anything").
HYPOTHESES: tuple[str, ...] = ("UAV", "GROUND", "AMBIENT")
UNKNOWN = "UNKNOWN"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Contributor(BaseModel):
    """One feature envelope that fed this fused object. Preserves lineage."""

    envelope_id: str
    modality: Modality
    source_sensor_id: str
    capture_ts: datetime
    audit_event_id: int | None = None  # PGC link, carried through unbroken


class BeliefMass(BaseModel):
    """
    A normalized Dempster-Shafer belief assignment over HYPOTHESES + UNKNOWN.

    `masses` maps each hypothesis label (and UNKNOWN) to its mass in [0, 1].
    Masses sum to ~1.0 after combination. `conflict_k` is the Dempster conflict
    mass that was redistributed during combination — reported, never hidden.
    High K means the contributing sensors disagreed.
    """

    masses: dict[str, float]
    conflict_k: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check(self) -> BeliefMass:
        if not self.masses:
            raise ValueError("BeliefMass.masses must not be empty")
        total = sum(self.masses.values())
        if not (0.99 <= total <= 1.01):
            raise ValueError(f"BeliefMass.masses must sum to ~1.0, got {total:.4f}")
        for k, v in self.masses.items():
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"mass for '{k}' out of range: {v}")
        return self

    @property
    def top_hypothesis(self) -> str:
        """Argmax over the *specific* hypotheses (excluding UNKNOWN ignorance)."""
        specific = {k: v for k, v in self.masses.items() if k != UNKNOWN}
        if not specific:
            return UNKNOWN
        return max(specific, key=lambda k: specific[k])

    @property
    def top_confidence(self) -> float:
        return self.masses.get(self.top_hypothesis, 0.0)


class FusedObject(BaseModel):
    """The object on the `fused.objects` topic."""

    fused_schema_version: str = FUSED_SCHEMA_VERSION
    fused_id: str = Field(default_factory=lambda: str(uuid4()))

    window_start: datetime
    window_end: datetime
    fused_ts: datetime = Field(default_factory=_utcnow)

    geo: GeoRef = Field(default_factory=GeoRef)

    belief: BeliefMass
    classification: str  # mirrors belief.top_hypothesis
    confidence: float = Field(ge=0.0, le=1.0)  # mirrors belief.top_confidence

    contributors: list[Contributor] = Field(min_length=1)

    n_modalities: int = Field(ge=1)
    is_multimodal: bool  # n_modalities >= 2

    @model_validator(mode="after")
    def _coherence(self) -> FusedObject:
        if self.window_end < self.window_start:
            raise ValueError("window_end precedes window_start")
        mods = {c.modality for c in self.contributors}
        if self.n_modalities != len(mods):
            raise ValueError(
                f"n_modalities={self.n_modalities} != distinct contributor "
                f"modalities {len(mods)}"
            )
        if self.is_multimodal != (self.n_modalities >= 2):
            raise ValueError("is_multimodal inconsistent with n_modalities")
        if self.classification != self.belief.top_hypothesis:
            raise ValueError("classification does not match belief.top_hypothesis")
        if abs(self.confidence - self.belief.top_confidence) > 1e-6:
            raise ValueError("confidence does not match belief.top_confidence")
        return self

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str | bytes) -> FusedObject:
        return cls.model_validate_json(raw)
