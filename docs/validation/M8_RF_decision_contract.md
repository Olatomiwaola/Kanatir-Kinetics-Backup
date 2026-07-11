# Decision Contract — M8-RF: RF / Wi-Fi-BLE Device-Presence Integration

**Block ID:** M8-RF (TRL 3 → 4 maturation)
**Prior sealed state:** M7 — TRL 3 close gate, commit `df82e13`
**Repo:** `Olatomiwaola/Kanatir-Kinetics-Backup`
**Machine of record:** `olaberry`
**Status:** DECISIONS CONFIRMED — signed off before implementation began.

> **PROVENANCE NOTE — read this first.**
> This document was **written retroactively**, after the M8-RF block was
> implemented and committed at `172ba0c`. It is **not** a contemporaneous
> artifact.
>
> The decisions it records **were** made and explicitly signed off *before* any
> code was written — that sequence is real, and the decisions-before-code
> discipline was followed. What did not happen is the decisions being *committed
> to disk* as a standalone artifact before implementation. They existed only in
> the working session, and were first written down after the fact inside
> §2 of `M8_RF_integration_completion.md`.
>
> This file extracts that decision set into its own record so the pair reads
> **decision → evidence**. It adds nothing that was not agreed at the time and
> it silently corrects nothing. Anyone auditing the trail should treat this as a
> faithful reconstruction, not as proof that a written contract preceded the
> code. For all subsequent blocks the decision contract is to be written and
> committed **before** implementation; see `DECISION_CONTRACT_TEMPLATE.md`.

---

## 1. Objective

Add passive RF / Wi-Fi-BLE device-presence sensing as a **third heterogeneous
source**, integrated through the existing modality-agnostic `FeatureEnvelope`
contract, to mature FusionGuard from a two-source proof of concept toward a
multi-source, lab-validated TRL 4 component.

This block begins **new, separately gated TRL 3 → TRL 4 work**. It does not
modify, rewrite, or re-gate any sealed M7 evidence or `docs/validation/` record.

---

## 2. Scope boundaries (binding)

**In scope:**
- Passive device-presence and emission-anomaly sensing.
- Locally captured RF metadata: Wi-Fi monitor mode, BLE scans, low-cost
  SDR-derived observations.

**Explicitly out of scope — no claim of any kind may be made in these areas:**
- No payload capture.
- No drone RF control-link classification.
- No SIGINT-grade detection claim.
- No mature RF threat classification.

**Data handling:** RF data is treated as **civilian-derived sensitive data**
throughout.

**Claim language (honesty constraint):** this block closes as
**"TRL 3 → 4 maturation evidence"** / **"TRL 4-track component integration
validated in lab."** It does **not** claim full TRL 4 achievement on its own.

---

## 3. The four decisions (confirmed before code)

### D1 — Frame of discernment

**Decision: keep Θ = {UAV, GROUND, AMBIENT} unchanged. No frame extension. No
fusion-core change approved for this block.**

RF device-presence evidence maps onto the existing hypotheses without extension:

- Dense, mobile, probing emitter signatures → evidence for **GROUND** (people
  and vehicles carry phones, wearables, vehicle BLE).
- Low, stable emitter activity with steady RSSI → evidence for **AMBIENT**.
- RF gives **no** discriminating mass toward **UAV**. Drone RF control-link
  classification is out of scope, so RF must not support UAV; that mass stays in
  ignorance (Θ).

