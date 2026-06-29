# M9 — Acoustic-Event-Aware Refit & Evaluation Procedure

This is the operator run procedure for the M9 block on `olaberry`. The code is
delivered and unit-verified; the fit + held-out evaluation run live (real broker,
real clips) and produce the validation result for `sprint_15_16_completion.md`
§8.

The M9 representation only carries signal if the fit corpus is **regenerated**
through the 1.1.0 pipeline so each FusedObject carries `acoustic_meta`. Reusing
the M7 `normal_corpus.jsonl` will hard-abort at fit time (acoustic-presence
guard) — by design.

## 0. Preconditions

- Code at the M9 commit installed (`pip install -e '.[ade]'`).
- `FUSED_SCHEMA_VERSION == 1.1.0`, `FEATURE_SCHEMA_VERSION == 1.1.0`.
- `esc50_split.json` present and unchanged (fit folds 1/2/3, eval folds 4/5).
- The sealed `models/ade/ade_isoforest_m7.joblib` left in place (never
  overwritten).

## 1. Regenerate the fit corpus (folds 1/2/3 ambient negatives only)

Bring up the stack and MSFE first, then run the capture tap, then replay ambient
audio. The tap requires no code change — it serializes whatever MSFE emits, which
is now 1.1.0 objects with `acoustic_meta`.

```
docker compose up -d
python3 -m kanatir.core.msfe            # MSFE; start BEFORE producers
python3 scripts/capture_normal_corpus.py \
    --out datasets/ade_fit_corpus/normal_corpus_m9.jsonl \
    --target 200 \
    --msfe-window-s 30.0 \
    --app-max-windows 200 --cvp-max-frames 600 \
    --source-clip "datasets/ESC-50/audio/1-100038-A-14.wav:chirping_birds" \
    --source-clip "datasets/ESC-50/audio/2-100786-A-10.wav:rain" \
    # ... one --source-clip per fold-1/2/3 ambient negative used this capture ...
    --idle-timeout-s 120
# then, concurrently, replay the ambient wavs into APP and normal video into CVP
```

### Three operator must-dos (not enforceable by the scripts)

1. **`--source-clip` must enumerate the real fold-1/2/3 clips.** The fold guard
   and the leakage firewall both parse `manifest.source_clips`. The m9 fit
   **refuses to write** without manifest/split provenance, and the fold guard
   fails closed on any fold-4/5 or unparseable filename. List the actual fit
   clips.
2. **APP must run YAMNet so acoustic envelopes carry `yamnet_top`.** If the
   replayed audio yields empty `yamnet_top`, `acoustic_meta` is `None`, and the
   acoustic-presence guard correctly aborts the fit. The fix is "replay real
   audio through APP," not "lower the floor."
3. **Reuse `esc50_split.json` unchanged.** It encodes the fit/eval fold map both
   leakage layers check against. Do not regenerate or edit it.

## 2. Fit the m9 detector (fit folds only; guards armed)

```
python3 scripts/fit_ade.py \
    --corpus   datasets/ade_fit_corpus/normal_corpus_m9.jsonl \
    --out      models/ade/ade_isoforest_m9.joblib \
    --manifest datasets/ade_fit_corpus/normal_corpus_m9.jsonl.manifest.json \
    --split    datasets/ade_fit_corpus/esc50_split.json
```

Expected: `ade.fit complete ... feature_view=m9_acoustic_event_aware
acoustic_fraction≈1.0`. The guards abort on: any fold-4/5 or unparseable source
clip; acoustic_fraction below 0.8 (stale/mixed corpus); a missing manifest/split;
or an attempt to overwrite the M7 artifact.

To inspect a corpus without producing a model (no artifact written, ever):

```
python3 scripts/fit_ade.py --corpus <corpus> --out /tmp/ignored.joblib \
    --diagnostic --manifest <manifest> --split <split>
```

## 3. Evaluate on held-out folds 4/5 (no refit; M7 framing)

Start ADE with the m9 artifact and the M7 z-threshold (no threshold gaming), then
run the eval harness on the held-out positives/negatives exactly as M7 did
(balanced-n, seed 42).

```
export ADE_MODEL_PATH=models/ade/ade_isoforest_m9.joblib
# ADE_Z_THRESHOLD: leave at the M7 value; do NOT tune to hit a target.
python3 -m kanatir.core.ade            # start BEFORE producers
# run eval_harness.py over held-out folds 4/5 (see its own --help)
```

## 4. Record the result

Fill `docs/validation/sprint_15_16_completion.md` §8 with, in order:
1. positive vs ambient anomaly-score distributions;
2. the siren-vs-ambient probe and z-reach;
3. the metric table (Precision, Recall, F1, FPR, latency) beside the M7 baseline.

Report whatever the run shows. Improved separability without F1 ≥ 0.75 is a valid
outcome (representation root cause addressed; residual is corpus/calibration, a
carry-forward) and is **not** "TRL 4 achieved." If separability does not improve,
the representation hypothesis is falsified and the dedicated acoustic-event head
(A3 follow-on) is the next candidate.
