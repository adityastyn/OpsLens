# OpsLens — the 8 simulated incident scenarios

Generated from `edge_intelligence/incident_catalog.py` (the single source the simulator, alarms, AI prompt and HMI all use). All data is **simulated** (Scenario Lab); limits come from the plant SOP manual SOP-MAN-000 (draft for review) unless marked as demonstration values. Deviation is measured from the **worst incident value** to the nearest alarm/trip limit it crossed; if it crossed none, from the normal range.

## The imaginary plant

Mid-size municipal wastewater plant, about 900–1,200 m³/h. Liquid line: sewers and lift station (wet well, P-101/P-102) → inlet works (bar screens BS-1/BS-2, grit) → primary clarifiers → aeration tanks (blowers B-1/B-2, standby B-3) → secondary clarifiers (RAS-P1/RAS-P2 return sludge) → tertiary filtration → disinfection (UV channel, lamp banks 1/2) → discharge. Sludge line: WAS → thickening → anaerobic digestion → dewatering centrifuge (centrate returns to the inlet). Gas line: digester → gas holder → CHP engine, flare.

| Equipment ID | What it is | Area (knowledge graph) |
|---|---|---|
| InletWorks-01 | Inlet works (screens BS-1/BS-2) | Preliminary treatment |
| LiftStation-01 | Lift station (wet well, P-101/P-102) | Preliminary treatment |
| AerationBasin-01 | Aeration tanks (blowers, diffusers) | Biological treatment |
| SecondaryClarifier-01 | Secondary clarifiers (blanket probe) | Biological treatment |
| AerationBlower-01 | Aeration blowers (B-1/B-2/B-3) | Biological treatment |
| RASSystem-01 | RAS pumps (RAS-P1/RAS-P2) | Biological treatment |
| BiogasSystem-01 | Gas holder, CHP engine, flare | Gas line |
| DewateringCentrifuge-01 | Dewatering centrifuge | Sludge line |
| Effluent-01 | Final effluent | Effluent |
| UVChannel-01 | UV disinfection channel (lamp banks 1/2) | Disinfection |

## Summary

| # | Scenario | Equipment | Primary alarm | Leading cause (scripted root) | SOP | Coverage | Severity |
|---|---|---|---|---|---|---|---|
| 1 | Rags jam the bar screen | InletWorks-01 | ALM-SCR-110-DL | Bar screen BS-2 rake jammed with rags; motor overload tripped | SOP-INL-001 | full | High |
| 2 | The lift station pumps clog | LiftStation-01 | ALM-WW-101-HH | P-101 tripped on thermal overload; P-102 impeller wrapped in cloth | SOP-LFT-001 | full | Critical |
| 3 | Nocardia foam on the aeration tank | AerationBasin-01 | ALM-AER-201-FOAM | Tank 3 foaming; sludge age far above target | SOP-BIO-001 | full | Medium |
| 4 | RAS pump fails, sludge blanket rises | RASSystem-01 | ALM-SLB-201-HIGH | RAS-P1 motor overheated and tripped; RAS-P2 at full speed | SOP-BIO-002 | full | High |
| 5 | CHP engine trips, gas holder fills | BiogasSystem-01 | ALM-GAS-601-HIGH | Engine tripped on a faulty spark plug sensor | SOP-GAS-001 | full | High |
| 6 | Centrate poisons the plant inlet | DewateringCentrifuge-01 | ALM-SLD-701-CAPTURE | Centrifuge bowl speed and polymer dose set wrongly after repair | SOP-SLD-001 | partial | High |
| 7 | Low dissolved oxygen due to aeration blower failure | AerationBlower-01 | ALM-AER-201-DO | Blower B-1 motor overload trip; standby blower B-3 did not start | SOP-AER-001 | full | High |
| 8 | UV disinfection intensity low (no SOP) | UVChannel-01 | ALM-UV-901-INT-LO | Lamp bank 2 ballast fault; bank 2 lamps off | none | none | High |

## 1. Rags jam the bar screen

- **Scenario ID:** `screen_differential_pressure`
- **Story:** Meera, night operator, 2:10 AM. A storm flushes rags into the plant while Screen 1 is out for repair. Screen 2 jams and the channel level climbs toward the overflow weir.
- **Equipment / area:** InletWorks-01 — Inlet works (screens BS-1/BS-2) (Preliminary treatment)
- **Primary alarm:** ALM-SCR-110-DL — Screen BS-2 differential level high
- **Root cause (scripted, never shown to the AI):** Inlet screens (BS-1/BS-2): Bar screen BS-2 rake jammed with rags; motor overload tripped
- **Knock-on effects:** Lift station — Pumps push against a rising channel level (higher discharge head); Grit removal — Rags can slip past the bypass rack and foul the grit equipment; Flow measurement — Backwater from the blocked screen distorts the flume reading
- **Severity / priority:** High. P1 if the channel reaches the 1.8 m weir (SOP-INL-001 §8, p.12); the incident peaks at 1.65 m.
- **SOP:** SOP-INL-001 Bar Screen Operation and Blockage Response (coverage: full)
  - Covered: differential level 30 cm, channel level 1.5 m action / 1.8 m weir, rake current 14 A trip, 1,500 m³/h single-screen capacity, bypass rack use, lockout before clearing (pages 11–12).
  - Gap: the SOP gives no normal range for the screenings removal rate (0.1–0.3 m³/h is incident-map data); it only says to record screenings volume each shift (p.11).
