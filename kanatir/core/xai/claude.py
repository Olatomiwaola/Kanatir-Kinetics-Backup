"""
ClaudeExplainer — optional demo enrichment via the real Anthropic SDK.

NON-AUTHORITATIVE, NON-GATING, OFF THE REAL-TIME PATH. The authoritative M6
output is TemplatedExplainer (deterministic, offline, in explainer.py). This
adapter exists only to produce richer operator-facing prose for the demo
capture, and is selected ONLY by explicit opt-in:

    XAI_EXPLAINER=claude

Per the project architecture note, the Claude API is appropriate for XAI because
explanation is offline / operator-facing — it is NEVER on the real-time alert
path (5-second latency budget). This module must never be the CI path or the M6
gate path; the gate runs templated only.

Design:
  - The `anthropic` SDK is imported LAZILY and GUARDED — inside __init__/_client,
    never at module top. `import kanatir.core.xai.claude` therefore succeeds with
    no `anthropic` installed (it lives in the separate `[xai-claude]` extra).
  - Attribution is delegated to the SAME honest path as the templated explainer
    (TemplatedExplainer.attribute). Claude rewrites PROSE; it does not invent
    attributions. SHAP numbers, when present, are passed to the model as facts to
    narrate, not as something for it to fabricate.
  - Failure is explicit. Missing SDK or missing API key raises a clear error,
    unless `fallback_to_templated=True` is explicitly configured — in which case
    it falls back and the resulting record honestly carries
    explainer_kind="templated", not "claude".
  - explainer_kind="claude" lands on every record this produces, so a reviewer
    can tell at a glance the prose came from the model, not the deterministic path.
"""

from __future__ import annotations

import os

from kanatir.core.csat.alert import TriagedAlert
from kanatir.core.xai.explained import ExplainedAlert
from kanatir.core.xai.explainer import TemplatedExplainer

# Offline / operator-facing only — never the real-time path. Sonnet is plenty
# for narrating a handful of structured fields into operator prose.
_DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 1000


class ClaudeExplainerError(RuntimeError):
    """Raised when the Claude path is selected but cannot run and no fallback
    was explicitly configured."""


class ClaudeExplainer:
    """Optional, opt-in, non-gating. Same Explainer protocol as TemplatedExplainer."""

    kind = "claude"

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        api_key: str | None = None,
        fallback_to_templated: bool = False,
        fitted_model=None,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._fallback_to_templated = fallback_to_templated
        # Reuse the templated explainer for attribution (honest, shared) and as
        # the fallback prose source if configured.
        self._templated = TemplatedExplainer(fitted_model=fitted_model)
        self._client = None  # lazily constructed

    # -- guarded SDK access ------------------------------------------------

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import anthropic  # lazy: lives in the [xai-claude] extra
        except ImportError as exc:
            raise ClaudeExplainerError(
                "anthropic SDK not installed — install the '[xai-claude]' extra "
                "to use XAI_EXPLAINER=claude (the templated explainer needs nothing)"
            ) from exc
        if not self._api_key:
            raise ClaudeExplainerError(
                "ANTHROPIC_API_KEY not set — required for XAI_EXPLAINER=claude"
            )
        self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    # -- prompt construction ----------------------------------------------

    @staticmethod
    def _build_prompt(alert: TriagedAlert, attribution_note: str, attributions) -> str:
        attr_lines = (
            "\n".join(f"  - {a.feature}: {a.value:+.4f} ({a.direction})" for a in attributions)
            if attributions
            else f"  (none — {attribution_note})"
        )
        modalities = sorted({c.modality for c in alert.contributors})
        return (
            "You are writing a concise operator-facing explanation of an urban "
            "anomaly alert. Use ONLY the facts below. Do not invent threat "
            "assessments, do not fabricate attributions, do not add severity not "
            "stated. If severity is info, make clear this describes pipeline "
            "mechanics, not a real threat. 2-4 sentences, plain operator English.\n\n"
            f"Severity: {alert.severity.value}\n"
            f"Classification: {alert.classification}\n"
            f"Anomaly score: {alert.anomaly_score:.3f}\n"
            f"Baseline state: {alert.baseline_state.value}\n"
            f"Sensor-fusion conflict K: {alert.conflict_k:.3f}\n"
            f"Anomalies suppressed into this alert: {alert.suppressed_count}\n"
            f"Contributing modalities: {', '.join(modalities) or 'none'}\n"
            f"Audit events preserved: {len(alert.audit_event_ids)}\n"
            f"Detector attributions:\n{attr_lines}\n"
        )

    @staticmethod
    def _extract_text(response) -> str:
        # Find text blocks by type, not position (mirrors the SDK content model).
        parts = [
            getattr(block, "text", "")
            for block in getattr(response, "content", [])
            if getattr(block, "type", None) == "text"
        ]
        return " ".join(p for p in parts if p).strip()

    # -- interface ---------------------------------------------------------

    def explain(self, alert: TriagedAlert) -> ExplainedAlert:
        # Attribution comes from the shared honest path — Claude never fabricates it.
        attributions, available, note = self._templated.attribute(alert)

        try:
            client = self._get_client()
            prompt = self._build_prompt(alert, note, attributions)
            response = client.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            text = self._extract_text(response)
            if not text:
                raise ClaudeExplainerError("Claude returned no text content")
        except ClaudeExplainerError:
            if self._fallback_to_templated:
                # Honest: the record reflects that the templated path actually ran.
                return self._templated.explain(alert)
            raise

        return ExplainedAlert.carry_from_alert(
            alert,
            attributions=attributions,
            attribution_available=available,
            attribution_note=note,
            explanation_text=text,
            explainer_kind=self.kind,
        )
