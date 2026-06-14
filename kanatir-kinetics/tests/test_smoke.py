"""Smoke tests — verify core modules import correctly."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_policy_engine_imports():
    from policy_engine.engine import load_rules, evaluate
    assert callable(load_rules) and callable(evaluate)

def test_sensor_simulators():
    from sensor_ingestion.simulators.eo_ir import generate_packet
    pkt = generate_packet()
    assert pkt.modality == "EO_IR"

def test_fuser():
    from fusion_core.fuser import fuse
    assert fuse([]) == {}

def test_audit_logger():
    from audit_logger.logger import init_db
    assert callable(init_db)
