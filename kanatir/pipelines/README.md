# Kanatir Pipelines — CVP & APP (Sprint 3-4)

Vision and acoustic ingestion pipelines. They run **host-native (arm64)** on the
Apple Silicon dev machine and connect to the dockerized Kafka broker on
`localhost:9092`. They are intentionally **not** run inside the `linux/amd64`
stack containers (emulation would cripple per-frame inference, and the real
deployment target is the Jetson AGX Orin with TensorRT, not an amd64 container).

## Install (host)

```bash
cd ~/dev/Kanatir-Kinetics-Backup
python3 -m venv .venv && source .venv/bin/activate
pip3 install -e ".[pipelines]"
# Optional GPU on Apple Silicon:
pip3 install tensorflow-metal
```

## Prereqs

The stack must be up so Kafka and Postgres are reachable:

```bash
docker compose up -d
python3 -m kanatir.core.udih          # ensure 15 topics exist
python3 -m kanatir.core.pgc.audit     # apply audit schema (idempotent)
```

## Run CVP (video)

```bash
# Live RTSP/ONVIF camera:
python3 -m kanatir.pipelines.cvp --source "rtsp://user:pass@cam/stream" --sensor-id cam-01

# Local webcam smoke test:
python3 -m kanatir.pipelines.cvp --source 0 --sensor-id webcam --max-frames 100
```

## Run APP (acoustic)

```bash
# Live microphone:
python3 -m kanatir.pipelines.app --source mic --sensor-id mic-01

# Audio file:
python3 -m kanatir.pipelines.app --source ./sample.wav --sensor-id file-01
```

## Privacy gate (fail-closed)

Every frame/window passes through `kanatir.pipelines.common.privacy_gate` before
anything is published:

1. Scrub PII (CVP: blur faces, hash plates; APP: detect + suppress speech).
2. Write **one** PGC audit event (`record_event`) describing the action.
3. Only then build and publish the `FeatureEnvelope`.

If the scrub **or** the audit write raises, the frame is **dropped** and nothing
is published. There is no path that publishes without a successful audit write.

## Feature envelope

`kanatir.pipelines.common.envelope.FeatureEnvelope` is the versioned contract on
all `features.*` topics (`SCHEMA_VERSION = 1.0.0`). Consumers gate on
`schema_version`. Every envelope carries a mandatory `privacy` block whose
`gate_passed` must be `True` (enforced by the schema).

## Tests

```bash
pip3 install -e ".[dev]"
python3 -m pytest tests/unit -q                          # no DB / no ML deps needed
PGC_DSN=postgresql://kanatir:kanatir_dev@localhost:5432/kanatir \
    python3 -m pytest tests/integration -m integration   # needs running Postgres
```
