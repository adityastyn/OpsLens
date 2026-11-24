"""End-to-end proof that the local model in LM Studio really answers. Run on the edge PC:

    cd edge_intelligence
    .venv\\Scripts\\python tools\\verify_ai.py

Checks LM Studio's Local Model API, the model, one direct answer, then one real
diagnosis through the backend. Exit code 0 only if the diagnosis was truly
AI-generated. Uses only the HTTP APIs. Runs a SIMULATED scenario if no incident
exists. Writes verify_ai_result.json in the current folder.
"""
import json
import os
import re
import sys
import time

import requests

BASE = re.sub(r"/v1$", "", (os.environ.get("EDGE_LLM_URL") or "http://127.0.0.1:1234").replace("localhost", "127.0.0.1").rstrip("/"))
MODEL = os.environ.get("EDGE_LLM_MODEL") or "qwen2.5-7b-instruct"
API = os.environ.get("OPSLENS_API") or "http://127.0.0.1:8001/api"
SESSION = requests.Session()
SESSION.trust_env = False
report = {"lm_studio": f"{BASE}/v1", "model": MODEL, "steps": []}


def step(name, ok, detail=""):
    report["steps"].append({"step": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""), flush=True)
    return ok


def finish(code):
    with open("verify_ai_result.json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    print("Details saved to verify_ai_result.json")
    sys.exit(code)


sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from edge_slm import model_matches  # noqa: E402  (same matching as the backend)

try:
    models = SESSION.get(f"{BASE}/v1/models", timeout=5).json()
except requests.exceptions.RequestException as exc:
    step("LM Studio Local Model API is running", False, f"{type(exc).__name__}. Start it in LM Studio or run start_lmstudio.bat")
    finish(1)
names = [m.get("id") for m in models.get("data") or []]
step("LM Studio Local Model API is running", True)
if not step("Model is available", any(model_matches(MODEL, n or "") for n in names), f"wanted {MODEL}; found: {names}"):
    finish(1)

started = time.time()
try:
    reply = SESSION.post(f"{BASE}/v1/chat/completions", timeout=(3, 300), json={
        "model": MODEL, "stream": False, "max_tokens": 12,
        "messages": [{"role": "user", "content": "Reply with the single word READY."}]})
    text = ((reply.json().get("choices") or [{}])[0].get("message") or {}).get("content", "")
    if not step("Direct answer", reply.status_code == 200 and bool(text.strip()), f"{time.time() - started:.1f} s: {text.strip()[:60]!r}"):
        finish(1)
except requests.exceptions.RequestException as exc:
    step("Direct answer", False, f"{type(exc).__name__}: {exc}")
    finish(1)

try:
    health = SESSION.get(f"{API}/copilot/ai-status", timeout=5).json()
except requests.exceptions.RequestException as exc:
    step("Backend is running", False, f"{type(exc).__name__}. Run start_backend.bat")
    finish(1)
step("Backend is running", True, health.get("status_label", ""))
if health.get("warning"):
    print(f"[WARN] {health['warning']}")
if not step("Backend selected LM Studio", bool(health.get("local_selected")), "set OPSLENS_LLM_PROVIDER=lmstudio in .env if false"):
    finish(1)

ranked = SESSION.get(f"{API}/incidents/ranked", timeout=20).json().get("ranked_incidents") or []
if not ranked:
    print("No open incident. Running a SIMULATED scenario...", flush=True)
    SESSION.post(f"{API}/scenarios/run", json={"scenario_id": "wet_well_high_level", "speed_multiplier": 10}, timeout=10)
    for _ in range(120):
        status = SESSION.get(f"{API}/scenarios/status", timeout=5).json()
        if status.get("status") not in ("running", "recovering"):
            break
        time.sleep(1)
    ranked = SESSION.get(f"{API}/incidents/ranked", timeout=20).json().get("ranked_incidents") or []
if not step("An incident exists", bool(ranked)):
    finish(1)

event_id = ranked[0]["event_id"]
print(f"Asking for a diagnosis of {ranked[0].get('primary_tag')} (this can take minutes on a CPU)...", flush=True)
started = time.time()
done = SESSION.get(f"{API}/events/{event_id}/diagnosis", params={"refresh": "true"}, timeout=(3, 600)).json()
report["diagnosis"] = {k: done.get(k) for k in ("source_status", "failure_kind", "fallback_reason", "latency", "validation", "text")}
step("Diagnosis finished", True, f"{time.time() - started:.1f} s, source {done.get('source_status')}")
removed = len((done.get("validation") or {}).get("removed_lines") or [])
ok = step("Diagnosis is AI-generated", done.get("source_status") == "ai_generated",
          done.get("fallback_reason") or f"{removed} line(s) removed by the record check; latency {done.get('latency')}")
print("\n" + str(done.get("text"))[:1800])
finish(0 if ok else 1)
