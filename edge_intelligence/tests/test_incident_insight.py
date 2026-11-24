"""Bottom line, confidence rating, history and pre-alarm correlation, through the API."""
import asyncio
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

import ai_diagnosis
from alarm_lifecycle import get_lifecycle_manager
from database import Database


class Insight(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        get_lifecycle_manager().clear_all()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "none", "OPSLENS_RECOVERY_HOLD_SECONDS": "0"})
        self.env.start()
        from api.routes import create_app
        from orchestrator import OpsLensOrchestrator
        self.db = Database(db_path=Path(self.tmp.name) / "i.db")
        self.orch = OpsLensOrchestrator(db=self.db)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(orchestrator=self.orch)), base_url="http://t", timeout=60)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.orch.stop()
        self.env.stop()
        get_lifecycle_manager().clear_all()
        self.tmp.cleanup()

    async def run_scenario(self, scenario_id):
        await self.client.post("/api/scenarios/run", json={"scenario_id": scenario_id, "speed_multiplier": 2000})
        for _ in range(800):
            status = (await self.client.get("/api/scenarios/status")).json()
            if status["status"] not in ("running", "recovering"):
                return status
            await asyncio.sleep(0.02)

    async def insight(self, event_id):
        response = await self.client.get(f"/api/events/{event_id}/insight")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_first_incident_bottom_line_and_confidence(self):
        status = await self.run_scenario("wet_well_high_level")
        data = await self.insight(status["event_id"])
        bluf = data["bluf"]
        self.assertIn("P-101", bluf["likely_cause"])
        self.assertEqual(len(bluf["top_steps"]), 2)
        self.assertTrue(all(s["page"] for s in bluf["top_steps"]))
        self.assertIn("SOP-LFT-001", bluf["steps_source"])
        self.assertIn("No earlier incident", bluf["history"])
        conf = data["confidence"]
        self.assertIn(conf["level"], ("High", "Medium", "Low"))
        self.assertIn("not a probability", conf["note"])
        flags = {f["flag"] for f in conf["flags"]}
        self.assertIn("unprecedented", flags)
        self.assertIn("several_causes", flags)
        self.assertIn("draft_sop", flags)

    async def test_history_shows_earlier_incident_with_resolution_notes(self):
        status = await self.run_scenario("wet_well_high_level")
        event = self.orch.get_full_event(status["event_id"])
        trigger = datetime.fromisoformat(event["trigger_timestamp"].replace("Z", "+00:00"))
        conn = self.db._get_connection()
        equipment = conn.execute("SELECT equipment_id FROM correlated_events WHERE event_id = ?", (status["event_id"],)).fetchone()[0]
        earlier = str(uuid.uuid4())
        when = trigger - timedelta(hours=3)
        conn.execute("INSERT INTO correlated_events (event_id, primary_tag, primary_alarm, trigger_timestamp, raw_deviation_pct,"
                     " clustered_tags, correlated_deviations, equipment_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (earlier, event["primary_tag"], "x", when.isoformat(), 0, "[]", "{}", equipment, when.isoformat()))
        conn.execute("INSERT INTO operator_actions (action_id, event_id, operator, action_type, timestamp, notes, created_at) VALUES (?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), earlier, "A. Tester", "RESOLVE", (when + timedelta(minutes=42)).isoformat(),
                      "Rag ball cleared from P-102", when.isoformat()))
        conn.commit()
        data = await self.insight(status["event_id"])
        self.assertEqual(data["history"]["same_alarm_total"], 1)
        self.assertEqual(data["history"]["same_alarm_last_24h"], 1)
        self.assertIn("resolved in 42.0 min", data["bluf"]["history"])
        self.assertIn("Rag ball cleared from P-102", data["bluf"]["history"])
        self.assertNotIn("unprecedented", {f["flag"] for f in data["confidence"]["flags"]})
        # The AI gets the history too, so "has this happened today?" can be answered.
        facts = ai_diagnosis.build_facts(event, self.db)
        self.assertIn("Rag ball cleared from P-102", ai_diagnosis.build_prompt(facts))

    async def test_operator_action_before_the_alarm_is_correlated(self):
        status = await self.run_scenario("wet_well_high_level")
        event = self.orch.get_full_event(status["event_id"])
        trigger = datetime.fromisoformat(event["trigger_timestamp"].replace("Z", "+00:00"))
        other = str(uuid.uuid4())
        conn = self.db._get_connection()
        equipment = conn.execute("SELECT equipment_id FROM correlated_events WHERE event_id = ?", (status["event_id"],)).fetchone()[0]
        conn.execute("INSERT INTO correlated_events (event_id, primary_tag, primary_alarm, trigger_timestamp, raw_deviation_pct,"
                     " clustered_tags, correlated_deviations, equipment_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (other, "P101-FAULT", "x", (trigger - timedelta(minutes=20)).isoformat(), 0, "[]", "{}", equipment, trigger.isoformat()))
        conn.execute("INSERT INTO operator_actions (action_id, event_id, operator, action_type, timestamp, notes, created_at) VALUES (?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), other, "B. Night", "ANNOTATE", (trigger - timedelta(minutes=6)).strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
                      "Closed P-102 discharge valve for maintenance", trigger.isoformat()))
        conn.commit()
        data = await self.insight(status["event_id"])
        self.assertTrue(any("discharge valve" in p["text"] and p["minutes_before_alarm"] == 6.0 for p in data["pre_alarm"]))
        facts = ai_diagnosis.build_facts(event, self.db)
        action = [c for c in facts["cause_candidates"] if c["kind"] == "action"]
        self.assertTrue(action and "Timing alone does not prove" in action[0]["contradicting"][0])
        self.assertIn("6.0 min before", ai_diagnosis.build_prompt(facts))


if __name__ == "__main__":
    unittest.main()
