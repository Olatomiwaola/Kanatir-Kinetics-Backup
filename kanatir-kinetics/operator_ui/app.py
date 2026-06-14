"""FastAPI app serving the operator dashboard."""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="FusionGuard Operator UI")

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return "<h1>FusionGuard Operator Dashboard</h1><p>Coming in Team 5.</p>"

@app.get("/health")
async def health():
    return {"status": "ok", "service": "operator_ui"}
