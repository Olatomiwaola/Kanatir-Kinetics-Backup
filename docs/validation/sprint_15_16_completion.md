# Completion Record — Acoustic Event-Aware Representation Redesign (TRL 3 → 4 maturation block)

**Block ID:** M9-ACOUSTIC (TRL 3 → 4 representation maturation)
**Prior sealed state:** M8-RF — `172ba0c`; M7 — TRL 3 close gate, `df82e13` / `adf0d33`
**Repo:** `Olatomiwaola/Kanatir-Kinetics-Backup`
**Status:** CODE COMPLETE — all decisions confirmed, all in-sandbox claims test-backed.
Live fit + held-out evaluation are an operator step on `olaberry` (real broker,
real clips); the validation-result section of this record is to be completed from
that run before the block is declared closed.

> This record documents new, separately gated TRL 3 → 4 representation work. It
> does **not** modify, rewrite, or re-gate any sealed M7 or M8-RF evidence. M7
> remains sealed at `df82e13` / `adf0d33`; M8-RF remains sealed at `172ba0c`.

---

## 1. Objective

Resolve the M7 acoustic recall limitation by redesigning the acoustic evidence
representation so YAMNet's class information is preserved **before** fusion,
rather than collapsed into the coarse {UAV, GROUND, AMBIENT} Dempster-Shafer
frame. Decide and implement a versioned acoustic-event feature ontology, refit
the detector under the new feature view, and validate whether held-out acoustic
positives and ambient negatives separate **without threshold gaming**, while
preserving the sealed M7 and M8-RF evidence.

**Claim language (honesty constraint, enforced):** This block provides **TRL 3 →
4 maturation evidence**: an acoustic representation redesign validated for
improved separability under a versioned acoustic-event feature schema. It does
**not** claim full TRL 4 acoustic detection on its own. The two-source
(optical + acoustic) result established TRL 3 at M7 and is **not** at stake in
this block; the third modality (RF, M8-RF) and this representation fix are
maturation toward TRL 4. Detector calibration against a representative
operational corpus remains a carry-forward unless and until the live evaluation
demonstrates the metric cleanly under a proper fit/eval split.

---

## 2. Root cause (restated from sealed M7 diagnosis)

The M7 recall failure (Recall = 0.12, F1 = 0.214 on balanced held-out folds 4-5;
Precision = 1.00, FPR = 0.0%) was diagnosed as a **representation limitation**,
not a detector bug, threshold miscalibration, warmup artifact, or attribution
error (all ruled out at M7). The acoustic mass mapper
(`evidence.acoustic_to_mass`) projects the top YAMNet label onto the
three-hypothesis frame; acoustically-distinct positives (siren, chainsaw, train,
car_horn) and several ambient sounds collapse onto similar belief-mass vectors,
so the detector's feature vector — built only from the collapsed masses — cannot
separate them. This block routes the surviving YAMNet detail **around** the mass
collapse and onto the fused object, where the featurizer can see it directly.

Two distinct lossy steps were confirmed in the sealed code:
- **Loss #1 (`evidence.acoustic_to_mass`):** top label → one of three
  hypotheses; YAMNet score distribution and class identity discarded.
- **Loss #2 (`ade/features.extract_features`):** the ADE vector was built only
  from fused belief masses, conflict, confidence, n_modalities, entropy — the
  acoustic class detail never reached the detector even if it had survived.

The fix is additive at both points and **does not edit the sealed mass mapper or
the D-S fusion core.**

---

## 3. Decisions confirmed before code (decisions-before-code)

**A1 — Frame vs. feature separation.** The fusion frame Θ = {UAV, GROUND,
AMBIENT} is **unchanged**; no new fusion hypothesis. The fix lives in the
acoustic/ADE feature representation. The D-S combination core
(`dempster_shafer.py`) and the sealed acoustic mass mapper
(`evidence.acoustic_to_mass`, incl. its 0.85 reliability discount and
`_ACOUSTIC_LABEL_MAP`) are untouched.

**A2 — Versioning.** ADE `FEATURE_SCHEMA_VERSION` bumped `1.0.0 → 1.1.0` with a
new feature view `m9_acoustic_event_aware`. No repo-wide event `SCHEMA_VERSION`
bump (the acoustic envelope already carries `yamnet_top`; no envelope-contract
change). `FUSED_SCHEMA_VERSION` bumped `1.0.0 → 1.1.0` for the new optional
`acoustic_meta` field on `FusedObject`.

**A3 — Representation choice.** Lower-risk option: carry YAMNet top-class
confidence, entropy, and semantic class-group scores as ADE features. A dedicated
acoustic-event model/head is explicitly **deferred to TRL 4+ follow-on**, not
built in this block.

