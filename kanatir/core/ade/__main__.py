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


def _major(version: str) -> str:
    """Major component of a semver string — the D-GATE granularity (M5.1, D2).

    ADE accepts any fused object whose MAJOR matches FUSED_SCHEMA_VERSION's,
    mirroring the CSAT and XAI hops. source_track_refs is an additive
    (semver-minor) field, so a future 1.x MSFE bump must not make ADE skip the
    object. The imported target already rose to 1.2.0 in-release, so this only
    hardens ADE against future minors — it does not change in-release behaviour.
    """
    return version.split(".", 1)[0]


def _build_unfitted_ensemble() -> AnomalyEnsemble:
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

    # ADE_MODEL_PATH — M7/TRL-3 fitted-model path.
    #   unset  -> M4-compatible: unfitted IsoForest (skipped), baseline+conflict
    #             only. Logged fitted=false.
    #   set    -> load the schema-pinned artifact (fit_ade.py output), validate
    #             feature_names/feature_dim/feature_schema_version EXACTLY, inject
    #             the fitted detector into a fresh ensemble+baseline. Hard-fail on
    #             drift (AdeModelIncompatible) — NEVER silently fall back to
    #             unfitted when a model path was given. M7 gate runs MUST set this.
    model_path = os.getenv("ADE_MODEL_PATH")
    if model_path:
        from kanatir.core.ade.model_io import load_fitted_ensemble

        loaded = load_fitted_ensemble(model_path)
        ensemble = loaded.ensemble
        ready = [d.name for d in ensemble.ready_detectors]
        log.info(
            "ade.start",
            fitted=True,
            model_path=model_path,
            feature_schema=loaded.feature_schema_version,
            n_samples=loaded.n_samples,
            corpus_id=loaded.corpus_id,
            ready=ready,
            scaffolded=["lstm_autoencoder", "gnn"],
        )
    else:
        ensemble = _build_unfitted_ensemble()
        ready = [d.name for d in ensemble.ready_detectors]
        log.info(
            "ade.start",
            fitted=False,
            ready=ready if ready else "none - baseline+conflict path only",
            scaffolded=["lstm_autoencoder", "gnn"],
            note="IsolationForest unfitted (M4 cold-start policy); set ADE_MODEL_PATH to fit-load",
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

    log.info("ade.broker", bootstrap=bootstrap, in_topic=in_topic, out_topic=out_topic,
             expects_fused_major=_major(FUSED_SCHEMA_VERSION))

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

            if _major(obj.fused_schema_version) != _major(FUSED_SCHEMA_VERSION):
                log.warning("ade.schema_skip", got=obj.fused_schema_version,
                            expected_major=_major(FUSED_SCHEMA_VERSION),
                            fused_id=obj.fused_id)
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
