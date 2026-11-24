"""Local diagnosis through LM Studio: prompt scope, streaming, validation,
fallback, caching and health — over real HTTP against a fake LM Studio."""
import asyncio
import json
import os
import re
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx

import ai_diagnosis
import incident_catalog as catalog
from alarm_lifecycle import get_lifecycle_manager
from database import Database
from generator import SyntheticDataGenerator
from tag_registry import TAG_REGISTRY
from tests.fake_lmstudio import MODEL_ID, FakeLMStudio

GOOD = """## 1. What is happening
- CONFIRMED: ALM-WW-101-HH wet well high-high on LiftStation-01; LIT-101-WWL reached 3.85 m.
## 2. Evidence supporting the diagnosis
- CONFIRMED: FIT-102-FLW 90 m³/h, PIT-102-DIS 1.1 bar, MOT-102-CUR 19 A.
- CONFIRMED: P101-FAULT active; MOT-101-CUR 0 A.
## 3. Possible causes
- HYPOTHESIS: P-102 is partly clogged (low flow, low pressure, low current) while P-101 tripped on winding temperature.
## 4. Recommended checks from SOP
- SOP step 4 (p.14): check pump status, motor temperature and local level.
## 5. Additional advisory checks
- Not in the SOP: confirm the HMI values against local gauges.
## 6. Missing information
- UNKNOWN: impeller condition and the P-101 fault code.
## 7. Confidence and limitations
- Loss of pumping capacity is a hypothesis; confirm in the field. SOP-LFT-001 is DRAFT, NOT APPROVED.
"""


class _FakeLMStudioChat(FakeLMStudio):
    reply = GOOD


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _build_event(scenario_id, db, orch, start):
    before = {c.event_id for c in db.get_candidate_events()}
    gen = SyntheticDataGenerator(db=db, sample_interval=2.0, start_time=start)
    async for r in gen.run_scenario_async(scenario_id, speed_multiplier=float("inf")):
        await orch.process_reading(r)
    while orch._in_flight_tasks:
        await asyncio.gather(*list(orch._in_flight_tasks.values()), return_exceptions=True)
    for c in db.get_candidate_events():
        if c.event_id not in before:
            await orch.process_candidate_event(c)
    while orch._in_flight_tasks:
        await asyncio.gather(*list(orch._in_flight_tasks.values()), return_exceptions=True)
    new = [c for c in db.get_candidate_events() if c.event_id not in before]
    return orch.get_full_event(new[0].event_id)


class DiagnosisTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeLMStudioChat)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    async def asyncSetUp(self):
        get_lifecycle_manager().clear_all()
        ai_diagnosis.CACHE.clear()
        ai_diagnosis._LATEST_JOB.clear()
        _FakeLMStudioChat.reply, _FakeLMStudioChat.delay, _FakeLMStudioChat.status = GOOD, 0.0, 200
        _FakeLMStudioChat.received = []
        self.tmp = tempfile.TemporaryDirectory()
        _FakeLMStudioChat.models, _FakeLMStudioChat.loaded, _FakeLMStudioChat.context = [MODEL_ID], True, 8192
        self.env = patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "lmstudio", "EDGE_LLM_URL": self.url + "/v1",
                                           "EDGE_LLM_MODEL": MODEL_ID, "OPSLENS_LOCAL_TIMEOUT": "5"})
        self.env.start()
        import llm_provider
        llm_provider._verified_provider = None
        from orchestrator import OpsLensOrchestrator
        self.db = Database(db_path=Path(self.tmp.name) / "d.db")
        with patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "none"}):
            self.orch = OpsLensOrchestrator(db=self.db)
            self.event = await _build_event("wet_well_high_level", self.db, self.orch,
                                            datetime.now(timezone.utc) - timedelta(minutes=6))

    async def asyncTearDown(self):
        self.env.stop()
        await self.orch.stop()
        get_lifecycle_manager().clear_all()
        self.tmp.cleanup()

    # -- prompt scope ------------------------------------------------------
    def test_facts_hold_only_this_incident_and_its_draft_sop(self):
        facts = ai_diagnosis.build_facts(self.event, self.db)
        spec = catalog.SCENARIO_BY_ID["wet_well_high_level"]
        self.assertEqual(set(facts["incident_tags"]), set(catalog.scenario_tags(spec)))
        self.assertTrue({r["tag"] for r in facts["readings"]} <= set(facts["incident_tags"]))
        self.assertTrue({s["tag"] for s in facts["sequence"]} <= set(facts["incident_tags"]))
        self.assertEqual(facts["sop"]["sop_id"], "SOP-LFT-001")
        self.assertFalse(facts["sop"]["approved"])
        self.assertIn("NOT APPROVED", facts["sop"]["status"])
        self.assertEqual(facts["data_source"], "SIMULATED")
        values = {r["tag"]: r["worst"] for r in facts["readings"]}
        self.assertEqual(values["LIT-101-WWL"], 3.85)
        self.assertEqual(values["FIT-102-FLW"], 90)
        self.assertEqual(facts["scenario_id"], "wet_well_high_level")
        self.assertIn("Lift station", facts["process_area"])
        prompt = ai_diagnosis.build_prompt(facts)
        # The simulator's scripted cause is not handed to the model.
        for cause in spec.root.values():
            self.assertNotIn(cause, prompt)
        self.assertNotIn("Pump-02", prompt)
        self.assertLess(len(prompt) + len(ai_diagnosis.SYSTEM_PROMPT), 12000)
        stamps = [s["time"] for s in facts["sequence"]]
        self.assertEqual(stamps, sorted(stamps))

    # -- real streaming call ----------------------------------------------
    def test_lmstudio_stream_is_used_validated_cached_and_reported(self):
        items = list(ai_diagnosis.stream_diagnosis(self.event, self.db))
        self.assertEqual(items[0]["type"], "meta")
        self.assertGreater(sum(1 for i in items if i["type"] == "token"), 20)
        done = items[-1]
        self.assertEqual(done["engine"], "lmstudio", done.get("fallback_reason"))
        self.assertTrue(done["validation"]["valid"], done["validation"])
        self.assertEqual(done["validation"]["cited_sop_steps"], [4])
        self.assertIsNotNone(done["latency"]["time_to_first_token_ms"])
        self.assertGreater(done["latency"]["output_tokens"], 20)
        self.assertIn("LM Studio", done["label"])
        path, payload = _FakeLMStudioChat.received[-1]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["model"], MODEL_ID)
        self.assertEqual(payload["max_tokens"], 1500)
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(done["source_status"], "ai_generated")
        self.assertTrue(done["operator_card"]["what"])
        self.assertLessEqual(len(done["operator_card"]["checks"]), 5)
        self.assertIn("NOT APPROVED", payload["messages"][1]["content"])
        from llm_provider import provider_status
        self.assertTrue(provider_status()["verified"])
        self.assertTrue(ai_diagnosis.LAST_CALL["succeeded"])
        # Second request: cache, no new model call.
        calls = len(_FakeLMStudioChat.received)
        again = ai_diagnosis.diagnose(self.event, self.db)
        self.assertTrue(again["cached"])
        self.assertEqual(len(_FakeLMStudioChat.received), calls)

    # -- validation -------------------------------------------------------
    def test_line_with_foreign_tag_is_removed_and_reported(self):
        _FakeLMStudioChat.reply = GOOD.replace("MOT-101-CUR 0 A", "PIT-02-PRS 9 bar on Pump-02")
        done = ai_diagnosis.diagnose(self.event, self.db)
        self.assertEqual(done["engine"], "lmstudio")
        self.assertNotIn("PIT-02-PRS", done["text"])
        removed = " ".join(r["line"] + " ".join(r["problems"]) for r in done["validation"]["removed_lines"])
        self.assertIn("PIT-02-PRS", removed)

    def test_invented_reading_and_step_never_reach_the_operator(self):
        _FakeLMStudioChat.reply = GOOD.replace("3.85 m", "4.72 m").replace("step 4", "step 14")
        done = ai_diagnosis.diagnose(self.event, self.db)
        self.assertNotIn("4.72", done["text"])
        self.assertNotIn("step 14", done["text"])
        problems = " ".join(p for r in done["validation"]["removed_lines"] for p in r["problems"])
        self.assertIn("number not in the incident record", problems)
        self.assertIn("SOP step that does not exist", problems)

    def test_mostly_invented_answer_is_rejected_whole(self):
        _FakeLMStudioChat.reply = re.sub(r"^- .*$", "- CONFIRMED: flow is 4321.5 m³/h on XYZ-999-ABC.", GOOD, flags=re.M)
        done = ai_diagnosis.diagnose(self.event, self.db)
        self.assertEqual(done["engine"], "deterministic_fallback")
        self.assertEqual(done["source_status"], "deterministic_fallback")
        self.assertEqual(done["failure_kind"], "rejected")
        self.assertEqual(done["label"], ai_diagnosis.FALLBACK_LABEL)
        self.assertIn("XYZ-999-ABC", done["rejected_ai_text"])
        self.assertNotIn("XYZ-999-ABC", done["text"])

    def test_abbreviated_tag_and_rounded_number_are_accepted(self):
        facts = ai_diagnosis.build_facts(self.event, self.db)
        text = GOOD.replace("- UNKNOWN: impeller condition and the P-101 fault code.",
                            "- UNKNOWN: whether FIT-102 is reading correctly at 90.2 m³/h.")
        check = ai_diagnosis.validate(text, facts)
        self.assertEqual(check["removed_lines"], [])

    def test_thousands_separator_is_read_as_one_number(self):
        self.assertTrue(ai_diagnosis._number_known(2150.0, {2150.0}))
        self.assertEqual(ai_diagnosis._to_number("2,150"), 2150.0)
        self.assertEqual(ai_diagnosis._NUM_RE.findall("flow 2,150 m3/h then 1.5"), ["2,150", "1.5"])

    def test_control_claims_and_approved_sop_are_rejected(self):
        check = ai_diagnosis.validate(GOOD + "\nI have reset the pump.", ai_diagnosis.build_facts(self.event, self.db))
        self.assertFalse(check["valid"])
        check = ai_diagnosis.validate(GOOD.replace("is DRAFT, NOT APPROVED", "is the approved SOP"),
                                      ai_diagnosis.build_facts(self.event, self.db))
        self.assertFalse(check["valid"])
        check = ai_diagnosis.validate(GOOD.replace("## 3. Possible causes", "").replace(
            "## 5. Additional advisory checks", "").replace("## 7. Confidence and limitations", ""),
            ai_diagnosis.build_facts(self.event, self.db))
        self.assertIn("missing sections", " ".join(check["rejected_reasons"]))

    # -- fallback ---------------------------------------------------------
    def test_lmstudio_down_gives_labelled_fallback_and_is_not_cached(self):
        with patch.dict(os.environ, {"EDGE_LLM_URL": f"http://127.0.0.1:{_free_port()}"}):
            done = ai_diagnosis.diagnose(self.event, self.db)
        self.assertEqual(done["engine"], "deterministic_fallback")
        self.assertIn("not running", done["fallback_reason"])
        # A SOP was found but no model answered: labelled SOP retrieval only.
        self.assertEqual(done["source_status"], "sop_retrieval_only")
        self.assertEqual(done["failure_kind"], "unreachable")
        self.assertIn("start_lmstudio", done["fix_hint"])
        self.assertTrue(done["text"].startswith("> " + ai_diagnosis.FALLBACK_LABEL))
        for i, name in enumerate(ai_diagnosis.SECTIONS, 1):
            self.assertIn(f"## {i}. {name}", done["text"])
        self.assertFalse(ai_diagnosis.LAST_CALL["succeeded"])
        from llm_provider import provider_status
        self.assertFalse(provider_status()["verified"])
        # LM Studio back: the next request really calls it (failure was not cached).
        self.assertEqual(ai_diagnosis.diagnose(self.event, self.db)["engine"], "lmstudio")

    def test_model_missing_and_provider_none(self):
        _FakeLMStudioChat.models = ["llama-3.2-3b-instruct"]
        done = ai_diagnosis.diagnose(self.event, self.db)
        self.assertEqual(done["failure_kind"], "model_missing")
        self.assertIn("llama-3.2-3b-instruct", done["fallback_reason"])
        self.assertIn("EDGE_LLM_MODEL", done["fix_hint"])
        _FakeLMStudioChat.models = [MODEL_ID]
        with patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "none"}):
            done = ai_diagnosis.diagnose(self.event, self.db)
        self.assertEqual(done["engine"], "deterministic_fallback")
        self.assertEqual(done["source_status"], "deterministic_fallback")
        self.assertIn("No LLM provider", done["fallback_reason"])

    def test_readings_after_recovery_still_describe_the_incident(self):
        """After recovery the latest reading is healthy; the facts must keep the incident."""
        from models import TagReading
        now = datetime.now(timezone.utc) + timedelta(minutes=1)
        self.db.insert_reading(TagReading(tag="FIT-102-FLW", value=412.0, timestamp=now.isoformat().replace("+00:00", "Z"),
                                          unit="m³/h", equipment_id="LiftStation-01", data_source="simulated"))
        row = next(r for r in ai_diagnosis.build_facts(self.event, self.db)["readings"] if r["tag"] == "FIT-102-FLW")
        self.assertEqual(row["worst"], 90)
        self.assertEqual(row["now"], 412)
        self.assertEqual(row["state_at_worst"], "LIMIT EXCEEDED")
        self.assertEqual(row["state_now"], "within normal")
        self.assertIn("worst 90", ai_diagnosis.build_prompt(ai_diagnosis.build_facts(self.event, self.db)))

    def test_no_matching_sop_answer(self):
        facts = ai_diagnosis.build_facts(self.event, self.db)
        facts["sop"] = None
        self.assertIn("SOP: none", ai_diagnosis.build_prompt(facts))
        text = ai_diagnosis.deterministic_diagnosis(facts)
        self.assertIn(ai_diagnosis.NO_SOP_SENTENCE, text)
        self.assertIn("authorized engineer", text)
        self.assertNotIn("Step 1", text)

    def test_parallel_requests_share_one_generation(self):
        _FakeLMStudioChat.delay = 0.3
        results = []
        threads = [threading.Thread(target=lambda: results.append(ai_diagnosis.diagnose(self.event, self.db))) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(15)
        self.assertEqual(len(results), 4)
        self.assertEqual(len([p for p, _ in _FakeLMStudioChat.received if p == "/v1/chat/completions"]), 1)
        self.assertTrue(all(r["engine"] == "lmstudio" for r in results))

    def test_generation_survives_the_reader_leaving(self):
        _FakeLMStudioChat.delay = 0.2
        stream = ai_diagnosis.stream_diagnosis(self.event, self.db)
        self.assertEqual(next(stream)["type"], "meta")
        stream.close()  # the operator switched page
        deadline = time.time() + 10
        while ai_diagnosis._JOBS and time.time() < deadline:
            time.sleep(0.05)
        done = ai_diagnosis.diagnose(self.event, self.db)
        self.assertTrue(done["cached"], "the finished job must serve the returning reader")
        self.assertEqual(len([p for p, _ in _FakeLMStudioChat.received if p == "/v1/chat/completions"]), 1)

    def test_a_newer_context_supersedes_a_waiting_job(self):
        facts = ai_diagnosis.build_facts(self.event, self.db)
        older = json.loads(json.dumps(facts))
        older["active_alarms"] = []
        ai_diagnosis._LATEST_JOB[facts["event_id"]] = ai_diagnosis._cache_key(facts)
        job = ai_diagnosis._Job(ai_diagnosis._cache_key(older))
        result = ai_diagnosis._generate(job, older, None)
        self.assertEqual(result["failure_kind"], "superseded")
        self.assertEqual([p for p, _ in _FakeLMStudioChat.received if p == "/v1/chat/completions"], [])

    def test_stored_answer_survives_a_restart_and_refresh_regenerates(self):
        ai_diagnosis.diagnose(self.event, self.db)
        ai_diagnosis.CACHE.clear()  # backend restarted
        calls = len(_FakeLMStudioChat.received)
        again = ai_diagnosis.diagnose(self.event, self.db)
        self.assertTrue(again["cached"])
        self.assertEqual(again["source_status"], "ai_generated")
        self.assertEqual(len(_FakeLMStudioChat.received), calls)
        facts = ai_diagnosis.build_facts(self.event, self.db)
        forced = [i for i in ai_diagnosis.stream_diagnosis_from_facts(facts, db=self.db, force=True)][-1]
        self.assertFalse(forced["cached"])
        self.assertEqual(len(_FakeLMStudioChat.received), calls + 1)

    def test_unrelated_reading_noise_does_not_invalidate_but_new_evidence_does(self):
        facts = ai_diagnosis.build_facts(self.event, self.db)
        noisy = json.loads(json.dumps(facts))
        noisy["readings"][0]["now"] = 12345
        self.assertEqual(ai_diagnosis.facts_hash(facts), ai_diagnosis.facts_hash(noisy))
        changed = json.loads(json.dumps(facts))
        changed["active_alarms"] = []
        self.assertNotEqual(ai_diagnosis.facts_hash(facts), ai_diagnosis.facts_hash(changed))

    def test_first_token_timeout_is_labelled(self):
        _FakeLMStudioChat.delay = 1.5
        with patch.dict(os.environ, {"OPSLENS_DIAG_FIRST_TOKEN_TIMEOUT": "5"}), patch("edge_slm.first_token_timeout", lambda: 0.3):
            done = ai_diagnosis.diagnose(self.event, self.db)
        self.assertEqual(done["failure_kind"], "timeout")
        self.assertIn("sent no text", done["fallback_reason"])

    def test_fallback_text_passes_its_own_validator(self):
        facts = ai_diagnosis.build_facts(self.event, self.db)
        self.assertTrue(ai_diagnosis.validate(ai_diagnosis.deterministic_diagnosis(facts), facts)["valid"])

    # -- HTTP --------------------------------------------------------------
    async def test_sse_endpoint_streams_meta_tokens_done_and_health_reports_call(self):
        from api.routes import create_app
        app = create_app(orchestrator=self.orch)
        _FakeLMStudioChat.delay = 0.2
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=20) as client:
            r = await client.get(f"/api/events/{self.event['event_id']}/diagnosis/stream")
            self.assertEqual(r.status_code, 200)
            kinds = [line.split(": ", 1)[1] for line in r.text.splitlines() if line.startswith("event: ")]
            self.assertEqual(kinds[0], "meta")
            self.assertIn("token", kinds)
            self.assertEqual(kinds[-1], "done")
            done = json.loads(r.text.strip().splitlines()[-1].split("data: ", 1)[1])
            self.assertEqual(done["engine"], "lmstudio")
            self.assertGreaterEqual(done["latency"]["time_to_first_token_ms"], 200)
            health = (await client.get("/api/copilot/ai-status")).json()
            self.assertTrue(health["local_selected"])
            self.assertEqual(health["status_label"], "AI: LM Studio ready")
            self.assertTrue(health["verified_in_this_process"])
            self.assertTrue(health["last_diagnosis_call"]["succeeded"])
            self.assertEqual((await client.get("/api/events/nope/diagnosis")).status_code, 404)
            self.assertIn("status", kinds)


class SixIncidentFactsTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_incident_prompt_has_only_its_tags_and_sop(self):
        get_lifecycle_manager().clear_all()
        from orchestrator import OpsLensOrchestrator
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "none"}):
            db = Database(db_path=Path(tmp) / "s.db")
            orch = OpsLensOrchestrator(db=db)
            start = datetime.now(timezone.utc) - timedelta(hours=3)
            all_tags = {t for s in catalog.SCENARIOS for t in catalog.scenario_tags(s)}
            for i, spec in enumerate(catalog.SCENARIOS):
                event = await _build_event(spec.scenario_id, db, orch, start + timedelta(minutes=20 * i))
                facts = ai_diagnosis.build_facts(event, db)
                prompt = ai_diagnosis.build_prompt(facts)
                with self.subTest(scenario=spec.scenario_id):
                    own = set(catalog.scenario_tags(spec))
                    if spec.sop_id:
                        self.assertEqual(facts["sop"]["sop_id"], spec.sop_id)
                        self.assertEqual(facts["sop"]["coverage"], spec.sop_coverage)
                    else:
                        self.assertIsNone(facts["sop"])
                        self.assertIn("SOP: none", prompt)
                    self.assertEqual(facts["primary_alarm"]["tag"], spec.primary_alarm)
                    for other in all_tags - own:
                        self.assertNotIn(f'"{other}"', prompt)
                    self.assertTrue(ai_diagnosis.validate(ai_diagnosis.deterministic_diagnosis(facts), facts)["valid"])
            await orch.stop()
        get_lifecycle_manager().clear_all()


if __name__ == "__main__":
    unittest.main()
