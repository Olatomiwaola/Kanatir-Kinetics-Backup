"""Tests for audit logger."""
import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from audit_logger.logger import init_db, log_decision, export_log

def test_init_and_log():
    db = tempfile.mktemp(suffix=".db")
    init_db(db)
    packet = {"packet_id": "abc-123", "sensor_id": "EOIR-01",
              "modality": "EO_IR", "classification": "PROTECTED_B"}
    result = {"action": "PERMIT", "rule_id": "RULE-002", "reason": "Test"}
    log_decision(packet, result, db_path=db)
    rows = export_log(db)
    assert len(rows) == 1
    assert rows[0]["action"] == "PERMIT"
    assert rows[0]["packet_id"] == "abc-123"
