# OpsLens Architecture

OpsLens runs on one edge PC: a FastAPI backend with SQLite, and two React/Vite frontends. Everything that decides *what happened* is deterministic Python. Language models are optional: they explain facts already established, and every answer is checked against the record.

```text
                 ┌──────────────── Telemetry sources (read-only) ────────────────┐
                 │  Synthetic scenario generator  │  OPC UA client (M241/M251)   │
                 │  data_source = "simulated"     │  data_source = "live_plc"    │
                 └───────────────┬────────────────┴──────────────┬───────────────┘
                                 ▼                               ▼
                         SQLite tag_readings  ◄──── write-through, timestamps, quality
                                 │
          ┌──────────────────────▼───────────────────────────────────────────────┐
          │ Orchestrator (asyncio)                                               │
          │  1 Time-series contextualiser (baseline, trend, rate of change)      │
          │  2 Prioritiser  — alarm input? status change? analog excursion?      │
          │  3 Dedup + alarm lifecycle (fingerprints, re-activation = repeat)    │
          │  4 Correlator   — equipment, tag topology DAG, time window           │
          │  5 Event story  — recorded chronological sequence (not causal proof) │
          │  6 Evidence engine — heuristic ranking (NOT a probability)           │
          │  7 SOP retrieval — curated manual section │ Qdrant vectors │ BM25    │
          │  8 Guidance     — verbatim SOP steps + calculated evidence           │
          └──────────────────────┬───────────────────────────────────────────────┘
                                 │  persisted per stage (SQLite) + SSE broadcast
          ┌──────────────────────▼───────────────────────────────────────────────┐
          │ FastAPI  /api/...                                                    │
          │  events, incidents, alarms/active, alarms/history, alarms/reference, │
          │  events/{id}/brief, assistant/ask(+stream), handover, scenarios,     │
          │  sops/*, datasource, copilot/ai-status, diagnostics, integration/status │
          └───────────────┬──────────────────────────────┬───────────────────────┘
                          ▼                              ▼
              Operator HMI (5173)               Engineering console (5174)
              alarms · story · brief ·          Scenario Lab · Procedures (PDF
              Ask OpsLens · handover            ingest) · Alarm Reference · Metrics
```

## Components and status

