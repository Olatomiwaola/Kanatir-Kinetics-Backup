# Decision Contract — M5.1: Identity-Reference Preservation and Triage-Honesty Correction

**Block ID:** M5.1
**Prior sealed state:** M5 (CSAT) — commits `4d91403` (code) / `7e52bd9` (gate evidence). Coordinated forward release also touches the MSFE (M3 `8cb43a8`) and ADE (M4 `9cfd25c`) *forward* code.
**Repo:** `Olatomiwaola/Kanatir-Kinetics-Backup`
**Machine of record:** `olaberry`
**Contract written:** 2026-07-17
**Status:** `DECISIONS CONFIRMED` (signed §8, before code)

> **Provenance:** This contract is written and committed **before** any M5.1 implementation code. Corresponding evidence will be recorded in `docs/validation/M5_1_completion.md`, which cites this contract's commit hash as the approved plan. The Step-Zero repository reads that this contract is built on were performed live on `olaberry`; their findings are recorded verbatim in §4.

---

## 1. Objective

M5.1 is a **forward corrective release** across MSFE (M3), ADE (M4), and CSAT (M5) that makes the triage output report honestly what it observed. A post-gate red-team review of the sealed M5 established that the flat `TriagedAlert` cannot distinguish one video track producing many observations from several distinct tracks each producing one, cannot expose mixed classifications at a shared site, and cannot link a continuing geo-temporal incident across max-age flushes. The root cause is specific: CVP produces ByteTrack `track_id` values, but `track_id` is discarded at MSFE fusion, while `source_sensor_id` already survives fusion through `Contributor`. Because the `track_id` half of the reference never leaves the `VideoFeatures.detections` payload, ADE and CSAT never receive video-track context, so the alert silently reports an *observation* count that reads like an *object* count. M5.1 additively preserves the available source-local video-track references end-to-end, separates observations from those references, exposes mixed classes, adds geo-temporal incident continuity, and bounds idempotency state. This is **TRL 3→4 maturation work within the CSAT/triage boundary** — not a TRL-4 achievement, and not any claim of physical-object tracking.

This block begins **new, separately gated work**. It does not modify, rewrite, or re-gate any sealed evidence or prior `docs/validation/` record.

---

## 2. Scope boundaries (binding)

**In scope:**
- MSFE: extract all distinct source-local video-track references present in a fused window as qualified tuples `(source_sensor_id, track_id)`, deduplicated on that tuple; publish them on `FusedObject` as an optional additive field. `FUSED_SCHEMA_VERSION 1.1.0 → 1.2.0`.
- ADE: propagate `source_track_refs[]` verbatim from `FusedObject` to `AnomalyRecord`; change ADE's forward `fused_schema_version` gate from exact-match to major-match (D-GATE). `ANOMALY_SCHEMA_VERSION 1.0.0 → 1.1.0`.
- CSAT: add `observation_count`, `distinct_video_track_ref_count`, `identity_reference_available`, `group_reason`, `incident_id`, `incident_sequence`, `class_breakdown`; bound `_seen_fused_ids` (TTL); thread `incident_id` through the max-age reset path. `SA_SCHEMA_VERSION 1.0.0 → 1.1.0`.
- Read-only downstream compatibility re-validation of the sealed M6/XAI runtime against the upgraded alert (Scenario E).

**Explicitly out of scope — no claim of any kind may be made in these areas:**
- Physical-object identity, exact physical-object count, or physical-object continuity.
- Cross-camera re-identification / association; cross-modal (acoustic/RF → video) object association.
- Any change to `kanatir/pipelines/` (CVP or the shared `FeatureEnvelope` schema). The qualifier reuses the **existing** `source_sensor_id`; no new envelope field, no `FeatureEnvelope.schema_version` bump.
- Multiplicity/count/arrival-rate-based severity escalation.
- Any edit to a sealed commit (M3–M9) or its `docs/validation/` record; M6 source/schema/tests untouched.
- The full `Incident → Object[] → Evidence` hierarchy and production-scale swarm handling (TRL-4+, §7).

**Data handling:** RF identifiers are HMAC-hashed upstream in the RAP privacy scrub; no raw PII travels in an envelope. M5.1 adds no new identifier to any envelope and moves only `source_sensor_id` (already present) and `track_id` (already present) references forward. Video refs are treated as source-local, civilian-derived sensor references throughout.

