"""
Detector interface — the common contract every ADE detector implements.

ML-free by construction: this is a typing.Protocol plus a tiny registry. No
sklearn/torch here, so `import kanatir.core.ade.detectors` succeeds on a
core-only install. Concrete detectors import their heavy deps lazily inside
their own methods.

A Detector turns a feature vector into a single anomaly score (higher = more
anomalous). `fit` is optional for stateless detectors (Isolation Forest scores
immediately once fit; the learned sequence/graph detectors need real training
data and are scaffolded behind this same interface for a later block).

This is also the seam for the head-to-head topology evaluation: a TDA /
persistent-homology detector, IF it ever earns a slot, implements this exact
Protocol and is scored on the same feature stream as the others. Topology is
NOT a pillar — the interface is what lets it compete on merit, nothing more.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np


@runtime_checkable
class Detector(Protocol):
    """A single anomaly detector. Higher score == more anomalous."""

    name: str

    def fit(self, X: np.ndarray) -> None:
        """Fit on a matrix of normal feature vectors. No-op for stateless ones."""
        ...

    def score(self, x: np.ndarray) -> float:
        """Score a single feature vector. Returns a non-negative anomaly score."""
        ...

    @property
    def is_ready(self) -> bool:
        """True once the detector can score (fitted / loaded). Scaffolded
        detectors report False until their model exists."""
        ...
