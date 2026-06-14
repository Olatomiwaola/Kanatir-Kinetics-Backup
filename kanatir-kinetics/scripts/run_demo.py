"""
End-to-end FusionGuard demo.
Simulates: sensor ingestion → policy evaluation → audit logging → fusion output.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sensor_ingestion.simulators.eo_ir import generate_packet as gen_eoir
from sensor_ingestion.simulators.sigint import generate_packet as gen_sigint
from policy_engine.engine import load_rules, evaluate
from audit_logger.logger import init_db, log_decision
from fusion_core.fuser import fuse

RULES_DIR = os.path.join(os.path.dirname(__file__), '..', 'policy_engine', 'rules')
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'fusionguard.db')

print("=" * 60)
print("  KANATIR KINETICS — FusionGuard Demo")
print("=" * 60)

init_db(DB_PATH)
rules = load_rules(RULES_DIR)
print(f"\n[POLICY]  Loaded {len(rules)} rules")

packets = [gen_eoir(), gen_eoir(), gen_sigint(), gen_sigint()]
permitted = []

print("\n[INGEST]  Processing sensor packets...\n")
for pkt in packets:
    data = pkt.model_dump(mode="json")
    result = evaluate(data, rules)
    log_decision(data, result, db_path=DB_PATH)
    icon = "✓" if result["action"] == "PERMIT" else "✗" if result["action"] == "BLOCK" else "⚑"
    print(f"  {icon}  [{pkt.modality:<8}] {pkt.classification:<15} → {result['action']}  ({result['reason']})")
    if result["action"] == "PERMIT":
        permitted.append(data)

print(f"\n[FUSION]  Fusing {len(permitted)} permitted packets...")
fused = fuse(permitted)
if fused:
    print(f"  Fused ID     : {fused['fused_id'][:16]}...")
    print(f"  Modalities   : {fused['modalities']}")
    print(f"  Classification: {fused['classification']}")
else:
    print("  No permitted packets to fuse.")

print(f"\n[AUDIT]   All decisions logged → {DB_PATH}")
print("\n  FusionGuard pipeline operational.\n")
print("=" * 60)
