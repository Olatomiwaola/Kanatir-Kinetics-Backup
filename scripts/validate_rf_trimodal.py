"""
Tri-modal validation harness (TRL 3->4 block).

Exercises the full evidence path with optical + acoustic + RF and verifies:

  V1. RF FeatureEnvelope is published on a replayed capture (built via RAP,
      gate-passed, on the features.rf contract).
  V2. Optical + acoustic + RF co-windowed evidence produces a TRI-MODAL
      FusedObject (n_modalities == 3, is_multimodal True, lineage from all
      three preserved as contributors).
  V3. Graceful RF dropout: with RF absent, fusion still produces a coherent
      bimodal FusedObject, and the RF contribution is the vacuous identity
      (its absence does not move the optical+acoustic belief).
  V4. Privacy/audit controls are applied to RF-derived data (PrivacyBlock
      gate_passed, audit_event_id linked, no raw identifiers in the envelope).

This mirrors how MSFE assembles a FusedObject (combine_all over per-envelope
masses + Contributor lineage) without requiring the Kafka/Postgres runtime. The
sealed fusion math (dempster_shafer.combine_all) and contracts are used
unchanged; only the windowing/assembly is inlined here for offline replay.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

from kanatir.core.msfe.dempster_shafer import combine_all
from kanatir.core.msfe.evidence import envelope_to_mass
from kanatir.core.msfe.fused import (
    UNKNOWN,
    BeliefMass,
    Contributor,
    FusedObject,
)
from kanatir.pipelines.common.envelope import (
    AcousticFeatures,
    FeatureEnvelope,
    Modality,
    PrivacyBlock,
    VideoFeatures,
)
from kanatir.pipelines.rap.features import build_rf_envelope
from kanatir.pipelines.rap.scrub import RawRFObservation, RotatingSalt

W_START = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)
W_END = W_START + timedelta(seconds=2)


def _privacy(eid: int) -> PrivacyBlock:
    return PrivacyBlock(gate_passed=True, pii_present=False, pii_scrubbed=False,
                        audit_event_id=eid)


def _video_env() -> FeatureEnvelope:
    from kanatir.pipelines.common.envelope import Detection
    return FeatureEnvelope(
        modality=Modality.VIDEO, source_sensor_id="cvp-01",
        capture_ts=W_START, privacy=_privacy(101),
        features=VideoFeatures(
            frame_w=1920, frame_h=1080,
            detections=[Detection(track_id=1, cls="person", confidence=0.7,
                                  bbox_xyxy=(10, 10, 50, 120))],
        ),
    )


def _acoustic_env() -> FeatureEnvelope:
    return FeatureEnvelope(
        modality=Modality.ACOUSTIC, source_sensor_id="app-01",
        capture_ts=W_START, privacy=_privacy(102),
        features=AcousticFeatures(
            sample_rate=16000, window_s=2.0,
            yamnet_top=[("Vehicle", 0.6), ("Wind", 0.2)],
            mfcc_mean=[0.1] * 13,
        ),
    )


def _rf_env() -> FeatureEnvelope:
    obs = [
        RawRFObservation(raw_id="AA:BB:CC:00:00:01", rssi=-58.0, is_probe=True,
                         first_seen=True, known=False),
        RawRFObservation(raw_id="AA:BB:CC:00:00:02", rssi=-62.0, is_burst=True,
                         first_seen=True, known=False),
        RawRFObservation(raw_id="AA:BB:CC:00:00:03", rssi=-66.0, is_probe=True,
                         known=True),
        RawRFObservation(raw_id="AA:BB:CC:00:00:04", rssi=-61.0, is_burst=True,
                         first_seen=True, known=False),
        RawRFObservation(raw_id="AA:BB:CC:00:00:05", rssi=-70.0, is_probe=True,
                         known=True),
    ]
    return build_rf_envelope(
        observations=obs, sensor_id="rap-01", band="wifi_2g4", window_s=2.0,
        salt=RotatingSalt(interval_s=900), capture_ts=W_START,
    )


def _assemble(envs: list[FeatureEnvelope]) -> FusedObject:
    masses = [envelope_to_mass(e) for e in envs]
    combined, k = combine_all(masses)
    belief = BeliefMass(masses=combined, conflict_k=k)
    contributors = [
        Contributor(envelope_id=e.envelope_id, modality=e.modality,
                    source_sensor_id=e.source_sensor_id, capture_ts=e.capture_ts,
                    audit_event_id=e.privacy.audit_event_id)
        for e in envs
    ]
    n_mod = len({c.modality for c in contributors})
    return FusedObject(
        window_start=W_START, window_end=W_END,
        belief=belief, classification=belief.top_hypothesis,
        confidence=belief.top_confidence, contributors=contributors,
        n_modalities=n_mod, is_multimodal=n_mod >= 2,
    )


def main() -> int:
    ok = True

    # V1 — RF publication
    rf = _rf_env()
    raw = rf.to_json()
    v1 = (rf.modality == Modality.RF and rf.privacy.gate_passed
          and rf.features.emitter_count == 5)
    print(f"[V1] RF envelope published on features.rf contract: {v1}")
    print(f"     emitter_count={rf.features.emitter_count} "
          f"band={rf.features.band} audit_event_id={rf.privacy.audit_event_id}")
    ok &= v1

    # V4 — privacy/audit on RF (checked early; uses same envelope)
    no_raw = all(o not in raw for o in
                 ["AA:BB:CC:00:00:01", "AA:BB:CC:00:00:02", "AA:BB:CC:00:00:03",
                  "AA:BB:CC:00:00:04", "AA:BB:CC:00:00:05"])
    v4 = (rf.privacy.gate_passed and rf.privacy.audit_event_id is not None
          and no_raw and "hashed_ids" not in raw)
    print(f"[V4] RF privacy/audit applied (gate_passed, audit linked, "
          f"no raw ids, derived-only): {v4}")
    ok &= v4

    # V2 — tri-modal fused object
    tri = _assemble([_video_env(), _acoustic_env(), rf])
    v2 = (tri.n_modalities == 3 and tri.is_multimodal
          and {c.modality for c in tri.contributors}
          == {Modality.VIDEO, Modality.ACOUSTIC, Modality.RF}
          and "UAV" not in {c.modality for c in tri.contributors})
    print(f"[V2] tri-modal FusedObject (V+A+RF): n_modalities={tri.n_modalities} "
          f"multimodal={tri.is_multimodal} class={tri.classification} "
          f"conf={tri.confidence:.3f} K={tri.belief.conflict_k:.3f}")
    print(f"     contributors={[str(c.modality) for c in tri.contributors]}")
    print(f"     belief={ {k: round(v,3) for k,v in tri.belief.masses.items()} }")
    ok &= v2

    # V3 — graceful RF dropout
    bimodal = _assemble([_video_env(), _acoustic_env()])
    va_mass, va_k = combine_all([envelope_to_mass(_video_env()),
                                 envelope_to_mass(_acoustic_env())])
    va_plus_missing_rf, _ = combine_all([
        envelope_to_mass(_video_env()), envelope_to_mass(_acoustic_env()),
        {UNKNOWN: 1.0},  # vacuous = missing RF identity
    ])
    drift = max(abs(va_mass.get(k, 0.0) - va_plus_missing_rf.get(k, 0.0))
                for k in set(va_mass) | set(va_plus_missing_rf))
    v3 = (bimodal.n_modalities == 2 and bimodal.is_multimodal and drift < 1e-9)
    print(f"[V3] graceful RF dropout: bimodal n_modalities={bimodal.n_modalities} "
          f"missing-RF belief drift={drift:.2e} (vacuous identity): {v3}")
    ok &= v3

    print()
    print(f"RESULT: {'ALL CHECKS PASSED' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
