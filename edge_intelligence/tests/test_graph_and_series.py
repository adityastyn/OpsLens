"""Knowledge graph built from the incident record, its relationships reaching
the AI, and long chart ranges keeping the newest readings."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

import ai_diagnosis
from alarm_lifecycle import get_lifecycle_manager
from database import Database
from knowledge_graph import IndustrialKnowledgeGraph
from models import TagReading


class GraphAndSeries(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        get_lifecycle_manager().clear_all()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "none"})
        self.env.start()
        from api.routes import create_app
        from orchestrator import OpsLensOrchestrator
        self.db = Database(db_path=Path(self.tmp.name) / "g.db")
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

    async def test_lift_station_graph_has_every_cause_fixes_and_sensor_states(self):
        status = await self.run_scenario("wet_well_high_level")
        graph = (await self.client.get("/api/knowledge-graph", params={"event_id": status["event_id"]})).json()
        nodes = {n["id"]: n for n in graph["nodes"]}
        path = [nodes[i] for i in graph["incident_path"]]
        causes = [n["label"] for n in path if n["kind"] == "cause"]
        self.assertTrue(any("P-101 thermal overload trip" in c for c in causes), causes)
        self.assertTrue(any("Clog on P-102" in c for c in causes), causes)
        fixes = [n for n in path if n["kind"] == "fix"]
        self.assertTrue(fixes and all(n["details"]["text"] and "NOT APPROVED" in n["details"]["approval"] for n in fixes))
        self.assertTrue(any(n["kind"] == "check" for n in path))
        flow = nodes["tag:FIT-102-FLW"]
        self.assertEqual(flow["state"], "limit")
        self.assertIn("worst", flow["details"])
        hypothesis = [e for e in graph["edges"] if e["relation"] == "possible_cause"]
        self.assertTrue(hypothesis and all(e["basis"] == "hypothesis" for e in hypothesis))
        self.assertTrue(all(e["basis"] == "procedure" for e in graph["edges"] if e["relation"] == "addressed_by"))

    async def test_graph_relationships_reach_the_ai_and_the_prototype_is_gone(self):
        status = await self.run_scenario("wet_well_high_level")
        event = self.orch.get_full_event(status["event_id"])
        prompt = ai_diagnosis.build_prompt(ai_diagnosis.build_facts(event, self.db))
        self.assertIn("Plant relationships (configured, from the knowledge graph):", prompt)
        self.assertIn("FIT-102-FLW P-102 flow may influence LIT-101-WWL Wet well level", prompt)
        self.assertIn("covered by SOP-LFT-001", prompt)
        links = IndustrialKnowledgeGraph().context("LiftStation-01", ["FIT-102-FLW", "LIT-101-WWL"])
        self.assertTrue(links)
        self.assertFalse(any("Pump-02" in l or "Valve-04" in l for l in links))

    async def test_long_range_chart_keeps_the_newest_readings(self):
        now = datetime.now(timezone.utc)
        rows = [TagReading(tag="LIT-101-WWL", value=float(i % 50), unit="m", equipment_id="LiftStation-01",
                           timestamp=(now - timedelta(seconds=10 * (25000 - i))).isoformat().replace("+00:00", "Z"))
                for i in range(25000)]
        self.db.insert_readings_batch(rows)
        series = (await self.client.get("/api/telemetry/series", params={"tag": "LIT-101-WWL", "minutes": 100000})).json()
        last = datetime.fromisoformat(series["points"][-1]["t"].replace("Z", "+00:00"))
        self.assertLess((now - last).total_seconds(), 60)


if __name__ == "__main__":
    unittest.main()