**Claim language (honesty constraint):** M5.1 closes as **"a forward corrective release that preserves available source-local video-track references, distinguishes anomaly observations from those references, exposes mixed classifications, provides geo-temporal incident continuity, and bounds duplicate-tracking state — while the sealed M5 evidence and the sealed M6 explanation chain remain valid and unchanged."** It does **not** claim physical-object tracking, exact object count, cross-camera identity, cross-modal object association, or swarm tracking.

---

## 3. Decisions (confirmed before code)

### D1 — Qualified reference is `(source_sensor_id, track_id)`, published as a new top-level `FusedObject` field

**Decision: the source-local video-track reference is the tuple `(source_sensor_id, track_id)`, both read from data already present at `fuse_window`, published as a new top-level `source_track_refs[]` field on `FusedObject`. No new identifier is added anywhere, and `Contributor` is NOT extended.**

`FeatureEnvelope.source_sensor_id` (`envelope.py:174`, non-optional) already identifies the source stream, and `Detection.track_id` (`envelope.py:88`) is the ByteTrack id. `fuse_window` already builds one `Contributor` per envelope carrying `source_sensor_id` and `envelope_id` (`fusion.py:138–146`), so the `source_sensor_id` half of the reference already survives fusion; only `track_id` is dropped, because `Contributor` has no track field and nothing else reaches into `e.features.detections`. Both halves are reachable in the same contributor loop with no new plumbing. Deduplication is on the tuple, so `(cam-01,5)` and `(cam-02,5)` are distinct. A bare integer list is rejected as insufficient: ByteTrack ids are stream-local.

**Considered and rejected (identifier):** adding a *new* source identifier at the CVP/envelope boundary (the "clean fix"). Rejected because `source_sensor_id` already is the canonical per-source handle; a second field would be redundant, would create two identifiers that must agree (drift risk), and — decisively — would reopen `kanatir/pipelines/` and force a `FeatureEnvelope.schema_version` bump, breaching the ratified MSFE+ADE+CSAT scope. Reuse is the narrowest change that satisfies the requirement.

**Considered and rejected (placement):** extending `Contributor` with an optional `track_ids: list[int]` instead of a top-level field, since `Contributor` already pairs `source_sensor_id` with an envelope. Rejected because `Contributor` is an audit and lineage structure and must stay that; overloading it with operational track references muddies that role, and — decisively — nesting `track_ids` under each contributor structurally groups tracks beneath one source-record, which re-introduces the exact "is this one physical object?" ambiguity M5.1 exists to correct. A top-level flat set of qualified tuples, with its own deduplication semantics, makes no grouping claim. Audit lineage and operational identity-reference summary are separate jobs and stay separate structures.

### D2 — D-GATE: ADE forward gate becomes major-match

**Decision: change ADE's forward `fused_schema_version` check from exact string equality to major-version match, mirroring CSAT and XAI.**

ADE currently gates exact-match (`__main__.py:139`, `obj.fused_schema_version != FUSED_SCHEMA_VERSION`), importing its expected value from MSFE (`__main__.py:39`, `from kanatir.core.msfe.fused import FUSED_SCHEMA_VERSION`). Because the target is imported, the coordinated 1.2.0 bump is self-consistent in-release (ADE's expected value rises with MSFE's) — so exact-match does **not** break this release. But exact-match remains brittle to every future MSFE minor/patch. Major-match removes that fragility structurally: ADE accepts any `1.x`, consistent with `source_track_refs[]` being an optional additive (semver-minor) field.

**This touches sealed-milestone code and is therefore called out explicitly:** the change is to the **forward** ADE (`ade/__main__.py`). The sealed M4 commit `9cfd25c` and its evidence are **not** edited or re-gated; the historical commit stays reproducible with its original fixtures. Approved as a logged forward change, consistent with ADE being in the coordinated release.

**Considered and rejected:** keep exact-match and let ADE's imported target track MSFE. Rejected — it leaves ADE the lone exact-match hop and re-exposes the same brittleness at the next minor bump.

### D3 — CSAT honesty fields, counting semantics, and frozen severity

