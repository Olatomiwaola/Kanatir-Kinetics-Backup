"""
UDIH — Universal Data Ingestion Hub
Kafka topic definitions, per Blueprint Section 3.2.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TopicSpec:
    name: str
    retention_ms: int
    description: str


# Retention helpers (ms)
MIN_5 = 5 * 60 * 1000
MIN_30 = 30 * 60 * 1000
MIN_60 = 60 * 60 * 1000
HOURS_24 = 24 * 60 * 60 * 1000
DAYS_7 = 7 * 24 * 60 * 60 * 1000
DAYS_90 = 90 * 24 * 60 * 60 * 1000


TOPICS: list[TopicSpec] = [
    # Raw sensor topics (UDIH → pipelines)
    TopicSpec("raw.video.frames", MIN_5, "Binary/JPEG video frames from UDIH-CVP"),
    TopicSpec("raw.acoustic.samples", MIN_5, "PCM/Float32 audio samples from UDIH-APP"),
    TopicSpec("raw.rf.iq", MIN_5, "Binary IQ samples from UDIH-RAP"),
    TopicSpec("raw.environmental", MIN_60, "JSON environmental readings from UDIH-ESP"),
    TopicSpec("raw.mobility", MIN_60, "JSON/GeoJSON mobility data from UDIH-MIP"),

    # Feature topics (pipelines → MSFE)
    TopicSpec("features.video", MIN_30, "Video feature envelopes from CVP"),
    TopicSpec("features.acoustic", MIN_30, "Acoustic feature envelopes from APP"),
    TopicSpec("features.rf", MIN_30, "RF feature envelopes from RAP"),
    TopicSpec("features.environmental", MIN_30, "Environmental feature envelopes from ESP"),
    TopicSpec("features.mobility", MIN_30, "Mobility feature envelopes from MIP"),

    # Fusion / detection / alerting pipeline
    TopicSpec("fused.objects", MIN_60, "Fused multi-sensor objects from MSFE"),
    TopicSpec("anomalies.raw", HOURS_24, "Raw anomaly detections from ADE"),
    TopicSpec("alerts.triaged", DAYS_7, "Triaged alerts from CSAT"),
    TopicSpec("alerts.explained", DAYS_7, "XAI-explained alerts from XAI"),

    # Governance
    TopicSpec("audit.events", DAYS_90, "Privacy & governance audit trail from PGC"),
]