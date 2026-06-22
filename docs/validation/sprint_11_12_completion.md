# Sprint Completion Record — Sprint 11-12 (Weeks 21-24)

**Project:** Urban Intelligence & Anomaly Detection Platform (UIADP) — *Project Sentinel*
**Repository:** Kanatir-Kinetics-Backup
**Block:** Sprint 11-12 · Explainable AI (XAI)
**TRL Batch:** TRL 1-3 (active)
**Status:** ✅ GATE PASSED — live end-to-end explanation demonstrated, `alerts.triaged` → `alerts.explained`; multimodal fusion (`n_modalities=2`) verified raw→explained against all 8 acceptance criteria

---

## 1. Objective

Stand up the Explainable AI stage: consume versioned `TriagedAlert` records from
`alerts.triaged` (sa_schema_version 1.0.0), turn the `detector_scores` and
`conflict_k` already carried on every alert into operator-facing explanation
text plus structured attributions, and publish versioned `ExplainedAlert`
records (explained_schema_version 1.0.0) to `alerts.explained` — preserving the
full PGC audit lineage raw → fused → anomaly → alert → explained, and keeping
`kanatir.core` importable with no ML deps.

This is the M6 gate named in the Sprint 9-10 carry-forward:
`alerts.triaged` → `alerts.explained`. An additional acceptance bar was imposed
this block, beyond the M5-equivalent mechanics gate: a **dedicated multimodal
verification** that genuinely heterogeneous streams (acoustic + video) co-window
into a single `FusedObject` and trace, intact, all the way to one
`ExplainedAlert` — because the IDEaS challenge requires heterogeneous data
fusion, and two single-modality alerts do not demonstrate it.

---

## 2. Key Decisions

- **XAI is an EXPLANATION stage, not a triage or detection stage.** It does not
  re-derive severity, re-run detection, or re-triage. It consumes the
  explainability inputs CSAT passed through verbatim (`detector_scores`,
  `conflict_k`) and produces `attributions` + `explanation_text`. The upstream
  snapshot (severity, classification, geo, baseline_state, anomaly_score) is
  carried forward for auditability so an explained alert is self-contained. The
  stage boundary is the blueprint's (`alerts.triaged` → `alerts.explained` were
  both reserved in the topic registry from the start), not invented here.

- **Explainer protocol with two implementations behind one seam.** `__main__`
  holds an `Explainer` and calls `.explain()`, never branching on the concrete
  type. `TemplatedExplainer` (pure-stdlib, deterministic, offline) is the
  AUTHORITATIVE M6 output — CI and the live gate run this and only this.
  `ClaudeExplainer` (real Anthropic SDK) is optional demo enrichment, selected
  ONLY by explicit `XAI_EXPLAINER=claude`, never CI / never gate / never the
  real-time path. Both produce the same `ExplainedAlert` contract; only the
  prose differs. `explainer_kind` ("templated" | "claude") lands on every record
  so a reviewer can tell which path generated it.

- **The Claude API is appropriate here precisely because XAI is off the
  real-time path.** Explanation is offline / operator-facing, not subject to the
  5-second alert-path latency budget. The architecture note's distinction holds:
  Claude API for operator-facing explanation, never for the live alert path.
  Failure is explicit — missing SDK or key raises a clear error unless
  `fallback_to_templated=True` is configured, in which case the record honestly
  carries `explainer_kind="templated"` (what actually ran), not "claude".

- **Attribution is honest or absent — never fabricated.** SHAP attribution sits
  behind the interface with a lazy import (inside the attribution method, never
  at module top), so `import kanatir.core.xai.explainer` succeeds core-only.
  When there are no real detector scores, the alert is severity=info, or no
  fitted model is supplied, `attributions` is empty and `attribution_available`
  is False with a stated reason. The `ExplainedAlert` validator REJECTS
  attributions carried while flagged unavailable — fabrication is structurally
  impossible, not merely discouraged. Same epistemic posture as
  WARMUP-not-escalating and conflict-as-input-not-override upstream.

