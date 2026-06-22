"""
Sprint 9-10 / M5 unit tests — CSAT triage (anomalies.raw -> alerts.triaged).

No broker, no ML at test time. `now` is injected into the TriageBuffer so
window/age behaviour is deterministic.
"""

from __future__ import annotations

import builtins
import importlib
from datetime import UTC, datetime, timedelta

import pytest

from kanatir.core.ade.anomaly import AnomalyRecord, BaselineState
from kanatir.core.csat.alert import (
    SA_SCHEMA_VERSION,
    Severity,
    TriagedAlert,
    assign_severity,
)
from kanatir.core.csat.triage import (
    CSAT_DEDUP_WINDOW_S,
    CSAT_MAX_AGE_S,
    TriageBuffer,
    geo_group_key,
)
from kanatir.core.msfe.fused import Contributor, Modality
from kanatir.pipelines.common.envelope import GeoRef

T0 = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)


def _contrib(eid: int, env: str = "env") -> Contributor:
    return Contributor(
        envelope_id=f"{env}-{eid}",
        modality=Modality.VIDEO,
        source_sensor_id="vid-01",
        capture_ts=T0,
        audit_event_id=eid,
    )


def _anomaly(
    *,
    fused_id: str,
    audit_ids: list[int],
    is_anomaly: bool = False,
    score: float = 0.0,
    baseline: BaselineState = BaselineState.ACTIVE,
    conflict: float = 0.0,
    site_id: str | None = "zone-A",
    lat: float | None = None,
    lon: float | None = None,
    win_offset_s: int = 0,
    classification: str = "UAV",
) -> AnomalyRecord:
    start = T0 + timedelta(seconds=win_offset_s)
    return AnomalyRecord(
        fused_id=fused_id,
        window_start=start,
        window_end=start + timedelta(seconds=5),
        geo=GeoRef(site_id=site_id, lat=lat, lon=lon),
        classification=classification,
        anomaly_score=score,
        is_anomaly=is_anomaly,
        baseline_state=baseline,
        conflict_k=conflict,
        detector_scores={"baseline": score, "conflict_k": conflict},
        contributors=[_contrib(i) for i in audit_ids],
    )


# --- Contract: versioning + schema -------------------------------------------

def test_triaged_alert_versioned_from_record_one():
    a = _anomaly(fused_id="f1", audit_ids=[1])
    alert = TriagedAlert.from_anomalies([a])
    assert alert.sa_schema_version == SA_SCHEMA_VERSION == "1.0.0"


# --- Severity rule (deterministic) -------------------------------------------

def test_severity_info_when_not_anomalous():
    assert assign_severity(is_anomaly=False, anomaly_score=0.99,
                           baseline_state=BaselineState.ACTIVE) is Severity.INFO


def test_severity_warmup_caps_at_watch():
    # High score but WARMUP baseline must NOT escalate to ALERT.
    assert assign_severity(is_anomaly=True, anomaly_score=0.99,
                           baseline_state=BaselineState.WARMUP) is Severity.WATCH


def test_severity_alert_requires_active_and_high_score():
    assert assign_severity(is_anomaly=True, anomaly_score=0.8,
                           baseline_state=BaselineState.ACTIVE) is Severity.ALERT
    assert assign_severity(is_anomaly=True, anomaly_score=0.4,
                           baseline_state=BaselineState.ACTIVE) is Severity.WATCH


def test_severity_does_not_escalate_on_conflict():
    # conflict_k is not a parameter of assign_severity at all — by design.
    import inspect
    params = inspect.signature(assign_severity).parameters
    assert "conflict_k" not in params and "conflict" not in params


# --- Idempotency: redelivery dropped, never counted as suppression -----------

def test_redelivered_fused_id_is_dropped_not_suppressed():
    buf = TriageBuffer()
    a = _anomaly(fused_id="dup", audit_ids=[1])
    buf.offer(a, now=0.0)
    buf.offer(a, now=0.1)  # exact redelivery
    assert buf.dropped_duplicates == 1
    alerts = buf.drain(now=0.2)
    assert len(alerts) == 1
    # one real observation -> suppressed_count 0, NOT 1
    assert alerts[0].suppressed_count == 0
    assert len(alerts[0].anomaly_ids) == 1


# --- Grouping: same site + sliding window collapse ---------------------------

def test_same_site_within_window_collapses_to_one_alert():
    buf = TriageBuffer()
    for i in range(3):
        buf.offer(_anomaly(fused_id=f"f{i}", audit_ids=[10 + i], site_id="zone-A"),
                  now=float(i))  # all within CSAT_DEDUP_WINDOW_S of each other
    # close the window
    alerts = buf.flush_ready(now=CSAT_DEDUP_WINDOW_S + 5)
    assert len(alerts) == 1
    assert alerts[0].suppressed_count == 2
    assert len(alerts[0].anomaly_ids) == 3


def test_different_sites_do_not_collapse():
    buf = TriageBuffer()
    buf.offer(_anomaly(fused_id="f1", audit_ids=[1], site_id="zone-A"), now=0.0)
    buf.offer(_anomaly(fused_id="f2", audit_ids=[2], site_id="zone-B"), now=0.0)
    alerts = buf.flush_ready(now=CSAT_DEDUP_WINDOW_S + 5)
    assert len(alerts) == 2
    assert all(al.suppressed_count == 0 for al in alerts)