| Component | File(s) | Status |
|---|---|---|
| Synthetic scenarios (eight incidents, one without an SOP, + legacy Pump-02/Compressor cases) | `incident_catalog.py`, `generator.py`, `scenarios/`, `simulated_plant.py` | Implemented. A run touches only its own tags. Unfixed incidents stay broken across runs and backend restarts, and any of them can be fixed. |
| Read-only OPC UA client | `adapters/opcua_adapter.py` | Implemented and unit-tested with mocked clients; started by `server.py` only when `PLC_OPCUA_ENABLED=true` and a mapping file exists. **Not tested against real lab hardware.** |
| MQTT / REST adapters | `adapters/mqtt_adapter.py`, `rest_adapter.py` | Classes exist; **not wired** into `server.py`. Planned. |
| Tag registry + topology DAG | `tag_registry.py`, `topology.py` | Implemented (demo configuration). |
| Knowledge graph | `knowledge_graph.py`, `/api/knowledge-graph` | Generated from the catalog and the incident record. Links are labelled configured, recorded or hypothesis. |
| Time-series analytics | `analytics.py`, `/api/telemetry/*`, `/api/analytics/*` | Rolling median/MAD anomaly markers, gated local-trend forecast with backtest, predictive warnings. Results: `docs/ANALYTICS_EVALUATION.md`. |
| **Alarm configuration from the SOP PDF** | `config/alarm_reference.json`, `alarm_config.py` | **New.** 31 parameters, page-cited. Used for tag classification, SOP limit checks, P1/P2/P3 deadlines and the conflict report. |
| Alarm lifecycle | `alarm_lifecycle.py`, `dedup.py`, `database.py` | Implemented; **fixed** (see CHANGELOG). States exposed as ACTIVE_UNACK / ACTIVE_ACK / RTN_UNACK / NORMAL by `/api/alarms/active`. |
| Correlator, event story, evidence engine | `correlator.py`, `event_story.py`, `evidence_engine.py` | Implemented. |
| SOP retrieval | `sop_retriever.py`, `demo_sop_sections.py`, `vector_store/` | Three paths, always reported: `bundled_pdf_section` (curated page ranges of SOP-MAN-000 for the primary scenarios), `qdrant_vector_search` (needs sentence-transformers + Qdrant), `bm25` (always available). |
| PDF SOP ingestion | `pdf_sop_ingestion.py` | Implemented. Selectable-text extraction (no OCR). Uploaded SOPs are never auto-approved; "Draft" covers are detected. **New:** stores keyword-only when Qdrant/embeddings are missing. |
| Verbatim procedure steps | `sop_steps.py` | **New.** Returns the numbered steps of the SOP's "Procedure" section exactly as printed, with page numbers. |
| Guidance | `guidance_generator.py`, `fallback_template.py` | Deterministic by default (verbatim steps); optional LLM drafting via `OPSLENS_GUIDANCE_USE_LLM=true`, run in a worker thread with its own DB connection. |
| **Operator brief** | `operator_brief.py`, `/api/events/{id}/brief` | **New.** 8-part, LLM-free brief: observed alarm, hypotheses, OBSERVED vs CALCULATED evidence, missing evidence, verbatim steps, approval status, verification criteria, resolution. |
| **Incident diagnosis** | `ai_diagnosis.py`, `/api/events/{id}/diagnosis(/stream)` | Facts include what happened during the incident, not only latest values. One background job per context; readers attach and can leave. Newer context supersedes older jobs. Results stored in `ai_diagnoses`, keyed by a hash of the facts. Line-level validation. |
| Ask OpsLens assistant | `assistant.py`, `context_builder.py`, `llm_provider.py` | Implemented: record-grounded deterministic answer; optional LLM rephrase validated for numbers, units, timestamps and SOP text. |
| LLM providers | `llm_provider.py`, `edge_slm.py` | LM Studio Local Model API (local, OpenAI format), Groq, Gemini, OpenAI, Anthropic, none. Health check reports the loaded model and context; warm-up at start. |
| Shift handover | `shift_handover.py`, `operational_history.py` | Implemented; shift window from `.env`. |
| Operator audit log | `operator_actions.py` | Implemented; allow-listed action types; never writes to a PLC. |

## Key design rules

1. **Alarms come from alarm inputs.** Only `ALM-*`, `*-FAULT` and `*-TRIP` tags create alarm records. `P101-RUN` (status) and analog measurements are correlation evidence. A status tag is evidence only when it leaves its configured normal state (`status_normal_states` in `alarm_reference.json`).
2. **Acknowledgement is not clearing.** Acknowledging records who/when; the alarm stays ACTIVE until the source input reads FALSE at a later timestamp. Resolving an incident additionally requires operator confirmation.
3. **Repeat count = re-annunciation.** Polled samples of an alarm that stays TRUE are not repeats. A repeat is counted when the input returned to normal and became active again, or the source explicitly re-sent it.
4. **Numbers are calculated in Python.** Margins to limits, deltas, deadlines and percentages are computed by the backend; LLM text containing a number that is not in the record (or attaches it to the wrong tag/unit) is rejected.
5. **Model time is scarce.** One generation at a time. Nothing that polls may call the model. Shift summaries by the model run only on request.
6. **No invented procedures.** Steps shown to the operator are copied from the SOP text with a page number, and labelled `DRAFT / REFERENCE ONLY — NOT APPROVED` unless the document is marked approved.
7. **Honest provenance.** Every reading carries `data_source`; the HMI shows SIMULATED for simulator data and never "PLC DISCONNECTED" for a simulation. `/api/health` reports the retrieval method actually available.
8. **Nothing optional is mandatory.** LM Studio, Groq, Qdrant, sentence-transformers, Docker and the PLC can all be absent; the app starts and the operator brief still works.