- **Expected fix / recovery:** Isolate and clear BS-2, open bypass at 1.5 m, return channel below 1.2 m and differential below 15 cm.

**Readings (normal → incident → after the fix):**

| Tag | Reading | Normal | Incident | After fix | Limits (alarm raised) | Deviation at incident |
|---|---|---|---|---|---|---|
| FIT-110-IN | Inlet flow vs one-screen capacity (InletWorks-01) | 900–1,200 m³/h | 2,150 m³/h | 1,480 m³/h (throttled) | 1,500 m³/h | **650 m³/h above the 1,500 m³/h, 43% past it** |
| LDT-110-SCR | Differential level across BS-2 (InletWorks-01) | 5–15 cm | 52 cm | 9 cm | 30 cm (high alarm) → ALM-SCR-110-DL | **22 cm above the 30 cm (high alarm), 73% past it** |
| SCR-110-MOT-CUR | Rake motor current (InletWorks-01) | 6–9 A | 15.2 A (tripped) | 7.5 A | 14 A (overload trip) | **1.2 A above the 14 A (overload trip), 9% past it** |
| LIT-110-CH | Upstream channel level (InletWorks-01) | 0.8–1.2 m | 1.65 m | 1.0 m | 1.8 m (overflow weir) → ALM-INL-110-CH-HH | 0.45 m above normal (max 1.2), 38% over |
| SCR-110-SCREENINGS | Screenings removed (InletWorks-01) | 0.1–0.3 m³/h | 0.9 m³/h, then 0 as rake stops | 0.3 m³/h | — | 0.6 m³/h above normal (max 0.3), 200% over |

**Status and trip signals:**

| Tag | Signal | Normal → incident | Kind | What happens |
|---|---|---|---|---|
| SCR-110-RAKE-RUN | BS-2 rake running | True → False | status (evidence) | Rake stopped by motor overload (when trip `rake_overload` latches) |
| SCR-110-RAKE-TRIP | BS-2 rake motor overload trip | False → True | alarm input (fault) | Motor overload tripped (when trip `rake_overload` latches) |
| SCR-110-BYPASS-OPEN | Bypass rack gate open | False → True | status (evidence) | Closed → Open (manual) (at 90 s) |

**Trips:** `rake_overload` when SCR-110-MOT-CUR goes above 15.2

**Alarms raised in this run:** ALM-SCR-110-DL, SCR-110-RAKE-TRIP

## 2. The lift station pumps clog

- **Scenario ID:** `wet_well_high_level`
- **Story:** Arjun, maintenance technician, 11:45 PM. Rain raises the inflow while a wad of cloth chokes both pumps. The wet well rises toward the level where sewage backs up into homes.
- **Equipment / area:** LiftStation-01 — Lift station (wet well, P-101/P-102) (Preliminary treatment)
- **Primary alarm:** ALM-WW-101-HH — Wet well level high-high
- **Root cause (scripted, never shown to the AI):** Lift station (P-101/P-102): P-101 tripped on thermal overload; P-102 impeller wrapped in cloth
- **Knock-on effects:** Sewers — Sewage backs up toward basements and manholes; Inlet screens — Surge of flow arrives when the pumps are restored
- **Severity / priority:** Critical. P1: wet well reaches 3.85 m (SOP-LFT-001 §7, p.15).
- **SOP:** SOP-LFT-001 Lift Station High-High Level and Pump Clog Response (coverage: full)
  - Covered: wet well 3.2/3.6/4.0 m and P1 at 3.85 m, motor current 42 A / 24 A, winding 105 °C trip, discharge pressure 2.0 bar, pump flow 300 m³/h, tanker, isolation and restart checks (pages 14–15).
  - Gap: the SOP sets no limit for wet-well inflow (610 m³/h); step 5 only says to reduce inflow where the network allows (p.14).
- **Expected fix / recovery:** Tanker at 3.6 m, clear P-102, cool and reset P-101 after cause identified, level back below 2.4 m.

**Readings (normal → incident → after the fix):**

