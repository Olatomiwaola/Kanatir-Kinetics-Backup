"""
UDIH — Universal Data Ingestion Hub
Kafka topic provisioning. Idempotent: safe to run on every startup.
"""

import logging
import os

from confluent_kafka.admin import AdminClient, NewTopic

from kanatir.core.udih.topics import TOPICS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("udih.admin")


def get_bootstrap_servers() -> str:
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


def ensure_topics() -> None:
    """Create all topics from Section 3.2 if they don't already exist."""
    bootstrap = get_bootstrap_servers()
    admin = AdminClient({"bootstrap.servers": bootstrap})

    existing = admin.list_topics(timeout=10).topics.keys()

    to_create = [
        NewTopic(
            topic.name,
            num_partitions=3,
            replication_factor=1,
            config={"retention.ms": str(topic.retention_ms)},
        )
        for topic in TOPICS
        if topic.name not in existing
    ]

    if not to_create:
        logger.info("All %d topics already exist. Nothing to do.", len(TOPICS))
        return

    logger.info("Creating %d new topic(s)...", len(to_create))
    futures = admin.create_topics(to_create)

    for topic_name, future in futures.items():
        try:
            future.result()
            logger.info("✅ Created topic: %s", topic_name)
        except Exception as e:
            logger.error("❌ Failed to create topic %s: %s", topic_name, e)
            raise


def verify_topics() -> bool:
    """Confirm every topic from Section 3.2 exists on the broker."""
    bootstrap = get_bootstrap_servers()
    admin = AdminClient({"bootstrap.servers": bootstrap})
    existing = admin.list_topics(timeout=10).topics.keys()

    missing = [t.name for t in TOPICS if t.name not in existing]
    if missing:
        logger.warning("Missing topics: %s", missing)
        return False

    logger.info("✅ All %d topics verified present.", len(TOPICS))
    return True


if __name__ == "__main__":
    ensure_topics()
    verify_topics()