## Database

SQLite file `edge_intelligence/opslens.db` (or `OPSLENS_DB_PATH`), WAL mode. Tables: `tag_readings`, `candidate_events`, `individual_alarms`, `alarm_occurrences`, `alarm_transitions`, `correlated_events`, `event_stories`, `evidence`, `sop_matches`, `guidance`, `operator_actions`, `assistant_messages`, `shift_handovers`, `handover_item_responses`, `ai_diagnoses`.

v2.2 adds one table, `ai_diagnoses`, created with `CREATE TABLE IF NOT EXISTS`. Existing data is untouched. Each thread has its own SQLite connection.

## Ports

| Port | Service |
|---|---|
| 8001 | FastAPI backend (`OPSLENS_PORT`) |
| 5173 | Operator HMI (Vite, proxies `/api` → 8001) |
| 5174 | Engineering console (Vite) |
| 1234 | LM Studio Local Model API (optional) |
| 6333 | Qdrant (optional) |

## SOP path to the AI (v2.2)

`sop_retriever` matches by catalog scenario, equipment, alarm and passage relevance. `sop_context.build_sop_context` then reads the same document the viewer shows, using `demo_sop_sections.load_demo_sop_document`, and returns the SOP ID, title, revision, approval, source document, pages, steps and passages. `ai_diagnosis` gives this to the model and adds `sop_context` and `sop_checks` to the result. The checks shown to the operator are copied from the SOP, not from the model.

## Operator Console and incident lifecycle (v2.2)

`App.jsx` holds one selected incident, its brief, insight (scores), timeline, lifecycle and one diagnosis stream. `components/home/` renders the Home screen (`Chrome.jsx`: top, notification and bottom bars; `HomeMain.jsx`: the six panels), the tab pages (`Pages.jsx`) and the overlays (`Overlays.jsx`: Scenarios sidebar, Confirm Resolution, Related Incidents). The tabs reuse `components/console/IncidentSections.jsx`, `DiagnosisCard.jsx`, `KnowledgeGraphPage` and `TimeSeriesPage`. Styles for 1280 × 800 are in `home.css`. `incident_lifecycle.evaluate` is the only place that decides state and whether resolving is allowed. `POST /api/actions` enforces the same rule, so the disabled button is not the only guard. `incident_views` builds `/timeline` and `/evidence-detail`. OpsLens only records operator actions: no route writes to a PLC.

## Proposed on-premise deployment (not implemented or validated)

Today OpsLens runs on one PC: the FastAPI backend, SQLite, the SOP index, the HMI (5173) and the Engineering Console (5174), fed by the simulator or the read-only OPC UA adapter. The deployment below is a **proposal** for a plant; none of it has been built or tested against production OT.

```
Plant OT (PLCs / SCADA historian)
   │  read-only (OPC UA client account, or historian export), through the plant DMZ
   ▼
Central on-premise OpsLens server
   ├─ Ingestion + evidence engine + cause candidates + urgency  (Python, as today)
   ├─ SOP library and index (approved + draft, status kept)
   ├─ Incident / shift database (PostgreSQL instead of SQLite)
   └─ On-premise LLM (e.g. a Qwen model served locally) — explains facts, never computes them
   ▼
Several HMI clients (control room, mobile panel, supervisor)  — browser, read-only
```

Design rules that would carry over: no writes to PLCs or SCADA; acknowledgement stays in the plant's alarm system (OpsLens's Acknowledge button is local to OpsLens and not connected to SCADA); no plant data leaves the site when the local LLM is used. Open items before any pilot: OT security review, user authentication and roles, historian integration, SOP approval workflow, and validation on real incident records.
