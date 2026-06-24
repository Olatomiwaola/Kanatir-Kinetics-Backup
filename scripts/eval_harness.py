"""
eval_harness.py — M7 / TRL-3: per-clip bounded replay of the held-out ESC-50
eval set through the LIVE fitted pipeline, attributing ExplainedAlerts to clips
by replay-window overlap, and computing F1 / FPR / latency against the TRL-3
thresholds.

Attribution (Option A — per-clip bounded replay):
  - Replay ONE eval clip at a time through APP, recording replay_start_ts /
    replay_end_ts (wall clock, monotonic-anchored to UTC).
  - A background consumer taps alerts.explained for the whole run, timestamping
    every ExplainedAlert by its window_start/window_end (capture/ingest lineage).
  - After all replays, attribute an alert to a clip if the alert's window overlaps
    [replay_start - pre_margin_s, replay_end + post_margin_s].
  - Alert "present" for a clip = at least one attributed ExplainedAlert with
    severity rank >= WATCH (rank: INFO=0, WATCH=1, ALERT=2).

Scoring (alert-presence, not classification-match — the honest TRL-3 bar):
  positive + alert present = TP ; positive + none = FN
  negative + alert present = FP ; negative + none = TN

Outputs:
  - <out>.perclip.jsonl : one record per clip (full attribution + outcome)
  - <out>.summary.json  : precision, recall, F1, FPR, latency stats, PASS/FAIL

This is the gate measurement. It does not modify the pipeline; it drives APP and
reads alerts.explained, exactly as an operator would. Margins default pre=1s,
post=5s per the eval design.

Run (docker stack up; MSFE, ADE [ADE_MODEL_PATH set], CSAT, XAI all running):
    python3 scripts/eval_harness.py \
        --split datasets/ade_fit_corpus/esc50_split.json \
        --audio-dir datasets/ESC-50/audio \
        --out datasets/eval/m7_eval \
        --bootstrap localhost:9092 \
        --window-s 0.96 --pre-margin-s 1 --post-margin-s 5 \
        --f1-threshold 0.75 --fpr-threshold 0.15 --latency-threshold-s 5
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path


# ---- severity ranking (mirrors kanatir/core/csat/triage.py) ----
def _severity_rank(sev: str) -> int:
    return {"INFO": 0, "WATCH": 1, "ALERT": 2}.get(str(sev).upper(), 0)


ALERT_PRESENT_MIN_RANK = 1  # >= WATCH


# ---- background alerts.explained collector ----
class AlertCollector:
    """Taps alerts.explained for the whole run, recording each ExplainedAlert's
    window + severity + arrival time."""

    def __init__(self, bootstrap: str, topic: str = "alerts.explained",
                 group: str | None = None) -> None:
        self.bootstrap = bootstrap
        self.topic = topic
        self.group = group or f"m7_eval_{uuid.uuid4().hex[:8]}"
        self._alerts: list[dict] = []
        self._active_seen = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        from confluent_kafka import Consumer
        from kanatir.core.xai.explained import ExplainedAlert

        consumer = Consumer({
            "bootstrap.servers": self.bootstrap,
            "group.id": self.group,
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
            # Harden against group-coordinator session timeouts on long runs with
            # quiet stretches (inter-clip gaps). Without these, the consumer's
            # session times out and rejoins, delaying alert ARRIVAL at the tap and
            # contaminating latency measurement (observed as SESSTMOUT).
            "session.timeout.ms": 300000,
            "max.poll.interval.ms": 600000,
            "heartbeat.interval.ms": 10000,
        })
        consumer.subscribe([self.topic])
        try:
            while not self._stop.is_set():
                msg = consumer.poll(0.5)
                if msg is None or msg.error():
                    continue
                try:
                    ea = ExplainedAlert.from_json(msg.value())
                except Exception:  # noqa: BLE001
                    continue
                self._alerts.append({
                    "explained_id": ea.explained_id,
                    "alert_id": ea.alert_id,
                    "severity": str(ea.severity),
                    "severity_rank": _severity_rank(str(ea.severity)),
                    "baseline_state": str(ea.baseline_state),
                    "anomaly_score": float(ea.anomaly_score),
                    "window_start": ea.window_start.timestamp(),
                    "window_end": ea.window_end.timestamp(),
                    "arrival_ts": time.time(),
                    "audit_event_ids": list(ea.audit_event_ids),
                })
                if str(ea.baseline_state).upper().endswith("ACTIVE"):
                    self._active_seen = True
        finally:
            consumer.close()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> list[dict]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        return self._alerts


def _overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return a_start <= b_end and b_start <= a_end


def _alerts_shared_across_clips(perclip: list[dict]) -> list[str]:
    """Return explained_ids attributed to MORE THAN ONE clip. A non-empty list
    means CSAT grouped clips together (separation failed) — the smoke run must
    show this empty before the full benchmark."""
    seen: dict[str, set[str]] = {}
    for rec in perclip:
        for eid in rec.get("attributed_explained_ids", []):
            seen.setdefault(eid, set()).add(rec["clip_id"])
    return sorted(eid for eid, clips in seen.items() if len(clips) > 1)


def _replay_clip(args, wav: Path, sensor_id: str, site_id: str) -> tuple[float, float, int]:
    """Replay one wav through APP, return (replay_start_ts, replay_end_ts, rc)."""
    start = datetime.now(UTC).timestamp()
    proc = subprocess.run(
        ["python3", "-m", "kanatir.pipelines.app",
         "--source", str(wav), "--sensor-id", sensor_id,
         "--window-s", str(args.window_s), "--site-id", site_id],
        capture_output=True, text=True,
        env={**_env(), "KAFKA_BOOTSTRAP": args.bootstrap},
    )
    return start, datetime.now(UTC).timestamp(), proc.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="M7 TRL-3 eval harness (per-clip bounded replay).")
    ap.add_argument("--split", required=True, type=Path)
    ap.add_argument("--audio-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path, help="Output stem (no extension).")
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--window-s", type=float, default=0.96)
    ap.add_argument("--pre-margin-s", type=float, default=1.0)
    ap.add_argument("--post-margin-s", type=float, default=5.0)
    ap.add_argument("--settle-s", type=float, default=6.0,
                    help="Wait after the last clip so trailing alerts arrive before stopping the tap.")
    ap.add_argument("--limit", type=int, default=None, help="Cap clips (for a quick smoke run).")
    ap.add_argument("--balanced-n", type=int, default=None,
                    help="Balanced medium subset: draw N/2 positives + N/2 negatives from folds 4-5 "
                         "(deterministic, --sample-seed). Overrides --limit. Use for the report subset.")
    ap.add_argument("--sample-seed", type=int, default=42,
                    help="Seed for the balanced draw (reproducibility).")
    ap.add_argument("--f1-threshold", type=float, default=0.75)
    ap.add_argument("--fpr-threshold", type=float, default=0.15)
    ap.add_argument("--latency-threshold-s", type=float, default=5.0)
    ap.add_argument("--sensor-id", default="file-acoustic-eval")
    ap.add_argument("--inter-clip-gap-s", type=float, default=10.0,
                    help="Sleep between clips, MUST exceed CSAT dedup/grouping window + drain "
                         "so CSAT cannot group adjacent eval clips into one alert. Default 10s "
                         "(safe for a 5s dedup window).")
    ap.add_argument("--csat-dedup-window-s", type=float, default=None,
                    help="Recorded in eval metadata for provenance (the CSAT_DEDUP_WINDOW_S the "
                         "pipeline ran with). Does not control behavior; documents the run.")
    ap.add_argument("--prewarm-n", type=int, default=40,
                    help="Number of fit-fold ambient clips to replay BEFORE scored eval clips, to "
                         "push ADE's baseline out of WARMUP. Must exceed ADE_BASELINE_WARMUP (default 30). "
                         "Pre-warm clips are fit-fold ambient only (disjoint from eval), tagged "
                         "phase=prewarm scored=false, and excluded from all metrics.")
    ap.add_argument("--prewarm-gap-s", type=float, default=0.0,
                    help="Gap between pre-warm clips. 0 = back-to-back (we WANT them to flow fast to "
                         "fill the baseline; separation only matters for scored eval clips).")
    args = ap.parse_args(argv)

    split = json.loads(args.split.read_text(encoding="utf-8"))
    pos = [{**e, "expected_label": "positive"} for e in split.get("eval_positives", [])]
    neg = [{**e, "expected_label": "negative"} for e in split.get("eval_negatives", [])]
    pos.sort(key=lambda c: c["filename"])
    neg.sort(key=lambda c: c["filename"])

    if args.balanced_n:
        # Deterministic balanced draw from folds 4-5: half positives, half negatives,
        # evenly spaced through each sorted list (a fixed, reproducible sample, not
        # the first-N-alphabetical which would skew by filename).
        import random
        rng = random.Random(args.sample_seed)
        half = args.balanced_n // 2
        pos_s = sorted(rng.sample(pos, min(half, len(pos))), key=lambda c: c["filename"])
        neg_s = sorted(rng.sample(neg, min(args.balanced_n - half, len(neg))), key=lambda c: c["filename"])
        clips = pos_s + neg_s
    else:
        clips = pos + neg
        clips.sort(key=lambda c: c["filename"])
        if args.limit:
            clips = clips[:args.limit]
    if not clips:
        raise ValueError("no eval clips in split file")

    eval_run_id = f"m7eval_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"
    args.out.parent.mkdir(parents=True, exist_ok=True)

    # Pre-warm clips: fit-fold ambient negatives only (folds 1-3), disjoint from
    # eval by construction. Used to push ADE's baseline out of WARMUP before any
    # scored clip; tagged phase=prewarm, scored=false, excluded from metrics.
    prewarm_clips = list(split.get("fit_negatives", []))[:args.prewarm_n]
    prewarm_folds = sorted({e["fold"] for e in prewarm_clips})
    prewarm_categories = sorted({e["category"] for e in prewarm_clips})

    collector = AlertCollector(args.bootstrap)
    collector.start()
    time.sleep(2.0)  # let the consumer subscribe before any replay

    print(f"eval.start run_id={eval_run_id} prewarm_n={len(prewarm_clips)} "
          f"eval_clips={len(clips)} margins=pre{args.pre_margin_s}/post{args.post_margin_s}")

    perclip: list[dict] = []

    # ---- PRE-WARM PHASE (unscored) ----
    prewarm_start = datetime.now(UTC).timestamp()
    for j, clip in enumerate(prewarm_clips, 1):
        wav = args.audio_dir / clip["filename"]
        if not wav.exists():
            print(f"  prewarm skip (missing): {clip['filename']}", file=sys.stderr)
            continue
        rs, re_, rc = _replay_clip(args, wav, "prewarm-sensor", f"prewarm-{j:04d}")
        perclip.append({
            "eval_run_id": eval_run_id, "phase": "prewarm", "scored": False,
            "clip_id": clip["filename"], "category": clip["category"], "fold": clip["fold"],
            "expected_label": "ambient_prewarm", "site_id": f"prewarm-{j:04d}",
            "replay_start_ts": rs, "replay_end_ts": re_, "sensor_id": "prewarm-sensor",
            "app_rc": rc,
        })
        if j % 20 == 0:
            print(f"  prewarm {j}/{len(prewarm_clips)} baseline_active_seen={collector._active_seen}")
        if args.prewarm_gap_s:
            time.sleep(args.prewarm_gap_s)
    prewarm_end = datetime.now(UTC).timestamp()
    # Let trailing prewarm alerts arrive so baseline_state is observed.
    time.sleep(args.settle_s)
    baseline_active_observed = collector._active_seen
    print(f"eval.prewarm_done n={len(prewarm_clips)} baseline_active_observed={baseline_active_observed}")

    if not baseline_active_observed:
        # Requirement 8: fail clearly rather than running an eval polluted by warmup.
        collector.stop()
        print("eval.FAIL baseline never reached ACTIVE during pre-warm. "
              f"Increase --prewarm-n (current {args.prewarm_n}; ADE_BASELINE_WARMUP default 30) "
              "or check that MSFE is producing FusedObjects. NOT running the scored eval.",
              file=sys.stderr)
        return 2

    # ---- EVAL PHASE (scored) ----
    for i, clip in enumerate(clips, 1):
        wav = args.audio_dir / clip["filename"]
        if not wav.exists():
            print(f"  skip (missing): {clip['filename']}", file=sys.stderr)
            continue
        # Unique site_id per clip so CSAT's geo grouping cannot merge clips.
        site_id = f"eval-{i:04d}"
        replay_start, replay_end, rc = _replay_clip(args, wav, args.sensor_id, site_id)
        perclip.append({
            "eval_run_id": eval_run_id,
            "phase": "eval",
            "scored": True,
            "clip_id": clip["filename"],
            "category": clip["category"],
            "fold": clip["fold"],
            "expected_label": clip["expected_label"],
            "site_id": site_id,
            "replay_start_ts": replay_start,
            "replay_end_ts": replay_end,
            "sensor_id": args.sensor_id,
            "app_rc": rc,
        })
        if i % 20 == 0:
            print(f"  replayed {i}/{len(clips)}")
        # Inter-clip gap: let CSAT's window close + drain before the next clip,
        # so adjacent clips never co-window into one alert. Skip after the last.
        if i < len(clips):
            time.sleep(args.inter_clip_gap_s)

    print(f"eval.replay_done settling {args.settle_s}s for trailing alerts...")
    time.sleep(args.settle_s)
    alerts = collector.stop()
    print(f"eval.collected n_alerts={len(alerts)}")

    # Persist the RAW collected alert stream (every alert + arrival_ts + lineage)
    # so latency vs replay windows can be analyzed and the run is auditable.
    alerts_path = Path(str(args.out) + ".alerts.jsonl")
    with alerts_path.open("w", encoding="utf-8") as fh:
        for a in alerts:
            fh.write(json.dumps(a) + "\n")

    # ---- attribute + score ----
    # Attribution uses ALERT ARRIVAL WALL-CLOCK (arrival_ts), NOT the alert's
    # window_start/window_end. The latter derive from capture_ts (media-internal
    # time) and are a different clock from the wall-clock replay window — the same
    # capture_ts/ingest_ts distinction as the M3 fix. arrival_ts (when the tap
    # received the ExplainedAlert) is the correct wall-clock to correlate against
    # replay_start_ts/replay_end_ts.
    tp = fp = tn = fn = 0
    latencies: list[float] = []
    for rec in perclip:
        if not rec.get("scored", False):
            continue  # prewarm rows excluded from metrics
        win_lo = rec["replay_start_ts"] - args.pre_margin_s
        win_hi = rec["replay_end_ts"] + args.post_margin_s
        attributed = [
            a for a in alerts
            if win_lo <= a["arrival_ts"] <= win_hi
            and a["severity_rank"] >= ALERT_PRESENT_MIN_RANK
        ]
        # Also record max anomaly_score among ALL attributed alerts (any severity)
        # so the report can show how close positives got even when sub-threshold.
        any_attributed = [
            a for a in alerts if win_lo <= a["arrival_ts"] <= win_hi
        ]
        rec["max_anomaly_score"] = (
            round(max((a["anomaly_score"] for a in any_attributed), default=0.0), 4)
        )
        rec["observed_baseline_states"] = sorted({a["baseline_state"] for a in any_attributed})
        alert_present = len(attributed) > 0
        rec["n_attributed_alerts"] = len(attributed)
        rec["alert_present"] = alert_present
        rec["attributed_severities"] = sorted({a["severity"] for a in attributed})
        rec["attributed_explained_ids"] = [a["explained_id"] for a in attributed]

        if alert_present:
            first = min(attributed, key=lambda a: a["arrival_ts"])
            lat = first["arrival_ts"] - rec["replay_start_ts"]
            rec["latency_to_first_alert_s"] = round(lat, 3)
            latencies.append(lat)
        else:
            rec["latency_to_first_alert_s"] = None

        if rec["expected_label"] == "positive":
            if alert_present:
                tp += 1; rec["outcome"] = "TP"
            else:
                fn += 1; rec["outcome"] = "FN"
        else:
            if alert_present:
                fp += 1; rec["outcome"] = "FP"
            else:
                tn += 1; rec["outcome"] = "TN"

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    lat_sorted = sorted(latencies)
    lat_max = max(latencies) if latencies else None
    lat_p95 = (lat_sorted[int(0.95 * (len(lat_sorted) - 1))] if lat_sorted else None)

    summary = {
        "eval_run_id": eval_run_id,
        "split_policy": split.get("split_policy"),
        "eval_folds": split.get("eval_folds"),
        "sampling": {
            "balanced_n": args.balanced_n,
            "sample_seed": args.sample_seed if args.balanced_n else None,
            "limit": args.limit if not args.balanced_n else None,
            "n_positive_scored": sum(1 for r in perclip if r.get("scored") and r["expected_label"] == "positive"),
            "n_negative_scored": sum(1 for r in perclip if r.get("scored") and r["expected_label"] == "negative"),
        },
        "n_clips_scored": sum(1 for r in perclip if r.get("scored")),
        "prewarm": {
            "prewarm_n": len(prewarm_clips),
            "prewarm_folds": prewarm_folds,
            "prewarm_categories": prewarm_categories,
            "prewarm_start_ts": prewarm_start,
            "prewarm_end_ts": prewarm_end,
            "baseline_active_observed": baseline_active_observed,
        },
        "confusion": {"TP": tp, "FP": fp, "TN": tn, "FN": fn},
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "fpr": round(fpr, 4),
        "latency_s": {
            "n_with_alert": len(latencies),
            "max": round(lat_max, 3) if lat_max is not None else None,
            "p95": round(lat_p95, 3) if lat_p95 is not None else None,
        },
        "thresholds": {
            "f1_min": args.f1_threshold,
            "fpr_max": args.fpr_threshold,
            "latency_max_s": args.latency_threshold_s,
        },
        "pass_fail": {
            "f1": "PASS" if f1 >= args.f1_threshold else "FAIL",
            "fpr": "PASS" if fpr <= args.fpr_threshold else "FAIL",
            "latency": ("PASS" if (lat_max is not None and lat_max <= args.latency_threshold_s)
                        else ("FAIL" if lat_max is not None else "N/A")),
        },
        "margins": {"pre_s": args.pre_margin_s, "post_s": args.post_margin_s},
        "inter_clip_gap_s": args.inter_clip_gap_s,
        "csat_dedup_window_s": args.csat_dedup_window_s,
        "alerts_shared_across_clips": _alerts_shared_across_clips(perclip),
        "generated_at": datetime.now(UTC).isoformat(),
    }

    perclip_path = Path(str(args.out) + ".perclip.jsonl")
    with perclip_path.open("w", encoding="utf-8") as fh:
        for rec in perclip:
            fh.write(json.dumps(rec) + "\n")
    summary_path = Path(str(args.out) + ".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"eval.done F1={summary['f1']}({summary['pass_fail']['f1']}) "
          f"FPR={summary['fpr']}({summary['pass_fail']['fpr']}) "
          f"latency_max={summary['latency_s']['max']}({summary['pass_fail']['latency']}) "
          f"confusion={summary['confusion']}")
    print(f"  per-clip: {perclip_path}")
    print(f"  summary:  {summary_path}")
    return 0


def _env() -> dict:
    import os
    return dict(os.environ)


if __name__ == "__main__":
    sys.exit(main())
