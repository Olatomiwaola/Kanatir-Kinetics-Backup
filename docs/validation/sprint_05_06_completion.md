# Sprint Completion Record — Sprint 5-6 (Weeks 9-12)

**Project:** Urban Intelligence & Anomaly Detection Platform (UIADP) — *Project Sentinel*
**Repository:** Kanatir-Kinetics-Backup
**Block:** Sprint 5-6 · Multi-Sensor Fusion Engine (MSFE)
**TRL Batch:** TRL 1-3 (active)
**Status:** ✅ GATE PASSED — live two-source heterogeneous fusion demonstrated

---

## 1. Objective

Stand up the Multi-Sensor Fusion Engine: consume versioned feature envelopes
from the two live `features.*` topics (video, acoustic), correlate co-windowed
evidence across modalities, fuse it with **Dempster-Shafer**, and publish a
versioned `FusedObject` to `fused.objects` — preserving the upstream privacy/
audit lineage and remaining modality-agnostic so rf/environmental/mobility
attach later without a rewrite.

This is the M3 gate named in the Sprint 3-4 carry-forward: `features.*` →
`fused.objects`.

---

## 2. Key Decisions

- **Execution model: host-native arm64, not Flink.** Parity with CVP/APP
  (Sprint 3-4 decision). Correlation/windowing and the D-S math are plain,
  fully unit-testable Python; the live consumer uses a Redis sliding-window
  buffer (Redis already in the stack). Flink is deferred to the Jetson/scale
  sprint alongside containerization. M3 is a correctness-and-evidence gate, not
  a throughput gate — Flink would not save the custom D-S logic, which must be
  our Python regardless.
