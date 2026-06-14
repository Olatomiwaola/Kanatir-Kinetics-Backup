"""FastAPI REST interface for the policy engine."""
from fastapi import FastAPI
from policy_engine.engine import load_rules, evaluate

app = FastAPI(title="FusionGuard Policy Engine", version="0.1.0")
_rules = []

@app.on_event("startup")
async def startup():
    global _rules
    _rules = load_rules()

@app.post("/evaluate")
async def evaluate_packet(packet: dict):
    return evaluate(packet, _rules)

@app.get("/rules")
async def list_rules():
    return {"rules": _rules, "count": len(_rules)}

@app.post("/reload")
async def reload_rules():
    global _rules
    _rules = load_rules()
    return {"status": "reloaded", "count": len(_rules)}
