# Changelog

## v2.9 update — 2026-11-12 — HMI on the EcoStruxure panel: graphs and cut-off content

- **Data and charts on the panel.** The HMI asked for its data at `http://localhost:8001/api`, which only works in a browser on the backend PC. On a panel (EcoStruxure Operator Terminal Expert Web Browser object, or any screen opening `http://<edge-pc>:5173`) "localhost" is the panel itself, so every request failed and the trends stayed empty. The default is now same-origin `/api`, forwarded to the backend by the HMI server (proxy target `127.0.0.1:8001`). `VITE_API_BASE_URL` still overrides it.
- **Nothing cut off at 1.5× (1920 × 1200).** The "+N more" and step-paging layouts converted measured sizes by dividing by the zoom, which is wrong on browsers that report sizes unzoomed (older Chromium in panels). They now measure the ratio. They also measure again when a web font finishes loading, when text changes, when the window is resized and every 1.5 s if anything sticks out, so a step, alarm, reading or button is never half hidden.
- **Root-Cause Evidence graph.** The evidence → cause → SOP step graph came after the causes and was always pushed out of the panel. It is now first, and the panel is taller.
- **Notification bar.** The alarm state ("Active, not acknowledged") no longer runs off the bar; the title steps its font size down before ending in "…".
- **Time-Series.** The chart and its time axis fit above the bottom bar; the sensor list shows the whole sensor name.
- **Older panel browsers.** Polyfills (`src/lib/polyfills.js`) for `Array#at`, `replaceAll`, `flat/flatMap` and others the charts use; no regex look-behind; JavaScript compiled for Chrome 80; an error boundary shows a message instead of a blank screen if a page fails to draw.
- Originals of every changed file are in `_backup_before_display_fix/`.

## v2.9 update — 2026-11-11 — The incident bar after the fix

Once every alarm input of the viewed incident is back to normal (after the simulated fix, or in the field), the notification bar stops showing it as an alert: a green tick with **BACK TO NORMAL** (and "was CRITICAL" in small text), no urgency score, a green outline and a green line under the top bar, "N alarms cleared". If the alarms still need acknowledging, Acknowledge is a plain button; when the recovery checks pass, a green **Confirm Resolution** button opens the existing resolution dialog. After confirmation the bar shows **RESOLVED**. The incident is not closed automatically: the operator confirms it after checking in the field.

## v2.9 update — 2026-11-11 — HMI sized for a 1920 × 1200 panel

The operator HMI keeps its 1280 × 800 layout and scales it evenly to fill a larger screen (`hmi_ui/src/lib/uiScale.js`): scale = the smaller of width/1280 and height/800, 1× to 2× in 0.05 steps, so **1920 × 1200 → 1.5×** with no scrollbars. Viewport-height styles and the measured pop-up/"+N more" layouts account for the scale. `?scale=1` in the address bar turns it off; `?scale=1.25` forces a value. Nothing changes at 1280 × 800.

Event story: a LOW advisory alarm is no longer named as "the alarm that came on"; the story names the first real alarm.

## v2.9 update — 2026-11-11 — Alarm priorities, LOW advisory alarms, top bar and incident order

**Alarm priorities (configuration).** Every alarm input has a priority (`incident_catalog.alarm_inputs()[tag]["priority"]`, also on each alarm record as `priority` and in `plantTags.generated.js` as `ALARM_PRIORITY`): an incident's primary alarm takes the incident's severity (Critical/High → HIGH, Medium → MEDIUM), equipment fault/trip inputs are HIGH, other alarm limits MEDIUM, advisory inputs LOW.

**LOW advisory alarms** (`Signal.advisories`): raised from the simulated telemetry when a reading leaves its normal range, cleared by the fix. They are alarm inputs only, not SOP limits, so they never count as "past a limit" in readings, trends or the cause ranking, and they never become the primary alarm (registry weight 0.5).

| Scenario | LOW alarm |
|---|---|
| 1 Bar screen | ALM-SCR-110-SCRN-ADV Screenings removal above normal (> 0.5 m³/h) |
| 2 Lift station | ALM-WW-101-IN-ADV Inflow to wet well above normal (> 500 m³/h) |
| 3 Foam | ALM-AER-201-FOAM-ADV Foam building (> 5 cm); ALM-EFF-301-TURB-ADV Effluent turbidity above normal (> 6.5 NTU) |
| 4 RAS | ALM-SLB-201-ADV Sludge blanket rising (> 1.0 m) |
| 5 Biogas | ALM-FLR-601-FLOW-ADV Flare gas flow detected (> 20 m³/h) |
| 6 Centrate | ALM-EFF-301-NH4-ADV Effluent ammonia above normal (> 2.5 mg/L) |
| 7 Blower | ALM-BLW-201-PRS-ADV Air header pressure below normal (< 450 mbar); ALM-EFF-301-NH4-ADV |
| 8 UV | ALM-UV-901-DOSE-ADV UV dose below normal (< 30 mJ/cm²) |

**Top bar.** Priority chips red (high/critical), orange (medium), yellow (low), counting every active alarm by its own priority across all open incidents. The line under the bar follows the incident being viewed: its priority colour while open, green once resolved, dark blue if logged unresolved. Larger date and time.

**Notification bar.** Outlined top and bottom in the incident's priority colour; the time of the group's first alarm next to the equipment; `why?` is a button that lists every open incident in order with the values the ranking used (live alarms, safety rules, time to limit, band and score, when it opened) and why each sits above the next (`ranking_facts`, `ranking_vs_next` from `alarm_urgency.rank_incidents`).

**After the fix** (Time-Series): checked end to end on scenario 3; the card fills once the primary alarm clears.

The AI system prompt was shortened slightly (same rules) to stay inside the prompt budget with the extra alarms.

## v2.9 update — 2026-11-11 — Shift handover written for the incoming operator

`edge_intelligence/handover_view.py`, `api/routes.py` (`_incident_handover`), `operational_history.py`, `ShiftHandoverPage.jsx`.

- **Shift summary:** plant status (open incidents by severity, alarms active / not acknowledged), this shift's counts, a numbered "Review first" list, repeated alarms, and what OpsLens does not track. One point per line, no database ids (incidents are named by alarm and a short number).
- **One card per open incident**, ordered active-critical first: **Now** (abnormal readings with their normal range, or "all back within normal"), **Likely cause — not confirmed** (the same leading and contributing causes as the Root Cause page), **Done** (logged actions grouped: "Ravi acknowledged 6 alarms at 15:40 (in OpsLens; not sent to SCADA or the PLC)", plus operator notes), **Next** (SOP recovery checks with pages, in operator words), **Escalate if** (SOP limits whose action is to notify someone, or "No SOP covers this alarm: escalate to the shift supervisor"). Two open incidents for the same alarm are flagged.
- Incoming-shift panel: summary keeps its line breaks, local times instead of raw timestamps, items named without full ids.
- Existing API fields are kept; the new ones are added (`number`, `short_id`, `status_line`, `now`, `likely_cause`, `done`, `next_checks`, `escalate_if`, `repeat_note`).

## v2.9 fix — 2026-11-11 — SOP search fell back to BM25 with an old Qdrant collection

Qdrant SOP search read only the 12 closest chunks. A collection that still holds the early prototype's SOPs (Pump-02 and similar) can fill those 12, all of which are ignored, so the search fell back to BM25 although Qdrant was running. It now looks at up to 200 chunks before falling back, and the reason it gives says whether the plant SOPs are still being indexed.

## v2.9 update — 2026-11-11 — Plant SOPs treated as approved; simpler reading bars

**Plant SOPs approved by a site setting** (`sop_approval.py`, `OPSLENS_PLANT_SOPS_APPROVED`, default `true`). The bundled plant SOPs are treated as the plant's approved procedures everywhere: no "draft / not approved" wording on the HMI or the console, SOP steps in Next Best Action are labelled **SOP-VERIFIED**, the AI prompt states the SOP is approved, and Evidence Support earns its "SOP is approved" points (Scenario 2: 90 % → 100 %). Uploaded PDFs keep the approval recorded at ingestion; old prototype SOPs that are not plant SOPs show "REFERENCE". Set the setting to `false` to show the recorded draft status. The source PDFs are unchanged, so their own pages still read "Revision 0 — Draft for review". The test suite runs with the setting off (tests/conftest.py) to keep checking draft handling; `test_sop_approval.py` and `test_plan_sources.py` cover it on.

**Important readings.** Each bar shows only the normal range (dark grey) and the value now (▼): green inside the normal range, amber outside it, red past an alarm limit, grey when the equipment is stopped. The red limit marks and the trend line are gone, and the bar is scaled to the normal range and the value. Under the value: just the signed amount past the limit (or outside normal), e.g. **+0.25**, without "past limit · 7 %". The full wording stays in the tooltip.

**Top bar line** follows the plant state: red with a HIGH alarm, amber with MED/LOW only, green when nothing is active (it was always green, even above an active P1).