**Decision: add the seven fields to `TriagedAlert`, built in `TriagedAlert.from_anomalies`. Counts are of anomaly records. `assign_severity` is byte-for-byte frozen.**

- `observation_count := len(anomaly_ids)` (grouped anomaly records; dedup on `fused_id` happens in `offer()` before grouping).
- `distinct_video_track_ref_count` = count of unique `(source_sensor_id, track_id)` across grouped members; **`null`** when no refs available (never `0` — `0` would falsely assert confirmed absence).
- `identity_reference_available` = bool.
- `group_reason` = the explicit grouping rule string (e.g. `"same_geo_group_within_sliding_window"`, sourced from the actual `geo_group_key` / `_same_group` logic).
- `class_breakdown` = counts of anomaly records per class, `"UNKNOWN"` key explicit, never omitted.
- `classification` is **retained**, redefined in documentation as the **trigger class** = classification of the highest-severity member, tiebroken by most-recent `window_end` (matching `_OpenGroup.trigger()`, `triage.py:129`). It must be labelled as the trigger class, distinct from `class_breakdown`, and must not stand in for group composition.

**Prohibited fields:** `distinct_object_count`, `physical_object_id`, or any equivalent.

**Severity frozen:** `assign_severity` is called only from the trigger (in `trigger()` and `from_anomalies`); no new field feeds it. `WATCH_SCORE_FLOOR`, `ALERT_SCORE_FLOOR`, and the WARMUP-cap logic are untouched. Multiplicity-based escalation is a TRL-4 non-goal (§7).

**Considered and rejected:** counting classes/refs by track reference. Rejected — that requires track↔record association CSAT does not perform; anomaly-record counting is what CSAT holds natively and can defend.

### D4 — Bounded idempotency state via TTL eviction

**Decision: replace the unbounded `_seen_fused_ids: set[str]` (`triage.py:166`) with a TTL-bounded structure; TTL = `CSAT_MAX_AGE_S` + a configurable safety margin, default margin making the effective TTL comfortably longer than the longest possible open incident.**

A genuine Kafka redelivery recurs within seconds; a `fused_id` older than the longest open incident cannot legitimately reappear as a live duplicate. A replay arriving **after** TTL eviction is treated as a **new observation** — acceptable and documented. The mechanism stays deterministic (`now` injected, as the buffer already does), exposes its config, and is test-covered. `dropped_duplicates` (`triage.py:168`) accounting is unchanged and remains separate from grouping.

**Considered and rejected:** an LRU capacity bound. Acceptable in principle but TTL maps directly onto the existing max-age timeline and the "redeliveries occur within seconds" property, so it is the locked default.

### D5 — Incident continuity keyed to the open-group identity within its geo bucket

**Decision: an `_OpenGroup` that survives a max-age flush-and-reset retains its `incident_id` and increments `incident_sequence`; a group that goes idle and closes ends the incident; later activity in the same geo bucket after closure opens a new `incident_id`.**

Defined precisely: **incident begins** = first member opens a group; **`incident_sequence` increments** = each max-age flush of a still-active group (the reset path in `flush_ready`); **incident closes** = group goes idle ≥ `CSAT_DEDUP_WINDOW_S`; **new `incident_id`** = any member arriving in the geo bucket after closure. This is **geo-temporal** incident continuity, not physical-object continuity. The `incident_id` is threaded through the flush-and-reset path so a long-running event re-emits under a stable id without losing grouping identity.

**Considered and rejected:** keying incidents to `fused_id` or to a track ref. Rejected — that would smuggle in an object-continuity claim M5.1 explicitly disclaims.

### D6 — `suppressed_count` invariant retained as a structural guarantee

**Decision: retain `suppressed_count == observation_count - 1` as a tested invariant.**

The sealed `_coherence` validator (`alert.py:162–168`) already enforces `len(anomaly_ids) - 1 == suppressed_count`, and construction (`alert.py:211`) sets `suppressed_count = len(anomalies) - 1` with `anomaly_ids = [a.anomaly_id …]`. With `observation_count := len(anomaly_ids)`, the retained invariant is **byte-identical to the existing check** — a rename/alias over the same quantity, not a new constraint. `observation_count` is the honestly-named surface; `suppressed_count` keeps its meaning for back-compat.

