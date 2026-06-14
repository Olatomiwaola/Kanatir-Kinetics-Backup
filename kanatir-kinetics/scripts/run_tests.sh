#!/bin/bash
cd "$(dirname "$0")/.."
echo "Running FusionGuard test suite..."
python -m pytest policy_engine/tests/ sensor_ingestion/tests/ audit_logger/tests/ fusion_core/tests/ -v