## v2.9 update — 2026-11-11 — Next Best Action source labels; repeat runs after a resolved incident

**Source labels** (`hmi_ui/src/lib/planSource.js`, `HomeMain.jsx`; backend adds provenance only). The plan's steps, wording and order are unchanged.

| Label | Shown when |
|---|---|
| TELEMETRY EVIDENCE | an evidence check whose recorded tags are known (shown next to the label, e.g. `P101-FAULT · TIT-101-WDG · MOT-101-CUR`). Without identifiable tags the step gets no label |
| HISTORICAL RESOLUTION | an earlier incident's recorded resolution, with its incident id and "Not a guaranteed solution and not an approved SOP instruction." |
| SOP-VERIFIED | an SOP step with its step and page, from an **approved** SOP |
| SOP DRAFT · NOT APPROVED | the same from a draft SOP (all plant SOPs today) |
| AI-GENERATED · NOT SOP-VERIFIED | the AI's own step |
| OPERATOR VERIFICATION | the final check that readings are back within normal, with the tags to check |

Backend: evidence steps carry `evidence_tags` / `alarm_ids` (only tags in the incident record; normal supporting readings are not cited); the plan carries `sop_approved` and `sop_document`; the insight's SOP block carries `approved`. No SOP reference is shown without an SOP id and step.

**Bug fixed: a repeat run after resolving created no incident.** Starting a scenario again after its incident was resolved reported "no incident": the resolved incident's cluster was kept open (same scenario) and swallowed the new alarm. Resolved incidents are now sealed at a run boundary; an open incident of the same scenario still takes repeats.

## v2.9 fix — 2026-11-11 — Scenario Lab said "Another scenario is already running (unknown)"

- Starting a scenario whose alarm was still active in its open incident returned 409 for that reason, but the console showed every 409 as "Another scenario is already running (unknown)". It now shows the backend's reason ("ALM-WW-101-HH is still active in open incident … Run the simulated fix … first"), and keeps the "already running" text only when a run is actually in progress.
- "Start another run" is disabled while the selected scenario's alarm is still active, with a tooltip saying to simulate the fix first. It enables again after the fix.

## v2.9 update — 2026-11-11 — Review feedback: evidence-based ranking, proof it is computed, presentation

**Positioning.** README: "AI-assisted, evidence-based incident investigation and decision support for wastewater treatment plants". Read-only decision support; the operator acts through the plant's own systems.

**Competing causes ranked on their own evidence** (`cause_candidates.py`)
- Each candidate earns evidence points: recorded trip 25 (standby 15), changed before the alarm 20, deviation size up to 15, agreeing readings up to 15, SOP limit action 10, configured path to the alarm 10, earlier same fix 5 (defined, not yet wired), minus up to 15 for related readings that stayed normal. Before, a trip always outranked everything by kind alone.
- A candidate first seen after the primary alarm cannot lead when an earlier one exists.
- Roles: Leading, Contributing, Related condition, Possible consequence, Insufficient evidence. A lone abnormal meter whose related readings stayed normal becomes "Insufficient evidence → check the instrument" instead of a cause.
- Every candidate has a ranking note ("65 evidence points against 90 for P-101 thermal overload trip. Where they differ: recorded trip or fault (-25)"); Root Cause page shows the comparison and a factor table.

**Proof it is backend-driven**
- `GET /api/events/{id}/inspection` and an Engineering Console **Inspector** page: predefined vs computed, readings (first/worst/now), first limit crossings, alarm first-on times, event order, candidates with points, ordering. The scenario's reference outcome is shown only there, in a closed box, never on the HMI.
- `REFERENCE_OUTCOME` (in `incident_catalog.py`) is read only by `incident_views.build_inspection` and tests; a test enforces this.
- Scenario name and ID removed from the AI prompt header and the console incident title (they leaked the scripted story).
- `tests/test_validation_scenarios.py`: tests A (genuine fault), B/B2 (misleading sensor), C (two competing faults), D1/D2 (changed timing / changed supporting readings) and all 8 scenarios against their reference. Results in `docs/VALIDATION.md`.

**Evidence Support display.** The existing evidence score (formula unchanged) is shown as "EVIDENCE SUPPORT 90 % · Strong evidence" with a bar; the pop-up has the definition (not a probability), the flags and "Why this score?" with each check's points. History text says how many earlier incidents are simulated; the SOP check says "draft, not approved".

**Investigation story.** A step bar on the investigation tabs (Alarm priority → Event story → Root-cause evidence → Knowledge graph → Incident analysis & AI → SOP → Trends → Verify recovery) with a Next button.

**Next Best Action.** Evidence checks first (confirm a recorded trip at the panel, inspect the readings behind an SOP "suspect" action together, check a lone instrument), then SOP steps, then call-outs, then "verify these readings are back within normal". Checks only, never control.

**Time series.** Limit status card: ACTIVE BREACH, ACTIVE BREACH + WORSENING TREND, PREDICTED LIMIT CROSSING, WITHIN LIMITS, or NO ALARM LIMIT CONFIGURED, plus whether the reading is inside its normal range. Readings already past a limit are listed as breaches, never early warnings. Forecast labelled "statistical linear trend; not a machine-learning model"; unreliable forecasts say so. Chart shows "now" and the predicted crossing.

**Human in the loop.** Acknowledge tooltip: local to OpsLens, not connected to SCADA.

**Docs.** ARCHITECTURE: proposed on-premise deployment (not implemented or validated). `docs/DEMO_SCRIPT.md`: 10-minute walkthrough + Q&A. README: how a cause is chosen; predefined vs computed.

**Bugs found and fixed**
- *Primary alarm "first triggered" time moved* when the incident's primary alarm was reassigned, so a trip after the alarm could count as before it. Now the start of the alarm input's unbroken active run (`operator_brief.primary_alarm_first_on`). This changed scenario 1: its injected timing puts the rake trip after the screen alarm, so the scenario's **reference** was corrected (inflow above one-screen capacity leads; the trip is a later condition). The ranking was not tuned to fit.
- Evidence fallback listed a normal status ("P-101 running") as evidence; only abnormal readings now.
- Readings with nested brackets in the normal range ("900 to 1,200 m³/h (plant)") were not parsed, so the evidence check read "Check Inlet flow vs one-screen capacity high." with no values.
- Inflow at 610 m³/h (normal 380–450, no SOP limit) was shown as "WITHIN LIMITS"; it now says no alarm limit is configured and that it is outside the normal range.
- Evidence Support in the 78 px "Check first" card overflowed; compact two-row layout, 36 px tap target.

## v2.2.0 update — 2026-11-10 — Why one incident is ranked first

- **"why?" under the incident counter (1/2).** It says in plain words which rule put this incident ahead of or behind its neighbour, for example "Ahead of UVChannel-01: same safety rules and band; this incident has the higher urgency score (90 vs 85)", and lists the order of the rules: live alarms, safety rules, time to limit, urgency band, urgency score, started first. Backend: `ranking_reason` on `/api/incidents/ranked`.
- **Earlier fixes are merged by their words**, ignoring numbers, punctuation, spacing and case (`src/lib/mergeFixes.js`, tested), so one fix on two dates is one step: "fixed this way 2 times".
- **Leading hypothesis headline** drops the AI's labels ("Confidence:", "Most likely cause:", "(leading hypothesis)").
- Test: for all 8 scenarios, the scripted cause, story and expected outcome never appear in the AI prompt.

## v2.2.0 fix — 2026-11-10 — Forecast horizon showed 1800 s

- The forecast took its time step from the last 400 readings, which now include the seeded past month (one reading every 30 min). The time step became 1800 s, so a 30 s forecast showed as "Next 1800 s". It now uses only the latest unbroken run of readings. Test: `test_a_past_month_at_another_spacing_does_not_set_the_time_step`.

## v2.2.0 fix — 2026-11-10 — Same earlier fix listed twice

- Next Best Action (no SOP): earlier fixes with the same words are one step ("fixed this way 2 times, last …"), whatever the spacing, punctuation or letter case. Before, two notes that differed only in spacing or punctuation appeared as two identical-looking steps.

## v2.2.0 update — 2026-11-10 — Shift panel, readings, SOP header, urgency

- **A shift is always running.** If `OPSLENS_SHIFTS` is not set, a Morning 06–14 / Evening 14–22 / Night 22–06 rotation is used. Set `OPSLENS_SHIFTS=` (empty) to use the single shift instead.
- **On duty defaults to Ravi.** A browser that saved the old "Control room operator" switches to Ravi.
- **Shift panel** no longer shows the Data (Simulated) and AI rows. The AI status is now in the Engineering Console header, next to OpsLens ("AI · Groq", "AI · LM Studio", …).
- **Important readings pop-up:** the long sentences under each reading are gone; each row shows the value and the short deviation. A reading of 0 on something that is never 0 in service (motor current, pump flow) shows **STOPPED** in grey instead of a red "100 % under". A low-limit alarm at 0 still shows as an alarm.
- **SOP page:** no DRAFT — NOT APPROVED badge in the top-right corner. Approval status stays in the SOP list and in the revision line ("Revision 0 (Draft for review)").
- **Urgency score under the severity** in the red box ("URGENCY 90 /100"). It is the backend's rule-based urgency (deviation, rate, evidence, persistence, data quality), not a probability; the reasons are in its tooltip.

