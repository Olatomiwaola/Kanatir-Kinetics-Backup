# M7 Fit-Stage Validation Record — ADE Fitted-Model Path

**Project:** Urban Intelligence & Anomaly Detection Platform (UIADP) — *Project Sentinel* / FusionGuard
**Repository:** Kanatir-Kinetics-Backup
**Gate:** M7 — TRL 3 Validation Gate (closes the TRL 1–3 batch). Per the blueprint
(FusionGuard.docx, Sprint 11–12), this gate is the blueprint's **M4 — TRL 3
validation gate**. In the finer per-stage M-scheme used across the sprint
completion records (M3 MSFE, M4 ADE, M5 CSAT, M6 XAI), the next sequential label
is **M7**. They are the same gate; "M7" is used here.
**Stage:** Fit-stage checkpoint (ADE fitted-model path) — a sub-stage of M7,
committed before the eval/benchmark harness so the fitted model is a known-good,
retraceable baseline.
**Status:** ✅ FIT-STAGE CHECKPOINT — ADE runs on a fitted model trained on real
captured FusedObjects; fitted/unfitted startup proven; live scoring proven. The
TRL-3 detection **metrics** (F1, FPR, latency) are NOT in this record — they are
the next stage (eval harness).

---

## 1. Objective

Replace the M4/M6 cold-start posture (IsolationForest present but UNFITTED) with a
**fitted** ADE detector trained on a real, captured normal corpus, while keeping
every prior contract and invariant intact. Produce a serialized, schema-pinned
model artifact that ADE loads at startup via `ADE_MODEL_PATH`, with hard-fail
validation on feature-schema drift and no silent fallback to unfitted when a
model path is supplied.

This stage does NOT compute performance metrics. It establishes the fitted-model
mechanism and the auditable corpus provenance the metrics will be measured on.

---

## 2. Key Decisions

- **Offline fit, serialized artifact, load at startup (Option A).** Training is
  separated from live inference. `scripts/fit_ade.py` fits offline and writes a
  joblib artifact + sidecar `.meta.json`; `kanatir/core/ade/__main__.py` loads it
  when `ADE_MODEL_PATH` is set. Unset preserves the M4-compatible unfitted path.
  Reproducible, auditable, and keeps M4 behavior available for non-gate runs.

- **Fit corpus built from REAL captured FusedObjects (A1 only).** The fit corpus
  is FusedObjects tapped from the live `fused.objects` topic during ambient
  replay — never FusedObjects constructed synthetically in code. `fit_ade.py`
  re-validates each corpus line into a real `FusedObject` and ABORTS on any line
  that does not validate. This is the M4 cold-start policy honored: no fitting on
  synthetic/ignorance-collapsed media.

- **Acoustic-led corpus and metric.** VisDrone has no empty/low-activity frames
  (0 empty; median 65 objects/frame; max 317), so it cannot supply a defensible
  "normal quiet video" baseline. The normal corpus and the detection metric are
  therefore ESC-50 acoustic: ambient negatives as normal, positive categories as
  detection targets. VisDrone is reserved for video-positive and multimodal
  fusion evidence only; **no video-channel FPR is claimed**. Video
  negative-baseline validation is explicitly deferred to Phase 2 (needs a
  low-activity urban video corpus or controlled camera capture).

- **`n_modalities` excluded from the fitted detector view (M7 only).** The
  acoustic-led fit corpus is structurally all `n_modalities=1`. Letting the
  IsolationForest see `n_modalities` would teach it "1 modality = normal" and
  flag legitimate multimodal (`n_modalities=2`) events as anomalous purely on
  modality count — a corpus-design artifact, not a real signal. A
  `MaskedFeatureView` wrapper applies the identical column mask at fit AND score
  time (keeps indices `[0,1,2,3,4,5,7]`, drops index 6 = `n_modalities`). The
  full 8-feature contract is UNCHANGED — `extract_features`, `FEATURE_NAMES`,
  `FEATURE_DIM`, and `FEATURE_SCHEMA_VERSION="1.0.0"` are untouched, and
  `n_modalities` is preserved everywhere (FusedObject, TriagedAlert,
  ExplainedAlert, lineage, multimodal gate). Only the detector's *view* is
  masked. `conflict_k` is KEPT — it reflects real fusion disagreement the ambient
  corpus does represent. `feature_view="m7_acoustic_led_no_n_modalities"`,
  `excluded_features=["n_modalities"]` recorded in the artifact. Phase 2
  reintroduces `n_modalities` once a proper multimodal normal corpus exists.

