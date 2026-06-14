# Kanatir Kinetics — FusionGuard

**Fusion Compliance Engine** for the Canadian IDEaS Defence Challenge.

A modular middleware system that enforces classification rules, provenance tracking,
and policy-aware data gatekeeping across multi-sensor fusion pipelines.

## Architecture

```
sensor_ingestion/   → Receives and normalises raw sensor data
policy_engine/      → Enforces classification and routing rules
fusion_core/        → Combines sensor streams into fused output
audit_logger/       → Provenance, lineage, and audit trail
operator_ui/        → Human-readable compliance dashboard
edge_deploy/        → Docker packaging for edge hardware
```

## Quick Start

```bash
cp .env.example .env          # Add your API key
pip install -r requirements.txt
python scripts/run_demo.py    # Full end-to-end demo
```

## Classification Support
- Protected B (minimum)
- Protected C
- Secret
- Coalition caveats (FVEY, NATO)
