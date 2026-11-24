# 10-minute presentation walkthrough + 5 minutes Q&A

**One line:** OpsLens is an AI-assisted, evidence-based incident investigation and decision-support system for wastewater treatment plants. It is read-only: the operator acts through the plant's existing systems.

**Setup (before you start):** run `start_all.bat`; open http://localhost:5173 (HMI, 1280 × 800) and http://localhost:5174 (Engineering Console). Check the AI pill reads *AI · Groq* or *AI · LM Studio*. Console → *Reset demo data*. Main scenario: **2. The lift station pumps clog** — it has two serious, competing conditions (P-101 thermal trip and a clog on P-102). Do not pre-run it; run it live at 1:00.

All data in the demo is **SIMULATED** (top-bar label). Never describe it as live PLC data.

---

### 0:00–1:00 · The problem and the positioning
- "An HMI tells the operator *that* an alarm is active. In an upset several alarms fire within a minute; the operator has to work out what happened first, which condition matters most, what the procedure says, and how to know it is fixed."
- "OpsLens is an AI-assisted, evidence-based incident investigation and decision-support system. Every number comes from Python over the recorded data; the AI only explains those facts and is checked line by line. It never writes to a PLC."

### 1:00–2:00 · Trigger the incident (Engineering Console)
- Scenario Lab → *2. The lift station pumps clog* → Start. Point at the pipeline stages ticking (telemetry → evidence → SOP → published).
- "The scenario only drives the simulated sensors. Nothing from the scenario's description reaches the diagnosis — I'll prove that at 6:00."

### 2:00–4:00 · Alarm priority and the event story (HMI)
- Home: banner *P1 · Wet well level high-high*. Alarms tab: urgency ranking with the reason ("live alarms first, then safety rules, then time to limit…").
- Investigation path bar across the top of the tabs: Alarm priority → Event story → Root-cause evidence → Knowledge graph → Incident analysis & AI → SOP → Trends → Verify recovery. "Each tab is one step of the same investigation." Press *Next* to move along.
- Event story: P-102 flow falls first, P-101 trips on winding temperature, level rises, H then HH.

### 4:00–6:00 · Two competing conditions — which one leads, and why
- Root Cause & Analysis → Evidence and root cause. Two candidates:
  - **Leading — P-101 thermal overload trip, 90 points**: recorded trip (+25), before the alarm (+20), past the 105 °C limit (+15), readings agree, SOP limit, on the path to the wet well.
  - **Contributing — Clog on P-102, 65 points**: flow 90 m³/h, pressure 1.1 bar, current 19 A all past SOP limits — but no recorded trip (−25 vs the leader).
  - Inflow high is a related contributing condition.
- Open *Why it ranks here*: the factor table and the comparison sentence. "Both are real; the trip leads because it has a recorded trip status on top of the same kind of evidence. Points are not probabilities."
- Home → **Evidence Support** (90 % · Strong evidence in our run) → *Why this score?*: each check and its points; the SOP check says *draft, not approved*; history says *no earlier incident recorded*.

### 6:00–8:00 · Proof it is backend-driven, SOP and trends
- Console → **Inspector**: readings (first / worst / now), breaches with first-crossing times, alarm first-on times, event order, candidates with points. "This is the raw analysis the HMI renders. The test reference sits in a closed box for engineers only."
- Mention validation (docs/VALIDATION.md): "Change the telemetry and the answer changes — move the trip after the alarm and the clog leads; remove the clog's supporting readings and it drops to *insufficient evidence*."
- SOP panel: SOP-LFT-001, section/page, **DRAFT — NOT APPROVED**, coverage and the gap (*no inflow limit*).
- Time-Series & Prediction: limit status card — *ACTIVE BREACH* for the wet well (no early warning for a limit already passed); predicted crossings are labelled *statistical linear trend, not machine learning*, and say when the trend is unreliable.

### 8:00–10:00 · Next Best Action, human in the loop, recovery, shifts, close
- Next Best Action: evidence checks first ("Confirm the P-101 trip status at the panel… read SOP-LFT-001 (draft) before any reset"; "Inspect P-102 for clog: flow, pressure, current together"), then SOP steps, then call-outs, then the verify step. "Checks, not control commands."
- Acknowledge: "local to OpsLens; it is not connected to SCADA." Console → *Simulate the fix* → Verify Status → condition recovered → Confirm Resolution → it appears in Shifts History (stored in the backend database).
- Close: "Read-only decision support; evidence you can trace; honest about drafts, gaps and simulated data. The next step is a proposed on-premise deployment — central server, local LLM, several HMI clients — which we have designed but not built or validated."

---

## Q&A preparation (5 minutes)

| Question | Short answer |
|---|---|
| Is the root cause hardcoded per scenario? | No. Candidates come from recorded trips, SOP limit actions and readings that moved before the alarm. The scenario's reference outcome is read only by tests and the Inspector — a test enforces that. Changing telemetry changes the ranking (tests D1/D2). |
| What does the Evidence Support % mean? | Evidence Support: the share of evidence checks met (trip/limit evidence, timing, agreement, SOP approved, history…). Not a probability that the cause is right. |
| Why does the trip beat the clog? | Same kinds of evidence, plus a recorded trip status (+25). If the trip happens after the alarm it cannot lead and the clog leads instead. |
| What if one sensor is wrong? | A lone abnormal meter with every related reading normal is *insufficient evidence* and the action is "check the instrument" (test B). |
| Is the forecast ML? | No — a statistical linear trend with a significance gate and a backtest; it says when it is unreliable. |
| What if the AI is down? | Deterministic fallback, labelled *not AI-generated*; every AI line is validated against the record. |
| Can it control the plant? | No PLC writes, no pump/valve control, no SCADA acknowledgement. |
| How would it deploy? | Proposed: read-only OT link → on-prem server (analytics, SOPs, DB, local LLM) → several HMI clients. Not built or validated. |
| What is not validated? | Everything runs on simulated telemetry; SOPs are drafts; the history factor in candidate ranking is not wired yet; no real-plant accuracy figures. |
