# OpsLens v2.1 audit findings

Audit of commit `06d8394`, 4 November 2026. Fixes are listed in `CHANGELOG.md` under v2.2.0.

## How information flows

Simulator or read-only OPC UA → `tag_readings` (SQLite) → orchestrator → prioritizer → dedup and alarm lifecycle → correlator → event story → evidence engine → SOP retrieval → guidance → operator brief → AI diagnosis (Qwen, validated) → HMI over REST and SSE → operator actions and handover.

Everything before the AI diagnosis is deterministic. The model only explains facts already established.

Active code is `edge_intelligence/` (backend) and `hmi_ui/` (frontends). `ops-lens/` and `opslens-generator/` are older copies and are not used.

## Exact reasons AI answers did not appear

1. `start_all.bat`, `start_backend.bat`: only checked for Ollama. Never ran `E:\\Ollamma\\App\\ollama.exe` or set `OLLAMA_MODELS`. This is the error in the screenshots.
2. `operational_history.generate_shift_narrative`: called the model synchronously on the FastAPI event loop with a 4 s limit from `/api/handover/current`. `App.handleLiveEvent` called that route on every SSE event. Measured: 2,815 calls during one scripted run of seven scenarios. Found only when running the system end to end.
3. `ai_diagnosis.stream_ollama`: one 60 s read limit covered model load and prompt reading.
4. `DiagnosisPanel`: restarted on every mount, with no single-flight on the server.
5. `ai_diagnosis._cache_key`: memory only, and hashed latest readings.
6. `ai_diagnosis.validate`: rejected a whole answer for one abbreviated tag or three derived numbers.
7. `ai_diagnosis.stream_ollama`: a read timeout inside the stream surfaced as `ConnectionError`, so it was reported as "Ollama is not running".
8. `llm_provider._stream_ollama`, `edge_slm.call_local_edge_slm`: swallowed exceptions.

## Wrong answers even when the model ran

9. `ai_diagnosis.build_facts`: used latest values, so a recovered incident looked healthy.
10. `evidence_engine.evaluate_event`: could rank the alarm's own measurement as the contributor, and alarm bits scored a meaningless 100 %.

## Plant and alarm defects

11. `generator.advance_step`: wrote every plant tag, so a new scenario cleared earlier alarms.
12. `alarm_urgency.rank_incidents`: ignored whether alarms were live.
13. `generator.last_scenario`: only the last run could be fixed.
14. `routes._last_simulated_timestamp`: a global floor pushed simulated time ahead of the clock.
15. `database.get_events_in_window`, `get_actions_in_window`: compared timestamps as text.
16. `App.refreshActiveIncident`: kept asking for a deleted incident.

## Not implemented at audit time

Knowledge graph (only hard-coded Pump-02 edges), forecasting and anomaly models, time-series page, Engineer Mode. MQTT and REST adapters exist but are not started by `server.py`; they remain so. OPC UA is read-only and tested with mocks only.
