"""Several Groq keys, tested against a local mock of the Groq API.
Each mock key behaves as its name says: ok-*, limited-*, slow-limited-*, bad-*."""
import json
import logging
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import ai_diagnosis
from groq_keys import POOL
from tests.test_groq_diagnosis import ANSWER, FACTS


class _MockGroq(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        key = self.headers.get("Authorization", "").replace("Bearer ", "")
        type(self).seen.append(key)
        if key.startswith("limited"):
            self.send_response(429)
            self.send_header("retry-after", "120")
            self.end_headers()
            return
        if key.startswith("slow-limited") and type(self).seen.count(key) == 1:
            self.send_response(429)
            self.send_header("retry-after", "0.05")
            self.end_headers()
            return
        if key.startswith("bad"):
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        if not body.get("stream"):
            self.wfile.write(json.dumps({"choices": [{"message": {"content": "1) DROP"}}]}).encode())
            return
        for word in ANSWER.split(" "):
            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": word + " "}}]}) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")


class GroqKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _MockGroq)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        POOL.reset()
        ai_diagnosis.CACHE.clear()
        _MockGroq.seen = []
        self.base = {"OPSLENS_LLM_PROVIDER": "groq", "GROQ_API_KEY": "", "EDGE_LLM_URL": "http://127.0.0.1:9",
                     "OPSLENS_LOCAL_CONNECT_TIMEOUT": "0.2", "OPSLENS_DIAG_LOCAL_BACKUP": "false",
                     "GROQ_API_ENDPOINT": f"http://127.0.0.1:{self.server.server_address[1]}/v1"}

    def tearDown(self):
        POOL.reset()

    def run_with(self, keys, event="e"):
        with patch.dict(os.environ, {**self.base, "GROQ_API_KEYS": keys}):
            done = [e for e in ai_diagnosis.stream_diagnosis_from_facts({**FACTS, "event_id": event}) if e["type"] == "done"][0]
            return done, POOL.status()

    def test_limited_key_is_skipped_and_the_next_answers(self):
        with self.assertLogs("opslens.ai_diagnosis", logging.WARNING) as logs:
            done, status = self.run_with("limited-1,ok-2,ok-3")
        self.assertEqual(done["engine"], "groq", done.get("fallback_reason"))
        self.assertEqual(_MockGroq.seen[:2], ["limited-1", "ok-2"])
        self.assertTrue(status["keys"][0]["state"].startswith("rate-limited"))
        self.assertIn("key 1 of 3 rate-limited", " ".join(logs.output))
        self.assertNotIn("limited-1", " ".join(logs.output))  # key values never logged

    def test_cooling_key_is_not_tried_again(self):
        self.run_with("limited-1,ok-2,ok-3", "a")
        _MockGroq.seen = []
        self.run_with("limited-1,ok-2,ok-3", "b")
        self.assertNotIn("limited-1", _MockGroq.seen)  # still cooling down
        self.assertEqual(_MockGroq.seen[0], "ok-3")  # the load moves on to the next key
        self.assertEqual(self.run_with("limited-1,ok-2,ok-3", "c")[0]["engine"], "groq")

    def test_rejected_key_is_set_aside(self):
        done, status = self.run_with("bad-1,ok-2")
        self.assertEqual(done["engine"], "groq")
        self.assertEqual(status["keys"][0]["state"], "rejected")

    def test_all_keys_limited_gives_the_labelled_fallback(self):
        done, _ = self.run_with("limited-1,limited-2,limited-3")
        self.assertEqual(done["failure_kind"], "rate_limit")
        self.assertIn("All 3 Groq keys are rate-limited", done["fallback_reason"])
        self.assertEqual(done["source_status"], "ai_unavailable")

    def test_all_keys_rejected(self):
        done, _ = self.run_with("bad-1,bad-2")
        self.assertEqual(done["failure_kind"], "auth")

    def test_short_limit_waits_once_then_answers(self):
        done, _ = self.run_with("slow-limited-1")
        self.assertEqual(done["engine"], "groq")
        self.assertEqual(_MockGroq.seen, ["slow-limited-1", "slow-limited-1"])

    def test_single_key_setting_still_works(self):
        with patch.dict(os.environ, {**self.base, "GROQ_API_KEY": "ok-1", "GROQ_API_KEYS": ""}):
            done = [e for e in ai_diagnosis.stream_diagnosis_from_facts({**FACTS, "event_id": "s"}) if e["type"] == "done"][0]
        self.assertEqual(done["engine"], "groq")

    def test_status_names_keys_by_position_only(self):
        _, status = self.run_with("limited-1,ok-2")
        self.assertEqual([k["key"] for k in status["keys"]], ["key 1", "key 2"])
        self.assertNotIn("ok-2", json.dumps(status))


if __name__ == "__main__":
    unittest.main()
