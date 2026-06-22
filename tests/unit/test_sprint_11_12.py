"""
Sprint 11-12 (M6 / XAI) unit tests.

No broker, no network, no ML, no API key. Covers:
  - ExplainedAlert contract validation + coherence rules
  - severity / classification / geo / anomaly_ids / lineage carry-forward
  - deterministic templated explanation (byte-identical across runs)
  - empty-attribution honesty when detector_scores / fitted model absent
  - ML-free import invariant (explained.py + explainer.py with ML/SDK blocked)
  - ClaudeExplainer faked, no network, no key (guarded-failure + mocked-success)
  - gate path is templated only

Run: pytest tests/unit/test_sprint_11_12.py
"""

from __future__ import annotations

import builtins
import importlib
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from kanatir.core.ade.anomaly import AnomalyRecord, BaselineState
from kanatir.core.csat.alert import Severity, TriagedAlert
from kanatir.core.msfe.fused import Contributor
from kanatir.core.xai.explained import (
    EXPLAINED_SCHEMA_VERSION,
    Attribution,
    ExplainedAlert,
)
from kanatir.core.xai.explainer import TemplatedExplainer
from kanatir.pipelines.common.envelope import GeoRef, Modality

# -- fixtures --------------------------------------------------------------


def _contrib(audit_event_id: int = 1) -> Contributor:
    """A minimal valid Contributor for negative tests that build alerts inline."""
    return Contributor(
        envelope_id=f"env-{audit_event_id}",
        modality=Modality.ACOUSTIC,
        source_sensor_id="file-01",
        capture_ts=datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC),
        audit_event_id=audit_event_id,
    )


def _anomaly(
    *,
    is_anomaly: bool = False,
    score: float = 0.0,
    state: BaselineState = BaselineState.WARMUP,
    classification: str = "AMBIENT",
    detector_scores: dict[str, float] | None = None,
    conflict_k: float = 0.0,
    site_id: str = "zone-A",
    audit_ids: tuple[int, ...] = (1783,),
    modality: Modality = Modality.ACOUSTIC,
) -> AnomalyRecord:
    now = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)
    return AnomalyRecord(
        fused_id=str(uuid4()),
        is_anomaly=is_anomaly,
        anomaly_score=score,
        classification=classification,
        baseline_state=state,
        window_start=now,
        window_end=now + timedelta(seconds=60),
        geo=GeoRef(site_id=site_id),
        conflict_k=conflict_k,
        detector_scores=detector_scores or {},
        contributors=[
            Contributor(
                envelope_id=f"env-{a}",
                modality=modality,
                source_sensor_id="file-01",
                capture_ts=now,
                audit_event_id=a,
            )
            for a in audit_ids
        ],
    )


def _alert(**kw) -> TriagedAlert:
    anomalies = kw.pop("anomalies", None) or [_anomaly(**kw)]
    return TriagedAlert.from_anomalies(anomalies)


# -- contract validation ---------------------------------------------------


def test_explained_alert_version_pinned():
    assert EXPLAINED_SCHEMA_VERSION == "1.0.0"
    alert = _alert()
    exp = TemplatedExplainer().explain(alert)
    assert exp.explained_schema_version == "1.0.0"


def test_explained_alert_roundtrips_json():
    exp = TemplatedExplainer().explain(_alert())
    raw = exp.to_json()
    back = ExplainedAlert.from_json(raw)
    assert back.alert_id == exp.alert_id
    assert back.explained_id == exp.explained_id
    assert back.contributors == exp.contributors


def test_window_coherence_rejected():
    alert = _alert()
    now = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="window_end precedes"):
        ExplainedAlert(
            alert_id=alert.alert_id,
            anomaly_ids=["x"],
            severity=Severity.INFO,
            classification="unknown",
            window_start=now,
            window_end=now - timedelta(seconds=1),
            baseline_state=BaselineState.WARMUP,
            anomaly_score=0.0,
            conflict_k=0.0,
            contributors=[_contrib()],
        )


def test_attributions_without_availability_rejected():
    """The contract forbids carrying attributions while flagged unavailable —
    the structural guard against fabricated SHAP output."""
    alert = _alert()
    now = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="do not fabricate"):
        ExplainedAlert(
            alert_id=alert.alert_id,
            anomaly_ids=["x"],
            severity=Severity.INFO,
            classification="unknown",
            window_start=now,
            window_end=now,
            baseline_state=BaselineState.WARMUP,
            anomaly_score=0.0,
            conflict_k=0.0,
            attributions=[Attribution(feature="f", value=0.5)],
            attribution_available=False,
            contributors=[_contrib()],
        )


