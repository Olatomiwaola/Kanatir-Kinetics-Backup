"""
Ensemble — combine ready detectors into one scalar, score it through the
adaptive baseline, and assemble the AnomalyRecord.

Combiner is deliberately simple and explainable: the mean of the ready
detectors' scores (skipping any detector whose is_ready is False, so a
scaffolded LSTM/GNN never contributes a fabricated number). No learned
meta-model — at TRL 3 with a DRDC honesty constraint we will not ship a
combiner we cannot justify.

is_anomaly is SINGLE-PATH, baseline-driven (TRL-6 recheck): conflict_k is a
first-class TRACKED INPUT the baseline learns the normal distribution of, NOT a
hardcoded override. Real sensor disagreement is dominated by miscalibration /
clock skew / occlusion, not genuine anomalies, so a fixed conflict threshold
would false-alarm on ordinary fusion messiness in a relevant operational
environment (e.g. it would have fired on the Sprint 5-6 capture_ts/ingest_ts
timing artifact). conflict_k is still surfaced first-class on the record for
explainability — it just doesn't get to unilaterally flag.

ML-free at import: numpy lazy; detectors bring their own deps lazily.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kanatir.core.ade.anomaly import AnomalyRecord
from kanatir.core.ade.baseline import AdaptiveBaseline
from kanatir.core.ade.detectors import Detector
from kanatir.core.ade.features import extract_features
from kanatir.core.msfe.fused import FusedObject

if TYPE_CHECKING:
    import numpy as np

# Weight on conflict_k when blending it into the scalar the baseline tracks.
# conflict_k is already a feature in the vector; here we also fold it into the
# combined scalar so the baseline's "normal" reflects normal disagreement too.
_CONFLICT_BLEND = 0.5


class AnomalyEnsemble:
    """Holds a roster of detectors and one adaptive baseline; scores FusedObjects."""

    def __init__(self, detectors: list[Detector], baseline: AdaptiveBaseline | None = None) -> None:
        self._detectors = detectors
        self._baseline = baseline or AdaptiveBaseline()

    @property
    def ready_detectors(self) -> list[Detector]:
        return [d for d in self._detectors if d.is_ready]

    def fit(self, X: np.ndarray) -> None:
        """Fit any fittable detectors on a matrix of normal feature vectors.
        Scaffolded detectors (is_ready False, fit raises NotImplementedError)
        are skipped, not fatal."""
        for d in self._detectors:
            try:
                d.fit(X)
            except NotImplementedError:
                continue  # scaffold; leave it not-ready

    def _combine(self, feats: np.ndarray, conflict_k: float) -> tuple[float, dict[str, float]]:
        """Combine ready-detector scores + conflict into one scalar for the
        baseline. Returns (scalar, per-detector raw scores for explainability)."""
        scores: dict[str, float] = {}
        for d in self.ready_detectors:
            scores[d.name] = float(d.score(feats))

        detector_mean = sum(scores.values()) / len(scores) if scores else 0.0
        combined = (1.0 - _CONFLICT_BLEND) * detector_mean + _CONFLICT_BLEND * conflict_k

        # Record intermediates so a reviewer sees exactly how the number was built.
        scores["_detector_mean"] = detector_mean
        scores["_conflict_k_input"] = conflict_k
        scores["_combined_scalar"] = combined
        return combined, scores

    def process(self, obj: FusedObject) -> AnomalyRecord:
        """Score one FusedObject -> AnomalyRecord, lineage carried through."""
        feats = extract_features(obj)
        conflict_k = float(obj.belief.conflict_k)

        combined, detector_scores = self._combine(feats, conflict_k)
        anomaly_score, is_anomaly, state = self._baseline.score(combined)

        return AnomalyRecord(
            fused_id=obj.fused_id,
            window_start=obj.window_start,
            window_end=obj.window_end,
            geo=obj.geo,
            classification=obj.classification,
            anomaly_score=anomaly_score,
            is_anomaly=is_anomaly,
            baseline_state=state,
            conflict_k=conflict_k,
            detector_scores=detector_scores,
            contributors=obj.contributors,  # FULL lineage, verbatim
            source_track_refs=obj.source_track_refs,  # M5.1: refs preserved verbatim
        )
