# Sprint Completion Record — Sprint 3-4 (Weeks 5-8)

**Project:** Urban Intelligence & Anomaly Detection Platform (UIADP) — *Project Sentinel*
**Repository:** Kanatir-Kinetics-Backup
**Block:** Sprint 3-4 · Ingestion + Vision/Acoustic
**TRL Batch:** TRL 1-3 (active)
**Status:** 🟡 IN PROGRESS — code complete, awaiting live two-source M2 run

---

## 1. Objective

Stand up the first two real ingestion pipelines — Computer Vision (CVP) and
Acoustic (APP) — each routing through a fail-closed privacy gate that scrubs PII
and writes a PGC audit event *before* anything is buffered or published. Define
the versioned feature-envelope contract that all `features.*` topics carry, and
publish valid envelopes to `features.video` and `features.acoustic`.

---

## 2. Tasks Completed

| Task | Description | Outcome |
|------|-------------|---------|
| Task 1 | Versioned feature-envelope schema | `pydantic` model, `SCHEMA_VERSION=1.0.0`, discriminated by modality |
| Task 2 | Fail-closed privacy gate wrapping PGC `record_event` | Scrub + audit before publish; any error drops the frame |
| Task 3 | Shared envelope producer (confluent-kafka) | Idempotent, keyed by `source_sensor_id` |
| Task 4 | CVP — RTSP/ONVIF + YOLOv8-nano + ByteTrack | Face blur + plate hash at gate → `features.video` |
| Task 5 | APP — YAMNet + MFCC | Speech-presence decision at gate → `features.acoustic` |
| Task 6 | Wire PGC audit into both gates | `record_event(...)` called at the gate, fail-closed |
| Task 7 | Unit tests (no DB / no ML deps) | 8 passing: envelope contract + fail-closed gate |
| Task 8 | Integration test for live audit path | Marked `integration`, skips without `PGC_DSN` |
| Task 9 | `pyproject.toml` — `[pipelines]` extra + `common` package | Host-native arm64 ML deps isolated from core/CI |

---

## 3. Files Committed

```
kanatir/pipelines/common/__init__.py
kanatir/pipelines/common/envelope.py        # versioned FeatureEnvelope contract
kanatir/pipelines/common/privacy_gate.py     # fail-closed gate over PGC record_event
kanatir/pipelines/common/producer.py          # confluent-kafka envelope producer
kanatir/pipelines/cvp/__init__.py
kanatir/pipelines/cvp/__main__.py             # RTSP/ONVIF → YOLOv8n+ByteTrack → features.video
kanatir/pipelines/cvp/privacy.py              # face blur + plate hash scrub
kanatir/pipelines/app/__init__.py
kanatir/pipelines/app/__main__.py             # YAMNet+MFCC → features.acoustic
kanatir/pipelines/app/privacy.py              # speech-presence decision scrub
kanatir/pipelines/README.md                   # host-native run instructions
tests/unit/test_sprint_03_04.py               # 8 unit tests (CI, no DB/ML)
tests/integration/test_audit_path.py          # live Postgres audit-path test
pyproject.toml                                 # + [pipelines] extra, + common package, + integration marker
```

---

## 4. Gate Criteria & Evidence (M2)

**Gate:** ≥2 heterogeneous sources ingesting simultaneously, PII scrubbed with
zero leakage, valid feature envelopes on the bus.

| Criterion | Evidence | Result |
|-----------|----------|--------|
| Versioned envelope contract exists | `SCHEMA_VERSION=1.0.0`; consumers can gate on version | ✅ |
| Privacy gate is fail-closed | Unit tests: scrub-error and audit-error both raise, no publish | ✅ |
| Audit written before publish | Gate writes `record_event` then returns the block stamped into envelope | ✅ |
| Envelope rejects unscrubbed publish | Schema rejects `gate_passed=False` | ✅ |
| Two heterogeneous sources live, simultaneously | CVP + APP run together against the stack | ⏳ pending live run |
| Zero PII leakage on the bus | Manual review of `features.*` payloads during live run | ⏳ pending live run |

The two pending rows are the live M2 demonstration — run CVP (webcam/RTSP) and
APP (mic/file) at the same time against the dockerized stack and confirm both
topics receive envelopes whose `privacy.gate_passed` is true and whose payloads
contain no raw PII. Code and contracts are in place to make that run pass.

---

## 5. Key Decisions & Notes

- **Feature envelope: designed fresh, versioned.** No prior spec existed; the
  `topics.py` reference was descriptive only. Versioned from envelope #1 so MSFE
  (later sprint) can branch/reject on `schema_version`.
- **Execution model: host-native arm64, not containerized.** CVP/APP are the
  compute-heavy parts; emulation under `linux/amd64` would cripple inference and
  remove MPS/Metal access. Real target is the Jetson (arm64+TensorRT), so an
  amd64 dev container would be parity with the wrong thing. Containerization is
  deferred to a Jetson-targeted sprint.
- **ML deps isolated.** `[project.optional-dependencies].pipelines` keeps
  torch/TF/cv2 off the core install and out of the CI lint/unit job;
  `tensorflow-macos` is selected by environment marker on Apple Silicon.
- **Privacy-first order of operations.** In both pipelines the gate runs before
  detection/feature work that leaves the box; CVP detects on the already-blurred
  frame.
- **Audit hashes only.** The gate hashes post-scrub payloads (SHA-256 via PGC
  `hash_payload`); raw PII is never stored or forwarded.

---

## 6. Carried Forward / Next

- **Live M2 run** is the remaining gate evidence (two sources simultaneously,
  zero-leakage review). Capture `docker compose ps`, a kafka-console-consumer
  sample of each `features.*` topic, and an `audit_events` count delta as TRL-3
  evidence.
- The CVP/APP read from their sources directly today; the `raw.video.frames` /
  `raw.acoustic.samples` UDIH passthrough topics exist but are not yet used —
  decide in a later sprint whether ingestion fans through raw topics first.
- Plate/face detection uses OpenCV Haar cascades as the fail-closed PII step;
  a stronger detector can be swapped behind the same `scrub_*` interface.
- **Next gate (M3):** feeds fused in MSFE — `features.*` → `fused.objects`.

---

*Record generated at the close of Sprint 3-4 code work for retraceability and TRL 3 validation evidence.*
