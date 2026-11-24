# Root Cause Candidate Engine: audit and proposed model

*Status: proposal for review. No change to the implementation has been made.*
*Scope: `edge_intelligence/cause_candidates.py` and what reads it (`incident_insight.py`, `incident_views.py`, `knowledge_graph.py`, `ai_diagnosis.py`, the HMI Root Cause page and the Inspector).*

---

## 1. Current algorithm audit

### 1.1 Inputs

| Input | Where from | Used for |
|---|---|---|
| Incident readings (first / worst / now, normal band, SOP limit, limit action, page, `severity_rank`) | `operator_brief` from stored telemetry | evidence rows |
| Event story steps (first sample-to-sample change per tag that passed the story threshold) | `full_event.story.steps` | "first change" time per tag |
| Fault/trip status turn-on times not in the story | `_status_turned_on` from stored telemetry (10 min look-back) | trip timing |
| Primary alarm first-on time | `operator_brief.primary_alarm_first_on` | before/after the alarm |
| Configured "may influence" links | `incident_catalog` signal `upstream` fields | topology, roles |
| Primary alarm's own measurement ("symptom") | `alarm_config` | excluded from causes |

Nothing reads the scenario title, story, injected conditions or `REFERENCE_OUTCOME` (a test enforces this).

### 1.2 Candidate generation (in this order; a tag used by one candidate is not reused)

1. **trip**: any `*FAULT|*TRIP|*FAIL` status that turned on. Evidence = readings of the same equipment code (regex on the label) or one configured link away. Abnormal rows → supporting; normal rows → contradicting.
2. **standby**: a "standby … running" status that never turned on while a trip exists.
3. **sop**: readings past an SOP limit whose action text matches `suspect <cause>`, grouped by (equipment, cause). Other readings of that equipment are added as support or contradiction.
4. **early**: each remaining abnormal reading with a story change, earliest first (ties within 1 s ordered upstream-first). Stops at 4 candidates.
5. **lone symptom** (only if nothing else): the alarm's own reading is abnormal while all related readings stayed normal → "may be the instrument".

### 1.3 Scoring (`assess_candidate`, 0–100 "evidence points")

| Factor | Points | Rule |
|---|---|---|
| direct | 25 / 15 / 0 | kind = trip / standby / other |
| timing | 20 / 5 / 0 | first change before the primary alarm / unknown / after |
| magnitude | 15, or 4–10, or 0 | worst row past an SOP limit / outside normal (scaled) / neither |
| corroboration | 5 each, max 15 | abnormal evidence rows (all of them for trips; all but one otherwise) |
| sop | 10 | any abnormal row past a limit **that has any action text** |
| topology | 10 | candidate or its abnormal rows reach the symptom upstream |
| history | 5 | equipment code appears in an earlier resolution note (**not wired: always 0**) |
| contradiction | −5 each, max −15 | related readings that stayed normal |

Sort: points ↓, then kind (trip > standby > sop > early), then time. `insufficient` = not trip/standby, ≤ 1 abnormal row and ≥ 1 normal row.

### 1.4 Roles (`assign_roles`)

Insufficient candidates go last. Candidates first seen after the primary alarm cannot lead if an earlier one exists. The top one is **leading**. Others: trip/sop/standby → **contributing** (by kind, regardless of evidence); otherwise by configured links and time → related condition (chain), contributing, or possible consequence (effect).

### 1.5 Weaknesses found

