"""
CSAT — Common Situational Awareness & Triage.

Consumes `anomalies.raw` (AnomalyRecord, anomaly_schema_version 1.0.0), triages
into operator-facing situation-awareness items, and publishes versioned
TriagedAlert (sa_schema_version 1.0.0) to `alerts.triaged`.

Triage = severity assignment + idempotent dedup + sliding geo+time grouping.
Pure rule-based; ML-free by design (no [csat] extra). The human-readable "why"
is XAI's job downstream on `alerts.explained` — CSAT carries the explainability
inputs (detector_scores, conflict_k) through verbatim.
"""
