"""Validation: does the backend's cause ranking follow the evidence?

Each test injects conditions through a variant of the lift-station scenario
(the same simulator, alarm logic, incident grouping and cause ranking that run
in the demo), then records what the backend concluded and compares it with a
known reference. Nothing is forced: the variants only change telemetry
(values, timing, which readings move); the assertions read the backend result.

  A  genuine P-101 overload            -> P-101 trip leads; no clog claimed
  B  one misleading P-102 flow reading -> flow alarm detected; no clog declared
  B2 the same, during a real high level -> inflow leads; the clog is
                                          "insufficient evidence"
  C  P-101 overload and P-102 clog     -> both found; ranked by their evidence
  D1 C, but the trip comes after the alarm -> the clog leads; the trip cannot
                                          have started the alarm
  D2 C, but P-102 pressure and current stay normal -> the clog is
                                          "insufficient evidence"

Also: all eight Scenario Lab incidents against their test reference, and a
code check that no analysis module reads the reference.

Set OPSLENS_VALIDATION_REPORT=path.json to write the records for the report.
"""
import asyncio
import dataclasses
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import incident_catalog as catalog
import scenarios as scenario_registry
from alarm_lifecycle import get_lifecycle_manager
from database import Database
from incident_views import build_inspection, reference_match
from scenarios.incident_scenarios import factory

HERE = Path(__file__).resolve().parents[1]
RECORDS = []
BASE = catalog.SCENARIO_BY_ID["wet_well_high_level"]
NORMAL = {"FIT-101-IN": 415, "FIT-102-FLW": 415, "PIT-102-DIS": 3.1, "MOT-102-CUR": 31, "TIT-101-WDG": 65,
          "MOT-101-CUR": 31, "LIT-101-WWL": 1.6}


def variant(sid, title, changes, primary_alarm=None):
    """The lift scenario with some readings changed: {tag: {field: value}}.
    A reading set to 'normal' ramps to its normal midpoint and never alarms."""
    signals = []
    for sig in BASE.signals:
        change = changes.get(sig.tag)
        if change == "normal":
            sig = dataclasses.replace(sig, incident=NORMAL[sig.tag], incident_text=f"{NORMAL[sig.tag]} {sig.unit} (normal)",
                                      status="n", peak_then_zero_on=None, ramp=(5, 35))
        elif change:
            sig = dataclasses.replace(sig, **change)
        signals.append(sig)
    return dataclasses.replace(BASE, scenario_id=sid, name=title, signals=tuple(signals),
                               primary_alarm=primary_alarm or BASE.primary_alarm)


