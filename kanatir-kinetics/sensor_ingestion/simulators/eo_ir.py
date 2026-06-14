"""Simulated EO/IR sensor feed."""
import random
from sensor_ingestion.models import SensorPacket

def generate_packet() -> SensorPacket:
    return SensorPacket(
        sensor_id=f"EOIR-{random.randint(1,4):02d}",
        modality="EO_IR",
        classification=random.choice(["UNCLASSIFIED","PROTECTED_B","PROTECTED_C"]),
        payload={
            "image_id": f"IMG-{random.randint(10000,99999)}",
            "resolution": "1920x1080",
            "thermal": random.choice([True, False]),
            "confidence": round(random.uniform(0.7, 1.0), 2),
        },
        source_location="GRID-447821N-0752314W",
    )
