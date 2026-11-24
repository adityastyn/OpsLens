# Troubleshooting

First stop for any problem: **http://127.0.0.1:8001/api/diagnostics** (database row counts, simulation state, retrieval method actually in use, LLM provider, PLC adapter, alarm reference). Then the specific endpoints below.

| Endpoint | Answers |
|---|---|
| `/api/health` | Is the backend up? Which SOP retrieval path is really available? |
| `/api/datasource` | SIMULATED / LIVE PLC / STALE / PLC DISCONNECTED / NO DATA, and why |
| `/api/copilot/ai-status` | Groq or LM Studio: is it running, is the model there and loaded, with what context, what timeout is used |
| `/api/alarms/active` | Alarm inputs and their states |
| `/api/scenarios/status` | Simulation state, last error, current event |
| `/api/integration/status` | PLC/OPC UA adapter state |

---

## Startup

**`setup_windows.bat` says Python not found** — install Python 3.10+ from python.org with *Add python.exe to PATH*, open a new terminal, run setup again.

**pip fails on `torch` / `sentence-transformers`** — the setup script automatically falls back to `requirements-core.txt`. Everything works; SOP retrieval uses BM25 and the HMI/diagnostics say so. To enable vector search later: `edge_intelligence\.venv\Scripts\python -m pip install sentence-transformers` (needs internet once to download `all-MiniLM-L6-v2`).

**Backend window closes / `address already in use` on 8001** — another backend is running. Close the other window, or find it: `netstat -ano | findstr :8001` then `taskkill /PID <pid> /F`. To use another port set `OPSLENS_PORT` in `.env` **and** `VITE_API_BASE_URL=http://127.0.0.1:<port>/api` before `npm run dev` (and change the proxy target in `hmi_ui/vite.config.js` and `hmi_ui/workbench/vite.config.js`).

**Frontend shows a blank page / `vite` not found** — run `npm ci` in `hmi_ui` (or `setup_windows.bat`). Do not copy `node_modules` from another computer.

**Port 5173/5174 in use** — close old `npm run dev` windows; `5174` uses `--strictPort` and will refuse to start rather than move silently.

## Dashboard messages

**"Active alarms are temporarily unavailable" (intermittent, older versions)** — root cause found and fixed in v2: the backend shared one SQLite connection between the event loop and the Ask OpsLens worker thread, so concurrent requests interleaved transactions (`cannot commit - no transaction is active`, `bad parameter or other API misuse`). Each thread now has its own connection. If you still see it, the backend is not reachable: check the backend window and `/api/health`.

**Data source shows `DISCONNECTED` while a scenario runs (older versions)** — fixed. The status now comes from `/api/datasource`; a simulation always shows `SIMULATED`. `PLC DISCONNECTED` appears only when `PLC_OPCUA_ENABLED=true` and the session is down. `NO DATA` means nothing has been received yet.

**"null shift · null" in Shift Handover** — fixed; set `OPSLENS_SHIFT_NAME`, `OPSLENS_SHIFT_START`, `OPSLENS_SHIFT_END` in `.env` (24-hour `HH:MM`). Without them the card says *Shift timing not configured*.

**Starting a scenario used to clear other incidents' alarms** — fixed in v2.2. Each incident stays active until its own simulated fix runs. Select it in the Scenario Lab and press *Simulate the fix*.

**Repeat count ×400+** — fixed. Periodic samples of an alarm that stays TRUE no longer count as repeats. Press *Reset demo data* in the Scenario Lab to clear old counts.

**`P101-RUN` listed as an active alarm** — fixed. Status tags are evidence only. Old records created before the fix are filtered out of `/api/alarms/active`; *Reset demo data* removes them.

## Alarm lifecycle

**Confirm resolved is greyed out**: open *Recovery criteria* in the action panel. All alarm inputs must be normal, readings inside limits, equipment back to normal, the inputs held clear for `OPSLENS_RECOVERY_HOLD_SECONDS`, data valid, and every alarm acknowledged.

**The AI says no SOP covers the event**: it should not. Such lines are removed. If you see one, your backend is older than v2.2.0's SOP fix, or the cached answer predates it. Press Retry.

## Scenario Lab

**HTTP 409 "A synthetic scenario is already running"** — expected for a duplicate submission while a run is in progress; no second incident is created. The response includes `running_scenario_id` and `elapsed_seconds`. A run still marked running after `OPSLENS_SCENARIO_STALE_SECONDS` (default 600) is treated as stale and a new run is allowed. A 409 does not mean the pipeline failed — check `/api/scenarios/status` and `/api/alarms/active`.

**"Scenario accepted. Waiting for the event pipeline"** — normal for a few seconds at 10× speed. If it never completes, check `/api/scenarios/status` → `error`, and the backend window.

**Acknowledgement time looks earlier than the alarm (older versions)** — fixed: fast-forwarded scenarios are stamped so the last sample is "now", never in the future.

## Dependencies