| # | Issue | Effect (Scenario 2 figures) |
|---|---|---|
| W1 | **Correlated readings are counted as independent corroboration.** P-102 flow, discharge pressure and current describe one physical behaviour (less water pumped). | Clog gets +10 corroboration for what is one observation. |
| W2 | **Consequences of a trip are counted as support for it.** P-101 current 0 A and "P-101 running = false" are what a trip *does*. | Trip gets +5 corroboration from its own consequence. |
| W3 | **One limit breach is scored twice** (magnitude 15 + sop 10), and "sop" fires for **any** limit action, not a diagnostic one. | Trip gets +10 "SOP limit" because the winding-temperature action is "Do not restart until cool", which is not a diagnosis. |
| W4 | **A trip is treated as the cause.** It proves a protective condition occurred; it does not prove why (overload, bearing, supply, ambient…). The statement says "cause not recorded" but the role says "Leading hypothesis". | The operator reads "P-101 trip" as the root cause. |
| W5 | **Timing is binary** (before / after the alarm). Order *between* candidates and lead time are ignored. | The clog started 26 s before the trip and inflow 34 s before; this does not register. |
| W6 | **"First change" is the story's sample-to-sample threshold**, not when a reading left its normal band. Onset can be late or early by several samples. | Timing is imprecise. |
| W7 | **No operating-condition consistency.** Nothing checks that a hypothesis fits the operating state (a clog with the pump running and current low; a trip with current 0). | A "clog" with high current would score the same. |
| W8 | **Contradictions are only "related readings stayed normal".** Opposite-direction readings, a running status that contradicts a stop, stale or missing data are not evaluated. | Contradictory evidence is under-weighted. |
| W9 | **Missing information is not represented.** No P-101 flow meter, no suction pressure, no inspection, no vibration: the score cannot say how much of the expected evidence was available. | High points can rest on thin coverage. |
| W10 | **Roles by kind.** Any trip/SOP/standby becomes "contributing" even with weak evidence; a process condition (inflow) can lead if no fault exists, and nothing prevents it leading when it does. | Requirement: a contributing process condition must not lead. |
| W11 | **Topology is binary** (any path = 10). Distance and mechanism are ignored. | Weak discrimination. |
| W12 | **History factor not wired**, and the intended rule (equipment code in a note) would accept irrelevant fixes. | Requirement 7 not met. |
| W13 | **Equipment grouping by label regex** (`[A-Z]{1,4}-\d…`) can miss or merge equipment. | Fragile grouping. |
| W14 | **Truncation to 4 candidates happens before roles**, so a later, better-explained candidate can be dropped. | Rare, but silent. |
| W15 | **The audit trail is prose.** Supporting/contradicting lines carry no structured source, timestamp, expected value or contribution. | Requirement 9 not met. |

What is sound and should be kept: record-only inputs, the symptom exclusion, the after-alarm rule, the lone-instrument check, deterministic ordering, "not a probability" wording, and the reference-isolation test.

---

## 2. Proposed scoring model ("evidence support model v2")

### 2.1 Hypothesis classes (generated separately, compared explicitly)

| Class | Meaning | Generated from | May lead? |
|---|---|---|---|
| **Confirmed equipment fault condition** | A protective or fault state *occurred* (recorded status). Says nothing about why. | fault/trip/fail status turn-on | Yes, as "the condition that started the event"; its physical cause is reported separately |
| **Suspected physical fault** | A physical fault inferred from a signature (clog, ballast failure, blower failure, instrument fault). | SOP diagnostic actions (`suspect X`), configured fault signatures, the lone-instrument rule | Yes |
| **Contributing process condition** | An operating condition that adds load or reduces margin (high inflow, high solids, temperature). | abnormal process readings not explained by a fault hypothesis | **Only if no fault hypothesis has support ≥ the minimum**; otherwise capped at Contributing |
| Physical cause of a confirmed condition | "Why did P-101 trip?" with the evidence that narrows it, or "not determinable from the record". | attached to each confirmed condition | Shown with it, never invented |

### 2.2 Evidence groups (no double counting)

Every measurement belongs to one **evidence group**: one physical behaviour of one piece of equipment, from configuration (equipment + quantity family), e.g.

* P-102 hydraulic output: `FIT-102-FLW`, `PIT-102-DIS`, `MOT-102-CUR`
* P-101 thermal: `TIT-101-WDG`
* P-101 drive state (consequence of run/trip): `MOT-101-CUR`, `P101-RUN`
* Wet-well inflow: `FIT-101-IN`

A group counts **once** toward corroboration. Agreement inside a group raises that group's *consistency*, not its count. A group that is the expected consequence of a candidate (drive state after a trip) is scored under operating consistency, never as corroboration.

Configuration change needed: an `evidence_group` attribute on each plant signal (default: equipment + unit family), plus signatures per equipment type (below). No scenario data.

### 2.3 Dimensions (each 0–1, then weighted)

