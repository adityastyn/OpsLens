"""Ask OpsLens on Groq: the Groq model (not the local backup model), several
keys in turn, and low reasoning. Against a local mock of the Groq API."""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import llm_provider
from groq_keys import POOL


class _Mock(BaseHTTPRequestHandler):
    bodies, keys = [], []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        key = self.headers["Authorization"][7:]
        type(self).bodies.append(body)
        type(self).keys.append(key)
        if key.startswith("limited"):
            self.send_response(429)
            self.end_headers()
            return
        if key.startswith("bad"):
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        if body.get("stream"):
            for word in ("Check ", "both ", "pumps."):
                self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": word}}]}) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self.wfile.write(json.dumps({"choices": [{"message": {"content": '{"answer": "Check both pumps."}'}}]}).encode())


class AskOpsLensGroq(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Mock)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        POOL.reset()
        _Mock.bodies, _Mock.keys = [], []
        self.env = patch.dict(os.environ, {
            "OPSLENS_LLM_PROVIDER": "groq", "GROQ_API_KEY": "", "GROQ_API_KEYS": "limited-1,bad-2,ok-3",
            "EDGE_LLM_MODEL": "qwen2.5:3b", "OPSLENS_GROQ_MODEL": "openai/gpt-oss-120b", "OPSLENS_LLM_MODEL": "",
            "GROQ_API_ENDPOINT": f"http://127.0.0.1:{self.server.server_address[1]}/v1"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        POOL.reset()

    def test_uses_the_groq_model_not_the_local_backup(self):
        self.assertEqual(llm_provider.llm_model(), "openai/gpt-oss-120b")

    def test_answer_comes_from_the_working_key(self):
        text = llm_provider.complete_chat("question")
        self.assertIn("Check both pumps", text)
        self.assertEqual(_Mock.keys, ["limited-1", "bad-2", "ok-3"])
        self.assertEqual(_Mock.bodies[-1]["model"], "openai/gpt-oss-120b")
        self.assertEqual(_Mock.bodies[-1]["reasoning_effort"], "low")

    def test_streaming_answer_moves_past_bad_keys(self):
        self.assertEqual("".join(llm_provider.stream_chat("question")), "Check both pumps.")
        self.assertEqual(_Mock.keys[-1], "ok-3")

    def test_every_key_failing_is_reported(self):
        with patch.dict(os.environ, {"GROQ_API_KEYS": "limited-1,limited-2"}):
            POOL.reset()
            with self.assertRaises(llm_provider.ProviderError) as caught:
                llm_provider.complete_chat("question")
        self.assertEqual(caught.exception.kind, "rate_limit")


if __name__ == "__main__":
    unittest.main()
