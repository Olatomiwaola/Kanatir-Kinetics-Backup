"""
masked_view.py — M7 / TRL-3: a Detector-protocol wrapper that applies a fixed
feature-column mask at BOTH fit and score time, around an inner detector.

Why this exists: the M7 fit corpus is acoustic-led (ESC-50 ambient), structurally
all n_modalities=1. Letting the fitted IsolationForest see n_modalities would
teach it "1 modality = normal" and flag legitimate multimodal (n_modalities=2)
events as anomalous purely on modality count — a corpus-design artifact, not a
real signal. So the detector trains and scores on a view of the 8-dim feature
vector with n_modalities (index 6) dropped.

The full featurizer (extract_features, FEATURE_NAMES, FEATURE_DIM,
FEATURE_SCHEMA_VERSION) is UNCHANGED — n_modalities stays on FusedObject,
TriagedAlert, ExplainedAlert, and all lineage. Only the detector's *view* is
masked. Because the wrapper (not the bare inner detector) is what gets
serialized into the model artifact, the live score path is guaranteed to use
the identical column view as the fit path — they are the same object.

This is a per-gate feature-view decision, recorded explicitly in the artifact
metadata (feature_view, kept_indices, excluded_features) so a reviewer can audit
exactly which columns the M7 detector saw. Phase 2 reintroduces n_modalities
once a proper multimodal normal corpus exists.
"""

from __future__ import annotations

# M7 acoustic-led view: drop n_modalities (index 6 in FEATURE_NAMES).
M7_FEATURE_VIEW = "m7_acoustic_led_no_n_modalities"
M7_KEPT_INDICES: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 7)
M7_EXCLUDED_FEATURES: tuple[str, ...] = ("n_modalities",)

# M9 acoustic-event-aware view (TRL 3->4): same n_modalities exclusion rationale
# as M7 (the m9 fit corpus is still acoustic-led / structurally n_modalities=1,
# so letting the detector see modality count would teach a corpus-design
# artifact), PLUS the 8 appended acoustic-event features (indices 8..15) which
# are exactly what this block adds to recover acoustic distinctiveness. Index 6
# (n_modalities) dropped; everything else kept.
#
# These M7 constants are retained UNCHANGED so the sealed M7 fit remains
# reproducible from fit_ade.py at the M7 commit. The m9 fit uses the M9 view.
M9_FEATURE_VIEW = "m9_acoustic_event_aware"
M9_KEPT_INDICES: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15)
M9_EXCLUDED_FEATURES: tuple[str, ...] = ("n_modalities",)


class MaskedFeatureView:
    """Wraps an inner Detector, presenting it a masked column view of every
    feature vector at fit and score time. Conforms to the Detector protocol
    (name, is_ready, fit, score)."""

    def __init__(
        self,
        inner_detector,
        kept_indices: tuple[int, ...] = M7_KEPT_INDICES,
        excluded_features: tuple[str, ...] = M7_EXCLUDED_FEATURES,
        feature_view: str = M7_FEATURE_VIEW,
    ) -> None:
        self._inner = inner_detector
        self.kept_indices = tuple(kept_indices)
        self.excluded_features = tuple(excluded_features)
        self.feature_view = feature_view

    @property
    def name(self) -> str:
        # Surface the inner detector's name so downstream detector_scores keys
        # are unchanged (e.g. "isolation_forest"), keeping lineage/reporting stable.
        return self._inner.name

    @property
    def inner_detector(self):
        return self._inner

    @property
    def is_ready(self) -> bool:
        return self._inner.is_ready

    def fit(self, X) -> None:
        """Accept a full FEATURE_DIM-column matrix; fit the inner detector on the
        masked column view."""
        import numpy as np

        Xf = np.asarray(X, dtype=np.float64)
        if Xf.ndim != 2:
            raise ValueError(f"fit expects a 2-D matrix, got shape {Xf.shape}")
        self._inner.fit(Xf[:, list(self.kept_indices)])

    def score(self, x) -> float:
        """Accept a full FEATURE_DIM-length vector; score the inner detector on
        the masked view."""
        import numpy as np

        xf = np.asarray(x, dtype=np.float64).reshape(-1)
        return float(self._inner.score(xf[list(self.kept_indices)]))
