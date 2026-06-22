# Sprint Completion Record — Sprint 7-8 (Weeks 13-16)

**Project:** Urban Intelligence & Anomaly Detection Platform (UIADP) — *Project Sentinel*
**Repository:** Kanatir-Kinetics-Backup
**Block:** Sprint 7-8 · Anomaly Detection Engine (ADE)
**TRL Batch:** TRL 1-3 (active)
**Status:** ✅ GATE PASSED — live end-to-end scoring demonstrated, `fused.objects` → `anomalies.raw`

---

## 1. Objective

Stand up the Anomaly Detection Engine: consume versioned `FusedObject` records
from `fused.objects` (fused_schema_version 1.0.0), extract a stable numeric
feature vector, score through a detector ensemble and an adaptive baseline, and
publish versioned `AnomalyRecord` records (anomaly_schema_version 1.0.0) to
`anomalies.raw` — preserving the full PGC audit lineage from raw capture through
fusion through anomaly, and keeping `kanatir.core` importable with no ML deps.

This is the M4 gate named in the Sprint 5-6 carry-forward:
`fused.objects` → `anomalies.raw`.

---

## 2. Key Decisions

- **Dependency isolation: `[ade]` extra, `kanatir.core.ade` package.** ADE was
  already scaffolded as `kanatir.core.ade` in the committed package list.
  ML deps (`scikit-learn`, `torch`, `numpy`) live behind a new `[ade]` optional-
  dependency extra — same discipline as `[pipelines]`. All ML imports are lazy
  (inside detector methods, never at module top level), so `import kanatir.core.ade`
  succeeds on a core-only install. Proven by `test_ade_contract_modules_import_without_ml`.

- **Single-path `is_anomaly`, baseline-driven (TRL-6 recheck).** `conflict_k`
  is a first-class tracked *input* to the `AdaptiveBaseline`, not a hardcoded
  override. Real sensor disagreement is dominated by miscalibration / clock skew
  / occlusion — a fixed conflict threshold would false-alarm on ordinary fusion
  messiness in a relevant operational environment (e.g. it would have fired on
  the Sprint 5-6 `capture_ts`/`ingest_ts` timing bug). `conflict_k` is still
  surfaced as a first-class field on every `AnomalyRecord` for explainability
  and downstream use. The two-path override was considered and rejected on
  TRL-6 grounds.

- **`AdaptiveBaseline`: rolling z-score, warmup fallback, confirmed-normal-only
  foldback.** Ported clean (reimplemented, not imported) from the parallel
  research-spike prototype logic. Only confirmed-normal readings fold back into
  the rolling statistics, so a sustained anomaly cannot drift the baseline into
  hiding itself. `baseline_state` (WARMUP / ACTIVE) is a first-class field on
  every `AnomalyRecord` — a WARMUP "no anomaly" is distinguishable from an
  ACTIVE "no anomaly". `ADE_Z_THRESHOLD` is configurable; flagged as
  calibration-pending until real-media score distributions exist.

- **IsolationForest: live-gate detector, unfitted at M4 gate (deliberate).**
  IsoForest is present, protocol-conformant, and unit-tested (outlier scores
  higher than inlier on real-shaped data). It is NOT fitted at this gate because
  fitting on synthetic media (ignorance-collapsed FusedObjects, `conflict_k=0.0`)
  would produce a number that cannot be honestly defended. The ensemble's
  `ready_detectors` check skips it automatically; the gate runs on the adaptive
  baseline + `conflict_k` as the tracked scalar. This is logged explicitly at
  startup (`ade.detector_state`). IsoForest goes live the moment real-media clips
  produce real feature distributions — the carried-forward real-media demo-capture
  item from Sprint 5-6.

- **LSTM Autoencoder + GNN: scaffolded behind the common interface.** Both
  implement the `Detector` protocol, `is_ready=False`, torch imported lazily.
  Not trainable at this gate (LSTM needs time-ordered sequences; GNN needs
  co-present multi-object windows). Wired so the head-to-head evaluation
  (including any TDA candidate) uses the same interface and the same feature
  stream — a slot is earned on merit, not assumed.

