
## CSAT config change (before Scenario B)
Scenarios B through degraded were run with CSAT_DEDUP_WINDOW_S=10 to reduce
idle-flush wait time. Scenario spacing remained below the dedup window, so
grouping semantics were unchanged.

## Scenario A (injected; deterministic producer at features.*) — recorded
observation_count=5, distinct_video_track_ref_count=1, suppressed_count=4,
identity_reference_available=true, class_breakdown total=5 ({UAV:5, UNKNOWN:0}).
Chain reached alerts.explained; sealed M6 emitted explained_schema_version=1.0.0;
lineage audit_event_ids=[900001..900005] preserved (per XAI log).

## Scenario B (injected; CSAT _union_track_refs path) — PASS
spacing=3s -> two separate MSFE windows -> two anomalies (each one ref) -> CSAT
unions. observation_count=2, distinct_video_track_ref_count=2,
source_track_refs=[(cam-01,5),(cam-02,5)] (same integer track_id=5 under two
source_sensor_ids), identity_reference_available=true, class_breakdown={UAV:2,UNKNOWN:0}.
FusedObject shows one ref each; ADE preserves unchanged; M6 emitted ExplainedAlert
(explained_schema_version=1.0.0), no gate rejection. UNION PATH: CSAT.
Caveat: injector audit_event_id counter restarts per process (B ids 900001-900002
coincide numerically with A's); per-scenario captures are separate, lineage intact.

## Scenario B-prime attempt 1 (spacing=0.3s) — NEGATIVE / verbatim
Intended to prove MSFE same-window union but did NOT co-window: MSFE produced TWO
single-contributor FusedObjects (contributors=['cam-01'], ['cam-02']) -> two
anomalies -> CSAT union -> observation_count=2 (same as B, not the intended 1).
ROOT CAUSE (live-path finding): WindowBuffer matures each envelope individually at
ingest_ts+window and trims on harvest, so envelopes 0.3s apart mature in different
harvest ticks; cam-01 is harvested+trimmed before cam-02 matures. The 2s window is
a per-envelope sliding maturity boundary, not a fixed bucket. Unit test
test_fuse_window_populates_source_track_refs exercises the union by calling
fuse_window([both]) directly, bypassing this timing. To co-window live, both
envelopes need ~identical ingest_ts (spacing ~0). Re-running with spacing=0.

## Scenario B-prime attempt 2 (spacing=0) — PASS (MSFE same-window union)
Near-identical ingest_ts -> MSFE co-windowed: ONE FusedObject with both refs
(contributors=['cam-01','cam-02'], source_track_refs=[(cam-01,5),(cam-02,5)]) ->
ONE AnomalyRecord with both refs (ADE passthrough) -> CSAT alert
observation_count=1, distinct_video_track_ref_count=2, n_anomaly_ids=1.
UNION PATH: MSFE. Together with Scenario B (CSAT union, obs=2) both union paths
are proven live with identical distinct=2 / two-qualified-pair result.
Labeled captures: scenarioBprime_MSFEunion_{fused,anomalies}.jsonl.

================================================================================
# HANDOFF — M5.1 live gate PARTIAL (bank & resume fresh)  [2026-07-17]
================================================================================
STATUS: The full live gate has NOT passed yet. Code is complete and unit-green;
several live scenarios are captured and PASS, but C/D/degraded/formal-E and the
real CVP/APP ledger evidence remain. Do NOT claim the live gate passed.

## Code state (implementation complete; DO NOT modify to finish the gate)
- HEAD code commit: e2e79dd  (M5.1 CSAT: honesty fields, TTL seen-set, incidents)
- Chain: contract 70a6906 -> MSFE 0d1668b -> ADE 5089053 -> CSAT e2e79dd
- Full unit suite: 217 passed. Branch `main`, 5 ahead of origin, NOT pushed.

## Scenarios captured
### A — PASS (injected; deterministic producer at features.*)
  inject: cam-01 track7 x5, site zone-A, spacing 3s.
  VERIFIED: sa_schema_version=1.1.0, observation_count=5,
  distinct_video_track_ref_count=1, suppressed_count=4,
  identity_reference_available=true, class_breakdown={UAV:5,UNKNOWN:0} (sum 5),
  group_reason=same_site_within_sliding_window, incident_sequence=0,
  source_track_refs=[(cam-01,7)].
  Location: topic_alerts.triaged.jsonl LINE 1.

### B — PASS (headline: distinct_video_track_ref_count = 2; CSAT union path)
  inject: cam-01 track5 + cam-02 track5, site zone-B, spacing 3s (separate windows).
  VERIFIED: observation_count=2, distinct_video_track_ref_count=2,
  source_track_refs=[(cam-01,5),(cam-02,5)] (same integer id under two sensors),
  identity_reference_available=true, n_anomaly_ids=2, class_breakdown={UAV:2,UNKNOWN:0}.
  Location: scenarioB_CSATunion_{fused,anomalies,alert,explained}.jsonl
            (also topic_alerts.triaged.jsonl LINE 2).

### B-prime — PASS (MSFE same-window union path)
  attempt 1 (spacing 0.3, zone-Bp): NEGATIVE, did NOT co-window (2 windows) ->
    triaged LINE 3 (obs=2). Root cause: WindowBuffer matures/trims per-envelope,
    so 0.3s apart -> separate harvest ticks. Recorded above.
  attempt 2 (spacing 0, zone-Bp2): PASS. ONE FusedObject both refs
    (contributors cam-01+cam-02) -> ONE anomaly both refs -> alert
    observation_count=1, distinct_video_track_ref_count=2, n_anomaly_ids=1.
  Location: scenarioBprime_MSFEunion_{fused,anomalies}.jsonl
            (alert at topic_alerts.triaged.jsonl LINE 4).

### E — PARTIAL. M6 consumed SA 1.1.0 alerts for A and B, emitted ExplainedAlert
  (explained_schema_version=1.0.0, explainer_kind=templated), NO gate rejection;
  XAI log shows lineage audit_event_ids=[900001..900005] for A.
  PENDING: formal extraction reading M6's ACTUAL ExplainedAlert field names
  (earlier probe used wrong keys). Location: topic_alerts.explained.jsonl,
  scenarioB_CSATunion_explained.jsonl.

### C — INCOMPLETE (reached anomalies.raw only; broker crashed pre-triage)
  inject: video UAV cam-01/1, video GROUND cam-01/2, acoustic AMBIENT app-01 Wind,
  site zone-C, spacing 3s. 3 FusedObjects + 3 AnomalyRecords produced & captured
  (UAV/GROUND video with refs; AMBIENT acoustic refs=None -- AMBIENT MUST come
  from the acoustic path, video cannot yield AMBIENT). CSAT never published the C
  alert (Docker/broker crashed before the 10s idle-flush). MUST RE-RUN C.
  Location: scenarioC_fused.jsonl (3 objects); anomalies at
  topic_anomalies.raw.jsonl LINES 11-13; scenarioC_alert.jsonl is EMPTY/stale.

## Infra failure (verbatim)
  Mid-Scenario-C the Kafka broker went to Connection-refused, then the Docker
  daemon itself returned EOF (docker info/ps/logs all EOF). Docker Desktop crashed
  under live-gate load. NOT an M5.1 code issue. Host engine PIDs stayed alive but
  orphaned. (Docker has since been restarted by the operator; engines/consumers
  from the crashed run are stale and must be relaunched.)

## Exact environment values used
  MSFE : KAFKA_BOOTSTRAP=localhost:9092  REDIS_URL=redis://localhost:6379/0
         MSFE_WINDOW_S=2   topics features.video/.acoustic -> fused.objects
  ADE  : ADE_BOOTSTRAP=localhost:9092    unfitted (no ADE_MODEL_PATH; M4 cold-start)
         expects_fused_major=1 (turn-2 gate)   fused.objects -> anomalies.raw
  CSAT : CSAT_BOOTSTRAP=localhost:9092
         Scenario A ran at default CSAT_DEDUP_WINDOW_S=60.
         B onward ran at CSAT_DEDUP_WINDOW_S=10 (restart, recorded above);
         CSAT_MAX_AGE_S=300 (default) throughout.  anomalies.raw -> alerts.triaged
  XAI  : KAFKA_BOOTSTRAP=localhost:9092  XAI_EXPLAINER=templated
         accepts_sa_major=1   alerts.triaged -> alerts.explained
  Injector spacing rule (live-path finding): spacing > MSFE_WINDOW_S -> separate
    windows; spacing ~0 -> co-window (per-envelope maturity + harvest-trim).
  Injector: scripts/m5_1_scenario_injector.py (UNTRACKED, on disk). Synthetic
    audit_event_id starts 900001 per process -> ids repeat across scenarios; per-
    scenario captures are separate. Real ledger counts need a real CVP/APP capture.

## PENDING for a full gate pass
  - Re-run C through triage+explanation (class_breakdown must show >1 nonzero class,
    e.g. {UAV:1,GROUND:1,AMBIENT:1,UNKNOWN:0}; trigger `classification` single/distinct).
  - degraded input (RF-only / acoustic-only / no-track-id) -> identity_reference_
    available=false, distinct_video_track_ref_count=null (NOT 0), source_track_refs=null.
  - D: RESTART CSAT with CSAT_MAX_AGE_S=30 CSAT_DEDUP_WINDOW_S=20; inject
    `--scenario D --duration 70 --spacing 8` (8<20 keeps group active -> 30s
    max-age flush). PROOF: same incident_id across emissions; incident_sequence 0->1->2.
  - Formal E extraction with correct ExplainedAlert field names.
  - Real CVP/APP capture for privacy/PGC audit-ledger counts before/after.

## SAFE RESTART ORDER (next session)
  1. Confirm Docker daemon:      docker info
  2. Start compose stack:        docker compose up -d
  3. Wait for health:            docker compose ps  (kafka + all healthy)
  4. Start engines DOWNSTREAM-FIRST: XAI -> CSAT -> ADE -> MSFE
     (nohup ... & ; capture PIDs; short sleep between; env values above)
  5. Attach 4 consumers at LATEST (NOT --from-beginning -- stale pre-M5.1 records
     from this reused broker would pollute captures) to
     topic_{fused.objects,anomalies.raw,alerts.triaged,alerts.explained}.jsonl
  6. Resume with Scenario C, then degraded, then CSAT restart for D, then formal E,
     then real CVP/APP ledger capture.

## Captured evidence files (docs/validation/M5_1_live_evidence/)
  run_notes.md  engine_{xai,csat,ade,msfe}.log  engine_pids.txt  consumer_pids.txt
  inject_{A,B,Bprime,Bprime2,C}.log  preB_counts.txt
  topic_{fused.objects,anomalies.raw,alerts.triaged,alerts.explained}.jsonl
  scenarioB_CSATunion_{fused,anomalies,alert,explained}.jsonl
  scenarioBprime_MSFEunion_{fused,anomalies}.jsonl
  scenarioC_fused.jsonl  (scenarioC_alert.jsonl EMPTY/stale)
================================================================================
