# Test Report — OpsLens v2.9 (review feedback: evidence-based ranking, inspector, validation)

**Date:** 11 November 2026. Fresh test database; stand-ins for the LLM (fake LM Studio/Ollama server), no Groq call, no Windows run.

| Suite | Result |
|---|---|
| Backend `pytest tests` | **539 passed**, 0 failed (+213 subtests). New: `tests/test_validation_scenarios.py` (tests A–D and all 8 scenarios against their reference; reference read only by the inspector and tests), `tests/test_limit_state.py` (active breach, worsening, predicted crossing, no limit configured). Two existing tests updated for the reworded prompt heading |
| Frontend `npm test` | **54 passed** |
| Frontend build | esbuild bundle of the HMI and console builds cleanly. `vite build` could not run here (no network to install Vite); run `npm run build` on the laptop |
| Browser run `e2e/home_1280.e2e.cjs`, 1280 × 800, all 8 scenarios, fresh DB | **131 checks passed**, 0 failed. Updated checks: Evidence Support button and its "Why this score?" breakdown; evidence-check steps count as labelled non-SOP steps; SOP-linked steps counted over the first two pages of the plan |
| Console errors | None from the app. One "Failed to load resource" per page is the web font, blocked by this sandbox's network |
| Backend log | No tracebacks or 500s; the only warning is the stand-in LM Studio warm-up |

