"""
diagnose_yamnet_labels.py — M7 diagnostic: run APP's EXACT YAMNet path on sample
clips from each held-out positive category and tabulate the top labels YAMNet
actually emits, the current mapper result, and the score distribution.

Purpose: before extending _ACOUSTIC_LABEL_MAP, confirm WHAT YAMNet labels these
clips as. Only labels YAMNet actually emits, with a defensible operational
interpretation, should be added. This is verification, not tuning.

Mirrors kanatir/pipelines/app/__main__.py YAMNet scoring exactly:
  model = hub.load("https://tfhub.dev/google/yamnet/1")
  scores = model(wave); mean = np.mean(scores, axis=0); top5 = argsort[::-1][:5]

Also applies the CURRENT acoustic mapper (evidence.acoustic_to_mass logic) to the
observed top label so the table shows current-result vs what-it-should-be.

Run (on the Mac with tensorflow_hub + librosa; the stack need NOT be up):
    python3 scripts/diagnose_yamnet_labels.py \
        --split datasets/ade_fit_corpus/esc50_split.json \
        --audio-dir datasets/ESC-50/audio \
        --per-category 4 \
        --out datasets/eval/yamnet_label_diagnostic.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# Categories to diagnose (held-out positives + a couple negatives for contrast).
POSITIVE_CATEGORIES = (
    "siren", "car_horn", "chainsaw", "train", "helicopter", "airplane", "engine",
)
NEGATIVE_CONTRAST = ("rain", "wind", "crickets", "sea_waves")

# Mirror of evidence._ACOUSTIC_LABEL_MAP (current state) for the "current result" column.
_CURRENT_MAP = (
    ("drone", "UAV"), ("aircraft", "UAV"), ("propeller", "UAV"), ("helicopter", "UAV"),
    ("vehicle", "GROUND"), ("engine", "GROUND"), ("car", "GROUND"), ("truck", "GROUND"),
    ("footsteps", "GROUND"), ("speech", "GROUND"),
    ("wind", "AMBIENT"), ("silence", "AMBIENT"), ("rain", "AMBIENT"), ("bird", "AMBIENT"),
)


def _current_hyp(label: str) -> str:
    low = label.lower()
    for frag, h in _CURRENT_MAP:
        if frag in low:
            return h
    return "UNKNOWN(vacuous)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tabulate actual YAMNet labels per ESC-50 category.")
    ap.add_argument("--split", required=True, type=Path)
    ap.add_argument("--audio-dir", required=True, type=Path)
    ap.add_argument("--per-category", type=int, default=4)
    ap.add_argument("--window-s", type=float, default=0.96)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    import numpy as np
    import librosa
    import tensorflow_hub as hub

    print("loading YAMNet (same as APP)...", file=sys.stderr)
    model = hub.load("https://tfhub.dev/google/yamnet/1")
    class_map_path = model.class_map_path().numpy().decode("utf-8")
    import csv as _csv
    with open(class_map_path) as fh:
        class_names = [row["display_name"] for row in _csv.DictReader(fh)]

    split = json.loads(args.split.read_text(encoding="utf-8"))
    # gather clips by category from the whole split (eval + would-be positives)
    by_cat: dict[str, list[str]] = defaultdict(list)
    for role in ("eval_positives", "eval_negatives", "fit_negatives"):
        for e in split.get(role, []):
            by_cat[e["category"]].append(e["filename"])

    want = list(POSITIVE_CATEGORIES) + list(NEGATIVE_CONTRAST)
    report = []
    for cat in want:
        clips = sorted(by_cat.get(cat, []))[:args.per_category]
        if not clips:
            report.append({"category": cat, "note": "no clips in split for this category"})
            continue
        label_counter: dict[str, list[float]] = defaultdict(list)
        per_clip = []
        for fn in clips:
            wav = args.audio_dir / fn
            if not wav.exists():
                continue
            y, sr = librosa.load(str(wav), sr=16000, mono=True)  # YAMNet wants 16k mono
            scores, _embeddings, _spec = model(y)  # exact APP call signature
            mean_scores = np.mean(scores.numpy(), axis=0)
            top_idx = np.argsort(mean_scores)[::-1][:3]
            top = [(class_names[i], float(mean_scores[i])) for i in top_idx]
            for lbl, sc in top:
                label_counter[lbl].append(sc)
            best_label, best_score = top[0]
            per_clip.append({"clip": fn, "top3": top,
                             "current_hyp": _current_hyp(best_label)})
        # aggregate
        agg = sorted(((lbl, len(v), round(sum(v) / len(v), 4), round(max(v), 4))
                      for lbl, v in label_counter.items()),
                     key=lambda t: -t[2])[:6]
        report.append({
            "category": cat,
            "n_clips": len(per_clip),
            "top_labels_observed": [{"label": l, "n": n, "mean": m, "max": mx} for l, n, m, mx in agg],
            "per_clip": per_clip,
        })
        print(f"\n[{cat}] n={len(per_clip)}")
        for l, n, m, mx in agg:
            print(f"    {l:40s} n={n} mean={m} max={mx}")
        if per_clip:
            print(f"    current mapper -> {per_clip[0]['current_hyp']} (from top label '{per_clip[0]['top3'][0][0]}')")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\ndiagnostic written: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
