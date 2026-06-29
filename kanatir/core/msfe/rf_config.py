"""
RF BPA mapper configuration.

PROVISIONAL VALUES. The per-feature low/high normalization bounds below are
seed defaults chosen for lab bring-up, NOT calibrated against a real RF normal
corpus. They MUST be re-derived once a representative RF normal corpus exists
(parallel to the ADE IsoForest cold-start policy: scores fit on synthetic or
ignorance-collapsed data are indefensible). Every value here is overridable via
environment variable so calibration does not require a code change.

Env override convention: KAN_RF_<UPPER_FIELD>  (floats), e.g.
    KAN_RF_W_RF=0.4
    KAN_RF_EMITTER_COUNT_LOW=3
    KAN_RF_EMITTER_COUNT_HIGH=40

Design guardrails (locked by decision, not just config):
  - W_RF caps the total discriminating mass RF can emit. RF residual always
    falls on UNKNOWN, so RF alone can never push ignorance below (1 - W_RF).
  - SINGLE_FEATURE_GROUND_CAP bounds GROUND mass when only one activity driver
    is elevated (no multi-feature agreement) — prevents one noisy spike from
    asserting presence.
  - RF NEVER emits UAV mass. That is enforced structurally in the mapper (UAV is
    never a key it writes), not by a threshold here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _f(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class RFMapperConfig:
    # --- reliability / guardrails ---
    # Per-modality reliability weight AND total mass ceiling for RF. Lower than
    # acoustic's 0.85 because device-presence is weak evidence of object CLASS.
    w_rf: float = 0.5
    # Max GROUND mass permitted when agreement is at its single-feature floor.
    single_feature_ground_cap: float = 0.1
    # Below this emitter_count the window is treated as sparse -> vacuous.
    emitter_count_sparse_floor: float = 1.0

    # --- activity drivers: (low, high) normalization bounds ---
    emitter_count_low: float = 2.0
    emitter_count_high: float = 30.0
    new_emitter_rate_low: float = 0.05
    new_emitter_rate_high: float = 2.0
    probe_density_low: float = 0.1
    probe_density_high: float = 5.0
    burst_rate_low: float = 0.1
    burst_rate_high: float = 5.0

    # --- stability drivers: (low, high) bounds (used inverted) ---
    rssi_variance_low: float = 1.0
    rssi_variance_high: float = 50.0
    channel_occupancy_low: float = 0.05
    channel_occupancy_high: float = 0.6

    # --- uncertainty amplifier ---
    # High unknown_emitter_rate raises the ignorance floor; it never adds
    # discriminating mass. Normalized against this bound.
    unknown_emitter_rate_high: float = 2.0

    @classmethod
    def from_env(cls) -> RFMapperConfig:
        return cls(
            w_rf=_f("KAN_RF_W_RF", 0.5),
            single_feature_ground_cap=_f("KAN_RF_SINGLE_FEATURE_GROUND_CAP", 0.1),
            emitter_count_sparse_floor=_f("KAN_RF_EMITTER_COUNT_SPARSE_FLOOR", 1.0),
            emitter_count_low=_f("KAN_RF_EMITTER_COUNT_LOW", 2.0),
            emitter_count_high=_f("KAN_RF_EMITTER_COUNT_HIGH", 30.0),
            new_emitter_rate_low=_f("KAN_RF_NEW_EMITTER_RATE_LOW", 0.05),
            new_emitter_rate_high=_f("KAN_RF_NEW_EMITTER_RATE_HIGH", 2.0),
            probe_density_low=_f("KAN_RF_PROBE_DENSITY_LOW", 0.1),
            probe_density_high=_f("KAN_RF_PROBE_DENSITY_HIGH", 5.0),
            burst_rate_low=_f("KAN_RF_BURST_RATE_LOW", 0.1),
            burst_rate_high=_f("KAN_RF_BURST_RATE_HIGH", 5.0),
            rssi_variance_low=_f("KAN_RF_RSSI_VARIANCE_LOW", 1.0),
            rssi_variance_high=_f("KAN_RF_RSSI_VARIANCE_HIGH", 50.0),
            channel_occupancy_low=_f("KAN_RF_CHANNEL_OCCUPANCY_LOW", 0.05),
            channel_occupancy_high=_f("KAN_RF_CHANNEL_OCCUPANCY_HIGH", 0.6),
            unknown_emitter_rate_high=_f("KAN_RF_UNKNOWN_EMITTER_RATE_HIGH", 2.0),
        )


DEFAULT_RF_CONFIG = RFMapperConfig()