| Tag | Reading | Normal | Incident | After fix | Limits (alarm raised) | Deviation at incident |
|---|---|---|---|---|---|---|
| FIT-101-IN | Inflow to wet well (LiftStation-01) | 380–450 m³/h | 610 m³/h (rain) | 480 m³/h | — | 160 m³/h above normal (max 450), 36% over |
| FIT-102-FLW | P-102 flow (LiftStation-01) | 380–450 m³/h | 90 m³/h | 420 m³/h | 300 m³/h (low alarm) → ALM-P102-FLW-LO | **210 m³/h below the 300 m³/h (low alarm), 70% past it** |
| PIT-102-DIS | P-102 discharge pressure (LiftStation-01) | 2.8–3.4 bar | 1.1 bar | 3.1 bar | 2.0 bar (low alarm) → ALM-P102-DIS-LO | **0.9 bar below the 2.0 bar (low alarm), 45% past it** |
| MOT-102-CUR | P-102 motor current (LiftStation-01) | 28–34 A | 19 A (spinning, not pumping) | 32 A | 24 A (low-load alarm) → ALM-P102-CUR-LO | **5 A below the 24 A (low-load alarm), 21% past it** |
| TIT-101-WDG | P-101 winding temperature (LiftStation-01) | 55–75 °C | 118 °C (tripped) | 68 °C | 105 °C (thermal trip) | **13 °C above the 105 °C (thermal trip), 12% past it** |
| MOT-101-CUR | P-101 motor current (LiftStation-01) | 28–34 A | 0 A (tripped) | 31 A | 42 A (overload) | 28 A below normal (min 28), 100% under |
| LIT-101-WWL | Wet well level (LiftStation-01) | 1.0–2.4 m | 3.85 m (spills at 4.0 m) | 1.6 m | 3.2 m high → ALM-WW-101-H; 3.6 m high-high → ALM-WW-101-HH | **0.25 m above the 3.6 m high-high, 7% past it** |

**Status and trip signals:**

| Tag | Signal | Normal → incident | Kind | What happens |
|---|---|---|---|---|
| P101-FAULT | P-101 thermal overload trip | False → True | alarm input (fault) | P-101 tripped on thermal overload (when trip `p101_thermal` latches) |
| P101-RUN | P-101 running | True → False | status (evidence) | P-101 stopped by its trip (when trip `p101_thermal` latches) |
| P102-RUN | P-102 running | True → True | status (evidence) | P-102 keeps running (impeller wrapped, not pumping) (unchanged) |

**Trips:** `p101_thermal` when TIT-101-WDG goes above 105

**Alarms raised in this run:** ALM-P102-FLW-LO, ALM-P102-DIS-LO, ALM-P102-CUR-LO, ALM-WW-101-H, ALM-WW-101-HH, P101-FAULT

## 3. Nocardia foam on the aeration tank

- **Scenario ID:** `biological_foam`
- **Story:** Priya, process engineer, 6:30 AM. Two weeks of too little sludge wasting let sludge age climb. Greasy brown foam builds on Tank 3 and spills into the clarifier.
- **Equipment / area:** AerationBasin-01 — Aeration tanks (blowers, diffusers) (Biological treatment)
- **Primary alarm:** ALM-AER-201-FOAM — Biological foam high
- **Root cause (scripted, never shown to the AI):** Aeration tanks: Tank 3 foaming; sludge age far above target
- **Knock-on effects:** Secondary clarifiers — Foam carries flocs over the weirs; Effluent — Turbidity close to the permit limit
- **Severity / priority:** Medium. Escalate to the Plant Manager at effluent TSS 25 mg/L or turbidity 10 NTU (p.18).
- **SOP:** SOP-BIO-001 Activated Sludge Foam Control (coverage: full)
  - Covered: SRT 15 days, WAS below 120 m³/d, MLSS 4,500 mg/L, F/M below 0.15, DO below 1.0 mg/L, foam 10 cm, turbidity 10 NTU, microscopy before changing control, 25 % per day SRT change limit (pages 17–18).
  - Gap: effluent TSS normal range and the 30 mg/L permit are in SOP-BIO-002 (p.19), not SOP-BIO-001; SOP-BIO-001 only gives the 25 mg/L escalation (p.18).
- **Expected fix / recovery:** Raise WAS stepwise (≤25 %/day SRT change), foam below 5 cm for 3 days, SRT back to ~11 days.

**Readings (normal → incident → after the fix):**

| Tag | Reading | Normal | Incident | After fix | Limits (alarm raised) | Deviation at incident |
|---|---|---|---|---|---|---|
| AER-201-WAS-FLOW | WAS (wasting) rate (AerationBasin-01) | 180–250 m³/d | 70 m³/d | 260 m³/d | < 120 m³/d → ALM-AER-201-WAS-LO | **50 m³/d below the < 120 m³/d, 42% past it** |
| AER-201-SLUDGE-AGE | Sludge age (SRT) (AerationBasin-01) | 5–10 days | 21 days | 11 days (recovers over ~3 weeks) | 15 days → ALM-AER-201-SRT-HI | **6 days above the 15 days, 40% past it** |
| AER-201-MLSS | MLSS (AerationBasin-01) | 2,500–3,500 mg/L | 5,100 mg/L | 3,300 mg/L | 4,500 mg/L → ALM-AER-201-MLSS-HI | **600 mg/L above the 4,500 mg/L, 13% past it** |
| AER-201-FM | F/M ratio (AerationBasin-01) | 0.2–0.4 | 0.09 | 0.28 | < 0.15 → ALM-AER-201-FM-LO | **0.06 ratio below the < 0.15, 40% past it** |
| AIT-201-DO | Dissolved oxygen (AerationBasin-01) | 1.5–3.0 mg/L | 2.4 mg/L | 2.2 mg/L | < 1.0 mg/L → ALM-AER-201-DO | within normal (unchanged) |
| AER-201-FOAM-DEPTH | Foam depth on surface (AerationBasin-01) | < 2 cm | 45 cm | < 5 cm (3–5 days) | 10 cm → ALM-AER-201-FOAM | **35 cm above the 10 cm, 350% past it** |
| EFF-301-TURB | Effluent turbidity (Effluent-01) | 2–5 NTU | 8.8 NTU, rising | 3.5 NTU | 10 NTU (permit) → ALM-EFF-301-TURB-HI | 3.8 NTU above normal (max 5), 76% over |
| EFF-301-TSS | Effluent TSS (Effluent-01) | 8–15 mg/L | 26 mg/L | 11 mg/L | 30 mg/L (permit) → ALM-EFF-301-TSS-HI | 11 mg/L above normal (max 15), 73% over |