- **`AnomalyRecord` versioned from record #1.** `ANOMALY_SCHEMA_VERSION = "1.0.0"`.
  Downstream consumers (CSAT, next gate) gate on it. Full contributor lineage
  (`audit_event_id` per envelope) carried verbatim from `FusedObject` —
  unbroken from raw capture → envelope → fused object → anomaly. `detector_scores`
  carries per-detector raw scores and ensemble intermediates for explainability.

- **Feature vector: positionally stable by design.** `FusedObject → np.ndarray`
  extraction reads `belief.masses` by fixed key order `(UAV, GROUND, AMBIENT,
  UNKNOWN)` with `.get(key, 0.0)` — never iterates dict order. 8 features:
  three specific-hypothesis masses, UNKNOWN mass, `conflict_k`, confidence,
  `n_modalities`, belief entropy. Positional stability is unit-tested explicitly.

- **TDA / persistent homology: not adopted as a pillar.** Evaluated and rejected
  as a capability pillar — not a moat, scales poorly, does not strengthen the
  actual differentiators (D-S fusion, fail-closed privacy gate, append-only audit
  ledger). May compete head-to-head as one candidate detector behind the `Detector`
  interface in a later block; earns a slot only if it beats the others on the
  same data.

- **Honesty constraint maintained.** No claims of trained classifier accuracy,
  calibrated false-alarm rates, or operational anomaly detection at this gate.
  The gate demonstrates mechanics: a `FusedObject` consumed, scored, an
  `AnomalyRecord` published with lineage intact. Classifier accuracy is a
  real-media polish item, not an M4 criterion.

---

## 3. Files Committed

```
kanatir/core/ade/__init__.py           # ML-free package init; exports contract only
kanatir/core/ade/anomaly.py            # AnomalyRecord output contract (ANOMALY_SCHEMA_VERSION=1.0.0)
kanatir/core/ade/features.py           # FusedObject → stable feature vector (8 features)
kanatir/core/ade/baseline.py           # AdaptiveBaseline: rolling z-score, warmup, safe foldback
kanatir/core/ade/ensemble.py           # AnomalyEnsemble: combines ready detectors + baseline
kanatir/core/ade/detectors/__init__.py # Detector protocol (ML-free)
kanatir/core/ade/detectors/isolation_forest.py  # IsoForest detector (sklearn lazy)
kanatir/core/ade/detectors/scaffolded.py        # LSTM-AE + GNN scaffolds (torch lazy, is_ready=False)
kanatir/core/ade/__main__.py           # live Kafka consumer → score → anomalies.raw
tests/unit/test_sprint_07_08.py        # 20 unit tests (no broker / ML at test time)
```

`pyproject.toml`: added `[ade]` optional-dependency group (`scikit-learn>=1.5.0`,
`torch>=2.3.0`, `numpy>=1.26.0`). No `[tool.setuptools]` packages change —
`kanatir.core.ade` was already in the committed packages list.

---

## 4. Gate Criteria & Evidence (M4)

**Gate:** `fused.objects` → `anomalies.raw`, versioned `AnomalyRecord` published,
full audit lineage preserved, ML-free core import invariant intact.

| Criterion | Evidence | Result |
|-----------|----------|--------|
| Versioned anomaly-record contract exists | `ANOMALY_SCHEMA_VERSION=1.0.0`; downstream gates on it | ✅ |
| Consumes `fused.objects`, validates schema version | `__main__` subscribes, validates `fused_schema_version`; mismatches skipped + logged | ✅ |
| Feature vector positionally stable | `test_feature_vector_positionally_stable_regardless_of_dict_order`; fixed key order, `.get()` | ✅ |
| `conflict_k` is a tracked input, not an override | `test_ensemble_conflict_is_input_not_override`; high conflict during WARMUP does not force flag | ✅ |
| AdaptiveBaseline warmup / foldback correct | `test_baseline_does_not_absorb_sustained_anomaly`; sustained anomaly keeps flagging | ✅ |
| `baseline_state` distinguishes WARMUP from ACTIVE | Field on every `AnomalyRecord`; live run shows `baseline_state=active` | ✅ |
| IsoForest skipped cleanly when unfitted | `test_ensemble_skips_scaffolded_detectors`; `ready_detectors` check; logged at startup | ✅ |
| Scaffolded detectors wired, refuse to score | `test_scaffolded_detectors_are_not_ready_and_refuse_to_score` | ✅ |
| Audit lineage preserved raw→fused→anomaly | `test_lineage_audit_event_ids_survive_into_record`; `{audit_event_ids}` on every live record | ✅ |
| ML-free core import invariant | `test_ade_contract_modules_import_without_ml`; sklearn+torch blocked, import succeeds | ✅ |
| Unit suite, no infra/ML at test time | 20 tests pass; ruff (E,F,I,UP) clean at line-length 100 | ✅ |
| **Live end-to-end: `fused.objects` → `anomalies.raw`** | `ade.scored` lines with `baseline_state=active`, populated `audit_event_ids`, `fused_id` backref confirmed | ✅ |