**An `EMITTER_ANOMALY` hypothesis was explicitly considered and rejected.** An
anomaly is a statement about a *baseline* (ADE's job), not about which physical
entity is present (the fusion frame's job). Adding a focal element is a
fusion-core change that would force re-derivation of every existing mass
assignment and BPA normalization, and would put pressure on the sealed M7 fusion
math — a real cost with no proof-of-concept payoff at this stage.

If lab data later shows a genuine need for an emitter-anomaly hypothesis, it
becomes its **own explicitly logged, separately gated fusion-core change** — not
something introduced inside this block.

### D2 — RF FeatureEnvelope schema

**Decision: new `RFFeatures` payload on topic `features.rf`. No repo-wide
`SCHEMA_VERSION` bump.**

Rationale for no bump: `Modality.RF` and `features.rf` were **already
provisioned** in the sealed architecture. Adding a member to the discriminated
union is **additive, not breaking** — existing video/acoustic envelopes validate
identically, and a consumer that does not understand `rf` simply never receives
`rf` envelopes (MSFE branches on modality).

**Required wording in the completion record (condition of approval):**

> "RF is added as an additive discriminated-union member under the pre-existing
> `Modality.RF`/`features.rf` contract; existing video/acoustic envelopes remain
> schema-compatible. No repo-wide schema bump was required."

**Approved field set** (derived observations only):

| field | type | unit / range |
|---|---|---|
| `window_s` | float > 0 | observation window (s) |
| `band` | str | **free string** for v1.0.0 |
| `emitter_count` | int ≥ 0 | distinct hashed emitters in window |
| `new_emitter_rate` | float ≥ 0 | new emitters / s |
| `unknown_emitter_rate` | float ≥ 0 | not-in-known-set / s |
| `rssi_mean` | float | dBm |
| `rssi_variance` | float ≥ 0 | dBm² |
| `channel_occupancy` | float | 0.0–1.0 |
| `probe_density` | float ≥ 0 | probe requests / s |
| `burst_rate` | float ≥ 0 | bursts / s |

**`emission_anomaly_score` is EXCLUDED from v1.0.0.** ADE owns anomaly scoring.
The RF envelope carries **derived observations, not an anomaly verdict**.
Computing an anomaly score in the extractor would duplicate ADE's job and create
a parallel anomaly path.

**`band` stays a free string** (not a constrained enum) for v1.0.0. RF sources
may include `wifi_2g4`, `wifi_5g`, `ble`, `sdr_900`, `sdr_2g4`, and future
bands; constraining before capture modes are stable would force unnecessary
schema churn. Tests cover common band strings without restricting the field.

**Windowing:** RF windows use `ingest_ts` / wall-clock bus-arrival semantics,
consistent with the existing fusion correlation decision.

### D3 — Privacy and audit policy

**Decision: confirmed as follows.**

- **No payload capture**, ever — enforced at the adapter boundary, not by
  downstream trust.
- **No raw identifiers** published, logged, or persisted.
- **HMAC-hash identifiers under a rotating salt.** Salt rotation default
  **15 minutes**, stored as **config/env, not hardcoded**
  (`KAN_RF_SALT_ROTATE_S`), so it can be tightened later without a code change.
  Rotation breaks cross-epoch linkability of any single device.
- **Derived-only retention.** Only the D2 fields persist. Raw identifiers exist
  transiently in the extractor and never reach an envelope.
- **Fail-closed privacy gate.** RF routes through the existing
  `run_privacy_gate`; if scrub *or* audit fails, the window is dropped and no
  envelope exists.
- **PGC append-only audit** for RF privacy actions (scrub, salt epoch,
  minimization).

**Two hashes, deliberately distinct** (must not be conflated):
- **HMAC-SHA256 + rotating salt** = identifier **anonymization**. Keyed, rotated,
  non-linkable across epochs.
- **SHA-256 `hash_payload`** (PGC) = audit-trail **integrity** hash over a
  post-scrub payload. Unkeyed; its job is tamper-evidence, not anonymization.

### D4 — Missing-RF semantics

**Decision: confirmed. Absent or dropped RF contributes vacuous BPA mass on Θ
(`UNKNOWN`) only — `m(Θ) = 1.0`.**

Missing RF is **ignorance, not negative evidence**. It must not be treated as
AMBIENT evidence and must not reduce confidence in another modality unless
explicitly justified and logged.

The vacuous BPA is the **identity under Dempster's rule of combination**, so RF
dropout is automatically graceful with no special-casing. This is to be
*verified*, not assumed.

---

## 4. Contract alignment (read before code — no reconstruction from memory)

The sealed contracts were **read from the repo**, not reconstructed. Findings
that bind the implementation:

| Concern | Sealed reality | RF action |
|---|---|---|
| Envelope version | single repo-wide `SCHEMA_VERSION = "1.0.0"` (not per-modality) | additive union member, **no bump** |
| Modality enum | `Modality.RF = "rf"` **already present** | none |
| Payload extension | discriminated union on `modality` | add `RFFeatures`, append to union |
| Mass type | `Mass = dict[str, float]` over `{UAV, GROUND, AMBIENT, UNKNOWN}` | emit `{GROUND, AMBIENT}`; residual → `UNKNOWN` |
| Ignorance label | `UNKNOWN` (**not** the string `"Θ"`) | use `UNKNOWN` |
| Residual handling | `normalize_bba` pushes residual onto `UNKNOWN` automatically | mapper need not compute `m(UNKNOWN)` explicitly |
| Frame | `HYPOTHESES = ("UAV", "GROUND", "AMBIENT")` | **untouched** |
| Mapper registration | `MAPPERS` dict in `evidence.py` | add `Modality.RF: rf_to_mass` |
| Fusion core | `dempster_shafer.py`, `combine_all` | **untouched** |
| Fused-object contract | `fused.py`, `Contributor` lineage fields | **untouched**; RF contributors built by the same path |
| Privacy gate | `run_privacy_gate(*, actor, sensor_id, data_modality, scrub, event_type)` | use **as-is**, RF scrub closure |
| Audit | `record_event(...) -> int` | use **as-is** |
| Topics | `features.rf` (MIN_30) and `raw.rf.iq` (MIN_5) **already provisioned** | **none** |

**Consequence:** the entire RF integration is achievable with **zero fusion-core
changes**. Total new surface: `RFFeatures` + union line; `rf_to_mass` + `MAPPERS`
line; a new RAP pipeline package; RF scrub; tests; validation script; completion
record.

**House style observed:** existing mappers are terse, return a two-key dict
`{hyp: m, UNKNOWN: 1-m}`, and apply a per-modality reliability discount (acoustic
uses `0.85`). The RF mapper follows this posture rather than introducing heavier
machinery.

---

## 5. RF BPA heuristic (approved before code)

`rf_to_mass` emits mass over **`{GROUND, AMBIENT}` only**. UAV is **never a key
it writes** — the no-UAV property is **structural**, not tuned, and therefore
survives normalization (`normalize_bba` cannot introduce a label the mapper did
not emit).

**Drivers:**
- **activity** — `emitter_count`, `new_emitter_rate`, `probe_density`,
  `burst_rate` → GROUND lean.
- **stability** — low variance, low occupancy, low churn → AMBIENT lean.
- **agreement** — fraction of activity drivers above their low bound. Suppresses
  single-feature spikes.
- **certainty** — high `unknown_emitter_rate` **raises ignorance**; it never adds
  discriminating mass. *Unknown ≠ threat.*

**Approved guardrails:**

| guardrail | value | purpose |
|---|---|---|
| `W_RF` | **0.5** | per-modality reliability weight **and** total discriminating-mass ceiling. RF alone can never push ignorance below 0.5. Lower than acoustic's 0.85 because device-presence is weak evidence of object *class*. |
| single-feature GROUND cap | **0.1** | one elevated driver alone cannot assert presence |
| `emitter_count` sparse floor | below floor → `vacuous()` | sparse RF is ignorance, not AMBIENT |
| normalization bounds | per-feature low/high | **provisional**, env-overridable |

**Thresholds are PROVISIONAL.** They are seed values for lab bring-up, **not**
calibrated against a real RF normal corpus. They must be re-derived once such a
corpus exists — the same cold-start discipline as the unfitted ADE
IsolationForest. All are env-overridable so calibration requires no code change.
No quantitative separability claim may be made on provisional thresholds.

**No z-scores in the mapper** — adaptive baselining is ADE's job. The mapper
stays a stateless, defensible feature → mass transform (same separation of
concerns that keeps `emission_anomaly_score` out of the envelope).

---

## 6. Required validation (agreed acceptance criteria)

**Eight mapper tests, mandatory:**

1. **No-UAV invariant** — property/fuzz over the input space: no UAV mass at the
   mapper, and zero UAV mass after `normalize_bba`.
2. **Valid BBA** — masses ≥ 0, sum to ~1.0, for all inputs.
3. **Missing RF → vacuous** — absent RF yields the vacuous identity.
4. **Sparse RF → vacuous** — below-floor `emitter_count` → `{UNKNOWN: 1.0}`.
5. **Ceiling honored** — `m(GROUND) + m(AMBIENT) ≤ W_RF` for all inputs.
6. **Agreement required** — a single spiking feature yields GROUND below the
   single-feature cap; only multi-feature corroboration lifts GROUND.
7. **Directionality** — low-steady → AMBIENT > GROUND; high-churn → GROUND >
   AMBIENT.
8. **Monotonicity** — raising `unknown_emitter_rate` never raises discriminating
   mass.

**Block-level validation:**
- RF `FeatureEnvelope` publication confirmed on a live or replayed capture.
- Optical + acoustic + RF produce a **tri-modal** fused object when all three are
  present.
- **Graceful RF dropout**, with missing RF represented as ignorance (zero belief
  drift).
- Privacy/audit controls confirmed applied to RF-derived data.
- RF contributor lineage preserved:
  `RF FeatureEnvelope → FusedObject → AnomalyRecord → TriagedAlert → ExplainedAlert`.

---

## 7. Standing constraints for this block

- Do **not** modify or re-gate sealed M7 evidence or `docs/validation/` records.
- Do **not** claim mature RF threat classification or drone RF detection.
- Do **not** alter the fusion core unless the frame-of-discernment decision
  explicitly requires it — and a test must first prove the pre-provisioned RF
  path cannot work.
- Preserve existing contracts unless a versioned schema change is genuinely
  required.
- **Decisions-before-code.**
- Every new claim backed by a **test, log, or validation artifact**.
- Close the block with a new completion record in `docs/validation/` referencing
  M7 `df82e13` as the prior state.
- Live end-to-end evidence on `olaberry` required before the gate is declared
  passed. Partner-machine evidence is excluded.

---

## 8. Sign-off

All four decisions (D1–D4), the contract-alignment findings (§4), and the BPA
heuristic with its guardrails (§5) were reviewed and **explicitly approved before
implementation began**. Approved refinements captured above:

- `emission_anomaly_score` excluded from the RF envelope (ADE owns anomaly
  scoring).
- Salt rotation default 15 min, **configurable via env/config**, not hardcoded.
- `band` kept a free string for v1.0.0.
- `W_RF = 0.5`; single-feature GROUND cap `0.1`.
- No repo-wide `SCHEMA_VERSION` bump, on condition the rationale is documented
  verbatim in the completion record.

**Corresponding evidence:** `docs/validation/M8_RF_integration_completion.md`
(commit `172ba0c`).
