# Mentor checklist — where OpsLens stands

Status as of 8 November 2026. "Built" means the code exists and its tests pass here. Tests ran against stand-ins (a fake Groq, local stand-ins for FastAPI, PyMuPDF and the frontend build), not the real services.

## Architecture

| Layer | In OpsLens |
|---|---|
| HMI runtime | Home screen for 1280 × 800 (React, port 5173): notification bar for the current incident with Acknowledge, six panels, a bottom bar with Verify Status and Confirm Resolution on every screen |
| Copilot service layer | FastAPI backend: `ai_diagnosis.py` (LLM orchestration, Groq or a local model in LM Studio), `ask_analysis.py` (Ask OpsLens), `context_builder.py` (30-minute context window), REST and streaming APIs |
| Data and intelligence | SQLite: `tag_readings` (time series), `individual_alarms` and `alarm_transitions` (alarm log), `correlated_events` and `event_stories` (event log), `operator_actions` (audit log), SOP manual (PDF) with BM25 retrieval |

## 1. Latency and real-time performance

| Point | Status | How |
|---|---|---|
| Context assembly under 2 s | Built and measured | On a test database with 3.9 million readings (30 days, all tags), worst case: context 0.5 s, AI facts 0.6 s, bottom line 0.13 s. The latest-reading query was made about 10× faster for this. Run `tools/measure_latency.py` on your own database |
| Streaming | Built | AI Diagnosis streams words as they are generated. Ask OpsLens streams each line once it has passed the record check, so unchecked text never reaches the operator |
| Caching | Built | Diagnoses are cached per incident facts (memory and SQLite), so reopening costs no LLM call. Identical Ask questions are cached. The bottom line and history are built without an LLM and show at once. For a new incident the LLM is still called by design, because its readings differ |

## 2. Safety, guardrails and determinism

| Point | Status | How |
|---|---|---|
| Grounding and RAG | Built | The AI gets the retrieved SOP steps and passages (from the same PDF as the viewer), the incident readings, alarms, possible causes from the record and history. Every line is checked against the record; lines with unknown numbers, times, tags or SOP steps are removed, and the answer says how many. Values worked out from the readings a sentence names (difference from a limit, percentage, rounding) are accepted. SOP checks shown to the operator are copied word for word. Draft SOPs show DRAFT — NOT APPROVED |
| Confidence and flags | Built | `GET /events/{id}/insight` gives High / Medium / Low with reasons, plus flags: unprecedented (no earlier incident), several causes, ambiguous, draft SOP, data quality, escalate. It is a rule-based rating of the evidence, not a probability |
| Human in the loop | Built | OpsLens only reads and records. No route writes to a PLC. Acknowledge and resolve are recorded operator actions; resolve is refused until the recovery criteria pass |

## 3. Context and data integration

| Point | Status | How |
|---|---|---|
| Time-window correlation | Built | Event Story merges readings, alarm states and operator actions in time order. New: operator actions and other alarms on the same equipment in the 30 minutes before the alarm are listed, given to the AI and offered as a possible cause ("timing alone does not prove cause"). The simulator does not yet write operator actions before an alarm, so in a demo this shows only actions logged from the console |
| Stateful conversation | Built | Ask OpsLens keeps a conversation per incident and sends the last messages with each question. The AI also gets earlier incidents of the same alarm or equipment, with when, how long and the resolution notes, so "Has this happened today?" can be answered |

## 4. UI and operator ergonomics

| Point | Status | How |
|---|---|---|
| Non-intrusive | Built | Ask Chatbot in the bottom bar opens it. It docks on the right between the top and bottom bars and the panels move aside, so the notification bar and every button stay usable |
| BLUF | Built | The notification bar: the component to check first with one High / Medium / Low confidence label (tap for reasons), and the suggested SOP with Full / Partial / Weak match. Next Best Action says where its steps come from. Ask OpsLens answers start with "Bottom line:" and, for broad questions, "Top 2 steps:" and "History:" |

## 5. Demo resilience and impact

| Point | Status | How |
|---|---|---|
| Demo resilience | Built | Scenario Lab (port 5174) replays eight simulated incidents, labelled SIMULATED. Seven have an SOP; the eighth (UV disinfection) has none in the manual and shows the no-SOP path: OpsLens says so, gives AI suggestions labelled as not from an SOP and advises escalation. If Groq fails, the diagnosis falls back to the local model if it is running, otherwise to a clearly labelled answer built from the record. Ask OpsLens falls back to its evidence-based answer. The bottom line never needs the LLM |
| Impact metrics | Measured, not claimed | `GET /api/metrics/impact` gives MTTR from resolved incidents, context assembly time and now AI time to first word and total time. A % MTTR reduction is only computed against a baseline you supply. We have not measured real operators, so we do not claim a figure such as 40 % |

## Suggested way to state impact in the pitch

State what was measured: "Context ready in under 0.6 s on 30 days of history; first AI words in N s; bottom line before the AI." Present MTTR reduction as a hypothesis to validate in a pilot, comparing incidents handled with and without OpsLens.