def test_unknown_explainer_kind_rejected():
    alert = _alert()
    now = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="unknown explainer_kind"):
        ExplainedAlert(
            alert_id=alert.alert_id,
            anomaly_ids=["x"],
            severity=Severity.INFO,
            classification="unknown",
            window_start=now,
            window_end=now,
            baseline_state=BaselineState.WARMUP,
            anomaly_score=0.0,
            conflict_k=0.0,
            explainer_kind="gpt",
            contributors=[_contrib()],
        )


# -- carry-forward ----------------------------------------------------------


def test_snapshot_carry_forward():
    alert = _alert(classification="speech", site_id="zone-B")
    exp = TemplatedExplainer().explain(alert)
    assert exp.severity == alert.severity
    assert exp.classification == alert.classification == "speech"
    assert exp.geo.site_id == "zone-B"
    assert exp.baseline_state == alert.baseline_state
    assert exp.anomaly_score == alert.anomaly_score
    assert exp.conflict_k == alert.conflict_k
    assert exp.suppressed_count == alert.suppressed_count


def test_anomaly_ids_and_lineage_carry_forward_on_collapse():
    """Union lineage from CSAT must survive verbatim into the explained alert."""
    anomalies = [
        _anomaly(audit_ids=(1783,), modality=Modality.ACOUSTIC),
        _anomaly(audit_ids=(1784,), modality=Modality.ACOUSTIC),
        _anomaly(audit_ids=(1942,), modality=Modality.VIDEO),
    ]
    alert = TriagedAlert.from_anomalies(anomalies)
    exp = TemplatedExplainer().explain(alert)

    assert exp.anomaly_ids == alert.anomaly_ids
    assert len(exp.anomaly_ids) == 3
    assert exp.suppressed_count == 2
    # contributors carried verbatim (straight copy, no re-derivation)
    assert exp.contributors == alert.contributors
    assert sorted(exp.audit_event_ids) == [1783, 1784, 1942]
    # both modalities preserved in lineage
    assert {c.modality for c in exp.contributors} == {Modality.ACOUSTIC, Modality.VIDEO}


def test_alert_id_backref_preserved():
    alert = _alert()
    exp = TemplatedExplainer().explain(alert)
    assert exp.alert_id == alert.alert_id


# -- deterministic templated output ----------------------------------------


def test_templated_explanation_is_deterministic():
    alert = _alert(classification="speech", site_id="zone-A")
    e1 = TemplatedExplainer().explain(alert).explanation_text
    e2 = TemplatedExplainer().explain(alert).explanation_text
    assert e1 == e2
    assert e1  # non-empty


def test_templated_explanation_describes_mechanics_on_info():
    alert = _alert()  # synthetic: not anomalous -> INFO
    exp = TemplatedExplainer().explain(alert)
    assert exp.severity == Severity.INFO
    assert "MECHANICS" in exp.explanation_text
    assert exp.explainer_kind == "templated"


def test_templated_explanation_reports_collapse():
    anomalies = [_anomaly(audit_ids=(i,)) for i in range(1783, 1788)]
    alert = TriagedAlert.from_anomalies(anomalies)
    exp = TemplatedExplainer().explain(alert)
    assert "collapse" in exp.explanation_text.lower()
    assert "suppressed" in exp.explanation_text.lower()


# -- empty-attribution honesty ----------------------------------------------


def test_no_detector_scores_yields_empty_attribution():
    exp = TemplatedExplainer().explain(_alert())  # empty detector_scores
    assert exp.attribution_available is False
    assert exp.attributions == []
    assert "nothing to attribute" in exp.attribution_note


def test_info_severity_yields_empty_attribution_even_with_scores():
    """Scores present but not anomalous (INFO) -> still no fabricated attribution."""
    alert = _alert(detector_scores={"iso_forest": 0.4, "lstm_ae": 0.2})
    # not anomalous -> INFO, so attribution must be gated off honestly
    exp = TemplatedExplainer().explain(alert)
    assert exp.severity == Severity.INFO
    assert exp.attribution_available is False
    assert exp.attributions == []
    assert "info" in exp.attribution_note.lower()


