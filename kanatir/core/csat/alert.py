"""
Triaged Alert — the canonical output contract CSAT publishes to `alerts.triaged`.

The mirror of the Sprint 7-8 AnomalyRecord, one gate downstream: where the
AnomalyRecord is what ADE produces after scoring a FusedObject, the TriagedAlert
is what CSAT produces after triaging one-or-more AnomalyRecords into a single
operator-facing situation-awareness item.

CSAT is a TRIAGE stage, not an explanation stage. It assigns severity,
deduplicates/suppresses repeat detections, and assembles the operator snapshot.
The human-readable "why" (SHAP + NLG) is XAI's job, downstream on
`alerts.explained`. CSAT carries the explainability *inputs* (`detector_scores`,
`conflict_k`) through verbatim so XAI has them; it does not re-derive them.

Design rules (parallel to anomaly.py, deliberately):
  - Versioned from record #1. Downstream consumers (XAI, mission modules) gate
    on SA_SCHEMA_VERSION and may reject/branch on versions they don't grok.
  - ML-free import. This module is the contract only; it must import with no
    sklearn/torch present so `import kanatir.core.csat.alert` works on a
    core-only install and in CI. CSAT triage is pure rule-based logic — there is
    no `[csat]` optional-dependency group, by design. A learned triage model
    would be a TRL-3 overclaim.
  - Privacy lineage is preserved by UNION, not re-derived. This is the new
    wrinkle vs. ADE: where ADE copied one FusedObject's contributors through,
    CSAT may collapse several AnomalyRecords into one alert, so it unions their
    contributors (dedup'd by audit_event_id). The merged alert's lineage covers
    every raw capture that fed any suppressed anomaly — PGC audit trail stays
    unbroken from raw capture -> envelope -> fused -> anomaly -> alert.
  - Severity is deterministic and stated. Rule-based, three levels. WARMUP
    baselines cannot escalate to ALERT (we don't trust a baseline that hasn't
    earned confidence — same epistemic honesty baseline_state was built for).
    conflict_k is surfaced but does NOT auto-escalate: ADE already settled that
    conflict is a tracked input, not an override; re-deriving an escalation from
    it here would relitigate that call.
  - Triage value is auditable. suppressed_count and the dedup window make the
    "N anomalies became 1 alert" collapse visible on the record, not buried in
    logs. An operator (and a reviewer) can see what triage actually did.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from kanatir.core.ade.anomaly import AnomalyRecord, BaselineState
from kanatir.core.msfe.fused import UNKNOWN, Contributor, GeoRef, SourceTrackRef

# Bump on any breaking change to the triaged-alert structure. Consumers gate here.
#
# 1.1.0 (M5.1, TRL 3->4): adds the additive triage-honesty fields
# (observation_count, distinct_video_track_ref_count, identity_reference_available,
# group_reason, incident_id, incident_sequence, class_breakdown) plus a verbatim
# source_track_refs union, so the alert reports OBSERVATIONS distinctly from
# distinct video-track REFERENCES, exposes mixed classes, and carries geo-temporal
# incident continuity. distinct_video_track_ref_count is null (never 0) when no
# refs are available — the "unavailable" vs "present" distinction is preserved end
# to end. assign_severity is FROZEN: no new field feeds it, no multiplicity
# escalation. XAI gates sa_schema_version on MAJOR match, so sealed M6 accepts
# 1.1.0 unchanged (read-only).
SA_SCHEMA_VERSION = "1.1.0"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Severity(StrEnum):
    """Operator-facing severity. Rule-based, deterministic, TRL-3 honest."""

    INFO = "info"  # not anomalous; situational record only
    WATCH = "watch"  # anomalous but low-confidence or warmup-capped; monitor
    ALERT = "alert"  # anomalous, confident, high score; operator attention


# Score thresholds for the deterministic severity rule. Calibration-pending
# against real-media score distributions, exactly like ADE_Z_THRESHOLD — flagged
# here so the gap is on the record, not silently baked in.
WATCH_SCORE_FLOOR = 0.3   # >= this and is_anomaly -> at least WATCH
ALERT_SCORE_FLOOR = 0.7   # >= this and is_anomaly and ACTIVE -> ALERT


def assign_severity(
    *,
    is_anomaly: bool,
    anomaly_score: float,
    baseline_state: BaselineState,
) -> Severity:
    """Deterministic severity rule. No learned model — pure stated logic.

    Rules, in order:
      - not anomalous            -> INFO
      - anomalous, WARMUP        -> WATCH   (cannot ALERT on an unconfident baseline)
      - anomalous, ACTIVE, high  -> ALERT
      - anomalous, ACTIVE, else  -> WATCH
    conflict_k deliberately absent: it is surfaced on the record but does not
    drive escalation (ADE settled conflict-as-input, not override).
    """
    if not is_anomaly:
        return Severity.INFO
    if baseline_state == BaselineState.WARMUP:
        return Severity.WATCH
    if anomaly_score >= ALERT_SCORE_FLOOR:
        return Severity.ALERT
    return Severity.WATCH


def _union_contributors(records: list[AnomalyRecord]) -> list[Contributor]:
    """Union contributors across merged anomalies, dedup'd by audit_event_id.

    Lineage preservation for the collapse case: every raw capture that fed any
    suppressed anomaly must survive into the merged alert. Contributors with a
    None audit_event_id are kept as-is (can't dedup on a missing id) but should
    not occur on a well-formed lineage.
    """
    seen: set[int] = set()
    merged: list[Contributor] = []
    for rec in records:
        for c in rec.contributors:
            if c.audit_event_id is None:
                merged.append(c)
                continue
            if c.audit_event_id in seen:
                continue
            seen.add(c.audit_event_id)
            merged.append(c)
    return merged


def _union_track_refs(records: list[AnomalyRecord]) -> list[SourceTrackRef] | None:
    """Union source-local video-track refs across merged anomalies, dedup'd on
    the (source_sensor_id, track_id) PAIR.

    Returns None when NO member carried any ref (the epistemic 'no information'
    case) — never an empty list, so distinct_video_track_ref_count stays null
    (never 0). Refs are only preserved and deduplicated here, never associated
    across sources or modalities: a multi-ref alert makes no physical-object claim.
    """
    seen: set[tuple[str, int]] = set()
    merged: list[SourceTrackRef] = []
    for rec in records:
        if rec.source_track_refs is None:
            continue
        for ref in rec.source_track_refs:
            key = (ref.source_sensor_id, ref.track_id)
            if key in seen:
                continue
            seen.add(key)
            merged.append(ref)
    return merged or None


def _class_breakdown(records: list[AnomalyRecord]) -> dict[str, int]:
    """Per-class anomaly-record counts, with UNKNOWN always explicit (never
    omitted), so an operator sees the FULL composition of a merged group — not
    just the trigger class. Sums to observation_count; hides nothing."""
    counts: dict[str, int] = {}
    for rec in records:
        counts[rec.classification] = counts.get(rec.classification, 0) + 1
    counts.setdefault(UNKNOWN, 0)
    return counts


def _group_reason(geo: GeoRef) -> str:
    """The explicit grouping rule that placed members in one alert, mirroring the
    tiered geo_group_key / _same_group logic in triage.py (read off GeoRef fields
    directly to avoid a triage<->alert import cycle).

    site_id present -> exact-site grouping within the sliding window
    lat/lon present -> proximity-cell grouping within the sliding window
    neither         -> single ungeolocated bucket within the sliding window
    """
    if geo.site_id is not None:
        return "same_site_within_sliding_window"
    if geo.lat is not None and geo.lon is not None:
        return "same_proximity_cell_within_sliding_window"
    return "ungeolocated_within_sliding_window"


class TriagedAlert(BaseModel):
    """The object on the `alerts.triaged` topic."""

    sa_schema_version: str = SA_SCHEMA_VERSION
    alert_id: str = Field(default_factory=lambda: str(uuid4()))

    # Backref set to the AnomalyRecord(s) this alert triaged. One alert may
    # answer for several anomalies after dedup — hence a list, not a single id.
    # The join key for anyone walking lineage forward or back.
    anomaly_ids: list[str] = Field(min_length=1)

    # Operator situation snapshot — carried from the *triggering* anomaly
    # (the highest-severity / most-recent member of the merged set).
    severity: Severity
    # The TRIGGER class: classification of the highest-severity member (tiebroken
    # by most-recent window_end, matching _OpenGroup.trigger). The single class
    # that DROVE the alert — NOT the group's composition. For the full per-class
    # breakdown of every merged observation see class_breakdown (M5.1);
    # classification must not be read as group composition.
    classification: str
    window_start: datetime
    window_end: datetime
    geo: GeoRef = Field(default_factory=GeoRef)
    baseline_state: BaselineState
    anomaly_score: float = Field(ge=0.0, le=1.0)

    # Explainability inputs passed through for XAI (alerts.explained). NOT
    # re-derived here — CSAT triages, XAI explains. These are the triggering
    # anomaly's diagnostics.
    conflict_k: float = Field(ge=0.0, le=1.0)
    detector_scores: dict[str, float] = Field(default_factory=dict)

    # Triage audit: how many anomalies were suppressed into this one alert, and
    # over what window the dedup applied. suppressed_count == 0 means this alert
    # is 1:1 with a single anomaly (no collapse).
    suppressed_count: int = Field(ge=0, default=0)
    dedup_window_start: datetime | None = None
    dedup_window_end: datetime | None = None

    # FULL lineage carried through — union of every contributing anomaly's
    # contributors, dedup'd by audit_event_id.
    contributors: list[Contributor] = Field(min_length=1)

    # M5.1 (TRL 3->4) triage-honesty surface. These make the alert report what it
    # actually observed. None of them feed assign_severity (frozen).
    #   observation_count            == len(anomaly_ids); == suppressed_count + 1.
    #   distinct_video_track_ref_count  count of unique (source_sensor_id,
    #                                track_id) across members; null when no refs
    #                                (NEVER 0 — 0 would assert confirmed absence).
    #   identity_reference_available bool: were any video-track refs present.
    #   group_reason                 the explicit grouping rule string.
    #   incident_id / incident_sequence  geo-temporal incident identity (D5),
    #                                stable across max-age flushes; NOT physical-
    #                                object continuity.
    #   class_breakdown              per-class record counts, UNKNOWN explicit.
    observation_count: int = Field(ge=1, default=1)
    distinct_video_track_ref_count: int | None = None
    identity_reference_available: bool = False
    group_reason: str = ""
    incident_id: str = Field(default_factory=lambda: str(uuid4()))
    incident_sequence: int = Field(ge=0, default=0)
    class_breakdown: dict[str, int] = Field(default_factory=dict)

    # M5.1: union of source-local video-track refs across merged anomalies,
    # dedup'd on (source_sensor_id, track_id). None = no ref information available
    # (RF-only, acoustic-only, no usable track ids); NEVER an empty list. Preserved
    # verbatim, never associated across sources -> no physical-object claim.
    source_track_refs: list[SourceTrackRef] | None = None

    triaged_ts: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _coherence(self) -> TriagedAlert:
        if self.window_end < self.window_start:
            raise ValueError("window_end precedes window_start")
        if len(self.anomaly_ids) - 1 != self.suppressed_count:
            raise ValueError(
                "suppressed_count must equal len(anomaly_ids) - 1 "
                f"(got {self.suppressed_count} vs {len(self.anomaly_ids)} ids)"
            )
        # D6: observation_count is defined as len(anomaly_ids); enforcing it makes
        # suppressed_count == observation_count - 1 byte-identical to the check
        # above (an alias over the same quantity, not a new invariant) and makes
        # the honesty field tamper-evident — an alert cannot lie about its own
        # anomaly list.
        if self.observation_count != len(self.anomaly_ids):
            raise ValueError(
                "observation_count must equal len(anomaly_ids) "
                f"(got {self.observation_count} vs {len(self.anomaly_ids)})"
            )
        # D3: refs, count, and availability are mutually consistent — and the
        # count is null (never 0) when no refs, so the alert can never assert a
        # confirmed absence of video-track references.
        if self.source_track_refs is None:
            if self.distinct_video_track_ref_count is not None:
                raise ValueError("distinct_video_track_ref_count must be null when no refs")
            if self.identity_reference_available:
                raise ValueError("identity_reference_available must be False when no refs")
        else:
            if self.distinct_video_track_ref_count != len(self.source_track_refs):
                raise ValueError("distinct_video_track_ref_count must equal the ref count")
            if not self.identity_reference_available:
                raise ValueError("identity_reference_available must be True when refs present")
        # D3: class composition is exhaustive and hides nothing.
        if UNKNOWN not in self.class_breakdown:
            raise ValueError("class_breakdown must carry an explicit UNKNOWN key")
        if sum(self.class_breakdown.values()) != self.observation_count:
            raise ValueError("class_breakdown counts must sum to observation_count")
        return self

    @property
    def audit_event_ids(self) -> list[int]:
        """The PGC audit_event_ids that flowed into this alert, lineage intact."""
        return [c.audit_event_id for c in self.contributors if c.audit_event_id is not None]

    @classmethod
    def from_anomalies(
        cls,
        anomalies: list[AnomalyRecord],
        *,
        trigger: AnomalyRecord | None = None,
        incident_id: str | None = None,
        incident_sequence: int = 0,
    ) -> TriagedAlert:
        """Build one triaged alert from one-or-more anomalies (the dedup collapse).

        `trigger` is the anomaly whose snapshot/diagnostics drive the alert
        (default: the first). Severity is assigned from the trigger. Lineage is
        the union across all members. Window spans the full merged set. The M5.1
        honesty fields are counted over the anomaly RECORDS here; incident identity
        (D5) is supplied by the triage buffer's open-group, defaulting to a fresh
        incident for a stand-alone build.
        """
        if not anomalies:
            raise ValueError("cannot triage an empty anomaly list")
        trig = trigger if trigger is not None else anomalies[0]
        severity = assign_severity(
            is_anomaly=trig.is_anomaly,
            anomaly_score=trig.anomaly_score,
            baseline_state=trig.baseline_state,
        )
        window_start = min(a.window_start for a in anomalies)
        window_end = max(a.window_end for a in anomalies)
        refs = _union_track_refs(anomalies)
        return cls(
            anomaly_ids=[a.anomaly_id for a in anomalies],
            severity=severity,
            classification=trig.classification,
            window_start=window_start,
            window_end=window_end,
            geo=trig.geo,
            baseline_state=trig.baseline_state,
            anomaly_score=trig.anomaly_score,
            conflict_k=trig.conflict_k,
            detector_scores=dict(trig.detector_scores),
            suppressed_count=len(anomalies) - 1,
            dedup_window_start=window_start if len(anomalies) > 1 else None,
            dedup_window_end=window_end if len(anomalies) > 1 else None,
            contributors=_union_contributors(anomalies),
            observation_count=len(anomalies),
            distinct_video_track_ref_count=(len(refs) if refs is not None else None),
            identity_reference_available=refs is not None,
            group_reason=_group_reason(trig.geo),
            incident_id=incident_id or str(uuid4()),
            incident_sequence=incident_sequence,
            class_breakdown=_class_breakdown(anomalies),
            source_track_refs=refs,
        )

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str | bytes) -> TriagedAlert:
        return cls.model_validate_json(raw)
