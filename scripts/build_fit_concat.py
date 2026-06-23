"""
build_fit_concat.py — M7 / TRL-3: from the ESC-50 split file, build an ffmpeg
concat list of the fit-fold (folds 1-3) ambient-negative wavs and concatenate
them into one long wav for a single continuous APP replay (Decision 1: one
YAMNet load, not 240 process spawns).

Records every source clip + sha256 and the output wav sha256 into a provenance
JSON, so the capture manifest and report can cite exactly which clips fed the
fit corpus. Refuses to include any clip not in the split's fit_negatives — the
concat cannot accidentally pull an eval clip.

Run:
    python3 scripts/build_fit_concat.py \
        --split datasets/ade_fit_corpus/esc50_split.json \
        --audio-dir datasets/ESC-50/audio \
        --out-wav datasets/ade_fit_corpus/fit_ambient_concat.wav \
        --out-provenance datasets/ade_fit_corpus/fit_concat_provenance.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Concatenate fit-fold ambient wavs for APP replay.")
    ap.add_argument("--split", required=True, type=Path)
    ap.add_argument("--audio-dir", required=True, type=Path)
    ap.add_argument("--out-wav", required=True, type=Path)
    ap.add_argument("--out-provenance", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=None,
                    help="Optional cap on number of clips (e.g. for a quick smaller capture).")
    args = ap.parse_args(argv)

    split = json.loads(args.split.read_text(encoding="utf-8"))
    fit = split.get("fit_negatives", [])
    if args.limit:
        fit = fit[:args.limit]
    if not fit:
        raise ValueError("no fit_negatives in split file")

    clips = []
    missing = []
    for e in fit:
        p = args.audio_dir / e["filename"]
        if not p.exists():
            missing.append(e["filename"])
            continue
        clips.append({"filename": e["filename"], "category": e["category"],
                      "fold": e["fold"], "path": str(p), "sha256": _sha256(p)})
    if missing:
        print(f"WARNING: {len(missing)} fit clips not found on disk (skipped): {missing[:5]}",
              file=sys.stderr)
    if not clips:
        raise ValueError("no fit clips found on disk")

    args.out_wav.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as listf:
        for c in clips:
            listf.write(f"file '{Path(c['path']).resolve()}'\n")
        list_path = listf.name

    # Concat demuxer -> re-encode to a uniform PCM wav (ESC-50 is already 44.1k/16-bit
    # mono-ish, but re-encoding guarantees a clean uniform stream APP/librosa reads).
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
           "-ar", "44100", "-ac", "1", str(args.out_wav)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr[-2000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg concat failed (exit {proc.returncode})")

    provenance = {
        "split_policy": split.get("split_policy"),
        "fit_folds": split.get("fit_folds"),
        "n_clips": len(clips),
        "out_wav": str(args.out_wav),
        "out_wav_sha256": _sha256(args.out_wav),
        "clips": clips,
    }
    args.out_provenance.parent.mkdir(parents=True, exist_ok=True)
    args.out_provenance.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")

    print(f"concat.done n_clips={len(clips)} out={args.out_wav} "
          f"sha256={provenance['out_wav_sha256'][:16]} provenance={args.out_provenance}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
