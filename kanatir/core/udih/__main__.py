"""Entry point for UDIH skeleton service (Sprint 1-2)."""

from kanatir.core.udih.admin import ensure_topics, verify_topics

if __name__ == "__main__":
    ensure_topics()
    ok = verify_topics()
    exit(0 if ok else 1)