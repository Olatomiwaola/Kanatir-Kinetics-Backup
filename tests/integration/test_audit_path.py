"""
Integration test: the privacy gate against a REAL Postgres audit log.

Addresses the Sprint 1-2 carry-forward gap ("no automated test yet covers the
live Postgres audit path"). Skips unless PGC_DSN points at a reachable DB, so it
stays out of the lightweight CI lint/unit job but can run in a DB-backed job or
locally against the docker-compose Postgres.

Run locally:
    PGC_DSN=postgresql://kanatir:kanatir_dev@localhost:5432/kanatir \
        python3 -m pytest tests/integration -m integration -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

_DSN = os.environ.get("PGC_DSN")


def _db_reachable() -> bool:
    if not _DSN:
        return False
    try:
        import psycopg

        with psycopg.connect(_DSN, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_reachable(), reason="no reachable PGC_DSN database"),
]


def test_gate_writes_real_audit_event_and_blocks_mutation():
    import psycopg

    from kanatir.core.pgc.audit import init_schema
    from kanatir.pipelines.common.privacy_gate import ScrubResult, run_privacy_gate

    init_schema()

    def scrub() -> ScrubResult:
        return ScrubResult(
            pii_present=True, pii_scrubbed=True,
            actions=["face_blur:1"], payload_to_hash=b"scrubbed-frame",
        )

    block = run_privacy_gate(
        actor="cvp", sensor_id="itest-cam", data_modality="video", scrub=scrub,
    )
    assert block.gate_passed is True
    assert block.audit_event_id is not None

    # The row exists and carries the hash, not raw PII.
    with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pii_scrubbed, payload_hash FROM audit_events WHERE event_id=%s",
            (block.audit_event_id,),
        )
        scrubbed, payload_hash = cur.fetchone()
        assert scrubbed is True
        assert payload_hash and len(payload_hash) == 64  # SHA-256 hex

        # Append-only: UPDATE must be rejected by the trigger.
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(
                "UPDATE audit_events SET actor='tamper' WHERE event_id=%s",
                (block.audit_event_id,),
            )
