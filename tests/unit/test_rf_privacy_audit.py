"""
RF privacy + audit tests (TRL 3->4 block).

Proves:
  - raw identifiers never appear in the emitted envelope (JSON-level check)
  - HMAC hashing is salt-dependent; salt rotation changes the hash for the same
    device, and is non-reversible / differs from a plain SHA-256
  - the envelope's PrivacyBlock is gate_passed and linked to an audit event
  - an audit event is recorded for the RF window
  - fail-closed: a scrub failure raises and yields NO envelope
  - fail-closed: an audit-write failure raises and yields NO envelope
  - only derived features are retained (no hashed_ids list on the envelope)
  - no payload field exists anywhere on the RF features

These are DB-free unit tests: record_event is replaced with an in-test fake via
monkeypatch so we observe audit calls without touching Postgres. They test the
RF privacy/audit LOGIC, not the PGC storage backend (covered by the integration
audit-path test against the real append-only log).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kanatir.pipelines.common.privacy_gate import PrivacyGateError
from kanatir.pipelines.rap.features import build_rf_envelope
from kanatir.pipelines.rap.scrub import (
    RawRFObservation,
    RotatingSalt,
    hash_identifier,
)


def _obs() -> list[RawRFObservation]:
    return [
        RawRFObservation(raw_id="AA:BB:CC:DD:EE:01", rssi=-60.0, is_probe=True,
                         first_seen=True, known=False),
        RawRFObservation(raw_id="AA:BB:CC:DD:EE:02", rssi=-70.0, is_burst=True,
                         known=True),
        RawRFObservation(raw_id="AA:BB:CC:DD:EE:03", rssi=-65.0, known=True),
    ]


class _AuditSpy:
    """Captures record_event calls; returns incrementing event ids."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event_type, actor, action, **kw) -> int:
        eid = len(self.events) + 1
        self.events.append(dict(event_type=event_type, actor=actor,
                                action=action, **kw))
        return eid


@pytest.fixture
def audit_spy(monkeypatch) -> _AuditSpy:
    spy = _AuditSpy()
    # The privacy gate calls record_event imported into its own module namespace.
    monkeypatch.setattr("kanatir.pipelines.common.privacy_gate.record_event", spy)
    return spy


def test_raw_identifiers_absent_from_envelope(audit_spy):
    salt = RotatingSalt(interval_s=900)
    env = build_rf_envelope(
        observations=_obs(), sensor_id="rap-01", band="wifi_2g4",
        window_s=2.0, salt=salt,
        capture_ts=datetime(2026, 6, 24, 12, 0, tzinfo=UTC),
    )
    raw_json = env.to_json()
    for obs in _obs():
        assert obs.raw_id not in raw_json, "raw identifier leaked into envelope"
    assert "hashed_ids" not in raw_json
    assert env.features.emitter_count == 3


def test_no_payload_field_on_rf_features(audit_spy):
    env = build_rf_envelope(
        observations=_obs(), sensor_id="rap-01", band="ble", window_s=1.0,
        salt=RotatingSalt(interval_s=900),
    )
    fields = set(type(env.features).model_fields)
    assert "payload" not in fields
    assert "iq" not in fields
    assert "emission_anomaly_score" not in fields


def test_privacy_block_passed_and_linked(audit_spy):
    env = build_rf_envelope(
        observations=_obs(), sensor_id="rap-01", band="wifi_2g4",
        window_s=2.0, salt=RotatingSalt(interval_s=900),
    )
    assert env.privacy.gate_passed is True
    assert env.privacy.pii_present is True
    assert env.privacy.pii_scrubbed is True
    assert env.privacy.audit_event_id is not None
    joined = "; ".join(env.privacy.actions)
    assert "rf_id_hash:3" in joined
    assert "salt_epoch:" in joined
    assert "derived_only" in joined


def test_audit_event_recorded(audit_spy):
    build_rf_envelope(
        observations=_obs(), sensor_id="rap-01", band="wifi_2g4",
        window_s=2.0, salt=RotatingSalt(interval_s=900),
    )
    assert len(audit_spy.events) == 1
    ev = audit_spy.events[0]
    assert ev["data_modality"] == "rf"
    assert ev["pii_scrubbed"] is True
    assert ev["payload_hash"] is not None  # integrity hash over post-scrub set


def test_hmac_is_salt_dependent_and_not_plain_sha256():
    import hashlib
    salt_a = b"\x01" * 32
    salt_b = b"\x02" * 32
    rid = "AA:BB:CC:DD:EE:01"
    ha = hash_identifier(rid, salt_a)
    hb = hash_identifier(rid, salt_b)
    assert ha != hb, "hash must depend on salt"
    plain = hashlib.sha256(rid.encode()).hexdigest()
    assert ha != plain, "HMAC must differ from unkeyed SHA-256"


def test_salt_rotation_changes_hash_for_same_device():
    t = {"now": 0.0}
    salt = RotatingSalt(interval_s=900, clock=lambda: t["now"])
    rid = "AA:BB:CC:DD:EE:01"
    e0 = salt.current()
    h0 = hash_identifier(rid, e0.salt)
    t["now"] = 901.0
    e1 = salt.current()
    h1 = hash_identifier(rid, e1.salt)
    assert e1.epoch != e0.epoch
    assert h0 != h1, "rotation must break cross-epoch linkability"


def test_default_salt_interval_is_15_min(monkeypatch):
    monkeypatch.delenv("KAN_RF_SALT_ROTATE_S", raising=False)
    salt = RotatingSalt()
    assert salt.interval_s == 15 * 60


def test_salt_interval_env_overridable(monkeypatch):
    monkeypatch.setenv("KAN_RF_SALT_ROTATE_S", "300")
    salt = RotatingSalt()
    assert salt.interval_s == 300


def test_fail_closed_on_scrub_failure(monkeypatch, audit_spy):
    def exploding_scrub(*a, **k):
        raise RuntimeError("scrub exploded")

    monkeypatch.setattr("kanatir.pipelines.rap.features.scrub_rf_window",
                        exploding_scrub)
    with pytest.raises(PrivacyGateError):
        build_rf_envelope(
            observations=_obs(), sensor_id="rap-01", band="wifi_2g4",
            window_s=2.0, salt=RotatingSalt(interval_s=900),
        )
    # No audit event should have been written for a failed scrub.
    assert audit_spy.events == []


def test_fail_closed_on_audit_failure(monkeypatch):
    def exploding_record(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("kanatir.pipelines.common.privacy_gate.record_event",
                        exploding_record)
    with pytest.raises(PrivacyGateError):
        build_rf_envelope(
            observations=_obs(), sensor_id="rap-01", band="wifi_2g4",
            window_s=2.0, salt=RotatingSalt(interval_s=900),
        )
