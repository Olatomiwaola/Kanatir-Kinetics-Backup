"""
Explained Alert — the canonical output contract XAI publishes to `alerts.explained`.

The mirror of CSAT's TriagedAlert, one gate downstream (M6): where the
TriagedAlert is what CSAT produces after triaging anomalies into an operator
situation item, the ExplainedAlert is what XAI produces after attaching a
human-readable "why" — structured attributions over the detector scores CSAT
already carried, plus NLG prose an operator can read.

XAI is an EXPLANATION stage, not a triage or detection stage. It does not
re-derive severity, re-run detection, or re-triage. It consumes the
explainability inputs CSAT passed through verbatim (`detector_scores`,
`conflict_k`) and turns them into `attributions` + `explanation_text`. The
upstream snapshot (severity, classification, geo, baseline_state, anomaly_score)
is carried forward for auditability so an explained alert is self-contained.

Design rules (parallel to csat/alert.py, deliberately):
  - Versioned from record #1. Downstream consumers (mission modules) gate on
    EXPLAINED_SCHEMA_VERSION and may reject/branch on versions they don't grok.
  - ML-free import. This module is the contract ONLY. It must import with no
    shap / sklearn / numpy / torch / anthropic present, so
    `import kanatir.core.xai.explained` works on a core-only install and in CI.
    SHAP attribution and the Claude API live behind the Explainer interface in
    explainer.py / claude.py, in the `[xai]` / `[xai-claude]` extras — never here.
  - Privacy lineage is preserved by straight COPY. Unlike CSAT (which UNIONs
    contributors across a collapse), XAI is 1:1 with a single TriagedAlert — no
    further collapse at this stage — so it carries the alert's contributors
    forward verbatim. The PGC audit trail stays unbroken: raw -> envelope ->
    fused -> anomaly -> alert -> explained.
  - Attribution is honest or absent. When there are no real detector scores (or
    no fitted model), `attributions` is empty and `attribution_available` is
    False with a stated reason. XAI never fabricates a SHAP explanation over
    empty/trivial scores — the same epistemic posture as WARMUP-not-escalating
    and conflict-as-input-not-override upstream. On synthetic media every alert
    is severity=info with empty detector_scores, so the explanation correctly
    describes triage MECHANICS, not a threat assessment.
  - Which explainer ran is auditable. `explainer_kind` ("templated" | "claude")
    is on the record. The authoritative M6 output is the deterministic templated
    path; the Claude API path is optional demo enrichment, and a reviewer can
    see at a glance which one produced the prose.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from kanatir.core.ade.anomaly import BaselineState
from kanatir.core.csat.alert import Severity, TriagedAlert
from kanatir.core.msfe.fused import Contributor, GeoRef

# Bump on any breaking change to the explained-alert structure. Consumers gate here.
EXPLAINED_SCHEMA_VERSION = "1.0.0"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Attribution(BaseModel):
    """One feature's contribution to the anomaly decision.

    `feature` is the detector-score key (e.g. an ensemble member name). `value`
    is the signed contribution magnitude (positive = pushed toward anomalous).
    `direction` is the human-facing sign label. These are produced by the
    Explainer's attribution method (SHAP behind the `[xai]` extra) — this class
    is just the structured carrier and stays ML-free.
    """

    feature: str
    value: float
    direction: str = "neutral"  # "increases" | "decreases" | "neutral"


class ExplainedAlert(BaseModel):
    """The object on the `alerts.explained` topic."""

    explained_schema_version: str = EXPLAINED_SCHEMA_VERSION
    explained_id: str = Field(default_factory=lambda: str(uuid4()))

    # Backref to the TriagedAlert this explains, and forward to the anomalies it
    # answered for. 1:1 with one alert (no collapse at this stage); anomaly_ids
    # carried so lineage walks both directions without re-joining upstream.
    alert_id: str
    anomaly_ids: list[str] = Field(min_length=1)

    # Operator snapshot carried forward from the alert (NOT re-derived) so the
    # explained record is self-contained for audit.
    severity: Severity
    classification: str
    window_start: datetime
    window_end: datetime
    geo: GeoRef = Field(default_factory=GeoRef)
    baseline_state: BaselineState
    anomaly_score: float = Field(ge=0.0, le=1.0)
    conflict_k: float = Field(ge=0.0, le=1.0)
    suppressed_count: int = Field(ge=0, default=0)

    # The explanation itself.
    attributions: list[Attribution] = Field(default_factory=list)
    attribution_available: bool = False
    attribution_note: str = ""
    explanation_text: str = ""
    explainer_kind: str = "templated"  # "templated" | "claude" — auditable

    # FULL lineage carried forward verbatim from the alert (straight copy).
    contributors: list[Contributor] = Field(min_length=1)

    explained_ts: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _coherence(self) -> ExplainedAlert:
        if self.window_end < self.window_start:
            raise ValueError("window_end precedes window_start")
        if not self.attribution_available and self.attributions:
            raise ValueError(
                "attribution_available is False but attributions are present "
                "— do not fabricate attributions"
            )
        if self.explainer_kind not in ("templated", "claude"):
            raise ValueError(f"unknown explainer_kind: {self.explainer_kind!r}")
        return self

    @property
    def audit_event_ids(self) -> list[int]:
        """The PGC audit_event_ids carried into this explained alert, lineage intact."""
        return [c.audit_event_id for c in self.contributors if c.audit_event_id is not None]

    @classmethod
    def carry_from_alert(
        cls,
        alert: TriagedAlert,
        *,
        attributions: list[Attribution],
        attribution_available: bool,
        attribution_note: str,
        explanation_text: str,
        explainer_kind: str,
    ) -> ExplainedAlert:
        """Build an ExplainedAlert by carrying the alert's snapshot + lineage
        forward verbatim and attaching the explanation produced by an Explainer.

        This is the single construction path used by every Explainer, so the
        carry-forward (snapshot fields, contributors, anomaly_ids) is identical
        regardless of which explainer generated the prose — only attributions,
        text, note and kind differ.
        """
        return cls(
            alert_id=alert.alert_id,
            anomaly_ids=list(alert.anomaly_ids),
            severity=alert.severity,
            classification=alert.classification,
            window_start=alert.window_start,
            window_end=alert.window_end,
            geo=alert.geo,
            baseline_state=alert.baseline_state,
            anomaly_score=alert.anomaly_score,
            conflict_k=alert.conflict_k,
            suppressed_count=alert.suppressed_count,
            attributions=list(attributions),
            attribution_available=attribution_available,
            attribution_note=attribution_note,
            explanation_text=explanation_text,
            explainer_kind=explainer_kind,
            contributors=list(alert.contributors),
        )

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str | bytes) -> ExplainedAlert:
        return cls.model_validate_json(raw)
