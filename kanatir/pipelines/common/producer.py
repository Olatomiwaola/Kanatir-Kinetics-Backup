"""
Envelope producer — thin wrapper over confluent_kafka.Producer that publishes
validated FeatureEnvelopes to their feature topic. Keyed by source_sensor_id so
a given sensor's stream stays ordered within a partition.
"""

from __future__ import annotations

import os

import structlog
from confluent_kafka import Producer

from kanatir.pipelines.common.envelope import FeatureEnvelope

log = structlog.get_logger("pipelines.producer")


def default_bootstrap() -> str:
    # Host-native pipelines reach the dockerized broker on localhost.
    return os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")


class EnvelopeProducer:
    def __init__(self, bootstrap: str | None = None) -> None:
        self._producer = Producer(
            {
                "bootstrap.servers": bootstrap or default_bootstrap(),
                "enable.idempotence": True,
                "acks": "all",
            }
        )

    def _delivery(self, err, msg) -> None:
        if err is not None:
            log.error("producer.delivery_failed", topic=msg.topic(), error=str(err))

    def publish(self, topic: str, envelope: FeatureEnvelope) -> None:
        """Serialize and publish. Validates by going through model_dump_json."""
        self._producer.produce(
            topic,
            key=envelope.source_sensor_id.encode("utf-8"),
            value=envelope.to_json().encode("utf-8"),
            on_delivery=self._delivery,
        )
        self._producer.poll(0)

    def flush(self, timeout: float = 5.0) -> int:
        return self._producer.flush(timeout)
