# Completion Record — M5.1: Identity-Reference Preservation and Triage-Honesty Correction

**Block ID:** M5.1
**Prior sealed state:** M5 (CSAT) — `4d91403` (code) / `7e52bd9` (gate evidence).
**Approved plan:** the signed M5.1 decision contract, commit `70a690663fac52aa6ee4f7bc4981d6f2f67f8728` (`docs/validation/M5_1_decision_contract.md`).
**Repo:** `Olatomiwaola/Kanatir-Kinetics-Backup`
**Machine of record:** `olaberry`
**Completed:** 2026-07-17
**Status:** `CODE COMPLETE + LIVE GATE PASSED` — coordinated push held pending sign-off.

> **Sealed-milestone integrity (verbatim, required by contract §7):**
> *"Original sealed milestone evidence remains valid for the system versions demonstrated at those commits. M5.1 is a forward corrective release and does not retrospectively alter the earlier results."*

---

## 1. Objective delivered

M5.1 is a forward corrective release across MSFE (M3), ADE (M4), and CSAT (M5) that makes the triage output report honestly what it observed. It additively preserves the available source-local video-track references end-to-end as qualified `(source_sensor_id, track_id)` tuples, separates anomaly *observations* from those *references*, exposes mixed classifications, adds geo-temporal incident continuity, and bounds idempotency state — while the sealed M5 evidence and the sealed M6 explanation chain remain valid and unchanged.

**Honesty constraint honored — M5.1 does NOT claim:** physical-object identity, exact object count, physical-object continuity, cross-camera re-identification, cross-modal object association, or swarm tracking. This is TRL 3→4 maturation within the CSAT/triage boundary.

---

## 2. Code delivered (per-engine, separately committed)

| Engine | Commit | Change | Schema |
|---|---|---|---|
| Decision contract | `70a6906` | signed, pre-code | — |
| MSFE (M3 fwd) | `0d1668b` | `_track_refs_for`; optional top-level `source_track_refs[]` on `FusedObject`; `Contributor` unchanged (D1) | `FUSED_SCHEMA_VERSION 1.1.0 → 1.2.0` |
| ADE (M4 fwd) | `5089053` | verbatim `source_track_refs` pass-through; forward gate exact→**major**-match (D2) | `ANOMALY_SCHEMA_VERSION 1.0.0 → 1.1.0` |
| CSAT (M5 fwd) | `e2e79dd` | 7 honesty fields + refs union; TTL-bounded `_seen_fused_ids` (D4); incident continuity (D5); `observation_count == len(anomaly_ids)` and null-not-0 enforced in `_coherence` | `SA_SCHEMA_VERSION 1.0.0 → 1.1.0` |
| Evidence handoff | `3501410` | partial-gate handoff note (interim) | — |

**Decisions realized:** D1 (reuse `source_sensor_id`; top-level flat set; `Contributor` untouched) · D2 (ADE major-match; sealed M4 `9cfd25c` untouched) · D3 (7 fields, counts of anomaly records, `assign_severity` **frozen byte-for-byte**) · D4 (TTL seen-set) · D5 (geo-temporal incident id/sequence) · D6 (`suppressed_count == observation_count − 1` retained + made tamper-evident) · D7 (`M5_1_completion.md` filename).

**Structural guarantees (true by construction):** no `distinct_object_count`/`physical_object_id` field exists; refs are only preserved+deduplicated, never associated across sources; `source_track_refs` is a top-level flat set (no grouping claim); `assign_severity` receives no new argument.

---

## 3. Unit validation

- **Full suite: 217 passed** (was 201 at M5; +16 M5.1 tests across `test_ade_source_track_refs.py`, `test_csat_m5_1_honesty.py`, and the additive MSFE test).
- **ML-free-import invariant intact:** `test_ade_contract_modules_import_without_ml` and the CSAT/XAI equivalents pass with sklearn/torch/numpy absent.
- **ruff clean** on all changed modules.
- Two sealed version-pin tests (`test_sprint_07_08.py`, `test_sprint_09_10.py`) updated with documented reason per contract §6 test-12; **no test silently deleted**. Sealed commits M3–M9 untouched and reproducible at their own hashes.

