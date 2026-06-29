# Completion Record — RF / Wi-Fi-BLE Device-Presence Integration (TRL 3 → 4 maturation block)

**Block ID:** M8-RF (TRL 3 → 4 component integration)
**Prior sealed state:** M7 — TRL 3 close gate, committed `df82e13`
**Repo:** `Olatomiwaola/Kanatir-Kinetics-Backup`
**Status:** COMPLETE — all decisions confirmed, all claims test/log/artifact-backed.

> This record is the authoritative session handoff. It documents new, separately
> gated TRL 3 → TRL 4 work. It does **not** modify, rewrite, or re-gate any sealed
> M7 evidence or `docs/validation/` record. M7 remains sealed at `df82e13`.

---

## 1. Objective

Add passive RF / Wi-Fi-BLE device-presence sensing as a third heterogeneous
source, integrated through the existing modality-agnostic `FeatureEnvelope`
contract, maturing FusionGuard from a two-source proof of concept toward a
multi-source, lab-validated TRL 4 component.

**Claim language (honesty constraint, enforced):** This block provides
**TRL 3 → 4 maturation evidence** and a **TRL 4-track component integration
validated in lab**. It does **not** claim full TRL 4 achievement on its own, and
makes **no** mature RF threat-classification or drone-RF-detection claim.

**RF scope (as approved):** passive device-presence and emission-anomaly sensing
only; no payload capture; no drone RF control-link classification; no
SIGINT-grade detection claim; RF data treated as civilian-derived sensitive data.

---

## 2. Decisions confirmed before code (decisions-before-code)

**D1 — Frame of discernment.** Frame Θ = {UAV, GROUND, AMBIENT} kept **unchanged**.
No fusion-core frame change in this block. RF contributes mass to GROUND, AMBIENT,
or ignorance (UNKNOWN) only, and **never** to UAV — enforced structurally (UAV is
never a key the RF mapper emits), not by tuning. An emitter-anomaly hypothesis was
explicitly **rejected** for this block: an anomaly is an ADE/baseline statement,
not a fusion hypothesis, and adding a focal element is a fusion-core change with no
proof-of-concept payoff here.

**D2 — RF FeatureEnvelope schema.** New `RFFeatures` payload on the existing
discriminated union, topic `features.rf`. No repo-wide `SCHEMA_VERSION` bump.

> RF is added as an additive discriminated-union member under the pre-existing
> `Modality.RF` / `features.rf` contract; existing video/acoustic envelopes remain
> schema-compatible. No repo-wide schema bump was required.

`emission_anomaly_score` deliberately **excluded** — ADE owns anomaly scoring; the
RF envelope carries derived observations only. `band` kept a **free string** for
v1.0.0 (wifi_2g4 / wifi_5g / ble / sdr_900 / sdr_2g4 / future), not an enum, to
avoid premature schema churn before RF capture modes stabilize. Windows use
`ingest_ts` / wall-clock bus-arrival semantics, consistent with the existing
correlation decision.

**D3 — Privacy & audit.** No payload capture; no raw identifiers published,
logged, or persisted; identifiers HMAC-SHA256-hashed under a **rotating salt**
(default 15 min, env-overridable `KAN_RF_SALT_ROTATE_S`); derived-only retention;
fail-closed privacy gate; PGC append-only audit for RF privacy actions. The
identifier-anonymization HMAC (keyed, rotated, non-linkable across epochs) is kept
deliberately distinct from the PGC SHA-256 audit-integrity hash (unkeyed,
tamper-evidence).

**D4 — Missing-RF semantics.** Absent/dropped RF → vacuous BBA, all mass on
UNKNOWN (Θ). Never negative or AMBIENT evidence by default. This is the identity
under Dempster's rule, so RF dropout is graceful with no special-casing.

---

## 3. What was built (new code surface)

All integration achieved with **zero changes to the fusion core**
(`dempster_shafer.py`) and the fused-object contract (`fused.py`) — verified by
grep: zero `rf` references in either file. The sealed video/acoustic mappers in
`evidence.py` are unchanged (acoustic 0.85 discount intact).

| File | Change |
|---|---|
| `kanatir/pipelines/common/envelope.py` | **+`RFFeatures`** model; appended to `FeaturePayload` union. Outer envelope, `PrivacyBlock`, validators untouched. |
| `kanatir/core/msfe/evidence.py` | **+`rf_to_mass`** mapper; registered `Modality.RF` in `MAPPERS`. Existing mappers unchanged. |
| `kanatir/core/msfe/rf_config.py` | **new** — env-overridable, **provisional** RF mapper thresholds + guardrails (`W_RF`, single-feature cap, normalization bounds). |
| `kanatir/pipelines/rap/scrub.py` | **new** — `RotatingSalt`, HMAC identifier hashing, window minimization. No payload, no raw-id retention. |
| `kanatir/pipelines/rap/features.py` | **new** — RAP feature builder; runs scrub inside the fail-closed privacy gate; emits `features.rf` envelope. |
| `scripts/validate_rf_trimodal.py` | **new** — tri-modal validation harness. |
| `tests/unit/test_rf_envelope.py` | **new** — 10 tests. |
| `tests/unit/test_rf_mapper.py` | **new** — 15 tests (incl. the 8 required conservatism proofs). |
| `tests/unit/test_rf_privacy_audit.py` | **new** — 10 tests. |

