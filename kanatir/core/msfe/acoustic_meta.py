"""
acoustic_meta.py — M9 / TRL 3->4: derive AcousticMeta from a YAMNet top-class
list, routing acoustic distinctiveness PAST the Dempster-Shafer mass collapse.

This is the only NEW modality-aware acoustic mapping in this block. It does NOT
touch the sealed evidence.acoustic_to_mass mapper or its _ACOUSTIC_LABEL_MAP:
the fix is additive (carry richer features onto the fused object), never a
re-edit of the sealed mass projection.

Aggregation policy (decision: MAX, not sum): each group score is the MAXIMUM
yamnet_top score among labels matching that group's fragments. Max is more
defensible than sum because YAMNet emits multiple near-synonyms for one sound;
summing would reward vocabulary duplication rather than acoustic evidence
strength.

Group fragments are matched case-insensitively as substrings, mirroring the
sealed mapper's matching style (YAMNet labels are descriptive phrases). The
groups and their fragments were FROZEN BEFORE evaluation; see
ACOUSTIC_GROUP_NAMES in fused.py for the ontology rationale and the recorded
decision that `chainsaw` is intentionally not group-mapped in this block.
"""

from __future__ import annotations

import math

from kanatir.core.msfe.fused import ACOUSTIC_GROUP_NAMES, AcousticMeta

# Frozen group -> matching label fragments (AudioSet-lineage parents). Keys are
# exactly ACOUSTIC_GROUP_NAMES; a module-load assert below enforces that, so the
# fragment map and the featurizer's index binding can never silently diverge.
_ACOUSTIC_GROUP_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "siren_alarm": (
        "siren", "alarm", "emergency vehicle", "police car", "ambulance",
        "fire engine", "civil defense siren", "buzzer", "smoke detector",
    ),
    "engine_vehicle": (
        "engine", "vehicle", "car", "truck", "motorcycle", "bus", "idling",
        "accelerating", "motor",
    ),
    "impact_transient": (
        "explosion", "gunshot", "glass", "crash", "bang", "boom", "breaking",
        "shatter", "thump",
    ),
    "aircraft_uav": (
        "aircraft", "helicopter", "propeller", "drone", "fixed-wing",
        "aircraft engine",
    ),
    "voice": (
        "speech", "shout", "scream", "yell", "conversation", "crowd",
        "children shouting",
    ),
    "nature_ambient": (
        "wind", "rain", "bird", "insect", "silence", "stream", "thunderstorm",
        "rustling",
    ),
}

# Fail at import if the fragment map and the contractual group names drift.
assert tuple(_ACOUSTIC_GROUP_FRAGMENTS.keys()) == ACOUSTIC_GROUP_NAMES, (
    "acoustic group fragment map keys must equal ACOUSTIC_GROUP_NAMES in fused.py, "
    f"got {tuple(_ACOUSTIC_GROUP_FRAGMENTS.keys())} vs {ACOUSTIC_GROUP_NAMES}"
)


def _yamnet_entropy(scores: list[float]) -> float:
    """Shannon entropy (nats) over the yamnet_top score distribution. Scores are
    sum-normalized into a proper distribution first; if they don't sum > 0,
    entropy is 0.0. Low entropy = belief concentrated on one class (a distinct
    event); high entropy = diffuse (ambient-like)."""
    total = sum(s for s in scores if s > 0.0)
    if total <= 0.0:
        return 0.0
    h = 0.0
    for s in scores:
        if s > 0.0:
            p = s / total
            h -= p * math.log(p)
    return h


def _group_scores(yamnet_top: list[tuple[str, float]]) -> dict[str, float]:
    """For each frozen group, the MAX yamnet_top score among labels matching any
    of that group's fragments. Every group is present (0.0 if no match), so the
    dict shape is stable for the positionally-stable featurizer downstream."""
    out: dict[str, float] = {g: 0.0 for g in ACOUSTIC_GROUP_NAMES}
    for label, score in yamnet_top:
        low = label.lower()
        s = max(0.0, min(1.0, float(score)))
        for group, fragments in _ACOUSTIC_GROUP_FRAGMENTS.items():
            if any(frag in low for frag in fragments):
                if s > out[group]:
                    out[group] = s
    return out


def acoustic_meta_from_yamnet(
    yamnet_top: list[tuple[str, float]] | None,
) -> AcousticMeta | None:
    """Build AcousticMeta from a YAMNet top-class list. Empty/None -> None (the
    caller leaves FusedObject.acoustic_meta as None, i.e. no acoustic evidence —
    honest absence, never fabricated signal)."""
    if not yamnet_top:
        return None

    top_label, top_raw = max(yamnet_top, key=lambda ls: ls[1])
    top_score = max(0.0, min(1.0, float(top_raw)))
    entropy = _yamnet_entropy([float(s) for _, s in yamnet_top])
    groups = _group_scores(yamnet_top)

    return AcousticMeta(
        top_label=top_label,
        top_score=top_score,
        yamnet_entropy=entropy,
        group_scores=groups,
    )