## v2.2.0 update — 2026-11-10 — Scenario reference; scenario 8 checked against the plant

- New `docs/SCENARIOS.md`: all 8 scenarios with alarms, equipment, readings (normal → incident → after the fix), limits, deviations, status signals, trips and SOP coverage. Generated from `incident_catalog.py`.
- Scenario 8 (UV): the UV dose at the incident is now **15 mJ/cm²** (was 18). Dose = intensity × exposure time, and with flow unchanged 3.5 mW/cm² gives about 15 mJ/cm²; 18 implied a longer exposure.
- Knowledge graph: `UVChannel-01` now belongs to a **Disinfection** area. It was "Other", so the UV incident's path skipped the area step.
- Open point: SOP-MAN-000 samples final effluent for "residual disinfectant" (p.27), which UV does not leave. See docs/SCENARIOS.md.

## v2.2.0 fix — 2026-11-10 — Related Incidents empty on Windows

- **Seeding the past month is sturdier on Windows.** The temporary database is closed before its folder is deleted, and a cleanup error no longer counts as a failure. Success is now judged by whether the history reached the database, not by the exit code.
- **An empty Related Incidents panel says why:** the past month is still being added (about a minute after the first start; it rechecks every 4 s), adding it failed (with the reason), it is turned off, or the stored history has nothing for this alarm. It points to Engineering Console > Scenario Lab > Past month of incidents > Add one month.
- `GET /api/history/seeded` also returns `enabled_on_start`.

## v2.2.0 cleanup — 2026-11-10 — Unused folders removed

- Removed `ops-lens/` and `opslens-generator/`: earlier hackathon versions with overlapping code. No script, setting or code in the running app used them.
- Removed the 10 old test scripts in the root of `edge_intelligence/` (`test_audit.py`, `test_guidance.py`, …). They were written for the first prototype (Ollama, old SOPs) and failed; `run_tests.bat` never ran them. The real tests are in `edge_intelligence/tests/`.
- Removed `docs/previous/` (the README from before v2) and the v2.1 screenshots. `docs/screenshots/` now has current ones, shown in the README.

## v2.2.0 fix — 2026-11-10 — Groq answers rejected as "deterministic fallback"

- **gpt-oss hyphens no longer look like invented numbers.** Groq's gpt-oss-120b writes "P‑101" with a non-breaking hyphen (U+2011). The checker did not see it as a tag, read "101" as a number that is not in the record, and removed the line. In a typical lift answer that removed 11 good lines and the answer was rejected. Model text is now normalised (plain hyphens and spaces, no invisible characters) before checking, in the diagnosis and in Ask OpsLens.
- **A Groq answer cut off by the output limit is asked again once.** gpt-oss counts its reasoning against the 4000-token limit, so a long think could end the answer after section 4 ("missing sections: Resolution and recovery check, …"). OpsLens now asks once more with low reasoning and keeps the more complete answer. The Assessment panel shows "The answer was cut off; asking again with shorter reasoning".
- A rejected answer still says which model wrote it and why.
- Tests: `tests/test_groq_diagnosis.py` (retry with low reasoning; non-breaking hyphens kept).

## v2.2.0 update — 2026-11-10 — Shift panel, evidence score, deviation, a past month of history

- **Top bar.** The SIM and "AI · Groq" pills are gone. The person icon opens the **current shift**:
  - which shift is running and the time left
  - who is on duty (editable)
  - open alarms, not-acknowledged alarms and open incidents
  - the last handover, the data source (Simulated, not live PLC) and the AI status.
  Shifts can be set as a rotation, `OPSLENS_SHIFTS=A:06:00-14:00,B:14:00-22:00,C:22:00-06:00`, so a shift is always running. With one shift (`OPSLENS_SHIFT_NAME/_START/_END`), outside its hours the panel says "No shift in progress" and when it next starts, instead of showing the shift that ended.
- **The incident ID is off the alarm bar.** The bar shows the equipment only. The alarm tag, incident ID, first alarm time and status are on the incident detail page (View Details).
- **Readings show their deviation.** Each reading shows how far it is past the alarm limit or outside the normal range, for example "+0.25 past limit · 7%" (full text in the pop-up: "0.25 m above the high-high alarm (3.6 m), 7% past it"). In range shows "in range".
- **Confidence shows an evidence score.** "High confidence · 90%". Tapping it shows a meter and the 7 checks with their points (cause backed by the record 30, one leading cause 10, SOP found 10, SOP covers the alarm 15, SOP approved 10, fresh valid data 15, seen before 10). High is 75 or more, Medium 45 or more. Stale data, or no cause in the record, makes it Low. It is not a probability that the cause is right.
- **Shift notes** list only the notes that were filled in, as label and text. Empty ones are summed up in one line ("Nothing to report: …"). The repeated "open alarms" lines are gone; open items are listed by name, equipment and status.
- **Before and after the fix** is two cards, "During the incident" and "After the fix", with min, max, mean and last. Until the alarm clears, the second card says how to get it (Simulate the fix).
- **A past month of history (simulated).** On first start the backend adds about 23 past incidents (3 per scenario, 2 for UV) over the last 30 days, run through the real pipeline, acknowledged, noted, fixed and resolved by A/B/C-shift operators, plus 30 days of background readings. All of it is labelled "simulated history".
  - Related Incidents shows them with date, time to fix, what was found and the fix, plus "same alarm, last 30 days", "last 24 h" and average time to fix.
  - The alarm summary lists them with a "simulated history" tag.
  - Impact metrics leave them out.
  - The live stream never replays them as new readings (a first build did, and made duplicate open incidents).
  - Scenario Lab has "Add one month" and "Remove history". `OPSLENS_SEED_HISTORY=false` turns it off.
  - **Retention:** OpsLens never deletes records on its own. Incidents, alarms and readings stay until the database is reset or the seeded history is removed.
- **Trend chart** opens on the last 60 minutes, not every stored record. Range adds Last 7 days and Last 30 days, and over a day the axis shows dates ("Oct 26, 12:53"). Before, a month of history on a time-only axis looked like 12:31 and 00:31 mixed up.
- **Bigger dropdowns** (44 px tall, 15 px text).
- **New OpsLens logo**: wordmark, tab icon and the Engineering Console header.
- **A rejected AI answer says which model wrote it** ("Written by LM Studio (qwen2.5-coder-3b)"), why Groq was not used, and whether it hit the output limit.
- Fixes: a slow incident load no longer pulls the operator back to the incident page after they tapped Home. Clock times are formatted by hand (00–23 hours).
- Tests: `tests/test_seed_history.py`, `tests/test_confidence_score.py`, shift rotation tests in `tests/test_operational_history.py`, `src/lib/deviation.test.js`, `src/lib/format.test.js`.

## v2.2.0 fix — 2026-11-09 — "AI · key missing" with keys in .env

- **An empty Windows variable no longer hides `.env`.** Settings already set in Windows or the PowerShell session win over `.env`, but an *empty* one, for example a blank `GROQ_API_KEY`, used to hide the key in the file. The status then showed "AI · key missing" even with keys in `.env`. Empty values now don't count, so the file fills them.
- **The backend window says where its settings came from.** On start it prints, for example, "Settings: …\edge_intelligence\.env (found, 31 values loaded). AI provider: groq. Groq keys found: 2." It prints only the number of keys, never the keys themselves.

## v2.2.0 update — 2026-11-09 — SOP search: Qdrant first, then BM25

- **Search order for an incident's SOP:**
  1. **Qdrant vector search** (all-MiniLM-L6-v2, offline).
  2. **BM25 keyword search** over the same SOP sections, when Qdrant or the embedding model is not available.
  3. The **SOP linked to the alarm tag**, when search finds nothing.

  The search result is checked against that alarm-tag link (`plant_sop_index.py`). When they agree, the SOP is shown as found by the search. When they differ, the linked SOP is shown and the difference is reported, so a look-alike procedure is never shown. The procedure text is always the exact pages of the plant manual.
- **The plant's SOPs are indexed in Qdrant on start.** When the backend starts and Qdrant is running, each SOP section of the plant manual (SOP-INL, LFT, BIO, GAS, SLD, GEN, LAB) and the SOP-AER-001 draft are embedded and stored in the background. Stable point IDs make repeating it safe. The backend window prints the result, for example "Plant SOPs in Qdrant: 45 chunks from 13 plant SOPs indexed; SOP search tries Qdrant first." Set `OPSLENS_INDEX_PLANT_SOPS=false` to turn this off.
- **The early prototype's Pump-02 SOPs are left out.** `ingest_sops.py` now loads the plant's SOPs. The old SOP-P02-… JSON SOPs are loaded only with `python ingest_sops.py --include-legacy`, and the search ignores any of their points already in Qdrant.
- **You can see how the SOP was found:**
  - The SUGGESTED SOP box shows "Full match · Qdrant", "· BM25" or "· tag link".
  - The SOP pop-up has a "How this SOP was found" box with the search order and scores.
  - `GET /api/sop-search/status` shows whether Qdrant and the embedding model are available and the indexing result.
