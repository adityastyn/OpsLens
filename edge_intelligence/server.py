"""
OpsLens Live Gateway Server
Runs FastAPI on http://0.0.0.0:8000 serving Component 11 orchestrator.
"""

import sys
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
import os
import uvicorn
from fastapi import FastAPI
from env_loader import load_env_file

# Add directory to sys.path
root_dir = Path(__file__).resolve().parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

# Load this backend's local settings before importing modules that read the
# environment. Existing process variables win over values in the local file.
_ENV_FILE = root_dir / ".env"
_ENV_LOADED = load_env_file(_ENV_FILE)
# Say where settings came from and how many Groq keys were found. Never the keys.
_GROQ_KEYS = [k for k in (os.environ.get("GROQ_API_KEYS") or os.environ.get("GROQ_API_KEY") or "").split(",") if k.strip()]
print(f"[OpsLens] Settings: {_ENV_FILE} ({'found, ' + str(_ENV_LOADED) + ' values loaded' if _ENV_FILE.exists() else 'NOT FOUND'}). "
      f"AI provider: {os.environ.get('OPSLENS_LLM_PROVIDER') or 'not set'}. Groq keys found: {len(_GROQ_KEYS)}.", flush=True)

from database import Database
from stream import TagStream
from orchestrator import OpsLensOrchestrator
from api.routes import create_app

db = Database()
stream = TagStream(db_path=db.db_path)
orch = OpsLensOrchestrator(db=db, stream=stream)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Readings already stored in SQLite have already contributed to persisted
    # incidents. Replaying them on every server restart inflates repeat counts
    # and reruns the full event pipeline; subscribe only to rows added after
    # this process starts.
    await orch.start_streaming(from_latest=True)
    from llm_provider import selected_provider
    if selected_provider() == "lmstudio" and \
            os.getenv("OPSLENS_LOCAL_WARMUP", "true").strip().lower() in ("1", "true", "yes"):
        # Load the local model in the background so the operator's first
        # question is not spent on a cold start. Startup never waits for it.
        from edge_slm import local_health, warm_up_local

        def _warm():
            health = local_health()
            if health["reachable"] and health["model_installed"]:
                print(f"[OpsLens] Warming up LM Studio model {health['model']} ...", flush=True)
                print(f"[OpsLens] LM Studio warm-up: {warm_up_local()}", flush=True)
                if health.get("warning"):
                    print(f"[OpsLens] WARNING: {health['warning']}", flush=True)
            else:
                print(f"[OpsLens] LM Studio not ready ({health['error']}). Deterministic answers remain available.", flush=True)

        asyncio.get_running_loop().run_in_executor(None, _warm)
    if os.getenv("OPSLENS_INDEX_PLANT_SOPS", "true").strip().lower() in ("1", "true", "yes"):
        # Put the plant's SOP sections into Qdrant in the background, so SOP
        # search tries Qdrant first. Without Qdrant, BM25 is used; startup never waits.
        from plant_sop_index import index_on_start
        index_on_start()
    if os.getenv("OPSLENS_SEED_HISTORY", "true").strip().lower() in ("1", "true", "yes"):
        # One month of simulated past incidents, added once in a separate
        # process so Related Incidents and the alarm history have a past.
        from seed_history import start_background
        start_background(Path(db.db_path))
    plc_adapter = app.state.opcua_adapter
    plc_task = None
    if plc_adapter.config.enabled and plc_adapter.configured:
        async def ingest_plc_readings():
            async for reading in plc_adapter.stream_readings():
                # PLC readings enter the same write-through store and incident pipeline.
                orch.db.insert_reading(reading)
        plc_task = asyncio.create_task(ingest_plc_readings(), name="opslens-plc-opcua-ingestion")
    try:
        yield
    finally:
        if plc_task and not plc_task.done():
            plc_task.cancel()
            try:
                await plc_task
            except asyncio.CancelledError:
                pass
        await plc_adapter.disconnect()
        await orch.stop()


app = create_app(orchestrator=orch)
app.router.lifespan_context = lifespan


if __name__ == "__main__":
    host = os.getenv("OPSLENS_HOST", "0.0.0.0")
    port = int(os.getenv("OPSLENS_PORT", "8001"))
    print(f"Starting OpsLens Gateway Server on http://{host}:{port}...")
    uvicorn.run(app, host=host, port=port)