**Validation (simulated telemetry, see docs/VALIDATION.md):** 14 of 14 cases match their reference. Changing the injected telemetry changes the result (D1: trip moved after the alarm → clog leads; D2: clog's supporting readings removed → clog becomes insufficient evidence; B: one bad meter → no cause, "check the instrument"). Scenario 1's reference was corrected after a timing defect was fixed (see CHANGELOG).

Checked on screenshots (scenario 2, lift station): Evidence Support 90 % · Strong; Root Cause comparison "65 evidence points against 90 for P-101 thermal overload trip. Where they differ: recorded trip or fault (-25)"; wet well ACTIVE BREACH (no early warning); inflow "NO ALARM LIMIT CONFIGURED" (checked through the forecast API); Inspector shows computed readings, breaches, alarm times and candidate points.

Not verified here: real Groq and LM Studio answers with the new prompt; Vite production build; Windows start scripts; any real plant data.

---

# Test Report — OpsLens v2.2.0 (shift panel, evidence score, deviation, a past month of history)

**Date:** 10 November 2026. Fresh database each run; the past month of simulated history is seeded on start (23 incidents, about 65,000 readings, about 13 s).

| Suite | Result |
|---|---|
| Backend `pytest tests` | **522 passed** (latest run, 0 failed) (+205 subtests). One run had a timing failure in `test_plant_behaviour.py` (restart test) that passed on two reruns; new since then: Groq retry when cut off, non-breaking hyphens. Earlier new: `tests/test_seed_history.py` (seeded incidents resolved, in the past, labelled, valid readings, never replayed by the live stream, removable), `tests/test_confidence_score.py` (score and level always agree), shift rotation tests |
| Frontend `npm test` | **51 passed** (new: deviation from threshold, clock formatting) |
| Browser run, all 8 scenarios, fresh database with seeded history | **128 checks passed**, 0 failed (confidence now checked as "Level confidence · N%" with the level matching the score) |

Checked by hand on screenshots: every live incident shows its 3 earlier incidents (2 for UV) in Related Incidents; the shift panel shows 7–8 open incidents (the live ones only, no duplicates from history); deviation text on an active lift alarm ("+0.25 past limit · 7%"); 30-day trend with dated axis.

Not run here: the older root-level acceptance scripts (`edge_intelligence/test_*.py`), which need Ollama and Qdrant.

---

## Earlier: OpsLens v2.2.0 (SOP search: Qdrant first, then BM25)

**Date:** 9 November 2026. Qdrant and sentence-transformers are not installed in this test environment and could not be downloaded, so the Qdrant path is tested with stand-ins. Here every scenario's SOP was found by BM25 and agreed with its alarm link. Real Qdrant ranking still has to be checked on the plant PC (see TROUBLESHOOTING, "Is SOP search using Qdrant?").

| Suite | Result |
|---|---|
| Backend `pytest tests` | **501 passed**, 0 failed (+205 subtests); new: `tests/test_plant_sop_index.py` (10 tests, 8 subtests) |
| Frontend `npm test` | **44 passed** |
| Browser run, all 8 scenarios, fresh database | **128 checks passed**, 0 failed |

---

# Test Report — OpsLens v2.2.0 (one cause story on every screen)

**Date:** 9 November 2026. Same limits as below: stand-ins for Groq, LM Studio, FastAPI, Vite and PyMuPDF, and no Windows run.

| Suite | Result |
|---|---|
| Backend `pytest tests` | **491 passed**, 0 failed (+197 subtests). New in the latest update: fix → acknowledge → resolve works and is counted in Metrics (`test_incident_lifecycle.py`); an assumed baseline from `.env` is used, labelled and overridden by the page (`test_operational_metrics.py`). Earlier in this update: `tests/test_cause_roles.py` runs all 8 scenarios and checks that the leading hypothesis is the same in the insight, the Home summary, the knowledge graph, the AI prompt and the fallback text. It also checks that likely effects are never shown as causes, that an AI line naming another cause is replaced by the record's sentence, and that a trip time belongs to this incident and not an earlier one |
| Frontend `npm test` | **44 passed**. New: `lib/eventStory.test.js` covers the plain "What happened" sentence, alarms recorded as observations, a fault that comes on first, and trips added from the record |
| Browser run, `e2e/home_1280.e2e.cjs`, 1280 × 800, all 8 scenarios, fresh database | **128 checks passed**, 0 failed. New: Assessment, Root-Cause Evidence and the record name the same leading cause; the event story is a plain sentence; no plan step is cut off; the knowledge graph has one leading cause, five columns, a breadcrumb, no cut names, and fades unlinked nodes on hover |

Checked in the browser: Metrics with a 45 min baseline after one resolved scenario showed 4.7 min and "89.6 % · Faster than the assumed 45 min"; the baseline stays after a reload.

Checked by hand: the leading hypothesis for all 8 scenarios at simulation speeds 2000× and 300× (identical).

Known limit: rerunning the browser test on a database already full of earlier runs is slower and sometimes times out waiting for a page; on a fresh database it passes.

---

# Test Report — OpsLens v2.2.0 (Home panels without scrolling)

**Date:** 8 November 2026. Same limits as below (stand-ins for Groq, LM Studio, FastAPI, Vite and PyMuPDF; no Windows run). The stand-in model cuts plan text at 70 characters, so some step texts in the screenshots end mid-word; real answers do not.

| Suite | Result |
|---|---|
| Backend `pytest tests` | **483 passed**, 0 failed (+189 subtests) |
| Frontend `npm test` | **38 passed** |
| Browser run, `e2e/home_1280.e2e.cjs`, 1280 × 800, all 8 scenarios | **111 checks passed**, 0 failed. New per scenario: no element inside a panel scrolls; every block that does not fit is behind a "+N more" button; the full record story is in the Diagnosis pop-up; the plan pages forward and back |

---

# Test Report — OpsLens v2.2.0 (AI plan and scenario 8)

**Date:** 8 November 2026. Same limits as below (stand-ins for Groq, LM Studio, FastAPI, Vite and PyMuPDF; no Windows run). The stand-in model writes simple fixed sentences, so the screenshots show the layout, not real answer quality.

| Suite | Result |
|---|---|
| Backend `pytest tests` | **482 passed**, 0 failed (+189 subtests). New: scenario 8 never gets an SOP, the manual has no UV procedure, the no-SOP plan has only labelled AI suggestions; the per-incident tests now cover eight scenarios |
| Frontend `npm test` | **38 passed** |
| Browser run, `e2e/home_1280.e2e.cjs`, 1280 × 800, local model through the stand-in LM Studio | **98 checks passed**, 0 failed, no server tracebacks. Per scenario: the AI's likely cause and the record in Diagnosis; a plan linked to SOP steps (seven scenarios) or "No SOP found" with only AI suggestions (scenario 8) |

---

# Test Report — OpsLens v2.2.0 (LM Studio)

**Date:** 8 November 2026. Same limits as below. LM Studio itself was not run here: a stand-in server speaking its Local Model API (`/v1/models`, `/api/v0/models`, streamed `/v1/chat/completions`) was used.

| Suite | Result |
|---|---|
| Backend `pytest tests` | **478 passed**, 0 failed (+177 subtests). New `test_lmstudio_transport.py` (health, loaded context warning, model-name matching, old Ollama settings, timeouts, warm-up); the diagnosis, SOP and history tests now run against the stand-in LM Studio |
| Frontend `npm test` | **38 passed** |
| Browser run, `e2e/home_1280.e2e.cjs`, backend with `OPSLENS_LLM_PROVIDER=lmstudio` | **89 checks passed**, 0 failed; 11 diagnoses written through the stand-in LM Studio; top bar shows *AI · LM Studio*; no server tracebacks |

Not tested: real LM Studio or LM Studio Bionic, the `lms` command used by `start_lmstudio.bat`, and real Qwen answer quality or speed.

---

# Test Report — OpsLens v2.2.0 (record check and simpler Home)

**Date:** 8 November 2026. Same environment and limits as below (stand-ins for Groq, FastAPI, Vite and PyMuPDF; no Windows run).

| Suite | Result |
|---|---|
| Backend `pytest tests` | **473 passed**, 0 failed (+177 subtests). New: `test_record_check.py` (worked-out values pass, made-up values, times and tags fail), Ask OpsLens tests for kept worked-out values, partial answers with a note, the readable fallback and no caching of fallbacks |
| Frontend `npm test` | **38 passed**, 0 failed |
| Browser run, `e2e/home_1280.e2e.cjs`, 1280 × 800, clean database | **89 checks passed**, 0 failed, no server tracebacks. One earlier run of the same script stopped once at scenario 6 (Home did not switch to the incident picked in the Incidents tab within 15 s); the rerun passed |

Not measured here: how often real Groq answers now pass the check. Check the backend window for `removed:` lines after a few diagnoses.

---

# Test Report — OpsLens v2.2.0 (Home screen update)

**Date:** 7 November 2026. Same environment and limits as below: no real Groq, FastAPI, Vite, PyMuPDF or PLC, and the `.bat` files were not run. The frontend was bundled with a local esbuild script, with icons replaced by placeholders.

| Suite | Result |
|---|---|
| Backend `pytest tests` | **465 passed**, 0 failed (+177 subtests), on the stand-ins |
| Frontend `npm test` | **38 passed**, 0 failed |
| Browser run, `e2e/home_1280.e2e.cjs`, 1280 × 800, clean database, Groq stand-in | **81 checks passed**, 0 failed; no server tracebacks |

What the browser run checks: all seven scenarios on Home (right alarm, suggested SOP shown as draft, six panels, scores with the "not a probability" note, where the next action came from, readings, no page scroll, bar heights 96 / 88 / 76 px); tap sizes of every bar button; Confirm Resolution blocks Resolved while active; Acknowledge leaves the alarm active and then disappears; Verify Status before and after the simulated fix; Resolved logged with a note; Related Incidents; View Suggested SOP and Escape; Chatbot not covering the bars; View Details; every tab with the simple top bar and the bottom bar; forecasting on; SOPs screen; Previous Shift Details; Active Alarms button.

---

# Test Report — OpsLens v2.2.0 (Operator Console update)

**Date:** 5 November 2026. Same environment and same limits as the section below: no real Qwen, FastAPI, Vite or PLC, and the `.bat` files were not run.

| Suite | Result |
|---|---|
| Backend `pytest tests` | **464 passed**, 0 failed, on the stand-ins |
| 11-step lifecycle, all seven scenarios, through the API | **7 passed** (part of the backend suite) |
| SOP regression tests A to E, on the backend response | Passed (part of the backend suite) |
| Frontend `npm test` | **38 passed**, 0 failed |
| Dependency checks: Python and npm scripts | Passed in temporary folders with fake installers. No real pip or npm install |
| Browser run, `e2e/hmi_1366.e2e.cjs`, clean database, Groq stand-in | **75 checks passed**, 0 failed; no server tracebacks |
| Same browser run through the Groq path, using a local stand-in for Groq | **66 checks passed**. Real Groq was not called from here |
| `tools/evaluate_diagnosis.py` against the Groq stand-in | Runs all seven scenarios and writes the report. Real answer quality not measured here |
| Latency, 3.9 million readings (30 days, all tags) | Worst: context 499 ms, AI facts 556 ms, bottom line 129 ms (target 2000 ms) |
| Ollama stopped, lift station incident | Labelled *SOP retrieval only*, SOP-LFT-001 steps shown with pages |

Known limits: simulated samples can be dated about two minutes ahead of the clock when scenarios run back to back at high speed. Operator actions can then appear before later alarms in the Event Story. A clear dated ahead of the clock counts its hold time from when it was first seen.

---

# Earlier report — OpsLens v2.2.0

**Date:** 5 November 2026. **Environment:** Linux container, Python 3.13, Node.js 22, Chromium (Playwright).

**Read this first. What was NOT run here:**
- **Real Qwen inference.** Ollama and its model registry are blocked in this environment and the laptop was not linked. Every "AI-GENERATED" result below came from a scripted fake Ollama that speaks the HTTP protocol. **No statement here says anything about Qwen's real speed or answer quality.** Run `tools/verify_ai.py` on the laptop.
- **Real FastAPI, rank-bm25, PyMuPDF, Vite, Tailwind 3, lucide-react, React 18.** PyPI and npm were blocked. The backend ran on small local stand-ins (FastAPI on Starlette, BM25, PDF reading through pypdfium2). The HMI was bundled with esbuild, React 19, placeholder icons and Tailwind 4. These stand-ins are not in the delivery. Run `run_tests.bat` on the laptop to repeat this with the pinned packages.
- **`npm run build`, Qdrant and sentence-transformers, the lab PLC, and the Windows `.bat` files** (including the new `start_ollama.bat`).

| Suite | Result |
|---|---|
| Backend `pytest tests` | **401 passed**, 0 failed (+172 subtests), on the stand-ins described above |
| Frontend `npm test` | **34 passed**, 0 failed |
| Browser run, `e2e/hmi_1366.e2e.cjs`, clean database, fake Ollama | **53 checks passed**, 0 failed; 7 incidents; no server tracebacks |
| Ollama stopped, in the browser | Labelled *SOP retrieval only*, reason shown, SOP steps and evidence kept |
| 1024×700 layout | Columns stack, no horizontal scroll |
| Legacy scripts outside `tests/` | Not run. Not maintained |

Each of the seven scenarios was checked for: correct alarm, one incident, correct SOP shown as draft, source label, 1–5 checks, at least 3 readings, no page scroll at 1366×768, simulated fix clears the alarm. Acknowledgement left the alarm active. All 12 Engineer pages rendered with no failed API request and no page error.

Known limitation: when two scenarios sharing a tag (dissolved oxygen, effluent ammonia, airflow) run straight after each other, simulated timestamps can sit up to about 2 minutes ahead of the clock. Before v2.2 this drift built up across every run.

Analytics results are in `docs/ANALYTICS_EVALUATION.md`, on simulated data only. In short: anomaly markers rarely false-flag but catch slow ramps late, and the forecast is only modestly better than "no change".

---

# Previous: Test Report — OpsLens v2.1.0

**Date:** 4 November 2026 · **Environment:** Linux container, Python 3.13, Node.js 22, Chromium (Playwright).
**Not available here:** a real Ollama/Qwen model (downloads are blocked in this container and the laptop folder was not shared with this session), Docker/Qdrant, the lab PLC. Nothing below is claimed as tested that was not run.

## Summary

| Suite | Command | Result |
|---|---|---|
| Backend (`edge_intelligence/tests`) | `python -m pytest tests -q` | **362 passed**, 0 failed (+152 subtests) |
| Same, venv from `requirements-dev.txt` (no PyTorch) | same | **362 passed** |
| New `tests/test_six_incidents.py` | — | 18 passed: exact values (incident + after fix) for all six, normal-period values, tag→equipment ownership, LiftStation-01 vs Pump-02 isolation, SOP-SLD-001 text check, per-scenario SOP retrieval, timestamps strictly increasing, causal order, start/stop/409/details/trends, back-to-back runs (2 rounds) |
| New `tests/test_ai_diagnosis.py` (fake Ollama `/api/chat` over real HTTP) | — | 10 passed: prompt scope, streaming, payload (`keep_alive`, `num_ctx`), validation (foreign tag, invented numbers, invented SOP step, control claim, "approved SOP", missing sections), Ollama down / model missing / provider none → labelled fallback, cache, health `last_diagnosis_call`, SSE endpoint |
| Frontend unit tests (`hmi_ui`) | `npm test` | **29 passed** |
| HMI + console builds | `npm run build`, `npx vite build workbench` | **OK** |
| End-to-end, 1366×768 (`hmi_ui/e2e/hmi_1366.e2e.cjs`) | backend + both UIs running | **21/21 checks for each of the six scenarios**, with reset between runs and in **two back-to-back rounds without reset** (12 runs) |
| Legacy scripts outside `tests/` (`edge_intelligence/test_*.py`) | `pytest test_*.py` | 18 passed, 13 failed — unchanged from v2.0 baseline, not maintained |

### End-to-end checks (per scenario)
Console lists exactly six incidents and none of the removed ones · Start becomes Stop while running and is never re-enabled mid-run · run completes with this run's incident · no "could not be started" error · **no vertical or horizontal page scroll at 1366×768** · no overlapping panels · all panels inside the viewport · workflow bar fully visible and buttons not clipped · data marked SIMULATED · SOP shown as *DRAFT — NOT APPROVED* · diagnosis labelled AI or *DETERMINISTIC FALLBACK* (and "Not AI-generated" when fallback) · trend tiles show only the incident's own tags · all sidebar pages open · shift handover page renders · Acknowledge targets the banner alarm and does not clear the input · simulated fix reaches after-fix values and clears the alarm input · no uncaught page errors.

## AI response time — what was and was not measured

**Real Qwen latency was not measured**: no Ollama runtime is reachable from this container. To measure on the laptop (Ollama running, backend with `OPSLENS_LLM_PROVIDER=ollama`):

```
cd edge_intelligence
python tools/benchmark_ai.py live            # all six scenarios
```
It prints, per scenario, the **before** path (`/api/assistant/ask`, not streamed) and the **after** path (`/diagnosis/stream`: time to first token, total, cached repeat) and writes `benchmark_results.json`.

**Measured here** (`python tools/benchmark_ai.py capture`, requests recorded by a fake Ollama — sizes, not latency):

| Scenario | Before: prompt chars (`/api/generate`, not streamed, num_predict 320) | After: prompt chars (`/api/chat`, streamed, num_predict 650) |
|---|---|---|
| Rags jam the bar screen | 17,802 | 5,518 |
| Lift station pumps clog | 19,324 | 6,674 |
| Nocardia foam | 20,448 | 6,419 |
| RAS pump fails | 17,746 | 5,928 |
| CHP engine trips | 18,475 | 6,420 |
| Centrate poisons the inlet | 19,803 | 7,045 |

The prompt the model must read is ~3× smaller (prompt evaluation dominates CPU latency for a 3B model), the operator sees text as soon as the first token arrives, the HMI no longer fires an extra assistant question on every incident, and a repeat view is served from cache. The pipeline overhead was checked against a test server that streams slowly: first token reached the browser 1.8 s after the request with a 1.5 s artificial server delay, i.e. ~0.3 s OpsLens overhead.

---

# Previous: Test Report — OpsLens v2.0.0

**Date:** 3 November 2026 · **Environment:** Linux container, Python 3.13.18, Node.js 22.23.4, Chromium (Playwright) for UI checks.
**Not available in this environment:** Ollama, Docker, Qdrant server, sentence-transformers/PyTorch, the lab PLC/HMI. Results for those are marked below; nothing is claimed as tested that was not run.

## Summary

| Suite | Command | Result |
|---|---|---|
| Backend unit + integration (`edge_intelligence/tests`) | `python -m pytest tests -q` | **348 passed**, 0 failed (+64 subtests) — baseline before changes: 292 passed, 12 failed |
| Same suite in a **fresh venv from `requirements-core.txt`** (no PyTorch) | same | **348 passed** |
| New: `tests/test_alarm_reference_and_brief.py` | — | 37 passed |
| New: `tests/test_ollama_transport.py` (fake Ollama HTTP server) | — | 6 passed |
| Frontend unit tests (`hmi_ui`) | `npm test` | **31 passed** |
| Operator HMI production build | `npm run build` | **OK** |
| Engineering console build | `npx vite build workbench` | **OK** |
| Legacy acceptance scripts (`edge_intelligence/test_*.py`, outside `tests/`) | `pytest test_*.py` | 18 passed, 13 failed — **pre-existing**: 15 passed, 16 failed on the original code. Superseded by `tests/`; not part of the maintained suite |
| Clean-copy portability check | copy of the delivered file set → new venv → `server.py` → scenario → brief; `npm ci` + build | **OK** |

## Part 15 checklist

| # | Requirement | How it was tested | Result |
|---|---|---|---|
| 1 | Backend imports and startup | `server.py` started from a clean copy with `.env.example`; also with Ollama selected but absent | Pass. Startup logs *"Ollama not ready … Deterministic answers remain available"* |
| 2 | Frontend dependency/build | `npm ci`, `npm run build`, workbench build | Pass |
| 3 | API health | `/api/health`, `/api/diagnostics`, `/api/datasource`, `/api/copilot/ollama` | Pass; retrieval reported as `bm25_keyword_fallback (sentence-transformers not installed)` |
| 4 | Scenario execution | All 11 scenarios via `POST /api/scenarios/run` (10 primary + Scenario 7) | Pass: each → `awaiting_corrective_action`, 1 incident, repeat 1, no tracebacks |
| 5 | Event creation | Pipeline test + API; one incident per run | Pass |
| 6 | Alarm trigger conditions | `ALM-WW-101-HH` TRUE at level ≥ 3.6 m (p.14); limit checks at 3.8/3.9/1.7 m; low-direction RAS flow | Pass |
| 7 | Alarm acknowledgement | `POST /api/actions` ACKNOWLEDGE → `/api/alarms/active` = `ACTIVE_ACK`, input still TRUE | Pass (API + `AlarmStateTests`) |
| 8 | Return-to-normal | FALSE at a later timestamp → `RTN_UNACK` / `NORMAL`; valve-restriction simulated recovery → `alarm_clear_observed`, brief = *returned to normal — awaiting operator confirmation* | Pass |
| 9 | Alarm history | `/api/alarms/history`, `/api/alarms/active`, occurrences table; checked independently of the scenario response | Pass; only alarm inputs recorded |
| 10 | SOP PDF ingestion | SOP manual uploaded via `POST /api/sops/ingest-pdf` without Qdrant → `stored_keyword_only`, auto-detected *draft*, 45 chunks, source PDF served; invalid PDF → 422 | Pass. **Vector indexing not tested** (no Qdrant/embeddings) |
| 11 | SOP retrieval | Curated manual sections for 7 wastewater scenarios; BM25 discrimination tests; plant-wide manual visibility | Pass (BM25 + curated). Qdrant path: not tested here |
| 12 | Evidence Engine | Existing evidence tests + brief ranking label ("NOT a probability") | Pass |
| 13 | Guidance with deterministic provider | Verbatim SOP-LFT-001 steps 1–3 with pages; regenerated when SOP/evidence change | Pass |
| 14 | Provider-unavailable fallback | Ollama absent: assistant answers deterministically in **0.11 s**; `complete_chat` raises `ProviderError` (no invented text); fake slow Ollama times out after one attempt at the configured budget | Pass |
| 15 | Missing sensor data | `limit_context(None)` not applied; brief lists MISSING/STALE tags and un-instrumented SOP parameters | Pass |
| 16 | Incorrect or stale readings | STALE quality → limit not evaluated; 0 A on a tripped pump not treated as low-load; unit/tag-swapped LLM numbers rejected | Pass |
| 17 | Duplicate scenario submissions | Second POST → 409 with `running_scenario_id`; still exactly 1 incident | Pass |
| 18 | Shift handover | Existing handover tests; `/api/handover/current` with configured shift | Pass |
| 19 | Frontend/backend integration | Playwright: HMI shows *Active alarms 2*, *Data source SIMULATED*, *AI DETERMINISTIC*, Operator Brief with SOP limit table; console Scenario Lab (*Repeat ×1*) and Alarm Reference; 390 px width has no horizontal scroll | Pass |

## Concurrency reproduction (root cause of "temporarily unavailable")

One writer thread + three reader threads on the same `Database`:

| Code | Result (3 runs) |
|---|---|
| Original `database.py` | `InterfaceError('bad parameter or other API misuse')` in 3/3 runs; corrupted reads (`TagReading.tag = None`) in 2/3 |
| v2 per-thread connections | 0 errors |

## Dependency status

| Dependency | Status |
|---|---|
| Ollama + `qwen2.5:3b` | **Not tested locally** (not installed). Transport tested against a fake Ollama HTTP server (health, generate, stream, keep-alive, timeout, warm-up). **Requires verification on the original laptop.** |
| Groq | Not tested (no key; no outbound access). Existing mocked provider tests pass. |
| Qdrant + sentence-transformers | **Not tested** (not installed). BM25 fallback tested; existing mocked Qdrant validation tests pass. **Requires verification on the original laptop** (`docker compose up -d qdrant`). |
| Docker | Not available. Optional. |
| PLC / OPC UA (M241, HMIST6600) | Unit-tested with mocked OPC UA clients only. **Requires access to the actual lab equipment.** |
| Windows `.bat` scripts | Written for Windows `cmd` (CRLF); **not executed here** (Linux). Requires a first run on the laptop. |

## How to re-run on the laptop

```bat
run_tests.bat
```
or
```bat
cd edge_intelligence && .venv\Scripts\python -m pytest tests -q
cd ..\hmi_ui && npm test && npm run build
```
With Ollama running, also check `http://127.0.0.1:8001/api/copilot/ollama` → `"status_label": "AI: Ollama ready"`, then ask a question in Ask OpsLens and confirm `llm_used: true` in the response (or a `fallback_reason` explaining why the answer was replaced).
