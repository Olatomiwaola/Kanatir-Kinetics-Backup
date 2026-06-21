# MSFE — Multi-Sensor Fusion Engine (Sprint 5-6, M3)

Consumes versioned feature envelopes from `features.video` + `features.acoustic`,
correlates co-windowed evidence, fuses it with **Dempster-Shafer**, and publishes
versioned `FusedObject` records to `fused.objects`.

## Why this shape

- **Host-native arm64, not Flink.** Parity with CVP/APP (the Sprint 3-4 model).
  The fusion math and windowing are plain, testable Python; Flink is deferred to
  the Jetson/scale sprint alongside containerization.
- **Dempster-Shafer, not weighted Bayesian.** D-S represents *ignorance*
  explicitly (an empty video frame contributes mass to UNKNOWN, not a fabricated
  probability) and *reports conflict* (`conflict_k`) instead of hiding it. High K
  is a downstream anomaly signal for ADE.
- **Zero ML dependency.** MSFE runs on the core install — no `[pipelines]` extra.
  It consumes derived features, not media.

## Modules

| File | Role |
|------|------|
| `fused.py` | `FusedObject` output contract (versioned `FUSED_SCHEMA_VERSION`), `BeliefMass`, `Contributor`, the frame of discernment |
| `dempster_shafer.py` | Pure D-S combination (Dempster's rule, conflict K, vacuous identity) |
| `evidence.py` | Per-modality envelope → mass mappers (video, acoustic; register more in `MAPPERS`) |
| `fusion.py` | Spatial+temporal correlation grouping and `fuse_window` |
| `buffer.py` | Redis sliding-window adapter for the live consumer |
| `__main__.py` | Live Kafka consumer → fuse → publish to `fused.objects` |

## Frame of discernment (M3)

`{UAV, GROUND, AMBIENT}` plus `UNKNOWN` (= full frame Θ, total ignorance).
Deliberately small for the gate; extend behind the same interface when ADE needs
a finer taxonomy.

## Run (host, docker stack up)

```bash
# core deps only — no [pipelines] extra needed
pip3 install -e .

KAFKA_BOOTSTRAP=localhost:9092 \
REDIS_URL=redis://localhost:6379/0 \
MSFE_WINDOW_S=2.0 \
python3 -m kanatir.core.msfe
```

With CVP and APP publishing, MSFE logs one `msfe.fused` line per emitted object
(classification, confidence, conflict_k, contributors). Inspect the output topic:

```bash
docker compose exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 --topic fused.objects --from-beginning
```

## Tests

```bash
python3 -m pytest tests/unit/test_sprint_05_06.py -v   # 20 tests, no broker/Redis/ML
```

## Versioning

Consumers (ADE, the M4 gate) gate on `fused_schema_version`. Incoming envelopes
whose `schema_version` ≠ `1.0.0` are skipped and logged, not crashed on — the
forward-compat behavior the versioned envelope was built for.

## Carried forward

- Correlation is greedy time+site grouping. A later sprint can swap a tracker
  (e.g. Hungarian assignment across windows) behind `fusion.correlate`.
- `geo` on a fused object takes the first contributor with a fix; multi-fix
  triangulation is a later refinement.
- Acoustic reliability discount (0.85) is a flat constant; per-sensor reliability
  weighting is the natural next step.
