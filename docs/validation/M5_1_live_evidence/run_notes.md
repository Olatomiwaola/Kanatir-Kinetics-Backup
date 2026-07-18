
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

## Scenario C (RE-RUN after infra recovery) — PASS
Config: CSAT_MAX_AGE_S=30 CSAT_DEDUP_WINDOW_S=20 (shared C/degraded/D), MSFE_WINDOW_S=2.
inject: video UAV cam-01/1, video GROUND cam-01/2, acoustic AMBIENT app-01 Wind,
site zone-C, spacing 3s. 3 separate windows -> 3 anomalies -> CSAT grouped.
VERIFIED: class_breakdown={UAV:1,GROUND:1,AMBIENT:1,UNKNOWN:0} (sum 3 == observation_count 3),
3 nonzero classes; classification (TRIGGER)=AMBIENT (single class, distinct from breakdown);
distinct_video_track_ref_count=2 (video tracks cam-01/1, cam-01/2; AMBIENT contributed
NO ref -> modality=['acoustic'], refs=None); identity_reference_available=true;
source_track_refs=[(cam-01,1),(cam-01,2)]; group_reason=same_site_within_sliding_window;
M6 emitted ExplainedAlert explained_schema_version=1.0.0, no gate rejection.
HONESTY: AMBIENT necessarily came from the acoustic path (video cannot yield AMBIENT).
Fresh clean captures this run (run-1 topic_*.jsonl archived to *.run1.jsonl).
Location: scenarioC_alert.jsonl; topic_{fused.objects,anomalies.raw,alerts.triaged,alerts.explained}.jsonl.

## Degraded input — PASS (null-not-0) [flowing cases]
inject: RF-only (features.rf), acoustic-only (Silence), video-no-track (empty
detections), distinct sites zone-D0-{rf,ac,novid}.
VERIFIED (both flowing alerts): identity_reference_available=false,
distinct_video_track_ref_count=null (NOT 0), source_track_refs=null.
  - acoustic-only zone-D0-ac: class=AMBIENT, refs=None.
  - video-no-track zone-D0-novid: refs=None.
FINDING (verbatim, out of M5.1 scope): live MSFE subscribes only
features.video+features.acoustic (FEATURE_TOPICS, __main__.py:39), NOT features.rf,
so RF-only produced NO fused object / anomaly / alert. The null-not-0 property is
proven by the acoustic-only and no-track-id cases. M5.1 changes no MSFE topics.
OBSERVATION (pre-existing, non-M5.1): empty-frame video fused object came out
classification=UAV conf=0 (BeliefMass.top_hypothesis returns first specific
hypothesis under full ignorance). Does not affect the degraded ref property.
Location: scenarioDegraded_alerts.jsonl (triaged lines 2-3).

## Scenario D — PASS (geo-temporal incident continuity)
Config: CSAT_MAX_AGE_S=30 CSAT_DEDUP_WINDOW_S=20. inject: cam-01 track7 @ zone-D
every 8s for 70s (8<20 -> group never idle-closes mid-incident -> hits 30s max-age).
VERIFIED 3 emissions, SAME incident_id=eea3eb57-cb46-4cda-a2d3-0520f5c093b4,
incident_sequence=0,1,2 (obs 4,4,1). Group aged out at max-age and re-emitted under
the stable id rather than reopening with a new id (the wrong behavior). Geo-temporal
continuity, NOT physical-object continuity. Location: scenarioD_alerts.jsonl.

## Scenario E — FORMAL — PASS (sealed M6 read-only consumes SA 1.1.0)
Correct M6 field layout: ExplainedAlert has NO sa_schema_version field and
audit_event_ids is a computed @property (not serialized) -- that is why the
earlier probe returned None; lineage is read from contributors[].audit_event_id.
VERIFIED against C's alert (alert_id=44da0a53-ed6e-498f-bbde-13db1db3e763,
sa_schema_version=1.1.0): matching ExplainedAlert explained_schema_version=1.0.0
(sealed M6 unchanged), explainer_kind=templated, alert_id backref matches,
anomaly_ids=3, attribution_available=false + honest note, contributors audit ids
[900001,900002,900003] (lineage intact). XAI log: 0 schema-skip/reject lines across
the resumed run, 6 xai.explained emissions. M5.1 additive alert fields did NOT break
M6 parsing. No M6 source/schema/test changed. Location: scenarioE_formal.txt.

