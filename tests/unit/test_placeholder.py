"""Unit tests for UDIH topic provisioning (Sprint 1 Task 4)."""

from kanatir.core.udih.topics import TOPICS


def test_kanatir_package_importable():
    import kanatir
    assert kanatir is not None


def test_topic_count_matches_blueprint():
    """Section 3.2 of the blueprint defines exactly 15 topics."""
    assert len(TOPICS) == 15


def test_topic_names_are_unique():
    names = [t.name for t in TOPICS]
    assert len(names) == len(set(names))


def test_all_topics_have_positive_retention():
    assert all(t.retention_ms > 0 for t in TOPICS)