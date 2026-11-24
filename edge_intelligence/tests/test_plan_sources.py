"""Next Best Action source attribution.

The plan's wording and order are unchanged; these tests check the provenance
fields the HMI turns into source labels (hmi_ui/src/lib/planSource.js):

  evidence steps carry the recorded tags they rest on (none -> no label),
  the final recovery check carries the readings to verify,
  the plan says whether its SOP is approved, and SOP steps exist only with an SOP,
  earlier resolutions carry the incident they came from.

Cases: no SOP and no history; no SOP but an earlier resolution; a draft SOP;
an approved SOP (the retrieved SOP marked approved); telemetry evidence
without an SOP; and all eight scenarios.
"""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import incident_catalog as catalog
from ai_diagnosis import build_facts, build_plan, deterministic_diagnosis, evidence_checks
from alarm_lifecycle import get_lifecycle_manager
from database import Database

UV, LIFT = "uv_disinfection_low", "wet_well_high_level"


class PlanSources(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        get_lifecycle_manager().clear_all()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "none", "OPSLENS_SEED_HISTORY": "false", "OPSLENS_RECOVERY_HOLD_SECONDS": "0"})
        self.env.start()
        from api.routes import create_app
        from orchestrator import OpsLensOrchestrator
        self.db = Database(db_path=Path(self.tmp.name) / "p.db")
        self.orch = OpsLensOrchestrator(db=self.db)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(orchestrator=self.orch)), base_url="http://t", timeout=60)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.orch.stop()
        self.env.stop()
        get_lifecycle_manager().clear_all()
        self.tmp.cleanup()

    async def run_plan(self, sid):
        r = await self.client.post("/api/scenarios/run", json={"scenario_id": sid, "speed_multiplier": 2000})
        self.assertEqual(r.status_code, 200, r.text)
        for _ in range(800):
            status = (await self.client.get("/api/scenarios/status")).json()
            if status["status"] not in ("running", "recovering"):
                break
            await asyncio.sleep(0.02)
        self.assertTrue(status.get("event_id"), status)
        facts = await asyncio.to_thread(build_facts, self.orch.get_full_event(status["event_id"]), self.db)
        plan = build_plan({"source_status": "deterministic", "text": deterministic_diagnosis(facts)}, facts)
        return status["event_id"], facts, plan

    async def resolve(self, sid, event_id, notes):
        post = self.client.post
        self.assertEqual((await post(f"/api/incidents/{event_id}/acknowledge", params={"operator": "A. Tester"})).status_code, 200)
        self.assertEqual((await post("/api/scenarios/corrective-action", json={"scenario_id": sid})).status_code, 200)
        done = await post("/api/actions", json={"event_id": event_id, "operator": "A. Tester", "action_type": "RESOLVE", "notes": notes})
        self.assertEqual(done.status_code, 200, done.text)

    def assert_traceable(self, facts, plan):
        """Every evidence step names only recorded tags; SOP steps only with an SOP."""
        recorded = set(facts["incident_tags"]) | {r["tag"] for r in facts["readings"]}
        for step in plan["steps"]:
            if step["source"] == "evidence":
                self.assertTrue(set(step["evidence_tags"]) <= recorded, step)
            if step["source"] == "sop":
                self.assertTrue(plan["sop_id"] and step["sop_step"], step)
        final = [s for s in plan["steps"] if s.get("final")]
        self.assertLessEqual(len(final), 1)
        if final:
            self.assertEqual(final[0]["kind"], "VERIFY")
            self.assertTrue(final[0]["evidence_tags"])

    async def test_no_sop_no_history_then_an_earlier_resolution(self):
        # 1 and 5: no SOP, no earlier incident; the checks rest on recorded telemetry.
        first, facts, plan = await self.run_plan(UV)
        self.assertEqual(plan["basis"], "no_sop")
        self.assertIsNone(plan["sop_id"])
        self.assertFalse(plan["sop_approved"])
        self.assertFalse(any(s["source"] == "sop" for s in plan["steps"]))
        self.assertFalse([e for e in facts["history"]["earlier"] if e.get("resolution_notes")])
        evidence = [s for s in plan["steps"] if s["source"] == "evidence" and not s.get("final")]
        self.assertTrue(evidence and all(s["evidence_tags"] for s in evidence), plan["steps"])
        self.assert_traceable(facts, plan)

        # 2: the same alarm again after the first was resolved: the earlier
        # resolution is available, with the incident it came from.
        await self.resolve(UV, first, "Cleaned the UV quartz sleeves and replaced lamp bank B ballast")
        _, facts2, plan2 = await self.run_plan(UV)
        self.assertEqual(plan2["basis"], "no_sop")
        earlier = [e for e in facts2["history"]["earlier"] if e.get("resolution_notes")]
        self.assertEqual([e["event_id"] for e in earlier], [first])
        self.assertIn("quartz sleeves", earlier[0]["resolution_notes"])
        self.assertFalse(earlier[0]["seeded_history"])
        self.assert_traceable(facts2, plan2)

    async def test_draft_sop_is_not_approved(self):
        _, facts, plan = await self.run_plan(LIFT)
        self.assertEqual(plan["sop_id"], catalog.SCENARIO_BY_ID[LIFT].sop_id)
        self.assertFalse(plan["sop_approved"])
        self.assertRegex(plan["sop_status"], "DRAFT")
        sop_steps = [s for s in plan["steps"] if s["source"] == "sop"]
        self.assertTrue(sop_steps and all(s["sop_step"] and s["sop_text"] for s in sop_steps))
        trip = next(s for s in plan["steps"] if s["source"] == "evidence" and "P-101" in s["text"])
        self.assertIn("TIT-101-WDG", trip["evidence_tags"], trip)
        self.assert_traceable(facts, plan)

    async def test_approved_sop_is_reported_as_approved(self):
        # No plant SOP is approved, so the retrieved SOP is marked approved here
        # to check the plan reports it; the steps themselves do not change.
        import sop_context
        real = sop_context.build_sop_context

        def approved(*a, **k):
            ctx = real(*a, **k)
            if ctx.get("sop_id"):
                ctx.update(approved=True, approval_label=sop_context.APPROVED_LABEL)
            return ctx

        with patch.object(sop_context, "build_sop_context", approved):
            _, facts, plan = await self.run_plan(LIFT)
        self.assertTrue(plan["sop_approved"])
        self.assertNotRegex(plan["sop_status"], "DRAFT")
        self.assertTrue(any(s["source"] == "sop" and s["sop_step"] for s in plan["steps"]))
        self.assert_traceable(facts, plan)

    async def test_site_setting_makes_plant_sops_approved_everywhere(self):
        # OPSLENS_PLANT_SOPS_APPROVED=true (the shipped default): the plan, the
        # insight and Evidence Support all see an approved SOP, with no draft wording.
        with patch.dict(os.environ, {"OPSLENS_PLANT_SOPS_APPROVED": "true"}):
            _, facts, plan = await self.run_plan(LIFT)
            import incident_insight
            support = incident_insight.confidence(facts, facts["history"])
        self.assertTrue(plan["sop_approved"])
        self.assertEqual(plan["sop_status"], "APPROVED")
        self.assertTrue(facts["sop"]["approved"])
        self.assertNotIn("draft", " ".join(s["text"] for s in plan["steps"]).lower())
        sop_check = next(c for c in support["checks"] if "SOP is approved" in c["check"])
        self.assertEqual(sop_check["earned"], sop_check["points"])

    async def test_all_eight_scenarios_are_traceable(self):
        for sid in [s.scenario_id for s in catalog.SCENARIOS]:
            with self.subTest(sid):
                event_id, facts, plan = await self.run_plan(sid)
                self.assertEqual(bool(plan["sop_id"]), bool(catalog.SCENARIO_BY_ID[sid].sop_id))
                self.assert_traceable(facts, plan)
                await self.client.post("/api/scenarios/corrective-action", json={"scenario_id": sid})


class EvidenceRefs(unittest.TestCase):
    def test_unrecorded_tags_give_no_evidence_reference(self):
        facts = {"incident_tags": ["LIT-101-WWL"], "readings": [], "active_alarms": [],
                 "cause_candidates": [{"role": "leading", "kind": "early", "short": "Something odd",
                                       "tags": ["NOT-IN-RECORD"], "evidence_tags": ["ALSO-NOT"], "supporting": []}]}
        step = evidence_checks(facts)[0]
        self.assertEqual(step["evidence_tags"], [])

    def test_normal_supporting_readings_are_not_cited(self):
        facts = {"incident_tags": ["A", "B", "C"], "active_alarms": [],
                 "readings": [{"tag": "B", "name": "B", "state_at_worst": "above normal", "normal": "1 to 2"},
                              {"tag": "C", "name": "C", "state_at_worst": "within normal", "normal": "1 to 2"}],
                 "cause_candidates": [{"role": "leading", "kind": "trip", "short": "A trip", "tags": ["A"],
                                       "evidence_tags": ["B", "C"], "supporting": []}]}
        self.assertEqual(evidence_checks(facts)[0]["evidence_tags"], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