**Alarms raised in this run:** ALM-AER-201-WAS-LO, ALM-AER-201-SRT-HI, ALM-AER-201-MLSS-HI, ALM-AER-201-FM-LO, ALM-AER-201-FOAM

## 4. RAS pump fails, sludge blanket rises

- **Scenario ID:** `ras_pump_failure`
- **Story:** Rohan, night operator, 3:20 AM. RAS-P1 trips on a hot motor. RAS-P2 alone cannot return enough sludge, so the clarifier fills while the aeration tank is starved of bacteria.
- **Equipment / area:** RASSystem-01 — RAS pumps (RAS-P1/RAS-P2) (Biological treatment)
- **Primary alarm:** ALM-SLB-201-HIGH — Sludge blanket high
- **Root cause (scripted, never shown to the AI):** RAS pumps: RAS-P1 motor overheated and tripped; RAS-P2 at full speed
- **Knock-on effects:** Secondary clarifiers — Sludge blanket climbs toward the overflow weirs; Aeration tanks — MLSS falls because returned sludge is short; Effluent — Solids start to carry over into the discharge
- **Severity / priority:** High. P1 if blanket reaches 2.5 m or effluent TSS 30 mg/L (p.20); incident peaks at 2.1 m / 24 mg/L.
- **SOP:** SOP-BIO-002 RAS Pump Failure and Sludge Blanket Control (coverage: full)
  - Covered: total RAS 550 m³/h, per-pump flow 350–420 m³/h, winding 130 °C trip, blanket 1.5 m alarm / 2.0 m WAS action / 2.5 m weir, solids loading 6.5 kg/m²/h, aeration MLSS 2,000 mg/L, effluent TSS 30 mg/L (pages 19–20).
  - Gap: none for the incident readings. Availability of the portable standby pump (step 4) is a site fact OpsLens cannot observe.
- **Expected fix / recovery:** Max RAS-P2, portable pump, reduce inflow, repair RAS-P1, blanket below 0.8 m in 4–8 h.

**Readings (normal → incident → after the fix):**

| Tag | Reading | Normal | Incident | After fix | Limits (alarm raised) | Deviation at incident |
|---|---|---|---|---|---|---|
| TIT-RAS-P1-WDG | RAS-P1 winding temperature (RASSystem-01) | 60–80 °C | 142 °C | 72 °C | 130 °C (trip) | **12 °C above the 130 °C (trip), 9% past it** |
| RAS-P1-FLOW | RAS-P1 flow (RASSystem-01) | 350–420 m³/h | 0 m³/h (tripped) | 390 m³/h | — | 350 m³/h below normal (min 350), 100% under |
| RAS-FLOW | Total RAS flow (RASSystem-01) | 700–840 m³/h | 410 m³/h | 760 m³/h | 550 m³/h (low alarm) → ALM-RAS-201-FLOW-LO | **140 m³/h below the 550 m³/h (low alarm), 25% past it** |
| SLB-201-DEPTH | Sludge blanket depth (SecondaryClarifier-01) | 0.3–0.8 m | 2.1 m (weirs at ~2.5 m) | 0.6 m | 1.5 m (high alarm) → ALM-SLB-201-HIGH | **0.6 m above the 1.5 m (high alarm), 40% past it** |
| CLR-201-SLR | Clarifier solids loading (SecondaryClarifier-01) | 3–5 kg/m²/h | 7.4 kg/m²/h | 4.2 kg/m²/h | 6.5 kg/m²/h → ALM-CLR-201-SLR-HI | **0.9 kg/m²/h above the 6.5 kg/m²/h, 14% past it** |
| AER-201-MLSS | Aeration MLSS (AerationBasin-01) | 2,500–3,500 mg/L | 1,900 mg/L, falling | 3,000 mg/L | 2,000 mg/L (low) → ALM-AER-201-MLSS-LO | **100 mg/L below the 2,000 mg/L (low), 5% past it** |
| EFF-301-TSS | Effluent TSS (Effluent-01) | 8–15 mg/L | 24 mg/L, rising | 12 mg/L | 30 mg/L (permit) → ALM-EFF-301-TSS-HI | 9 mg/L above normal (max 15), 60% over |