`start_all.bat` compares `edge_intelligence/requirements*.txt` and `hmi_ui/package-lock.json` with the fingerprint saved after the last successful install.

| What you did | What happens at startup |
|---|---|
| First launch after `setup_windows.bat` | Installs once, then saves the fingerprint |
| Normal launch | Nothing is installed |
| Added a package to `requirements.txt` | Installs the change, without upgrading other packages or PyTorch |
| Changed `package-lock.json` | Runs `npm install` |
| Changed source code only | Nothing is installed. Just restart |

**"Python environment not found"**: `.venv` is missing. Run `setup_windows.bat`. OpsLens never deletes or recreates it.

**Install failed and startup stopped**: the fingerprint is not saved, so the next launch tries again. Fix the error shown, usually the network or a package version.

## Groq (cloud AI)

Set `OPSLENS_LLM_PROVIDER=groq` and `GROQ_API_KEY=...` in `edge_intelligence\.env`, then restart. The default model is `openai/gpt-oss-120b`; `OPSLENS_GROQ_MODEL` changes it. `EDGE_LLM_MODEL` stays the local backup model.

| Message | Meaning | What to do |
|---|---|---|
| Groq limit reached (HTTP 429) | OpsLens waited once, up to 20 s, and the limit still applied | Wait a minute, then press Retry. Run scenarios one at a time |
| authentication failed | The key is missing or wrong | Check `GROQ_API_KEY`, then restart |
| Groq could not be reached, is out of quota, or rejected the key | No internet, a Groq outage, the free limit, or a wrong key | The local model in LM Studio answers if its Local Model API is running; otherwise the record-based answer. Set `OPSLENS_DIAG_LOCAL_BACKUP=false` to skip LM Studio |

**Answer quality.** Run `.venv\Scripts\python tools\evaluate_diagnosis.py` from `edge_intelligence` with OpsLens running. It runs all eight scenarios and writes `reports\diagnosis_eval_<time>.md` with each answer, what the record check removed, and a rough cause score.

**Token use.** One diagnosis with `OPSLENS_GROQ_REASONING=medium` and the repair call can use about 5,000 to 7,000 tokens, so expect about one new diagnosis a minute on the free limit. Set `OPSLENS_GROQ_REASONING=low` and `OPSLENS_DIAG_REPAIR=false` to use less.

**Several keys.** Put `GROQ_API_KEYS=key1,key2` in `.env` and restart. `tools\check_groq_keys.py` checks each key once and shows Groq's remaining limits. The top-bar status endpoint `/api/copilot/ai-status` lists each key as ready, rate-limited or rejected.

Limits apply to the Groq organization, so several keys in one account share them. Registering extra accounts to get more quota is against Groq's Acceptable Use Policy.

## AI / LM Studio

**First check, always:** with the backend running, `cd edge_intelligence` and run `.venv\Scripts\python tools\verify_ai.py`. It tests LM Studio's Local Model API, the model, one real answer and one real diagnosis, and says which step failed.

**AI pill says `AI · offline`** — LM Studio's Local Model API is not running. Start it in LM Studio (Local Model API or Developer page), or run `start_lmstudio.bat`, which uses `lms server start`. If `lms` is not found, set `OPSLENS_LMS_EXE` in `.env` to the full path of `lms.exe`.

**AI pill or diagnosis says `model missing`** — `EDGE_LLM_MODEL` does not match a model LM Studio lists. The message names the models it found; copy one into `.env`. Names like `qwen2.5-7b-instruct` and `lmstudio-community/Qwen2.5-7B-Instruct-GGUF` are treated as the same model. The *Coder* versions are not.

**"Reload the model with Context Length 8192"** — the model is loaded with a small context (often 4096). OpsLens's prompt is 5,000–7,000 tokens, so the start of it would be cut off. In LM Studio, eject the model and load it again with Context Length 8192 or more, or let `start_lmstudio.bat` load it.

**Diagnosis times out** — on a CPU the first word includes loading the model and reading the prompt. Check that LM Studio shows the model fully on the GPU. Raise `OPSLENS_DIAG_FIRST_TOKEN_TIMEOUT` (default 240 s) or `OPSLENS_DIAG_TOTAL_TIMEOUT` (default 480 s, 600 in `.env.example`). Reduce output with `OPSLENS_DIAG_MAX_TOKENS`.

**Old `.env` with Ollama settings** — `OPSLENS_LLM_PROVIDER=ollama` is read as `lmstudio`, and `EDGE_LLM_URL=...:11434` is replaced by LM Studio's `http://127.0.0.1:1234`, with a warning in the backend window. Update `.env` from `.env.example` when you can. Ollama is no longer used.

**Lines were removed from the AI answer** — the record check dropped lines naming a tag, number, time or SOP step that is not in the incident record. Each is listed in the backend window as `removed: ... [reason]`. The answer is rejected only if fewer than 6 lines survive or more than 75 % are removed.

**Source labels** — *AI-generated*: real model output that passed the check. *SOP retrieval only*: the model did not answer, but a matching SOP was found. *AI unavailable*: the model did not answer and no SOP matched. *Deterministic fallback*: the model is switched off, or its answer was rejected.

