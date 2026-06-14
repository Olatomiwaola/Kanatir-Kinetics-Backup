"""Data models for sensor packets flowing into the fusion pipeline."""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
import uuid

class SensorPacket(BaseModel):
    packet_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sensor_id: str
    modality: str          # EO_IR | SIGINT | UAS | RADAR | ACOUSTIC
    classification: str    # UNCLASSIFIED | PROTECTED_B | PROTECTED_C | SECRET
    caveats: list = []
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    domain: str = "NETWORK"
    pipeline_level: str = "PROTECTED_B"
    payload: dict = {}
    source_location: Optional[str] = None