**Status and trip signals:**

| Tag | Signal | Normal → incident | Kind | What happens |
|---|---|---|---|---|
| ALM-RAS-P1-FAIL | RAS-P1 thermal trip | False → True | alarm input (fault) | RAS-P1 motor overheated and tripped (when trip `ras_p1_thermal` latches) |
| RAS-P1-RUN | RAS-P1 running | True → False | status (evidence) | RAS-P1 stopped by its trip (when trip `ras_p1_thermal` latches) |

**Trips:** `ras_p1_thermal` when TIT-RAS-P1-WDG goes above 130

**Alarms raised in this run:** ALM-RAS-201-FLOW-LO, ALM-SLB-201-HIGH, ALM-CLR-201-SLR-HI, ALM-AER-201-MLSS-LO, ALM-RAS-P1-FAIL

## 5. CHP engine trips, gas holder fills

- **Scenario ID:** `biogas_holder_pressure`
- **Story:** Kavita, shift supervisor, 1:00 AM. A sensor fault stops the CHP engine. The digester keeps making biogas and the holder roof rises toward its limit until the flare takes over.
- **Equipment / area:** BiogasSystem-01 — Gas holder, CHP engine, flare (Gas line)
- **Primary alarm:** ALM-GAS-601-HIGH — Gas holder pressure high
- **Root cause (scripted, never shown to the AI):** CHP engine / boiler: Engine tripped on a faulty spark plug sensor
- **Knock-on effects:** Gas holder — Filling to the roof limit; relief valve is the last defence; Flare — Opens automatically and burns the surplus gas; Anaerobic digester — Keeps producing gas, so back-pressure builds
- **Severity / priority:** High. P1 at 10 % LEL, or at 38 mbar if the flare is not burning (pp.22–23); incident: 36 mbar, 4 % LEL.
- **SOP:** SOP-GAS-001 Biogas Holder High Pressure, CHP Trip and Flare Operation (coverage: full)
  - Covered: holder 32 mbar alarm / 38 mbar relief, fill 90 %, production, CHP consumption 100 m³/h, output 380–420 kWe, flare verification (~185 m³/h), 10 % LEL evacuation, no hot work (pages 21–23).
  - Gap: no CHP engine fault-diagnosis procedure for the spark plug sensor; steps 8–9 defer to the CHP technician and the manufacturer's start sequence (p.23).
- **Expected fix / recovery:** Flare verified burning, exclusion zone, technician repairs sensor, CHP restarted, holder back to 15–25 mbar.

**Readings (normal → incident → after the fix):**

| Tag | Reading | Normal | Incident | After fix | Limits (alarm raised) | Deviation at incident |
|---|---|---|---|---|---|---|
| CHP-601-GAS | CHP gas consumption (BiogasSystem-01) | 150–190 m³/h | 0 m³/h | 170 m³/h | 100 m³/h (low-use alarm) → ALM-CHP-601-GAS-LO | **100 m³/h below the 100 m³/h (low-use alarm), 100% past it** |
| CHP-601-KWE | CHP electrical output (BiogasSystem-01) | 380–420 kWe | 0 kWe (tripped) | 400 kWe | — | 380 kWe below normal (min 380), 100% under |
| GAS-601-PROD | Biogas production (BiogasSystem-01) | 160–200 m³/h | 185 m³/h (unchanged) | 180 m³/h | — | within normal (unchanged) |
| GAS-601-PRESS | Holder pressure (BiogasSystem-01) | 15–25 mbar | 36 mbar | 22 mbar | 32 mbar alarm → ALM-GAS-601-HIGH; 38 mbar relief opens | **4 mbar above the 32 mbar alarm, 12% past it** |
| GAS-601-FILL | Holder fill level (BiogasSystem-01) | 40–70 % | 96 % | 60 % | 90 % → ALM-GAS-601-FILL-HI | **6 % above the 90 %, 7% past it** |
| FLR-601-FLOW | Flare gas flow (BiogasSystem-01) | 0 m³/h (standby) | 185 m³/h (burning) | 0 m³/h | — | 185 m³/h above normal (max 0) |
| GAS-601-LEL | Methane near holder vent (BiogasSystem-01) | 0 % LEL | 4 % LEL | 0 % LEL | 10 % LEL (alarm) → ALM-GAS-601-LEL-HI | 4 % LEL above normal (max 0) |

**Status and trip signals:**

| Tag | Signal | Normal → incident | Kind | What happens |
|---|---|---|---|---|
| CHP-601-TRIP | CHP engine trip | False → True | alarm input (fault) | Engine tripped on a faulty spark plug sensor (at 20 s) |
| CHP-601-RUN | CHP engine running | True → False | status (evidence) | CHP stopped (at 20 s) |
| FLR-601-RUN | Flare burning | False → True | status (evidence) | Flare opens automatically at high holder pressure (when trip `holder_pressure_high` latches) |

