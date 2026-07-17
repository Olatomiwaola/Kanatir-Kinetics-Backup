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
#
# 1.1.0 (M9, TRL 3->4): adds the OPTIONAL `acoustic_meta` field carrying
# YAMNet-derived acoustic-event metadata (top class, score, entropy, semantic
# group scores) PAST the Dempster-Shafer mass collapse, so the ADE featurizer
# can see acoustic distinctiveness the coarse UAV/GROUND/AMBIENT frame discards.
# The field is optional with a None default: a FusedObject without it is still
# valid (e.g. a video-only or video+RF object). The fusion FRAME is unchanged —
# no new hypothesis — and the D-S combination core (dempster_shafer.py) and the
# sealed acoustic mass mapper (evidence.acoustic_to_mass) are untouched. Sealed
# M7/M8 1.0.0 objects remain reproducible at their sealed commits; the live
# 1.1.0 runtime gates on exact version match (see kanatir.core.ade.__main__),
# so a 1.0.0 object on a live bus is skipped, not silently up-converted.
#
# 1.2.0 (M5.1, TRL 3->4): adds the OPTIONAL `source_track_refs` field — the set
# of distinct source-local video-track references (source_sensor_id + ByteTrack
# track_id) present in the fused window, deduplicated on (source_sensor_id,
# track_id). It preserves references CVP produces that were previously dropped
# at fusion: `source_sensor_id` already survived via Contributor, but `track_id`
# never left the VideoFeatures.detections payload. The field is optional with a
# None default: None means NO video-track-reference information is available
# (RF-only, acoustic-only, or a video window with no usable track ids) — it is
# never an empty list, so the epistemic distinction "unavailable" vs "present"
# survives to CSAT (distinct_video_track_ref_count = null, never 0). A multi-ref
# fused object does NOT imply one physical object: MSFE correlates on ingest_ts +
# spatial key, not on track, so one FusedObject may legitimately span several
# video tracks. This is reference preservation, NOT physical-object association.
# 1.1.0-shaped payloads still parse (field defaults to None). The live runtime
# gates on fused_schema_version; sealed 1.0.0/1.1.0 objects are skipped by an
# exact-match consumer, not coerced (ADE moves to major-match in M5.1, see
# kanatir.core.ade.__main__).
FUSED_SCHEMA_VERSION = "1.2.0"

# The Dempster-Shafer frame of discernment for M3. The hypotheses MSFE fuses
# belief over. Kept small and explicit for the gate; extend in a later sprint
# when ADE needs a finer object taxonomy.
#   UAV     — uncrewed aerial system
#   GROUND  — ground vehicle / person / surface object
#   AMBIENT — environmental / non-threat ambient activity
# UNKNOWN carries mass on the full frame Theta = ignorance ("could be anything").
HYPOTHESES: tuple[str, ...] = ("UAV", "GROUND", "AMBIENT")
UNKNOWN = "UNKNOWN"