- **Fusion math: Dempster-Shafer, not weighted Bayesian.** D-S represents
  *ignorance* explicitly (an empty video frame — the Sprint 3-4 live sample —
  contributes mass to UNKNOWN rather than a fabricated probability) and *reports
  conflict* (`conflict_k`) rather than normalizing it away (Zadeh's critique).
  High K is retained as a downstream anomaly signal for ADE.
- **MSFE lives in `kanatir/core/msfe/`, not `pipelines/`.** It is core fusion
  logic, not a sensor-edge pipeline. Package was already registered in
  `pyproject.toml`.
- **Zero ML dependency.** MSFE consumes derived features, not media, so it runs
  on the **core install** — no `[pipelines]` extra. Whole engine is CI-testable
  with no torch/TF/cv2, no broker, no Redis.
- **New output contract, versioned from object #1.** `FusedObject` mirrors the
  `FeatureEnvelope` discipline: `FUSED_SCHEMA_VERSION = "1.0.0"`; ADE (next gate)
  gates on it.
- **Frame of discernment (M3):** `{UAV, GROUND, AMBIENT}` + `UNKNOWN` (full
  frame Θ). Small and explicit for the gate; extensible behind the same
  interface.

---

## 3. Files Committed

```
kanatir/core/msfe/__init__.py
kanatir/core/msfe/fused.py            # FusedObject output contract (FUSED_SCHEMA_VERSION=1.0.0)
kanatir/core/msfe/dempster_shafer.py  # pure D-S combination: Dempster's rule, conflict K, vacuous identity
kanatir/core/msfe/evidence.py         # per-modality envelope→mass mappers (video, acoustic; MAPPERS registry)
kanatir/core/msfe/fusion.py           # spatial+temporal correlation grouping + fuse_window
kanatir/core/msfe/buffer.py           # Redis sliding-window adapter
kanatir/core/msfe/__main__.py         # live Kafka consumer → fuse → fused.objects
kanatir/core/msfe/README.md           # design rationale + host run instructions
tests/unit/test_sprint_05_06.py       # 20 unit tests (no broker / Redis / ML)
```

`pyproject.toml`: **no change required** — `kanatir.core.msfe` already in the
packages list; `redis`, `confluent-kafka`, `pydantic`, `structlog` already in
core deps; no ML dependency added.

---

## 4. Gate Criteria & Evidence (M3)

**Gate:** features fused in MSFE — `features.*` → `fused.objects`, heterogeneous
multimodal fusion, privacy lineage preserved.

| Criterion | Evidence | Result |
|-----------|----------|--------|
| Versioned fused-object contract exists | `FUSED_SCHEMA_VERSION=1.0.0`; consumers gate on it | ✅ |
| Consumes both live feature topics | `__main__` subscribes `features.video`+`features.acoustic`, validates `schema_version`; mismatches skipped + logged | ✅ |
| Dempster-Shafer combination, conflict reported | `dempster_shafer.combine_pair/all`; `BeliefMass.conflict_k` carried, never hidden; total-conflict falls back to ignorance | ✅ (unit) |
| Empty video → ignorance, not false AMBIENT | `test_empty_video_is_ignorance_not_ambient` | ✅ (unit) |
| Heterogeneous multimodal fusion | `test_fuse_multimodal_agreement_produces_valid_object` (video+acoustic → reinforced GROUND, is_multimodal) | ✅ (unit) |
| Privacy/audit lineage preserved | `Contributor.audit_event_id` carried into every FusedObject; test asserts `{13, 4}` survive fusion; no raw PII enters MSFE | ✅ (unit) |
| Modality-agnostic boundary | `MAPPERS` registry + frame discernment; rf/env/mobility attach by adding a mapper only | ✅ |
| Unit suite, no infra/ML | 21 tests pass; ruff (E,F,I,UP) clean | ✅ |
| **Live two-source heterogeneous fusion** | `msfe.fused multimodal=True n_modalities=2`, contributors `vid-01`+`file-01` | ✅ |

**Validation in sandbox:** `21 passed`; `ruff check --select E,F,I,UP` → All
checks passed.

**Live M3 run evidence (2026-06-20/21, committed repo, host arm64):**

- **Multimodal fused object** (the gate criterion): MSFE emitted
  `msfe.fused classification=UAV confidence=0.0 conflict_k=0.0 multimodal=True n_modalities=2`
  with `contributors=['vid-01','file-01',...]`,
  `fused_id=9eb218da-4845-4307-8441-90df97f42d0f`. Two heterogeneous modalities
  (CVP video `vid-01` + APP acoustic `file-01`) correlated into one fused object
  on `fused.objects`, schema `fused_schema_version=1.0.0`.
- **Both feature streams live on the bus simultaneously**: `features.video`
  (`vid-01`) and `features.acoustic` (`file-01`), both `geo.site_id="zone-A"`,
  both `schema_version=1.0.0`, confirmed via console-consumer.
- **Privacy/audit lineage intact**: every contributing envelope passed the
  fail-closed gate (`privacy.gate_passed=true`) with a PGC `audit_event_id`
  before publish. Audit ledger after runs
  (`SELECT data_modality, count(*) FROM audit_events GROUP BY data_modality`):
  **video 1202, acoustic 90**. No raw PII on the bus.
- **Correlation-key fix** (`Sprint_05_06_FIX`): MSFE correlates on `ingest_ts`,
  not `capture_ts`. File-replayed sources stamp `capture_ts` from each clip's
  internal timeline, so video and acoustic never shared a window despite being
  published together; keying on `ingest_ts` (bus arrival, always wall-clock)
  makes heterogeneous fusion work for both live and replayed sources. New test
  `test_correlate_uses_ingest_ts_not_capture_ts` added (21 total, all passing).

**Operational note (not a gate item):** `confidence=0.0 / classification=UAV`
reflects the synthetic test media — `test.avi` has no objects for YOLO and
`test.wav` does not trigger the acoustic evidence mappers, so both contribute
ignorance and belief collapses to UNKNOWN (UAV is the argmax tiebreak among
near-zero specific masses). This exercises the *fusion mechanics* (the gate
criterion: heterogeneous fusion + zero PII), not classifier accuracy. Real
object/sound clips are a pre-submission demo-polish item, not an M3 blocker.

**Run-choreography note for retraceability:** start MSFE first (it consumes
`latest`), then both producers into the live engine, with a window wide enough
to span APP's ~10s YAMNet load offset relative to CVP (`MSFE_WINDOW_S` >= the
inter-source skew). A matured window emits ~`window_s` seconds after the last
contributing envelope's `ingest_ts`.

---

## 5. Carried Forward / Next

- **Real-media demo capture (pre-submission polish, not a gate item):** re-run
  with clips that trigger the mappers (real person/car in video, real
  engine/drone/speech audio) to produce a confident classification and a
  non-zero `conflict_k` object for the IDEaS demo. Gate already passed on
  synthetic media.
- **File-source real-time pacing (carried):** CVP/APP drain files at machine
  speed, not wall-clock, and APP's YAMNet load offsets it ~10s from CVP. A
  real-time pacing option in the readers (sleep to native frame/window rate)
  would let file replay behave like a live sensor and remove the wide-window
  workaround. Pipeline-side change, not MSFE.
- **Correlation upgrade:** greedy time+site grouping today; a tracker
  (Hungarian assignment across windows) can swap behind `fusion.correlate`.
- **Geo:** first-fix-wins today; multi-fix triangulation later.
- **Acoustic reliability discount** is a flat 0.85; per-sensor reliability
  weighting is the natural next refinement (D-S handles it natively).
- **Next gate (M4):** `fused.objects` → `anomalies.raw` in ADE (Isolation Forest
  + LSTM Autoencoder + GNN). `conflict_k` is available as an input signal.

---

*Record generated at the close of Sprint 5-6 for retraceability and TRL 3
validation evidence. M3 gate passed: live two-source heterogeneous fusion
demonstrated on the committed repo.*
