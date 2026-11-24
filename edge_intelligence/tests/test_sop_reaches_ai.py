"""Regression: the SOP the viewer shows must reach the AI diagnosis.

Reported bug: for the lift station incident the AI said "No SOP step covers this
event" while the viewer displayed SOP-LFT-001. These tests read the real manual
and check the actual backend response, including the diagnosis HTTP route.
"""
import asyncio
import json
import os
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx

from tests.fake_lmstudio import MODEL_ID, FakeLMStudio
import ai_diagnosis
import sop_context
from alarm_lifecycle import get_lifecycle_manager
from database import Database

ANSWER = """## 1. What is happening
- CONFIRMED: ALM-WW-101-HH on LiftStation-01.
## 2. Evidence supporting the diagnosis
- CONFIRMED: LIT-101-WWL reached 3.85 m.
## 3. Possible causes
- HYPOTHESIS: pump capacity is reduced.
## 4. Recommended checks from SOP
- No SOP step covers this event.
- SOP step 8 (p.14): allow the motor to cool and identify the cause.
## 5. Additional advisory checks
- Not in the SOP: compare HMI values with local gauges.
## 6. Missing information
- UNKNOWN: fault code.
## 7. Confidence and limitations
- The cause is a hypothesis.
"""


class _LMStudio(FakeLMStudio):
    reply = ANSWER


class Base(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _LMStudio)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    async def asyncSetUp(self):
        get_lifecycle_manager().clear_all()
        ai_diagnosis.CACHE.clear()
        _LMStudio.received = []
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "lmstudio", "EDGE_LLM_MODEL": MODEL_ID, "OPSLENS_LOCAL_WARMUP": "false",
                                           "EDGE_LLM_URL": f"http://127.0.0.1:{self.server.server_address[1]}"})
        self.env.start()
        from api.routes import create_app
        from orchestrator import OpsLensOrchestrator
        self.db = Database(db_path=Path(self.tmp.name) / "s.db")
        with patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "none"}):
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
        for _ in range(600):
            status = (await self.client.get("/api/scenarios/status")).json()
            if status["status"] != "running":
                return status
            await asyncio.sleep(0.02)

    async def diagnosis(self, event_id):
        return (await self.client.get(f"/api/events/{event_id}/diagnosis")).json()


class LiftStationSop(Base):
    async def test_a_steps_and_pages_reach_the_model(self):
        status = await self.run_scenario("wet_well_high_level")
        done = await self.diagnosis(status["event_id"])
        prompt = _LMStudio.received[-1][1]["messages"][1]["content"]
        for text in ("Check both pumps", "Dispatch the on-call technician", "Reduce the inflow",
                     "impeller for wear", "cool to normal winding temperature",
                     "Identify the cause before resetting", "start it in hand mode"):
            self.assertIn(text, prompt, text)
        self.assertIn("(p.14)", prompt)
        self.assertIn("(p.15)", prompt)
        self.assertIn("SOP-LFT-001", prompt)
        self.assertIn("DRAFT — NOT APPROVED", prompt)
        self.assertIn("This SOP applies to this incident", prompt)
        context = done["sop_context"]
        self.assertEqual(context["sop_id"], "SOP-LFT-001")
        self.assertIn(context["status"], ("full", "partial"))
        self.assertTrue({12, 13, 14, 15} <= set(context["pages"]) or {13, 14, 15} <= set(context["pages"]))
        self.assertTrue(context["revision"])
        self.assertEqual(context["source_document"], "SOP-MAN-000")
        self.assertGreaterEqual(len(context["steps"]), 12)
        self.assertTrue(context["passages"])
        self.assertNotIn("no sop step covers", ai_diagnosis.SYSTEM_PROMPT.lower().replace("never write that no sop or sop step covers", ""))

    async def test_false_no_sop_line_never_reaches_the_operator(self):
        status = await self.run_scenario("wet_well_high_level")
        done = await self.diagnosis(status["event_id"])
        self.assertEqual(done["source_status"], "ai_generated")
        self.assertNotIn("No SOP step covers", done["text"])
        removed = " ".join(" ".join(r["problems"]) for r in done["validation"]["removed_lines"])
        self.assertIn("matching SOP was retrieved", removed)
        # The cited step, plus the first two: the immediate response (acknowledge, call the technician).
        self.assertEqual([c["step"] for c in done["sop_checks"]], [1, 2, 8])
        self.assertIn("Identify the cause before resetting", done["sop_checks"][2]["text"])
        self.assertEqual(done["sop_checks"][2]["page"], 14)

    async def test_e_viewer_text_is_the_text_the_ai_receives(self):
        status = await self.run_scenario("wet_well_high_level")
        viewer = (await self.client.get("/api/sops/SOP-LFT-001")).json()["data"]["content_pages"]
        viewer_text = re.sub(r"\s+", " ", " ".join(page["text"] for page in viewer))
        done = await self.diagnosis(status["event_id"])
        for step in done["sop_context"]["steps"]:
            self.assertIn(step["text"][:80], viewer_text)
            self.assertIn(step["page"], [page["page"] for page in viewer])
        for passage in done["sop_context"]["passages"]:
            self.assertIn(passage["text"][:60], viewer_text)

    async def test_pdf_hyphen_codes_are_readable(self):
        status = await self.run_scenario("wet_well_high_level")
        context = (await self.client.get(f"/api/events/{status['event_id']}/retrieval")).json()["sop_context"]
        text = " ".join(s["text"] for s in context["steps"]) + " ".join(p["text"] for p in context["passages"])
        self.assertNotIn("\ufffe", text)
        self.assertNotIn("\u00ad", text)

    async def test_matching_reports_its_signals(self):
        status = await self.run_scenario("wet_well_high_level")
        signals = {s["signal"]: s["matched"] for s in (await self.diagnosis(status["event_id"]))["sop_context"]["match_signals"]}
        self.assertTrue(signals["Scenario and process area"])
        self.assertTrue(signals["Keyword match in source text"])
        self.assertTrue(signals["SOP metadata"])


