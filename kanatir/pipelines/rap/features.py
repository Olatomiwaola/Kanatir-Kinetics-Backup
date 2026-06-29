"""
RAP — RF Acquisition Pipeline: feature extraction + envelope emission.

Pipeline for one observation window:
  1. Accept raw RF metadata (Wi-Fi monitor-mode / BLE scan / SDR-derived). No
     payload.
  2. Run the RF scrub INSIDE the fail-closed privacy gate, so identifier
     anonymization (HMAC + rotating salt) and the audit event happen as one
     operation. If the gate raises, the window is dropped and NO envelope exists.
  3. Derive aggregate RF features from the scrubbed (identifier-free) window.
  4. Build and return a FeatureEnvelope on the features.rf contract, stamped with
     the PrivacyBlock the gate returned.

statistics note: rssi_mean/variance use population statistics over the window;
an empty window yields a sparse envelope (emitter_count == 0) which the RF mapper
treats as ignorance (vacuous), per the missing/sparse-RF decision.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime

from kanatir.pipelines.common.envelope import (
    FeatureEnvelope,
    Modality,
    RFFeatures,
)
from kanatir.pipelines.common.privacy_gate import ScrubResult, run_privacy_gate
from kanatir.pipelines.rap.scrub import (
    RawRFObservation,
    RotatingSalt,
    ScrubbedRFWindow,
    scrub_rf_window,
)

FEATURES_RF_TOPIC = "features.rf"


def _derive_features(
    win: ScrubbedRFWindow, window_s: float, band: str
) -> RFFeatures:
    n = len(win.hashed_ids)
    distinct = len(set(win.hashed_ids))
    rssi_mean = statistics.fmean(win.rssis) if win.rssis else 0.0
    rssi_var = statistics.pvariance(win.rssis) if len(win.rssis) > 1 else 0.0
    span = window_s if window_s > 0 else 1.0
    return RFFeatures(
        window_s=window_s,
        band=band,
        emitter_count=distinct,
        new_emitter_rate=win.n_new / span,
        unknown_emitter_rate=win.n_unknown / span,
        rssi_mean=rssi_mean,
        rssi_variance=rssi_var,
        channel_occupancy=min(1.0, max(0.0, (win.n_probe + win.n_burst) / max(1, n))),
        probe_density=win.n_probe / span,
        burst_rate=win.n_burst / span,
    )


def build_rf_envelope(
    *,
    observations: list[RawRFObservation],
    sensor_id: str,
    band: str,
    window_s: float,
    salt: RotatingSalt,
    capture_ts: datetime | None = None,
    actor: str = "rap",
) -> FeatureEnvelope:
    """
    Run a raw RF window through scrub+gate+extraction and return an RF envelope.

    Fail-closed: if the privacy gate raises (scrub or audit failure), the
    exception propagates and the caller must NOT publish — there is no envelope.
    """
    captured: dict[str, ScrubbedRFWindow] = {}

    def _scrub() -> ScrubResult:
        # Anonymize + minimize INSIDE the gate. The hashed-id set and salt epoch
        # are the audit-relevant facts; the raw ids are gone after this returns.
        win = scrub_rf_window(observations, salt)
        captured["win"] = win
        # payload_to_hash is a post-scrub, identifier-free integrity payload:
        # the sorted hashed-id set for this window. Never raw ids.
        integrity_payload = ",".join(sorted(win.hashed_ids)).encode("utf-8")
        actions = [
            f"rf_id_hash:{win.n_ids_hashed}",
            f"salt_epoch:{win.salt_epoch}",
            "rf_min:derived_only",
        ]
        return ScrubResult(
            pii_present=win.n_ids_hashed > 0,
            pii_scrubbed=win.n_ids_hashed > 0,
            actions=actions,
            payload_to_hash=integrity_payload or None,
        )

    privacy = run_privacy_gate(
        actor=actor,
        sensor_id=sensor_id,
        data_modality=str(Modality.RF),
        scrub=_scrub,
        event_type="PII_SCRUB",
    )

    win = captured["win"]
    feats = _derive_features(win, window_s=window_s, band=band)

    return FeatureEnvelope(
        modality=Modality.RF,
        source_sensor_id=sensor_id,
        capture_ts=capture_ts or datetime.now(UTC),
        privacy=privacy,
        features=feats,
    )
