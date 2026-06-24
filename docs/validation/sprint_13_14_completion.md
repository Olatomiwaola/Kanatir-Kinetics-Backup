# M7 — TRL 3 Validation Report (closes the TRL 1–3 batch)

**Project:** Urban Intelligence & Anomaly Detection Platform (UIADP) — *Project Sentinel* / FusionGuard
**Challenge:** CAF/DND IDEaS — "Turning Urban Data Into Real-Time Insight"
**Repository:** Kanatir-Kinetics-Backup
**Gate:** M7 — TRL 3 Validation Gate. Per the blueprint (FusionGuard.docx, Sprint
11–12), this is the blueprint's **M4 — TRL 3 validation gate** that closes the
TRL 1–3 batch and begins Phase 2. In the finer per-stage M-scheme used across the
sprint completion records (M3 MSFE, M4 ADE, M5 CSAT, M6 XAI), the next sequential
label is **M7**. They are the same gate; "M7" is used here.
**Status:** ✅ GATE COMPLETE — validation executed against the three TRL-3 criteria
with PASS/FAIL recorded honestly. This closes the TRL 1–3 batch. Two of three
criteria FAIL on the acoustic benchmark; the cause is diagnosed and remediation is
defined for TRL 4–6. The operational pipeline paths are all demonstrated.

---

## 1. Purpose and honest framing

M7 is a validation/benchmark gate, not a build gate. It measures the live,
contract-versioned pipeline (UDIH → CVP/APP → MSFE → ADE → CSAT → XAI) against
three TRL-3 criteria — end-to-end latency ≤ 5 s, F1 ≥ 0.75, FPR ≤ 15 % — on real,
labeled public benchmark data, and reports the result as-is.

This report does not claim the platform meets the thresholds where it does not.
A defensible, falsifiable negative result with a diagnosed cause is the honest
TRL-3 outcome, and is reported as such.

**Scope of what is validated (and what is not):**
- This is an **acoustic-led detection benchmark** using ESC-50. ESC-50 provides an
  honest ambient-negative / activity-positive split with a genuine quiet baseline.
- **VisDrone is NOT used for a video detection metric.** Its frames have no
  empty/low-activity negative class (0 empty frames; median 65 objects/frame; max
  317), so it cannot supply a defensible "normal quiet video" baseline. No
  video-channel FPR is claimed. Video negative-baseline validation is deferred to
  Phase 2 (controlled low-activity urban capture).
- Detection is scored on **alert-presence** (severity ≥ WATCH within the clip's
  replay window = positive), not classification-match. The unsupervised
  IsolationForest at TRL 3 does not support fine class accuracy; alert-presence is
  the honest bar.
- Fit and eval data are **public benchmark data** (ESC-50, CC non-commercial),
  not field capture. ESC-50 positive categories (siren, helicopter, airplane,
  engine, chainsaw, car_horn, train) are generic sounds, not drone-specific. This
  benchmark demonstrates **detection of urban acoustic activity vs ambient**; it is
  **not** a counter-UAS validation (no drones-as-targets data).

---

## 2. Methodology

### 2.1 Fitted detector (from the fit-stage record)
ADE runs a fitted IsolationForest loaded at startup via `ADE_MODEL_PATH`
(`models/ade/ade_isoforest_m7.joblib`, `corpus_id=sha256:7155a7bb…`, n_samples=200,
feature_schema=1.0.0, `feature_view=m7_acoustic_led_no_n_modalities`). Startup
proof: `ade.start fitted=True ready=['isolation_forest']`. The model was fit on
200 real captured ambient FusedObjects (ESC-50 folds 1–3), schema-pinned, leakage-
checked. Full provenance in `docs/validation/sprint_13_14_fit_stage.md`.

### 2.2 Disjoint fit/eval split (no leakage)
ESC-50 fold-based, deterministic: **folds 1–3 → fit corpus; folds 4–5 → held-out
eval.** Authoritative split file `datasets/ade_fit_corpus/esc50_split.json`
(`split_policy=esc50_fold_based_disjoint`). The fit leakage-guard confirmed
`leakage_check=passed`: no clip used to fit ADE appears in the held-out eval set.
Eval counts available in folds 4–5: 160 negatives, 112 positives.

### 2.3 Eval harness (per-clip bounded replay)
- Each eval clip replayed individually through APP → live pipeline; per-clip
  `replay_start_ts/replay_end_ts` recorded.
- A background tap on `alerts.explained` collects every ExplainedAlert, stamped
  with wall-clock `arrival_ts`. **Attribution uses arrival_ts vs the replay window
  ± margins**, NOT the alert's capture-time `window_start/end` (different clock —
  same `ingest_ts` vs `capture_ts` distinction as the M3 fix).
- **Unique `site_id` per clip + inter-clip gap** prevent CSAT geo/time grouping
  from merging clips. Confirmed: `alerts_shared_across_clips: []` (no cross-clip
  grouping) in the scored runs.
