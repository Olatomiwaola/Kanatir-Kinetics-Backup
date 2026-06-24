"""
analyze_alert_latency.py — M7: measure ExplainedAlert arrival latency against the
clip replay windows, to set post_margin_s and inter_clip_gap_s from DATA rather
than guesswork (per the attribution-fix requirement).

Reads <stem>.perclip.jsonl (clip replay windows) and <stem>.alerts.jsonl (raw
collected alerts with arrival_ts). For each alert, finds the nearest preceding
clip by replay_start_ts and reports the post-replay latency distribution, plus
how many alerts fall in the gap / next-clip window / beyond the current margin.

Run:
    python3 scripts/analyze_alert_latency.py \
        --stem datasets/eval/m7_prewarm_smoke \
        --current-post-margin-s 5 --current-gap-s 10
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    k = int(round((p / 100.0) * (len(s) - 1)))
    return s[k]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure alert latency vs clip windows.")
    ap.add_argument("--stem", required=True, type=Path, help="Output stem used by eval_harness.")
    ap.add_argument("--current-post-margin-s", type=float, default=5.0)
    ap.add_argument("--current-gap-s", type=float, default=10.0)
    ap.add_argument("--current-pre-margin-s", type=float, default=1.0)
    args = ap.parse_args(argv)

    perclip_path = Path(str(args.stem) + ".perclip.jsonl")
    alerts_path = Path(str(args.stem) + ".alerts.jsonl")
    if not perclip_path.exists() or not alerts_path.exists():
        print(f"missing {perclip_path} or {alerts_path}; run the harness with the raw-alert dump first",
              file=sys.stderr)
        return 1

    clips = [json.loads(l) for l in perclip_path.read_text().splitlines() if l.strip()]
    alerts = [json.loads(l) for l in alerts_path.read_text().splitlines() if l.strip()]
    # scored eval clips only, sorted by replay_start
    eval_clips = sorted((c for c in clips if c.get("scored")), key=lambda c: c["replay_start_ts"])
    if not eval_clips or not alerts:
        print(f"no eval clips ({len(eval_clips)}) or no alerts ({len(alerts)})", file=sys.stderr)
        return 1

    post_latencies: list[float] = []
    n_in_gap = n_in_next = n_beyond_margin = n_before_any = 0

    for a in alerts:
        at = a["arrival_ts"]
        # nearest clip whose replay_start <= arrival
        preceding = [c for c in eval_clips if c["replay_start_ts"] <= at]
        if not preceding:
            n_before_any += 1
            continue
        c = max(preceding, key=lambda c: c["replay_start_ts"])
        post = at - c["replay_end_ts"]
        post_latencies.append(post)
        idx = eval_clips.index(c)
        nxt = eval_clips[idx + 1] if idx + 1 < len(eval_clips) else None
        if post > args.current_post_margin_s:
            n_beyond_margin += 1
        if nxt and at >= (nxt["replay_start_ts"] - args.current_pre_margin_s):
            n_in_next += 1
        elif post > 0 and (nxt is None or at < nxt["replay_start_ts"]):
            n_in_gap += 1

    print(f"alerts={len(alerts)} eval_clips={len(eval_clips)} "
          f"before_any_clip={n_before_any} (likely prewarm alerts)")
    if post_latencies:
        print("\npost-replay latency (arrival_ts - replay_end_ts), seconds:")
        print(f"  min={min(post_latencies):.2f} p50={_pct(post_latencies,50):.2f} "
              f"p90={_pct(post_latencies,90):.2f} p95={_pct(post_latencies,95):.2f} "
              f"max={max(post_latencies):.2f} mean={statistics.mean(post_latencies):.2f}")
    print("\nattribution diagnosis (current settings "
          f"post_margin={args.current_post_margin_s} gap={args.current_gap_s}):")
    print(f"  alerts beyond current post_margin: {n_beyond_margin}")
    print(f"  alerts landing in NEXT clip window: {n_in_next}")
    print(f"  alerts landing in inter-clip gap:   {n_in_gap}")

    if post_latencies:
        p95 = _pct(post_latencies, 95)
        mx = max(post_latencies)
        rec_post = round(max(p95, mx) + 2.0, 1)
        rec_gap = round(rec_post + args.current_pre_margin_s + 3.0, 1)
        print("\n=== RECOMMENDED SETTINGS (p95/max + safety) ===")
        print(f"  post_margin_s >= {rec_post}  (covers p95={p95:.2f}, max={mx:.2f})")
        print(f"  inter_clip_gap_s >= {rec_gap}  (> post_margin + pre_margin + drain)")
        print(f"  NOTE: full-run wall-clock at gap={rec_gap}s over 272 clips "
              f"~= {round(272 * (rec_gap + 6) / 60)} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