---

## 4. Live end-to-end gate evidence (on `olaberry`)

Full pipeline brought up on the docker stack (Kafka/Zookeeper/Redis/Postgres/Timescale/Flink), engines started downstream-first (XAI → CSAT → ADE → MSFE), four console-consumers attached at `latest` before injection. Raw captures in `docs/validation/M5_1_live_evidence/`.

### 4a. Injected scenarios (deterministic producer at `features.*`)

**Method + honesty label:** Scenarios A–D and degraded were produced by `scripts/m5_1_scenario_injector.py`, which publishes real, schema-validated `FeatureEnvelope`s to the earliest real topics (`features.video`/`.acoustic`/`.rf`) and lets the **real MSFE→ADE→CSAT→XAI chain run every hop** — it never fabricates a fused object, anomaly, or alert. An injector is required because Scenario B (two cameras emitting the *same* integer ByteTrack id) cannot be produced by live capture; this is a **data-plane** test (does the pipeline preserve/distinguish refs?), explicitly **labeled injected**, not live-sensor capture.

| Scenario | Config | Verdict |
|---|---|---|
| **A** — one track, many observations | DEDUP=60, WINDOW=2, spacing 3 | `observation_count=5`, `distinct_video_track_ref_count=1`, `suppressed_count=4`, `class_breakdown={UAV:5,UNKNOWN:0}` ✅ |
| **B** — two sensors, same id (CSAT union) | DEDUP=10, spacing 3 | `observation_count=2`, **`distinct_video_track_ref_count=2`**, `source_track_refs=[(cam-01,5),(cam-02,5)]` ✅ |
| **B-prime** — MSFE same-window union | DEDUP=10, spacing 0 | `observation_count=1`, `distinct=2`, one FusedObject carrying both refs ✅ |
| **C** — mixed classes | MAX_AGE=30, DEDUP=20, spacing 3 | `class_breakdown={UAV:1,GROUND:1,AMBIENT:1,UNKNOWN:0}` (sum=obs=3); trigger `classification=AMBIENT` (single, distinct); `distinct=2` (AMBIENT via acoustic, no ref) ✅ |
| **degraded** — null-not-0 | MAX_AGE=30, DEDUP=20 | acoustic-only + video-no-track → `identity_reference_available=false`, `distinct_video_track_ref_count=null` (NOT 0), `source_track_refs=null` ✅ |
| **D** — incident continuity | MAX_AGE=30, DEDUP=20, spacing 8, 70s | 3 emissions, **same `incident_id`**, `incident_sequence` 0→1→2 ✅ |
| **E** — sealed M6 read-only | — | M6 consumed SA `1.1.0` (alert_id backref), emitted `ExplainedAlert` `explained_schema_version=1.0.0`, **0 gate rejections** across the run, lineage via `contributors` intact ✅ |

### 4b. Real-capture evidence (real perception front-end, split-evidence plan)

**Method + honesty label:** run through the **actual** CVP code path (YOLOv8n + ByteTrack + fail-closed privacy gate) on file sources — no webcam, no personal media, repo data only — so real PGC ledger rows are written and real audit ids + real ByteTrack ids flow.

- **PGC audit-ledger before/after — PASS.** Two real CVP runs moved `audit_events` `6607 → 6627 → 6642` (delta 20 then 15). New rows are real gate-generated (`actor=cvp`, real `event_id`s), `count(event_id ≥ 900001) = 0` — i.e. NOT the injector's synthetic `900001` range. No new identifier enters any envelope (M5.1 reuses `source_sensor_id`, adds none).
- **Real ByteTrack track_ids end-to-end — PASS.** A 15-frame static sequence from one 52-detection VisDrone aerial image produced a `TriagedAlert` (`zone-REAL2`) with `observation_count=15`, `distinct_video_track_ref_count=53` (**53 real ByteTrack ids 1..53**, sensor `cam-visdrone`), and `audit_event_ids=[6628..6642]` (real ledger range). Real detections → real refs → preserved MSFE→ADE→CSAT with real PGC lineage. `observation_count=15` and `distinct_video_track_ref_count=53` are **consistent, not contradictory**: `observation_count` counts the grouped anomaly *records* (one per fused window), while the distinct count counts unique `(source_sensor_id, track_id)` *refs* unioned across them — and a single fused window legitimately carries many video-track refs, so the ref count can exceed the observation count. This is exactly the observation-vs-reference distinction M5.1 exists to surface.

