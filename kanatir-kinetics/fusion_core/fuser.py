"""Multi-sensor fusion module."""
from datetime import datetime
import uuid

CLASSIFICATION_ORDER = ["UNCLASSIFIED","PROTECTED_B","PROTECTED_C","SECRET","TOP_SECRET"]

def fuse(packets: list) -> dict:
    if not packets:
        return {}
    classifications = [p.get("classification","UNCLASSIFIED") for p in packets]
    fused_class = max(classifications,
                      key=lambda c: CLASSIFICATION_ORDER.index(c) if c in CLASSIFICATION_ORDER else 0)
    return {
        "fused_id": str(uuid.uuid4()),
        "source_packet_ids": [p.get("packet_id") for p in packets],
        "modalities": list({p.get("modality") for p in packets}),
        "classification": fused_class,
        "timestamp": datetime.utcnow().isoformat(),
        "product": "FUSED_TRACK",
        "confidence": 0.0,
    }
