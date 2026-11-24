"""Behaviour added in v2.2: one incident cannot clear another, any unfixed
incident can be fixed, live alarms rank first, the symptom is never ranked as
the contributor, the shift window compares instants, and Scenario 7 works."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

import ai_diagnosis
import alarm_config
import incident_catalog as catalog
from alarm_lifecycle import get_lifecycle_manager
from alarm_urgency import rank_incidents
from database import Database, _timestamp_in_window
from evidence_engine import EvidenceEngine
from models import CandidateEvent, CorrelatedDeviation, CorrelatedEvent


async def _wait(client):
    for _ in range(600):
        status = (await client.get("/api/scenarios/status")).json()
        if status["status"] not in ("running", "recovering"):
            return status
        await asyncio.sleep(0.02)
    raise AssertionError("scenario did not finish")


class PlantApiBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        get_lifecycle_manager().clear_all()
        ai_diagnosis.CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "none"})
        self.env.start()
        from api.routes import create_app
        from orchestrator import OpsLensOrchestrator
        self.db = Database(db_path=Path(self.tmp.name) / "p.db")
        self.orch = OpsLensOrchestrator(db=self.db)
        self.app = create_app(orchestrator=self.orch)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t", timeout=60)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.orch.stop()
        self.env.stop()
        get_lifecycle_manager().clear_all()
        self.tmp.cleanup()

    async def run_scenario(self, scenario_id):
        response = await self.client.post("/api/scenarios/run", json={"scenario_id": scenario_id, "speed_multiplier": 2000})
        self.assertEqual(response.status_code, 200, response.text)
        return await _wait(self.client)

    def latest(self, tag):
        reading = self.db.get_latest_reading(tag)
        return reading.value if reading else None


class IncidentsStayBrokenTests(PlantApiBase):
    async def test_next_scenario_does_not_clear_an_unfixed_incident(self):
        await self.run_scenario("wet_well_high_level")
        self.assertIs(self.latest("ALM-WW-101-HH"), True)
        level = self.latest("LIT-101-WWL")
        await self.run_scenario("centrifuge_capture")
        self.assertIs(self.latest("ALM-WW-101-HH"), True, "another scenario cleared this alarm")
        self.assertEqual(self.latest("LIT-101-WWL"), level)
        active = (await self.client.get("/api/alarms/active")).json()
        tags = {a["tag"] for a in active["alarms"] if a["state"].startswith("ACTIVE")}
        self.assertIn("ALM-WW-101-HH", tags)
        self.assertIn("ALM-SLD-701-CAPTURE", tags)
        # And no duplicate incident appeared for the held alarm.
        primaries = [c.primary_tag for c in self.db.get_candidate_events()]
        self.assertEqual(primaries.count("ALM-WW-101-HH"), 1)

    async def test_any_unfixed_incident_can_be_fixed_and_others_stay(self):
        first = await self.run_scenario("wet_well_high_level")
        second = await self.run_scenario("centrifuge_capture")
        unfixed = {u["scenario_id"] for u in second["unfixed_scenarios"]}
        self.assertEqual(unfixed, {"wet_well_high_level", "centrifuge_capture"})
        fix = await self.client.post("/api/scenarios/corrective-action", json={"event_id": first["event_id"]})
        self.assertEqual(fix.status_code, 200, fix.text)
        self.assertEqual(fix.json()["scenario_id"], "wet_well_high_level")
        self.assertIs(self.latest("ALM-WW-101-HH"), False)
        self.assertIs(self.latest("ALM-SLD-701-CAPTURE"), True)
        status = (await self.client.get("/api/scenarios/status")).json()
        self.assertEqual([u["scenario_id"] for u in status["unfixed_scenarios"]], ["centrifuge_capture"])
        # The recovery of an older incident must not open a second incident.
        self.assertEqual(sorted(c.primary_tag for c in self.db.get_candidate_events()), ["ALM-SLD-701-CAPTURE", "ALM-WW-101-HH"])

    async def test_state_is_rebuilt_after_a_backend_restart(self):
        await self.run_scenario("wet_well_high_level")
        from api.routes import create_app
        restarted = create_app(orchestrator=self.orch)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted), base_url="http://t", timeout=60) as client:
            status = (await client.get("/api/scenarios/status")).json()
            self.assertEqual([u["scenario_id"] for u in status["unfixed_scenarios"]], ["wet_well_high_level"])
            fix = await client.post("/api/scenarios/corrective-action", json={"scenario_id": "wet_well_high_level"})
            self.assertEqual(fix.status_code, 200, fix.text)
        self.assertIs(self.latest("ALM-WW-101-HH"), False)

    async def test_ranking_puts_the_incident_with_live_alarms_first(self):
        first = await self.run_scenario("wet_well_high_level")
        await self.client.post("/api/scenarios/corrective-action", json={"event_id": first["event_id"]})
        second = await self.run_scenario("biological_foam")
        ranked = (await self.client.get("/api/incidents/ranked")).json()["ranked_incidents"]
        self.assertEqual(ranked[0]["event_id"], second["event_id"])
        self.assertTrue(ranked[0]["alarm_inputs_active"])
        cleared = next(r for r in ranked if r["event_id"] == first["event_id"])
        self.assertFalse(cleared["alarm_inputs_active"])
        self.assertIn("returned to normal", cleared["ranking_rationale"])

    async def test_contributor_is_never_the_alarms_own_measurement(self):
        for spec in catalog.SCENARIOS:
            with self.subTest(spec.scenario_id):
                status = await self.run_scenario(spec.scenario_id)
                evidence = self.orch.get_full_event(status["event_id"])["evidence"]
                source = catalog.alarm_inputs()[spec.primary_alarm]["source_tag"]
                self.assertNotEqual(evidence["likely_contributor_tag"], source)
                self.assertNotIn(evidence["likely_contributor_tag"], [
                    t for t, a in catalog.alarm_inputs().items() if a["threshold"] is not None])

    async def test_handover_records_this_shifts_events_in_a_local_timezone(self):
        status = await self.run_scenario("wet_well_high_level")
        await self.client.post("/api/actions", json={"event_id": status["event_id"], "operator": "op", "action_type": "INSPECT", "notes": "Walked the wet well."})
        from datetime import datetime, timedelta, timezone
        ist = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(ist)
        start, end = (now - timedelta(hours=1)).isoformat(), (now + timedelta(hours=1)).isoformat()
        self.assertIn(status["event_id"], self.db.get_events_in_window(start, end))
        self.assertEqual(len(self.db.get_actions_in_window(start, end)), 1)
        with patch.dict(os.environ, {"OPSLENS_SHIFTS": "", "OPSLENS_SHIFT_NAME": "Night", "OPSLENS_SHIFT_START": "00:00", "OPSLENS_SHIFT_END": "23:59"}):
            summary = (await self.client.get("/api/handover/current")).json()
        record = next(r for r in summary["incident_handover"] if r["incident_id"] == status["event_id"])
        self.assertEqual(record["alarm"], "ALM-WW-101-HH")
        self.assertEqual([a["action_type"] for a in record["actions_taken"]], ["INSPECT"])
        self.assertEqual(record["sop"]["sop_id"], "SOP-LFT-001")
        self.assertIn("NOT APPROVED", record["sop"]["approval"])
        self.assertTrue(record["important_trends"])
        self.assertIn("Alarm still active", record["next_shift"])
        # Written for the incoming operator: same cause as the Root Cause page, grouped actions, no ids.
        self.assertTrue(record["now"] and record["now"][0].startswith("Wet well level"))
        self.assertIn("P-101 thermal overload trip", record["likely_cause"]["text"])
        self.assertEqual(len(record["done"]), 1)
        self.assertIn("inspected", record["done"][0])
        self.assertTrue(record["escalate_if"])
        self.assertEqual(record["number"], 1)
        self.assertNotIn(status["event_id"], summary["shift_narrative"]["text"] if isinstance(summary["shift_narrative"], dict) else summary["shift_narrative"])


class RecoveryReadingsTests(PlantApiBase):
    async def test_recovery_excursion_never_opens_an_incident_even_via_the_stream(self):
        from models import TagReading
        reading = TagReading(tag="LIT-101-WWL", value=3.8, timestamp="2026-11-04T10:00:00Z", unit="m", equipment_id="LiftStation-01",
                             data_source="simulated", connection_status="simulation_recovery")
        self.assertIsNone(await self.orch.process_reading(reading))
        self.assertEqual(self.db.get_candidate_events(), [])


class HandoverDoesNotCallTheModelTests(PlantApiBase):
    async def test_polled_handover_never_calls_the_model(self):
        calls = []
        with patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "lmstudio"}), \
                patch("llm_provider.complete_chat", side_effect=lambda *a, **k: calls.append(a) or "x"):
            for _ in range(3):
                self.assertEqual((await self.client.get("/api/handover/current")).status_code, 200)
            self.assertEqual(calls, [])
            await self.client.post("/api/handover/ai-summary")
        self.assertEqual(len(calls), 1)


class ScenarioSevenTests(PlantApiBase):
    async def test_full_blower_incident(self):
        status = await self.run_scenario("aeration_blower_failure")
        self.assertEqual(status["alarm_tag"], "ALM-AER-201-DO")
        self.assertEqual(status["equipment_id"], "AerationBasin-01")
        for tag, value in (("AIT-201-DO", 0.6), ("AER-201-AIRFLOW", 1600), ("BLW-201-PRS", 350), ("BLW-201-CUR", 0.0)):
            self.assertEqual(self.latest(tag), value, tag)
        self.assertIs(self.latest("BLW-201-RUN"), False)
        self.assertIs(self.latest("BLW-201-FAULT"), True)
        self.assertIs(self.latest("ALM-AER-201-AIR-LO"), True)
        self.assertIs(self.latest("ALM-BLW-201-PRS-LO"), True)

        brief = (await self.client.get(f"/api/events/{status['event_id']}/brief")).json()
        self.assertEqual(brief["sop"]["sop_id"], "SOP-AER-001")
        self.assertFalse(brief["sop"]["approved"])
        self.assertIn("NOT APPROVED", brief["sop"]["approval_label"])
        self.assertGreaterEqual(len(brief["safe_inspection_steps"]["steps"]), 5)
        self.assertEqual(brief["safe_inspection_steps"]["steps"][0]["source"], "verbatim")
        self.assertIn("0.6", brief["what_happened"])

        facts = ai_diagnosis.build_facts(self.orch.get_full_event(status["event_id"]), self.db)
        self.assertEqual(facts["sop"]["sop_id"], "SOP-AER-001")
        prompt = ai_diagnosis.build_prompt(facts)
        self.assertIn("AIT-201-DO", prompt)
        for cause in catalog.SCENARIO_BY_ID["aeration_blower_failure"].root.values():
            self.assertNotIn(cause, prompt)

        fix = await self.client.post("/api/scenarios/corrective-action", json={})
        self.assertEqual(fix.status_code, 200, fix.text)
        self.assertEqual(self.latest("AIT-201-DO"), 2.1)
        self.assertEqual(self.latest("AER-201-AIRFLOW"), 3100)
        self.assertIs(self.latest("BLW-201-FAULT"), False)
        self.assertIs(self.latest("ALM-AER-201-DO"), False)
        self.assertIs(self.latest("BLW-201-RUN"), True)

    async def test_sop_is_listed_as_draft_and_never_as_approved(self):
        library = (await self.client.get("/api/sops/library")).json()
        items = library.get("documents") or library.get("sops") or library
        item = next(x for x in items if x["sop_id"] == "SOP-AER-001")
        self.assertFalse(item["approved"])
        self.assertIn("DRAFT FOR REVIEW — NOT APPROVED", item["document_status"])
        doc = (await self.client.get("/api/sops/SOP-AER-001")).json()["data"]
        titles = " ".join(p["text"] for p in doc["content_pages"])
        for section in ("Purpose", "Scope", "Alarm conditions", "Symptoms and observations", "Procedure",
                        "Equipment inspection considerations", "Safety precautions", "Escalation criteria",
                        "Recovery verification", "Related procedures", "References and approval status"):
            self.assertIn(section, titles)

    def test_draft_limits_are_kept_apart_from_the_manual(self):
        raw = json.loads(Path(alarm_config.CONFIG_PATH).read_text(encoding="utf-8"))
        self.assertNotIn("BLW-201-CUR", json.dumps(raw))
        low = alarm_config.limit_context("AER-201-AIRFLOW", 1600)
        self.assertEqual(low["highest_exceeded"]["sop_id"], "SOP-AER-001")
        self.assertIsNone(alarm_config.limit_context("AER-201-AIRFLOW", 3000)["highest_exceeded"])
        self.assertEqual(alarm_config.limit_context("AER-201-AIRFLOW", 4300)["highest_exceeded"]["name"], "MAX")


class UnitTests(unittest.TestCase):
    def test_window_compares_instants_not_text(self):
        self.assertTrue(_timestamp_in_window("2026-11-04T16:57:00Z", "2026-11-04T18:00:00+05:30", "2026-11-05T06:00:00+05:30"))
        self.assertFalse(_timestamp_in_window("2026-11-04T12:00:00Z", "2026-11-04T18:00:00+05:30", "2026-11-05T06:00:00+05:30"))
        self.assertFalse(_timestamp_in_window("not a time", "2026-11-04T18:00:00Z", "2026-11-05T06:00:00Z"))

    def test_rank_without_alarm_records_is_unchanged(self):
        ranked = rank_incidents([{"event_id": "a", "urgency": {"urgency_band": "ROUTINE"}}, {"event_id": "b", "urgency": {"urgency_band": "URGENT"}}])
        self.assertEqual(ranked[0]["event_id"], "b")

    def test_a_fault_input_stays_eligible_as_contributor(self):
        event = CorrelatedEvent(**CandidateEvent(event_id="e", primary_tag="ALM-WW-101-HH", primary_alarm=True,
                                                 trigger_timestamp="2026-11-04T10:00:00Z", raw_deviation_pct=100,
                                                 clustered_tags=[]).model_dump(),
                                correlated_deviations=[
                                    CorrelatedDeviation(tag="LIT-101-WWL", deviation_pct=120, timestamp="2026-11-04T09:59:00Z", precedes_primary=True, topology_distance=1),
                                    CorrelatedDeviation(tag="ALM-P102-FLW-LO", deviation_pct=100, timestamp="2026-11-04T09:59:00Z", precedes_primary=True, topology_distance=2),
                                    CorrelatedDeviation(tag="P101-FAULT", deviation_pct=100, timestamp="2026-11-04T09:59:00Z", precedes_primary=True, topology_distance=2)])
        self.assertEqual(EvidenceEngine(db=None).evaluate_event(event, persist=False).likely_contributor_tag, "P101-FAULT")