- Tests: `tests/test_plant_sop_index.py` covers:
  - Qdrant first, with old Pump-02 points ignored.
  - A chunk of the uploaded manual counting for its section.
  - A different first hit, reported while the linked SOP stays.
  - BM25 when Qdrant is down.
  - Ingest leaving out the old SOPs.
  - All 8 scenarios, each found by search and agreeing with its link.

## v2.2.0 update — 2026-11-09 — Metrics baseline and a resolve deadlock

- **Metrics: assumed baseline.** The Comparison baseline card has a "Baseline (min)" box with Set and Clear. With a baseline and at least one timed resolution, **Change vs baseline** shows, for example, "89.6 % · Faster than the assumed 45 min", or "Slower than…" in amber.
  - The baseline is always labelled "Assumed, entered here. Not measured." It is kept in this browser only.
  - A default can be set in `edge_intelligence\.env` as `OPSLENS_BASELINE_MTTR_MIN=45`. A value typed on the page wins over it.
  - The calculation note on the page says the baseline is assumed and must be replaced by a validated figure before anyone quotes it.
- **Fixed: an incident fixed before it was acknowledged could never be resolved.** Resolving needs every alarm acknowledged, but the database refused to acknowledge an alarm that had already returned to normal, and the Acknowledge button failed with a server error. Now, following ISA-18.2, an alarm that returned to normal unacknowledged can be acknowledged: OpsLens records who and when, and the alarm stays cleared. A resolved alarm still cannot be acknowledged, and that case now returns a clear 409 message instead of a 500 error.

## v2.2.0 update — 2026-11-09 — One cause story on every screen

**Why the panels disagreed.** Each screen picked its own "likely cause". The AI could put any candidate first, and the Root-Cause panel and the knowledge graph showed every candidate as "also possible". In the lift-station scenario, the AI led with high inflow, the Root-Cause panel with the P-101 trip, and the graph listed three causes. In fact all three were true at once: P-101 tripped while P-102 clogged during rain. Side effects such as a filling gas holder were also shown as competing causes.

- **One leading hypothesis, shared by every screen.** `cause_candidates.py` now gives each candidate a role:
  - **Leading:** the strongest evidence.
  - **Contributing:** may act together with the leading cause.
  - **Chain link:** a step between the leading cause and the alarm.
  - **Likely effect:** a consequence, not a cause.

  Roles use the plant's configured "may influence" links and recorded times, never the scenario's scripted answer. They produce one sentence, for example "P-101 thermal overload trip, together with clog on P-102 and inflow to wet well high." Home, Root-Cause Evidence, the knowledge graph, the AI prompt and the fallback text all use it.
- **The AI is checked against it.** The prompt gives the model the leading hypothesis and every role (prompt version 7). When the AI's "most likely" line names the same leading cause, Home shows the AI's words, marked "AI, agrees with the record". Otherwise Home shows the record's sentence with the note "The AI named a different cause first; its view is in the full assessment."
- **Stable across runs.** Readings that change in the same second get a fixed order: upstream first, then readings past an SOP limit, then by name. All 8 scenarios give the same leading hypothesis at fast and slow simulation speeds.
- **Fixed: wrong trip time.** When a trip was not in the event story, the trip time came from stored telemetry and could pick an earlier trip of the same equipment. It now uses the latest turn-on before this incident's alarm. All cause times are now timezone-aware.
- **Fixed: picking an incident could show the previous one.** A background refresh that finished after you picked an incident could put the old incident back on Home. It now checks that the incident is still the selected one.
- **Home: "Diagnosis" is now "Assessment".** The box under it is labelled "LEADING HYPOTHESIS", with "· AI, agrees with the record" or "· from the record" after it.
  - A plain **What happened** sentence is built from the recorded timeline, plus trips found in the status readings. For example: "CHP engine trip came on first (11:35:44). Then the CHP gas consumption low alarm came on."
  - The alarm title line was removed, because the notification bar already shows it.
- **Root-Cause Evidence** shows one line per cause with its role tag: LEADING, CONTRIBUTING, CHAIN LINK or LIKELY EFFECT. "More ›" opens the knowledge graph.
- **Knowledge graph, incident view:**
  - Only readings that moved are shown, with the rest behind "Show readings that stayed normal (N)".
  - Five columns: readings, alarms, causes, SOP fix, SOP and recovery check. Plant, area and equipment become a breadcrumb.
  - Names wrap instead of being cut off.
  - Causes are ranked with their role and how many readings support them. Likely effects are dashed readings, not causes.
  - Rows are ordered by the cause they lead to, so lines rarely cross.
  - Pointing at a node lights up its chain and fades the rest.
  - The "Whole plant" view is unchanged.
- Next Best Action keeps room for its page buttons, so a step is never half cut off.

## v2.2.0 fix — 2026-11-08 — LM Studio port

- `start_lmstudio.bat` now starts LM Studio with `--port` taken from `EDGE_LLM_URL` (1234 by default). Before, `lms server start` used the port saved in LM Studio's own settings (for example 1235), so OpsLens could not find it.
- The script now uses Windows line endings, which `cmd` needs for its labels and jumps to work reliably.

## v2.2.0 update — 2026-11-08 — Home panels without scrolling

Colours are unchanged; only the panel layout and behaviour changed.

- **No panel scrolls.** Each panel shows what fits. Anything else sits behind a "+N more ›" button at the bottom right ("+3 alarms", "+2 readings"), and the pop-up shows everything. A sentence cut with "…" opens in full when tapped, so nothing is hidden without a sign.
- **Three columns with their own heights:** Diagnosis over Root-Cause Evidence on the left; Next Best Action at full height in the middle; Related Alarms over Important Readings on the right. With Ask OpsLens open, the right column gives way to the chat.
- **Next Best Action in pages:** steps that do not fit move to the next page ("Steps 1–3 of 4" with large ‹ › buttons). "SOP step N · p.P ›" on each step opens the exact SOP text.
- **Important Readings as bar indicators:** grey normal band, red alarm-limit marks, ▼ for the value now and a 30-minute trend line, with the value and unit on the right. "Trends ›" opens Time-Series & Prediction.
- **Root-Cause Evidence:** a compact evidence graph, then the likely cause and alternatives; "More ›" opens Root Cause & Analysis.
- **Messages appear as toasts** above the bottom bar: confirmations close after 6 s, problems after 12 s.
- Panel headers have one-line buttons: "Full diagnosis ›", "All alarms ›", "Trends ›", "More ›".

## v2.2.0 update — 2026-11-08 — Groq first, then LM Studio, then the record

- AI diagnosis order: **Groq → LM Studio → record-based answer**. LM Studio now also takes over when Groq is out of quota (429) or its key is rejected, not only when Groq is offline or too slow. Switch the backup off with `OPSLENS_DIAG_LOCAL_BACKUP=false`.
- When both fail, the message names both ("Groq unavailable: … Backup: LM Studio unavailable: …"), and Groq's problem is the one the fix hint addresses.
- `.env.example` now lists Groq first, the LM Studio backup second and the record-based answer last. Ask OpsLens is unchanged: Groq, otherwise the record-based answer.

## v2.2.0 update — 2026-11-08 — AI diagnosis and plan on Home, scenario 8 without an SOP

### Diagnosis panel
- Shows the AI's own diagnosis first: **Likely cause** (marked as a hypothesis), **Why** (the evidence it used) and **Still unknown**. The AI-GENERATED tag now sits on text the AI wrote.
- **From the record** follows: the headline, the readings past their limit as tiles with the normal range, and the event story in words ("WAS rate fell to 70 m³/d", alarms tagged ALARM, the first change marked "started here").

### Next Best Action
- The AI writes an ordered plan in its own words; each step names the SOP step it comes from (`SOP step N (p.P)`). Tapping the reference shows the exact SOP text. The record check still removes any step whose SOP reference or numbers are not in the record.
- Each step is tagged CHECK, ADJUST, CALL or VERIFY. Warnings in the SOP step ("Do not…", "Never…", "only after…") are shown word for word in a yellow box. Numbers and limits are bold.
- Steps not from the SOP are tagged **AI SUGGESTION · not in the SOP**. Without an SOP: earlier fixes first (EARLIER FIX), then AI suggestions, and "Escalate to the shift supervisor".
- If the AI cites no SOP step, the SOP's own steps are shown word for word. The repeated "Then confirm the fix" block is gone.
- Tap a step number to tick it off. Ticks stay on the screen only; they are not recorded or sent anywhere.
- Backend: `result.plan` in the diagnosis (`ai_diagnosis.build_plan`). Prompt version 6 asks for short plan lines; cached diagnoses are regenerated.

