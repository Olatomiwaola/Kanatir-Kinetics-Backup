"""
Anomaly Detection Engine (ADE) — Sprint 7-8 / M4.

Consumes `fused.objects` (FusedObject, fused_schema_version 1.0.0) and produces
`anomalies.raw` (AnomalyRecord, anomaly_schema_version 1.0.0).

kanatir.core.ade ships as a CORE package, but its ML dependencies (sklearn for
Isolation Forest; torch for the scaffolded LSTM-AE / GNN) live behind the [ade]
optional-dependency extra and are imported LAZILY inside detector methods. So
`import kanatir.core.ade` and the contract/feature/baseline/ensemble modules all
import with no ML present — same discipline as kanatir.pipelines under
[pipelines]. The invariant is "the core install pulls no ML", not "no core
package may use ML".

This __init__ deliberately re-exports only ML-free surfaces.
"""

from kanatir.core.ade.anomaly import (
    ANOMALY_SCHEMA_VERSION,
    AnomalyRecord,
    BaselineState,
)

__all__ = [
    "ANOMALY_SCHEMA_VERSION",
    "AnomalyRecord",
    "BaselineState",
]