**Considered and rejected:** letting `observation_count` supersede `suppressed_count`. Rejected — removing a field in an additive correction is a silent semantic change to a sealed-adjacent surface; retention is the safer, back-compatible posture.

### D7 — Completion-record filename

**Decision: the completion record is `docs/validation/M5_1_completion.md`.**

M5.1 is a forward corrective, not two sprints of work; a `sprint_17_18` label would misstate what happened and dilute the `sprint_XX_YY` sequence as source-of-truth. **Verification completed:** the repository search (`grep -rn "sprint_.*completion" docs/ kanatir/ scripts/`) returned only literal historical references and found no wildcard glob or tooling dependency on the `sprint_` prefix. `docs/validation/M5_1_completion.md` is therefore approved.

---

## 4. Contract alignment (read the sealed code — findings verbatim)

**Files read (live on `olaberry`, Step Zero):**
- `docs/validation/DECISION_CONTRACT_TEMPLATE.md` — governing template (this document is aligned to it).
- `kanatir/core/msfe/fused.py` — `FUSED_SCHEMA_VERSION` (`:46`), `FusedObject` (`:174`), `Contributor` (`:87`), custom `to_json`/`from_json` (`:219`/`:223`), `acoustic_meta` optional-additive precedent (`:198`).
- `kanatir/core/msfe/fusion.py` — `fuse_window(envelopes) -> FusedObject | None` (`:126`), `FusedObject(...)` construction (`:157`).
- `kanatir/core/msfe/evidence.py` — imports `VideoFeatures` etc. from `kanatir.pipelines.common.envelope`; reads `feats.detections` (`:71,74`).
- `kanatir/core/ade/__main__.py` — imported target (`:39`), exact-match gate (`:139`).
- `kanatir/core/ade/anomaly.py` — `ANOMALY_SCHEMA_VERSION` (`:39`), `AnomalyRecord` (`:53`), to/from_json (`:99`/`:103`).
- `kanatir/core/csat/__main__.py` — `_major` (`:43`), `accepted_major` (`:73`), major-match gate (`:109`).
- `kanatir/core/csat/triage.py` — `_OpenGroup` (`:111`), `trigger()` (`:129`), `TriageBuffer` (`:150`), `_seen_fused_ids` (`:166`), `dropped_duplicates` (`:168`), `offer()` (`:172`), flush-and-reset (`flush_ready`).
- `kanatir/core/csat/alert.py` — `SA_SCHEMA_VERSION` (`:52`), `TriagedAlert` (`:121`), `_coherence` (`:162`), `from_anomalies` (`:211`), to/from_json (`:217`/`:221`).
- `kanatir/core/xai/__main__.py` — `_SA_MAJOR` (`:44`), `_accepts` major-match (`:66`).
- `kanatir/pipelines/common/envelope.py` — `Detection.track_id` (`:88`), `VideoFeatures` (`:94`), `FeatureEnvelope.source_sensor_id` (`:174`), `envelope_id` (`:172`), `schema_version` (`:171`).
- `kanatir/pipelines/cvp/__main__.py` — ByteTrack `track_id=int(tid)` (`:112`), `source_sensor_id=sensor_id` on envelope build (`:122`).

**Findings:**

| Concern | Sealed reality | This block's action |
|---|---|---|
| Schema versions | MSFE `1.1.0`, ADE `1.0.0`, CSAT `1.0.0` (confirmed live) | Bump to `1.2.0` / `1.1.0` / `1.1.0` |
| MSFE→ADE gate | **EXACT-match** (`__main__.py:139`), but target **imported** from MSFE (`:39`) — self-consistent in-release; brittle to future minors | Change forward ADE to **major-match** (D2). Sealed M4 untouched |
| ADE→CSAT gate | MAJOR-match (`_major`, `:109`); `1.1.0` major stays `1` | No change; accepts `1.1.0` |
| CSAT→XAI gate | MAJOR-match (`_accepts`, `:66`); `1.1.0` major stays `1` | No change; sealed M6 accepts `1.1.0` (verified live, Scenario E) |
| Qualifier for refs | `source_sensor_id` exists on `FeatureEnvelope` (`:174`) and **already survives fusion** via `Contributor` (`fusion.py:138–146`); `track_id` on `Detection` (`:88`) is dropped — `Contributor` has no track field, nothing reads `detections` | Reuse both as `(source_sensor_id, track_id)`; add only the missing `track_id` extraction; **no** envelope change |
| Root cause (precise) | Not "source identity discarded at fusion" — `Contributor.source_sensor_id` carries it. Only `track_id` never leaves `VideoFeatures.detections` | Walk `e.features.detections` in the contributor loop; collect `(e.source_sensor_id, det.track_id)` |
| `FusedObject` additivity + placement | Pydantic model; `acoustic_meta` is an existing optional/`None`-default additive precedent; `_coherence` checks only windows + modality counts; `contributors` published on the wire | Add optional **top-level** `source_track_refs[]`, `None`/empty default, mirror `acoustic_meta` docstring; `Contributor` **not** extended (D1) |
| `suppressed_count` | `_coherence` already enforces `len(anomaly_ids)-1 == suppressed_count` (`:165`) | `observation_count := len(anomaly_ids)`; invariant is identical, retained (D6) |
| Severity | `assign_severity` called only from trigger | Frozen byte-for-byte; nothing new feeds it |

