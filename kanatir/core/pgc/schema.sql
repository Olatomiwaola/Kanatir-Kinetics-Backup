-- PGC — Privacy & Governance Controller
-- Append-only audit log. Per Blueprint Section 3.2 (audit.events, 90-day retention).
-- Design principle: INSERT and SELECT only. No UPDATE, no DELETE.

CREATE TABLE IF NOT EXISTS audit_events (
    event_id        BIGSERIAL PRIMARY KEY,
    event_uuid      UUID NOT NULL DEFAULT gen_random_uuid(),
    event_time      TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type      TEXT NOT NULL,          -- e.g. INGEST, PII_SCRUB, ACCESS, ALERT_EMIT
    actor           TEXT NOT NULL,          -- service or operator that triggered the event
    sensor_id       TEXT,                   -- nullable: not all events tie to a sensor
    data_modality   TEXT,                   -- video | acoustic | rf | environmental | mobility
    action          TEXT NOT NULL,          -- human-readable action description
    pii_present     BOOLEAN NOT NULL DEFAULT FALSE,
    pii_scrubbed    BOOLEAN NOT NULL DEFAULT FALSE,
    payload_hash    TEXT,                   -- SHA-256 of the related payload (no raw PII stored)
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Index for time-range queries (audit reports, retention sweeps)
CREATE INDEX IF NOT EXISTS idx_audit_events_time ON audit_events (event_time);
CREATE INDEX IF NOT EXISTS idx_audit_events_type ON audit_events (event_type);

-- Enforce append-only at the database level.
-- Block UPDATE and DELETE via a trigger that always raises.
CREATE OR REPLACE FUNCTION prevent_audit_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS no_update_audit ON audit_events;
CREATE TRIGGER no_update_audit
    BEFORE UPDATE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();

DROP TRIGGER IF EXISTS no_delete_audit ON audit_events;
CREATE TRIGGER no_delete_audit
    BEFORE DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();