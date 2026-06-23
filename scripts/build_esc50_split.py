"""
build_esc50_split.py — M7 / TRL-3: read ESC-50 meta once and emit the single,
authoritative fit/eval split. Every downstream step (capture, fit guard, eval
harness) reads THIS file — never the CSV directly — so the split is defined in
exactly one auditable place and leakage is prevented by construction.

Split policy (esc50_fold_based_disjoint):
  - fit_negatives   : ambient-negative categories, folds 1-3   (fit corpus)
  - eval_negatives  : ambient-negative categories, folds 4-5   (held-out FPR)
  - eval_positives  : positive categories,          folds 4-5  (held-out detection)
No clip appears in more than one role (folds are disjoint by construction).

Run:
    python3 scripts/build_esc50_split.py \
        --meta datasets/ESC-50/meta/esc50.csv \
        --out  datasets/ade_fit_corpus/esc50_split.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

AMBIENT_NEGATIVES = (
    "rain", "wind", "crickets", "sea_waves", "insects",
    "chirping_birds", "frog", "water_drops", "pouring_water", "thunderstorm",
)
ACOUSTIC_POSITIVES = (
    "helicopter", "siren", "engine", "chainsaw", "airplane", "car_horn", "train",
)
FIT_FOLDS = (1, 2, 3)
EVAL_FOLDS = (4, 5)
SPLIT_POLICY = "esc50_fold_based_disjoint"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the ESC-50 fit/eval split file.")
    ap.add_argument("--meta", required=True, type=Path, help="datasets/ESC-50/meta/esc50.csv")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    if not args.meta.exists():
        raise FileNotFoundError(f"meta csv not found: {args.meta}")

    fit_negatives: list[dict] = []
    eval_negatives: list[dict] = []
    eval_positives: list[dict] = []

    with args.meta.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            fn = row["filename"]
            cat = row["category"]
            try:
                fold = int(row["fold"])
            except (KeyError, ValueError):
                continue
            entry = {"filename": fn, "category": cat, "fold": fold}
            if cat in AMBIENT_NEGATIVES and fold in FIT_FOLDS:
                fit_negatives.append(entry)
            elif cat in AMBIENT_NEGATIVES and fold in EVAL_FOLDS:
                eval_negatives.append(entry)
            elif cat in ACOUSTIC_POSITIVES and fold in EVAL_FOLDS:
                eval_positives.append(entry)
            # positives in fit folds and 'other' categories are intentionally unused

    for lst in (fit_negatives, eval_negatives, eval_positives):
        lst.sort(key=lambda e: e["filename"])

    # Leakage assertion: the three role sets must be pairwise disjoint by filename.
    s_fit = {e["filename"] for e in fit_negatives}
    s_en = {e["filename"] for e in eval_negatives}
    s_ep = {e["filename"] for e in eval_positives}
    overlap = (s_fit & s_en) | (s_fit & s_ep) | (s_en & s_ep)
    if overlap:
        raise RuntimeError(f"split leakage: clips in multiple roles: {sorted(overlap)[:5]}")

    def _role_hash(entries: list[dict]) -> str:
        blob = "\n".join(e["filename"] for e in entries)
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    split = {
        "split_policy": SPLIT_POLICY,
        "fit_folds": list(FIT_FOLDS),
        "eval_folds": list(EVAL_FOLDS),
        "ambient_negative_categories": list(AMBIENT_NEGATIVES),
        "acoustic_positive_categories": list(ACOUSTIC_POSITIVES),
        "fit_negatives": fit_negatives,
        "eval_negatives": eval_negatives,
        "eval_positives": eval_positives,
        "counts": {
            "fit_negatives": len(fit_negatives),
            "eval_negatives": len(eval_negatives),
            "eval_positives": len(eval_positives),
        },
        "role_hashes": {
            "fit_negatives": _role_hash(fit_negatives),
            "eval_negatives": _role_hash(eval_negatives),
            "eval_positives": _role_hash(eval_positives),
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(split, indent=2) + "\n", encoding="utf-8")

    print(
        f"split.done policy={SPLIT_POLICY} "
        f"fit_neg={len(fit_negatives)} eval_neg={len(eval_negatives)} "
        f"eval_pos={len(eval_positives)} out={args.out}"
    )
    if not fit_negatives:
        print("WARNING: no fit negatives selected — check categories/folds in the CSV.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
