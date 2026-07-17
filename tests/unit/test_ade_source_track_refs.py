"""M5.1 — ADE propagation of source-local video-track references.

Turn 2 of the M5.1 coordinated release (decision contract D1/D2, commit
70a6906). ADE copies FusedObject.source_track_refs onto the AnomalyRecord
VERBATIM — it makes no track<->record association — and moves its forward
fused_schema_version gate from exact-match to major-match (D2, D-GATE).

These tests exercise the REAL ensemble.process() path (not a hand-built
AnomalyRecord), so a regression in the pass-through wiring is caught:

  - refs present on the fused object survive verbatim onto the record;
  - refs absent (None) stay None — never coerced to [];
  - a 1.1.0 record with refs round-trips through JSON;
  - a 1.0.0-shaped payload (no source_track_refs key) still parses, field None;
  - _major accepts any 1.x fused version and rejects a 2.x major bump (D2).

Style mirrors tests/unit/test_sprint_07_08.py (ADE ensemble fixtures) and
tests/unit/test_source_track_refs.py (the MSFE turn-1 companion).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from kanatir.core.ade.anomaly import ANOMALY_SCHEMA_VERSION, AnomalyRecord
from kanatir.core.ade.detectors.isolation_forest import IsolationForestDetector
from kanatir.core.ade.ensemble import AnomalyEnsemble
from kanatir.core.msfe.fused import (
    UNKNOWN,
    BeliefMass,
    Contributor,
    FusedObject,
    SourceTrackRef,
)
from kanatir.pipelines.common.envelope import Modality

np = pytest.importorskip("numpy")  # process() -> extract_features/baseline need numpy


def _contributor(eid: int, modality: Modality, sensor: str) -> Contributor:
    return Contributor(
        envelope_id=f"env-{eid}",
        modality=modality,
        source_sensor_id=sensor,
        capture_ts=datetime.now(UTC),
        audit_event_id=eid,
    )


def _fused(source_track_refs: list[SourceTrackRef] | None) -> FusedObject:
    ws = datetime.now(UTC)
    belief = BeliefMass(
        masses={"UAV": 0.1, "GROUND": 0.7, "AMBIENT": 0.1, UNKNOWN: 0.1},
        conflict_k=0.05,
    )
    contributors = [
        _contributor(13, Modality.VIDEO, "cam-01"),
        _contributor(4, Modality.ACOUSTIC, "file-01"),
    ]
    mods = {c.modality for c in contributors}
    return FusedObject(
        window_start=ws,
        window_end=ws + timedelta(seconds=60),
        belief=belief,
        classification=belief.top_hypothesis,
        confidence=belief.top_confidence,
        contributors=contributors,
        n_modalities=len(mods),
        is_multimodal=len(mods) >= 2,
        source_track_refs=source_track_refs,
    )


def _ensemble() -> AnomalyEnsemble:
    # Unfitted IsoForest is is_ready False -> skipped; process() still returns a
    # record via the baseline+conflict path, which is all these tests need.
    return AnomalyEnsemble(detectors=[IsolationForestDetector()])


# --- real process() pass-through ----------------------------------------------- #


def test_process_passes_source_track_refs_through_verbatim() -> None:
    refs = [
        SourceTrackRef(source_sensor_id="cam-01", track_id=5),
        SourceTrackRef(source_sensor_id="cam-02", track_id=5),  # same int, distinct
    ]
    rec = _ensemble().process(_fused(refs))
    assert rec.source_track_refs == refs  # verbatim, no merge across sources
    assert {(r.source_sensor_id, r.track_id) for r in rec.source_track_refs} == {
        ("cam-01", 5),
        ("cam-02", 5),
    }


def test_process_none_refs_stay_none() -> None:
    rec = _ensemble().process(_fused(None))
    assert rec.source_track_refs is None  # never coerced to []


# --- schema / serialization ---------------------------------------------------- #


def test_record_is_1_1_0_and_round_trips_with_refs() -> None:
    assert ANOMALY_SCHEMA_VERSION == "1.1.0"
    refs = [SourceTrackRef(source_sensor_id="cam-01", track_id=7)]
    rec = _ensemble().process(_fused(refs))
    assert rec.anomaly_schema_version == "1.1.0"
    back = AnomalyRecord.from_json(rec.to_json())
    assert back.source_track_refs == refs


def test_old_shape_payload_parses_with_none() -> None:
    """A 1.0.0-shaped record (no source_track_refs key) still parses; field None."""
    rec = _ensemble().process(_fused(None))
    data = json.loads(rec.to_json())
    data.pop("source_track_refs", None)
    reparsed = AnomalyRecord.from_json(json.dumps(data))
    assert reparsed.source_track_refs is None


# --- D2: major-match gate ------------------------------------------------------ #


def test_major_gate_accepts_1_x_rejects_2_0_0() -> None:
    from kanatir.core.ade.__main__ import _major
    from kanatir.core.msfe.fused import FUSED_SCHEMA_VERSION

    accepted = _major(FUSED_SCHEMA_VERSION)  # "1"
    assert _major("1.2.0") == accepted
    assert _major("1.5.0") == accepted
    assert _major("2.0.0") != accepted


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
