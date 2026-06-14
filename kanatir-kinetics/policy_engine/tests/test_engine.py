"""Unit tests for the policy engine."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from policy_engine.engine import load_rules, evaluate

RULES_DIR = os.path.join(os.path.dirname(__file__), '..', 'rules')

def test_load_rules():
    rules = load_rules(RULES_DIR)
    assert len(rules) > 0

def test_block_unclassified():
    rules = load_rules(RULES_DIR)
    packet = {"classification": "UNCLASSIFIED", "pipeline_level": "PROTECTED_B"}
    result = evaluate(packet, rules)
    assert result["action"] == "BLOCK"

def test_permit_protected_b():
    rules = load_rules(RULES_DIR)
    packet = {"classification": "PROTECTED_B", "pipeline_level": "PROTECTED_B"}
    result = evaluate(packet, rules)
    assert result["action"] == "PERMIT"

def test_flag_noforn():
    rules = load_rules(RULES_DIR)
    packet = {"classification": "PROTECTED_B", "caveats": ["NOFORN"]}
    result = evaluate(packet, rules)
    assert result["action"] == "FLAG"

def test_default_permit():
    rules = load_rules(RULES_DIR)
    packet = {"classification": "PROTECTED_B"}
    result = evaluate(packet, rules)
    assert result["action"] == "PERMIT"
    assert result["rule_id"] == "DEFAULT"
