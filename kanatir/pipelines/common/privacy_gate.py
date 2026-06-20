"""
Privacy Gate — the fail-closed chokepoint every pipeline routes through before
publishing. Per the Sprint 1-2 carry-forward: record_event(...) must fire at the
privacy gate, BEFORE buffering, and the gate must fail closed.

Contract:
  1. The caller hands the gate raw, in-memory feature material plus a scrub
     callable that mutates/produces the PII-safe version.
  2. The gate runs the scrub. If it raises, NOTHING is published and the frame
     is dropped (fail-closed) — the exception propagates so the caller drops it.
  3. The gate writes ONE audit event via PGC (kanatir.core.pgc.audit.record_event)
     describing what happened. If the audit write raises, that ALSO fails closed —
     no audit record means no publish.
  4. Only after scrub + audit succeed does the gate return a PrivacyBlock the
     caller stamps into the envelope. The envelope cannot be built without it.

The gate never stores or forwards raw PII. It hashes payloads (SHA-256, via the
PGC hash_payload) and records only hashes + boolean flags + action strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from kanatir.core.pgc.audit import hash_payload, record_event
from kanatir.pipelines.common.envelope import PrivacyBlock

log = structlog.get_logger("pipelines.privacy_gate")


@dataclass
class ScrubResult:
    """What a modality's scrub step reports back to the gate."""

    pii_present: bool
    pii_scrubbed: bool
    actions: list[str] = field(default_factory=list)
    # Optional bytes to hash for the audit trail (post-scrub, never raw PII —
    # e.g. a hashed plate string, or the scrubbed frame bytes). May be None.
    payload_to_hash: bytes | None = None


class PrivacyGateError(RuntimeError):
    """Raised when the gate cannot complete. Caller MUST drop the frame."""


def run_privacy_gate(
    *,
    actor: str,
    sensor_id: str,
    data_modality: str,
    scrub: callable,
    event_type: str = "PII_SCRUB",
) -> PrivacyBlock:
    """
    Execute the privacy gate. Returns a PrivacyBlock on success.

    Fail-closed: any exception from `scrub` or the audit write is wrapped in
    PrivacyGateError and re-raised. The caller must NOT publish on error.

    `scrub` is a zero-arg callable that performs the PII removal in-place on the
    caller's buffers and returns a ScrubResult. It runs inside the gate so the
    audit event is written about the SAME operation that actually happened.
    """
    try:
        result: ScrubResult = scrub()
    except Exception as exc:  # noqa: BLE001 — fail-closed by design
        log.error(
            "privacy_gate.scrub_failed", sensor_id=sensor_id,
            modality=data_modality, error=str(exc),
        )
        raise PrivacyGateError(f"scrub failed, frame dropped: {exc}") from exc

    payload_hash = (
        hash_payload(result.payload_to_hash)
        if result.payload_to_hash is not None
        else None
    )

    try:
        audit_event_id = record_event(
            event_type=event_type,
            actor=actor,
            action="; ".join(result.actions) or "no PII actions",
            sensor_id=sensor_id,
            data_modality=data_modality,
            pii_present=result.pii_present,
            pii_scrubbed=result.pii_scrubbed,
            payload_hash=payload_hash,
        )
    except Exception as exc:  # noqa: BLE001 — fail-closed by design
        log.error(
            "privacy_gate.audit_failed", sensor_id=sensor_id,
            modality=data_modality, error=str(exc),
        )
        raise PrivacyGateError(f"audit write failed, frame dropped: {exc}") from exc

    log.info(
        "privacy_gate.passed", sensor_id=sensor_id, modality=data_modality,
        pii_present=result.pii_present, pii_scrubbed=result.pii_scrubbed,
        audit_event_id=audit_event_id,
    )

    return PrivacyBlock(
        gate_passed=True,
        pii_present=result.pii_present,
        pii_scrubbed=result.pii_scrubbed,
        actions=result.actions,
        audit_event_id=audit_event_id,
    )