**Consequence:** the entire correction is achievable with **zero changes to `kanatir/pipelines/`** and zero edits to any sealed commit. New surface: one optional field on each of three forward schemas, one gate-mode change (ADE), CSAT triage-buffer fields + TTL bound + incident threading. The highest-risk hop flagged at planning (MSFE→ADE) is confirmed non-breaking in-release and is hardened anyway by D2.

**Assumptions corrected by reading the code:**
- Planning assumed the 1.2.0 bump would *break* MSFE→ADE. **Corrected:** ADE imports its expected value from MSFE, so the bump is self-consistent in-release; exact-match is brittle-not-breaking. (Amendment B.)
- Planning assumed M6 already existed and consumed the flat alert. **Corrected/confirmed:** true; M6 major-gates and needs no change. (Amendment A context.)
- Initial searches assumed a `source_id`-style field was absent. **Corrected:** the field exists as `source_sensor_id`; earlier greps missed it because the patterns searched did not include the bare substring `source_sensor`. Reading the schema, not the name, resolved it.
- Planning assumed both halves of the reference were lost at fusion. **Corrected:** reading the `fuse_window` body (`fusion.py:126–168`) shows `source_sensor_id` already survives via the per-envelope `Contributor` (`:138–146`); only `track_id` is dropped. The extraction to add is narrower than assumed: reach `e.features.detections` in the existing contributor loop.
- `fuse_window` envelope scope confirmed: the full `list[FeatureEnvelope]` is in scope through construction (`FusedObject(...)` at `:157`); no refactor needed to reach `source_sensor_id` + `detections` at the extraction point.
- Completion-record glob check run (§6/D7): `grep -rn "sprint_.*completion" docs/ kanatir/ scripts/` returns only **literal historical references** (`sprint_15_16`, `sprint_13_14` named in prose/READMEs) — no wildcard glob, no tooling that discovers records by the `sprint_` prefix. `M5_1_completion.md` is safe; existing records are referenced by exact name anyway.
- **Stated limitation (not corrected — declared):** `source_sensor_id` is stamped per-envelope by CVP from an upstream `sensor_id`; its global uniqueness per physical camera is an upstream **deployment-configuration** property this block relies on but does **not** itself establish. Tuple-distinctness therefore means "distinct as reported by the envelope source id," not "proven distinct physical cameras." Recorded so no reader over-reads Test 4 / Scenario B.

---

## 5. Design / algorithm decisions (approved before code)

**MSFE reference extraction (`fuse_window`).** In the existing per-envelope loop (`fusion.py:138–146`), for each contributing **video** envelope, read `e.source_sensor_id` and walk `e.features.detections`, collecting `(e.source_sensor_id, det.track_id)`; emit the set of distinct tuples as a **new top-level** `source_track_refs[]` field on `FusedObject`. `Contributor` is **not** extended (D1). Handle: repeated detections from one track; several distinct tracks from one source; identical integer ids from different sources; video envelopes with no valid track id; non-video contributors (no refs contributed); duplicate refs within one window. A multi-ref fused object does **not** imply one physical object, and the top-level flat-set placement is chosen precisely so the structure makes no grouping claim.