class ValidationRun(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        get_lifecycle_manager().clear_all()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "none", "OPSLENS_SEED_HISTORY": "false"})
        self.env.start()
        from api.routes import create_app
        from orchestrator import OpsLensOrchestrator
        self.db = Database(db_path=Path(self.tmp.name) / "v.db")
        self.orch = OpsLensOrchestrator(db=self.db)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(orchestrator=self.orch)), base_url="http://t", timeout=60)
        self.registered = []

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.orch.stop()
        for sid in self.registered:
            catalog.SCENARIO_BY_ID.pop(sid, None)
            scenario_registry.SCENARIO_REGISTRY.pop(sid, None)
            if sid in scenario_registry.PRIMARY_SCENARIO_IDS:
                scenario_registry.PRIMARY_SCENARIO_IDS.remove(sid)
        self.env.stop()
        get_lifecycle_manager().clear_all()
        self.tmp.cleanup()

    def register(self, spec):
        catalog.SCENARIO_BY_ID[spec.scenario_id] = spec
        scenario_registry.SCENARIO_REGISTRY[spec.scenario_id] = factory(spec.scenario_id)
        scenario_registry.PRIMARY_SCENARIO_IDS.append(spec.scenario_id)
        self.registered.append(spec.scenario_id)

    async def run_case(self, spec, test, injected, expected):
        """Run one injected scenario through the real pipeline and record the result."""
        if spec.scenario_id not in catalog.SCENARIO_BY_ID or catalog.SCENARIO_BY_ID[spec.scenario_id] is not spec:
            self.register(spec)
        r = await self.client.post("/api/scenarios/run", json={"scenario_id": spec.scenario_id, "speed_multiplier": 2000})
        self.assertEqual(r.status_code, 200, r.text)
        for _ in range(800):
            status = (await self.client.get("/api/scenarios/status")).json()
            if status["status"] not in ("running", "recovering"):
                break
            await asyncio.sleep(0.02)
        self.assertTrue(status.get("event_id"), status)
        insp = await asyncio.to_thread(build_inspection, self.orch.get_full_event(status["event_id"]), self.db)
        cands = insp["candidates"]
        record = {
            "test": test, "injected": injected, "expected": expected,
            "telemetry": [f"{r['label']}: {r['first']} -> worst {r['worst']} {r.get('unit') or ''}".strip()
                          for r in insp["readings"] if r["kind"] == "measurement"],
            "alarms": [f"{a['first_on_hms']} {a['tag']}" for a in insp["alarms"] if a.get("first_on_hms")],
            "primary_alarm": (insp["primary_alarm"] or {}).get("tag"),
            "leading": next((c.get("short") or c["statement"] for c in cands if c.get("role") == "leading"), None),
            "contributing": [c.get("short") or c["statement"] for c in cands if c.get("role") == "contributing"],
            "insufficient": [c.get("short") or c["statement"] for c in cands if c.get("role") == "insufficient"],
            "ranking": [f"{c['rank']}. {c.get('short') or c['statement']} — {c.get('role_label')}"
                        + (f", {c['evidence_points']} pts" if c.get("evidence_points") is not None else "") for c in cands],
            "comparison": (insp.get("comparison") or {}).get("why"),
        }
        RECORDS.append(record)
        return record, insp

    def finish(self, record, ok_checks):
        """Assert every check; note any discrepancy in the record."""
        failed = [name for name, ok in ok_checks if not ok]
        record["discrepancy"] = "; ".join(failed) or "none"
        record["status"] = "PASS" if not failed else "FAIL"
        self.assertFalse(failed, json.dumps(record, indent=1))

    # --- A ---------------------------------------------------------------
    async def test_a_genuine_p101_overload(self):
        spec = variant("val_a_p101_overload", "Validation A: genuine P-101 overload",
                       {"FIT-101-IN": "normal", "FIT-102-FLW": "normal", "PIT-102-DIS": "normal", "MOT-102-CUR": "normal"})
        rec, _ = await self.run_case(spec, "A · Genuine fault",
                                "P-101 winding temperature to 118 °C (trip at 105 °C), P-101 trips, motor current to 0 A, "
                                "wet well rises to 3.85 m. P-102 and inflow normal.",
                                "Leading: P-101 thermal overload trip. No clog.")
        self.finish(rec, [
            ("P-101 trip leads", "P-101" in (rec["leading"] or "")),
            ("no clog candidate", not any("clog" in x.lower() for x in rec["ranking"])),
        ])

    # --- B ---------------------------------------------------------------
    async def test_b_misleading_p102_flow(self):
        spec = variant("val_b_misleading_flow", "Validation B: one misleading P-102 flow reading",
                       {"FIT-101-IN": "normal", "PIT-102-DIS": "normal", "MOT-102-CUR": "normal",
                        "TIT-101-WDG": "normal", "MOT-101-CUR": "normal", "LIT-101-WWL": "normal"},
                       primary_alarm="ALM-P102-FLW-LO")
        rec, insp = await self.run_case(spec, "B · Misleading sensor reading",
                                   "Only P-102 flow falls to 90 m³/h. P-102 discharge pressure, motor current and running "
                                   "status, P-101, inflow and wet well level stay normal.",
                                   "The low-flow anomaly is detected (alarm and breach). No clog declared as the cause.")
        breach_tags = {b["tag"] for b in insp["breaches"]}
        self.finish(rec, [
            ("flow alarm detected", any("ALM-P102-FLW-LO" in a for a in rec["alarms"])),
            ("flow breach detected", "FIT-102-FLW" in breach_tags),
            ("no clog leads", "clog" not in (rec["leading"] or "").lower()),
            ("flagged as a possible instrument fault, not a cause", any("on its own" in x for x in rec["insufficient"])),
        ])

    async def test_b2_misleading_flow_during_real_high_level(self):
        spec = variant("val_b2_misleading_flow_storm", "Validation B2: misleading P-102 flow during a storm inflow",
                       {"FIT-101-IN": {"incident": 900, "incident_text": "900 m³/h (storm)", "ramp": (5, 35)},
                        "PIT-102-DIS": "normal", "MOT-102-CUR": "normal", "TIT-101-WDG": "normal", "MOT-101-CUR": "normal"})
        rec, _ = await self.run_case(spec, "B2 · Misleading sensor during a real event",
                                "Storm inflow 900 m³/h raises the wet well to 3.85 m. P-102 flow reads 90 m³/h, but P-102 "
                                "discharge pressure and motor current stay normal. P-101 runs normally.",
                                "Leading: inflow high. The P-102 clog is marked insufficient evidence, not a cause.")
        self.finish(rec, [
            ("inflow leads", "inflow" in (rec["leading"] or "").lower()),
            ("clog is insufficient evidence", any("clog" in x.lower() for x in rec["insufficient"])),
            ("clog is not leading or contributing", not any("clog" in x.lower() for x in [rec["leading"] or "", *rec["contributing"]])),
        ])

    # --- C ---------------------------------------------------------------
    async def test_c_two_competing_faults(self):
        spec = variant("val_c_two_faults", "Validation C: P-101 overload and P-102 clog",
                       {"FIT-101-IN": "normal",
                        "FIT-102-FLW": {"ramp": (5, 30)}, "PIT-102-DIS": {"ramp": (5, 30)}, "MOT-102-CUR": {"ramp": (5, 30)},
                        "TIT-101-WDG": {"ramp": (35, 70)}})
        rec, insp = await self.run_case(spec, "C · Two competing faults",
                                   "P-102 clog first (flow 90 m³/h, pressure 1.1 bar, current 19 A from 5 s), then P-101 "
                                   "overload (winding 118 °C from 35 s, trips at 105 °C). Wet well to 3.85 m. Inflow normal.",
                                   "Both found. Leading: P-101 trip (recorded trip plus the same readings). Contributing: "
                                   "clog on P-102. The ranking says why.")
        self.finish(rec, [
            ("P-101 trip leads", "P-101" in (rec["leading"] or "")),
            ("clog contributes", any("clog" in x.lower() for x in rec["contributing"])),
            ("comparison names the recorded trip", "recorded trip" in (rec["comparison"] or "")),
        ])

    # --- D ---------------------------------------------------------------
    async def test_d1_trip_after_the_alarm(self):
        spec = variant("val_d1_late_trip", "Validation D1: P-101 trips after the wet well alarm",
                       {"FIT-101-IN": "normal",
                        "FIT-102-FLW": {"ramp": (5, 30)}, "PIT-102-DIS": {"ramp": (5, 30)}, "MOT-102-CUR": {"ramp": (5, 30)},
                        "LIT-101-WWL": {"ramp": (10, 55)}, "TIT-101-WDG": {"ramp": (70, 110)}})
        rec, _ = await self.run_case(spec, "D1 · Changed timing",
                                "As C, but the wet well reaches high-high at about 50 s and P-101 only overheats from 70 s, "
                                "tripping after the alarm.",
                                "Leading: clog on P-102. The P-101 trip came after the alarm, so it cannot have started it.")
        self.finish(rec, [
            ("clog leads", "clog" in (rec["leading"] or "").lower()),
            ("P-101 trip does not lead", "P-101" not in (rec["leading"] or "")),
        ])

    async def test_d2_weaker_clog_evidence(self):
        spec = variant("val_d2_weak_clog", "Validation D2: P-102 flow low but pressure and current normal",
                       {"FIT-101-IN": "normal", "PIT-102-DIS": "normal", "MOT-102-CUR": "normal",
                        "FIT-102-FLW": {"ramp": (5, 30)}, "TIT-101-WDG": {"ramp": (35, 70)}})
        rec, _ = await self.run_case(spec, "D2 · Changed supporting readings",
                                "As C, but P-102 discharge pressure and motor current stay normal: only its flow reading is low.",
                                "Leading: P-101 trip. The clog drops to insufficient evidence.")
        self.finish(rec, [
            ("P-101 trip leads", "P-101" in (rec["leading"] or "")),
            ("clog is insufficient evidence", any("clog" in x.lower() for x in rec["insufficient"])),
        ])

    # --- the eight Scenario Lab incidents -----------------------------------
    async def test_eight_scenarios_against_their_reference(self):
        for spec in catalog.SCENARIOS:
            with self.subTest(scenario=spec.scenario_id):
                ref = catalog.REFERENCE_OUTCOME[spec.scenario_id]
                rec, insp = await self.run_case(spec, f"Scenario {spec.number} · {spec.name}",
                                           "; ".join(insp_line for insp_line in
                                                     [f"{s.label}: {s.normal_text} -> {s.incident_text}" for s in spec.signals if s.status == "c"][:4]),
                                           f"Primary: {ref['primary']}. Secondary: {'; '.join(ref['secondary'])}.")
                rec["reference_primary"], rec["reference_secondary"] = ref["primary"], ref["secondary"]
                match = reference_match(rec["leading"], ref["primary"])
                rec["kind"] = "scenario"
                self.finish(rec, [("leading matches the reference primary", bool(match))])
                await self.client.post("/api/scenarios/corrective-action", json={"scenario_id": spec.scenario_id})
                for _ in range(800):
                    status = (await self.client.get("/api/scenarios/status")).json()
                    if status["status"] not in ("running", "recovering"):
                        break
                    await asyncio.sleep(0.02)


class ReferenceIsNotReadByTheAnalysis(unittest.TestCase):
    def test_only_the_inspection_view_and_tests_read_the_reference(self):
        readers = sorted(str(p.relative_to(HERE)) for p in HERE.rglob("*.py")
                         if "REFERENCE_OUTCOME" in p.read_text(errors="ignore") and "tests" not in p.parts)
        self.assertEqual(readers, ["incident_catalog.py", "incident_views.py"])
        # And the inspection view uses it only inside the separate test-reference block.
        views = (HERE / "incident_views.py").read_text()
        self.assertEqual(len(re.findall(r"REFERENCE_OUTCOME", views)), 2)


def tearDownModule():
    path = os.environ.get("OPSLENS_VALIDATION_REPORT")
    if path and RECORDS:
        Path(path).write_text(json.dumps(RECORDS, indent=1, default=str))


if __name__ == "__main__":
    unittest.main()