**Topics:** none. `features.rf` (MIN_30) and `raw.rf.iq` (MIN_5) were already
provisioned in the sealed UDIH topic registry. UDIH unchanged.

---

## 4. RF BPA heuristic (conservative, as approved)

`rf_to_mass` emits mass over {GROUND, AMBIENT} only; residual → UNKNOWN via the
sealed `normalize_bba`. Drivers: **activity** (emitter density/churn → GROUND),
**stability** (low, steady presence → AMBIENT), **agreement** (fraction of
activity drivers elevated — suppresses single-feature spikes), **certainty**
(high `unknown_emitter_rate` raises ignorance, never adds discriminating mass).

Guardrails: `W_RF = 0.5` doubles as the total discriminating-mass ceiling (RF
alone can never push ignorance below 0.5); single-feature GROUND cap = 0.1;
sparse `emitter_count` → vacuous. **All thresholds are provisional** (seed lab
bring-up values, not calibrated against a real RF normal corpus) and
env-overridable; they must be re-derived once a representative RF normal corpus
exists — the same cold-start discipline as the ADE IsoForest policy.

---

## 5. Validation evidence (every claim backed)

**Unit tests — 35 passed:**
- Envelope (10): RF validates + round-trips; schema_version unchanged; no
  `emission_anomaly_score`; common band strings accepted; video/acoustic still
  validate; modality/payload mismatch rejected; field-range constraints.
- Mapper (15, incl. 8 required proofs): (1) no-UAV invariant over 5k fuzz;
  (2) valid BBA after normalize over 5k fuzz; (3) non-RF → vacuous; (4) sparse →
  vacuous; (5) mass ceiling ≤ W_RF over 5k fuzz; (6) single-feature spike capped
  + (6b) multi-feature agreement lifts above cap; (7) directionality both ways;
  (8) monotonic in `unknown_emitter_rate`; + registration/dispatch.
- Privacy/audit (10): no raw ids in envelope; no payload field; PrivacyBlock
  passed + audit-linked; audit event recorded; HMAC salt-dependent & ≠ plain
  SHA-256; salt rotation breaks cross-epoch linkability; default 15-min interval;
  env override; fail-closed on scrub failure (no audit written); fail-closed on
  audit failure.

**Heavy fuzz (20k inputs):** no-UAV invariant holds at the mapper **and after
fusion**; worst-case discriminating mass 0.4999 (≤ W_RF = 0.5).

**Tri-modal harness (`validate_rf_trimodal.py`) — ALL CHECKS PASSED:**
- V1 RF envelope published on `features.rf` (emitter_count=5, gate_passed,
  audit-linked).
- V2 tri-modal `FusedObject`: n_modalities=3, multimodal=True, contributors
  {video, acoustic, rf}, class=GROUND conf=0.856, K=0.022 — three sources
  corroborate.
- V3 graceful RF dropout: bimodal fused object coherent; missing-RF belief drift
  = 0.00e+00 (exact vacuous identity).
- V4 RF privacy/audit applied: gate_passed, audit linked, no raw identifiers in
  envelope JSON, derived-only (no `hashed_ids` retained).

**Lineage:** RF `Contributor` (envelope_id, modality, source_sensor_id,
capture_ts, audit_event_id) is built by the same assembly path as video/acoustic,
preserving RF lineage through FusedObject → (downstream AnomalyRecord →
TriagedAlert → ExplainedAlert, which consume FusedObject unchanged).

---

## 6. Constraints honored

- Sealed M7 evidence/docs: **not modified or re-gated.** M7 sealed at `df82e13`.
- No mature RF threat classification / drone RF detection claimed.
- Fusion core **not altered** — no test showed the pre-provisioned RF path
  cannot work; the additive path was sufficient.
- Existing contracts preserved; RF added additively, no schema bump.
- decisions-before-code followed; every new claim backed by a test, log, or
  validation artifact.

## 7. Known limitations / carry-forward

- RF mapper thresholds are **provisional**; require calibration against a real RF
  normal corpus before any quantitative separability claim.
- M7 acoustic siren-vs-ambient separability remains tracked and **out of scope**
  for this block (unchanged).
- The RAP runtime here is validated offline (replay) and via unit/integration
  tests; live-capture bring-up on the Jetson edge target is TRL 4 follow-on work.
- TRL 4-6 architecture design remains a separate, deferred deliverable.

---

**Prior state referenced:** M7 `df82e13`.
**This block closes as:** TRL 3 → 4 maturation evidence; RF device-presence
integrated as a third heterogeneous source via the pre-existing additive
`features.rf` contract, with conservative no-UAV BPA mapping, fail-closed
privacy/audit on civilian-derived RF data, and validated tri-modal fusion with
graceful dropout.
