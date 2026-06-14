"""Simulated SIGINT sensor feed."""
import random
from sensor_ingestion.models import SensorPacket

def generate_packet() -> SensorPacket:
    return SensorPacket(
        sensor_id=f"SIGINT-{random.randint(1,3):02d}",
        modality="SIGINT",
        classification=random.choice(["PROTECTED_B","PROTECTED_C","SECRET"]),
        caveats=random.choice([[], ["NOFORN"], ["FVEY"]]),
        payload={
            "frequency_mhz": round(random.uniform(100, 3000), 2),
            "signal_type": random.choice(["RADAR","COMMS","DATALINK"]),
            "intercept_confidence": round(random.uniform(0.5, 1.0), 2),
        },
    )
