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
from kanatir.core.msfe.fused import Contributor, GeoRef

# Bump on any breaking change to the triaged-alert structure. Consumers gate here.
SA_SCHEMA_VERSION = "1.0.0"


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
    ) -> TriagedAlert:
        """Build one triaged alert from one-or-more anomalies (the dedup collapse).

        `trigger` is the anomaly whose snapshot/diagnostics drive the alert
        (default: the first). Severity is assigned from the trigger. Lineage is
        the union across all members. Window spans the full merged set.
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
        )

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str | bytes) -> TriagedAlert:
        return cls.model_validate_json(raw)
