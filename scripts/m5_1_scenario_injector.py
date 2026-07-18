"""M5.1 live-gate scenario injector (Scenarios A-D + degraded).

============================ HONESTY / METHODOLOGY ============================
Read this before running or citing anything this tool produces.

INJECTION POINT = THE EARLIEST REAL TOPIC. This publishes real, schema-validated
`FeatureEnvelope`s to `features.video` / `features.acoustic` / `features.rf`,
exactly as the CVP/APP pipelines would, and then lets the REAL
MSFE -> ADE -> CSAT -> XAI chain do every hop. It NEVER constructs a FusedObject,
AnomalyRecord, or TriagedAlert, and NEVER short-circuits an engine. Every gate is
exercised for real; only the *input frames* are controlled.

WHY AN INJECTOR (not live capture). Scenario B requires two DISTINCT cameras
emitting the SAME integer ByteTrack id — which cannot be produced on demand by
live webcams. The injector controls (source_sensor_id, track_id, class, site) so
it can prove the DATA-PLANE property M5.1 exists to deliver: does the pipeline
preserve and *distinguish* source-local video-track references end to end? That
is a data-plane test, not a perception test. It does not claim the perception
front-end produced these tracks.

LABELING REQUIREMENT. Any evidence produced with this tool MUST be recorded in
`M5_1_completion.md` as "injected (deterministic scenario producer at
features.*)", never presented as live-sensor capture. An injected scenario
proving a data-plane property is legitimate; mislabeling it as live capture is
not.

AUDIT / PGC CAVEAT. Each envelope carries a synthetic `audit_event_id` for
lineage tracing. The injector does NOT invoke the PGC privacy-gate ledger writer,
so these ids demonstrate lineage FLOW (envelope -> fused -> anomaly -> alert), not
ledger population. Real "audit-ledger counts before/after" evidence must come
from a real CVP/APP capture path — this tool is not a substitute for it.

Usage:
    KAFKA_BOOTSTRAP=localhost:9092 python3 scripts/m5_1_scenario_injector.py \\
        --scenario B --site zone-B

    # Scenario D runs in real time for --duration seconds; run it backgrounded.
    KAFKA_BOOTSTRAP=localhost:9092 python3 scripts/m5_1_scenario_injector.py \\
        --scenario D --site zone-D --duration 70 --spacing 8
=============================================================================
"""

from __future__ import annotations

import argparse
import itertools
import time
from datetime import UTC, datetime

from kanatir.pipelines.common.envelope import (
    AcousticFeatures,
    Detection,
    FeatureEnvelope,
    GeoRef,
    Modality,
    PrivacyBlock,
    RFFeatures,
    VideoFeatures,
)
from kanatir.pipelines.common.producer import EnvelopeProducer, default_bootstrap

VIDEO_TOPIC = "features.video"
ACOUSTIC_TOPIC = "features.acoustic"
RF_TOPIC = "features.rf"

# Monotonic source of distinct synthetic audit_event_ids (lineage tracing only).
_audit_seq = itertools.count(900_001)


def _now() -> datetime:
    return datetime.now(UTC)


def _privacy() -> PrivacyBlock:
    # gate_passed MUST be True (fail-closed validator); audit_event_id is a
    # synthetic lineage marker (see AUDIT / PGC CAVEAT in the module docstring).
    return PrivacyBlock(gate_passed=True, pii_present=False, audit_event_id=next(_audit_seq))


def _video_env(
    source_sensor_id: str,
    site_id: str,
    detections: list[tuple[int, str]],
) -> FeatureEnvelope:
    """A real video envelope. `detections` is a list of (track_id, cls) — cls
    drives the D-S classification (uav->UAV, car/person->GROUND; empty->UNKNOWN).
    An empty list is the honest 'video window with no usable track id' case."""
    now = _now()
    return FeatureEnvelope(
        modality=Modality.VIDEO,
        source_sensor_id=source_sensor_id,
        geo=GeoRef(site_id=site_id),
        capture_ts=now,
        ingest_ts=now,
        privacy=_privacy(),
        features=VideoFeatures(
            frame_w=1920,
            frame_h=1080,
            detections=[
                Detection(track_id=t, cls=c, confidence=0.9, bbox_xyxy=(0.0, 0.0, 10.0, 10.0))
                for t, c in detections
            ],
        ),
    )