- **Feature schema pinned; drift is a hard failure.** The artifact pins
  `feature_names`, `feature_dim`, `feature_schema_version`. On load, ADE asserts
  exact equality against the current featurizer and raises `AdeModelIncompatible`
  (naming the drifted field) on any mismatch. When `ADE_MODEL_PATH` is set, ADE
  NEVER falls back to unfitted — a gate run cannot silently score with a
  mismatched or unfitted model.

- **Fresh baseline, not serialized.** Only the fitted DETECTOR is persisted. ADE
  builds a fresh `AnomalyEnsemble` with a fresh `AdaptiveBaseline` at load and
  injects the fitted detector. The rolling baseline warms up on gate traffic
  (WARMUP-not-escalating preserved); it is not carried in the artifact.

- **Fold-based disjoint split (no leakage).** ESC-50 folds 1–3 → fit corpus;
  folds 4–5 → held-out eval (negatives + positives). One authoritative split file
  (`datasets/ade_fit_corpus/esc50_split.json`) defines every clip's role.
  `fit_ade.py` takes the split + capture manifest and ABORTS if any fit-corpus
  source clip appears in the held-out eval sets. **No clip used to fit ADE may
  appear in the FPR/F1 evaluation set.**

- **Dual corpus identity.** `corpus_id` (hash of captured `fused_id`s) proves the
  fit matrix; `source_corpus_id` (hash of source clips + capture config) proves
  where the corpus came from. Both recorded; the report cites both.

---

## 3. Files (this stage)

```
kanatir/core/ade/features.py                 # + FEATURE_SCHEMA_VERSION = "1.0.0" (one line; contract unchanged)
kanatir/core/ade/model_io.py                 # load + schema-pin validation + fresh-ensemble injection; AdeModelIncompatible
kanatir/core/ade/__main__.py                 # ADE_MODEL_PATH branch; ade.start fitted=True/False; ade.broker (renamed)
kanatir/core/ade/detectors/masked_view.py    # MaskedFeatureView wrapper (m7_acoustic_led_no_n_modalities)
scripts/fit_ade.py                           # offline fit; artifact + sidecar; leakage guard; provenance
scripts/build_esc50_split.py                 # authoritative fit/eval split (folds 1-3 / 4-5)
scripts/build_fit_concat.py                  # ffmpeg concat of fit-fold ambient wavs + provenance
scripts/capture_normal_corpus.py             # taps fused.objects -> JSONL corpus + manifest
tests/unit/test_m7_model_load.py             # 6 tests: load, 3 drift hard-fails, ready detector, unfitted rejected
tests/unit/test_m7_masked_view.py            # 5 tests: fit/score masking, n_modalities no-op, conflict_k active, schema unchanged
```

Generated data artifacts (version per repo policy — see §6):
```
datasets/ade_fit_corpus/esc50_split.json                       # the split
datasets/ade_fit_corpus/fit_concat_provenance.json             # 240 source clips + hashes
datasets/ade_fit_corpus/normal_corpus.jsonl                    # 200 captured FusedObjects
datasets/ade_fit_corpus/normal_corpus.jsonl.manifest.json      # capture provenance
models/ade/ade_isoforest_m7.joblib                             # fitted model artifact
models/ade/ade_isoforest_m7.joblib.meta.json                   # sidecar metadata
```

---

## 4. Gate Evidence (fit-stage)

**Corpus capture (live pipeline, `olaberry@...`, host arm64):**
- 240 ESC-50 ambient-negative clips (folds 1–3) concatenated via ffmpeg into one
  wav (`fit_ambient_concat.wav`), each clip + output wav sha256-recorded.
- Replayed through APP (`--source fit_ambient_concat.wav --window-s 0.96
  --site-id zone-A`) → MSFE (`MSFE_WINDOW_S=2.0`) → `fused.objects`.
- Tap captured **200** normal FusedObjects, all `n_modalities=1` (MSFE
  single-source fusion, proven at `fusion.py` `fuse_window`: a single-source
  group fuses, flagged non-multimodal).
- `source_corpus_id=sha256:17847cc725fd4b5b2ffc44a8fb95ace4`.

**Fit:**
- `fit_ade.py` → `models/ade/ade_isoforest_m7.joblib` (+ sidecar).
- `n_samples=200`, `feature_dim=8`, `feature_schema=1.0.0`,
  `corpus_id=sha256:7155a7bb7b0208fcb9abbe6987bf7da3`.
- Sidecar: `feature_view=m7_acoustic_led_no_n_modalities`, `leakage_check=passed`,
  `split_policy=esc50_fold_based_disjoint`, `fit_folds=[1,2,3]`.