### Scenario 8: UV disinfection intensity low (no SOP)
- New simulated incident on UVChannel-01: lamp bank 2 ballast fault, UV intensity and UV dose fall, flow and transmittance unchanged. Alarms: `ALM-UV-901-INT-LO` (primary), `ALM-UV-901-DOSE-LO`, `UV-901-B2-FAULT`.
- The supplied manual has no UV disinfection procedure, so OpsLens answers "no SOP" (`SOP-NONE`) and never matches a loosely related SOP. A test checks the manual text really has no UV procedure.
- Its limits (5 mW/cm², 25 mJ/cm²) are demonstration values for the simulation, not from any document.
- The seven existing scenarios, their SOPs and the PDF are unchanged. The old `filters_uv_disinfection_failure` scenario stays removed.

## v2.2.0 update — 2026-11-08 — LM Studio replaces Ollama

- **Local AI now runs in LM Studio (or LM Studio Bionic)** through its Local Model API (OpenAI chat format, `http://127.0.0.1:1234/v1`). Ollama is no longer used anywhere.
- `OPSLENS_LLM_PROVIDER=lmstudio`; `EDGE_LLM_MODEL` is the name LM Studio lists (default `qwen2.5-7b-instruct`). Names such as `lmstudio-community/Qwen2.5-7B-Instruct-GGUF` match the same model; the *Coder* models do not.
- The health check (`GET /api/copilot/ai-status`, replaces `/api/copilot/ollama`) reports whether the server is up, the model is there and loaded, and its loaded context. Below 8,192 tokens it warns, because the diagnosis prompt would be cut off.
- The AI diagnosis streams from LM Studio; the local model is still the backup when Groq cannot be reached. Groq stays limited to the AI diagnosis and Ask OpsLens.
- `start_lmstudio.bat` replaces `start_ollama.bat`: it checks the Local Model API, starts it with `lms server start` when `lms` is found, loads the model with an 8,192-token context and checks the model name. `start_all.bat`, `start_backend.bat` and `setup_windows.bat` check port 1234 instead of 11434.
- Old settings keep working: `OPSLENS_LLM_PROVIDER=ollama` is read as `lmstudio`, and an `EDGE_LLM_URL` on port 11434 is replaced by LM Studio's port, with a warning.
- New settings: `OPSLENS_LOCAL_TIMEOUT`, `OPSLENS_LOCAL_CONNECT_TIMEOUT`, `OPSLENS_LOCAL_WARMUP`, `OPSLENS_DIAG_MAX_TOKENS` (default 1,500), `OPSLENS_LMS_EXE`. The `OPSLENS_OLLAMA_*` settings are gone.
- `tools/verify_ai.py` and `tools/benchmark_ai.py` test LM Studio. Tests use a stand-in LM Studio server (`tests/fake_lmstudio.py`); `test_ollama_transport.py` became `test_lmstudio_transport.py`.
- The folders `ops-lens/` and `opslens-generator/` (earlier iterations, not used by the scripts) were not changed.

## v2.2.0 update — 2026-11-08 — Fairer record check, readable fallback, simpler Home

### AI answers are no longer thrown away for worked-out values
- The record check now accepts values an engineer works out from the record: the difference from a limit or normal range, a percentage or ratio between two recorded values, rounded values ("about 2,100 m³/h" for 2,150), clock times that appear in the record and the time between two recorded events. A worked-out value must come from the readings the sentence names, so a made-up reading still fails.
- Removed lines no longer sink the whole answer. A diagnosis is rejected only when fewer than 6 checked lines survive or more than 75 % are removed (was: more than 50 %). Ask OpsLens: fewer than 2 lines or more than 75 %.
- When lines are removed, the answer says so: "N statements removed because they could not be checked against the record."
- Every removed line is written to the backend window with its reason, for example `ai_diagnosis event=… removed: 'Flow 812.3 m³/h' [number not in the incident record: 812.3]`.

### Ask OpsLens fallback
- When the AI answer is not used, the operator is told why (AI busy, too slow, unreachable, key rejected, or answer could not be checked) instead of "not configured correctly".
- The record-based answer is now short and readable: Bottom line, Confirmed in the record (local times), Possible causes (hypotheses, not confirmed), Check first (SOP steps with pages), and what is missing.
- A fallback is no longer cached, so asking again gives the AI another chance.

### Simpler Home screen
- The seven "% sure" numbers are gone. One label remains: **High / Medium / Low confidence** in the likely cause, on the CHECK FIRST block. Tapping it lists the reasons and flags in words.
- The suggested SOP shows **Full / Partial / Weak match** instead of a percentage.
- Five panels: Diagnosis (two-sentence lead, up to three points, a short event story), Root-Cause Evidence (graph, likely cause, other possible causes), Next Best Action (full height), Related Alarms, Important Readings.
- "How to Verify Resolution?" is removed from Home; the same checks are in Verify Status and the Confirm Resolution pop-up.

## v2.2.0 update — 2026-11-07 — Home screen for a 1280 × 800 HMI panel

### Layout
- **Logo:** the OpsLens wordmark supplied by the team (`hmi_ui/src/assets/opslens-wordmark.png`) in both top bars; its aperture is the browser tab icon (`hmi_ui/public/opslens-icon.png`).
- **Top bar (Home):** OpsLens logo (opens the Scenarios sidebar), alarm counts by priority under it, a large **Active Alarms** button (opens the Alarms tab) with date and time, then the five tabs: Alarms, Incidents, Root Cause & Analysis, Time-Series & Prediction, Shifts History. A small SIM / LIVE PLC tag, the AI status and the operator icon sit on the right.
- **Top bar (other tabs):** logo, a Home icon and the tabs only.
- **Notification bar (Home):** severity tile with the incident score, incident title, equipment, alarm tag and ID, alarm count and state, incident switcher (‹ 1/3 ›) when several are open, **Acknowledge** (only while something is unacknowledged), **View Details** (Event Story and explanation in the Incidents tab), **CHECK** component, **Suggested SOP** (tap to view), **Related Incidents**.
- **Home panels:** Diagnosis (incident and a simplified event story), Root-Cause Evidence (how it was ranked plus a small evidence → cause → SOP step graph), Next Best Action (says where the steps came from: the suggested SOP, past events, or AI reasoning when neither exists), How to Verify Resolution?, Related Alarms (with View all active alarms), Important Readings (value, arrow, normal range, deviation).
- **Bottom bar on every screen:** Verify Status, SOPs, Ask Chatbot, Confirm Resolution, Previous Shift Details.
- **Verify Status** reloads the lifecycle, the incident, the readings and the scores, then says either "Readings are back to normal…" or "Not fixed yet: …" with the failing checks.
- **Confirm Resolution** pop-up: recovery checks, then Resolved / Not resolved / Note only, and a required note. Resolved stays disabled until every recovery check passes; the backend refuses it too.
- **Alarms tab:** active alarms on top (Acknowledge and Open per row), the alarm summary of all stored alarms below with search.
- **Incidents tab:** active incidents on top, every logged incident below; a row opens its explanation, Event Story and Incident Analysis.
- **Root Cause & Analysis:** Evidence and root cause, Knowledge graph, Incident analysis.
- **Time-Series & Prediction:** forecasting and anomaly detection switched on by default.
- **Shifts History:** the last handover on top with an in-depth view (or the current shift so far if none was submitted), past shifts below, and Prepare this shift's handover.
- **SOPs screen:** SOPs grouped by process area with ID, title and approval state; the selected SOP's pages on the right. The suggested SOP is marked.
- **Scenarios sidebar (logo):** start any of the seven simulated incidents, stop, and Simulate the fix. Labelled as simulated; it never touches a PLC.
- **Chatbot** docks on the right between the bars; the panels move aside instead of being covered.

### Scores
- Every "NN% sure" is a rule-based evidence score from `GET /api/events/{id}/insight` (`scores`): incident, component, SOP, root cause, diagnosis, next action, verification. Hover or focus shows what it is based on and "Rule-based evidence score, not a probability".

### Design
- Colours: green for actions and "good", red for alarms and unacknowledged, yellow for "medium / check", black top bar, dark-grey bottom bar, white panels. White text on the button green passes 4.5:1.
- Tap targets are at least 44 px; bar buttons are 52–60 px with 16–17 px bold text. Panel titles are 17 px, body text 15–16 px.
- The page never scrolls at 1280 × 800; each panel scrolls on its own.
- The SOP viewer closes with Escape and has a 44 px close button.

### Groq scope
- Groq is used only for the AI diagnosis and Ask OpsLens. The shift summary, the guidance text and the old assistant path never call it.

### Removed
- The old console's alarm list, Alarm Overview, alarm pop-up, Alarm History and Bottom line components (replaced by the Home screen and the Alarms tab). `e2e/hmi_1366.e2e.cjs` is replaced by `e2e/home_1280.e2e.cjs`.

## v2.2.0 update — 2026-11-05 — Operator Console, SOP reaches the AI, dependency checks

