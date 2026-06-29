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
from kanatir.core.msfe.rf_config import DEFAULT_RF_CONFIG, RFMapperConfig
from kanatir.pipelines.common.envelope import (
    AcousticFeatures,
    FeatureEnvelope,
    Modality,
    RFFeatures,
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


# --- RF mapper (TRL 3->4 block) ---------------------------------------------


def _norm(x: float, low: float, high: float) -> float:
    """Clamp-linear normalization to [0, 1]. Degenerate bounds -> 0.0."""
    if high <= low:
        return 0.0
    if x <= low:
        return 0.0
    if x >= high:
        return 1.0
    return (x - low) / (high - low)


def rf_to_mass(env: FeatureEnvelope, cfg: RFMapperConfig | None = None) -> Mass:
    """
    Map passive RF device-presence features to a conservative BBA.

    Emits mass over {GROUND, AMBIENT} only; the residual lands on UNKNOWN via
    normalize_bba downstream. RF NEVER emits UAV mass — this block does not
    implement drone RF control-link classification, so UAV support is structurally
    outside the codomain of this mapper (UAV is never a key written here).

    Posture (all conservative, by approved decision):
      - missing RF             -> mapper not invoked (vacuous identity in fusion)
      - sparse RF              -> explicit vacuous() ({UNKNOWN: 1.0})
      - high device density /  -> some GROUND mass (people/vehicles carry emitters)
        churn
      - low, steady presence   -> some AMBIENT mass
      - low feature agreement  -> discriminating mass collapses toward UNKNOWN
      - high unknown_emitter_  -> raises ignorance; never adds discriminating mass
        rate
      - total GROUND+AMBIENT   -> <= W_RF (RF cannot dominate optical/acoustic)
    """
    cfg = cfg or DEFAULT_RF_CONFIG
    feats = env.features
    if not isinstance(feats, RFFeatures):
        return vacuous()

    # Sparse guard: essentially no devices observed -> ignorance, not AMBIENT.
    if feats.emitter_count < cfg.emitter_count_sparse_floor:
        return vacuous()

    # Activity drivers (device density / churn) -> GROUND lean.
    a_count = _norm(feats.emitter_count, cfg.emitter_count_low, cfg.emitter_count_high)
    a_new = _norm(feats.new_emitter_rate, cfg.new_emitter_rate_low, cfg.new_emitter_rate_high)
    a_probe = _norm(feats.probe_density, cfg.probe_density_low, cfg.probe_density_high)
    a_burst = _norm(feats.burst_rate, cfg.burst_rate_low, cfg.burst_rate_high)
    activity_drivers = (a_count, a_new, a_probe, a_burst)
    activity = sum(activity_drivers) / len(activity_drivers)

    # Agreement = fraction of activity drivers above their low bound. A single
    # spiking feature yields low agreement -> GROUND mass suppressed toward the
    # single-feature cap. Requires multi-feature corroboration to assert GROUND.
    n_elevated = sum(1 for d in activity_drivers if d > 0.0)
    agreement = n_elevated / len(activity_drivers)

    # Stability drivers (low, steady presence) -> AMBIENT lean. Built from the
    # inverse of variance/occupancy and from low churn.
    s_var = 1.0 - _norm(
        feats.rssi_variance, cfg.rssi_variance_low, cfg.rssi_variance_high
    )
    s_occ = 1.0 - _norm(
        feats.channel_occupancy,
        cfg.channel_occupancy_low,
        cfg.channel_occupancy_high,
    )
    s_newlow = 1.0 - a_new
    stability = (s_var + s_occ + s_newlow) / 3.0

    # Uncertainty amplifier: high unknown_emitter_rate suppresses discriminating
    # mass (raises ignorance). Never adds mass.
    unknown_frac = _norm(
        feats.unknown_emitter_rate, 0.0, cfg.unknown_emitter_rate_high
    )
    certainty = 1.0 - unknown_frac

    # GROUND: needs activity AND agreement AND certainty.
    m_ground = cfg.w_rf * activity * agreement * certainty
    # Single-feature guardrail: if only one driver is elevated, cap GROUND hard.
    if n_elevated <= 1:
        m_ground = min(m_ground, cfg.single_feature_ground_cap)

    # AMBIENT: steady, low-churn presence. Damp by certainty too, and by the
    # complement of activity so a busy window does not also read as "ambient".
    m_ambient = cfg.w_rf * stability * (1.0 - activity) * certainty

    out: Mass = {}
    if m_ground > 0.0:
        out["GROUND"] = m_ground
    if m_ambient > 0.0:
        out["AMBIENT"] = m_ambient
    if not out:
        return vacuous()
    # Residual -> UNKNOWN handled by normalize_bba in the fusion path.
    return out


MAPPERS: dict[Modality, Callable[[FeatureEnvelope], Mass]] = {
    Modality.VIDEO: video_to_mass,
    Modality.ACOUSTIC: acoustic_to_mass,
    Modality.RF: rf_to_mass,
}


def envelope_to_mass(env: FeatureEnvelope) -> Mass:
    """Dispatch to the modality mapper. Unknown modality -> vacuous (ignorance)."""
    mapper = MAPPERS.get(env.modality)
    if mapper is None:
        return vacuous()
    return mapper(env)
