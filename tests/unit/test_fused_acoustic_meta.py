"""Unit tests — M9 FusedObject contract (AcousticMeta + optional field, 1.1.0)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from kanatir.core.msfe.fused import (
    ACOUSTIC_GROUP_NAMES,
    FUSED_SCHEMA_VERSION,
    AcousticMeta,
    BeliefMass,
    Contributor,
    FusedObject,
)
from kanatir.pipelines.common.envelope import Modality


def _now():
    return datetime.now(UTC)


def _belief():
    return BeliefMass(
        masses={"UAV": 0.0, "GROUND": 0.6, "AMBIENT": 0.0, "UNKNOWN": 0.4},
        conflict_k=0.0,
    )


def _contrib(mod=Modality.ACOUSTIC):
    return Contributor(
        envelope_id="e1",
        modality=mod,
        source_sensor_id="s1",
        capture_ts=_now(),
        audit_event_id=1,
    )


def _meta():
    gs = {g: 0.0 for g in ACOUSTIC_GROUP_NAMES}
    gs["siren_alarm"] = 0.8
    return AcousticMeta(
        top_label="Siren", top_score=0.8, yamnet_entropy=0.4, group_scores=gs
    )


def test_version_is_1_2_0():
    # M5.1 (TRL 3->4) bumped FUSED_SCHEMA_VERSION 1.1.0 -> 1.2.0 for the additive
    # optional source_track_refs field. See docs/validation/M5_1_decision_contract.md
    # (contract 70a6906). This pin follows the bump; the acoustic-meta behaviour
    # tested elsewhere in this file is unchanged.
    assert FUSED_SCHEMA_VERSION == "1.2.0"


def test_fused_object_without_acoustic_meta_validates():
    o = FusedObject(
        window_start=_now(),
        window_end=_now(),
        belief=_belief(),
        classification="GROUND",
        confidence=0.6,
        contributors=[_contrib()],
        n_modalities=1,
        is_multimodal=False,
    )
    assert o.acoustic_meta is None


def test_fused_object_with_acoustic_meta_validates():
    o = FusedObject(
        window_start=_now(),
        window_end=_now(),
        belief=_belief(),
        classification="GROUND",
        confidence=0.6,
        contributors=[_contrib()],
        n_modalities=1,
        is_multimodal=False,
        acoustic_meta=_meta(),
    )
    assert o.acoustic_meta.top_label == "Siren"


def test_roundtrip_preserves_acoustic_meta():
    o = FusedObject(
        window_start=_now(),
        window_end=_now(),
        belief=_belief(),
        classification="GROUND",
        confidence=0.6,
        contributors=[_contrib()],
        n_modalities=1,
        is_multimodal=False,
        acoustic_meta=_meta(),
    )
    o2 = FusedObject.from_json(o.to_json())
    assert o2.acoustic_meta is not None
    assert o2.acoustic_meta.group_scores["siren_alarm"] == 0.8


def test_legacy_shaped_payload_parses_with_none_meta():
    # A 1.0.0-shaped payload (no acoustic_meta key) still parses; the field
    # defaults to None. The live runtime gates on version separately (see
    # test_ade runtime/skip behavior), so this is NOT "accepted as current".
    o = FusedObject(
        window_start=_now(),
        window_end=_now(),
        belief=_belief(),
        classification="GROUND",
        confidence=0.6,
        contributors=[_contrib()],
        n_modalities=1,
        is_multimodal=False,
    )
    d = json.loads(o.to_json())
    d.pop("acoustic_meta", None)
    d["fused_schema_version"] = "1.0.0"
    o2 = FusedObject.from_json(json.dumps(d))
    assert o2.acoustic_meta is None
    assert o2.fused_schema_version == "1.0.0"


def test_acoustic_meta_rejects_out_of_range_group_score():
    gs = {g: 0.0 for g in ACOUSTIC_GROUP_NAMES}
    gs["siren_alarm"] = 1.5
    with pytest.raises(ValueError):
        AcousticMeta(
            top_label="x", top_score=0.5, yamnet_entropy=0.1, group_scores=gs
        )


def test_acoustic_meta_rejects_out_of_range_top_score():
    gs = {g: 0.0 for g in ACOUSTIC_GROUP_NAMES}
    with pytest.raises(ValueError):
        AcousticMeta(
            top_label="x", top_score=1.5, yamnet_entropy=0.1, group_scores=gs
        )


def test_coherence_validator_unaffected_by_meta():
    # classification mismatch still raises, with acoustic_meta present.
    with pytest.raises(ValueError):
        FusedObject(
            window_start=_now(),
            window_end=_now(),
            belief=_belief(),
            classification="UAV",  # mismatches belief.top_hypothesis GROUND
            confidence=0.6,
            contributors=[_contrib()],
            n_modalities=1,
            is_multimodal=False,
            acoustic_meta=_meta(),
        )
