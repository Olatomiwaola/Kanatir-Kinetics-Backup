"""Main ingestion pipeline."""
import asyncio, httpx
from sensor_ingestion.simulators.eo_ir import generate_packet as gen_eoir
from sensor_ingestion.simulators.sigint import generate_packet as gen_sigint
import random

POLICY_ENGINE_URL = "http://localhost:8001/evaluate"

async def ingest_and_evaluate(packet):
    data = packet.model_dump(mode="json")
    async with httpx.AsyncClient() as client:
        resp = await client.post(POLICY_ENGINE_URL, json=data)
        result = resp.json()
    print(f"[{packet.modality}] {packet.packet_id[:8]}... → {result['action']}")
    return result

async def run_pipeline(n: int = 10):
    for _ in range(n):
        packet = random.choice([gen_eoir, gen_sigint])()
        await ingest_and_evaluate(packet)
        await asyncio.sleep(0.2)

if __name__ == "__main__":
    asyncio.run(run_pipeline())