def test_same_site_after_window_closes_makes_two_alerts():
    buf = TriageBuffer()
    buf.offer(_anomaly(fused_id="f1", audit_ids=[1], site_id="zone-A"), now=0.0)
    # first group goes idle and emits
    first = buf.flush_ready(now=CSAT_DEDUP_WINDOW_S + 1)
    assert len(first) == 1
    # a later anomaly at the same site opens a fresh group
    buf.offer(_anomaly(fused_id="f2", audit_ids=[2], site_id="zone-A"),
              now=CSAT_DEDUP_WINDOW_S + 2)
    second = buf.flush_ready(now=2 * CSAT_DEDUP_WINDOW_S + 5)
    assert len(second) == 1
    assert first[0].alert_id != second[0].alert_id


# --- Max-age cap: long-running event re-emits periodically -------------------

def test_max_age_cap_forces_reemission_while_active():
    buf = TriageBuffer()
    # Feed a member just often enough that the sliding window never goes idle.
    t = 0.0
    step = CSAT_DEDUP_WINDOW_S / 2
    fed = 0
    emitted = []
    while t <= CSAT_MAX_AGE_S + step:
        buf.offer(_anomaly(fused_id=f"f{fed}", audit_ids=[fed], site_id="zone-A"), now=t)
        fed += 1
        emitted.extend(buf.flush_ready(now=t))
        t += step
    # The group never went idle, but the max-age cap must have force-emitted once.
    assert len(emitted) >= 1, "max-age cap should force at least one re-emission"


# --- Lineage: union of audit_event_ids survives the collapse -----------------

def test_lineage_union_across_merged_anomalies():
    buf = TriageBuffer()
    buf.offer(_anomaly(fused_id="f1", audit_ids=[100, 101], site_id="zone-A"), now=0.0)
    buf.offer(_anomaly(fused_id="f2", audit_ids=[101, 102], site_id="zone-A"), now=1.0)
    alerts = buf.flush_ready(now=CSAT_DEDUP_WINDOW_S + 5)
    assert len(alerts) == 1
    # union, dedup'd by audit_event_id: {100,101,102}
    assert sorted(alerts[0].audit_event_ids) == [100, 101, 102]


def test_trigger_is_highest_severity_member():
    buf = TriageBuffer()
    buf.offer(_anomaly(fused_id="f1", audit_ids=[1], is_anomaly=False, score=0.0,
                       site_id="zone-A"), now=0.0)
    buf.offer(_anomaly(fused_id="f2", audit_ids=[2], is_anomaly=True, score=0.9,
                       baseline=BaselineState.ACTIVE, conflict=0.42,
                       site_id="zone-A"), now=1.0)
    alerts = buf.flush_ready(now=CSAT_DEDUP_WINDOW_S + 5)
    assert len(alerts) == 1
    assert alerts[0].severity is Severity.ALERT
    # trigger's diagnostics carried through for XAI
    assert alerts[0].conflict_k == pytest.approx(0.42)


# --- Contract invariant: suppressed_count == len(ids) - 1 --------------------

def test_suppressed_count_invariant_enforced():
    with pytest.raises(ValueError):
        TriagedAlert(
            anomaly_ids=["x", "y", "z"],   # 3 ids
            severity=Severity.INFO,
            classification="UAV",
            window_start=T0,
            window_end=T0 + timedelta(seconds=5),
            baseline_state=BaselineState.ACTIVE,
            anomaly_score=0.0,
            conflict_k=0.0,
            suppressed_count=0,            # but says 0 suppressed -> incoherent
            contributors=[_contrib(1)],
        )


def test_window_coherence_enforced():
    with pytest.raises(ValueError):
        TriagedAlert(
            anomaly_ids=["x"],
            severity=Severity.INFO,
            classification="UAV",
            window_start=T0 + timedelta(seconds=5),
            window_end=T0,                 # end before start
            baseline_state=BaselineState.ACTIVE,
            anomaly_score=0.0,
            conflict_k=0.0,
            suppressed_count=0,
            contributors=[_contrib(1)],
        )


# --- Geo key tiering ----------------------------------------------------------

def test_geo_key_prefers_site_id():
    assert geo_group_key(GeoRef(site_id="z", lat=1.0, lon=2.0)) == ("site", "z")


def test_geo_key_falls_back_to_cell_then_ungeo():
    k = geo_group_key(GeoRef(lat=45.42, lon=-75.69))
    assert k[0] == "cell"
    assert geo_group_key(GeoRef()) == ("ungeo",)


# --- ML-free import invariant -------------------------------------------------

def test_csat_modules_import_without_ml():
    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name.split(".")[0] in {"sklearn", "torch", "numpy"}:
            raise ImportError(f"blocked: {name}")
        return real_import(name, *a, **k)

    builtins.__import__ = blocked
    try:
        import kanatir.core.csat.alert as m_alert
        import kanatir.core.csat.triage as m_triage
        importlib.reload(m_alert)
        importlib.reload(m_triage)
    finally:
        builtins.__import__ = real_import
