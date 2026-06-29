"""
Tests for rf_to_mass (TRL 3->4 block) — the 8 required conservatism proofs.

Every claim made about the RF mapper's conservatism is backed here by an
assertion. Test numbers match the approved heuristic proposal:

  1. No-UAV invariant (property/fuzz over the input space)
  2. Valid BBA after normalize_bba, for all inputs
  3. Missing RF -> vacuous (mapper not invoked path + non-RF payload path)
  4. Sparse RF -> vacuous
  5. Ceiling honored: m(GROUND)+m(AMBIENT) <= W_RF
  6. Agreement required: single elevated feature -> GROUND below cap
  7. Directionality: low-steady -> AMBIENT>GROUND ; high-churn -> GROUND>AMBIENT
  8. Monotonicity: raising unknown_emitter_rate never raises discriminating mass

Plus: RF is registered in MAPPERS and dispatched by envelope_to_mass.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

from kanatir.core.msfe.dempster_shafer import normalize_bba
from kanatir.core.msfe.evidence import (
    MAPPERS,
    envelope_to_mass,
    rf_to_mass,
)
from kanatir.core.msfe.fused import UNKNOWN
from kanatir.core.msfe.rf_config import DEFAULT_RF_CONFIG
from kanatir.pipelines.common.envelope import (
    AcousticFeatures,
    FeatureEnvelope,
    Modality,
    PrivacyBlock,
    RFFeatures,
)

W_RF = DEFAULT_RF_CONFIG.w_rf
CAP = DEFAULT_RF_CONFIG.single_feature_ground_cap


def _privacy() -> PrivacyBlock:
    return PrivacyBlock(gate_passed=True, audit_event_id=1)


def _env(**over) -> FeatureEnvelope:
    base = dict(
        window_s=2.0, band="wifi_2g4", emitter_count=5, new_emitter_rate=0.5,
        unknown_emitter_rate=0.0, rssi_mean=-65.0, rssi_variance=12.0,
        channel_occupancy=0.3, probe_density=1.2, burst_rate=0.8,
    )
    base.update(over)
    return FeatureEnvelope(
        modality=Modality.RF, source_sensor_id="rap-01",
        capture_ts=datetime(2026, 6, 24, 12, 0, tzinfo=UTC),
        privacy=_privacy(), features=RFFeatures(**base),
    )


# --- 1. No-UAV invariant -----------------------------------------------------

def test_1_no_uav_mass_ever_fuzz():
    rng = random.Random(20260624)
    for _ in range(5000):
        env = _env(
            emitter_count=rng.randint(0, 500),
            new_emitter_rate=rng.uniform(0, 50),
            unknown_emitter_rate=rng.uniform(0, 50),
            rssi_mean=rng.uniform(-100, -20),
            rssi_variance=rng.uniform(0, 500),
            channel_occupancy=rng.uniform(0, 1),
            probe_density=rng.uniform(0, 50),
            burst_rate=rng.uniform(0, 50),
        )
        raw = rf_to_mass(env)
        assert "UAV" not in raw, f"RF emitted UAV mass: {raw}"
        # And it survives normalization into the fused frame.
        norm = normalize_bba(raw)
        assert norm.get("UAV", 0.0) == 0.0


# --- 2. Valid BBA ------------------------------------------------------------

def test_2_valid_bba_after_normalize_fuzz():
    rng = random.Random(1)
    for _ in range(5000):
        env = _env(
            emitter_count=rng.randint(0, 500),
            new_emitter_rate=rng.uniform(0, 50),
            unknown_emitter_rate=rng.uniform(0, 50),
            rssi_variance=rng.uniform(0, 500),
            channel_occupancy=rng.uniform(0, 1),
            probe_density=rng.uniform(0, 50),
            burst_rate=rng.uniform(0, 50),
        )
        norm = normalize_bba(rf_to_mass(env))
        assert abs(sum(norm.values()) - 1.0) < 1e-9
        for k, v in norm.items():
            assert 0.0 <= v <= 1.0
            assert k in ("UAV", "GROUND", "AMBIENT", UNKNOWN)


# --- 3. Missing RF -> vacuous ------------------------------------------------

def test_3_non_rf_payload_is_vacuous():
    # A non-RF payload handed to rf_to_mass -> vacuous (defensive).
    env = FeatureEnvelope(
        modality=Modality.ACOUSTIC, source_sensor_id="app-01",
        capture_ts=datetime(2026, 6, 24, 12, 0, tzinfo=UTC),
        privacy=_privacy(),
        features=AcousticFeatures(sample_rate=16000, window_s=1.0,
                                  yamnet_top=[("Wind", 0.4)], mfcc_mean=[0.0]),
    )
    assert rf_to_mass(env) == {UNKNOWN: 1.0}


# --- 4. Sparse RF -> vacuous -------------------------------------------------

def test_4_sparse_rf_is_vacuous():
    env = _env(emitter_count=0, new_emitter_rate=0.0, probe_density=0.0,
               burst_rate=0.0)
    assert rf_to_mass(env) == {UNKNOWN: 1.0}


# --- 5. Ceiling honored ------------------------------------------------------

def test_5_mass_ceiling_honored_fuzz():
    rng = random.Random(2)
    for _ in range(5000):
        env = _env(
            emitter_count=rng.randint(1, 500),
            new_emitter_rate=rng.uniform(0, 50),
            unknown_emitter_rate=rng.uniform(0, 50),
            rssi_variance=rng.uniform(0, 500),
            channel_occupancy=rng.uniform(0, 1),
            probe_density=rng.uniform(0, 50),
            burst_rate=rng.uniform(0, 50),
        )
        raw = rf_to_mass(env)
        discriminating = raw.get("GROUND", 0.0) + raw.get("AMBIENT", 0.0)
        assert discriminating <= W_RF + 1e-9, f"{discriminating} > W_RF={W_RF}"


# --- 6. Agreement required ---------------------------------------------------

def test_6_single_feature_spike_capped():
    # Only emitter_count elevated; all other activity drivers at/below low.
    # emitter_count high but new_emitter_rate / probe_density / burst_rate ~0.
    env = _env(
        emitter_count=500, new_emitter_rate=0.0, probe_density=0.0,
        burst_rate=0.0, unknown_emitter_rate=0.0,
    )
    raw = rf_to_mass(env)
    assert raw.get("GROUND", 0.0) <= CAP + 1e-9, raw


def test_6b_multi_feature_agreement_lifts_ground_above_cap():
    # Multiple activity drivers elevated -> GROUND can exceed the single-feature
    # cap (proves the cap is specifically a single-feature guard, not a global
    # clamp that would make agreement meaningless).
    env = _env(
        emitter_count=200, new_emitter_rate=10.0, probe_density=20.0,
        burst_rate=20.0, unknown_emitter_rate=0.0,
        rssi_variance=200.0, channel_occupancy=0.9,
    )
    raw = rf_to_mass(env)
    assert raw.get("GROUND", 0.0) > CAP, raw


# --- 7. Directionality -------------------------------------------------------

def test_7_low_steady_favors_ambient():
    # Low churn, low variance, low occupancy, modest emitter count.
    env = _env(
        emitter_count=4, new_emitter_rate=0.0, probe_density=0.0,
        burst_rate=0.0, unknown_emitter_rate=0.0,
        rssi_variance=1.0, channel_occupancy=0.05,
    )
    raw = rf_to_mass(env)
    assert raw.get("AMBIENT", 0.0) > raw.get("GROUND", 0.0), raw


def test_7_high_churn_favors_ground():
    # High density and churn -> GROUND should dominate AMBIENT.
    env = _env(
        emitter_count=200, new_emitter_rate=10.0, probe_density=20.0,
        burst_rate=20.0, unknown_emitter_rate=0.0,
        rssi_variance=200.0, channel_occupancy=0.9,
    )
    raw = rf_to_mass(env)
    assert raw.get("GROUND", 0.0) > raw.get("AMBIENT", 0.0), raw


# --- 8. Monotonicity in unknown_emitter_rate ---------------------------------

def test_8_unknown_rate_never_increases_discriminating_mass():
    prev = None
    for unk in [0.0, 0.5, 1.0, 1.5, 2.0, 5.0, 20.0]:
        env = _env(
            emitter_count=200, new_emitter_rate=10.0, probe_density=20.0,
            burst_rate=20.0, unknown_emitter_rate=unk,
            rssi_variance=200.0, channel_occupancy=0.9,
        )
        raw = rf_to_mass(env)
        disc = raw.get("GROUND", 0.0) + raw.get("AMBIENT", 0.0)
        if prev is not None:
            assert disc <= prev + 1e-9, f"unknown={unk} raised disc {prev}->{disc}"
        prev = disc


# --- registration ------------------------------------------------------------

def test_rf_registered_and_dispatched():
    assert Modality.RF in MAPPERS
    env = _env()
    # envelope_to_mass dispatches to rf_to_mass for RF modality.
    assert envelope_to_mass(env) == rf_to_mass(env)