def test_scores_present_anomalous_but_no_model_is_honest():
    """The TRL-3 honest case: real anomaly + scores, but no fitted model supplied
    -> empty attribution with a stated reason, never fabricated."""
    alert = _alert(
        is_anomaly=True,
        score=0.8,
        state=BaselineState.ACTIVE,
        classification="drone",
        detector_scores={"iso_forest": 0.8, "lstm_ae": 0.6},
    )
    exp = TemplatedExplainer().explain(alert)  # no fitted_model
    assert exp.severity == Severity.ALERT
    assert exp.attribution_available is False
    assert exp.attributions == []
    assert "no fitted model" in exp.attribution_note


# -- ML-free import invariant -----------------------------------------------


def test_contract_and_interface_import_without_ml(monkeypatch):
    """explained.py and explainer.py must import with shap/sklearn/numpy/torch/
    anthropic all unavailable — the core-installable invariant."""
    blocked = {"shap", "sklearn", "numpy", "torch", "anthropic"}
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        top = name.split(".")[0]
        if top in blocked:
            raise ImportError(f"blocked for test: {name}")
        return real_import(name, *args, **kwargs)

    for mod in list(sys.modules):
        if mod.split(".")[0] in blocked:
            monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.delitem(sys.modules, "kanatir.core.xai.explained", raising=False)
    monkeypatch.delitem(sys.modules, "kanatir.core.xai.explainer", raising=False)
    monkeypatch.setattr(builtins, "__import__", guarded)

    explained = importlib.import_module("kanatir.core.xai.explained")
    explainer = importlib.import_module("kanatir.core.xai.explainer")
    assert explained.EXPLAINED_SCHEMA_VERSION == "1.0.0"
    assert explainer.TemplatedExplainer().kind == "templated"


def test_claude_module_imports_without_anthropic(monkeypatch):
    """claude.py imports with no anthropic SDK present (guarded lazy import)."""
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] == "anthropic":
            raise ImportError("blocked for test: anthropic")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    monkeypatch.delitem(sys.modules, "kanatir.core.xai.claude", raising=False)
    monkeypatch.setattr(builtins, "__import__", guarded)

    claude = importlib.import_module("kanatir.core.xai.claude")
    assert claude.ClaudeExplainer.kind == "claude"


# -- ClaudeExplainer: no network, no key ------------------------------------


def test_claude_explainer_raises_without_key(monkeypatch):
    from kanatir.core.xai.claude import ClaudeExplainer, ClaudeExplainerError

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    expl = ClaudeExplainer(api_key=None)
    with pytest.raises(ClaudeExplainerError):
        expl.explain(_alert())


def test_claude_explainer_falls_back_when_configured(monkeypatch):
    """With fallback enabled and no key, the record honestly says 'templated'."""
    from kanatir.core.xai.claude import ClaudeExplainer

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    expl = ClaudeExplainer(api_key=None, fallback_to_templated=True)
    exp = expl.explain(_alert())
    assert exp.explainer_kind == "templated"  # not "claude" — honest


def test_claude_explainer_mocked_success_no_network():
    """Fake the SDK client entirely — no network, no key. Verifies the record is
    built with explainer_kind='claude' and the model's prose."""
    from kanatir.core.xai.claude import ClaudeExplainer

    class _Block:
        type = "text"
        text = "Operator summary: zone-A nominal, info-level, mechanics only."

    class _Resp:
        content = [_Block()]

    class _Messages:
        def create(self, **kwargs):
            return _Resp()

    class _FakeClient:
        messages = _Messages()

    expl = ClaudeExplainer(api_key="fake-key-not-used")
    expl._client = _FakeClient()  # inject fake; _get_client returns it

    exp = expl.explain(_alert())
    assert exp.explainer_kind == "claude"
    assert "Operator summary" in exp.explanation_text
    # attribution still honest even on the Claude path (no fabrication)
    assert exp.attribution_available is False


# -- gate path is templated only --------------------------------------------


def test_default_explainer_selection_is_templated(monkeypatch):
    monkeypatch.delenv("XAI_EXPLAINER", raising=False)
    from kanatir.core.xai.__main__ import select_explainer

    assert select_explainer().kind == "templated"


def test_unknown_explainer_env_falls_back_to_templated(monkeypatch):
    monkeypatch.setenv("XAI_EXPLAINER", "banana")
    from kanatir.core.xai.__main__ import select_explainer

    assert select_explainer().kind == "templated"


def test_claude_selection_only_on_explicit_optin(monkeypatch):
    monkeypatch.setenv("XAI_EXPLAINER", "claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from kanatir.core.xai.__main__ import select_explainer

    # selection succeeds (construction is lazy); it would only fail on .explain()
    assert select_explainer().kind == "claude"
