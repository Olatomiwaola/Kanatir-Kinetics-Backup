"""
model_io.py — M7 / TRL-3: load a serialized ADE model artifact, validate its
feature-schema pins against the CURRENT featurizer, and inject the fitted
detector into a fresh AnomalyEnsemble with a fresh AdaptiveBaseline.

Why a fresh baseline: the AdaptiveBaseline is a rolling, deployment-specific
state that must warm up on the traffic it actually sees at the gate — it is NOT
part of the trained artifact. We persist the fitted DETECTOR only; the baseline
semantics (WARMUP-not-escalating, conflict-as-tracked-input) are unchanged from
M4. This keeps training (offline, auditable) and live inference cleanly split.

Schema drift is a HARD FAILURE. When ADE_MODEL_PATH is set, ADE must never fall
back to an unfitted detector — a gate run that silently scored TRL-3 traffic
with an unfitted model would be an undetectable validity hole. AdeModelIncompatible
names the exact field that drifted.
"""

from __future__ import annotations

from dataclasses import dataclass

from kanatir.core.ade.baseline import AdaptiveBaseline
from kanatir.core.ade.ensemble import AnomalyEnsemble
from kanatir.core.ade.features import (
    FEATURE_DIM,
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
)


class AdeModelIncompatible(Exception):
    """Raised when a loaded artifact's pinned feature schema does not match the
    current featurizer. Carries the name of the first field that drifted."""

    def __init__(self, field: str, expected: object, found: object) -> None:
        self.field = field
        self.expected = expected
        self.found = found
        super().__init__(
            f"ADE model artifact incompatible: {field} drifted "
            f"(featurizer expects {expected!r}, artifact has {found!r}). "
            f"Refusing to score gate traffic with a mismatched model."
        )


@dataclass
class LoadedModel:
    """Result of a successful load: the ready-to-run ensemble plus the audit
    metadata ADE logs at startup."""

    ensemble: AnomalyEnsemble
    n_samples: int
    corpus_id: str
    feature_schema_version: str


def _validate_pins(artifact: dict) -> None:
    """Exact-equality checks against the current featurizer contract. First
    drift raises AdeModelIncompatible(field)."""
    if tuple(artifact.get("feature_names", ())) != tuple(FEATURE_NAMES):
        raise AdeModelIncompatible(
            "feature_names", tuple(FEATURE_NAMES), tuple(artifact.get("feature_names", ()))
        )
    if artifact.get("feature_dim") != FEATURE_DIM:
        raise AdeModelIncompatible("feature_dim", FEATURE_DIM, artifact.get("feature_dim"))
    if artifact.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        raise AdeModelIncompatible(
            "feature_schema_version", FEATURE_SCHEMA_VERSION, artifact.get("feature_schema_version")
        )


def load_fitted_ensemble(model_path: str) -> LoadedModel:
    """Load + validate the artifact at model_path and build a fresh ensemble
    around the fitted detector(s). Raises AdeModelIncompatible on schema drift;
    propagates load errors (missing file, corrupt pickle) unchanged. Never
    returns an unfitted ensemble."""
    import joblib

    artifact = joblib.load(model_path)

    _validate_pins(artifact)

    fitted = artifact.get("fitted_detectors") or {}
    detectors = list(fitted.values())
    if not detectors:
        raise AdeModelIncompatible("fitted_detectors", "at least one fitted detector", detectors)
    for d in detectors:
        if not getattr(d, "is_ready", False):
            raise AdeModelIncompatible(
                "fitted_detectors", "all detectors ready (fitted)", f"{d.name}: not ready"
            )

    ensemble = AnomalyEnsemble(detectors=detectors, baseline=AdaptiveBaseline())

    return LoadedModel(
        ensemble=ensemble,
        n_samples=int(artifact.get("n_samples", -1)),
        corpus_id=str(artifact.get("corpus_id", "unknown")),
        feature_schema_version=str(artifact.get("feature_schema_version")),
    )