**Trips:** `holder_pressure_high` when GAS-601-PRESS goes above 32

**Alarms raised in this run:** ALM-CHP-601-GAS-LO, ALM-GAS-601-HIGH, ALM-GAS-601-FILL-HI, CHP-601-TRIP

## 6. Centrate poisons the plant inlet

- **Scenario ID:** `centrifuge_capture`
- **Story:** Nikhil, lab technician, 9:15 AM. After a repair the centrifuge runs at the wrong settings. Wet cake and ammonia-rich centrate flow back to the inlet and overload the aeration tanks.
- **Equipment / area:** DewateringCentrifuge-01 — Dewatering centrifuge (Sludge line)
- **Primary alarm:** ALM-SLD-701-CAPTURE — Centrifuge solids capture low
- **Root cause (scripted, never shown to the AI):** Dewatering: Centrifuge bowl speed and polymer dose set wrongly after repair
- **Knock-on effects:** Inlet screens — Centrate re-enters the plant at the inlet works; Aeration tanks — Ammonia shock load; blowers near maximum; Effluent — Effluent ammonia creeping toward the permit
- **Severity / priority:** High. Effluent ammonia 5 mg/L is a permit limit (p.25); incident reaches 3.8 mg/L, rising.
- **SOP:** SOP-SLD-001 Dewatering Centrifuge Operation and Centrate Recycle Control (coverage: partial)
  - Covered: bowl speed 2,900 rpm, polymer 6 kg/t DS, capture 88 %, cake 20 % DS, centrate ammonia 1,400 mg/L, inlet ammonia 50 mg/L, blower 4,200 Nm³/h, restore approved setpoints after repair, slow the centrate return over 24 h, 4-hourly effluent ammonia sampling (pages 24–26).
  - Gap: SOP-SLD-001 treats the centrate as an ammonia LOAD only. It does not cover inhibitory or toxic centrate: no nitrification-inhibition or toxicity test, and no aeration-tank response beyond pausing centrate return near 4,200 Nm³/h (p.26). The words 'toxic', 'inhibit' and 'nitrification' do not appear in the procedure.
  - Gap: effluent ammonia 5 mg/L says only 'Escalate' (p.25); the route is SOP-GEN-004 (pp.9–10).
- **Expected fix / recovery:** Restore approved bowl speed and polymer dose, hold centrate and bleed back over 24 h, inlet ammonia below 42 mg/L.

**Readings (normal → incident → after the fix):**

| Tag | Reading | Normal | Incident | After fix | Limits (alarm raised) | Deviation at incident |
|---|---|---|---|---|---|---|
| SLD-701-BOWL-RPM | Centrifuge bowl speed (DewateringCentrifuge-01) | 3,200 rpm | 2,400 rpm | 3,200 rpm | 2,900 rpm (low alarm) → ALM-SLD-701-BOWL-LO | **500 rpm below the 2,900 rpm (low alarm), 17% past it** |
| SLD-701-POLYMER | Polymer dose (DewateringCentrifuge-01) | 8–12 kg/t DS | 4 kg/t DS | 10 kg/t DS | 6 kg/t DS (low) → ALM-SLD-701-POLY-LO | **2 kg/t DS below the 6 kg/t DS (low), 33% past it** |
| SLD-701-CAPTURE | Solids capture (DewateringCentrifuge-01) | 92–97 % | 74 % | 94 % | 88 % → ALM-SLD-701-CAPTURE | **14 % below the 88 %, 16% past it** |
| SLD-701-CAKE-DRY | Cake dryness (DewateringCentrifuge-01) | 22–26 % DS | 16 % DS | 24 % DS | 20 % DS → ALM-SLD-701-CAKE-LO | **4 % DS below the 20 % DS, 20% past it** |
| SLD-701-CENTRATE-NH4 | Centrate ammonia (NH₄-N) (DewateringCentrifuge-01) | 800–1,100 mg/L | 1,650 mg/L | 950 mg/L (bled slowly) | 1,400 mg/L → ALM-SLD-701-NH4-HI | **250 mg/L above the 1,400 mg/L, 18% past it** |
| INL-110-NH4 | Plant inlet ammonia (InletWorks-01) | 35–42 mg/L | 56 mg/L (+40 %) | 41 mg/L | 50 mg/L → ALM-INL-110-NH4-HI | **6 mg/L above the 50 mg/L, 12% past it** |
| AER-201-AIRFLOW | Blower airflow (AerationBasin-01) | 2,800–3,400 Nm³/h | 4,050 Nm³/h | 3,300 Nm³/h | 4,200 Nm³/h (max) → ALM-AER-201-AIR-HI | 650 Nm³/h above normal (max 3,400), 19% over |
| EFF-301-NH4 | Effluent ammonia (Effluent-01) | 0.5–2 mg/L | 3.8 mg/L, rising | 1.2 mg/L | 5 mg/L (permit) → ALM-EFF-301-NH4-HI | 1.8 mg/L above normal (max 2), 90% over |