- **Pre-warm phase:** 40 fit-fold ambient clips (folds 1–3, disjoint from eval)
  replayed first to drive ADE's adaptive baseline out of WARMUP; tagged
  `phase=prewarm scored=false`, excluded from all metrics. The harness asserts
  `baseline_active_observed=True` before scoring; both reported runs confirmed it.
  This separates baseline-warmup artifact from genuine detector behavior.
- Attribution margins were set from measured latency (see §3.3), not guesswork.

### 2.4 Benchmark subsets
- **Detection metric:** balanced medium subset, 50 clips (25 positive + 25 negative,
  deterministic seed 42, folds 4–5). Reported as a representative held-out subset,
  not the full 272-clip sweep.
- **Latency metric:** separate clean 12-clip run with a hardened tap consumer to
  eliminate Kafka consumer-group session-timeout artifacts (see §3.3).

---

## 3. Results

### 3.1 Detection (50-clip balanced held-out subset)

| | Predicted alert | Predicted quiet |
|---|---|---|
| **Positive (n=25)** | TP = 3 | FN = 22 |
| **Negative (n=25)** | FP = 0 | TN = 25 |

- **Precision = 1.00** (every alert raised was on a true positive; zero false alarms)
- **Recall = 0.12**
- **F1 = 0.214** → **FAIL** (threshold ≥ 0.75)
- **FPR = 0.00** → **PASS** (threshold ≤ 0.15)
- Baseline ACTIVE during scoring; `alerts_shared_across_clips: []`.

**Anomaly-score distributions (max attributed anomaly_score per clip):**
- Positives: n=25, mean = 0.252, max = 0.597
- Negatives: n=25, mean = 0.190, max = 0.436

The distributions **overlap**: positives score only marginally higher than
negatives, and most positives never reach the WATCH threshold. Precision is
perfect because the few alerts raised were on genuine positives, but recall is
very low because the detector does not lift most positives above ambient.

*Note on recall as a lower bound:* during the 50-clip run, alert arrival latency
drifted to p95 ≈ 11 s (Kafka session-timeout effects), so a small number of
positive alerts arrived after the attribution window closed. Reported recall is
therefore a conservative lower bound; the true value may be marginally higher but
remains far below the level required for F1 ≥ 0.75.

### 3.2 False-positive rate
FPR = 0.00 across 25 ambient negatives — **PASS**. The system did not false-alarm
on ambient sound. (This is the favourable face of the same low-sensitivity
behaviour that depresses recall.)

### 3.3 Latency (clean 12-clip run, no consumer-timeout contamination)
End-to-end raw→explained latency, templated explainer path (Claude API
deliberately off the gate path):
- p50 = 6.15 s, p90 = 6.20 s, p95 = 6.20 s, max = 6.21 s → **FAIL** (threshold ≤ 5 s)

This measurement used a tap consumer hardened against group-coordinator session
timeouts (`session.timeout.ms`/`max.poll.interval.ms` raised); **no SESSTMOUT
occurred**, and attribution was clean (0 alerts beyond margin). An earlier run
recorded an inflated max of 18.68 s; that figure is a **measurement artifact** of
tap consumer-group timeouts delaying alert *arrival*, not pipeline processing
time, and is **not** reported as the latency result. The honest latency is ~6.2 s,
which still misses the 5 s budget.

### 3.4 TRL-3 criteria summary

| Criterion | Threshold | Result | PASS/FAIL |
|-----------|-----------|--------|-----------|
| End-to-end latency | ≤ 5 s | ~6.2 s (p95, clean) | **FAIL** |
| F1 | ≥ 0.75 | 0.214 | **FAIL** |
| FPR | ≤ 15 % | 0.0 % | **PASS** |

---

## 4. Operational paths demonstrated (independent of the metric result)

The benchmark also exercised and confirmed the full pipeline mechanics:
- **Fitted-model path operational** — ADE loads a schema-pinned fitted model and
  scores live `fused.objects` (`ade.start fitted=True`, `ade.scored` from the bus).
- **End-to-end lineage** — clips traced UDIH → APP → MSFE → ADE → CSAT → XAI to
  ExplainedAlerts on `alerts.explained`, with PGC `audit_event_id`s intact.
- **Multimodal fusion** — demonstrated end-to-end in M6 (genuine n_modalities=2,
  app-01+cvp-01 → one ExplainedAlert); preserved here (n_modalities retained on all
  contracts; excluded only from the M7 detector view to avoid modality-count
  leakage on the acoustic-only fit corpus).
- **Adaptive baseline posture** — WARMUP-not-escalating confirmed; pre-warm reaches
  ACTIVE; confirmed-normal-only foldback intact.
- **Privacy gate** — `privacy_gate.passed` on every window; speech windows scrubbed
  (`pii_scrubbed=True`) before fusion.
- **Attribution integrity** — per-clip site_id + gap eliminated cross-clip alert
  grouping (`alerts_shared_across_clips: []`).

---

## 5. Root-cause diagnosis (why detection FAILs)

