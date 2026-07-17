"""M5.1 — MSFE source-local video-track reference extraction.

Covers the MSFE-touching acceptance cases from the M5.1 decision contract §6
(cases 1-6). These exercise `_track_refs_for` and its surfacing on `FusedObject`
via `fuse_window`. The load-bearing properties under test:

  - dedup is on the (source_sensor_id, track_id) PAIR, not the bare track id, so
    the same integer id on two cameras is two distinct refs;
  - the no-information case yields None, never [] (the epistemic distinction that
    becomes CSAT's distinct_video_track_ref_count = null, never 0);
  - a multi-ref fused object carries every distinct ref without implying one
    physical object;
  - the field is optional and additive — a fused object with no video refs is
    still valid, and `_coherence` is unaffected.

Style mirrors tests/unit/test_fused_acoustic_meta.py (the M9 additive-field
precedent). No sealed test file is modified.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kanatir.core.msfe.fused import FUSED_SCHEMA_VERSION, SourceTrackRef
from kanatir.core.msfe.fusion import _track_refs_for, fuse_window
from kanatir.pipelines.common.envelope import (
    Detection,
    FeatureEnvelope,
    Modality,
    PrivacyBlock,
    VideoFeatures,
)

_T0 = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _privacy(audit_event_id: int = 1) -> PrivacyBlock:
    # Matches the sealed PrivacyBlock: gate_passed is required and its validator
    # rejects anything but True (the fail-closed privacy gate). A published
    # envelope always has gate_passed=True.
    return PrivacyBlock(gate_passed=True, pii_present=False, audit_event_id=audit_event_id)


def _video_env(source_sensor_id: str, track_ids: list[int], *, aid: int = 1) -> FeatureEnvelope:
    return FeatureEnvelope(
        modality=Modality.VIDEO,
        source_sensor_id=source_sensor_id,
        capture_ts=_T0,
        privacy=_privacy(aid),
        features=VideoFeatures(
            frame_w=1920,
            frame_h=1080,
            detections=[
                Detection(
                    track_id=t,
                    cls="uav",
                    confidence=0.9,
                    bbox_xyxy=(0.0, 0.0, 1.0, 1.0),
                )
                for t in track_ids
            ],
        ),
    )


# --- direct helper tests (case 1,3,4,5,6) ---------------------------------------


def test_case1_one_track_repeated_detections_is_one_ref() -> None:
    """One track producing repeated detections in a window -> a single ref."""
    refs = _track_refs_for([_video_env("cam-01", [5, 5, 5])])
    assert refs is not None
    assert len(refs) == 1
    assert refs[0] == SourceTrackRef(source_sensor_id="cam-01", track_id=5)


def test_case3_multiple_tracks_one_source() -> None:
    """Five distinct tracks from one camera -> five distinct refs."""
    refs = _track_refs_for([_video_env("cam-01", [1, 2, 3, 4, 5])])
    assert refs is not None
    assert len(refs) == 5
    assert {r.track_id for r in refs} == {1, 2, 3, 4, 5}
    assert {r.source_sensor_id for r in refs} == {"cam-01"}


def test_case4_same_integer_id_different_cameras_are_distinct() -> None:
    """track_id 5 on cam-01 and cam-02 are DIFFERENT refs (ids are stream-local)."""
    refs = _track_refs_for([_video_env("cam-01", [5]), _video_env("cam-02", [5], aid=2)])
    assert refs is not None
    assert len(refs) == 2
    assert set((r.source_sensor_id, r.track_id) for r in refs) == {
        ("cam-01", 5),
        ("cam-02", 5),
    }


def test_case5_multi_track_window_with_dupes_dedups_on_pair() -> None:
    """Repeated + distinct refs across two cameras -> the exact deduped set.

    Mirrors the contract §3.1 worked example:
    (cam-01,5)(cam-01,5)(cam-01,7)(cam-02,5) -> three refs.
    """
    refs = _track_refs_for(
        [_video_env("cam-01", [5, 5, 7]), _video_env("cam-02", [5], aid=2)]
    )
    assert refs is not None
    assert len(refs) == 3
    assert set((r.source_sensor_id, r.track_id) for r in refs) == {
        ("cam-01", 5),
        ("cam-01", 7),
        ("cam-02", 5),
    }


def test_case6a_no_video_returns_none_not_empty() -> None:
    """A video env with no detections exercises the 'no usable track ids' path
    -> None (not [])."""
    refs = _track_refs_for([_video_env("cam-01", [])])
    assert refs is None


def test_case6b_empty_envelope_list_returns_none() -> None:
    refs = _track_refs_for([])
    assert refs is None


# --- surfaced-on-FusedObject tests (end-to-end through fuse_window) --------------


def test_fuse_window_populates_source_track_refs() -> None:
    """A fused window with video refs surfaces them on the FusedObject, and the
    object serializes/deserializes under the 1.2.0 schema."""
    obj = fuse_window([_video_env("cam-01", [5, 7]), _video_env("cam-02", [5], aid=2)])
    assert obj is not None
    assert obj.fused_schema_version == FUSED_SCHEMA_VERSION == "1.2.0"
    assert obj.source_track_refs is not None
    assert len(obj.source_track_refs) == 3
    round_trip = type(obj).from_json(obj.to_json())
    assert round_trip.source_track_refs is not None
    assert {(r.source_sensor_id, r.track_id) for r in round_trip.source_track_refs} == {
        ("cam-01", 5),
        ("cam-01", 7),
        ("cam-02", 5),
    }


def test_fuse_window_no_video_refs_is_none_and_valid() -> None:
    """A video window with no usable track ids yields None on the field, and the
    object is still valid (additive/optional)."""
    obj = fuse_window([_video_env("cam-01", [])])
    assert obj is not None
    assert obj.source_track_refs is None
    assert type(obj).from_json(obj.to_json()).source_track_refs is None


def test_one_one_dot_x_shaped_payload_still_parses() -> None:
    """A payload without the M5.1 field (1.1.0-shaped) must still parse, with the
    field defaulting to None — the additive/back-compat guarantee."""
    obj = fuse_window([_video_env("cam-01", [1])])
    assert obj is not None
    data = obj.model_dump()
    data.pop("source_track_refs", None)
    import json

    reparsed = type(obj).from_json(json.dumps(data, default=str))
    assert reparsed.source_track_refs is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
