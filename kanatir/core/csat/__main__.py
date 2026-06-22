"""
CSAT live consumer — `anomalies.raw` -> triage -> `alerts.triaged`.

Mirrors ade/__main__.py:
  - Subscribes `anomalies.raw`, validates `anomaly_schema_version` (mismatches
    skipped + logged, never crash the loop).
  - Feeds each AnomalyRecord into a TriageBuffer (idempotency + sliding geo+time
    grouping with a max-age cap).
  - Publishes versioned TriagedAlert to `alerts.triaged`.

Ordering note (carried lesson): with auto.offset.reset=latest, this consumer
must be running BEFORE the ADE producer for a given run, or anomalies published
in the gap are missed entirely. Start CSAT first.

Env:
    CSAT_IN_TOPIC            input  (default anomalies.raw)
    CSAT_OUT_TOPIC           output (default alerts.triaged)
    CSAT_BOOTSTRAP           Kafka bootstrap (default localhost:9092)
    CSAT_GROUP_ID            consumer group (default csat)
    CSAT_POLL_TIMEOUT_S      poll timeout / tick cadence (default 1.0)
    CSAT_DEDUP_WINDOW_S      sliding idle gap (see triage.py)
    CSAT_MAX_AGE_S           max-age cap     (see triage.py)
    CSAT_DEDUP_RADIUS_M      lat/lon radius  (see triage.py; calibration-pending)

This module performs no ML. It does use a Kafka client at runtime, imported
lazily inside main() so `import kanatir.core.csat.__main__` stays light and the
contract/triage modules remain importable with no broker present.
"""

from __future__ import annotations

import json
import logging
import os
import time

from kanatir.core.ade.anomaly import ANOMALY_SCHEMA_VERSION, AnomalyRecord
from kanatir.core.csat.triage import TriageBuffer

log = logging.getLogger("csat")


def _major(version: str) -> str:
    return version.split(".", 1)[0]


def main() -> None:
    logging.basicConfig(
        level=os.getenv("CSAT_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(message)s",
    )
    # Lazy import: keep broker dep out of module import time.
    from confluent_kafka import Consumer, Producer

    in_topic = os.getenv("CSAT_IN_TOPIC", "anomalies.raw")
    out_topic = os.getenv("CSAT_OUT_TOPIC", "alerts.triaged")
    bootstrap = os.getenv("CSAT_BOOTSTRAP", "localhost:9092")
    group_id = os.getenv("CSAT_GROUP_ID", "csat")
    poll_timeout = float(os.getenv("CSAT_POLL_TIMEOUT_S", "1.0"))

    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": group_id,
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
        }
    )
    producer = Producer({"bootstrap.servers": bootstrap})
    consumer.subscribe([in_topic])

    buf = TriageBuffer()
    accepted_major = _major(ANOMALY_SCHEMA_VERSION)

    log.info(
        "csat.start in=%s out=%s group=%s accepts anomaly_schema_major=%s",
        in_topic,
        out_topic,
        group_id,
        accepted_major,
    )

    def _publish(alerts: list) -> None:
        for alert in alerts:
            producer.produce(out_topic, value=alert.to_json().encode("utf-8"))
            log.info(
                "csat.triaged alert_id=%s severity=%s n_anomalies=%d "
                "suppressed=%d baseline_state=%s conflict_k=%.3f "
                "audit_event_ids=%s",
                alert.alert_id,
                alert.severity,
                len(alert.anomaly_ids),
                alert.suppressed_count,
                alert.baseline_state,
                alert.conflict_k,
                alert.audit_event_ids,
            )
        producer.poll(0)

    try:
        while True:
            msg = consumer.poll(poll_timeout)
            now = time.monotonic()

            if msg is not None and not msg.error():
                try:
                    payload = json.loads(msg.value())
                    ver = payload.get("anomaly_schema_version", "")
                    if _major(ver) != accepted_major:
                        log.warning(
                            "csat.skip reason=schema_version got=%s want_major=%s",
                            ver,
                            accepted_major,
                        )
                    else:
                        rec = AnomalyRecord.from_json(msg.value())
                        buf.offer(rec, now=now)
                except Exception as exc:  # malformed payload: log, never crash
                    log.warning("csat.skip reason=parse err=%s", exc)
            elif msg is not None and msg.error():
                log.warning("csat.kafka_error %s", msg.error())

            _publish(buf.flush_ready(now=now))
    except KeyboardInterrupt:
        log.info("csat.shutdown draining open groups dropped_duplicates=%d", buf.dropped_duplicates)
        _publish(buf.drain(now=time.monotonic()))
        producer.flush(5.0)
        consumer.close()


if __name__ == "__main__":
    main()