| Dimension | Weight | 1.0 means | Rule |
|---|---|---|---|
| **D: direct fault evidence** | 25 | a recorded fault status | 1.0 recorded status; 0.6 full SOP diagnostic signature (every "suspect X" limit for that equipment breached); 0.3 partial; 0 none |
| **T: temporal precedence** | 20 | clearly before the symptom | onset = first sample outside the normal band (not the story threshold). 0 if after the primary alarm; else 0.5 + 0.5 × min(1, lead time before symptom onset / 60 s); 0.25 if unknown. Reported with the onset time and the lead/lag versus every other candidate |
| **C: independent corroboration** | 20 | ≥ 2 independent groups agree | groups other than the candidate's own that support it: 0 → 0, 1 → 0.5, ≥ 2 → 1.0 |
| **E: engineering relationship** | 15 | direct configured link to the symptom | 1 hop 1.0; 2 hops 0.7; 3+ hops 0.4; none 0. Mechanism text from configuration |
| **O: operating-condition consistency** | 20 | every expected check holds | share of configured checks for that hypothesis type that pass, e.g. *trip*: current → 0 and run = false; *clog*: running, flow low, pressure low, current low; *instrument*: related readings normal |
| **X: contradictions** | penalty, max −30 | | strong −10 (opposite-direction reading, running status that rules it out, onset after the alarm); weak −5 (related reading stayed normal). Each listed with its source |
| **M: missing information** | not in the score | | expected evidence that is not available (no sensor, stale, no samples, not inspected). **Coverage %** = expected evidence items available ÷ expected for that hypothesis type (from the signature configuration); coverage < 50 % caps the strength word at "Limited" |

**Support score** = 25·D + 20·T + 20·C + 15·E + 20·O − X, clamped to 0–100. Deterministic, integer-rounded for display. Shown as "evidence support", never as a probability or confidence.

### 2.4 History (only when similar)

An earlier resolved incident counts only if **all** of: same primary alarm or same equipment; Jaccard similarity of abnormal evidence groups ≥ 0.6; the earlier resolution names the hypothesis' equipment; and no contradicting current evidence. Then +5 under D (capped at 1.0) and the item is listed with its incident id. Otherwise it is listed as "earlier fix not similar enough (similarity 0.25)" with 0 contribution. Simulated (seeded) history is labelled.

### 2.5 Leading selection and roles

1. Rank fault hypotheses (confirmed conditions and suspected physical faults) by support score; ties: higher D, earlier onset, then tag order.
2. A candidate with onset after the primary alarm cannot lead while an earlier one exists (kept).
3. Process conditions are ranked separately and become **Contributing condition** unless no fault hypothesis reaches the minimum support (proposed 35), in which case the strongest process condition leads with the note "no equipment fault is supported by the record".
4. Insufficient evidence: support < 25, or coverage < 30 % with a strong contradiction.
5. Related condition / possible consequence: configured links and onset order, as today.

### 2.6 Competing-hypothesis comparison

For the leader and each runner-up: a per-dimension difference table and one sentence built from the largest differences, e.g. "P-101 trip ranks above the P-102 clog: a recorded trip (+10 D) and an independent thermal reading (+10 C) outweigh the clog's earlier onset (−4 T). Both may be needed to explain the level rise."

### 2.7 Audit record (per evidence item)

```json
{"hypothesis": "P-102 clog", "dimension": "O", "group": "P-102 hydraulic output",
 "source": {"type": "reading", "tag": "PIT-102-DIS"}, "timestamp": "07:07:04 UTC",
 "observed": "1.1 bar", "expected": "2.8 to 3.4 bar normal; 2.0 bar 'Suspect clog' (SOP-LFT-001 p.14)",
 "effect": "supports", "contribution": 5.0, "note": "low pressure with the pump running"}
```

Types: reading, status, alarm, SOP limit, configured link, history, missing. Contradictions carry `"effect": "contradicts"` and a negative contribution.

---

## 3. Example: Scenario 2 (lift station), run of 11 Nov 2026

