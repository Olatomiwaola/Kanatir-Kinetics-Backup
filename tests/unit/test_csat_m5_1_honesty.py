"""M5.1 — CSAT triage-honesty surface (contract D3-D6, commit 70a6906).

Turn 3 of the coordinated release. Exercises the real TriageBuffer / from_anomalies
paths (not hand-built alerts) so the honesty fields, incident continuity, and the
bounded seen-set are validated end to end:

  D3 — observation vs distinct-ref counts, null-never-0, mixed-class breakdown,
       trigger class distinct from composition, group_reason, frozen severity.
  D4 — TTL-bounded idempotency set: in-retention dup suppressed, post-eviction
       replay treated as a new observation, set stays bounded.
  D5 — geo-temporal incident continuity across max-age flushes; closure -> new id.
  D6 — suppressed_count == observation_count - 1 invariant, tamper-evident.

Style mirrors tests/unit/test_sprint_09_10.py (CSAT fixtures).
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from kanatir.core.ade.anomaly import AnomalyRecord, BaselineState
from kanatir.core.csat.alert import (
    SA_SCHEMA_VERSION,
    TriagedAlert,
    assign_severity,
)
from kanatir.core.csat.triage import (
    CSAT_DEDUP_WINDOW_S,
    CSAT_MAX_AGE_S,
    CSAT_SEEN_TTL_S,
    TriageBuffer,
)
from kanatir.core.msfe.fused import UNKNOWN, Contributor, Modality, SourceTrackRef
from kanatir.pipelines.common.envelope import GeoRef

T0 = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _contrib(eid: int) -> Contributor:
    return Contributor(
        envelope_id=f"env-{eid}",
        modality=Modality.VIDEO,
        source_sensor_id="vid-01",
        capture_ts=T0,
        audit_event_id=eid,
    )


def _anomaly(
    *,
    fused_id: str,
    refs: list[SourceTrackRef] | None = None,
    classification: str = "UAV",
    is_anomaly: bool = True,
    score: float = 0.8,
    baseline: BaselineState = BaselineState.ACTIVE,
    site_id: str | None = "zone-A",
    win_offset_s: int = 0,
    audit_id: int = 1,
) -> AnomalyRecord:
    start = T0 + timedelta(seconds=win_offset_s)
    return AnomalyRecord(
        fused_id=fused_id,
        window_start=start,
        window_end=start + timedelta(seconds=5),
        geo=GeoRef(site_id=site_id),
        classification=classification,
        anomaly_score=score,
        is_anomaly=is_anomaly,
        baseline_state=baseline,
        conflict_k=0.0,
        detector_scores={"baseline": score},
        contributors=[_contrib(audit_id)],
        source_track_refs=refs,
    )


def _ref(sensor: str, tid: int) -> SourceTrackRef:
    return SourceTrackRef(source_sensor_id=sensor, track_id=tid)


# --- version -------------------------------------------------------------------- #


def test_alert_schema_is_1_1_0() -> None:
    alert = TriagedAlert.from_anomalies([_anomaly(fused_id="f1")])
    assert alert.sa_schema_version == SA_SCHEMA_VERSION == "1.1.0"


# --- D3: observation vs distinct-reference counts ------------------------------- #


def test_one_track_many_observations() -> None:  # contract case 2 / Scenario A
    ref = [_ref("cam-01", 9)]
    members = [_anomaly(fused_id=f"f{i}", refs=ref, audit_id=i) for i in range(100)]
    alert = TriagedAlert.from_anomalies(members)
    assert alert.observation_count == 100
    assert alert.distinct_video_track_ref_count == 1
    assert alert.identity_reference_available is True


def test_multiple_tracks_one_source_counts_five() -> None:  # case 3
    members = [
        _anomaly(fused_id=f"f{t}", refs=[_ref("cam-01", t)], audit_id=t)
        for t in range(1, 6)
    ]
    alert = TriagedAlert.from_anomalies(members)
    assert alert.observation_count == 5
    assert alert.distinct_video_track_ref_count == 5


def test_same_int_different_cameras_are_distinct() -> None:  # case 4
    members = [
        _anomaly(fused_id="f1", refs=[_ref("cam-01", 5)], audit_id=1),
        _anomaly(fused_id="f2", refs=[_ref("cam-02", 5)], audit_id=2),
    ]
    alert = TriagedAlert.from_anomalies(members)
    assert alert.distinct_video_track_ref_count == 2


def test_csat_unions_refs_across_members() -> None:  # case 5
    members = [
        _anomaly(fused_id="f1", refs=[_ref("cam-01", 5), _ref("cam-01", 5)], audit_id=1),
        _anomaly(fused_id="f2", refs=[_ref("cam-01", 7)], audit_id=2),
        _anomaly(fused_id="f3", refs=[_ref("cam-02", 5)], audit_id=3),
    ]
    alert = TriagedAlert.from_anomalies(members)
    assert alert.distinct_video_track_ref_count == 3
    assert {(r.source_sensor_id, r.track_id) for r in alert.source_track_refs} == {
        ("cam-01", 5),
        ("cam-01", 7),
        ("cam-02", 5),
    }


def test_missing_refs_is_null_never_zero() -> None:  # case 6
    alert = TriagedAlert.from_anomalies([_anomaly(fused_id="f1", refs=None)])
    assert alert.identity_reference_available is False
    assert alert.distinct_video_track_ref_count is None  # NOT 0
    assert alert.source_track_refs is None


# --- D3: mixed-class breakdown + trigger class ---------------------------------- #


def test_mixed_class_breakdown_exposes_all_classes() -> None:  # case 7
    buf = TriageBuffer()
    # same site + window -> one group; distinct classes; the ALERT-scoring UAV
    # member is the trigger (highest severity), others WATCH.
    buf.offer(_anomaly(fused_id="f1", classification="UAV", score=0.9, audit_id=1), now=0.0)
    buf.offer(_anomaly(fused_id="f2", classification="GROUND", score=0.4, audit_id=2), now=1.0)
    buf.offer(_anomaly(fused_id="f3", classification="AMBIENT", score=0.4, audit_id=3), now=2.0)
    alert = buf.flush_ready(now=CSAT_DEDUP_WINDOW_S + 5)[0]
    assert alert.class_breakdown == {"UAV": 1, "GROUND": 1, "AMBIENT": 1, UNKNOWN: 0}
    assert sum(alert.class_breakdown.values()) == alert.observation_count == 3
    assert alert.classification == "UAV"  # trigger class, distinct from breakdown


def test_group_reason_reflects_geo_tier() -> None:
    site = TriagedAlert.from_anomalies([_anomaly(fused_id="f1", site_id="zone-A")])
    assert site.group_reason == "same_site_within_sliding_window"


# --- D3: assign_severity is FROZEN ---------------------------------------------- #


def test_assign_severity_signature_unchanged() -> None:
    params = set(inspect.signature(assign_severity).parameters)
    assert params == {"is_anomaly", "anomaly_score", "baseline_state"}


def test_refs_do_not_change_severity() -> None:
    no_ref = TriagedAlert.from_anomalies([_anomaly(fused_id="f1", refs=None)])
    with_ref = TriagedAlert.from_anomalies(
        [_anomaly(fused_id="f1", refs=[_ref("cam-01", 5)])]
    )
    assert no_ref.severity is with_ref.severity  # multiplicity/refs never escalate


# --- D6: suppressed_count == observation_count - 1 ------------------------------ #


def test_suppressed_count_equals_observation_count_minus_one() -> None:
    members = [_anomaly(fused_id=f"f{i}", audit_id=i) for i in range(4)]
    alert = TriagedAlert.from_anomalies(members)
    assert alert.observation_count == 4
    assert alert.suppressed_count == alert.observation_count - 1 == 3


def test_coherence_rejects_observation_count_mismatch() -> None:
    good = TriagedAlert.from_anomalies([_anomaly(fused_id="f1")])
    payload = good.model_dump()
    payload["observation_count"] = 99  # != len(anomaly_ids) == 1
    with pytest.raises(ValueError):
        TriagedAlert.model_validate(payload)


# --- D4: TTL-bounded idempotency set -------------------------------------------- #


def test_in_retention_duplicate_suppressed_then_post_eviction_replay_is_new() -> None:
    buf = TriageBuffer()
    a = _anomaly(fused_id="dup", audit_id=1)
    buf.offer(a, now=0.0)
    buf.offer(a, now=5.0)  # in-retention redelivery -> dropped
    assert buf.dropped_duplicates == 1
    assert "dup" in buf._seen_fused_ids

    # advance beyond TTL and offer a fresh id -> eviction runs, "dup" is bounded out
    buf.offer(_anomaly(fused_id="other", audit_id=2), now=CSAT_SEEN_TTL_S + 1)
    assert "dup" not in buf._seen_fused_ids

    # replay of "dup" AFTER eviction is a NEW observation, not a counted duplicate
    before = buf.dropped_duplicates
    buf.offer(a, now=CSAT_SEEN_TTL_S + 2)
    assert buf.dropped_duplicates == before
    assert "dup" in buf._seen_fused_ids


def test_seen_set_stays_bounded_under_spaced_load() -> None:
    buf = TriageBuffer()
    for i in range(100):
        buf.offer(_anomaly(fused_id=f"x{i}", audit_id=i), now=float(i) * CSAT_SEEN_TTL_S)
    assert len(buf._seen_fused_ids) <= 2  # each offer evicts all prior


# --- D5: incident continuity ---------------------------------------------------- #


def test_incident_id_stable_and_sequence_increments_across_max_age_flush() -> None:
    buf = TriageBuffer()
    # feed every 50s (< DEDUP_WINDOW 60) so the group never goes idle, until aged.
    t = 0.0
    i = 0
    while t <= CSAT_MAX_AGE_S:
        buf.offer(_anomaly(fused_id=f"a{i}", audit_id=i), now=t)
        t += 50.0
        i += 1
    first = buf.flush_ready(now=CSAT_MAX_AGE_S)[0]  # aged, not idle -> emit seq0, reset
    assert first.incident_sequence == 0
    incident = first.incident_id

    # keep the SAME group alive for another max-age span
    t = CSAT_MAX_AGE_S + 50.0
    while t <= 2 * CSAT_MAX_AGE_S:
        buf.offer(_anomaly(fused_id=f"b{i}", audit_id=i), now=t)
        t += 50.0
        i += 1
    second = buf.flush_ready(now=2 * CSAT_MAX_AGE_S)[0]
    assert second.incident_id == incident       # SAME incident across the flush
    assert second.incident_sequence == 1        # incremented, deterministic


def test_incident_closes_then_new_incident_gets_new_id() -> None:
    buf = TriageBuffer()
    buf.offer(_anomaly(fused_id="a", site_id="zone-A", audit_id=1), now=0.0)
    closed = buf.flush_ready(now=CSAT_DEDUP_WINDOW_S + 1)[0]  # idle -> close
    first_id = closed.incident_id

    buf.offer(_anomaly(fused_id="b", site_id="zone-A", audit_id=2), now=200.0)
    reopened = buf.flush_ready(now=200.0 + CSAT_DEDUP_WINDOW_S + 1)[0]
    assert reopened.incident_id != first_id     # closed incident -> new id
    assert reopened.incident_sequence == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