The failure is a **representation limitation**, not a detector bug, a threshold
miscalibration, a warmup artifact, or an attribution error — each of these was
ruled out:
- **Not warmup:** baseline ACTIVE during scoring (pre-warm verified).
- **Not attribution:** clean run shows 0 alerts beyond margin; arrival latency
  measured and margins set from data.
- **Not labels:** a YAMNet-label diagnostic confirmed YAMNet labels the positives
  well and with high confidence (e.g. siren → "Emergency vehicle / Police car
  (siren) / Siren" at 0.65–0.81; train → "Rail transport / Train"; airplane →
  "Aircraft / Fixed-wing aircraft").
- **Not a code defect:** scoring logic unit-tested with known-answer fixtures.

**The cause is the Dempster-Shafer evidence ontology.** The acoustic mapper
(`evidence.acoustic_to_mass`) projects YAMNet labels onto a coarse three-hypothesis
frame (UAV / GROUND / AMBIENT). This frame was designed for urban situational
*evidence fusion*, not for discriminating "alert-worthy acoustic activity" from
"ambient sound." Acoustically distinct positives (siren, chainsaw, train, car_horn)
and several ambient sounds collapse onto similar belief-mass vectors — many via the
broad `"vehicle"` AudioSet parent label, or onto UNKNOWN. The IsolationForest then
faithfully reports that the feature vectors are nearly identical, because the
mapping made them so. Confirmed quantitatively by the overlapping anomaly-score
distributions (positives 0.252 vs negatives 0.190) and the probe (siren raw mean
0.470 vs ambient 0.439; 0/30 sirens reach z=3.0). No threshold cleanly separates
the classes (ambient max z ≈ 2.1 vs siren max z ≈ 2.5), so threshold tuning cannot
recover the metric without inflating FPR.

The latency miss (~6.2 s vs 5 s) is a separate, modest gap attributable to the
per-event MSFE windowing + CSAT dedup-idle + templating path on a host-native
(non-edge, non-Flink) execution model.

---

## 6. TRL 4–6 remediation (Phase 2)

1. **Acoustic event ontology / detector head.** Add modality-specific acoustic
   event features (or a dedicated acoustic-event head) *before* fusion, so the
   detector sees acoustic distinctiveness directly rather than only the collapsed
   three-hypothesis mass. Candidate: carry YAMNet top-class confidence / class-group
   embedding as ADE features (a `FEATURE_SCHEMA_VERSION` bump + re-fit), reintroducing
   `n_modalities` once a proper multimodal normal corpus exists.
2. **Threshold calibration to a target false-alarm rate** against learned score
   distributions (the `z_threshold` is flagged calibration-pending in code), once a
   discriminating feature space exists.
3. **Latency:** move the gate path to the Jetson/Flink edge execution model and
   tighten the MSFE window + CSAT dedup for the real-time path to recover the 5 s
   budget; confirm the templated (non-Claude) path meets it.
4. **Video negative baseline:** controlled low-activity urban video capture (or a
   sparse urban corpus) to enable an honest video-channel FPR.
5. **Scenario traceability:** propagate a `scenario_id`/`clip_id` through the
   envelope so attribution does not depend on time-window correlation.

---

## 7. Artifacts (retraceability)

```
models/ade/ade_isoforest_m7.joblib(.meta.json)       # fitted model + provenance
datasets/ade_fit_corpus/esc50_split.json             # disjoint fit/eval split
datasets/ade_fit_corpus/normal_corpus.jsonl(.manifest.json)  # fit corpus + provenance
datasets/eval/m7_medium.summary.json / .perclip.jsonl / .alerts.jsonl   # detection benchmark
datasets/eval/m7_latclean.summary.json / .perclip.jsonl / .alerts.jsonl # clean latency run
datasets/eval/yamnet_label_diagnostic.json           # label diagnostic
datasets/eval/probe_siren.jsonl / probe_ambient.jsonl # discrimination probe inputs
scripts/{build_esc50_split,build_fit_concat,capture_normal_corpus,fit_ade,
         eval_harness,analyze_alert_latency,probe_ade_scores,diagnose_yamnet_labels}.py
docs/validation/sprint_13_14_fit_stage.md            # fit-stage checkpoint record
```

---

## 8. Gate disposition

**TRL 1–3 batch: CLOSED.** The full pipeline is built, contract-versioned, and
operational end-to-end on a fitted detector. The TRL-3 acoustic detection benchmark
was executed honestly on held-out public data: **FPR PASS (0.0 %), F1 FAIL (0.214),
latency FAIL (~6.2 s)**, with the detection shortfall traced to a representational
limitation of the current D-S ontology and a concrete TRL 4–6 remediation defined.

No claim is made that the platform meets F1 ≥ 0.75 or latency ≤ 5 s. The result is
defensible, falsifiable, and fully retraceable to committed artifacts. TRL 4–6
architecture design proceeds as a separate deliverable (new chat), with this report
and the fit-stage record as the closing TRL 1–3 evidence.
