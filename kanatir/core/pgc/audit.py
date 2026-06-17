"""
PGC — Privacy & Governance Controller
Append-only audit log writer. Records every privacy-relevant event.
"""

import hashlib
import logging
import os
from pathlib import Path

import psycopg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pgc.audit")

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_dsn() -> str:
    return os.environ.get(
        "PGC_DSN",
        "postgresql://kanatir:kanatir_dev@localhost:5432/kanatir",
    )


def init_schema() -> None:
    """Apply the append-only audit schema. Idempotent."""
    sql = SCHEMA_PATH.read_text()
    with psycopg.connect(get_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    logger.info("✅ Audit schema applied.")


def hash_payload(payload: bytes) -> str:
    """SHA-256 of a payload. We store the hash, never the raw PII."""
    return hashlib.sha256(payload).hexdigest()


def record_event(
    event_type: str,
    actor: str,
    action: str,
    *,
    sensor_id: str | None = None,
    data_modality: str | None = None,
    pii_present: bool = False,
    pii_scrubbed: bool = False,
    payload_hash: str | None = None,
    metadata: dict | None = None,
) -> int:
    """Insert a single audit event. Returns the new event_id."""
    import json

    with psycopg.connect(get_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_events
                    (event_type, actor, sensor_id, data_modality, action,
                     pii_present, pii_scrubbed, payload_hash, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING event_id
                """,
                (
                    event_type,
                    actor,
                    sensor_id,
                    data_modality,
                    action,
                    pii_present,
                    pii_scrubbed,
                    payload_hash,
                    json.dumps(metadata or {}),
                ),
            )
            event_id = cur.fetchone()[0]
        conn.commit()
    return event_id


if __name__ == "__main__":
    init_schema()
    eid = record_event(
        event_type="INGEST",
        actor="udih",
        action="Sprint 1 smoke test — first audit event",
        data_modality="video",
        pii_present=False,
        pii_scrubbed=False,
    )
    logger.info("✅ Wrote audit event id=%s", eid)