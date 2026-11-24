"""Groq as the diagnosis provider, against a local stand-in for its API."""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import ai_diagnosis

FACTS = {"event_id": "e1", "incident": "Test", "data_source": "SIMULATED", "primary_alarm": {"tag": "ALM-1", "description": "Test alarm", "first_at": "10:00:00", "input_active": True},
         "active_alarms": ["ALM-1"], "incident_tags": ["ALM-1"], "readings": [], "sequence": [], "sop": None, "missing": []}
ANSWER = """## 1. What is happening
- CONFIRMED: Test alarm ALM-1 is recorded.
## 2. Evidence supporting the diagnosis
- CONFIRMED: ALM-1 is active.
## 3. Possible causes
- HYPOTHESIS: unknown.
## 4. Recommended checks from SOP
- No directly matching approved SOP was found for this incident.
## 5. Additional advisory checks
- Not in the SOP: compare the runtime with the field.
## 6. Missing information
- UNKNOWN: field confirmation.
## 7. Confidence and limitations
- The cause is a hypothesis.
"""


class _Groq(BaseHTTPRequestHandler):
    mode, bodies, calls, answer, repair = "ok", [], 0, None, None

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).bodies.append(body)
        type(self).calls += 1
        if type(self).mode == "limit" or (type(self).mode == "limit_once" and type(self).calls == 1):
            self.send_response(429)
            self.send_header("retry-after", "0.01")
            self.end_headers()
            return
        if type(self).mode == "no_options" and "reasoning_effort" in body:
            self.send_response(400)
            self.end_headers()
            return
        if type(self).mode == "bad_key":
            self.send_response(401)
            self.end_headers()
            return
        if not body.get("stream"):
            content = type(self).repair or "1) DROP"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
            return
        self.send_response(200)
        self.end_headers()
        text, finish = (type(self).answer or ANSWER), "stop"
        if type(self).mode == "cut_first" and sum(1 for b in type(self).bodies if b.get("stream")) == 1:
            text, finish = text.split("## 4.")[0], "length"  # thinking used up the output limit
        for word in text.split(" "):
            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": word + " "}}]}) + "\n\n").encode())
        self.wfile.write(('data: {"choices":[{"delta":{},"finish_reason":"%s"}]}\n\ndata: [DONE]\n\n' % finish).encode())


