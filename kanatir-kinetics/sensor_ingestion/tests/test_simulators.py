"""Tests for sensor simulators."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from sensor_ingestion.simulators.eo_ir import generate_packet as gen_eoir
from sensor_ingestion.simulators.sigint import generate_packet as gen_sigint

def test_eoir_packet():
    pkt = gen_eoir()
    assert pkt.modality == "EO_IR"
    assert pkt.classification in ["UNCLASSIFIED","PROTECTED_B","PROTECTED_C"]
    assert "image_id" in pkt.payload

def test_sigint_packet():
    pkt = gen_sigint()
    assert pkt.modality == "SIGINT"
    assert pkt.classification in ["PROTECTED_B","PROTECTED_C","SECRET"]

def test_packet_has_required_fields():
    pkt = gen_eoir()
    assert pkt.packet_id
    assert pkt.sensor_id
    assert pkt.timestamp
