#!/usr/bin/env bash
# verify_m6_multimodal.sh — force a genuine multimodal FusedObject and trace it
# through ADE -> CSAT -> XAI, checking all 8 acceptance criteria.
#
# Strategy: BOTH sources stream live and concurrently.
#   - APP  : --source mic   (continuous real-time windows; the `while True` path)
#   - CVP  : --source 0/1   (continuous webcam frames)
# Because MSFE correlates on ingest_ts (bus-arrival), two live streams publishing
# into the same MSFE_WINDOW_S co-window by construction. No burst/stream mismatch.
#
# Run from repo root with the Docker stack already up:
#   bash verify_m6_multimodal.sh
#
# Tunables (env): MSFE_WINDOW_S, RUN_SECONDS, CVP_SOURCE, SITE_ID, KAFKA
set -uo pipefail

# ---- config -----------------------------------------------------------------
MSFE_WINDOW_S="${MSFE_WINDOW_S:-30}"     # wide enough that live mic+cam co-window
RUN_SECONDS="${RUN_SECONDS:-75}"          # how long both pipelines stream
CVP_SOURCE="${CVP_SOURCE:-0}"             # 0 built-in; try 1 if Continuity Camera grabs
SITE_ID="${SITE_ID:-zone-A}"              # SAME on both -> CSAT can group; required for co-site
KAFKA="${KAFKA:-kanatir-kafka}"           # docker container name
BOOT="localhost:9092"
LOGDIR="$(mktemp -d)/m6"; mkdir -p "$LOGDIR"
echo "logs -> $LOGDIR   MSFE_WINDOW_S=$MSFE_WINDOW_S  RUN_SECONDS=$RUN_SECONDS  CVP_SOURCE=$CVP_SOURCE"

pids=()
cleanup() { echo "--- stopping ---"; for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; sleep 2; }
trap cleanup EXIT

start_stage() { # name, logfile, command...
  local name="$1" logf="$2"; shift 2
  echo ">>> start $name"
  ( "$@" ) >"$logf" 2>&1 &
  pids+=($!)
}

# ---- 0. pre-flight: topics exist -------------------------------------------
python3 -m kanatir.core.udih >/dev/null 2>&1

# ---- 1. consumers FIRST (auto.offset.reset=latest: subscribe before producers)
start_stage XAI  "$LOGDIR/xai.log"  python3 -m kanatir.core.xai
sleep 3
start_stage CSAT "$LOGDIR/csat.log" python3 -m kanatir.core.csat
sleep 2
start_stage ADE  "$LOGDIR/ade.log"  python3 -m kanatir.core.ade
sleep 2
MSFE_WINDOW_S="$MSFE_WINDOW_S" \
  start_stage MSFE "$LOGDIR/msfe.log" env MSFE_WINDOW_S="$MSFE_WINDOW_S" python3 -m kanatir.core.msfe
sleep 4

# ---- 2. BOTH pipelines concurrently — the overlap is the whole point --------
APP_START_EPOCH=$(date +%s)
start_stage APP "$LOGDIR/app.log" \
  python3 -m kanatir.pipelines.app --source mic --sensor-id app-01 --site-id "$SITE_ID"
CVP_START_EPOCH=$(date +%s)
start_stage CVP "$LOGDIR/cvp.log" \
  python3 -m kanatir.pipelines.cvp --source "$CVP_SOURCE" --sensor-id cvp-01 --site-id "$SITE_ID" \
    --max-frames 100000

echo "--- both streaming; running $RUN_SECONDS s ---"
sleep "$RUN_SECONDS"
cleanup; trap - EXIT
echo "--- drain (let MSFE close its window + chain settle) ---"
sleep "$((MSFE_WINDOW_S + 15))"

# ---- 3. pull the topics for assertions -------------------------------------
dump() { docker exec "$KAFKA" kafka-console-consumer --bootstrap-server "$BOOT" \
  --topic "$1" --from-beginning --timeout-ms 12000 2>/dev/null; }
dump fused.objects   > "$LOGDIR/fused.jsonl"
dump anomalies.raw   > "$LOGDIR/anom.jsonl"
dump alerts.triaged  > "$LOGDIR/triaged.jsonl"
dump alerts.explained> "$LOGDIR/explained.jsonl"

# ---- 4. assertions (criteria 1-8) via python -------------------------------
python3 - "$LOGDIR" "$APP_START_EPOCH" "$CVP_START_EPOCH" "$MSFE_WINDOW_S" <<'PY'
import json, sys, pathlib
d = pathlib.Path(sys.argv[1]); app_t0, cvp_t0, win = int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
def load(f):
    p = d/f
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []
fused = load("fused.jsonl"); anom = load("anom.jsonl"); tri = load("triaged.jsonl"); exp = load("explained.jsonl")

# criterion 2/3/4: a FusedObject with n_modalities>=2 incl app-01 AND cvp-01
mm = None
for fo in fused:
    srcs = {c.get("source_sensor_id") for c in fo.get("contributors", [])}
    mods = {c.get("modality") for c in fo.get("contributors", [])}
    if fo.get("n_modalities",0) >= 2 and {"app-01","cvp-01"} <= srcs and {"acoustic","video"} <= mods:
        mm = fo; break

