"""
Scaffolded detectors — LSTM autoencoder and GNN.

Both implement the Detector protocol so the ensemble can hold a full roster and
the interface is exercised, but neither is on the M4 live-gate path. They are
NOT trainable on the synthetic media available at this gate:

  - LSTM-AE needs a temporal SEQUENCE of feature vectors plus a fitted model;
    the reconstruction error is the anomaly score. Meaningful only once real
    media produces populated, time-ordered fused-object streams.
  - GNN needs MULTIPLE co-present objects per window to build a graph; a single
    fused object per window has no graph structure to reason over.

They report is_ready=False until a model is actually loaded, so the ensemble
skips them rather than emitting a fabricated score. torch is imported lazily
inside the (currently inert) model-build paths so importing this module needs
no [ade] extra. This is deliberate honesty-at-TRL-3: the capability slot exists
and is wired, the claim of a working learned sequence/graph detector does not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


class _ScaffoldDetector:
    """Shared scaffold: wired to the interface, not yet trainable. is_ready=False."""

    name = "scaffold"

    def __init__(self) -> None:
        self._model = None

    @property
    def is_ready(self) -> bool:
        return self._model is not None  # always False until a model is loaded

    def fit(self, X: np.ndarray) -> None:
        raise NotImplementedError(
            f"{self.name}: training requires real time-ordered / multi-object "
            f"fused-object data not available at the M4 gate; scaffold only."
        )

    def score(self, x: np.ndarray) -> float:
        raise RuntimeError(
            f"{self.name} is not ready (scaffold). Ensemble must skip "
            f"detectors whose is_ready is False."
        )


class LSTMAutoencoderDetector(_ScaffoldDetector):
    """Sequence reconstruction-error detector. Scaffold — see module docstring."""

    name = "lstm_autoencoder"


class GNNDetector(_ScaffoldDetector):
    """Graph-structure detector over co-present objects. Scaffold — see module docstring."""

    name = "gnn"
