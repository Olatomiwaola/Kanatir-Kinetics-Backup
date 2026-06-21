"""
Dempster-Shafer evidence combination for MSFE.

Pure Python, zero dependencies. This is the fusion math: each sensor modality
contributes a *mass function* (basic belief assignment, BBA) over the frame of
discernment, and we combine them with Dempster's rule of combination.

Why Dempster-Shafer rather than weighted Bayesian for this platform:

  - It represents *ignorance* explicitly. When the camera sees nothing (empty
    detections, exactly the Sprint 3-4 live video sample) it can put mass on
    UNKNOWN — "I have no evidence" — instead of being forced to assert a
    probability. Bayesian fusion has no clean way to say "I don't know"; it must
    spread a prior, which manufactures false confidence.
  - It surfaces *conflict*. Dempster's rule produces a conflict mass K when
    sources disagree. We report K rather than normalizing it away silently
    (Zadeh's well-known critique). High K is a useful downstream anomaly signal:
    sensors that should agree but don't.

Frame of discernment Theta = {UAV, GROUND, AMBIENT}. We work over the focal
elements we actually use: the three singletons plus Theta itself (= UNKNOWN,
total ignorance). This keeps the power set small and the math transparent for
the M3 gate; a richer focal-element set can be introduced later behind the same
interface without touching MSFE callers.
"""

from __future__ import annotations

from collections.abc import Iterable

from kanatir.core.msfe.fused import HYPOTHESES, UNKNOWN

# A mass function maps a focal element to its mass. Focal elements here are the
# singleton hypotheses ("UAV", ...) and UNKNOWN (the full frame Theta).
Mass = dict[str, float]

_FOCAL = (*HYPOTHESES, UNKNOWN)


def vacuous() -> Mass:
    """The vacuous BBA: all mass on UNKNOWN. The identity for combination."""
    return {UNKNOWN: 1.0}


def normalize_bba(raw: Mass) -> Mass:
    """
    Clean a proposed mass function: drop unknown labels, clamp negatives, and
    normalize to sum 1.0. Any residual (or an all-zero input) lands on UNKNOWN,
    so a sensor that offers no usable evidence becomes vacuous rather than
    invalid.
    """
    cleaned: Mass = {}
    for label, m in raw.items():
        if label not in _FOCAL:
            continue
        if m <= 0.0:
            continue
        cleaned[label] = cleaned.get(label, 0.0) + float(m)

    total = sum(cleaned.values())
    if total <= 0.0:
        return vacuous()
    if total > 1.0:
        cleaned = {k: v / total for k, v in cleaned.items()}
        total = 1.0
    # Any leftover mass is ignorance.
    residual = 1.0 - total
    if residual > 1e-12:
        cleaned[UNKNOWN] = cleaned.get(UNKNOWN, 0.0) + residual
    return cleaned


def _intersect(a: str, b: str) -> str | None:
    """
    Intersection of two focal elements in our restricted lattice.
      - UNKNOWN (= Theta) intersect X = X
      - singleton intersect itself = itself
      - two different singletons = empty set (None) -> contributes to conflict K
    """
    if a == UNKNOWN:
        return b
    if b == UNKNOWN:
        return a
    return a if a == b else None


def combine_pair(m1: Mass, m2: Mass) -> tuple[Mass, float]:
    """
    Dempster's rule of combination for two mass functions.

    Returns (combined_mass, conflict_K). The combined mass is normalized by
    (1 - K). K == 1.0 means total conflict (orthogonal singletons) and the
    combination is undefined; we guard that case by returning a vacuous result
    with K reported, so the caller can flag it rather than crash.
    """
    m1 = normalize_bba(m1)
    m2 = normalize_bba(m2)

    combined: Mass = {}
    conflict = 0.0
    for a, ma in m1.items():
        for b, mb in m2.items():
            inter = _intersect(a, b)
            prod = ma * mb
            if inter is None:
                conflict += prod
            else:
                combined[inter] = combined.get(inter, 0.0) + prod

    if conflict >= 1.0 - 1e-12:
        # Total conflict: rule is undefined. Report it; fall back to ignorance.
        return vacuous(), 1.0

    scale = 1.0 / (1.0 - conflict)
    combined = {k: v * scale for k, v in combined.items()}
    return combined, conflict


def combine_all(masses: Iterable[Mass]) -> tuple[Mass, float]:
    """
    Fold Dempster's rule across many mass functions.

    Returns (combined_mass, max_pairwise_conflict). We report the *maximum*
    conflict encountered across the sequential combinations as the object's K:
    it is the most conservative single number to surface ("at some point two
    bodies of evidence disagreed this much"). Combining in input order is
    well-defined because Dempster's rule is commutative and associative.
    """
    acc = vacuous()
    max_k = 0.0
    any_evidence = False
    for m in masses:
        any_evidence = True
        acc, k = combine_pair(acc, m)
        max_k = max(max_k, k)
    if not any_evidence:
        return vacuous(), 0.0
    return normalize_bba(acc), max_k
