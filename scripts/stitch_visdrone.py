"""
stitch_visdrone.py — M7 / TRL-3: stitch VisDrone val still images into an .mp4
for replay through CVP (which wants a stream, not stills). CVP itself is NOT
modified — that is the pipeline being validated. ffmpeg does the encode.

VisDrone annotations (VisDrone2019-DET comma format, per image, in annotations/):
    x,y,w,h,score,class,trunc,occ
A frame's object count = number of annotation lines with score>0 (ignored
regions have score 0). Selection modes:
  --mode normal   : sparse/empty frames (object_count <= --normal-max-objects),
                    the NORMAL-video side of the fit corpus / negatives.
  --mode positive : frames WITH target objects (object_count >= --positive-min),
                    the POSITIVE side of the eval set.

Records the exact frame list + fps + per-frame object counts to a sidecar so the
report can cite how the mp4 was built and which frames are normal vs positive.

Run:
    python3 scripts/stitch_visdrone.py \
        --visdrone-root datasets/VisDrone/VisDrone2019-DET-val \
        --mode normal --fps 5 --max-frames 600 \
        --out datasets/visdrone_normal.mp4
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def _object_count(ann_path: Path) -> int:
    if not ann_path.exists():
        return 0
    n = 0
    for line in ann_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 6:
            try:
                score = int(parts[4])
            except ValueError:
                continue
            if score > 0:
                n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stitch VisDrone val stills into an mp4 for CVP replay.")
    ap.add_argument("--visdrone-root", required=True, type=Path,
                    help="dir containing images/ and annotations/")
    ap.add_argument("--mode", choices=["normal", "positive"], required=True)
    ap.add_argument("--fps", type=int, default=5)
    ap.add_argument("--max-frames", type=int, default=600)
    ap.add_argument("--normal-max-objects", type=int, default=0,
                    help="normal mode: keep frames with <= this many objects")
    ap.add_argument("--positive-min", type=int, default=1,
                    help="positive mode: keep frames with >= this many objects")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    images_dir = args.visdrone_root / "images"
    ann_dir = args.visdrone_root / "annotations"
    if not images_dir.is_dir():
        raise FileNotFoundError(f"images dir not found: {images_dir}")

    images = sorted(images_dir.glob("*.jpg"))
    if not images:
        raise FileNotFoundError(f"no .jpg images under {images_dir}")

    selected: list[tuple[Path, int]] = []
    for img in images:
        oc = _object_count(ann_dir / (img.stem + ".txt"))
        keep = (oc <= args.normal_max_objects) if args.mode == "normal" else (oc >= args.positive_min)
        if keep:
            selected.append((img, oc))
        if len(selected) >= args.max_frames:
            break

    if not selected:
        raise ValueError(f"no frames matched mode={args.mode}; adjust thresholds")

    args.out.parent.mkdir(parents=True, exist_ok=True)

    # ffmpeg concat demuxer over the selected frames at fixed fps.
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as listf:
        for img, _ in selected:
            listf.write(f"file '{img.resolve()}'\n")
            listf.write(f"duration {1.0/args.fps:.6f}\n")
        # concat demuxer needs the last file repeated without duration
        listf.write(f"file '{selected[-1][0].resolve()}'\n")
        list_path = listf.name

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-vsync", "vfr", "-pix_fmt", "yuv420p", "-r", str(args.fps), str(args.out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr[-2000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed (exit {proc.returncode})")

    sidecar = {
        "mode": args.mode,
        "fps": args.fps,
        "n_frames": len(selected),
        "out": str(args.out),
        "frames": [{"file": str(p.name), "object_count": oc} for p, oc in selected],
    }
    side_path = args.out.with_suffix(args.out.suffix + ".frames.json")
    side_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")

    print(f"stitch.done mode={args.mode} frames={len(selected)} fps={args.fps} "
          f"out={args.out} sidecar={side_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