def _acoustic_env(source_sensor_id: str, site_id: str, label: str, score: float) -> FeatureEnvelope:
    """A real acoustic envelope. `label` is a YAMNet-style phrase; 'Wind'/'Silence'
    map to AMBIENT (video cannot produce AMBIENT, so mixed-class scenarios need
    this). Carries NO track refs by construction (acoustic modality)."""
    now = _now()
    return FeatureEnvelope(
        modality=Modality.ACOUSTIC,
        source_sensor_id=source_sensor_id,
        geo=GeoRef(site_id=site_id),
        capture_ts=now,
        ingest_ts=now,
        privacy=_privacy(),
        features=AcousticFeatures(
            sample_rate=16_000,
            window_s=1.0,
            yamnet_top=[(label, score)],
            mfcc_mean=[0.0] * 13,
        ),
    )


def _rf_env(source_sensor_id: str, site_id: str) -> FeatureEnvelope:
    """A real RF-only envelope (passive device presence). No video, no track refs
    — the degraded 'RF-only window' case."""
    now = _now()
    return FeatureEnvelope(
        modality=Modality.RF,
        source_sensor_id=source_sensor_id,
        geo=GeoRef(site_id=site_id),
        capture_ts=now,
        ingest_ts=now,
        privacy=_privacy(),
        features=RFFeatures(
            window_s=1.0,
            band="wifi_2g4",
            emitter_count=8,
            new_emitter_rate=0.5,
            unknown_emitter_rate=0.1,
            rssi_mean=-60.0,
            rssi_variance=4.0,
            channel_occupancy=0.3,
            probe_density=1.0,
            burst_rate=0.5,
        ),
    )


class _Pub:
    """Thin wrapper: publish a real envelope to its feature topic and log it."""

    def __init__(self, bootstrap: str) -> None:
        self._ep = EnvelopeProducer(bootstrap)

    def send(self, topic: str, env: FeatureEnvelope) -> None:
        self._ep.publish(topic, env)
        feats = env.features
        detail = ""
        if isinstance(feats, VideoFeatures):
            detail = f"tracks={[(d.track_id, d.cls) for d in feats.detections] or 'none'}"
        elif isinstance(feats, AcousticFeatures):
            detail = f"yamnet={feats.yamnet_top}"
        else:
            detail = f"band={feats.band}"
        print(
            f"  -> {topic:18s} src={env.source_sensor_id:8s} site={env.geo.site_id} "
            f"aid={env.privacy.audit_event_id} {detail}",
            flush=True,
        )

    def flush(self) -> None:
        self._ep.flush(5.0)


# --------------------------------------------------------------------------- #
# Scenarios. Each publishes ONLY real envelopes; the engines do the rest.
# --------------------------------------------------------------------------- #


def scenario_a(pub: _Pub, site: str, count: int, spacing: float) -> None:
    """A - one track, repeated observations. `count` separate windows (spaced >
    MSFE window apart), one camera, SAME (source, track). Each matures as its own
    FusedObject -> its own anomaly; CSAT groups them into ONE alert with
    observation_count=count, distinct_video_track_ref_count=1."""
    print(f"[A] one track / {count} observations @ {site} (spacing {spacing}s)")
    for i in range(count):
        pub.send(VIDEO_TOPIC, _video_env("cam-01", site, [(7, "uav")]))
        if i < count - 1:
            time.sleep(spacing)
    pub.flush()


def scenario_b(pub: _Pub, site: str, spacing: float) -> None:
    """B - two distinct cameras, SAME integer track_id, DIFFERENT source_sensor_id.

    `spacing` selects WHICH union path is exercised (the refs are identical either
    way - exactly {(cam-01,5),(cam-02,5)}, the same integer track_id under two
    sensors):
      spacing > MSFE window -> two separate FusedObjects -> two anomalies -> CSAT
                               _union_track_refs unions them (observation_count=2,
                               distinct=2). [B]
      spacing < MSFE window -> one co-windowed FusedObject -> MSFE same-window union
                               (observation_count=1, distinct=2). [B-prime]
    """
    print(f"[B] two cameras, same track_id=5 @ {site} (spacing {spacing}s)")
    pub.send(VIDEO_TOPIC, _video_env("cam-01", site, [(5, "uav")]))
    time.sleep(spacing)
    pub.send(VIDEO_TOPIC, _video_env("cam-02", site, [(5, "uav")]))
    pub.flush()


