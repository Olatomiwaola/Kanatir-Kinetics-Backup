"""Tests for fusion core."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from fusion_core.fuser import fuse

def test_empty_fuse():
    assert fuse([]) == {}

def test_classification_elevation():
    packets = [
        {"packet_id": "a", "modality": "EO_IR", "classification": "PROTECTED_B"},
        {"packet_id": "b", "modality": "SIGINT", "classification": "SECRET"},
    ]
    result = fuse(packets)
    assert result["classification"] == "SECRET"
    assert set(result["modalities"]) == {"EO_IR","SIGINT"}

def test_source_tracking():
    packets = [{"packet_id": "x", "modality": "UAS", "classification": "PROTECTED_B"}]
    result = fuse(packets)
    assert "x" in result["source_packet_ids"]
