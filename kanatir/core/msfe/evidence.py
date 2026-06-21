"""
Evidence mapping — turn a FeatureEnvelope into a Dempster-Shafer mass function.

Each modality has its own mapper. This is the only modality-aware code in MSFE;
everything else (windowing, combination, the fused-object contract) is generic.
Adding rf/environmental/mobility later means adding a mapper here and registering
it in MAPPERS — no change to the fusion core or the consumer loop.

The mappers are deliberately conservative: they put substantial mass on UNKNOWN
unless the evidence is clear. That is the honest Dempster-Shafer posture and it
keeps a single weak sensor from dominating a fused object.
"""

from __future__ import annotations

from collections.abc import Callable

from kanatir.core.msfe.dempster_shafer import Mass, vacuous
from kanatir.core.msfe.fused import UNKNOWN
from kanatir.pipelines.common.envelope import (
    AcousticFeatures,
    FeatureEnvelope,
    Modality,
    VideoFeatures,
)

# How class labels coming off the pipelines map onto our frame of discernment.
# CVP/YOLO labels -> hypotheses.
_VIDEO_CLASS_MAP = {
    "uav": "UAV",
    "drone": "UAV",
    "aircraft": "UAV",
    "person": "GROUND",
    "car": "GROUND",
    "truck": "GROUND",
    "bicycle": "GROUND",
    "motorcycle": "GROUND",
    "bus": "GROUND",
}

# Acoustic (YAMNet) label fragments -> hypotheses. Matched case-insensitively as
# substrings because YAMNet labels are descriptive phrases.
_ACOUSTIC_LABEL_MAP = (
    ("drone", "UAV"),
    ("aircraft", "UAV"),
    ("propeller", "UAV"),
    ("helicopter", "UAV"),
    ("vehicle", "GROUND"),
    ("engine", "GROUND"),
    ("car", "GROUND"),
    ("truck", "GROUND"),
    ("footsteps", "GROUND"),
    ("speech", "GROUND"),
    ("wind", "AMBIENT"),
    ("silence", "AMBIENT"),
    ("rain", "AMBIENT"),
    ("bird", "AMBIENT"),
)


def video_to_mass(env: FeatureEnvelope) -> Mass:
    """
    Map video detections to a BBA. No detections -> vacuous (all on UNKNOWN):
    an empty frame is *absence of evidence*, not evidence of AMBIENT. The
    highest-confidence detection drives the assignment; its confidence becomes
    the mass on its hypothesis, with the remainder left as ignorance.
    """
    feats = env.features
    if not isinstance(feats, VideoFeatures) or not feats.detections:
        return vacuous()

    best = max(feats.detections, key=lambda d: d.confidence)
    hyp = _VIDEO_CLASS_MAP.get(best.cls.lower())
    if hyp is None:
        # Saw *something* but can't place it -> mild GROUND lean, mostly unknown.
        return {"GROUND": 0.2 * best.confidence, UNKNOWN: 1.0 - 0.2 * best.confidence}

    m = max(0.0, min(1.0, best.confidence))
    return {hyp: m, UNKNOWN: 1.0 - m}


def acoustic_to_mass(env: FeatureEnvelope) -> Mass:
    """
    Map the top YAMNet label to a BBA. The score becomes the mass on the matched
    hypothesis; unmatched or empty -> vacuous. We damp the acoustic mass slightly
    (0.85) relative to its raw score because single-window acoustic class scores
    are noisier than tracked video detections — this is the per-modality
    reliability discount that Dempster-Shafer handles naturally.
    """
    feats = env.features
    if not isinstance(feats, AcousticFeatures) or not feats.yamnet_top:
        return vacuous()

    label, score = max(feats.yamnet_top, key=lambda ls: ls[1])
    low = label.lower()
    hyp = None
    for frag, h in _ACOUSTIC_LABEL_MAP:
        if frag in low:
            hyp = h
            break
    if hyp is None:
        return vacuous()

    m = max(0.0, min(1.0, score)) * 0.85
    return {hyp: m, UNKNOWN: 1.0 - m}


MAPPERS: dict[Modality, Callable[[FeatureEnvelope], Mass]] = {
    Modality.VIDEO: video_to_mass,
    Modality.ACOUSTIC: acoustic_to_mass,
}


def envelope_to_mass(env: FeatureEnvelope) -> Mass:
    """Dispatch to the modality mapper. Unknown modality -> vacuous (ignorance)."""
    mapper = MAPPERS.get(env.modality)
    if mapper is None:
        return vacuous()
    return mapper(env)