## Injected live-gate scenarios: A,B,B-prime,C,degraded,D,E ALL PASS.
Remaining for full gate: real CVP/APP capture for PGC audit-ledger counts
before/after (injector uses synthetic audit ids; split-evidence plan).

## Real CVP capture (privacy-safe, file source test.avi) — PARTIAL
PURPOSE: PGC audit-ledger before/after counts + real (non-synthetic) audit ids.
Ran real CVP (YOLOv8n + ByteTrack + privacy gate) on test.avi, --max-frames 20,
--site-id zone-REAL, --sensor-id cam-real.
LEDGER EVIDENCE — PASS: audit_events 6607 -> 6627 (delta=20, one per frame).
  New rows real gate-generated: event_id 6608-6627, actor=cvp, sensor_id=cam-real,
  action='no PII actions'. max_event_id=6627; count(event_id>=900001)=0 -> NOT the
  injector's synthetic 900001 range. Real privacy gate wrote real ledger rows and
  the chain carried real audit ids. Location: ledger_before.txt, ledger_after.txt,
  cvp_real_capture.log.
REAL TRACK_IDS — NOT DEMONSTRATED (verbatim): all 15 cam-real fused windows have
  source_track_refs=None. CVP logged 0 detections. Probe: test.avi is 150 frames
  and yields ZERO YOLOv8n detections anywhere (sampled 12 frames @ conf 0.15) --
  it is objectless test footage, so no ByteTrack ids can exist. Real end-to-end
  track_id flow cannot be shown with this media. (Real-track-id PRESERVATION is
  already covered by the MSFE unit test and by injected B/B-prime, which use
  controlled ids precisely because real ids cannot be deterministically forced.)
  To demonstrate real track_ids live would need object-bearing footage (not in
  repo) or a live webcam capture (privacy-sensitive; requires operator consent).

## Real CVP capture #2 (VisDrone static sequence) — PASS (real track_ids + ledger)
Media finding: datasets/VisDrone is VisDrone2019-DET-val = 548 independent aerial
IMAGES (not video); YOLOv8n detects 8-52 objects/image. cv2.VideoCapture accepts a
printf %04d image-sequence pattern (and single images), NOT a glob/dir. Built a
15-frame STATIC sequence from one 52-detection image (seq_%04d.jpg x15) so ByteTrack
confirms tracks over consecutive frames.
Ran real CVP (YOLOv8n+ByteTrack+privacy gate), --sensor-id cam-visdrone,
--site-id zone-REAL2, --max-frames 15, --conf 0.25.
LEDGER: audit_events 6627 -> 6642 (delta=15). New rows real gate-generated.
REAL TRACK_IDS — PASS: 15/15 fused windows carried source_track_refs; 53 distinct
REAL ByteTrack ids (1..53) from real detections (NOT injector 5/7).
REAL-CAPTURE TriagedAlert (zone-REAL2): sa_schema_version=1.1.0, observation_count=15,
distinct_video_track_ref_count=53, identity_reference_available=true,
source_track_refs sensor=cam-visdrone, audit_event_ids=[6628..6642] (real ledger
range, NOT synthetic 900001), group_reason=same_site_within_sliding_window.
This proves M5.1 ref-preservation end-to-end through the REAL perception front-end
with REAL PGC audit lineage. Location: scenarioReal_visdrone_alert.jsonl,
cvp_visdrone_capture.log, ledger_before2.txt.

## LIVE GATE COMPLETE: A,B,B-prime,C,degraded,D,E PASS; PGC ledger PASS (real
## capture, delta 20 then 15); real ByteTrack track_ids PASS (53 real ids e2e).