class OtherCases(Base):
    async def test_b_partial_match_says_what_is_and_is_not_covered(self):
        status = await self.run_scenario("centrifuge_capture")
        context = (await self.diagnosis(status["event_id"]))["sop_context"]
        self.assertEqual(context["status"], "partial")
        self.assertIn("partly", context["status_text"])
        self.assertTrue(context["covered"])
        self.assertTrue(any("inhibitory or toxic" in note for note in context["notes"]))
        self.assertIn("Details the SOP does not address", _LMStudio.received[-1][1]["messages"][1]["content"])

    async def test_c_no_relevant_sop(self):
        status = await self.run_scenario("wet_well_high_level")
        full = self.orch.get_full_event(status["event_id"])
        full["sop_match"] = {"sop_id": "SOP-NONE", "retrieval_status": "no_match", "retrieved_chunks": []}
        facts = ai_diagnosis.build_facts(full, self.db)
        self.assertIsNone(facts["sop"])
        self.assertEqual(facts["sop_context"]["status"], "none")
        prompt = ai_diagnosis.build_prompt(facts)
        self.assertIn("SOP: none", prompt)
        text = ai_diagnosis.deterministic_diagnosis(facts)
        self.assertIn("No directly matching approved SOP was found", text)
        self.assertIn("GENERAL ADVICE", text)
        self.assertIn("authorized engineer", text)
        self.assertNotRegex(text, r"SOP step \d")
        check = ai_diagnosis.validate(text, facts)
        self.assertTrue(check["valid"], check)

    async def test_d_every_draft_stays_draft(self):
        for scenario in ("wet_well_high_level", "aeration_blower_failure"):
            with self.subTest(scenario):
                status = await self.run_scenario(scenario)
                context = (await self.diagnosis(status["event_id"]))["sop_context"]
                self.assertIsNotNone(context["sop_id"])
                self.assertFalse(context["approved"])
                self.assertEqual(context["approval_label"], "DRAFT — NOT APPROVED")

    async def test_shared_alarm_tag_still_finds_its_sop(self):
        from sop_retriever import SopRetriever, PRIMARY_DEMO_SOPS
        self.assertIsNone(PRIMARY_DEMO_SOPS.get("EFF-301-NH4"))
        retriever = SopRetriever(db=self.db)
        match = retriever._retrieve_primary_demo_reference("e", "EFF-301-NH4", None, "q", False, incident_tags=["BLW-201-FAULT", "BLW-201-CUR"])
        self.assertEqual(match.sop_id, "SOP-AER-001")

    def test_denial_rule_keeps_honest_lines(self):
        facts = {"sop": {"sop_id": "SOP-LFT-001", "steps": []}, "incident_tags": [], "readings": []}
        self.assertTrue(ai_diagnosis._DENIES_SOP.search("No SOP step covers this event."))
        self.assertTrue(ai_diagnosis._DENIES_SOP.search("There is no relevant SOP for this."))
        self.assertTrue(ai_diagnosis._DENIES_SOP.search("The SOP does not cover this event."))
        self.assertIsNone(ai_diagnosis._DENIES_SOP.search("SOP step 4 says to check the pump."))
