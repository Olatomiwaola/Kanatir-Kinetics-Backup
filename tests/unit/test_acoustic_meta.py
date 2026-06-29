"""Unit tests — M9 acoustic_meta helper (YAMNet -> AcousticMeta)."""

from __future__ import annotations

from kanatir.core.msfe.acoustic_meta import (
    _ACOUSTIC_GROUP_FRAGMENTS,
    acoustic_meta_from_yamnet,
)
from kanatir.core.msfe.fused import ACOUSTIC_GROUP_NAMES


def test_empty_or_none_returns_none():
    assert acoustic_meta_from_yamnet(None) is None
    assert acoustic_meta_from_yamnet([]) is None


def test_fragment_map_keys_match_group_names():
    # Enforced at import too, asserted here for visibility.
    assert tuple(_ACOUSTIC_GROUP_FRAGMENTS.keys()) == ACOUSTIC_GROUP_NAMES


def test_top_label_and_score():
    m = acoustic_meta_from_yamnet([("Siren", 0.8), ("Vehicle", 0.3)])
    assert m is not None
    assert m.top_label == "Siren"
    assert abs(m.top_score - 0.8) < 1e-9


def test_group_scores_all_keys_present():
    m = acoustic_meta_from_yamnet([("Wind", 0.5)])
    assert set(m.group_scores.keys()) == set(ACOUSTIC_GROUP_NAMES)


def test_group_aggregation_is_max_not_sum():
    # Two siren synonyms; the group score must be the MAX (0.81), not the sum.
    m = acoustic_meta_from_yamnet(
        [("Emergency vehicle (siren)", 0.81), ("Siren", 0.65)]
    )
    assert abs(m.group_scores["siren_alarm"] - 0.81) < 1e-9


def test_chainsaw_not_group_mapped_but_scalar_carries():
    # Decision: chainsaw is intentionally not group-mapped this block; the scalar
    # features preserve its distinctiveness.
    m = acoustic_meta_from_yamnet([("Chainsaw", 0.74), ("Power tool", 0.30)])
    assert all(v == 0.0 for v in m.group_scores.values())
    assert abs(m.top_score - 0.74) < 1e-9
    assert m.yamnet_entropy > 0.0


def test_entropy_lower_for_concentrated_than_diffuse():
    concentrated = acoustic_meta_from_yamnet([("Siren", 0.95), ("Vehicle", 0.05)])
    diffuse = acoustic_meta_from_yamnet(
        [("Wind", 0.3), ("Rain", 0.28), ("Silence", 0.22), ("Insect", 0.2)]
    )
    assert concentrated.yamnet_entropy < diffuse.yamnet_entropy


def test_unmatched_label_yields_zero_groups_nonzero_scalar():
    m = acoustic_meta_from_yamnet([("Mysterious blorp", 0.6)])
    assert all(v == 0.0 for v in m.group_scores.values())
    assert m.top_score == 0.6


def test_scores_clamped_to_unit_interval():
    m = acoustic_meta_from_yamnet([("Siren", 1.5)])  # out-of-range guard
    assert 0.0 <= m.top_score <= 1.0
    assert 0.0 <= m.group_scores["siren_alarm"] <= 1.0
