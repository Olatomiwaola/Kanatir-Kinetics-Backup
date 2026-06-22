# Sprint Completion Record — Sprint 9-10 (Weeks 17-20)

**Project:** Urban Intelligence & Anomaly Detection Platform (UIADP) — *Project Sentinel*
**Repository:** Kanatir-Kinetics-Backup
**Block:** Sprint 9-10 · Common Situational Awareness & Triage (CSAT)
**TRL Batch:** TRL 1-3 (active)
**Status:** ✅ GATE PASSED — live end-to-end triage demonstrated, `anomalies.raw` → `alerts.triaged`

---

## 1. Objective

Stand up the Common Situational Awareness & Triage stage: consume versioned
`AnomalyRecord` records from `anomalies.raw` (anomaly_schema_version 1.0.0),
assign deterministic severity, deduplicate and suppress repeat detections into
single operator-facing situational items, and publish versioned `TriagedAlert`
records (sa_schema_version 1.0.0) to `alerts.triaged` — preserving the full PGC
audit lineage from raw capture through fusion through anomaly through alert, and
keeping `kanatir.core` importable with no ML deps.

This is the M5 gate named in the Sprint 7-8 carry-forward:
`anomalies.raw` → `alerts.triaged`.

---

## 2. Key Decisions

- **CSAT is a TRIAGE stage, not an explanation stage.** The topic registry
  (`kanatir/core/udih/topics.py`) already reserved `alerts.triaged` ("Triaged
  alerts from CSAT", DAYS_7) and a separate `alerts.explained` ("XAI-explained
  alerts from XAI"). CSAT's job is severity + dedup + situation-snapshot
  assembly; the human-readable "why" (SHAP + NLG) is XAI's job downstream. CSAT
  carries the explainability *inputs* (`detector_scores`, `conflict_k`) through
  verbatim so XAI has them; it does not re-derive them. The stage boundary is
  the blueprint's, not invented here.

- **No `[csat]` extra; CSAT is pure rule-based, ML-free.** `kanatir.core.csat`
  was already in the committed packages list. Triage is deterministic rule logic
  — no sklearn/torch — so `import kanatir.core.csat.*` succeeds on a core-only
  install with no optional-dependency group. A learned triage model would be a
  TRL-3 overclaim. Proven by `test_csat_modules_import_without_ml`.

- **Two distinct dedup mechanisms, deliberately separated.**
  - *Idempotency (drop).* A repeated `fused_id` is a redelivery (Kafka
    at-least-once, consumer restart, replay) — nothing happened twice in the
    world. Dropped silently, counted only as a consumer-side `dropped_duplicates`
    metric. NEVER inflates `suppressed_count`, which would fabricate a second
    observation.
  - *Grouping (suppress).* Distinct anomalies at the same place within a sliding
    time window are one situational event. This is the real operator value-add
    over raw `anomalies.raw`. Surfaced as `suppressed_count` on the alert.

- **Sliding window + max-age cap.** Each new member resets the group's idle
  timer, so one continuous event stays one alert. A hard max-age cap
  (`CSAT_MAX_AGE_S`, default 300s) force-emits a still-active group periodically,
  so a long-running threat re-surfaces instead of buffering behind a
  never-closing window. `CSAT_DEDUP_WINDOW_S` default 60s. Both env-configurable.

- **Tiered, hashable geo key matching GeoRef's own design.** `GeoRef` carries
  lat/lon (optional) OR `site_id` (logical zone label "when no fix is
  available"). `geo_group_key` tiers accordingly: `site_id` present → exact-match
  key (the path M5 actually runs on, since replayed media has site_id, not GPS);
  lat/lon only → coarse grid pre-bucket with authoritative haversine re-check in
  `_same_group` (radius `CSAT_DEDUP_RADIUS_M`, **calibration-pending**, flagged
  like `ADE_Z_THRESHOLD`); neither → single ungeolocated bucket. The lat/lon
  proximity path is implemented and unit-tested on synthetic coordinates but
  **not exercised on real fixes** — no GPS on replayed media. Stated, not hidden.

- **Severity is deterministic and stated.** Rule-based, three levels
  (INFO / WATCH / ALERT). A WARMUP baseline cannot escalate to ALERT — we do not
  trust a baseline that has not earned confidence (same epistemic honesty
  `baseline_state` was built for). `conflict_k` is surfaced but does NOT
  auto-escalate: ADE already settled conflict-as-input, not override;
  re-deriving an escalation here would relitigate that call. `conflict_k` is
  deliberately absent from `assign_severity`'s signature — enforced by test.

- **Lineage preserved by UNION, not copy.** The new wrinkle vs. ADE: where ADE
  copied one FusedObject's contributors through, CSAT may collapse several
  AnomalyRecords into one alert, so it unions their contributors (dedup'd by
  `audit_event_id`). The merged alert's lineage covers every raw capture that fed
  any suppressed anomaly — PGC audit trail unbroken raw → envelope → fused →
  anomaly → alert. The `suppressed_count == len(anomaly_ids) - 1` invariant pins
  grouping-suppression as distinct from idempotency-drops (drops never enter
  `anomaly_ids`).

- **`TriagedAlert` versioned from record #1.** `SA_SCHEMA_VERSION = "1.0.0"`.
  Downstream consumers (XAI, mission modules) gate on it. `anomaly_ids` is a
  list (not a single backref) since one alert may answer for several anomalies.
  `detector_scores` and `conflict_k` from the triggering anomaly are passed
  through as explainability inputs for XAI.

- **Honesty constraint maintained.** No claims of trained triage accuracy or
  operational alerting at this gate. The gate demonstrates mechanics: anomalies
  consumed, triaged, versioned alerts published with lineage intact. On synthetic
  media every alert is `severity=info` (ignorance-collapsed anomalies score
  `is_anomaly=False`) — correct and expected. The scoring/triage mechanics are
  the gate criterion, not classifier output.

---

## 3. Files Committed

```
kanatir/core/csat/__init__.py        # ML-free package init; stage description
kanatir/core/csat/alert.py           # TriagedAlert output contract (SA_SCHEMA_VERSION=1.0.0)
kanatir/core/csat/triage.py          # TriageBuffer: idempotency + sliding geo+time grouping + max-age cap
kanatir/core/csat/__main__.py        # live Kafka consumer → triage → alerts.triaged
tests/unit/test_sprint_09_10.py      # 17 unit tests (no broker / ML at test time)
```

No `[tool.setuptools]` packages change — `kanatir.core.csat` was already in the
committed packages list. No optional-dependency group added — CSAT is ML-free by
design.

(Preceding chore commit `a013791` also added the missing `[ade]`
optional-dependency group to `pyproject.toml` — documented in a scratch file at
M4 but never applied; `pip install -e '.[ade]'` would otherwise have failed —
and stopped tracking `data/.DS_Store`.)

---

## 4. Gate Criteria & Evidence (M5)

**Gate:** `anomalies.raw` → `alerts.triaged`, versioned `TriagedAlert` published,
full audit lineage preserved, ML-free core import invariant intact.

| Criterion | Evidence | Result |
|-----------|----------|--------|
| Versioned triaged-alert contract exists | `SA_SCHEMA_VERSION=1.0.0`; XAI/modules gate on it | ✅ |
| Consumes `anomalies.raw`, validates schema version | `__main__` subscribes, validates `anomaly_schema_version`; mismatches skipped + logged | ✅ |
| Severity assignment deterministic + tested | rule maps is_anomaly/score/baseline_state → severity; WARMUP cannot ALERT; boundary tests | ✅ |
| `conflict_k` does not auto-escalate severity | `test_severity_does_not_escalate_on_conflict`; absent from `assign_severity` signature | ✅ |
| Idempotency: redelivery dropped, not suppressed | `test_redelivered_fused_id_is_dropped_not_suppressed`; `dropped_duplicates` separate from `suppressed_count` | ✅ |
| Dedup/suppression works (grouping) | `test_same_site_within_window_collapses_to_one_alert`; live `suppressed_count=4` and `=76` | ✅ |
| Sliding window + max-age cap | `test_max_age_cap_forces_reemission_while_active` | ✅ |
| Situation snapshot carries operator fields | geo, window, baseline_state, score on every alert | ✅ |
| `detector_scores`/`conflict_k` passed through for XAI | present on every `TriagedAlert`, not re-derived | ✅ |
| Lineage preserved anomaly→alert by union | `test_lineage_union_across_merged_anomalies`; live alerts union audit_event_ids | ✅ |
| ML-free core import invariant | `test_csat_modules_import_without_ml`; sklearn+torch blocked, import succeeds | ✅ |
| Unit suite, no infra/ML at test time | 17 tests pass; ruff (E,F,I,UP) clean at line-length 100 | ✅ |
| **Live end-to-end: `anomalies.raw` → `alerts.triaged`** | two `TriagedAlert` records on topic, `csat.triaged` logged, populated `audit_event_ids`, `anomaly_id` backrefs confirmed | ✅ |

**Validation in sandbox:** `17 passed`; `ruff check --select E,F,I,UP` → `All checks passed!`

**Live M5 run evidence (2026-06-22, committed repo, host arm64):**

- **CSAT startup**: `csat.start in=anomalies.raw out=alerts.triaged group=csat
  accepts anomaly_schema_major=1` — consumer subscribed before ADE producer
  (correct ordering for `auto.offset.reset=latest`).
- **Live `csat.triaged` line**:
  ```
  csat.triaged  alert_id=5c23fca6-ab18-475d-a158-2368eb014ace
                severity=info  n_anomalies=5  suppressed=4
                baseline_state=active  conflict_k=0.000
                audit_event_ids=[1785, 1786, 1787, 1784, 1783]
  ```
- **`alerts.triaged` topic live** — two versioned `TriagedAlert` records read
  back via console-consumer, `sa_schema_version=1.0.0`:
  - **Alert `5c23fca6`**: `suppressed_count=4`, 5 anomaly_ids, 5 acoustic
    contributors (`file-01`, audit_event_id 1783–1787), `site_id=zone-A`,
    `baseline_state=warmup` — the WARMUP/ACTIVE distinction visible end-to-end on
    the alert (a warmup "no anomaly" distinguishable from a confident one).
  - **Alert `b34bf8c3`**: `suppressed_count=76`, 77 anomaly_ids, contributor
    union spanning **both modalities** — acoustic (`file-01`) + video (`vid-01`),
    audit_event_id 1783–1942, ~82s window — collapsed to one situational item at
    `site_id=zone-A` under the sliding window. `baseline_state=active`.
- **Privacy/audit lineage intact**: audit ledger after runs:
  **video 1802, acoustic 140**. No raw PII on the bus. Every contributor on every
  alert traceable to a PGC audit_event_id in the ledger.

**Operational notes (not gate items):**
- `severity=info` / `anomaly_score=0.0` expected on synthetic media —
  ignorance-collapsed anomalies (`is_anomaly=False`) triage to INFO. This
  exercises the *triage mechanics* (the gate criterion), not classifier accuracy.
- The 77-anomaly mega-collapse (Alert `b34bf8c3`) is the geo+time grouping
  behaving exactly as specified: a continuous feed at one fixed `site_id`
  collapses to one situational item under the sliding window (one event = one
  alert). In real deployment with multiple sites and intermittent activity this
  fragments naturally; finer-grained intra-site alerting is a deliberate-policy
  tuning question (shorter window or secondary grouping key), out of M5 scope.
- Video and acoustic did not co-window into a single multimodal FusedObject this
  run (YAMNet ~10s load offset clustered APP ~60s ahead of CVP, landing them in
  adjacent MSFE windows). CSAT triages regardless of modality; the grouping
  collapse fired anyway. A tighter APP/CVP launch gap or wider `MSFE_WINDOW_S`
  would force same-window multimodal fusion — not an M5 criterion.

---

## 5. Carried Forward / Next

- **Real-media demo capture (carried from Sprint 5-6 and M4):** Re-run with clips
  that trigger the evidence mappers (real person/car in video, real
  engine/drone/speech audio) to produce non-zero `conflict_k`, confident
  classification, meaningful `anomaly_score` from a fitted IsoForest, and
  therefore live WATCH/ALERT severities (vs. all-INFO on synthetic). Gate already
  passed on synthetic media.
- **IsolationForest fitting:** Fit on a real-media normal corpus once available
  (`[ade]` extra now installable — fixed at `a013791`). `ADE_Z_THRESHOLD` and
  `CSAT` severity floors (`WATCH_SCORE_FLOOR`, `ALERT_SCORE_FLOOR`) all
  calibration-pending against real score distributions.
- **`CSAT_DEDUP_RADIUS_M` calibration:** lat/lon proximity grouping is wired and
  unit-tested on synthetic coordinates; calibrate against real geo data when GPS
  fixes flow. Until then the `site_id` exact-match path is authoritative.
- **Intra-site alerting granularity:** evaluate shorter `CSAT_DEDUP_WINDOW_S` or
  a secondary grouping key if single-site continuous feeds need finer-grained
  alerts. Deliberate-policy question, not a defect.
- **Next gate (M6):** XAI — `alerts.triaged` → `alerts.explained`. SHAP + NLG
  operator-facing explanation text over the `detector_scores` and `conflict_k`
  already carried on every `TriagedAlert`. Claude API appropriate here (offline /
  operator-facing explanation, NOT the real-time alert path — 5s latency budget).
- **Mission modules** (`counter_uas`, `critical_infra`, `env_warning`,
  `crowd_vehicle`, `rf_anomaly`): scaffolded in the package list; consume
  `alerts.triaged` (and later `alerts.explained`). TRL-3 architecture, not yet
  built — honestly labeled.
- **Partner machine (parallel setup):** Excluded from gate evidence per protocol.

---

*Record generated at the close of Sprint 9-10 for retraceability and TRL 3
validation evidence. M5 gate passed: live `anomalies.raw` → `alerts.triaged`
end-to-end triage demonstrated on the committed repo, two versioned TriagedAlert
records published with full audit lineage preserved (video 1802, acoustic 140 in
the PGC ledger), grouping collapse fired live (`suppressed_count` 4 and 76).*
