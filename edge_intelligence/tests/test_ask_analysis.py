"""Ask OpsLens with Groq: an AI answer built from the diagnosis facts, checked
line by line, through the real /assistant/ask route, against a mock Groq."""
import asyncio
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx

import ai_diagnosis
from alarm_lifecycle import get_lifecycle_manager
from database import Database
from groq_keys import POOL


class _Groq(BaseHTTPRequestHandler):
    reply, bodies, fail = "", [], False

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).bodies.append(body)
        if type(self).fail:
            self.send_response(503)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        for word in type(self).reply.split(" "):
            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": word + " "}}]}) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")


class AskAnalysis(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Groq)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    async def asyncSetUp(self):
        get_lifecycle_manager().clear_all()
        POOL.reset()
        ai_diagnosis.CACHE.clear()
        _Groq.bodies, _Groq.fail = [], False
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "OPSLENS_LLM_PROVIDER": "groq", "GROQ_API_KEYS": "ok-1", "OPSLENS_GROQ_MODEL": "openai/gpt-oss-120b",
            "GROQ_API_ENDPOINT": f"http://127.0.0.1:{self.server.server_address[1]}/v1", "OPSLENS_DIAG_LOCAL_BACKUP": "false",
            "EDGE_LLM_URL": "http://127.0.0.1:9", "OPSLENS_LOCAL_CONNECT_TIMEOUT": "0.2"})
        self.env.start()
        from api.routes import create_app
        from orchestrator import OpsLensOrchestrator
        self.orch = OpsLensOrchestrator(db=Database(db_path=Path(self.tmp.name) / "a.db"))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(orchestrator=self.orch)), base_url="http://t", timeout=60)
        await self.client.post("/api/scenarios/run", json={"scenario_id": "wet_well_high_level", "speed_multiplier": 2000})
        for _ in range(500):
            status = (await self.client.get("/api/scenarios/status")).json()
            if status["status"] not in ("running", "recovering"):
                break
            await asyncio.sleep(0.02)
        self.event_id = status["event_id"]

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.orch.stop()
        self.env.stop()
        POOL.reset()
        get_lifecycle_manager().clear_all()
        self.tmp.cleanup()

    async def ask(self, question):
        response = await self.client.post("/api/assistant/ask", json={"event_id": self.event_id, "question": question})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_ai_answer_from_the_diagnosis_facts(self):
        _Groq.reply = ("The most likely cause is a clog on P-102 together with the P-101 thermal overload trip.\n"
                       "- P-102 flow reached 90 m³/h, below the SOP limit of 300 m³/h.\n"
                       "- Wet well level reached 3.85 m.\n"
                       "- Next, follow SOP step 8 (p.14) before any reset.")
        result = await self.ask("Why did the wet well level rise?")
        self.assertTrue(result["llm_used"], result.get("fallback_reason"))
        self.assertFalse(result["fallback_used"])
        self.assertEqual(result["answer_source"], "ai_analysis")
        self.assertIn("clog on P-102", result["question_answer"])
        prompt = _Groq.bodies[-1]["messages"][1]["content"]
        self.assertIn("Cause candidates, ordered by each one's own evidence (NOT a probability)", prompt)
        self.assertIn("SOP step", prompt)
        self.assertIn("OPERATOR QUESTION: Why did the wet well level rise?", prompt)
        self.assertEqual(_Groq.bodies[-1]["model"], "openai/gpt-oss-120b")

    async def test_invented_number_is_removed_not_shown(self):
        _Groq.reply = ("Both pumps have lost capacity.\n- P-102 flow reached 90 m³/h.\n- The level will overflow in 7.77 minutes.")
        result = await self.ask("How long until it overflows?")
        self.assertTrue(result["llm_used"])
        self.assertNotIn("7.77", result["question_answer"])
        removed = result["provider_metadata"]["ai_analysis"]["removed_lines"]
        self.assertTrue(any("7.77" in r["line"] for r in removed))

    async def test_mostly_invented_answer_falls_back_to_the_evidence_answer(self):
        _Groq.reply = "- Level 7.71 m.\n- Flow 812.3 m³/h.\n- Pressure 9.13 bar.\n- Current 55.5 A."
        result = await self.ask("What is happening?")
        self.assertFalse(result["llm_used"])
        self.assertTrue(result["fallback_used"])

    async def test_stream_sends_only_checked_lines_before_the_final_answer(self):
        _Groq.reply = ("Bottom line: P-102 is likely clogged and P-101 has tripped.\n"
                       "- P-102 flow reached 90 m³/h.\n- The level will overflow in 7.77 minutes.\n- Follow SOP step 8 (p.14).")
        body = (await self.client.post("/api/assistant/ask/stream", json={"event_id": self.event_id, "question": "What is going on?"})).text
        events = [(b.split("\n")[0][7:], json.loads(b.split("data: ", 1)[1])) for b in body.strip().split("\n\n") if b.startswith("event: ")]
        tokens = [d["text"] for name, d in events if name == "token"]
        self.assertGreaterEqual(len(tokens), 3)  # line by line, not one block
        self.assertTrue(tokens[0].startswith("Bottom line:"))
        self.assertFalse(any("7.77" in t for t in tokens))
        names = [name for name, _ in events]
        self.assertLess(names.index("token"), names.index("done"))
        done = events[-1][1]
        self.assertTrue(done["llm_used"])
        self.assertTrue(done["streamed"])

    async def test_groq_down_falls_back(self):
        _Groq.fail = True
        result = await self.ask("What is happening?")
        self.assertFalse(result["llm_used"])
        self.assertTrue(result["answer"])

    async def test_worked_out_values_are_kept(self):
        # A difference from the limit, a percentage and a rounded value come from the record.
        _Groq.reply = ("Bottom line: P-102 is likely clogged.\n"
                       "- P-102 flow reached 90 m³/h, 210 m³/h below the SOP limit of 300 m³/h.\n"
                       "- Wet well level reached 3.85 m, about 3.9 m.\n"
                       "- Hypothesis: a blockage in the P-102 discharge line.")
        result = await self.ask("Compare the evidence for the causes.")
        self.assertTrue(result["llm_used"], result.get("fallback_reason"))
        self.assertIn("210 m³/h below", result["question_answer"])
        self.assertIn("Hypothesis: a blockage", result["question_answer"])
        self.assertEqual(result["provider_metadata"]["ai_analysis"]["removed_lines"], [])

    async def test_half_unchecked_answer_is_still_shown_with_a_note(self):
        _Groq.reply = ("Bottom line: P-102 is likely clogged.\n- P-102 flow reached 90 m³/h.\n- Wet well level reached 3.85 m.\n"
                       "- Flow 812.3 m³/h.\n- Pressure 9.13 bar.")
        result = await self.ask("What is happening?")
        self.assertTrue(result["llm_used"], result.get("fallback_reason"))
        self.assertNotIn("812.3", result["question_answer"])
        self.assertIn("2 statements removed because they could not be checked", result["question_answer"])

    async def test_fallback_says_why_and_is_readable(self):
        _Groq.reply = "- Level 7.71 m.\n- Flow 812.3 m³/h.\n- Pressure 9.13 bar.\n- Current 55.5 A."
        result = await self.ask("Why did the level rise?")
        self.assertEqual(result["fallback_reason"], "ai_answer_unchecked")
        answer = result["answer"]
        for part in ("Bottom line:", "Confirmed in the record:", "Possible causes (hypotheses, not confirmed):", "the AI answer could not be checked"):
            self.assertIn(part, answer)
        self.assertNotRegex(answer, r"\d{4}-\d{2}-\d{2}T")  # no raw ISO timestamps
        self.assertNotIn("numeric reading was recorded during the first", answer)

    async def test_rate_limit_is_reported_and_not_cached(self):
        _Groq.fail = True
        first = await self.ask("Is it getting worse?")
        self.assertTrue(first["fallback_used"])
        self.assertIn(first["fallback_reason"], ("unavailable", "rate_limit", "timeout", "unreachable"))
        _Groq.fail = False
        _Groq.reply = "Bottom line: P-102 is likely clogged.\n- P-102 flow reached 90 m³/h."
        second = await self.ask("Is it getting worse?")
        self.assertTrue(second["llm_used"], "a fallback must not be served from the cache")


if __name__ == "__main__":
    unittest.main()