**Fitted startup proof (`ADE_MODEL_PATH` set):**
```
ade.start fitted=True model_path=models/ade/ade_isoforest_m7.joblib
          feature_schema=1.0.0 n_samples=200
          corpus_id=sha256:7155a7bb7b0208fcb9abbe6987bf7da3
          ready=['isolation_forest'] scaffolded=['lstm_autoencoder','gnn']
```

**Live scoring proof (fitted detector consuming `fused.objects`):**
```
ade.scored anomaly_id=ce5a3d3e-... fused_id=c20d0bc0-... anomaly_score=0.0
           baseline_state=warmup conflict_k=0.0 is_anomaly=False audit_event_ids=[2868]
```
`baseline_state=warmup` correctly prevents escalation early — the
WARMUP-not-escalating posture from M4 is intact under the fitted detector. (Early
`anomaly_score=0.0` reflects the warmup window, not a defect.)

**Unfitted fallback proof (`ADE_MODEL_PATH` unset):**
```
ade.start fitted=False ready='none - baseline+conflict path only'
          note='IsolationForest unfitted (M4 cold-start policy); set ADE_MODEL_PATH to fit-load'
          scaffolded=['lstm_autoencoder','gnn']
```

**Unit suite:** 11 M7 tests pass (`test_m7_model_load.py` 6 + `test_m7_masked_view.py` 5).
Existing suite unaffected (contract files unchanged except the additive
`FEATURE_SCHEMA_VERSION` line).

| Criterion | Evidence | Result |
|-----------|----------|--------|
| Offline fit produces schema-pinned artifact + sidecar | `fit_ade.py` output; sidecar fields | ✅ |
| Fit corpus is real captured FusedObjects (A1) | capture tap; `fit_ade.py` aborts on non-FusedObject line | ✅ |
| Feature-schema drift hard-fails on load | 3 drift tests; `AdeModelIncompatible(field)` | ✅ |
| `ADE_MODEL_PATH` set → fitted, no silent fallback | `fitted=True ready=['isolation_forest']` | ✅ |
| `ADE_MODEL_PATH` unset → M4 unfitted | `fitted=False` cold-start | ✅ |
| Fitted detector scores live fused.objects | `ade.scored` from bus | ✅ |
| `n_modalities` excluded from detector view; multimodal not penalized | `MaskedFeatureView`; 5 tests | ✅ |
| `conflict_k` retained as active feature | masked-view test | ✅ |
| Feature contract unchanged (schema 1.0.0, dim 8) | tests; featurizer untouched | ✅ |
| Fold-based disjoint split, leakage guard | `esc50_split.json`; `leakage_check=passed` | ✅ |
| Dual corpus identity recorded | `corpus_id` + `source_corpus_id` | ✅ |

---

## 5. Honesty / Scope Notes (load-bearing)

- This stage proves the fitted-model **mechanism and provenance**, NOT detection
  performance. F1/FPR/latency are the next stage; nothing here claims a metric
  meets a TRL-3 threshold.
- The detection metric will be **alert-presence**, not classification-match. On
  single-source ambient input the `classification` label (e.g. "UAV" on a rain
  clip) is not meaningful; the IsoForest learns the shape of ambient feature
  vectors, and scoring asks whether positives deviate from that shape. The system
  detects **urban activity**, not validated threats.
- Acoustic-led: ESC-50 carries the metric (honest negative/positive split).
  VisDrone supplies positive + multimodal evidence only; no video FPR claimed.
- Fit and eval data are public benchmark data (ESC-50 CC non-commercial — note in
  the final report), not field capture.

---

## 6. Open Items for the Eval Stage

1. Eval harness: replay held-out ESC-50 folds 4–5 (160 negatives, 112 positives)
   through the fitted pipeline; map each clip → expected alert outcome
   (positive category → expect severity ≥ WATCH; ambient negative → expect quiet);
   collect ExplainedAlerts.
2. Latency harness: raw→explained per event vs ≤5s (templated path; Claude API
   off-path).
3. Benchmark: F1, FPR, latency vs F1≥0.75 / FPR≤15% / ≤5s, PASS/FAIL each.
4. Multimodal evidence refresh: one VisDrone+acoustic co-window through the fitted
   pipeline (`n_modalities=2`), kept SEPARATE from the acoustic detection metric.
5. TRL-3 validation report (`docs/validation/sprint_13_14_completion.md` or agreed
   name) closing the TRL 1–3 batch.
6. Repo policy decision: whether `normal_corpus.jsonl` and the `.joblib` artifact
   are committed to the repo or tracked out-of-band (size / data-license). The
   split file, provenance JSON, and sidecar `.meta.json` SHOULD be committed
   regardless — they are the retraceability evidence.
