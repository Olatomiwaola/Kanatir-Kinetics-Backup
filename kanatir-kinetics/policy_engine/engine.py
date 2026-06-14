"""
Main policy evaluation engine.
Loads rules from YAML and evaluates data packets against them.
"""
import yaml, os

POLICY_DIR = os.getenv("POLICY_DIR", "./policy_engine/rules")

CLASSIFICATION_ORDER = ["UNCLASSIFIED","PROTECTED_B","PROTECTED_C","SECRET","TOP_SECRET"]

Action = str  # PERMIT | BLOCK | DOWNGRADE | SEGREGATE | FLAG

def load_rules(policy_dir: str = POLICY_DIR) -> list:
    rules = []
    for fname in sorted(os.listdir(policy_dir)):
        if fname.endswith(".yaml") or fname.endswith(".yml"):
            with open(os.path.join(policy_dir, fname)) as f:
                data = yaml.safe_load(f)
                if data and "rules" in data:
                    rules.extend(data["rules"])
    return rules

def evaluate(packet: dict, rules: list) -> dict:
    """Evaluate a data packet against loaded policy rules. Returns action + reason."""
    for rule in rules:
        cond = rule.get("condition", {})
        match = all(packet.get(k) == v for k, v in cond.items()
                    if k not in ("caveat",))
        caveat_match = True
        if "caveat" in cond:
            caveat_match = cond["caveat"] in packet.get("caveats", [])
        if match and caveat_match:
            return {
                "action": rule["action"],
                "rule_id": rule["id"],
                "reason": rule.get("reason", ""),
                "classification": packet.get("classification", "UNCLASSIFIED"),
            }
    return {
        "action": "PERMIT",
        "rule_id": "DEFAULT",
        "reason": "No rules matched — default permit",
        "classification": packet.get("classification", "UNCLASSIFIED"),
    }
