"""
Isolation Forest detector — the live-gate detector for M4.

Chosen as the live path because it is the only one of the three planned learned
detectors that is stateless at score time and needs no warmup, no sequence
buffer, and no graph construction: fit once on normal feature vectors, then
score any single vector instantly. That makes it the fastest defensible thing
to put end-to-end on the bus, and it deploys cleanly to the Jetson edge target
(sklearn inference is light — no torch runtime needed on the gate path).

sklearn is imported LAZILY inside methods, never at module top level, so
`import kanatir.core.ade.detectors.isolation_forest` succeeds on a core-only
install with no [ade] extra; the heavy dep is only touched when the detector is
actually constructed/used.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


class IsolationForestDetector:
    """Wraps sklearn's IsolationForest behind the Detector protocol.

    score() returns a non-negative anomaly score: we negate sklearn's
    score_samples (higher = more normal there) and shift so higher = more
    anomalous here, consistent with the Detector contract.
    """

    name = "isolation_forest"

    def __init__(self, n_estimators: int = 100, contamination: float | str = "auto",
                 random_state: int = 42) -> None:
        self._n_estimators = n_estimators
        self._contamination = contamination
        self._random_state = random_state
        self._model = None  # lazily built on fit

    @property
    def is_ready(self) -> bool:
        return self._model is not None

    def fit(self, X: np.ndarray) -> None:
        from sklearn.ensemble import IsolationForest

        model = IsolationForest(
            n_estimators=self._n_estimators,
            contamination=self._contamination,
            random_state=self._random_state,
        )
        model.fit(X)
        self._model = model

    def score(self, x: np.ndarray) -> float:
        if self._model is None:
            raise RuntimeError("IsolationForestDetector.score called before fit")
        import numpy as np

        row = np.asarray(x, dtype=np.float64).reshape(1, -1)
        # score_samples: higher = more normal. Negate so higher = more anomalous,
        # then clamp at 0 so the contract (non-negative) holds.
        raw = float(self._model.score_samples(row)[0])
        return max(0.0, -raw)
