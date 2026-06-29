"""Unit tests — M9 featurizer (8->16, append-only, positional stability)."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np

from kanatir.core.ade.features import (
    FEATURE_DIM,
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    extract_features,
)
from kanatir.core.msfe.fused import (
    ACOUSTIC_GROUP_NAMES,
    AcousticMeta,
    BeliefMass,
    Contributor,
    FusedObject,
)
from kanatir.pipelines.common.envelope import Modality


def _now():
    return datetime.now(UTC)


def _obj(acoustic_meta=None):
    bm = BeliefMass(
        masses={"UAV": 0.0, "GROUND": 0.6, "AMBIENT": 0.0, "UNKNOWN": 0.4},
        conflict_k=0.05,
    )
    c = Contributor(
        envelope_id="e1",
        modality=Modality.ACOUSTIC,
        source_sensor_id="s1",
        capture_ts=_now(),
        audit_event_id=1,
    )
    return FusedObject(
        window_start=_now(),
        window_end=_now(),
        belief=bm,
        classification="GROUND",
        confidence=0.6,
        contributors=[c],
        n_modalities=1,
        is_multimodal=False,
        acoustic_meta=acoustic_meta,
    )


def test_version_and_dim():
    assert FEATURE_SCHEMA_VERSION == "1.1.0"
    assert FEATURE_DIM == 16


def test_first_eight_names_unchanged():
    assert FEATURE_NAMES[:8] == (
        "mass_UAV",
        "mass_GROUND",
        "mass_AMBIENT",
        "mass_UNKNOWN",
        "conflict_k",
        "confidence",
        "n_modalities",
        "belief_entropy",
    )


def test_appended_names_in_group_order():
    assert FEATURE_NAMES[8] == "ac_top_score"
    assert FEATURE_NAMES[9] == "ac_yamnet_entropy"
    assert FEATURE_NAMES[10:] == tuple(f"ac_grp_{g}" for g in ACOUSTIC_GROUP_NAMES)


def test_missing_acoustic_zeros_tail():
    v = extract_features(_obj(None))
    assert len(v) == 16
    assert np.allclose(v[8:], 0.0)


def test_indices_0_7_byte_stable():
    v = extract_features(_obj(None))
    expected = [
        0.0,
        0.6,
        0.0,
        0.4,
        0.05,
        0.6,
        1.0,
        -(0.6 * math.log(0.6) + 0.4 * math.log(0.4)),
    ]
    assert np.allclose(v[:8], expected)


def test_with_acoustic_meta_populates_tail_in_order():
    gs = {g: 0.0 for g in ACOUSTIC_GROUP_NAMES}
    gs["siren_alarm"] = 0.8  # first group -> index 10
    gs["voice"] = 0.2
    am = AcousticMeta(
        top_label="Siren", top_score=0.8, yamnet_entropy=0.97, group_scores=gs
    )
    v = extract_features(_obj(am))
    assert v[8] == 0.8
    assert abs(v[9] - 0.97) < 1e-9
    # siren_alarm is ACOUSTIC_GROUP_NAMES[0] -> v[10]
    assert v[10] == 0.8
    # voice is ACOUSTIC_GROUP_NAMES[4] -> v[14]
    assert v[10 + ACOUSTIC_GROUP_NAMES.index("voice")] == 0.2


def test_group_read_order_independent_of_dict_insertion_order():
    # Build group_scores in a scrambled insertion order; featurizer must read by
    # the fixed ACOUSTIC_GROUP_NAMES order, not dict order.
    scrambled = {}
    for g in reversed(ACOUSTIC_GROUP_NAMES):
        scrambled[g] = 0.0
    scrambled["impact_transient"] = 0.5
    am = AcousticMeta(
        top_label="Crash", top_score=0.5, yamnet_entropy=0.1, group_scores=scrambled
    )
    v = extract_features(_obj(am))
    idx = 10 + ACOUSTIC_GROUP_NAMES.index("impact_transient")
    assert v[idx] == 0.5