**Alarms raised in this run:** ALM-SLD-701-BOWL-LO, ALM-SLD-701-POLY-LO, ALM-SLD-701-CAPTURE, ALM-SLD-701-CAKE-LO, ALM-SLD-701-NH4-HI, ALM-INL-110-NH4-HI

## 7. Low dissolved oxygen due to aeration blower failure

- **Scenario ID:** `aeration_blower_failure`
- **Story:** Farah, day operator, 2:35 PM. Blower B-1 overloads and trips. The remaining blower cannot carry the load, airflow and header pressure fall, and dissolved oxygen drops in the aeration tanks.
- **Equipment / area:** AerationBlower-01 — Aeration blowers (B-1/B-2/B-3) (Biological treatment)
- **Primary alarm:** ALM-AER-201-DO — Dissolved oxygen low
- **Root cause (scripted, never shown to the AI):** Aeration blowers: Blower B-1 motor overload trip; standby blower B-3 did not start
- **Knock-on effects:** Aeration tanks — Dissolved oxygen falls and biological treatment slows; Effluent — Effluent ammonia starts to rise
- **Severity / priority:** High. Demonstration priority: escalate if DO stays below 1.0 mg/L or effluent ammonia nears 5 mg/L.
- **SOP:** SOP-AER-001 Aeration Blower Failure Response (coverage: full)
  - Covered by draft SOP-AER-001: blower current, airflow, header pressure, dissolved oxygen, standby start checks and recovery verification. The draft is not approved and its limits are demonstration values.
  - Gap: the draft gives no site-specific blower restart sequence; it defers to the manufacturer and an authorized engineer.
- **Expected fix / recovery:** Confirm B-1 trip cause, bring standby capacity on under the site procedure, DO back to 1.5–3.0 mg/L.

**Readings (normal → incident → after the fix):**

| Tag | Reading | Normal | Incident | After fix | Limits (alarm raised) | Deviation at incident |
|---|---|---|---|---|---|---|
| BLW-201-CUR | Blower B-1 motor current (AerationBlower-01) | 58–66 A | 80 A, then 0 A (tripped) | 62 A | 78 A (overload trip) | **2 A above the 78 A (overload trip), 3% past it** |
| AER-201-AIRFLOW | Blower airflow (AerationBasin-01) | 2,800–3,400 Nm³/h | 1,600 Nm³/h | 3,100 Nm³/h | 2,000 Nm³/h (low alarm) → ALM-AER-201-AIR-LO | **400 Nm³/h below the 2,000 Nm³/h (low alarm), 20% past it** |
| BLW-201-PRS | Air header pressure (AerationBlower-01) | 480–560 mbar | 350 mbar | 520 mbar | 380 mbar (low alarm) → ALM-BLW-201-PRS-LO | **30 mbar below the 380 mbar (low alarm), 8% past it** |
| AIT-201-DO | Dissolved oxygen (AerationBasin-01) | 1.5–3.0 mg/L | 0.6 mg/L | 2.1 mg/L | < 1.0 mg/L → ALM-AER-201-DO | **0.4 mg/L below the < 1.0 mg/L, 40% past it** |
| AER-201-TEMP | Aeration basin temperature (AerationBasin-01) | 20–28 °C | 24.6 °C (unchanged) | 24.5 °C | — | within normal (unchanged) |
| EFF-301-NH4 | Effluent ammonia (Effluent-01) | 0.5–2 mg/L | 3.4 mg/L, rising | 1.3 mg/L | 5 mg/L (permit) → ALM-EFF-301-NH4-HI | 1.4 mg/L above normal (max 2), 70% over |

**Status and trip signals:**

| Tag | Signal | Normal → incident | Kind | What happens |
|---|---|---|---|---|
| BLW-201-FAULT | Blower B-1 motor overload trip | False → True | alarm input (fault) | Blower B-1 tripped on motor overload (when trip `blower_overload` latches) |
| BLW-201-RUN | Blower B-1 running | True → False | status (evidence) | Blower B-1 stopped by its trip (when trip `blower_overload` latches) |
| BLW-202-RUN | Blower B-2 running | True → True | status (evidence) | Blower B-2 keeps running alone (unchanged) |
| BLW-203-RUN | Standby blower B-3 running | False → False | status (evidence) | Standby blower B-3 stayed off (unchanged) |

**Trips:** `blower_overload` when BLW-201-CUR goes above 78

**Alarms raised in this run:** ALM-AER-201-AIR-LO, ALM-BLW-201-PRS-LO, ALM-AER-201-DO, BLW-201-FAULT

## 8. UV disinfection intensity low (no SOP)

