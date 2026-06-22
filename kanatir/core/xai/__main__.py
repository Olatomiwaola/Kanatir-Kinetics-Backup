"""
XAI live consumer — `alerts.triaged` -> explain -> `alerts.explained`.

The M6 pipeline stage. Subscribes to triaged alerts, runs the selected Explainer
(templated by default, Claude only on explicit opt-in), and publishes versioned
ExplainedAlert records. Implementation-agnostic: it holds an `Explainer` and
never branches on the concrete type.

Selection (explicit, env-driven):
    XAI_EXPLAINER=templated   (default — authoritative, CI/gate path)
    XAI_EXPLAINER=claude      (opt-in demo enrichment; needs [xai-claude] + key)

Consumer ordering note (matches CSAT/ADE): with auto.offset.reset=latest, this
consumer must be subscribed BEFORE the upstream CSAT producer starts, or alerts
published in the gap are missed entirely.

Schema gating: validates explained_schema_major against the records it reads and
skips/logs mismatches, mirroring the upstream stages.
"""

from __future__ import annotations

import os
import signal
import sys
from typing import TYPE_CHECKING

import structlog

from kanatir.core.csat.alert import SA_SCHEMA_VERSION, TriagedAlert
from kanatir.core.udih.topics import TopicSpec  # noqa: F401  (topic registry is source of truth)
from kanatir.core.xai.explained import EXPLAINED_SCHEMA_VERSION
from kanatir.core.xai.explainer import TemplatedExplainer

if TYPE_CHECKING:
    from kanatir.core.xai.explainer import Explainer

log = structlog.get_logger("xai")

IN_TOPIC = "alerts.triaged"
OUT_TOPIC = "alerts.explained"
GROUP_ID = "xai"

_SA_MAJOR = SA_SCHEMA_VERSION.split(".")[0]


def select_explainer() -> Explainer:
    """Pick the explainer from env. Templated is the default and the only path
    CI / the M6 gate ever runs. Claude is strictly opt-in.
    """
    kind = os.environ.get("XAI_EXPLAINER", "templated").strip().lower()
    if kind == "claude":
        # Imported here, not at top, so the module loads with no [xai-claude].
        from kanatir.core.xai.claude import ClaudeExplainer

        fallback = os.environ.get("XAI_CLAUDE_FALLBACK", "").lower() in ("1", "true", "yes")
        log.info("xai.explainer.select", kind="claude", fallback_to_templated=fallback)
        return ClaudeExplainer(fallback_to_templated=fallback)
    if kind not in ("templated", ""):
        log.warning("xai.explainer.unknown", requested=kind, using="templated")
    log.info("xai.explainer.select", kind="templated")
    return TemplatedExplainer()


def _accepts(alert: TriagedAlert) -> bool:
    major = alert.sa_schema_version.split(".")[0]
    if major != _SA_MAJOR:
        log.warning(
            "xai.skip.schema_mismatch",
            got=alert.sa_schema_version,
            accepts_major=_SA_MAJOR,
        )
        return False
    return True


def run() -> None:  # pragma: no cover - requires a live broker
    from confluent_kafka import Consumer, Producer

    explainer = select_explainer()
    log.info(
        "xai.start",
        in_topic=IN_TOPIC,
        out_topic=OUT_TOPIC,
        group=GROUP_ID,
        explainer_kind=explainer.kind,
        emits_schema=EXPLAINED_SCHEMA_VERSION,
        accepts_sa_major=_SA_MAJOR,
    )

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": GROUP_ID,
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
        }
    )
    producer = Producer({"bootstrap.servers": bootstrap})
    consumer.subscribe([IN_TOPIC])

    running = True

    def _stop(_signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    n_in = n_out = n_skipped = 0
    try:
        while running:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                log.error("xai.consume.error", error=str(msg.error()))
                continue
            n_in += 1
            try:
                alert = TriagedAlert.from_json(msg.value())
            except Exception as exc:  # malformed record
                n_skipped += 1
                log.warning("xai.skip.parse", error=str(exc))
                continue
            if not _accepts(alert):
                n_skipped += 1
                continue

            explained = explainer.explain(alert)
            producer.produce(OUT_TOPIC, value=explained.to_json().encode("utf-8"))
            producer.poll(0)
            n_out += 1
            log.info(
                "xai.explained",
                explained_id=explained.explained_id,
                alert_id=explained.alert_id,
                severity=explained.severity.value,
                explainer_kind=explained.explainer_kind,
                attribution_available=explained.attribution_available,
                n_attributions=len(explained.attributions),
                audit_event_ids=explained.audit_event_ids,
            )
    finally:
        producer.flush(5.0)
        consumer.close()
        log.info("xai.stop", consumed=n_in, explained=n_out, skipped=n_skipped)


if __name__ == "__main__":  # pragma: no cover
    try:
        run()
    except KeyboardInterrupt:
        sys.exit(0)