Recorded (UTC): inflow FIT-101-IN 425 → 610 m³/h, onset 07:06:46. P-102 flow 421 → 90 m³/h, onset 07:06:54, SOP 300 m³/h "Suspect clog" crossed 07:07:00. P-102 pressure 2.99 → 1.1 bar, onset 07:06:54, 2.0 bar "Suspect clog" crossed 07:07:04. P-102 current 31 → 19 A, onset 07:06:58, 24 A "Investigate" crossed 07:07:04. P-102 running = true. P101-FAULT on 07:07:20. P-101 winding 63.5 → 118 °C, 105 °C trip limit crossed 07:07:20. P-101 current → 0 A at 07:07:22, P-101 running = false. Wet-well level onset 07:07:30, H 07:07:50, HH (primary) 07:08:02.

### 3.1 Current model (as shipped)

| | direct | timing | magnitude | corroboration | sop | topology | contra | **total** |
|---|---|---|---|---|---|---|---|---|
| P-101 trip | 25 | 20 | 15 | 10 (winding, current 0 A) | 10 ("do not restart until cool") | 10 | 0 | **90** |
| P-102 clog | 0 | 20 | 15 | 10 (3 rows − 1) | 10 | 10 | 0 | **65** |
| Inflow high | 0 | 20 | 10 | 0 | 0 | 10 | 0 | **40** |

Double counts: W1 (clog +10), W2 (trip +5), W3 (trip +10 sop and +15 magnitude from the same breach).

### 3.2 Proposed model

**H1: P-101 thermal overload trip (confirmed equipment fault condition)**

| Dim | Value | Evidence | Pts |
|---|---|---|---|
| D | 1.0 | P101-FAULT on 07:07:20 | 25.0 |
| T | 0.58 | onset 07:07:20, 10 s before level onset 07:07:30 | 11.7 |
| C | 0.5 | 1 independent group: P-101 thermal (118 °C > 105 °C trip at 07:07:20) | 10.0 |
| E | 1.0 | P-101 → wet-well level, 1 hop | 15.0 |
| O | 1.0 | 2/2: current 0 A at 07:07:22; running = false | 20.0 |
| X | 0 | none | 0 |
| M | | expected for a pump trip: status, winding temperature, drive state, pump flow, vibration. Available 3 of 5 (no P-101 flow meter, no vibration); cause of the trip not recorded | coverage 60 % |
| | | **Support** | **82** |

Physical cause of the trip: "Winding over-temperature recorded before the trip. Overload, cooling or bearing causes cannot be told apart from the recorded data."

**H2: P-102 clog (suspected physical fault)**

| Dim | Value | Evidence | Pts |
|---|---|---|---|
| D | 0.6 | full SOP signature: flow < 300 and pressure < 2.0 ("Suspect clog", SOP-LFT-001 p.14) | 15.0 |
| T | 0.8 | onset 07:06:54, 36 s before level onset | 16.0 |
| C | 0 | flow, pressure and current are **one** group; no other group | 0 |
| E | 1.0 | P-102 → wet-well level, 1 hop | 15.0 |
| O | 1.0 | 4/4: running, flow low, pressure low, current low | 20.0 |
| X | 0 | none | 0 |
| M | | expected for a clog: flow, discharge pressure, current, running status, suction pressure. Available 4 of 5 (no suction pressure, so an air-lock or closed suction valve gives the same signature) | coverage 80 % |
| | | **Support** | **66** |

**H3: Inflow high (contributing process condition)**

| Dim | Value | Evidence | Pts |
|---|---|---|---|
| D | 0 | none | 0 |
| T | 0.87 | onset 07:06:46, 44 s before level onset | 17.3 |
| C | 0 | none | 0 |
| E | 1.0 | inflow → wet-well level, 1 hop | 15.0 |
| O | 0.5 | consistent with the level rise; no outflow total to close the balance (missing) | 10.0 |
| | | **Support** | **42**, capped at Contributing (process condition, fault hypotheses exist) |

**Result:** P-101 trip leads (82), P-102 clog contributing (66), high inflow contributing condition (42). The order matches today's, but each number now rests on independent evidence. The comparison reads: "P-101 trip ranks above the P-102 clog: a recorded trip (+10 direct) and an independent thermal reading (+10 corroboration) outweigh the clog's earlier onset (−4.3 timing). The clog began 26 s before the trip. The record cannot show whether either alone would have raised the level; treat both as active."