### Console layout, knowledge graph and time series
- Sections grouped as Respond (Alarm Overview), Understand (Incident Analysis, Event Story, Evidence and Root Cause), Investigate (Time Series and Forecasting, Knowledge Graph) and Records (Alarm History, Shift Handover). Eight tabs instead of ten.
- Forecasting and anomaly detection are a switch inside Time Series. A prediction that did worse than "no change" on its own history says so.
- The AI Diagnosis tab is removed. The full diagnosis is on Alarm Overview, below the Bottom line, in its own scrollable box.
- Top bar: no urgent count. The operator name is behind a person icon (actions are still recorded with it). The data source is a small SIM / LIVE PLC tag. A green Ask OpsLens button opens and closes the assistant.
- Knowledge graph built from the incident record: every possible cause, the best SOP step for each, the recovery check, and sensor nodes coloured by state (past limit, outside normal, no data) with worst and current values.
- The AI and Ask OpsLens get the graph's relationships (sensors on the equipment, which reading may influence which, which sensor raises which alarm, which SOP covers it). The old hard-coded Pump-02 prototype graph is removed.
- Fixed: long chart ranges showed the oldest 20,000 readings instead of the newest.

### Mentor checklist (see docs/MENTOR_CHECKLIST.md)
- Bottom line card (BLUF): likely cause, top 2 SOP steps for it, most relevant earlier incident. Built from the record, shown before the AI.
- Confidence rating High / Medium / Low with reasons and flags (unprecedented, several causes, ambiguous, draft SOP, data quality, escalate). Not a probability. `GET /api/events/{id}/insight`.
- History of the same alarm or equipment (when, minutes to resolve, resolution notes) given to the AI and to Ask OpsLens.
- Operator actions and alarms on the same equipment in the 30 minutes before an alarm are correlated and given to the AI as a possible cause.
- Ask OpsLens streams each line after it passes the record check, and answers in Bottom line / Top 2 steps / History form. It docks beside the console instead of covering it.
- Latest-reading query uses the index: context and AI facts stay under 0.6 s on 3.9 million readings (was up to 2.4 s). `tools/measure_latency.py`.
- `/api/metrics/impact` adds AI time to first word and total time.

### Ask OpsLens answered by the AI
- With `OPSLENS_LLM_PROVIDER=groq`, Ask OpsLens sends the question with the same facts as the diagnosis: readings, alarms, event order, possible causes from the record, SOP steps and recovery criteria. The answer is labelled AI-generated.
- Each sentence goes through the diagnosis record check. Sentences with numbers, tags or SOP steps not in the record are removed and listed in `provider_metadata.ai_analysis.removed_lines`. If more than half fails, or Groq is unavailable, the older evidence-based answer is shown.
- Turn off with `OPSLENS_ASK_ANALYSIS=false`.

### Several Groq keys (for testing)
- `GROQ_API_KEYS=key1,key2` tries the next key when one is rate-limited (429) or rejected (401/403). A rate-limited key waits out its `retry-after`; a rejected key is set aside until restart. Keys are named "key 1", "key 2" in logs and status, never by value.
- Ask OpsLens uses Groq too when `OPSLENS_LLM_PROVIDER=groq`: the same keys, `OPSLENS_GROQ_MODEL` (it no longer picks up the local `EDGE_LLM_MODEL`, which Groq rejected), and low reasoning for quick replies.
- The shift handover AI summary can use Groq, labelled "AI-generated (Groq cloud)". It was local-only before. `OPSLENS_HANDOVER_LOCAL_ONLY=true` restores that.
- `tools/check_groq_keys.py` checks each real key once. `tools/mock_groq_keys.py` shows the rotation against a local mock of Groq, without real keys.
- Keys in one Groq account share one limit. Groq's Acceptable Use Policy restricts using several accounts to get more usage.

### Better AI answers
- Possible causes are built from the record and given to the model: recorded trips, SOP limits that say "Suspect …" (for example "Suspect clog"), a standby unit that never started, and readings that changed before the alarm. They are hypotheses, ordered by kind of evidence, not probabilities. The Evidence and Root Cause tab shows the same list.
- If the model leaves out a recorded trip or an SOP "suspect" cause, OpsLens adds it to Possible causes, labelled "Added by OpsLens from the record; the AI left it out".
- New section "Resolution and recovery check": the SOP steps that fix each likely cause and the SOP's recovery readings.
- Lines removed by the record check are sent back once to Groq to be corrected, then checked again. Only lines that pass are restored. Removed lines are written to the backend log.
- The operator's SOP checks always include the first two SOP steps (immediate response) plus the steps the model cited, up to 7.
- gpt-oss reasons at medium effort by default (`OPSLENS_GROQ_REASONING`).
- `tools/evaluate_diagnosis.py` scores real answers for all seven scenarios on your laptop.

### Groq diagnosis
- `OPSLENS_LLM_PROVIDER=groq` runs the diagnosis on Groq, default `openai/gpt-oss-120b`. The label shows when an answer came from the cloud.
- A rate limit waits once, then gives the labelled fallback. No internet tries the local model.
- A model that stops after writing the SOP checks keeps its answer, marked as ended early.
- Shorter prompt text. The timeout hint names the right setting. Reference-document fragments no longer appear as advisory checks.

### SOP coverage bug
- The AI could say "No SOP step covers this event" while the viewer showed the SOP. The prompt allowed that phrase and shared alarm tags failed the match. See `docs/AUDIT_v2.1.md` for the v2.1 background.
- `sop_context.py` passes real steps, passages, pages, revision and approval from the viewer's own document. The phrase is removed and answers give coverage, gaps and SOP checks separately.
- Drafts show as DRAFT — NOT APPROVED. With no match the answer says no directly matching approved SOP was found.

### Unified Operator Console
- Operator and Engineer modes are gone. There is one console with ten sections and one selected incident.
- SOP Library and System Health left the main navigation. Their backend, documents and viewer remain, and the Engineering console still has Procedures.
- Lifecycle added: active unacknowledged, active acknowledged, condition recovered, resolved. Resolving is enforced by the backend.
- New: `/events/{id}/lifecycle`, `/timeline`, `/evidence-detail`, `POST /incidents/{id}/acknowledge`.
- Operator name is entered in the top bar. The hard-coded name is removed.

### Dependencies
- `start_all.bat` installs Python or npm packages only when their requirement files change. See `TROUBLESHOOTING.md`.

## v2.2.0 — 2026-11-05 — Working AI pipeline, Operator and Engineer modes, Scenario 7, analytics

Full findings: `docs/AUDIT_v2.1.md`. Existing data is preserved; one table (`ai_diagnoses`) is added.

### Why AI answers did not appear
| # | Cause | Fix |
|---|---|---|
| 1 | Nothing started Ollama or set `OLLAMA_MODELS` | `start_ollama.bat`, called by `start_all.bat` |
| 2 | Every handover refresh called the model on the server's event loop with a 4 s limit. One scripted run of seven scenarios caused 2,815 of these, blocking the backend and competing with the diagnosis | Polled handover is record-based. Model summary only on request, off the event loop |
| 3 | The 60 s limit included model load and prompt reading | Separate first-token limit (240 s) and total limit (480 s) |
| 4 | No single-flight; each page view or StrictMode remount queued more work | One background job per incident context. Readers attach and can leave. Newer context supersedes older jobs |
| 5 | Cache lived in memory and changed with any new reading | Stored in SQLite, keyed on facts that define the answer |
| 6 | One bad tag or three derived numbers rejected the whole answer | Line-level validation with reported removals. Abbreviated tags and rounded numbers are accepted, and `2,150` is read as one number |
| 7 | A timeout inside a stream was reported as "Ollama is not running" | Reported as a timeout |
| 8 | Provider errors were swallowed | Logged, and raised to the caller |
| 9 | The model saw latest readings, so a recovered incident looked healthy | Facts hold start, worst and current values |
| 10 | The evidence "cause" was the alarm's own measurement | Symptoms excluded; fault and trip inputs stay eligible |
| 11 | `localhost` tried IPv6 first on Windows, and system proxies applied | Loopback IPv4, with proxies ignored for the local server |

### Plant and alarm fixes
- A new scenario no longer clears earlier incidents. Unfixed incidents survive other runs and backend restarts, and any of them can be fixed (`simulated_plant.py`).
- Recovery readings cannot open a second incident.
- Incidents with live alarms rank above those returned to normal. The HMI follows them unless the operator picked an incident.
- Simulated time stays anchored to the real clock across back-to-back runs.
- Shift windows compare instants, not text. A +05:30 window previously matched no UTC records ("Recorded this shift: 0 events").
- Selecting a deleted incident no longer loops on HTTP 404. Live events are coalesced to one fetch per second.
- Plant view shows values outside normal instead of NORMAL.

