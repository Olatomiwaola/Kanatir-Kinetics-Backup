# FusionGuard Architecture

## Data Flow

```
Sensor Feed (EO/IR, SIGINT, UAS, Radar, Acoustic)
        ↓
  Sensor Ingestion Layer      ← Team 3
  Normalise, tag, timestamp
        ↓
  Policy Engine               ← Team 2  ← YOU ARE HERE
  PERMIT / BLOCK / DOWNGRADE / FLAG / SEGREGATE
        ↓
  Audit Logger                ← Team 4
  Provenance, lineage, export
        ↓
  Fusion Core (permitted only)← Team 3
  Multi-modal merge
        ↓
  Operator UI                 ← Team 5
  Dashboard + override
        ↓
  Edge Deploy                 ← Team 6
  Docker, SWaP, rugged hw
```

## Classification Levels
| Level        | Rank |
|---|---|
| UNCLASSIFIED | 0 |
| PROTECTED_B  | 1 |
| PROTECTED_C  | 2 |
| SECRET       | 3 |
| TOP_SECRET   | 4 |