---

## 5. Findings and caveats (reported verbatim — no threshold gaming)

- **B-prime first attempt (spacing 0.3s) was NEGATIVE** and is recorded: the live MSFE `WindowBuffer` matures/trims each envelope individually at `ingest_ts + window`, so envelopes 0.3s apart are harvested in different ticks (two windows), not one. Co-window requires near-identical `ingest_ts` (spacing ≈ 0). The unit test exercises the union directly; the live path needed the timing fix. Recording the failed attempt is evidence quality, not a blemish.
- **RF-only degraded sub-case did not flow:** live MSFE subscribes only `features.video` + `features.acoustic` (`FEATURE_TOPICS`), not `features.rf`. Out of M5.1 scope (M5.1 changes no MSFE topics). The null-not-0 property is proven by the acoustic-only and no-track-id cases.
- **`test.avi` is objectless** (150 frames, zero YOLO detections at conf 0.15); the real track_id demonstration therefore used a VisDrone-DET image sequence instead. `datasets/VisDrone` is the DET (image) set, not video; `cv2.VideoCapture` accepts a printf `%04d` sequence pattern, not a glob.
- **The VisDrone real capture used 15 IDENTICAL frames** (a static sequence), so it proves real `(source_sensor_id, track_id)` **flow and preservation** through MSFE→ADE→CSAT — it does **NOT** demonstrate motion tracking or track persistence across genuine object motion. M5.1 claims reference *preservation*, not tracking *quality*: ByteTrack confirmed ids over a static scene, which is exactly (and only) what the data-plane property under test requires. Tracker accuracy under motion is out of scope for this block.
- **Injector `audit_event_id`s are synthetic** (counter restarts per process → repeats across scenarios). They demonstrate lineage *flow* for the injected scenarios; real ledger population is demonstrated separately by the CVP captures (§4b). Per-scenario captures are separate files.
- **Empty-frame video classified `UAV` conf=0.0** — a pre-existing `BeliefMass.top_hypothesis` behavior under full ignorance (returns the first specific hypothesis). Non-M5.1; does not affect the ref property.
- **Infrastructure interruption:** Docker Desktop crashed under live-gate load mid-Scenario-C (daemon EOF). Not a code issue. Progress was banked (`3501410`), the stack restarted cleanly, and C was re-run to completion. All scenarios above are post-recovery captures except A/B/B-prime (pre-crash, archived as `*.run1.jsonl`).

---

## 6. Downstream compatibility (Scenario E, sealed M6)

M6 (`kanatir/core/xai`) major-gates on `sa_schema_version` and consumed SA `1.1.0` with no source/schema/test change. `ExplainedAlert` has no `sa_schema_version` field and `audit_event_ids` is a computed property (read from `contributors`) — the M5.1 additive alert fields did not break M6 parsing. M6 remains sealed and read-only w.r.t. M5.1.

---

## 7. Provenance

- Approved plan: contract `70a6906` (signed §8, pre-code).
- Prior sealed state: M5 `4d91403` / `7e52bd9` — unchanged.
- Evidence directory: `docs/validation/M5_1_live_evidence/` (`run_notes.md` step log; `topic_*.jsonl` raw captures; `scenario*_*.jsonl` labeled slices; `engine_*.log`; `inject_*.log`; `cvp_*_capture.log`; `ledger_before*.txt`/`ledger_after.txt`).
- Producer tool: `scripts/m5_1_scenario_injector.py` (injection point = `features.*`; honesty header in-module).

**Closing (contract §2 claim language):** M5.1 closes as *a forward corrective release that preserves available source-local video-track references, distinguishes anomaly observations from those references, exposes mixed classifications, provides geo-temporal incident continuity, and bounds duplicate-tracking state — while the sealed M5 evidence and the sealed M6 explanation chain remain valid and unchanged.*