**CSAT fields** built in `from_anomalies` from the grouped members; `class_breakdown` and counts computed over anomaly records; `incident_id`/`incident_sequence` supplied by the buffer's group identity (D5).

**Guardrails:**

| guardrail | value | purpose |
|---|---|---|
| ref dedup key | `(source_sensor_id, track_id)` | stream-local ids never collide across sources |
| ref placement | top-level `source_track_refs[]` flat set; `Contributor` unchanged | no structure groups tracks under a source → no implied physical object; audit lineage stays a separate concern |
| `distinct_video_track_ref_count` when no refs | `null` | never assert confirmed absence with `0` |
| `assign_severity` inputs | unchanged (trigger only) | no multiplicity escalation; severity frozen |
| `observation_count` | `len(anomaly_ids)` | equals existing `suppressed_count + 1` — no new invariant |
| TTL | `CSAT_MAX_AGE_S` + margin | bound memory without dropping live redeliveries |
| envelope schema | untouched | keep block inside MSFE+ADE+CSAT scope |

**Structural guarantees** (true by construction):
- No `distinct_object_count` / `physical_object_id` field exists, so no physical-object claim can be emitted.
- Refs are only ever *preserved and deduplicated*, never associated across sources or modalities — no code path merges refs.
- `source_track_refs[]` is a top-level flat set; no schema structure nests tracks under a contributor or a source, so the shape itself cannot encode a "these tracks are one object" claim.
- `assign_severity` receives no new argument, so no new field can alter severity.
- `observation_count == suppressed_count + 1` holds by the existing `_coherence` validator.

**Provisional values** (not calibrated; no quantitative claim rests on these):
- TTL safety margin default — an operational bound, not a measured quantity; documented as configurable.
- `CSAT_DEDUP_WINDOW_S` / `CSAT_MAX_AGE_S` remain at their current values; unchanged by M5.1.

---

## 6. Required validation (acceptance criteria agreed in advance)

**Unit/property tests, mandatory (expected outputs locked):**
1. Duplicate replay suppression — same `fused_id` twice → one observation; duplicate counted, bounded state valid.
2. One track, many observations — one ref across 100 anomalies → `observation_count=100`, `distinct_video_track_ref_count=1`, `identity_reference_available=true`.
3. Multiple tracks, one source — five distinct refs, one camera → `observation_count=5`, `distinct_video_track_ref_count=5`.
4. Same integer id, different cameras — `(cam-01,5)`, `(cam-02,5)` → `distinct_video_track_ref_count=2`.
5. Multiple tracks in one fused window — repeated + distinct refs → MSFE emits complete deduped set; ADE preserves; CSAT unions.
6. Missing references — no usable refs → `identity_reference_available=false`, `distinct_video_track_ref_count=null` (not `0`).
7. Mixed-class group — UAV+GROUND+AMBIENT → all classes in `class_breakdown`; trigger class distinct; nothing hidden.
8. Incident continuation across max-age flush → same `incident_id`, `incident_sequence` increments, deterministic.
9. Incident closure + new incident → original closes; later activity gets a new `incident_id`.
10. Bounded seen-set — exceed TTL → memory bounded; in-retention duplicates still suppressed; post-eviction replay documented + tested.
11. Lineage preservation — contributors survive MSFE/ADE/CSAT; dedup by `audit_event_id`.
12. Existing-behaviour regression — existing single-track/single-class/dedup/severity/grouping/flush tests remain valid or are rewritten with documented reason. **No test silently deleted to green the suite.**