- **Scenario ID:** `uv_disinfection_low`
- **Story:** Ravi, night operator, 1:20 AM. A ballast fault switches off UV lamp bank 2. UV intensity and UV dose fall while the flow through the channel stays normal. The SOP manual has no procedure for UV disinfection.
- **Equipment / area:** UVChannel-01 — UV disinfection channel (lamp banks 1/2) (Disinfection)
- **Primary alarm:** ALM-UV-901-INT-LO — UV intensity low
- **Root cause (scripted, never shown to the AI):** UV disinfection: Lamp bank 2 ballast fault; bank 2 lamps off
- **Knock-on effects:** Effluent — UV dose below the demonstration minimum; effluent bacteria are not measured online
- **Severity / priority:** High. Demonstration priority: disinfection below target can breach the discharge permit; escalate to the shift supervisor.
- **SOP:** none in the manual
  - No SOP in the supplied manual covers UV disinfection. SOP-MAN-000 names disinfection only in its plant overview and lists residual disinfectant in the sampling schedule (SOP-LAB-001).
- **Expected fix / recovery:** No SOP: OpsLens says so, shows the evidence and AI suggestions labelled as not from an SOP, and advises escalation.

**Readings (normal → incident → after the fix):**

| Tag | Reading | Normal | Incident | After fix | Limits (alarm raised) | Deviation at incident |
|---|---|---|---|---|---|---|
| UV-901-INT | UV intensity (UVChannel-01) | 8–12 mW/cm² | 3.5 mW/cm² | 10 mW/cm² | 5 mW/cm² (low alarm, demonstration value) → ALM-UV-901-INT-LO | **1.5 mW/cm² below the 5 mW/cm² (low alarm, demonstration value), 30% past it** |
| UV-901-DOSE | UV dose (UVChannel-01) | 35–50 mJ/cm² | 15 mJ/cm² | 42 mJ/cm² | 25 mJ/cm² (low alarm, demonstration value) → ALM-UV-901-DOSE-LO | **10 mJ/cm² below the 25 mJ/cm² (low alarm, demonstration value), 40% past it** |
| UV-901-UVT | UV transmittance (UVChannel-01) | 60–70 % | 65 % (unchanged) | 65 % | — | within normal (unchanged) |
| UV-901-FLW | Flow through UV channel (UVChannel-01) | 900–1,200 m³/h | 1,050 m³/h (unchanged) | 1,040 m³/h | — | within normal (unchanged) |

**Status and trip signals:**

| Tag | Signal | Normal → incident | Kind | What happens |
|---|---|---|---|---|
| UV-901-B2-FAULT | Lamp bank 2 ballast fault | False → True | alarm input (fault) | Lamp bank 2 reported a ballast fault (at 30 s) |
| UV-901-B2-RUN | Lamp bank 2 on | True → False | status (evidence) | Lamp bank 2 switched off (at 30.5 s) |
| UV-901-B1-RUN | Lamp bank 1 on | True → True | status (evidence) | Lamp bank 1 stays on (unchanged) |

**Alarms raised in this run:** ALM-UV-901-INT-LO, ALM-UV-901-DOSE-LO, UV-901-B2-FAULT

## Scenario 8 against the plant setup (checked 10 Nov 2026)

| Check | Result |
|---|---|
| The plant has a disinfection step | **Yes.** SOP-MAN-000 §2 lists the liquid line as "… secondary clarifiers, tertiary filtration, disinfection, effluent discharge or reuse" (p.2). UV sits after tertiary filtration, before discharge. |
| No SOP for it | **Correct.** The manual names disinfection only in the plant overview and the sampling schedule. It has no disinfection procedure, so "no SOP" is true to the manual. |
| Flow through the UV channel | **Matches.** 900–1,200 m³/h is the plant's inlet flow (inlet works, scenario 1). All treated flow passes the one UV channel. |
| UV transmittance 60–70 % | **Realistic** for filtered secondary effluent. Unchanged in the incident, which correctly rules out poor water quality. |
| Intensity 8–12 → 3.5 mW/cm² with one of two banks off | **Plausible.** A drop of 65 % (more than half) fits an intensity sensor placed nearer bank 2. |
| Dose vs intensity | **Fixed.** Dose = intensity × exposure time; with flow unchanged, exposure stays about 4.2 s, so 3.5 mW/cm² gives about 15 mJ/cm². The catalog said 18 mJ/cm² (implying 5.1 s); it now says 15 mJ/cm². |
| Limits (5 mW/cm², 25 mJ/cm²) | Demonstration values, labelled as such, because the manual has none. 25 mJ/cm² is below the usual 30–40 mJ/cm² design dose for discharge. |
| Knowledge graph | **Fixed.** UVChannel-01 had no plant area ("Other"), so its incident path skipped the area step. It is now under "Disinfection". |
| **Residual disinfectant in the sampling schedule** | **Mismatch to decide.** SOP-MAN-000 samples final effluent for "residual disinfectant" daily (p.27). UV leaves no residual, which suggests chlorine. To keep UV, describe the plant as: UV as the main disinfection, plus a small hypochlorite dose after UV on the reuse line, which is what the residual sample measures. Or treat the line as a template value that does not apply to a UV plant. |
| Tertiary filter equipment | Not modelled. The manual lists `TertiaryTreatment-01`, but no scenario uses it, so it has no tags. Optional. |
