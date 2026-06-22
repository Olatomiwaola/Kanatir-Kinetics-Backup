"""
ADE live consumer — `fused.objects` -> ensemble -> `anomalies.raw`.

Mirrors the MSFE __main__ choreography:
  - Subscribes to `fused.objects`, validates fused_schema_version, skips +
    logs mismatches (never crashes on an unknown version).
  - auto.offset.reset=latest, so START ADE BEFORE the upstream producer/MSFE
    every run or events are missed (same gotcha as MSFE's consumer).
  - Publishes versioned AnomalyRecord to `anomalies.raw`.

Run (host-native arm64, [ade] extra installed):
    python3 -m kanatir.core.ade

Env:
    ADE_BOOTSTRAP            Kafka bootstrap (default localhost:9092)
    ADE_IN_TOPIC             input topic  (default fused.objects)
    ADE_OUT_TOPIC            output topic (default anomalies.raw)
    ADE_GROUP                consumer group (default ade)
    ADE_Z_THRESHOLD          baseline z cut (default 3.0; calibration-pending)
    ADE_BASELINE_WINDOW      rolling window (default 200)
    ADE_BASELINE_WARMUP      warmup samples (default 30)

confluent-kafka is imported lazily here so the module is import-safe without a
broker for testing; the ensemble/contract are fully unit-tested without infra.
"""

from __future__ import annotations

import os
import signal
import sys

import structlog

from kanatir.core.ade.baseline import AdaptiveBaseline
from kanatir.core.ade.detectors.isolation_forest import IsolationForestDetector
from kanatir.core.ade.detectors.scaffolded import GNNDetector, LSTMAutoencoderDetector
from kanatir.core.ade.ensemble import AnomalyEnsemble
from kanatir.core.msfe.fused import FUSED_SCHEMA_VERSION, FusedObject

log = structlog.get_logger("ade")


def _build_ensemble() -> AnomalyEnsemble:
    baseline = AdaptiveBaseline(
        window=int(os.getenv("ADE_BASELINE_WINDOW", "200")),
        warmup=int(os.getenv("ADE_BASELINE_WARMUP", "30")),
        z_threshold=float(os.getenv("ADE_Z_THRESHOLD", "3.0")),
    )
    detectors = [
        IsolationForestDetector(),
        LSTMAutoencoderDetector(),  # scaffold; is_ready False, skipped by ensemble
        GNNDetector(),              # scaffold; is_ready False, skipped by ensemble
    ]
    return AnomalyEnsemble(detectors=detectors, baseline=baseline)


def main() -> int:
    from confluent_kafka import Consumer, Producer

    bootstrap = os.getenv("ADE_BOOTSTRAP", "localhost:9092")
    in_topic = os.getenv("ADE_IN_TOPIC", "fused.objects")
    out_topic = os.getenv("ADE_OUT_TOPIC", "anomalies.raw")
    group = os.getenv("ADE_GROUP", "ade")

    ensemble = _build_ensemble()

    # M4 GATE — cold-start policy (deliberate, not a gap):
    # IsolationForest is present and protocol-conformant but NOT fitted at this
    # gate. Fitting it on synthetic media (ignorance-collapsed FusedObjects with
    # conflict_k=0.0) would produce a number we can't honestly defend. The
    # ensemble's ready_detectors check means it is SKIPPED automatically — the
    # gate runs on the adaptive baseline + conflict_k as the tracked scalar
    # input, which IS defensible at TRL 3 without a representative corpus.
    # IsoForest goes live the moment real-media clips produce real feature
    # distributions — that's the carried-forward real-media demo-capture item.
    # This is logged at startup so the gate evidence is explicit about it.
    ready = [d.name for d in ensemble.ready_detectors]
    log.info(
        "ade.detector_state",
        ready=ready if ready else "none - baseline+conflict path only",
        scaffolded=["lstm_autoencoder", "gnn"],
        note="IsolationForest unfitted at M4 gate; fits on real-media corpus",
    )

    consumer = Consumer({
        "bootstrap.servers": bootstrap,
        "group.id": group,
        "auto.offset.reset": "latest",
        "enable.auto.commit": True,
    })
    producer = Producer({"bootstrap.servers": bootstrap})
    consumer.subscribe([in_topic])

    running = True

    def _stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    log.info("ade.start", bootstrap=bootstrap, in_topic=in_topic, out_topic=out_topic,
             expects_fused_schema=FUSED_SCHEMA_VERSION)

    try:
        while running:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                log.warning("ade.consume_error", error=str(msg.error()))
                continue

            raw = msg.value()
            try:
                obj = FusedObject.from_json(raw)
            except Exception as e:  # noqa: BLE001 - log + skip, never crash the loop
                log.warning("ade.decode_skip", error=str(e))
                continue

            if obj.fused_schema_version != FUSED_SCHEMA_VERSION:
                log.warning("ade.schema_skip", got=obj.fused_schema_version,
                            expected=FUSED_SCHEMA_VERSION, fused_id=obj.fused_id)
                continue

            record = ensemble.process(obj)
            producer.produce(out_topic, value=record.to_json().encode("utf-8"))
            producer.poll(0)
            log.info(
                "ade.scored",
                anomaly_id=record.anomaly_id,
                fused_id=record.fused_id,
                is_anomaly=record.is_anomaly,
                anomaly_score=round(record.anomaly_score, 4),
                baseline_state=record.baseline_state.value,
                conflict_k=round(record.conflict_k, 4),
                audit_event_ids=record.audit_event_ids,
            )
    finally:
        producer.flush(5)
        consumer.close()
        log.info("ade.stop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