### New
- Operator Mode (default) and Engineer Mode.
- Scenario 7, *Low dissolved oxygen due to aeration blower failure*, with draft **SOP-AER-001** (DRAFT FOR REVIEW — NOT APPROVED). Its limits are demonstration values in `config/alarm_reference_drafts.json`, kept apart from the page-cited manual values.
- Four source labels; no-matching-SOP answer; uploaded documents reach the model when they share specific words with the incident.
- Time series, anomaly markers, data gaps, before/after comparison, forecast with backtest, predictive warnings (`analytics.py`).
- Knowledge graph generated from the catalog and incident records.
- Shift handover per-incident records, grounded in stored data.
- `tools/verify_ai.py` and `tools/evaluate_analytics.py`.

## v2.1.0 — 2026-11-04 — Six incident-map scenarios, Qwen diagnosis, 1366×768 HMI

### Scenario Lab: only the six incidents from the plant incident map
- New single source of truth `edge_intelligence/incident_catalog.py`: the six incidents with the exact normal / limit / during-incident / after-fix values from *"Wastewater plant: six incidents on one map"*. Tags, equipment owner, units, alarm inputs, trips and SOP mapping are all defined there; the tag registry, alarm reference, simulator, trend endpoint and frontend tag map are derived from it (`tools/gen_frontend_tags.py`, checked by a test).
- Removed the previous incidents (Pump-02 valve restriction, filters/UV disinfection, low DO/ammonia, filter sensor fault, monitoring due). Legacy Pump-02/Compressor/Tank scenarios stay in code for old records only; `/api/scenarios/run` refuses them unless `OPSLENS_ENABLE_LEGACY_SCENARIOS=true`.
- SOP mapping (SOP-MAN-000 Rev 0, **Draft for review — NOT APPROVED**): 1 SOP-INL-001 · 2 SOP-LFT-001 · 3 SOP-BIO-001 · 4 SOP-BIO-002 · 5 SOP-GAS-001 · 6 SOP-SLD-001.
- Coverage inspected against the SOP text. Five are full coverage with named gaps; **SOP-SLD-001 is partial**: it treats centrate as an ammonia *load* only and has no inhibitory/toxic-centrate, nitrification-inhibition or toxicity-test content (the words "toxic", "inhibit", "nitrification" do not appear; a test checks this against the SOP text). Gaps are shown in the console, the HMI SOP panel and the AI facts.
- "After fix" for every scenario (`POST /api/scenarios/corrective-action` ramps to the incident map's after-fix values); `POST /api/scenarios/stop`; `GET /api/scenarios/{id}/details`; run status now has `progress`, `stage` and sample counts.

### AI: Qwen via Ollama is the explanation layer, the Evidence Engine is the factual source
- New `ai_diagnosis.py`: compact incident-scoped facts (this incident's tags only, readings vs SOP limits, event sequence, ranking score labelled *not a probability*, draft SOP steps and coverage gaps) → Ollama `/api/chat` streaming with `keep_alive`, `num_ctx 4096`, `num_predict 650`, `temperature 0.2` → seven sections (summary, confirmed observations, possible causes, evidence for/against, missing information, safe checks, engineering assessment).
- Every output is validated: headings present, tags must belong to this incident, numbers must exist in the facts, cited SOP steps must exist, no claims of operating equipment or of an *approved* SOP. Failing output is replaced by the deterministic diagnosis and the reason is shown.
- Fallback is labelled **"DETERMINISTIC FALLBACK — not AI-generated"** everywhere and is never presented as model output. `/api/copilot/ollama` and `/api/copilot/health` report `last_diagnosis_call` (attempted / succeeded / error / latency); `verified` is set only after a successful model call.
- Speed: one model call per incident instead of a call per question on load; prompt about 3× smaller; streaming to the HMI (time to first token shown); result cache keyed on incident + facts hash + model; warm-up at start-up (existing). `tools/benchmark_ai.py live` measures real latency on the laptop.

### Operator HMI redesigned for a 1366×768 panel
- Fixed-height layout, no page scroll: top status bar (alarm counts, SIMULATED/LIVE source, AI state, handover, clock) · alarm banner (severity, alarm, equipment, state, first time, acknowledgement deadline) · three columns: alarm groups + readings/trends with sparklines and limit lines · Event Story + readings-vs-SOP-limits table · streaming AI diagnosis + SOP panel (draft badge, coverage, gaps, verbatim steps) · operator workflow bar.
- Detail views in the sidebar: Event Story, Evidence Engine, Operator Brief, AI Diagnosis, Plant Overview (monitoring view, not an incident), Incident History, Shift Handover, SOP Library.
- Acknowledge targets the alarm shown in the banner first (then the remaining inputs) and never clears the alarm.

### Bugs found and fixed in this round
| # | Symptom | Root cause | Fix | Test |
|---|---|---|---|---|
| 20 | Console showed *"Scenario could not be started. Try again."* | The console set "not running" whenever any open incident existed, so **Start** was re-enabled mid-run; the second POST got 409 and the generic message hid it | Run state comes only from `/api/scenarios/status`; Start becomes **Stop** while running; backend reason shown | e2e `hmi_1366.e2e.cjs` |
| 21 | Console ran a different scenario than the one selected | A late `/api/scenarios` response reset the selection to the first item | Keep a valid selection | e2e |
| 22 | Run status reported an older incident as this run's result | Status fell back to "latest open incident" | Only incidents created (or repeated) by this run | `test_six_incidents` |
| 23 | Back-to-back runs merged incidents: RAS-P1 alarms were added to the open foam incident | 90 s clustering window + shared downstream tags (AER-201-MLSS, EFF-301-TSS) | Run boundary seals clusters of other scenarios (`dedup.seal_clusters`) | `BackToBackRunsTests` (fails without the fix) |
| 24 | A recurrence after the old cluster expired created an incident with **no** primary alarm row and re-activated the old incident's alarm | Alarm fingerprint record and cluster disagreed on the owning incident | A recurrence joining a different incident gets a fresh alarm record | `BackToBackRunsTests` round 2 (fails without the fix) |
| 25 | After a previous fix, the next run's alarm looked already cleared | Simulated fix readings were stamped later than the next back-dated run | Simulated time is monotonic across runs and fixes | `BackToBackRunsTests` |
| 26 | Re-running a scenario whose alarm was still active silently produced "no incident" | Nothing new can annunciate | 409 with *"… still active in open incident …; run the simulated fix first"*; a repeat into an open incident is reported as `incident_repeated` | `ScenarioStartStopApiTests` |
| 27 | Generic words ("pump", "motor current", "discharge pressure") on a lift-station question could flag it as Pump-02 | Over-broad equipment keywords | Specific terms per equipment, catalog-derived tags | `test_generic_pump_words_on_lift_station_do_not_point_at_pump02` |
| 28 | Diagnosis restarted mid-stream when the brief loaded (two model calls per incident) | Refresh key included a field that arrives later | Key on incident status only | e2e against a test server |

## v2.0.0 — 2026-11-03 — Stabilisation, SOP-manual alarm configuration, operator brief

Baseline: `nagavardhan987/opslens` @ `aa92b09` ("Update OpsLens incident workflows, scenarios, and assistant"). The original folder is preserved; this update edits files in place and adds new ones. Active code: `edge_intelligence/` (backend) and `hmi_ui/` (frontends). `ops-lens/` and `opslens-generator/` were not changed.

### Bugs discovered and fixed (each verified by running the code)

| # | Symptom | Root cause | Fix | Test |
|---|---|---|---|---|
| 1 | Dashboard intermittently showed *"Active alarms temporarily unavailable"* (known issue 1) | One SQLite connection (`check_same_thread=False`) was shared by the event loop and the Ask OpsLens worker thread; concurrent use interleaved transactions. Reproduced on the original code: `InterfaceError: bad parameter or other API misuse`, corrupted reads (`tag=None`), and `cannot commit - no transaction is active` | Per-thread SQLite connections (`database.py`), WAL unchanged | `DatabaseThreadSafetyTests` |
| 2 | `P101-RUN` (pump running) recorded as an ACTIVE alarm; every analog excursion (level, flow, current, pressure) also stored as an "alarm" — 12 alarm rows for one wet-well incident (known issue 8) | `dedup.py` saved every breaching tag as an individual alarm; `prioritizer.py` treated **any** boolean TRUE as a trip alarm, so a *running* pump was a breach and a *stopped* pump was not | New `alarm_config.classify_tag`: only `ALM-*`, `*-FAULT`, `*-TRIP` are alarms. Status tags are evidence only when they leave their configured normal state. Wet-well incident now has exactly `ALM-WW-101-HH` + `P101-FAULT` | `TagClassificationTests`, `AlarmRepeatAndLifecycleTests`, pipeline test |
| 3 | Repeat count ×52 after one scenario, ×416 in the field | Every 2 s sample of an alarm that stayed TRUE incremented `repeat_count` and wrote an occurrence row | Repeat = re-activation after return-to-normal, or explicit re-annunciation (`alarm_state="REANNUNCIATED"`, used by *Trigger same alarm again*). Polled sources (simulator/OPC UA) no longer inflate. Out-of-order FALSE samples are ignored | repeat tests; all 11 scenarios now show repeat 1 |
| 4 | Data source showed **DISCONNECTED** during simulation (known issue 2) | HMI fell back to `DISCONNECTED` when no event-specific source was known | New `GET /api/datasource` (SIMULATED / LIVE PLC / STALE / PLC DISCONNECTED / NO DATA); HMI uses it | `DataSourceLabelTests` |
| 5 | SOP-MAN-000 match hidden: *"All wastewater process areas does not match LiftStation-01"* (known issue 7) | Equipment check compared strings literally; plant-wide check covered only 4 area prefixes | Plant-wide manuals apply to all wastewater areas (RAS, clarifier, biogas, centrifuge, monitoring, filters added) | `PlantWideSopVisibilityTests` |
| 6 | Ollama inference timed out after ~5–6 s (known issue 5) | Global 4 s timeout for every provider; on timeout `call_local_edge_slm` retried `/v1/chat/completions` with the same timeout (≈2×); guidance used a hard 3 s timeout; no keep-alive | Provider-aware timeout (`OPSLENS_OLLAMA_TIMEOUT`, default 60 s; cloud 8 s), 2 s connect timeout, single attempt, `keep_alive=30m`, background warm-up at start-up, `/api/copilot/ollama` health | `tests/test_ollama_transport.py` (fake Ollama server) |
| 7 | Guidance "Next best actions" were raw page text (Purpose/Scope/PPE table) and the explanation said *"Supporting deviations are unavailable"* although 4 deviations existed; cited SOP-MAN-000 while the incident matched SOP-LFT-001 | (a) `is_sop_available` rejected `bundled_pdf_section` matches; (b) guidance cached per event on the first, smaller cluster and never refreshed | Verbatim numbered procedure steps with page numbers (`sop_steps.py`); cache keyed on contributor/deltas/SOP; stale DB cache regenerated | `test_guidance_uses_verbatim_lift_station_steps`, `SopStepTests` |
| 8 | BM25 fallback crashed with `ValidationError: SopMatch.revision` | JSON SOP chunks carry `revision=None` | Coerce to string | `tests/test_retrieval_engine.py` (previously failing) |
| 9 | Event story builder crashed: `UnboundLocalError: primary_desc` | Missing branch for an analog primary signal with a recorded value (masked before because `P101-RUN` was always primary) | Branch added | scenario runs, pipeline test |
| 10 | Orchestrator wrote an older cluster snapshot over a newer one (incident collapsed to 1 tag under burst input) | Worker scheduled with a stale `CandidateEvent` saved it back | Worker reloads the persisted candidate if it is a superset | `OperatorBriefPipelineTests` |
| 11 | Acknowledgement timestamp could be earlier than the alarm | Fast-forwarded scenarios stamped samples up to 80 s in the future | Scenario timeline ends at "now" | `ScenarioApiTests` |
| 12 | Duplicate scenario submission returned a bare 409 string | — (409 itself is correct; known issue 4) | 409 detail now includes `running_scenario_id`, `status`, `elapsed_seconds`; stale "running" state auto-expires after `OPSLENS_SCENARIO_STALE_SECONDS` | `ScenarioApiTests` |
| 13 | `/api/health` always claimed `qdrant_vector_search` | Hard-coded string | Reports the method actually available | manual API check |
| 14 | Qdrant `connection refused` printed full stack traces (known issue 6) | `logger.exception` | One-line warning with the start command | server log check |
| 15 | PDF ingestion failed outright without Qdrant/embeddings | Hard dependency | Stores PDF + metadata for BM25 keyword retrieval; status `stored_keyword_only`; strict mode via `OPSLENS_PDF_BM25_FALLBACK=false` | `KeywordOnlyPdfIngestionTests`, ingestion safety tests |
| 16 | Shift handover card showed **"null shift · null"** | Unconfigured shift rendered literally | Shows *Shift timing not configured*; `.env.example` sets a demo shift | build + screenshot |
| 17 | `SOP-GEN-002` lost steps 8–9 | Page range stopped at page 7 | Pages 6–8 | `SopStepTests` |
| 18 | LLM answer *"FIT-02-FLW fell by 1.8 A"* (units swapped between tags) was accepted | Unit-matched tag overrode the tag the sentence attributed the number to | Reject unit/tag mismatch | `test_compound_open_ended_trend_answer_scopes_values_to_nearest_tag` (previously failing) |
| 19 | Wet-well scenario defaulted the level to 47.5 m before the ramp | Typo-level default | 1.7 m (registry midpoint) | scenario tests |

Stale tests updated to the current, intentional behaviour (no production logic weakened): event-story label prefix, 90-tag registry in the stream test, constant-value tags in the noise test, workflow-route stub, curated SOP-INL-001 mapping, provider-aware timeout test.

### Added

- `edge_intelligence/config/alarm_reference.json` — 31 parameters from **SOP-MAN-000** with page numbers, P1/P2/P3 convention (p.4), SOP-GEN-003 alarm-response rules, verification criteria, restrictions, `NOT SPECIFIED` where the manual is silent, and mapping to OpsLens tags.
- `alarm_config.py` — tag classification, SOP limit check (margins in Python), conditional/unconditional P1, conflict report.
- `operator_brief.py` + `GET /api/events/{id}/brief` — deterministic 8-part brief.
- `sop_steps.py` — verbatim procedure steps with page numbers for all 12 SOPs.
- API: `/api/alarms/reference`, `/api/alarms/conflicts`, `/api/alarms/limit-check`, `/api/alarms/active`, `/api/datasource`, `/api/copilot/ollama`, `POST /api/copilot/ollama/warmup`, `/api/diagnostics`.
- HMI: **Operator Brief** panel, AI status pill, data-source fix. Console: **Alarm Reference** page.
- `tools/opcua_browse.py` for PLC node discovery.
- Windows: `setup_windows.bat`, `start_all.bat`, `start_backend.bat`, `start_frontend.bat`, `run_tests.bat` (`START_DEMO.bat` now calls `start_all.bat`).
- `requirements-core.txt` (no PyTorch), `requirements-dev.txt`; `hmi_ui/package-lock.json` committed.
- Docs: README, ARCHITECTURE, ALARM_CONFIGURATION, PLC_OPCUA_SETUP, TROUBLESHOOTING, TEST_REPORT. Previous README kept in `docs/previous/`.
- Tests: `tests/test_alarm_reference_and_brief.py` (37), `tests/test_ollama_transport.py` (6).

### Changed configuration defaults

| Setting | Before | After |
|---|---|---|
| Local LLM timeout | 4 s (shared) | `OPSLENS_OLLAMA_TIMEOUT` 60 s |
| Cloud LLM timeout | 4 s | `OPSLENS_LLM_TIMEOUT` 8 s |
| Default Ollama model | `qwen2.5:0.5b` | `qwen2.5:3b` |
| Pipeline guidance | LLM attempted when an SOP matched | Deterministic verbatim steps; LLM drafting opt-in (`OPSLENS_GUIDANCE_USE_LLM=true`) |
| Assistant single-flight wait | 30 s | max(30 s, provider timeout + 15 s) |
| PDF ingestion without Qdrant | error | stored for BM25 (`OPSLENS_PDF_BM25_FALLBACK`) |

### Database schema changes and migration

- **No table or column changes.** Existing `opslens.db` files open unchanged.
- Recommended once: stop the backend and delete `edge_intelligence\opslens.db*` (or *Reset demo data*) so old rows created under the previous alarm/repeat logic (e.g. `P101-RUN` alarms, inflated repeat counts) do not appear in history. `/api/alarms/active` already hides legacy status/measurement rows.
- Optional `OPSLENS_DB_PATH` moves the database.

### Incomplete / not implemented (honest list)

- Live PLC connection — adapter implemented and unit-tested with mocks; **not verified against lab hardware** (needs endpoint, Symbol Configuration, mapping, registry entries for the lab motor tags).
- MQTT and REST adapters — classes exist, not started by `server.py`.
- OCR for scanned PDFs — not implemented (rejected with a clear message).
- The manual gives no alarm deadbands, on/off delays or reset conditions; OpsLens therefore implements return-to-normal on the source input only.
- 40 registry-vs-manual conflicts are reported but **not resolved** (needs a site decision); most important: `PIT-101-DIS` direction.
- `ops-lens/` and `opslens-generator/` are earlier iterations; their tests are not maintained.
- Legacy acceptance scripts at `edge_intelligence/test_*.py` (outside `tests/`) were already failing before this update (16 of 31); now 13 of 31. They predate the current scenario catalogue and are superseded by `tests/`.

### Assumptions

- The SOP manual PDF supplied is the same file as the repository's `SOP-MAN-000` (verified byte-identical).
- `PIT-101-DIS` is treated as *assumed* to be the per-pump discharge pressure for the SOP limit check (labelled in the UI).
- Normal states of status tags (`P101-RUN` normally running, flare normally off, …) are OpsLens demo configuration, not from the PDF.
- Simulated scenario data represents the last *N* seconds compressed, ending at the current time.