**A4 — Validation.** Success criterion is distributional separability of
held-out acoustic positives vs ambient negatives, reported honestly, compared
against the M7 failure mode using the **same** eval framing (folds 4-5,
balanced-n, seed 42). No threshold tuning to manufacture recall; `ADE_Z_THRESHOLD`
held at its M7 value.

**A5 — Refit honesty.** Fit on fit-fold data only (folds 1/2/3 ambient
negatives). Held-out folds 4/5 are never used for fit or calibration. Any
synthetic/probe set is diagnostic-only and cannot produce a model artifact.

**Four follow-on decisions (this block):**
- **D1 — `FUSED_SCHEMA_VERSION = 1.1.0`** because `acoustic_meta` is a real,
  serialized FusedObject contract addition. Sealed 1.0.0 objects remain
  reproducible at their commits; the live runtime gates on exact version
  (`ade.__main__`), so a 1.0.0 object on a live bus is skipped, not up-converted.
- **D2 — `group_scores` aggregated by MAX per group**, not sum. Max reflects
  acoustic evidence strength; sum would reward YAMNet vocabulary duplication.
- **D3 — M7 artifact reproducible only at its sealed commit**, not
  forward-compatible with 1.1.0/16-dim code. The sealed `model_io._validate_pins`
  loader is **not** modified to force old pins through; new code carries the m9
  view only. (Confirmed: the masked view operates downstream of pin validation,
  so it cannot rescue an M7 artifact against the 1.1.0 module — by design.)
- **Six-group acoustic ontology frozen before evaluation** (see §5).

**Hard leakage guard (added this block):** fitting must fail if any source
filename resolves to fold 4 or 5, or to an unparseable fold, in artifact-writing
mode. Diagnostic mode may relax the unparseable check **only** when it
structurally cannot write an artifact; a held-out fold is fatal even in
diagnostic mode.

---

## 4. What was built (new + edited code surface)

Zero changes to the D-S fusion core (`dempster_shafer.py`), the sealed acoustic
mass mapper (`evidence.acoustic_to_mass`), or the sealed `model_io` loader
contract.

| File | Change |
|---|---|
| `kanatir/core/ade/fold_guard.py` | **new** — pure ESC-50 fold parser + `assert_no_heldout` hard guard (held-out always fatal; unparseable fatal in write mode; diagnostic+write forbidden). |
| `kanatir/core/msfe/fused.py` | **edit** — `AcousticMeta` model; optional `acoustic_meta` field on `FusedObject`; `ACOUSTIC_GROUP_NAMES` frozen ontology; `FUSED_SCHEMA_VERSION → 1.1.0`. `_coherence` validator unchanged. |
| `kanatir/core/msfe/acoustic_meta.py` | **new** — `acoustic_meta_from_yamnet` helper + frozen six-group fragment map (MAX aggregation); import-time assert binds the map to `ACOUSTIC_GROUP_NAMES`. |
| `kanatir/core/msfe/fusion.py` | **edit** — `fuse_window` populates `acoustic_meta` via the same helper the fit corpus is built through (no train/serve skew); deterministic multi-acoustic tiebreak (highest top score, then lowest envelope_id). |
| `kanatir/core/ade/features.py` | **edit** — `FEATURE_SCHEMA_VERSION → 1.1.0`; 8 appended acoustic features (indices 8-15); indices 0-7 byte-identical; missing-acoustic → zeros; group read by fixed order. |
| `kanatir/core/ade/detectors/masked_view.py` | **edit** — added `M9_FEATURE_VIEW` / `M9_KEPT_INDICES` / `M9_EXCLUDED_FEATURES`; M7 constants retained unchanged for reproducibility. |
| `scripts/fit_ade.py` | **edit** — M9 view; acoustic-presence hard abort (default floor 0.8, zero-fraction always fatal); fold guard as independent second layer; refuses blind write without manifest/split provenance; refuses to overwrite `ade_isoforest_m7.joblib`; diagnostic mode cannot write. Pins flow from module globals. |
| `tests/unit/test_fold_guard.py` | **new** — 10 tests. |
| `tests/unit/test_acoustic_meta.py` | **new** — 9 tests. |
| `tests/unit/test_fused_acoustic_meta.py` | **new** — 8 tests. |
| `tests/unit/test_fusion_acoustic_meta.py` | **new** — 6 tests. |
| `tests/unit/test_features_m9.py` | **new** — 7 tests. |
| `tests/unit/test_masked_view_m9.py` | **new** — 6 tests. |
| `tests/unit/test_model_io_m9.py` | **new** — 3 tests. |

**Topics / envelope contract:** none changed. The acoustic `FeatureEnvelope`
already carried `yamnet_top`; no `SCHEMA_VERSION` bump.

---

## 5. Acoustic-event ontology (frozen before evaluation)