# M9 (TRL 3->4): the frozen semantic-group ontology for acoustic events. These
# are coarse AudioSet-lineage parents, NOT Dempster-Shafer hypotheses — their
# whole purpose is to re-expose distinctions the {UAV,GROUND,AMBIENT} frame
# collapses (siren vs engine vs impact all read as "GROUND" in the frame but are
# distinct groups here). Frozen BEFORE evaluation to avoid eval-set tuning; the
# ordering is contractual because the ADE featurizer binds fixed indices to
# these names in this exact order. Append-only if ever extended.
#
# NOTE (recorded by decision): `chainsaw` (an ESC-50 eval positive) is NOT
# explicitly group-mapped in this block. This was intentional, to preserve the
# frozen-before-eval policy after held-out category coverage was inspected.
# Chainsaw distinctiveness is carried by the scalar acoustic features
# (ac_top_score, ac_yamnet_entropy). A future TRL 4+ ontology expansion may add
# a mechanical_tool group ONLY under a broader pre-registered acoustic ontology,
# never from post-hoc eval coverage.
ACOUSTIC_GROUP_NAMES: tuple[str, ...] = (
    "siren_alarm",
    "engine_vehicle",
    "impact_transient",
    "aircraft_uav",
    "voice",
    "nature_ambient",
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Contributor(BaseModel):
    """One feature envelope that fed this fused object. Preserves lineage."""

    envelope_id: str
    modality: Modality
    source_sensor_id: str
    capture_ts: datetime
    audit_event_id: int | None = None  # PGC link, carried through unbroken


class AcousticMeta(BaseModel):
    """
    YAMNet-derived acoustic-event metadata carried on a FusedObject PAST the
    Dempster-Shafer mass collapse (M9, TRL 3->4).

    The acoustic mass mapper (evidence.acoustic_to_mass) projects the top YAMNet
    label onto the coarse {UAV,GROUND,AMBIENT} frame, which collapses
    acoustically-distinct sounds (siren, engine, train, car_horn) onto similar
    belief-mass vectors — the diagnosed M7 recall limitation. This block does NOT
    edit that sealed mapper; instead it routes the surviving YAMNet detail (which
    already lives on the acoustic FeatureEnvelope as `yamnet_top`) around the
    collapse and onto the fused object, so the ADE featurizer can see it directly.

    Fields (all derived purely from AcousticFeatures.yamnet_top):
      top_label      the winning YAMNet class label for the window
      top_score      its score in [0, 1]
      yamnet_entropy Shannon entropy (nats) over the yamnet_top score
                     distribution; low = one class dominates (distinct event),
                     high = diffuse (ambient). This is a cue the mass collapse
                     destroys.
      group_scores   one score per frozen semantic group (ACOUSTIC_GROUP_NAMES),
                     each the MAX yamnet_top score among labels matching that
                     group's fragments, in [0, 1]. Absent groups are present
                     with 0.0, so the dict shape is stable.
    """

    top_label: str
    top_score: float = Field(ge=0.0, le=1.0)
    yamnet_entropy: float = Field(ge=0.0)
    group_scores: dict[str, float]

    @model_validator(mode="after")
    def _check(self) -> AcousticMeta:
        for g, v in self.group_scores.items():
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"group_score for '{g}' out of range: {v}")
        return self


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


class SourceTrackRef(BaseModel):
    """A source-local video-track reference: one ByteTrack track id, qualified
    by the sensor stream it came from.

    ByteTrack ids are LOCAL to a source stream — track_id 5 on camera-01 and
    track_id 5 on camera-02 are different tracks. The reference is therefore the
    (source_sensor_id, track_id) pair, never the bare integer. This is a
    reference to a *video track*, not a claim about a physical object: the same
    physical object may hold several track ids (re-id gaps), and one fused window
    may carry several refs without implying one object.
    """

    source_sensor_id: str
    track_id: int


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

    # M9 (TRL 3->4): optional YAMNet-derived acoustic-event metadata. None when
    # no acoustic envelope contributed (video-only, video+RF). Optional with a
    # None default so 1.0.0-shaped payloads still parse; the live runtime gates
    # on fused_schema_version, so sealed 1.0.0 objects are skipped, not coerced.
    acoustic_meta: AcousticMeta | None = None

    # M5.1 (TRL 3->4): OPTIONAL set of distinct source-local video-track
    # references present in this fused window, deduplicated on
    # (source_sensor_id, track_id). None = no video-track-reference information
    # available (RF-only, acoustic-only, or video with no usable track ids);
    # a populated list = references present. NEVER an empty list for the
    # no-information case — the None-vs-populated distinction is load-bearing
    # downstream (CSAT distinct_video_track_ref_count = null, never 0). A
    # multi-ref object does NOT imply one physical object (see the version note).
    source_track_refs: list[SourceTrackRef] | None = None

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