def line(label, ok, detail=""): print(f"[{'PASS' if ok else 'FAIL'}] {label}{(' — '+detail) if detail else ''}")

print("="*70); print("M6 MULTIMODAL VERIFICATION"); print("="*70)
print(f"MSFE_WINDOW_S used        : {win}")
print(f"APP launch epoch          : {app_t0}")
print(f"CVP launch epoch          : {cvp_t0}")
print(f"launch overlap (s)        : {abs(app_t0-cvp_t0)} (both ran concurrently for the full window)")
print(f"fused.objects total       : {len(fused)}")
print(f"  multimodal (n_mod>=2)   : {sum(1 for f in fused if f.get('n_modalities',0)>=2)}")
print("-"*70)

c1 = mm is not None  # overlap actually produced a co-window
line("C1 APP/CVP events co-windowed in MSFE", c1)
if not mm:
    line("C2 FusedObject n_modalities>=2", False)
    line("C3 contributors from app-01 AND cvp-01", False)
    line("C4 lineage has acoustic AND video audit_event_ids", False)
    print("\nNo multimodal FusedObject. Likely cause:")
    n2 = sum(1 for f in fused if f.get('n_modalities',0)>=2)
    if not fused: print("  -> MSFE emitted nothing: topic lag or pipelines not producing (check app.log/cvp.log).")
    elif n2==0:  print(f"  -> {len(fused)} fused objects but all single-modality: window too small OR one stream silent. Raise MSFE_WINDOW_S / confirm both logs show events.")
    sys.exit(1)

fid = mm["fused_id"]; mods = {c["modality"] for c in mm["contributors"]}
aids = sorted(c["audit_event_id"] for c in mm["contributors"] if c.get("audit_event_id") is not None)
ac = sorted(c["audit_event_id"] for c in mm["contributors"] if c["modality"]=="acoustic")
vd = sorted(c["audit_event_id"] for c in mm["contributors"] if c["modality"]=="video")
line("C2 FusedObject n_modalities>=2", mm["n_modalities"]>=2, f"n_modalities={mm['n_modalities']}")
line("C3 contributors from app-01 AND cvp-01", True, f"sources={sorted({c['source_sensor_id'] for c in mm['contributors']})}")
line("C4 lineage has acoustic AND video audit ids", bool(ac) and bool(vd),
     f"acoustic={ac[0]}..{ac[-1]} ({len(ac)})  video={vd[0]}..{vd[-1]} ({len(vd)})")

# C5: ADE anomaly linked to this fused_id
an = next((a for a in anom if a.get("fused_id")==fid), None)
line("C5 ADE emitted anomaly for this fused_id", an is not None,
     f"anomaly_id={an['anomaly_id']}" if an else "no anomaly with this fused_id")
if not an: sys.exit(1)
aid = an["anomaly_id"]

# C6: CSAT triaged that anomaly
al = next((t for t in tri if aid in t.get("anomaly_ids",[])), None)
line("C6 CSAT triaged that anomaly", al is not None,
     f"alert_id={al['alert_id']}" if al else "anomaly not in any TriagedAlert")
if not al: sys.exit(1)
alid = al["alert_id"]

# C7: XAI explained that alert
ex = next((e for e in exp if e.get("alert_id")==alid), None)
line("C7 XAI produced ExplainedAlert", ex is not None,
     f"explained_id={ex['explained_id']}" if ex else "alert not explained")
if not ex: sys.exit(1)

# C8: both modalities preserved in the final ExplainedAlert + multimodal stated
exmods = {c["modality"] for c in ex["contributors"]}
both = {"acoustic","video"} <= exmods
multimodal_stated = ("acoustic" in ex["explanation_text"] and "video" in ex["explanation_text"]) \
                    or "modalit" in ex["explanation_text"].lower()
line("C8 ExplainedAlert preserves both modalities", both, f"modalities={sorted(exmods)}")
line("C8 explanation_text states multimodal", multimodal_stated)

print("-"*70)
print("TRACE")
print(f"  fused_object_id : {fid}")
print(f"  n_modalities    : {mm['n_modalities']}")
print(f"  contributor src : {sorted({c['source_sensor_id'] for c in mm['contributors']})}")
print(f"  audit ids       : acoustic {ac[0]}..{ac[-1]} | video {vd[0]}..{vd[-1]}")
print(f"  anomaly_id      : {aid}")
print(f"  alert_id        : {alid}")
print(f"  explained_id    : {ex['explained_id']}")
print(f"  explanation     : {ex['explanation_text'][:300]}")
allpass = c1 and both and multimodal_stated and bool(ac) and bool(vd)
print("="*70); print("RESULT:", "ALL CRITERIA PASSED" if allpass else "INCOMPLETE")
sys.exit(0 if allpass else 1)
PY
rc=$?
echo ""; echo "stage logs in $LOGDIR (app.log cvp.log msfe.log ade.log csat.log xai.log)"
exit $rc