Six semantic groups, coarse AudioSet-lineage parents, NOT D-S hypotheses — their
purpose is to re-expose distinctions the {UAV,GROUND,AMBIENT} frame collapses.
Frozen before any eval run to prevent eval-set tuning. Group order is contractual
(featurizer binds fixed indices):

1. `siren_alarm` — siren, alarm, emergency vehicle, police car, ambulance, fire
   engine, civil defense siren, buzzer, smoke detector
2. `engine_vehicle` — engine, vehicle, car, truck, motorcycle, bus, idling,
   accelerating, motor
3. `impact_transient` — explosion, gunshot, glass, crash, bang, boom, breaking,
   shatter, thump
4. `aircraft_uav` — aircraft, helicopter, propeller, drone, fixed-wing, aircraft
   engine
5. `voice` — speech, shout, scream, yell, conversation, crowd, children shouting
6. `nature_ambient` — wind, rain, bird, insect, silence, stream, thunderstorm,
   rustling

Each group score is the **MAX** `yamnet_top` score among labels matching that
group's fragments (case-insensitive substring), in [0, 1]; all six groups are
always present (0.0 if no match).

**`aircraft_uav` is an ADE feature, not a D-S focal element** — it does not
re-introduce UAV mass and does not violate the M8-RF "RF never emits UAV mass"
invariant. The fusion frame is untouched.

**Recorded decision — chainsaw is intentionally NOT group-mapped this block.**
`chainsaw` is an ESC-50 eval positive, but adding a `mechanical_tool` group after
inspecting held-out category coverage would weaken the frozen-before-eval
provenance even with a defensible ontology rationale. Chainsaw distinctiveness is
instead carried by the scalar acoustic features (`ac_top_score`,
`ac_yamnet_entropy`): a confident YAMNet "Chainsaw" hit yields a high top score
and low entropy regardless of group membership. A future TRL 4+ ontology
expansion may add a `mechanical_tool` group **only** under a broader
pre-registered acoustic ontology, never from post-hoc eval coverage.

**Transparency note (substring matching):** a label such as "Emergency vehicle
(siren)" matches both `siren_alarm` and `engine_vehicle` (via "vehicle"). This is
expected and harmless — a siren legitimately carries vehicle character, and the
detector sees both group activations. Not a defect.

---

## 6. Train/serve path identity

The m9 fit corpus must be built through the **same** `FusedObject` construction
used by live inference:

```
fit clips (folds 1/2/3 ambient negatives)
  → FeatureEnvelope (CVP/APP; acoustic carries yamnet_top)
  → fuse_window  [populates acoustic_meta via acoustic_meta_from_yamnet]
  → FusedObject (1.1.0, acoustic_meta present)
  → normal_corpus_m9.jsonl (capture tap)
  → fit_ade.py --out models/ade/ade_isoforest_m9.joblib
```

`fit_ade.py` is structurally incapable of synthesizing FusedObjects (it reads
captured JSONL via `FusedObject.from_json`), and `fuse_window` and the fit corpus
compute `acoustic_meta` through the identical helper — so there is no offline
shortcut and no train/serve skew. The **only** way skew can re-enter is a stale
corpus: reusing the M7 `normal_corpus.jsonl` would yield 1.0.0 objects with no
`acoustic_meta`, all-zero acoustic features, and an unlearnable representation.
The acoustic-presence guard makes that a **hard fit-time abort** (zero fraction
always; below the 0.8 floor unless a documented mixed-modality value is set), so
the stale-corpus failure cannot silently produce an invalid artifact.

---

## 7. In-sandbox verification (code-level, this block)

Full unit suite for this block: **49 passed**. New/edited files **ruff clean**
on E, F, I, UP at line-length 100. Verified behaviors:

- **Schema/version:** `FUSED_SCHEMA_VERSION = 1.1.0`, `FEATURE_SCHEMA_VERSION =
  1.1.0`, `FEATURE_DIM = 16`; indices 0-7 byte-identical to M7; legacy 1.0.0
  payload parses with `acoustic_meta = None`.
- **acoustic_meta helper:** empty/None → None; MAX-not-sum proven; chainsaw →
  all-zero groups but non-zero scalar; concentrated entropy < diffuse entropy.
- **fusion:** acoustic present → meta populated; V+RF → meta None with masses
  unchanged (regression); deterministic tiebreaks (lowest envelope_id on tie;
  highest score otherwise).
- **featurizer:** missing-acoustic → zeros 8-15; group read order independent of
  dict insertion order (positional stability preserved).
- **masked view:** m9 selects exactly the 15 kept indices in order; M7 view still
  selects 7 columns from an 8-dim vector (reproducibility regression).
