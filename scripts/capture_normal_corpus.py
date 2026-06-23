"""
capture_normal_corpus.py — M7 / TRL-3: tap the LIVE fused.objects topic and
serialize captured normal/baseline FusedObjects into the ADE fit corpus
(JSONL) plus a provenance manifest.

This is the A1 capture path: the fit corpus is REAL FusedObjects emitted by the
live UDIH -> APP/CVP -> MSFE pipeline on ambient/normal media — never synthetic
objects constructed in code. The operator runs this tap, then (concurrently)
replays ambient audio into APP and normal video into CVP; this script collects
what MSFE fuses until the target sample count is reached, then stops.

Mirrors ADE's consumer exactly: confluent_kafka Consumer, FusedObject.from_json
on msg.value(), FusedObject.to_json() to write each JSONL line — so the corpus
round-trips losslessly into fit_ade.py (which reads with the same from_json).

PROVENANCE (manifest, written beside the corpus):
  - source clips (filenames + sha256), sensor IDs
  - MSFE_WINDOW_S, APP --max-windows, CVP --max-frames (passed in by the operator
    so the manifest records the exact capture config)
  - capture_started / capture_ended timestamps
  - n_samples actually captured, n_modalities distribution seen
  - source_corpus_id = sha256 over the sorted (clip, sha256) list + capture config
  - object_corpus_id = sha256 over the sorted captured fused_ids

Run (AFTER docker compose up -d and MSFE started, BEFORE launching APP/CVP):
    python3 scripts/capture_normal_corpus.py \
        --out datasets/ade_fit_corpus/normal_corpus.jsonl \
        --target 200 \
        --bootstrap localhost:9092 \
        --msfe-window-s 30.0 \
        --app-max-windows 200 --cvp-max-frames 600 \
        --source-clip "datasets/ESC-50/audio/1-12345-A-10.wav:rain" \
        --source-clip "datasets/visdrone_normal.mp4:visdrone_sparse" \
        --idle-timeout-s 120
"""

from __future__ import annotations

import argparse
import hashlib
import json
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from kanatir.core.msfe.fused import FusedObject


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_source_clips(specs: list[str]) -> list[dict]:
    """Each spec is 'path:label'. Records path, label, sha256, exists."""
    out = []
    for spec in specs:
        if ":" in spec:
            path_str, label = spec.rsplit(":", 1)
        else:
            path_str, label = spec, "unlabeled"
        p = Path(path_str)
        entry = {"path": str(p), "label": label, "exists": p.exists()}
        if p.exists():
            entry["sha256"] = _sha256_file(p)
        out.append(entry)
    return out


def _source_corpus_id(source_clips: list[dict], config: dict) -> str:
    parts = sorted(f"{c['path']}|{c.get('sha256','MISSING')}" for c in source_clips)
    config_blob = json.dumps(config, sort_keys=True)
    digest = hashlib.sha256(("\n".join(parts) + "\n" + config_blob).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:32]}"


def _object_corpus_id(fused_ids: list[str]) -> str:
    digest = hashlib.sha256("\n".join(sorted(fused_ids)).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:32]}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tap fused.objects into the ADE normal fit corpus.")
    ap.add_argument("--out", required=True, type=Path, help="Output JSONL corpus path.")
    ap.add_argument("--target", type=int, default=200, help="Stop after this many FusedObjects.")
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--topic", default="fused.objects")
    ap.add_argument("--group", default="ade_corpus_capture")
    ap.add_argument("--idle-timeout-s", type=float, default=120.0,
                    help="Stop if no new FusedObject arrives for this long (prevents a hung tap).")
    # Provenance — recorded verbatim into the manifest, not used for control flow.
    ap.add_argument("--msfe-window-s", type=float, default=None)
    ap.add_argument("--app-max-windows", type=int, default=None)
    ap.add_argument("--cvp-max-frames", type=int, default=None)
    ap.add_argument("--source-clip", action="append", default=[],
                    help="Repeatable 'path:label' of a source clip fed into APP/CVP this capture.")
    args = ap.parse_args(argv)

    from confluent_kafka import Consumer

    source_clips = _parse_source_clips(args.source_clip)
    missing = [c["path"] for c in source_clips if not c["exists"]]
    if missing:
        print(f"WARNING: source clips not found (recorded as MISSING): {missing}", file=sys.stderr)

    capture_config = {
        "msfe_window_s": args.msfe_window_s,
        "app_max_windows": args.app_max_windows,
        "cvp_max_frames": args.cvp_max_frames,
        "target": args.target,
        "topic": args.topic,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)

    consumer = Consumer({
        "bootstrap.servers": args.bootstrap,
        "group.id": args.group,
        "auto.offset.reset": "latest",  # downstream-first: tap subscribes before producers
        "enable.auto.commit": True,
    })
    consumer.subscribe([args.topic])

    running = True

    def _stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    captured: list[FusedObject] = []
    modality_counts: dict[int, int] = {}
    started = datetime.now(UTC)
    last_rx = time.monotonic()

    print(f"capture.start topic={args.topic} target={args.target} bootstrap={args.bootstrap}")
    print("Subscribed. Now launch APP (ambient wav) + CVP (normal mp4) concurrently.")

    with args.out.open("w", encoding="utf-8") as fh:
        while running and len(captured) < args.target:
            msg = consumer.poll(1.0)
            if msg is None:
                if time.monotonic() - last_rx > args.idle_timeout_s:
                    print(f"capture.idle_timeout after {args.idle_timeout_s}s with no new objects; stopping.")
                    break
                continue
            if msg.error():
                print(f"capture.kafka_error {msg.error()}", file=sys.stderr)
                continue
            try:
                obj = FusedObject.from_json(msg.value())
            except Exception as exc:  # noqa: BLE001
                print(f"capture.decode_skip {exc}", file=sys.stderr)
                continue

            fh.write(obj.to_json() + "\n")
            fh.flush()
            captured.append(obj)
            nm = int(getattr(obj, "n_modalities", 1))
            modality_counts[nm] = modality_counts.get(nm, 0) + 1
            last_rx = time.monotonic()
            if len(captured) % 25 == 0:
                print(f"capture.progress n={len(captured)}/{args.target} modality_dist={modality_counts}")

    consumer.close()
    ended = datetime.now(UTC)

    fused_ids = [o.fused_id for o in captured]
    manifest = {
        "corpus_path": str(args.out),
        "n_samples": len(captured),
        "n_modalities_seen": modality_counts,
        "source_clips": source_clips,
        "sensor_capture_config": capture_config,
        "msfe_window_s": args.msfe_window_s,
        "capture_started": started.isoformat(),
        "capture_ended": ended.isoformat(),
        "object_corpus_id": _object_corpus_id(fused_ids),
        "source_corpus_id": _source_corpus_id(source_clips, capture_config),
    }
    manifest_path = args.out.with_suffix(args.out.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(
        f"capture.done n={len(captured)} corpus={args.out} manifest={manifest_path} "
        f"modality_dist={modality_counts} source_corpus_id={manifest['source_corpus_id']}"
    )
    if len(captured) < args.target:
        print(f"NOTE: captured {len(captured)} < target {args.target}. "
              f"Replay more ambient media or lower --target. Corpus is still valid at this size.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
