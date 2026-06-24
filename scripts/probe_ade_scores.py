"""
probe_ade_scores.py — M7 diagnostic: score FusedObject corpora through the
FITTED ADE model and report the raw detector score, the combined scalar (what
the baseline z-scores), and — given an ambient baseline corpus — the z-score
each object WOULD produce. This answers, offline and in seconds, the one
question that decides whether the eval is worth running:

    Does the fitted masked-view IsolationForest score positive (e.g. siren)
    FusedObjects meaningfully differently from ambient ones?

If positives sit >= z_threshold (default 3.0) off the ambient mean, the
pre-warm + eval path will work. If positive and ambient score distributions
overlap, no pre-warming helps — the detector isn't discriminating and that must
be fixed before any benchmark.

Loads the fitted detector via model_io (same path ADE uses), so the masked
feature view is applied identically. Computes the combined scalar exactly as
AnomalyEnsemble._combine does (detector_mean blended with conflict_k at 0.5).

Run:
    python3 scripts/probe_ade_scores.py \
        --model models/ade/ade_isoforest_m7.joblib \
        --baseline datasets/ade_fit_corpus/normal_corpus.jsonl \
        --probe ambient:datasets/eval/probe_ambient.jsonl \
        --probe siren:datasets/eval/probe_siren.jsonl \
        --z-threshold 3.0
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from kanatir.core.ade.features import extract_features
from kanatir.core.ade.model_io import load_fitted_ensemble
from kanatir.core.msfe.fused import FusedObject

_CONFLICT_BLEND = 0.5  # mirror ensemble._CONFLICT_BLEND


def _combined_scalar(detector_score: float, conflict_k: float) -> float:
    # single detector -> detector_mean == its score
    return (1.0 - _CONFLICT_BLEND) * detector_score + _CONFLICT_BLEND * conflict_k


def _load_objs(path: Path) -> list[FusedObject]:
    objs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            objs.append(FusedObject.from_json(line))
    return objs


def _score_set(detector, objs: list[FusedObject]) -> list[dict]:
    out = []
    for o in objs:
        feats = extract_features(o)
        raw = float(detector.score(feats))
        ck = float(o.belief.conflict_k)
        out.append({"raw": raw, "combined": _combined_scalar(raw, ck), "conflict_k": ck})
    return out


def _summ(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "min": round(min(vals), 5),
        "mean": round(statistics.mean(vals), 5),
        "max": round(max(vals), 5),
        "std": round(statistics.pstdev(vals), 5) if len(vals) > 1 else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Probe fitted ADE score discrimination.")
    ap.add_argument("--model", required=True, type=Path)
    ap.add_argument("--baseline", required=True, type=Path,
                    help="Ambient corpus JSONL defining the 'normal' combined-scalar distribution.")
    ap.add_argument("--probe", action="append", default=[], required=True,
                    help="Repeatable 'label:path.jsonl' set to score against the baseline.")
    ap.add_argument("--z-threshold", type=float, default=3.0)
    args = ap.parse_args(argv)

    lm = load_fitted_ensemble(str(args.model))
    detector = lm.ensemble.ready_detectors[0]
    print(f"model loaded: detector={detector.name} feature_view="
          f"{getattr(detector, 'feature_view', 'n/a')} n_samples={lm.n_samples}")

    base_objs = _load_objs(args.baseline)
    base_scored = _score_set(detector, base_objs)
    base_combined = [s["combined"] for s in base_scored]
    base_mean = statistics.mean(base_combined)
    base_std = statistics.pstdev(base_combined) if len(base_combined) > 1 else 0.0

    print("\n=== BASELINE (ambient corpus) ===")
    print(f"  raw IsoForest score: {_summ([s['raw'] for s in base_scored])}")
    print(f"  combined scalar:     {_summ(base_combined)}")
    print(f"  baseline mean={base_mean:.5f} std={base_std:.5f}")

    def z_of(v: float) -> float:
        if base_std == 0.0:
            return 0.0 if v == base_mean else float("inf")
        return abs(v - base_mean) / base_std

    print(f"\n=== PROBES (z vs ambient baseline; z_threshold={args.z_threshold}) ===")
    overall_ok = True
    for spec in args.probe:
        label, path_str = spec.split(":", 1)
        objs = _load_objs(Path(path_str))
        scored = _score_set(detector, objs)
        combined = [s["combined"] for s in scored]
        zs = [z_of(v) for v in combined]
        n_would_flag = sum(1 for z in zs if z >= args.z_threshold)
        print(f"\n  [{label}] n={len(scored)} file={path_str}")
        print(f"    raw IsoForest:  {_summ([s['raw'] for s in scored])}")
        print(f"    combined:       {_summ(combined)}")
        print(f"    z vs baseline:  {_summ(zs)}")
        print(f"    would-flag (z>={args.z_threshold}): {n_would_flag}/{len(scored)}")
        # crude discrimination read
        if label.lower().startswith(("siren", "pos", "helicopter", "engine", "chainsaw")):
            frac = n_would_flag / len(scored) if scored else 0.0
            verdict = "DISCRIMINATES" if frac >= 0.5 else ("WEAK" if frac > 0 else "NO SIGNAL")
            print(f"    -> positive-set discrimination: {verdict} ({frac:.0%} would flag)")
            if frac < 0.5:
                overall_ok = False

    print("\n=== READ ===")
    if overall_ok:
        print("Positive sets separate from ambient at z_threshold. Pre-warm + eval is viable.")
    else:
        print("Positive sets do NOT clearly separate from ambient. Investigate detector/feature "
              "view BEFORE running the benchmark — pre-warming will not fix non-discrimination.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