- **fit guards (8 paths):** clean m9 fit succeeds (acoustic_fraction = 1.0);
  stale corpus hard-aborts with the required message; fold-4 leak →
  `HeldoutLeakageError`; unparseable fold → `HeldoutLeakageError` (write mode);
  no manifest → refuse blind write; overwrite `ade_isoforest_m7.joblib` →
  refused; diagnostic on stale corpus → no abort and **no artifact written**;
  diagnostic + held-out fold → still fatal.
- **model_io seal boundary:** m9 artifact loads under 1.1.0; M7-shaped artifact
  rejected on `feature_names`; version drift rejected on `feature_schema_version`.

---

## 8. Live validation (TO BE COMPLETED on `olaberry` before close)

Procedure (apples-to-apples with M7):
1. Bring up the 1.1.0 pipeline; run `capture_normal_corpus.py` replaying **folds
   1/2/3 ambient negatives only**, with `--source-clip` enumerating the real fit
   clips so the manifest carries fold provenance. Produces
   `normal_corpus_m9.jsonl` + manifest.
2. `fit_ade.py --corpus normal_corpus_m9.jsonl --out
   models/ade/ade_isoforest_m9.joblib --manifest <manifest> --split
   datasets/.../esc50_split.json`. Acoustic-presence + fold guards must pass.
3. Run `eval_harness.py` on **held-out folds 4/5** (balanced-n, seed 42,
   `ADE_MODEL_PATH=ade_isoforest_m9.joblib`, `ADE_Z_THRESHOLD` at the M7 value).
4. Report, in order: positive vs ambient anomaly-score distributions; the
   siren-vs-ambient probe and z-reach; then the metric table (Precision, Recall,
   F1, FPR, latency) beside the M7 baseline.

**Result:** _[fill from the live run]_

**Honest-outcome statement (pre-committed):** This block may improve separability
without reaching F1 ≥ 0.75 on fit-folds-only ESC-50. That is still a valid
result — the representation root cause would be addressed and the residual would
be corpus/calibration, already a carry-forward — and it would **not** be "TRL 4
achieved." If separability does not improve, the representation hypothesis is
falsified and the dedicated acoustic-event head (A3 follow-on) becomes the next
candidate. The result is reported as found; no threshold gaming.

---

## 9. Constraints honored

- Sealed M7 (`df82e13`/`adf0d33`) and M8-RF (`172ba0c`) evidence/records **not
  modified or re-gated.**
- Fusion frame and D-S core **unchanged**; sealed acoustic mass mapper
  **untouched**; sealed `model_io` validation contract **unmodified** (M7 stays
  reproducible at its commit; no forward-loading).
- No mature acoustic threat-classification claim; representation-separability
  framing only.
- decisions-before-code followed; every in-sandbox claim test-backed; the live
  metric claim is deferred to §8 rather than asserted from code.
- TRL 3 (two-source) not re-litigated; this block is TRL 3 → 4 maturation.

---

## 10. Known limitations / carry-forward

- **Detector calibration vs a representative operational corpus** remains
  carry-forward; ESC-50 fit-folds are not that corpus. A clean F1 claim requires
  it.
- **M7 latency miss** (~6.2 s p95 vs 5 s), diagnosed at M7 as a host-native
  windowing/templating gap, is **not** addressed by this representation block and
  remains carry-forward (Jetson/Flink edge work).
- **Dedicated acoustic-event head/embedding** (A3 alternative) deferred to TRL
  4+; revisit if §8 separability is insufficient.
- **`mechanical_tool` group** (chainsaw and other power tools) deferred to a
  pre-registered TRL 4+ ontology expansion; never from post-hoc eval coverage.
- **Pre-existing lint observations** (outside this block's scope, present at the
  sealed commits, left untouched to avoid modifying sealed code):
  `kanatir/core/ade/detectors/masked_view.py:30` carries an unused
  `TYPE_CHECKING` numpy import (F401); `scripts/fit_ade.py` original argparse
  help text exceeds 100 cols on several lines (E501). Flagged for a separate
  hygiene pass at your discretion.
- **IsolationForest cold-start / RF threshold calibration** carry-forwards from
  M4 / M8-RF are unchanged by this block.

---

**Prior state referenced:** M8-RF `172ba0c`; M7 `df82e13` / `adf0d33`.
**This block closes as (pending §8):** acoustic representation redesign validated
for improved separability under a versioned acoustic-event feature schema
(`m9_acoustic_event_aware`, `FEATURE_SCHEMA_VERSION 1.1.0`,
`FUSED_SCHEMA_VERSION 1.1.0`); YAMNet class distinctiveness carried past the D-S
mass collapse via an additive optional `acoustic_meta`; fusion frame and core
untouched; sealed M7/M8-RF preserved; fit/eval fold disjointness enforced by a
hard leakage guard and an acoustic-presence guard. Detector calibration against a
representative corpus remains a carry-forward.
