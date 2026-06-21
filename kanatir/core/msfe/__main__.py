"""
MSFE — Multi-Sensor Fusion Engine (live consumer).

Subscribes to features.video + features.acoustic, validates envelopes against
SCHEMA_VERSION, parks them in the Redis sliding window, and on each poll tick
harvests matured windows, fuses co-windowed evidence via Dempster-Shafer, and
publishes versioned FusedObjects to fused.objects.

Execution model: host-native arm64 (parity with CVP/APP). Zero ML deps — runs
on the core install, no [pipelines] extra needed.

Run (host, with the docker stack up):
    KAFKA_BOOTSTRAP=localhost:9092 REDIS_URL=redis://localhost:6379/0 \
        python3 -m kanatir.core.msfe

Version handling: envelopes whose schema_version != SCHEMA_VERSION are skipped
and logged (not crashed on) — this is the forward-compat behavior the versioned
envelope was designed for.
"""

from __future__ import annotations

import os
import signal
import sys
from datetime import timedelta

import redis
import structlog
from confluent_kafka import Consumer, KafkaError

from kanatir.core.msfe.buffer import WindowBuffer
from kanatir.core.msfe.fusion import DEFAULT_WINDOW
from kanatir.pipelines.common.envelope import SCHEMA_VERSION, FeatureEnvelope
from kanatir.pipelines.common.producer import EnvelopeProducer, default_bootstrap

log = structlog.get_logger("core.msfe")

FEATURE_TOPICS = ["features.video", "features.acoustic"]
FUSED_TOPIC = "fused.objects"
GROUP_ID = "msfe-fusion"


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


class FusedPublisher:
    """Reuses the confluent-kafka producer wiring, keyed by fused_id."""

    def __init__(self, bootstrap: str | None = None) -> None:
        # Reuse EnvelopeProducer's underlying Producer config via a private path;
        # FusedObject is not a FeatureEnvelope, so we publish its JSON directly.
        self._ep = EnvelopeProducer(bootstrap)

    def publish(self, fused) -> None:  # fused: FusedObject
        self._ep._producer.produce(
            FUSED_TOPIC,
            key=fused.fused_id.encode("utf-8"),
            value=fused.to_json().encode("utf-8"),
            on_delivery=self._ep._delivery,
        )
        self._ep._producer.poll(0)

    def flush(self, timeout: float = 5.0) -> int:
        return self._ep.flush(timeout)


def build_consumer(bootstrap: str) -> Consumer:
    c = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": GROUP_ID,
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
        }
    )
    c.subscribe(FEATURE_TOPICS)
    return c


def run() -> int:
    bootstrap = default_bootstrap()
    window = timedelta(
        seconds=float(os.environ.get("MSFE_WINDOW_S", DEFAULT_WINDOW.total_seconds()))
    )
    consumer = build_consumer(bootstrap)
    r = redis.Redis.from_url(_redis_url())
    buf = WindowBuffer(r, window)
    publisher = FusedPublisher(bootstrap)

    running = True

    def _stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    log.info("msfe.start", topics=FEATURE_TOPICS, window_s=window.total_seconds())
    consumed = 0
    published = 0
    try:
        while running:
            msg = consumer.poll(0.5)
            if msg is not None and not msg.error():
                try:
                    env = FeatureEnvelope.from_json(msg.value())
                except Exception as exc:  # malformed -> skip, never crash the loop
                    log.warning("msfe.bad_envelope", error=str(exc))
                else:
                    if env.schema_version != SCHEMA_VERSION:
                        log.warning(
                            "msfe.version_skip",
                            got=env.schema_version,
                            want=SCHEMA_VERSION,
                        )
                    else:
                        buf.add(env)
                        consumed += 1
            elif msg is not None and msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    log.error("msfe.consume_error", error=str(msg.error()))

            for fused in buf.harvest():
                publisher.publish(fused)
                published += 1
                log.info(
                    "msfe.fused",
                    fused_id=fused.fused_id,
                    classification=fused.classification,
                    confidence=round(fused.confidence, 3),
                    conflict_k=round(fused.belief.conflict_k, 3),
                    n_modalities=fused.n_modalities,
                    multimodal=fused.is_multimodal,
                    contributors=[c.source_sensor_id for c in fused.contributors],
                )
    finally:
        publisher.flush()
        consumer.close()
        log.info("msfe.stop", consumed=consumed, published=published)
    return 0


if __name__ == "__main__":
    sys.exit(run())