**Validation in sandbox:** `20 passed`; `ruff check --select E,F,I,UP` → `All checks passed!`

**Live M4 run evidence (2026-06-22, committed repo, host arm64):**

- **ADE startup**: `ade.detector_state ready='none - baseline+conflict path only'`
  logged explicitly — cold-start policy on record, not a gap.
- **Live `ade.scored` lines** (multiple, `baseline_state=active`):
  ```
  ade.scored  anomaly_id=ea93f527-b803-48ed-a1ec-5084d3d84ec1
              anomaly_score=0.0  baseline_state=active  conflict_k=0.0
              is_anomaly=False  audit_event_ids=[1770]
              fused_id=fe0a7418-50a7-499a-8762-1db60e0d1f79
  ```
- **Multimodal FusedObject** flowing through: `is_multimodal=true`,
  `n_modalities=2`, contributors `vid-01` (video) + `file-01` (acoustic),
  `fused_schema_version=1.0.0` — M3 lineage intact into M4.
- **Privacy/audit lineage intact**: audit ledger after runs:
  **video 1652, acoustic 130**. No raw PII on the bus.
- **`anomalies.raw` topic live**: ADE published versioned `AnomalyRecord`
  records with `anomaly_schema_version=1.0.0` and full contributor lineage.

**Operational notes (not gate items):**
- `anomaly_score=0.0 / is_anomaly=False` expected on synthetic media —
  ignorance-collapsed FusedObjects with `conflict_k=0.0` produce a flat scalar
  the baseline correctly scores as normal. This exercises the *scoring mechanics*
  (the gate criterion), not classifier accuracy.
- `baseline_state=active` confirms the baseline passed its warmup window and is
  scoring confidently — the WARMUP/ACTIVE distinction is working as designed.
- IsoForest fitting and real anomaly signals are pre-submission polish items
  contingent on the real-media demo-capture carry-forward from Sprint 5-6.

---

## 5. Carried Forward / Next

- **Real-media demo capture (carried from Sprint 5-6, now also M4 polish):**
  Re-run with clips that trigger the evidence mappers (real person/car in video,
  real engine/drone/speech audio) to produce non-zero `conflict_k`, a confident
  classification, and a meaningful `anomaly_score` from a fitted IsoForest.
  Gate already passed on synthetic media.
- **IsolationForest fitting:** Fit on a real-media normal corpus once available.
  `ADE_Z_THRESHOLD` calibration-pending against a real score distribution.
- **LSTM Autoencoder:** Needs time-ordered FusedObject sequences and a fitted
  model. Scaffolded and wired; activates when real temporal data exists.
- **GNN detector:** Needs co-present multi-object windows per fusion cycle.
  Scaffolded and wired; activates when real multi-object scenes exist.
- **TDA evaluation:** May compete head-to-head as one `Detector` implementation
  on the same feature stream. Earns a slot only if it beats the others on the
  same data. Not a pillar.
- **Next gate (M5):** CSAT — `anomalies.raw` → operator-facing alert/
  situation-awareness output. `AnomalyRecord.detector_scores` and `conflict_k`
  are available as explainability inputs.
- **Partner machine (parallel setup):** Running on `~/Documents/Kanatir-Kinetics`
  (iCloud path — monitor for sync issues; recommend moving to `~/dev/` if
  instability occurs). Python 3.13, full stack including CVP/APP confirmed
  working after upgrade from 3.11. Excluded from gate evidence per protocol.

---

*Record generated at the close of Sprint 7-8 for retraceability and TRL 3
validation evidence. M4 gate passed: live `fused.objects` → `anomalies.raw`
end-to-end scoring demonstrated on the committed repo, full audit lineage
preserved.*