- **Lineage preserved by straight COPY, not union.** Unlike CSAT (which UNIONs
  contributors across a collapse), XAI is 1:1 with a single `TriagedAlert` — no
  further collapse at this stage — so it carries the alert's contributors
  forward verbatim. The PGC audit trail stays unbroken.

- **The single-site mega-collapse is explained as-is, not re-fragmented.** The
  Sprint 9-10 open question is settled: XAI explains whatever CSAT emits,
  including a large `suppressed_count`, as one situational item. Demonstrated
  live (a 93-anomaly collapse narrated as "92 further anomalies suppressed into
  this single situational item over a ~70s window … one continuous event").
  `suppressed_count` is an explanation input, not a blocker. Finer intra-site
  granularity remains a CSAT tuning question, not an XAI prerequisite.

- **Two separate optional-dependency groups.** `[xai]` (shap, numpy,
  scikit-learn) for attribution; `[xai-claude]` (anthropic only) isolated so the
  SDK is never forced into the normal XAI install. Mirrors the `[ade]` pattern.
  `[xai]` resolves clean (shap 0.52 → pandas/numpy/sklearn/cloudpickle/slicer/
  tqdm) — verified via `pip install -e '.[xai]' --dry-run`. The authoritative
  templated path needs NEITHER group.

- **Honesty constraint maintained.** No claims of trained explanation accuracy or
  validated threat assessment at this gate. The gate demonstrates mechanics:
  alerts consumed, explained, versioned records published with lineage intact.
  On synthetic/ambient media most alerts are severity=info with empty
  attributions — correct and expected. (One live run produced severity=ALERT;
  see §4 operational notes for the honest reading.)

---

## 3. Files Committed

```
kanatir/core/xai/explained.py        # ExplainedAlert contract (EXPLAINED_SCHEMA_VERSION=1.0.0); ML-free
kanatir/core/xai/explainer.py        # Explainer protocol + TemplatedExplainer; SHAP lazy-imported
kanatir/core/xai/claude.py           # ClaudeExplainer; anthropic guarded/lazy; opt-in, non-gating
kanatir/core/xai/__main__.py         # live consumer alerts.triaged → explain → alerts.explained
tests/unit/test_sprint_11_12.py      # 22 unit tests (no broker / ML / network / API key at test time)
verify_m6_multimodal.sh              # dedicated multimodal acceptance harness (8 criteria)
pyproject.toml                       # + [xai] and [xai-claude] optional-dependency groups
```

`kanatir.core.xai` was already in the committed packages list. Committed at
`0c1ad32`, pushed to `main` (`87c0359..0c1ad32`).

---

## 4. Gate Criteria & Evidence (M6)

**Gate:** `alerts.triaged` → `alerts.explained`, versioned `ExplainedAlert`
published, full audit lineage preserved, ML-free core import invariant intact,
authoritative output deterministic/offline (templated), Claude API optional and
auditable.

| Criterion | Evidence | Result |
|-----------|----------|--------|
| Versioned explained-alert contract exists | `EXPLAINED_SCHEMA_VERSION=1.0.0`; mission modules gate on it | ✅ |
| Consumes `alerts.triaged`, validates schema version | `__main__` subscribes, validates `sa_schema_version` major; mismatches skipped + logged | ✅ |
| Explainer interface, implementation-agnostic consumer | `Explainer` protocol; `__main__` holds one, never branches on type | ✅ |
| Templated explainer deterministic + offline | `test_templated_explanation_is_deterministic`; pure stdlib, no clock/rng/network | ✅ |
| Attribution honest when no scores/model | empty `attributions` + stated reason; validator rejects attributions-while-unavailable | ✅ |
| SHAP behind `[xai]`, lazy-imported | `test_contract_and_interface_import_without_ml`; shap/sklearn/numpy/torch blocked, import succeeds | ✅ |
| Claude path opt-in, guarded, non-gating | `XAI_EXPLAINER=claude` only; anthropic lazy; mocked in tests, no network/key; `explainer_kind="claude"` auditable | ✅ |
| Lineage preserved alert→explained | contributors copied verbatim; `audit_event_ids` intact | ✅ |
| ML-free core import invariant | explained.py + explainer.py + claude.py import with all ML/SDK absent | ✅ |
| Unit suite, no infra/ML/network at test time | 22 tests pass (full repo suite **92 passed, 1 skipped**); ruff (E,F,I,UP) clean at line-length 100 | ✅ |
| **Live end-to-end: `alerts.triaged` → `alerts.explained`** | versioned `ExplainedAlert` records on topic, `xai.explained` logged, populated `audit_event_ids`, `alert_id` backrefs confirmed | ✅ |
| **Multimodal fusion verified raw→explained (8/8 criteria)** | dedicated harness: genuine `n_modalities=2` co-window traced to one `ExplainedAlert` preserving both modalities (see below) | ✅ |

**Validation in sandbox / repo:** `92 passed, 1 skipped`; `ruff check --select
E,F,I,UP` → clean. (The 1 skip is the numpy-guarded M4 test on the ML-free
runner, by design.)

**Live M6 explanation evidence (2026-06-22, committed repo, host arm64):**

- **XAI startup**: `xai.start in_topic=alerts.triaged out_topic=alerts.explained
  group=xai explainer_kind=templated emits_schema=1.0.0 accepts_sa_major=1` —
  consumer subscribed before the CSAT producer (correct ordering for
  `auto.offset.reset=latest`).
- **`alerts.explained` topic live** — versioned `ExplainedAlert` records read
  back via console-consumer, `explained_schema_version=1.0.0`,
  `explainer_kind=templated`, `attribution_available=false` with the honest note
  on synthetic media, full contributor lineage carried.

**Live M6 MULTIMODAL verification (2026-06-22, `verify_m6_multimodal.sh`):**

Strategy: both sensor sources stream live and concurrently (APP `--source mic`,
CVP `--source 0` webcam), launched in the same instant, both publishing into the
same MSFE window. Because MSFE correlates on `ingest_ts` (bus-arrival, per the
M3 fix), concurrent live streams co-window by construction. `CSAT_DEDUP_WINDOW_S=5`
so the grouped anomaly triages within the harness drain.

- `MSFE_WINDOW_S` used: **30.0 s**
- APP / CVP launch: concurrent (same epoch; both streamed the full window)
- `fused.objects` total: **257**, of which **67** are multimodal (`n_modalities≥2`)
- Traced multimodal event (all 8 acceptance criteria PASSED):
  - `fused_object_id`: `bf011839-4a87-423a-99c3-1199b1f44980`
  - `n_modalities`: **2**
  - contributor sources: **`app-01` + `cvp-01`** (acoustic + video)
  - audit lineage: acoustic `2292`, video `2293` — both modalities present
  - `anomaly_id`: `e6c07b62-f062-477b-8834-45944b1f3dac` (ADE consumed the
    multimodal `FusedObject` and emitted an anomaly linked to its `fused_id`)
  - `alert_id`: `2f4823e8-2af0-48d6-a4e7-4ef7922b5130` (CSAT triaged it)
  - `explained_id`: `211c1fe5-b22b-40d5-bf01-374da714976e` (XAI explained it)
  - final `ExplainedAlert` preserves **both** modalities in contributors and the
    explanation text states multimodal evidence
- **RESULT: ALL CRITERIA PASSED** (C1 co-window, C2 `n_modalities≥2`, C3 both
  sensors, C4 both modalities in lineage, C5 ADE anomaly linked, C6 CSAT triaged,
  C7 XAI explained, C8 both modalities preserved + multimodal stated).

This multimodal `fused_id=bf011839` is the authoritative M6 heterogeneous-fusion
evidence. The earlier acoustic-only (`explained_id=a4dabfd8`) and video-only
(`explained_id=06677225`, a 110-anomaly collapse) records are valid XAI evidence
but are NOT multimodal — APP and CVP ran minutes apart and never co-windowed —
and are recorded as supporting, not as the fusion claim.

**Operational notes (not gate items):**
- The verified multimodal run produced **severity=ALERT, classification=GROUND**
  on the traced alert — the FIRST non-INFO severity observed end-to-end. The
  honest reading: this demonstrates the deterministic severity-escalation path
  firing live (`is_anomaly=True`, `baseline_state=active`, score ≥ floor), NOT a
  validated threat detection. On a live webcam in a room, "GROUND/ALERT" is the
  triage MECHANICS working, not classifier accuracy. Threat/classification
  accuracy remains deferred to a fitted detector on a labeled corpus.
- First multimodal verification attempt FAILED at C6 only — the multimodal
  anomaly was still buffered in CSAT's sliding window when the harness read the
  topic (drain expired before the group force-emitted). Not a fusion or lineage
  defect; a timing artifact of CSAT's deliberate buffering. Re-run with
  `CSAT_DEDUP_WINDOW_S=5` closed C6→C8 cleanly. Documented as a launch/timing
  finding, not a code change.
- Attribution is empty on every record this block — no fitted detector yet, and
  most alerts are info. `attribution_available=false` with a stated reason
  throughout; the SHAP path is wired and `[xai]`-installable but not exercised
  on a fitted model. Honest, on the record.

---

## 5. Carried Forward / Next

- **Real-media demo capture (carried from Sprint 5-6, M4, M5, M6):** still the
  item that converts synthetic/ambient INFO + the single live ALERT into
  *validated* WATCH/ALERT severities and meaningful SHAP attributions over a
  fitted IsolationForest. The webcam/mic live run demonstrated mechanics and one
  escalation, but classification accuracy is unproven. Re-run with clips that
  trigger the evidence mappers (real person/car in video, real engine/drone/
  speech audio) once a labeled corpus is available.
- **IsolationForest fitting:** fit on a real-media normal corpus
  (`[ade]` installable). Once fitted, `TemplatedExplainer.attribute()` reaches
  its SHAP path and produces non-empty `attributions`; the empty-but-honest
  branch then no longer fires for confident anomalies. `ADE_Z_THRESHOLD` and CSAT
  severity floors (`WATCH_SCORE_FLOOR`, `ALERT_SCORE_FLOOR`) all
  calibration-pending against real score distributions.
- **Claude explainer live demo:** the `XAI_EXPLAINER=claude` path is wired
  against the real Anthropic SDK but was not exercised live this block (templated
  is the gate path). Run it with a key for the operator-facing-prose demo;
  attribution still flows from the shared honest path (Claude rewrites prose, never
  fabricates numbers).
- **Multimodal launch discipline (new learning):** genuine `n_modalities≥2`
  fusion requires both pipelines PRODUCING into the same wall-clock
  `MSFE_WINDOW_S` — concurrent live streams (mic + webcam), not file-burst +
  stream launched apart. `--source mic` is the continuous APP path; `test.wav`
  is a finite burst. For multimodal demos, stream both live and concurrently.
- **CSAT dedup window vs verification drain:** the default 60s dedup window can
  buffer a grouped anomaly past a short verification drain. For fast end-to-end
  verification use a shorter `CSAT_DEDUP_WINDOW_S`; for realistic operator
  grouping keep the default. Deliberate-policy knob, documented.
- **Next gate (M7):** Mission modules — consume `alerts.explained` (and
  `alerts.triaged`). `counter_uas`, `critical_infra`, `env_warning`,
  `crowd_vehicle`, `rf_anomaly` are scaffolded in the package list; TRL-3
  architecture, not yet built — honestly labeled.
- **TRL 4-6 architecture design:** to begin after the Sprint 11-12 validation
  gate closes, targeting a DRDC POINT Phase 1 proposal.
- **Partner machine (parallel setup):** Excluded from gate evidence per protocol.

---

*Record generated at the close of Sprint 11-12 for retraceability and TRL 3
validation evidence. M6 gate passed: live `alerts.triaged` → `alerts.explained`
end-to-end explanation demonstrated on the committed repo (`0c1ad32`); versioned
ExplainedAlert records published with full audit lineage preserved. Dedicated
multimodal verification passed all 8 acceptance criteria — genuine
`n_modalities=2` heterogeneous fusion (`fused_id=bf011839`, app-01 + cvp-01,
acoustic 2292 + video 2293) traced raw → fused → anomaly (`e6c07b62`) → alert
(`2f4823e8`) → explained (`211c1fe5`), both modalities preserved end-to-end. 92
tests pass, 1 skipped (ML-free runner, by design); ruff clean.*