**HMI froze, or AI always timed out, on versions before v2.2** — every handover refresh called the model on the server's main loop, up to many times a second during a scenario. Fixed; shift summaries by the model now run only from the *Write AI summary* button.

**Groq** — set `OPSLENS_LLM_PROVIDER=groq` and `GROQ_API_KEY` in `.env` (never in the frontend). Without internet, use `lmstudio` or `none`.

**Diagnosis is slow** — the first call after start-up loads the model (watch for *LM Studio warm-up* in the backend window). Measure with `python tools/benchmark_ai.py live`.

## Scenario Lab

**"Another scenario is already running"** — wait for the run (progress bar) or press **Stop**.

**"… is still active in open incident …"** — the same scenario's alarm is still active. Press *Simulate the fix* (after-fix values) or *Reset demo data*, then start it again.

**"The same alarm re-annunciated while its earlier incident is still open"** — by design the recurrence joins the open incident (repeat count) instead of creating a duplicate. Close the earlier incident on the HMI to get a new one.

**Old scenarios (Pump-02, filters, the old filters/UV case) are gone** — only the eight catalog incidents are offered. Scenario 8 is a new UV disinfection incident with no SOP. Legacy runs for old records: `OPSLENS_ENABLE_LEGACY_SCENARIOS=true`.

## SOP retrieval / Qdrant

**`Qdrant ... connection refused`** — Qdrant is optional. Without it, retrieval uses BM25 (reported in `/api/health` → `components.sop_retrieval`). To start it: install Docker Desktop, then from the project root `docker compose up -d qdrant`. Vector search also needs `sentence-transformers` and its model cached locally.

**Uploaded PDF says "Added for keyword (BM25) search · vector index unavailable"** — the PDF was stored and is searchable by keyword; re-ingest after starting Qdrant for vector search.

**"OCR required" / extraction failed** — the PDF has no selectable text (scanned). OCR is not implemented; use a text PDF or OCR it first.

**SOP-MAN-000 hidden for LiftStation-01 ("does not match LiftStation-01") (older versions)** — fixed: plant-wide manuals apply to every wastewater process area.

**Uploaded SOP shows "DRAFT / REFERENCE ONLY — NOT APPROVED"** — by design. Uploading never approves a document.

## Data

**Start from a clean database** — stop the backend and delete `edge_intelligence\opslens.db*` (back it up first if you need the history), or use *Reset demo data* in the Scenario Lab.

**Logs** — the backend logs to its console window. To keep a file: `start_backend.bat > backend.log 2>&1`.


## Is SOP search using Qdrant?

SOP search tries **Qdrant first, then BM25**, and checks the result against the SOP linked to the alarm tag.

1. **Look at the backend window.** It prints one of these lines:
   - "Plant SOPs in Qdrant: N chunks from 13 plant SOPs indexed; SOP search tries Qdrant first." Qdrant is used.
   - "Qdrant is not running; SOP search uses BM25." Start Docker Desktop, then run `docker compose up -d qdrant`.
   - "Not indexed (…). SOP search uses BM25. Install the embedding model…" Run `pip install -r edge_intelligence\requirements.txt` while online. This needs sentence-transformers and the all-MiniLM-L6-v2 model.
2. **Open http://127.0.0.1:8001/api/sop-search/status.** `first_available` should be `qdrant_vector_search`.
3. **Run a scenario.** The SUGGESTED SOP box should say "Full match · Qdrant". The SOP pop-up's "How this SOP was found" box shows the search order and scores. "· tag link" means the search ranked another SOP first or found nothing, so the SOP linked to the alarm is shown.
4. **Old Pump-02 SOPs in the Qdrant dashboard** are from the early prototype. The search ignores them. To remove them, delete the collection (`Invoke-RestMethod -Method Delete http://localhost:6333/collections/opslens_sops`), restart the backend so it indexes the plant SOPs again, then upload your PDFs again in Procedures.


## "AI · key missing" although the key is in .env

1. **Read the first lines of the backend window:** "Settings: …\.env (found, N values loaded). AI provider: groq. Groq keys found: N."
   - **"NOT FOUND":** the file isn't at `edge_intelligence\.env`, or it's really `.env.txt`. In File Explorer, turn on View → Show → File name extensions to check.
   - **"Groq keys found: 0"** with the file found: the line has a typo, a `#` in front of it, or a space before `=`. It must be exactly `GROQ_API_KEY=gsk_...`, or `GROQ_API_KEYS=key1,key2`.
   - **"AI provider: lmstudio" or "none":** another setting overrides `OPSLENS_LLM_PROVIDER`. Check `echo $env:OPSLENS_LLM_PROVIDER` in PowerShell.
2. **Restart after editing.** The backend reads `.env` only when it starts, so close its window and run `start_all.bat` again.
3. **Keep keys out of chats and screenshots.** Share only the "Groq keys found: N" line.
