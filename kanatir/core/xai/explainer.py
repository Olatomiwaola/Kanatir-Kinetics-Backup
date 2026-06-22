"""
Explainer interface + the deterministic, offline TemplatedExplainer.

The Explainer protocol is the seam that keeps the M6 consumer
implementation-agnostic: `__main__` holds an `Explainer` and calls `.explain()`,
never caring whether the concrete type is templated (default, authoritative) or
Claude-API-backed (optional demo enrichment in claude.py).

TemplatedExplainer is the AUTHORITATIVE M6 output: pure-stdlib NLG, fully
deterministic, offline, reproducible. CI and the live M6 gate run this and only
this. The same input alert always yields byte-identical prose — no clock, no
randomness, no network.

SHAP attribution sits BEHIND this interface with a lazy import: `shap` (and its
numpy/sklearn stack) is imported INSIDE `attribute()`, never at module top, so
`import kanatir.core.xai.explainer` succeeds on a core-only install with no
`[xai]` extra. Attribution honesty is enforced here, not just in the contract:
with no real detector scores or no fitted model, `attribute()` returns empty
attributions and a stated reason — it never fabricates a SHAP explanation over
empty/trivial input.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from kanatir.core.csat.alert import Severity, TriagedAlert
from kanatir.core.xai.explained import Attribution, ExplainedAlert

# Attribution is only meaningful when scores carry signal. A single trivial/zero
# score is not something to attribute against — say so rather than invent a bar.
_MIN_SCORES_FOR_ATTRIBUTION = 1


@runtime_checkable
class Explainer(Protocol):
    """The seam. Every explainer takes a TriagedAlert and returns an
    ExplainedAlert. Implementations differ only in attribution mechanism and
    prose generation; lineage/snapshot carry-forward is the contract's job
    (ExplainedAlert.carry_from_alert), identical across implementations.
    """

    kind: str  # "templated" | "claude" — lands on ExplainedAlert.explainer_kind

    def explain(self, alert: TriagedAlert) -> ExplainedAlert: ...


def _attribution_inputs_present(alert: TriagedAlert) -> tuple[bool, str]:
    """Decide whether there is anything real to attribute, and state why not.

    Honest gating: empty detector_scores, or a not-anomalous alert, means there
    is no decision to attribute. We report the reason on the record rather than
    manufacturing attributions over nothing.
    """
    if not alert.detector_scores:
        return False, "no detector_scores present on alert — nothing to attribute"
    if len(alert.detector_scores) < _MIN_SCORES_FOR_ATTRIBUTION:
        return False, "insufficient detector_scores to attribute"
    if alert.severity == Severity.INFO:
        return (
            False,
            "alert is severity=info (not anomalous) — attribution describes "
            "triage mechanics only, no anomaly decision to attribute",
        )
    return True, ""


class TemplatedExplainer:
    """Deterministic, stdlib-only, offline explainer. The authoritative M6 path."""

    kind = "templated"

    def __init__(self, *, fitted_model: Any | None = None) -> None:
        # A fitted detector (e.g. IsolationForest) can be supplied once real
        # media is available; SHAP attributes against it. Absent it (the TRL-3
        # synthetic case), attribution is honestly empty.
        self._fitted_model = fitted_model

    # -- attribution -------------------------------------------------------

    def attribute(self, alert: TriagedAlert) -> tuple[list[Attribution], bool, str]:
        """Return (attributions, available, note).

        SHAP is imported lazily INSIDE this method so the module stays ML-free
        on import. When inputs are absent or no fitted model is provided, returns
        empty attributions with a stated reason — never fabricated.
        """
        present, reason = _attribution_inputs_present(alert)
        if not present:
            return [], False, reason

        if self._fitted_model is None:
            return (
                [],
                False,
                "detector_scores present but no fitted model supplied — "
                "SHAP attribution requires a fitted detector; not fabricating",
            )

        # Real attribution path — lazy import keeps the module core-installable.
        try:
            import numpy as np  # noqa: F401  (lazy, [xai] extra)
            import shap  # noqa: F401  (lazy, [xai] extra)
        except ImportError:
            return (
                [],
                False,
                "shap/numpy not installed (install the '[xai]' extra) — "
                "attribution unavailable; not fabricating",
            )

        # With a fitted model + shap available, compute genuine attributions over
        # the detector_scores feature vector. (Exercised once real media yields a
        # fitted IsoForest; on synthetic media we never reach here because
        # _attribution_inputs_present gates INFO/empty-score alerts out above.)
        feature_names = sorted(alert.detector_scores)
        values = np.array([[alert.detector_scores[f] for f in feature_names]])
        explainer = shap.Explainer(self._fitted_model)
        shap_values = explainer(values)
        contributions = list(shap_values.values[0])

        attributions = [
            Attribution(
                feature=name,
                value=float(contrib),
                direction=_direction(float(contrib)),
            )
            for name, contrib in zip(feature_names, contributions, strict=True)
        ]
        return attributions, True, "SHAP attribution over fitted detector"

    # -- prose -------------------------------------------------------------

    def _narrate(
        self,
        alert: TriagedAlert,
        attributions: list[Attribution],
        attribution_available: bool,
        attribution_note: str,
    ) -> str:
        """Deterministic NLG. Pure string assembly — same input, same output."""
        loc = _location_phrase(alert)
        window_s = int((alert.window_end - alert.window_start).total_seconds())

        lines: list[str] = []
        lines.append(
            f"Alert {alert.alert_id[:8]} — severity {alert.severity.value.upper()}, "
            f"classification '{alert.classification}', at {loc}."
        )

        if alert.suppressed_count > 0:
            lines.append(
                f"This is a triage collapse: {alert.suppressed_count} further "
                f"anomal{'y' if alert.suppressed_count == 1 else 'ies'} were "
                f"suppressed into this single situational item over a ~{window_s}s "
                f"window ({len(alert.anomaly_ids)} anomalies total). "
                "It represents one continuous event, not many."
            )
        else:
            lines.append(
                f"This alert corresponds to a single anomaly over a ~{window_s}s window."
            )

        lines.append(
            f"Baseline state at detection: {alert.baseline_state.value}. "
            f"Anomaly score: {alert.anomaly_score:.3f}. "
            f"Sensor-fusion conflict (K): {alert.conflict_k:.3f}."
        )

        if alert.severity == Severity.INFO:
            lines.append(
                "Severity is INFO: the underlying detection was not anomalous "
                "(ignorance-collapsed input). This explanation describes triage "
                "MECHANICS — what the pipeline did — not a threat assessment."
            )
        elif alert.baseline_state.value == "warmup":
            lines.append(
                "Severity is capped at WATCH because the baseline was still in "
                "WARMUP — the model had not yet earned confidence to escalate."
            )

        if attribution_available and attributions:
            ranked = sorted(attributions, key=lambda a: abs(a.value), reverse=True)
            top = ranked[:3]
            frag = "; ".join(
                f"{a.feature} ({a.direction}, {a.value:+.3f})" for a in top
            )
            lines.append(f"Top contributing detectors: {frag}.")
        else:
            lines.append(f"Attribution: none available — {attribution_note}.")

        n_contrib = len(alert.contributors)
        modalities = sorted({c.modality for c in alert.contributors})
        lines.append(
            f"Lineage: {n_contrib} contributor(s) across "
            f"{', '.join(modalities) if modalities else 'no'} "
            f"modalit{'y' if len(modalities) == 1 else 'ies'}, "
            f"{len(alert.audit_event_ids)} audit event(s) preserved."
        )

        return " ".join(lines)

    # -- interface ---------------------------------------------------------

    def explain(self, alert: TriagedAlert) -> ExplainedAlert:
        attributions, available, note = self.attribute(alert)
        text = self._narrate(alert, attributions, available, note)
        return ExplainedAlert.carry_from_alert(
            alert,
            attributions=attributions,
            attribution_available=available,
            attribution_note=note,
            explanation_text=text,
            explainer_kind=self.kind,
        )


def _direction(value: float) -> str:
    if value > 0:
        return "increases"
    if value < 0:
        return "decreases"
    return "neutral"


def _location_phrase(alert: TriagedAlert) -> str:
    g = alert.geo
    if g.site_id:
        return f"site {g.site_id}"
    if g.lat is not None and g.lon is not None:
        return f"({g.lat:.5f}, {g.lon:.5f})"
    return "an ungeolocated location"