class GroqDiagnosis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Groq)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        from groq_keys import POOL
        POOL.reset()
        ai_diagnosis.CACHE.clear()
        _Groq.mode, _Groq.bodies, _Groq.calls, _Groq.answer, _Groq.repair = "ok", [], 0, None, None
        self.env = patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "groq", "GROQ_API_KEY": "test", "EDGE_LLM_MODEL": "qwen2.5:3b",
                                           "EDGE_LLM_URL": "http://127.0.0.1:9", "OPSLENS_LOCAL_CONNECT_TIMEOUT": "0.2",
                                           "GROQ_API_ENDPOINT": f"http://127.0.0.1:{self.server.server_address[1]}/v1"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def run_once(self, key):
        facts = {**FACTS, "event_id": key}
        return [e for e in ai_diagnosis.stream_diagnosis_from_facts(facts) if e["type"] == "done"][0]

    def test_answer_comes_from_groq_with_its_own_model(self):
        done = self.run_once("a")
        self.assertEqual(done["engine"], "groq", done.get("fallback_reason"))
        self.assertEqual(done["source_status"], "ai_generated")
        self.assertIn("via Groq (cloud)", done["label"])
        self.assertEqual(_Groq.bodies[0]["model"], "openai/gpt-oss-120b")  # never the local EDGE_LLM_MODEL

    def test_waits_once_on_the_limit(self):
        _Groq.mode = "limit_once"
        self.assertEqual(self.run_once("b")["engine"], "groq")
        self.assertEqual(_Groq.calls, 2)

    def test_limit_tries_lm_studio_then_gives_a_labelled_fallback(self):
        _Groq.mode = "limit"
        done = self.run_once("c")
        self.assertEqual(done["failure_kind"], "rate_limit")
        self.assertIn("Retry", done["fix_hint"])
        self.assertIn("Backup: LM Studio unavailable", done["fallback_reason"])

    def test_limit_with_lm_studio_running_answers_from_lm_studio(self):
        import threading
        from http.server import ThreadingHTTPServer
        from tests.fake_lmstudio import MODEL_ID, FakeLMStudio

        class _Local(FakeLMStudio):
            reply = ANSWER
        local = ThreadingHTTPServer(("127.0.0.1", 0), _Local)
        threading.Thread(target=local.serve_forever, daemon=True).start()
        try:
            _Groq.mode = "limit"
            with patch.dict(os.environ, {"EDGE_LLM_URL": f"http://127.0.0.1:{local.server_address[1]}/v1", "EDGE_LLM_MODEL": MODEL_ID}):
                done = self.run_once("c2")
            self.assertEqual(done["engine"], "lmstudio", done.get("fallback_reason"))
            self.assertIn("via LM Studio (local)", done["label"])
        finally:
            local.shutdown()

    def test_answer_cut_off_by_the_output_limit_is_asked_again_with_low_reasoning(self):
        _Groq.mode = "cut_first"
        events = [e for e in ai_diagnosis.stream_diagnosis_from_facts({**FACTS, "event_id": "cut"})]
        done = [e for e in events if e["type"] == "done"][0]
        self.assertEqual(done["source_status"], "ai_generated", done.get("fallback_reason"))
        self.assertIn("Confidence and limitations", done["text"] if "text" in done else json.dumps(done))
        streamed = [b for b in _Groq.bodies if b.get("stream")]
        self.assertEqual(len(streamed), 2)
        self.assertEqual(streamed[0]["reasoning_effort"], "medium")
        self.assertEqual(streamed[1]["reasoning_effort"], "low")
        self.assertIn("retrying", [e.get("stage") for e in events if e["type"] == "status"])

    def test_non_breaking_hyphens_from_the_model_are_not_invented_numbers(self):
        facts = {**FACTS, "incident_tags": ["ALM-1", "P101-FAULT"]}
        nb = "\u2011"
        line = f"- CONFIRMED: P{nb}101 tripped and P{nb}102 runs at low flow."
        self.assertEqual(ai_diagnosis.line_checker(facts)(line), [])
        check = ai_diagnosis.validate(ANSWER.replace("Test alarm ALM-1 is recorded.", line[len("- CONFIRMED: "):]), facts)
        self.assertEqual(check["removed_lines"], [])
        self.assertNotIn(nb, check["clean_text"])

    def test_bad_key_is_named(self):
        _Groq.mode = "bad_key"
        self.assertEqual(self.run_once("d")["failure_kind"], "auth")

    def test_model_without_reasoning_options_still_answers(self):
        _Groq.mode = "no_options"
        self.assertEqual(self.run_once("e")["engine"], "groq")

    def test_no_internet_and_no_local_model_is_labelled(self):
        with patch.dict(os.environ, {"GROQ_API_ENDPOINT": "http://127.0.0.1:9/v1"}):
            done = self.run_once("f")
        self.assertEqual(done["engine"], "deterministic_fallback")
        self.assertIn("Groq unavailable", done["fallback_reason"])
        self.assertIn("Backup: LM Studio unavailable", done["fallback_reason"])


    def test_reasoning_is_medium_by_default(self):
        self.run_once("g")
        self.assertEqual(_Groq.bodies[0]["reasoning_effort"], "medium")

    def test_removed_line_is_repaired_and_checked_again(self):
        _Groq.answer = ANSWER.replace("- CONFIRMED: ALM-1 is active.", "- CONFIRMED: ALM-1 is active.\n- CONFIRMED: level reached 123.4 m.")
        _Groq.repair = "1) CONFIRMED: Test alarm ALM-1 is recorded as active."
        done = self.run_once("h")
        self.assertEqual(done["validation"]["repaired_lines"], 1)
        self.assertIn("recorded as active", done["text"])
        self.assertNotIn("123.4", done["text"])
        self.assertEqual(len(done["validation"]["removed_lines"]), 1)

    def test_a_repair_that_invents_a_number_is_not_put_back(self):
        _Groq.answer = ANSWER.replace("- CONFIRMED: ALM-1 is active.", "- CONFIRMED: ALM-1 is active.\n- CONFIRMED: level reached 123.4 m.")
        _Groq.repair = "1) CONFIRMED: level reached 456.7 m."
        done = self.run_once("i")
        self.assertNotIn("456.7", done["text"])
        self.assertNotIn("repaired_lines", done["validation"])


if __name__ == "__main__":
    unittest.main()
