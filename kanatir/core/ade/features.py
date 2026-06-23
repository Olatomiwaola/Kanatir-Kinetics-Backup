"""
Feature extraction — FusedObject -> stable numeric feature vector.

This is the bridge from the MSFE output contract to the detector ensemble, and
the real design surface of ADE: every detector, the adaptive baseline, and the
conflict path all consume the vector this module produces. It is the analogue
of MSFE's evidence.py mappers (envelope -> mass); here it is fused-object ->
vector.

Invariants this module guarantees:
  - POSITIONAL STABILITY. BeliefMass.masses is a dict whose UNKNOWN key may or
    may not be present and whose iteration order is not contractual. We read
    masses by a FIXED key order with .get(key, 0.0) and NEVER iterate the dict,
    so feature[i] means the same thing for every object. Iterating dict order
    here would silently corrupt every downstream detector — this is the bug the
    extractor exists to prevent.
  - ML-FREE AT IMPORT. numpy is imported lazily inside the function, not at
    module top level, so `import kanatir.core.ade.features` succeeds on a
    core-only install with no [ade] extra present. (numpy is light, but the
    invariant is "core imports pull no optional deps", and we keep it uniform.)
  - conflict_k is included as a feature so the adaptive baseline LEARNS the
    normal distribution of sensor disagreement for a given deployment, rather
    than us asserting a universal hardcoded conflict threshold. (TRL-6 recheck:
    real sensor disagreement is dominated by miscalibration / skew / occlusion,
    not genuine anomalies, so conflict must be calibrated per-deployment, not
    force-flagged.)
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from kanatir.core.msfe.fused import HYPOTHESES, UNKNOWN, FusedObject

if TYPE_CHECKING:
    import numpy as np

# Fixed, ordered feature layout. The vector is built in EXACTLY this order every
# time. Adding a feature = append to the end (keeps existing indices stable);
# never insert in the middle without a deliberate version bump downstream.
#
#   0..2  : specific-hypothesis masses, in HYPOTHESES order (UAV, GROUND, AMBIENT)
#   3     : UNKNOWN (ignorance) mass
#   4     : conflict_k  (D-S conflict mass; a tracked input, NOT an override)
#   5     : confidence  (== belief.top_confidence, the winning specific mass)
#   6     : n_modalities (how many distinct sensor types agreed to this object)
#   7     : belief entropy over the full mass assignment (spread of belief)

# Bump this on any change to FEATURE_NAMES / FEATURE_DIM. The ADE model artifact
# pins this value; ADE startup hard-fails if a loaded model's version != this.
FEATURE_SCHEMA_VERSION = "1.0.0"

FEATURE_NAMES: tuple[str, ...] = (
    *(f"mass_{h}" for h in HYPOTHESES),
    f"mass_{UNKNOWN}",
    "conflict_k",
    "confidence",
    "n_modalities",
    "belief_entropy",
)
FEATURE_DIM = len(FEATURE_NAMES)


def _belief_entropy(masses: dict[str, float]) -> float:
    """Shannon entropy (nats) over the mass assignment. High = diffuse belief,
    low = belief concentrated on one hypothesis. A natural scalar anomaly cue
    independent of *which* hypothesis won."""
    h = 0.0
    for v in masses.values():
        if v > 0.0:
            h -= v * math.log(v)
    return h


def extract_features(obj: FusedObject) -> np.ndarray:
    """Map a FusedObject to a fixed-length, positionally-stable feature vector.

    Pure function, no side effects, no infra. numpy imported lazily so this
    module is import-safe without the [ade] extra.
    """
    import numpy as np

    masses = obj.belief.masses
    vec = [
        *(float(masses.get(h, 0.0)) for h in HYPOTHESES),  # 0..2 specific masses
        float(masses.get(UNKNOWN, 0.0)),                   # 3 ignorance
        float(obj.belief.conflict_k),                      # 4 conflict (tracked input)
        float(obj.confidence),                             # 5 winning confidence
        float(obj.n_modalities),                           # 6 modality count
        _belief_entropy(masses),                           # 7 belief spread
    ]
    return np.asarray(vec, dtype=np.float64)