---

## 4. Test plan and expected behaviour

All tests use simulator variants of the existing scenarios (as in `test_validation_scenarios.py`): telemetry changes only, assertions on the computed output, references read only by tests.

| # | Case | Set-up | Expected |
|---|---|---|---|
| 1 | Two simultaneous equipment faults | Scenario 2 as is | Trip leads (≈82), clog contributing (≈66); comparison names D and C gains and the clog's earlier onset |
| 2 | Misleading sensor | P-102 flow low only; pressure, current normal; pump running | No clog. "P-102 flow instrument" insufficient; contradictions list pressure and current normal (strong); coverage shown |
| 3 | Missing sensor data | Scenario 2 with no TIT-101-WDG samples | Trip still a confirmed condition; C = 0; M lists the winding temperature; support ≈ 72; no crash |
| 4 | Recorded trip, no confirmed physical cause | P101-FAULT on, winding temperature normal | Trip = confirmed condition; physical cause "not determinable"; "winding temperature normal" listed as contradicting a thermal cause |
| 5 | Contributing condition must not lead | Inflow rises first and furthest, with a clog | Inflow stays Contributing even with the earliest onset; it leads only in a variant with no fault hypothesis ≥ 35 |
| 6 | Contradictory evidence | Clog signature but P-102 current high (≥ 34 A) | O drops (3/4), strong contradiction −10; clog support falls below the trip's; contradiction listed with its tag and value |
| 7 | Changed event timing | Trip after the HH alarm (D1) | Trip cannot lead; clog leads; note "first appeared after the primary alarm" |
| 8 | Irrelevant earlier fix | Seeded resolved incident: different alarm, P-102 named, different abnormal groups (similarity < 0.6) | History contributes 0; listed as "not similar enough (0.2x)"; ranking unchanged |
| 9 | Relevant earlier fix | Same alarm, same groups, P-102 named | +5 to the clog under D, with the incident id |
| 10 | All 8 scenarios | as shipped | Each matches its reference outcome; any change from today's leader is reported as a discrepancy, not tuned away |
| 11 | Determinism | Same run twice | Identical scores, order, audit records |
| 12 | Isolation | Code check | No ranking module reads scenario titles, injected conditions or `REFERENCE_OUTCOME` |
| 13 | Audit completeness | Every candidate | Every non-zero contribution has an audit item with source, timestamp, observed and expected values; contributions sum to the score |

---

## 5. Output schema and migration

Kept unchanged for one release: `role`, `role_label`, `role_note`, `rank`, `statement`, `short`, `supporting`, `contradicting`, `evidence_points` (= the new support score), `evidence_factors` (keys become `D, T, C, E, O, X`), `ranking_note`. The Root Cause page factor table and the Inspector render factors from a list, so new dimension names show without a frontend change.

Added: `ranking_model: "v2"`, `hypothesis_class`, `dimensions` (value 0–1, weight, points, reason), `evidence_items` (the audit records), `contradictions`, `missing`, `coverage`, `physical_cause` (for confirmed conditions), `comparison` (per-dimension differences vs the leader).

Steps:

1. Add the evidence-group and signature configuration, with defaults so every current tag has a group.
2. Compute v2 alongside v1 behind `OPSLENS_RANKING_MODEL=v1|v2` (default v1). The Inspector shows both, for comparison on all 8 scenarios.
3. Run the test plan and review differences with you.
4. Switch the default to v2; the HMI adds the class chip and the coverage line; the Inspector shows the audit table.
5. Remove v1 after one release.

Evidence Support (the Home banner percentage) is a separate score and is not changed by this work.

## 6. Decisions needed before implementation

1. The weights (25/20/20/15/20) and the contradiction cap (−30).
2. Whether a confirmed trip may be called the "leading hypothesis", or the leader is phrased as "the condition that started the event" with its physical cause separate.
3. The minimum support (35) below which a process condition may lead.
4. Whether to configure evidence groups by hand for the 8 equipment areas, or derive them from equipment and units and review the result.
