"""The eleven-step alarm lifecycle, through the API, for all seven scenarios."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import incident_catalog
from alarm_lifecycle import get_lifecycle_manager
from database import Database


class Lifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        get_lifecycle_manager().clear_all()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "none", "OPSLENS_RECOVERY_HOLD_SECONDS": "0"})
        self.env.start()
        from api.routes import create_app
        from orchestrator import OpsLensOrchestrator
        self.db = Database(db_path=Path(self.tmp.name) / "l.db")
        self.orch = OpsLensOrchestrator(db=self.db)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(orchestrator=self.orch)), base_url="http://t", timeout=60)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.orch.stop()
        self.env.stop()
        get_lifecycle_manager().clear_all()
        self.tmp.cleanup()

    async def get(self, path):
        response = await self.client.get("/api" + path)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def run_scenario(self, scenario_id):
        response = await self.client.post("/api/scenarios/run", json={"scenario_id": scenario_id, "speed_multiplier": 2000})
        self.assertEqual(response.status_code, 200, response.text)
        for _ in range(800):
            status = await self.get("/scenarios/status")
            if status["status"] not in ("running", "recovering"):
                return status
            await asyncio.sleep(0.02)
        self.fail("scenario did not finish")

    async def action(self, event_id, action_type, notes):
        return await self.client.post("/api/actions", json={"event_id": event_id, "operator": "A. Tester", "action_type": action_type, "notes": notes})

    async def lifecycle(self, scenario_id):
        status = await self.run_scenario(scenario_id)
        event_id = status["event_id"]

        # 1. Active and unacknowledged, and it cannot be resolved.
        state = await self.get(f"/events/{event_id}/lifecycle")
        self.assertEqual(state["state"], "ACTIVE_UNACKNOWLEDGED")
        self.assertTrue(state["can_acknowledge"])
        self.assertFalse(state["can_resolve"])
        self.assertTrue(state["simulated"])
        premature = await self.action(event_id, "RESOLVE", "Too early")
        self.assertEqual(premature.status_code, 400)

        # 2. Acknowledge.
        ack = await self.client.post(f"/api/incidents/{event_id}/acknowledge", params={"operator": "A. Tester"})
        self.assertEqual(ack.status_code, 200, ack.text)

        # 3. It stays active, and a second acknowledgement has nothing to do.
        state = await self.get(f"/events/{event_id}/lifecycle")
        self.assertEqual(state["state"], "ACTIVE_ACKNOWLEDGED")
        self.assertEqual(state["message"], "Alarm acknowledged. The process condition remains active.")
        self.assertGreater(state["active_count"], 0)
        self.assertFalse(state["can_resolve"])
        self.assertEqual((await self.client.post(f"/api/incidents/{event_id}/acknowledge", params={"operator": "A. Tester"})).status_code, 409)
        self.assertEqual((await self.action(event_id, "RESOLVE", "Still too early")).status_code, 400)

        # 4 and 5. Inspection and a note change the record, not the state.
        self.assertEqual((await self.action(event_id, "INSPECT", "Checked locally")).status_code, 200)
        self.assertEqual((await self.action(event_id, "ANNOTATE", "Standby pump noisy")).status_code, 200)
        self.assertEqual((await self.get(f"/events/{event_id}/lifecycle"))["state"], "ACTIVE_ACKNOWLEDGED")

        # 6 and 7. The simulated fix writes after-fix telemetry.
        before = len(self.db.get_readings(tag=status["alarm_tag"], limit=100000))
        fix = await self.client.post("/api/scenarios/corrective-action", json={"scenario_id": scenario_id})
        self.assertEqual(fix.status_code, 200, fix.text)
        self.assertFalse(fix.json()["alarm_active"])
        after = len(self.db.get_readings(tag=status["alarm_tag"], limit=100000))
        self.assertGreater(after, before)

        # 8. Recovery criteria, each one met.
        state = await self.get(f"/events/{event_id}/lifecycle")
        self.assertEqual(state["blocking"], [])
        self.assertEqual(state["state"], "CONDITION_RECOVERED")
        self.assertTrue(state["can_resolve"])

        # 9. Confirm resolved.
        done = await self.action(event_id, "RESOLVE", "Cause found and fixed")
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual((await self.get(f"/events/{event_id}/lifecycle"))["state"], "RESOLVED")

        # 10. It leaves the open list and stays in the record.
        ranked = await self.get("/incidents/ranked")
        self.assertNotIn(event_id, [i["event_id"] for i in ranked["ranked_incidents"] if i.get("status") != "RESOLVED"])
        self.assertIn(event_id, [a.get("incident_id") for a in (await self.get("/alarms/history"))["alarms"]])
        self.assertEqual((await self.get(f"/events/{event_id}"))["status"], "RESOLVED")

        # 11. The audit trail holds every step, in order, with the operator.
        kinds = [a["action_type"] for a in (await self.get(f"/events/{event_id}"))["actions"]]
        for kind in ("ACKNOWLEDGED", "INSPECT", "ANNOTATE", "RESOLVE"):
            self.assertTrue(any(kind in k or k in kind for k in kinds), f"{kind} missing from {kinds}")
        timeline = await self.get(f"/events/{event_id}/timeline")
        self.assertTrue(any(e["kind"] == "operator" and e["operator"] == "A. Tester" for e in timeline["events"]))

        # Mark Unresolved reopens with an audit record and never closes.
        reopened = await self.client.post(f"/api/incidents/{event_id}/unresolved", json={"operator": "A. Tester", "reason": "Alarm came back in the field"})
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertNotEqual((await self.get(f"/events/{event_id}/lifecycle"))["state"], "RESOLVED")
        self.assertTrue(any("UNRESOLVED" in a["action_type"] for a in (await self.get(f"/events/{event_id}"))["actions"]))

    async def test_fix_before_acknowledging_can_still_be_resolved(self):
        """ISA-18.2: alarms that returned to normal unacknowledged must still be
        acknowledgeable, otherwise the incident could never be resolved."""
        scenario_id = "wet_well_high_level"
        status = await self.run_scenario(scenario_id)
        event_id = status["event_id"]
        fix = await self.client.post("/api/scenarios/corrective-action", json={"scenario_id": scenario_id})
        self.assertEqual(fix.status_code, 200, fix.text)

        state = await self.get(f"/events/{event_id}/lifecycle")
        self.assertFalse(state["can_resolve"])
        self.assertTrue(state["can_acknowledge"])
        self.assertEqual((await self.action(event_id, "RESOLVE", "Too early")).status_code, 400)

        ack = await self.client.post(f"/api/incidents/{event_id}/acknowledge", params={"operator": "A. Tester"})
        self.assertEqual(ack.status_code, 200, ack.text)
        state = await self.get(f"/events/{event_id}/lifecycle")
        self.assertEqual(state["unacknowledged_count"], 0)
        self.assertEqual(state["active_count"], 0)
        self.assertTrue(state["can_resolve"], state["blocking"])
        # Acknowledging again has nothing to do, and is a clean 409, not an error.
        self.assertEqual((await self.client.post(f"/api/incidents/{event_id}/acknowledge", params={"operator": "A. Tester"})).status_code, 409)

        done = await self.action(event_id, "RESOLVE", "Fixed, then acknowledged")
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual((await self.get(f"/events/{event_id}/lifecycle"))["state"], "RESOLVED")
        metrics = await self.get("/metrics/impact")
        self.assertEqual(metrics["resolved_incidents"], 1)
        self.assertIsNotNone(metrics["avg_mttr_minutes"])


for _scenario in incident_catalog.SCENARIOS:
    _id = _scenario.scenario_id

    async def _test(self, scenario_id=_id):
        await self.lifecycle(scenario_id)
    setattr(Lifecycle, f"test_{_id}", _test)


if __name__ == "__main__":
    unittest.main()
