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

from kanatir.core.msfe.acoustic_meta import acoustic_meta_from_yamnet
from kanatir.core.msfe.dempster_shafer import combine_all
from kanatir.core.msfe.evidence import envelope_to_mass
from kanatir.core.msfe.fused import (
    HYPOTHESES,
    UNKNOWN,
    AcousticMeta,
    BeliefMass,
    Contributor,
    FusedObject,
    SourceTrackRef,
)
from kanatir.pipelines.common.envelope import (
    AcousticFeatures,
    FeatureEnvelope,
    GeoRef,
    VideoFeatures,
)

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


def _acoustic_meta_for(envelopes: list[FeatureEnvelope]) -> AcousticMeta | None:
    """
    Derive AcousticMeta for a correlated group, or None if no acoustic envelope
    contributed. M9 (TRL 3->4): carries YAMNet distinctiveness onto the fused
    object PAST the D-S mass collapse, via the same acoustic_meta_from_yamnet
    helper the fit corpus is built through — so the live serve path and the fit
    path compute acoustic_meta identically (no train/serve skew).

    If multiple acoustic envelopes co-window, the one with the highest YAMNet
    top score wins; ties break on lowest envelope_id (deterministic). This mirrors
    "the strongest acoustic evidence in the window drives the meta", consistent
    with the sealed mapper taking the max-score label.
    """
    acoustic = [
        e for e in envelopes if isinstance(e.features, AcousticFeatures)
    ]
    if not acoustic:
        return None

    def _key(e: FeatureEnvelope) -> tuple[float, str]:
        feats = e.features
        top = max((s for _, s in feats.yamnet_top), default=0.0)
        # Negate id for "lowest id wins" under max(): compare (score, -ord-ish);
        # simpler: pick max score, tiebreak lowest envelope_id lexically.
        return (top, e.envelope_id)

    # Highest top score; on tie, lowest envelope_id. max() on score, but for the
    # tiebreak we want the SMALLEST id, so select explicitly.
    best = acoustic[0]
    best_top = max((s for _, s in best.features.yamnet_top), default=0.0)
    for e in acoustic[1:]:
        top = max((s for _, s in e.features.yamnet_top), default=0.0)
        if top > best_top or (top == best_top and e.envelope_id < best.envelope_id):
            best, best_top = e, top

    return acoustic_meta_from_yamnet(best.features.yamnet_top)


def _track_refs_for(
    envelopes: list[FeatureEnvelope],
) -> list[SourceTrackRef] | None:
    """
    Extract the distinct source-local video-track references in this window, or
    None if no video-track-reference information is available (M5.1, TRL 3->4).

    Mirrors _acoustic_meta_for: modality-aware, additive, and deliberately
    conservative about the no-information case. Walks every VIDEO envelope, reads
    each Detection.track_id, and qualifies it with the envelope's
    source_sensor_id. Deduplicates on the (source_sensor_id, track_id) pair, so
    the same track id from two cameras yields two refs and a repeated detection
    from one track yields one.

    Returns None — NOT [] — when no video envelope contributed, or the video
    envelopes carried no usable track ids. The None-vs-populated distinction is
    load-bearing: it maps to CSAT's distinct_video_track_ref_count = null (never
    0), i.e. "unavailable" rather than "confirmed zero". A populated result is a
    set of references, not a physical-object count.
    """
    seen: set[tuple[str, int]] = set()
    refs: list[SourceTrackRef] = []
    for e in envelopes:
        feats = e.features
        if not isinstance(feats, VideoFeatures):
            continue
        for det in feats.detections:
            key = (e.source_sensor_id, det.track_id)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                SourceTrackRef(source_sensor_id=e.source_sensor_id, track_id=det.track_id)
            )
    return refs or None


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
        acoustic_meta=_acoustic_meta_for(envelopes),
        source_track_refs=_track_refs_for(envelopes),
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
