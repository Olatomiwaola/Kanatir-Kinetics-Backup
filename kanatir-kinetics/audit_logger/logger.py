"""Core audit logging — records every compliance decision with full provenance."""
import sqlite3, os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "./data/fusionguard.db")

def init_db(db_path: str = None):
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            packet_id   TEXT NOT NULL,
            sensor_id   TEXT,
            modality    TEXT,
            classification TEXT,
            action      TEXT NOT NULL,
            rule_id     TEXT,
            reason      TEXT,
            timestamp   TEXT NOT NULL,
            operator_id TEXT,
            override    INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def log_decision(packet: dict, result: dict, operator_id: str = "SYSTEM", db_path: str = None):
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.execute("""
        INSERT INTO audit_log
        (packet_id,sensor_id,modality,classification,action,rule_id,reason,timestamp,operator_id)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        packet.get("packet_id"), packet.get("sensor_id"), packet.get("modality"),
        packet.get("classification"), result.get("action"), result.get("rule_id"),
        result.get("reason"), datetime.utcnow().isoformat(), operator_id,
    ))
    conn.commit()
    conn.close()

def export_log(db_path: str = None) -> list:
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM audit_log ORDER BY timestamp DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]