**Block-level validation (live on `olaberry`, per topic, captured records not verbal summary):**
- Scenario A — one track, repeated observations; alert shows many observations, one distinct ref, "physical-object identity not established."
- Scenario B — two distinct refs → `distinct_video_track_ref_count = 2`, refs visible in fused + anomaly records.
- Scenario C — mixed classes → populated `class_breakdown`, trigger class retained separately.
- Scenario D — long-running incident → stable `incident_id`, increasing `incident_sequence`.
- Scenario E — sealed M6 read-only: extend chain `features.video → fused.objects → anomalies.raw → alerts.triaged → alerts.explained`; verify M6 consumes `SA 1.1.0`, no gate rejection/silent skip, emits valid `ExplainedAlert`, lineage present, new optional fields don't break parsing; **no M6 source/schema/test/record change.** Capture input alert, M6 logs, emitted explained alert, schema-version values, and the harness command.
- Lineage preserved through MSFE → ADE → CSAT (→ XAI, read-only), dedup by `audit_event_id`.
- Degraded/absent-input behaviour: RF-only / acoustic-only / no-track-id windows → `identity_reference_available=false`, `null` count.
- Privacy & audit: no new identifier enters any envelope; PGC audit-ledger counts captured before/after.
- **Pre-commit item (D7) — DONE:** glob check run; only literal historical references to `sprint_XX_YY` exist, no wildcard glob depends on the filename. `M5_1_completion.md` confirmed safe (§4).
- **CI green on `main`** at block close, ML-free-import + numpy-import-guard invariants intact and CI-enforced.

**Gate condition:** live end-to-end evidence on `olaberry`. Partner-machine evidence is excluded.

---

## 7. Standing constraints

- Do **not** modify or re-gate sealed evidence or prior `docs/validation/` records (M3–M9).
- Do **not** claim physical-object identity/count/continuity, cross-camera association, cross-modal object association, or swarm tracking.
- Do **not** alter core/sealed modules unless a §3 decision explicitly approves it and a test first proves the additive path cannot work. The one approved core-adjacent change is D2 (forward ADE gate mode); sealed M4 is untouched.
- Do **not** touch `kanatir/pipelines/` (CVP / shared envelope). Reuse `source_sensor_id`.
- Preserve existing contracts; the three schema bumps are additive minor bumps, gated compatibly.
- **Decisions-before-code.** This contract is signed and committed before implementation.
- Every new claim backed by a test, log, or validation artifact.
- Negative findings reported **verbatim**. No threshold gaming. `assign_severity` frozen.
- Close with `docs/validation/M5_1_completion.md` referencing `M5 4d91403 / 7e52bd9` as prior state and **this contract's commit** as the approved plan. Include the verbatim statement: *"Original sealed milestone evidence remains valid for the system versions demonstrated at those commits. M5.1 is a forward corrective release and does not retrospectively alter the earlier results."*

---

## 8. Sign-off

Decisions D1–D7, the contract-alignment findings (§4), the design specification and guardrails (§5), and the acceptance criteria (§6) are reviewed and **approved. Implementation is authorised.**

**Approved refinements to the original proposal:**
- Qualifier reuses existing `source_sensor_id` rather than adding a new envelope field — keeps the block inside MSFE+ADE+CSAT scope (D1).
- ADE gate change reframed: not a break-fix but forward-robustness hardening, since the imported target makes the bump self-consistent in-release (D2, Amendment B).
- `source_sensor_id` uniqueness recorded as a stated upstream-deployment limitation, not an M5.1 guarantee (§4).
- Root cause corrected to "`track_id` dropped, `source_sensor_id` survives via `Contributor`" after reading the `fuse_window` body; extraction narrowed accordingly (§1, §4, §5).
- `source_track_refs[]` placed as a new top-level `FusedObject` field, `Contributor` left unchanged — audit lineage and operational identity-reference summary kept as separate structures so the schema shape implies no physical-object grouping (D1, §5).

**Signed:** Olatomiwa Oladunjoye — 2026-07-17
**Contract commit:** `70a690663fac52aa6ee4f7bc4981d6f2f67f8728`

---

## 9. Amendments

| # | Date | Decision amended | Change | Why / what forced it | Claim impact |
|---|---|---|---|---|---|
| A | 2026-07-17 | §1 rationale | M6 recognised as already existing and consuming the flat `TriagedAlert`; M5.1 reframed as correcting the live upstream contract while M6 stays sealed and read-only | Repository reality: M6 `e1f3dd4` present and major-gates on `sa_schema_version` | neutral (narrows M5.1's interaction with M6 to read-only) |
| B | 2026-07-17 | D2 (MSFE→ADE) | Planning assumption "1.2.0 bump breaks MSFE→ADE" corrected: ADE imports its expected value from MSFE, so the bump is self-consistent in-release; exact-match is brittle-not-breaking, and major-match is adopted for forward robustness | Live read of `ade/__main__.py:39` (import) + `:139` (gate) | neutral (removes a false break; hardens future compatibility) |