def scenario_c(pub: _Pub, site: str, spacing: float) -> None:
    """C - mixed classes at one site. UAV (video), GROUND (video), AMBIENT
    (acoustic - video cannot yield AMBIENT). Three separate windows grouped by
    CSAT -> class_breakdown = {UAV:1, GROUND:1, AMBIENT:1, UNKNOWN:0}; trigger
    classification retained separately."""
    print(f"[C] mixed classes UAV+GROUND+AMBIENT @ {site} (spacing {spacing}s)")
    pub.send(VIDEO_TOPIC, _video_env("cam-01", site, [(1, "uav")]))
    time.sleep(spacing)
    pub.send(VIDEO_TOPIC, _video_env("cam-01", site, [(2, "car")]))
    time.sleep(spacing)
    pub.send(ACOUSTIC_TOPIC, _acoustic_env("app-01", site, "Wind", 0.8))
    pub.flush()


def scenario_d(pub: _Pub, site: str, duration: float, spacing: float) -> None:
    """D - long-running incident. Feed one site's group every `spacing`s for
    `duration`s so it never goes idle; run CSAT with a SHORT CSAT_MAX_AGE_S so the
    group force-emits mid-incident. incident_id stays stable across the max-age
    flush; incident_sequence increments. (Set CSAT_MAX_AGE_S / CSAT_DEDUP_WINDOW_S
    on the CSAT engine, e.g. 30 / 20, before starting this.)"""
    print(f"[D] long-running incident @ {site} for {duration}s (feed every {spacing}s)")
    start = time.monotonic()
    i = 0
    while time.monotonic() - start < duration:
        pub.send(VIDEO_TOPIC, _video_env("cam-01", site, [(7, "uav")]))
        i += 1
        time.sleep(spacing)
    pub.flush()
    print(f"[D] fed {i} observations over ~{duration}s")


def scenario_degraded(pub: _Pub, site: str) -> None:
    """Degraded inputs - each must yield identity_reference_available=false and
    distinct_video_track_ref_count=null (never 0):
      - RF-only window
      - acoustic-only window
      - video window with NO usable track id (empty detections)
    Published at DISTINCT sites so they do not group together."""
    print(f"[degraded] rf-only / acoustic-only / video-no-track @ {site}-*")
    pub.send(RF_TOPIC, _rf_env("rap-01", f"{site}-rf"))
    time.sleep(0.3)
    pub.send(ACOUSTIC_TOPIC, _acoustic_env("app-01", f"{site}-ac", "Silence", 0.7))
    time.sleep(0.3)
    pub.send(VIDEO_TOPIC, _video_env("cam-09", f"{site}-novid", []))  # no detections
    pub.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description="M5.1 scenario injector (features.* boundary)")
    ap.add_argument("--scenario", required=True, choices=["A", "B", "C", "D", "degraded"])
    ap.add_argument("--site", default="zone-A", help="site_id for the scenario's spatial key")
    ap.add_argument("--bootstrap", default=None, help="Kafka bootstrap (default localhost:9092)")
    ap.add_argument("--count", type=int, default=5, help="[A] number of observations")
    ap.add_argument("--spacing", type=float, default=3.0, help="[A/C/D] seconds between windows")
    ap.add_argument("--duration", type=float, default=70.0, help="[D] total feed duration (s)")
    args = ap.parse_args()

    bootstrap = args.bootstrap or default_bootstrap()
    print(f"injector: bootstrap={bootstrap} scenario={args.scenario} site={args.site}")
    print("NOTE: injected at features.* - real MSFE/ADE/CSAT/XAI run every hop; "
          "label evidence as 'injected (deterministic producer)'.", flush=True)
    pub = _Pub(bootstrap)

    if args.scenario == "A":
        scenario_a(pub, args.site, args.count, args.spacing)
    elif args.scenario == "B":
        scenario_b(pub, args.site, args.spacing)
    elif args.scenario == "C":
        scenario_c(pub, args.site, args.spacing)
    elif args.scenario == "D":
        scenario_d(pub, args.site, args.duration, args.spacing)
    elif args.scenario == "degraded":
        scenario_degraded(pub, args.site)

    print("injector: done (envelopes flushed).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
