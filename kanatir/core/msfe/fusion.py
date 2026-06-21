"""
Fusion core — correlate co-windowed envelopes and emit a FusedObject.

This is the logic that, in a Flink deployment, windowing operators would host.
We keep it as plain, testable Python (the Sprint 3-4 execution-model decision:
host-native arm64 parity with CVP/APP, Flink deferred to the Jetson sprint).

The correlation rule for M3 is deliberately simple and defensible: envelopes
whose capture timestamps fall in the same fixed time window, optionally sharing
a spatial key (site_id), are candidates for fusion. The MSFEngine (consumer.py)
owns the live Redis-backed sliding buffer; this module owns the *decision* —
given a set of envelopes, group and fuse them — so it can be unit-tested with no
broker and no Redis.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from kanatir.core.msfe.dempster_shafer import combine_all
from kanatir.core.msfe.evidence import envelope_to_mass
from kanatir.core.msfe.fused import (
    HYPOTHESES,
    UNKNOWN,
    BeliefMass,
    Contributor,
    FusedObject,
)
from kanatir.pipelines.common.envelope import FeatureEnvelope, GeoRef

# Default correlation window. Two envelopes are co-windowed if their correlation
# timestamps fall within this span. Tunable per deployment.
DEFAULT_WINDOW = timedelta(seconds=2.0)


def correlation_ts(env: FeatureEnvelope) -> datetime:
    """
    The timestamp MSFE correlates on.

    We use ``ingest_ts`` (when the pipeline emitted the envelope onto the bus),
    NOT ``capture_ts``. For a live sensor the two are within milliseconds. For a
    *file-replayed* source, ``capture_ts`` carries the media's own internal
    timeline, so video and acoustic clips drained independently would never
    co-window even though they were published to the bus at the same wall-clock
    moment. Correlating on ``ingest_ts`` makes "arrived on the bus together"
    the fusion condition, which is the correct semantics for both live and
    replayed sources. The fused object still records capture_ts-based
    window_start/window_end for provenance.
    """
    return env.ingest_ts


def _spatial_key(env: FeatureEnvelope) -> str:
    """
    The key envelopes must share to be fused. site_id when present, else a
    coarse lat/lon cell, else "GLOBAL" (single-site demo default). Keeps two
    unrelated sites from fusing into one object.
    """
    if env.geo.site_id:
        return f"site:{env.geo.site_id}"
    if env.geo.lat is not None and env.geo.lon is not None:
        return f"cell:{round(env.geo.lat, 3)}:{round(env.geo.lon, 3)}"
    return "GLOBAL"


def _belief_from(envelopes: list[FeatureEnvelope]) -> BeliefMass:
    masses = [envelope_to_mass(e) for e in envelopes]
    combined, k = combine_all(masses)
    # Ensure every focal label is present (zero-filled) so the contract is stable
    # for downstream consumers regardless of which sensors fired.
    full = {h: 0.0 for h in HYPOTHESES}
    full[UNKNOWN] = 0.0
    for label, m in combined.items():
        full[label] = full.get(label, 0.0) + m
    # Re-normalize defensively against float drift before constructing the model.
    total = sum(full.values()) or 1.0
    full = {k2: v / total for k2, v in full.items()}
    return BeliefMass(masses=full, conflict_k=k)


def fuse_window(envelopes: list[FeatureEnvelope]) -> FusedObject | None:
    """
    Fuse one correlated group into a FusedObject. Returns None for an empty
    group. A single-source group still fuses (degraded but valid); the M3
    "heterogeneous" condition is reported via is_multimodal/n_modalities so a
    consumer can choose to require >= 2 modalities for an alert.
    """
    if not envelopes:
        return None

    belief = _belief_from(envelopes)
    contributors = [
        Contributor(
            envelope_id=e.envelope_id,
            modality=e.modality,
            source_sensor_id=e.source_sensor_id,
            capture_ts=e.capture_ts,
            audit_event_id=e.privacy.audit_event_id,
        )
        for e in envelopes
    ]
    caps = [e.capture_ts for e in envelopes]  # provenance window (media time)
    mods = {e.modality for e in envelopes}

    # Geo: take the first contributor that carries a fix, else default.
    geo = GeoRef()
    for e in envelopes:
        if e.geo.site_id or (e.geo.lat is not None and e.geo.lon is not None):
            geo = e.geo
            break

    return FusedObject(
        window_start=min(caps),
        window_end=max(caps),
        geo=geo,
        belief=belief,
        classification=belief.top_hypothesis,
        confidence=belief.top_confidence,
        contributors=contributors,
        n_modalities=len(mods),
        is_multimodal=len(mods) >= 2,
    )


def correlate(
    envelopes: Iterable[FeatureEnvelope],
    window: timedelta = DEFAULT_WINDOW,
) -> list[list[FeatureEnvelope]]:
    """
    Group envelopes into correlation sets by spatial key + time window.

    Greedy single-pass clustering: sort by correlation_ts within each spatial
    key, then start a new group whenever an envelope's correlation_ts is more than
    `window` past the *first* member of the current group. Sufficient and
    predictable for the M3 gate; a later sprint can swap a smarter tracker
    behind this same signature.
    """
    by_key: dict[str, list[FeatureEnvelope]] = {}
    for e in envelopes:
        by_key.setdefault(_spatial_key(e), []).append(e)

    groups: list[list[FeatureEnvelope]] = []
    for items in by_key.values():
        items.sort(key=correlation_ts)
        cur: list[FeatureEnvelope] = []
        anchor = None
        for e in items:
            if anchor is None or (correlation_ts(e) - anchor) <= window:
                if anchor is None:
                    anchor = correlation_ts(e)
                cur.append(e)
            else:
                groups.append(cur)
                cur = [e]
                anchor = correlation_ts(e)
        if cur:
            groups.append(cur)
    return groups
