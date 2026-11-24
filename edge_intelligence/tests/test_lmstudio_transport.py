"""LM Studio transport tests against a local fake LM Studio server.

No real model is needed: the fake server speaks the Local Model API (OpenAI
chat format), so timeouts, health reporting, warm-up and streaming are
exercised over real HTTP."""
import json
import os
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from tests.fake_lmstudio import MODEL_ID, FakeLMStudio


class _Fake(FakeLMStudio):
    reply = json.dumps({"answer": "Wet-well level 3.8 m."})


class LMStudioTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Fake.delay, _Fake.received, _Fake.models, _Fake.loaded, _Fake.context, _Fake.status = 0.0, [], [MODEL_ID], True, 8192, 200
        _Fake.reply = json.dumps({"answer": "Wet-well level 3.8 m."})
        self.env = patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "lmstudio", "EDGE_LLM_URL": self.url + "/v1",
                                           "EDGE_LLM_MODEL": MODEL_ID, "OPSLENS_LOCAL_TIMEOUT": "5"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_health_reports_loaded_model_and_context(self):
        from edge_slm import local_health
        health = local_health()
        self.assertTrue(health["reachable"])
        self.assertTrue(health["model_installed"])
        self.assertTrue(health["model_loaded"])
        self.assertEqual(health["context_length"], 8192)
        self.assertIsNone(health["error"])
        self.assertIsNone(health["warning"])

    def test_health_warns_about_a_small_context(self):
        from edge_slm import local_health
        _Fake.context = 4096
        self.assertIn("Context Length 8192", local_health()["warning"])

    def test_health_names_the_models_when_ours_is_missing(self):
        from edge_slm import local_health
        _Fake.models = ["llama-3.2-3b-instruct"]
        health = local_health()
        self.assertEqual(health["error_kind"], "model_missing")
        self.assertIn("llama-3.2-3b-instruct", health["error"])

    def test_lm_studio_names_match_each_other(self):
        from edge_slm import model_matches
        self.assertTrue(model_matches("qwen2.5-7b-instruct", "lmstudio-community/Qwen2.5-7B-Instruct-GGUF"))
        self.assertFalse(model_matches("qwen2.5-7b-instruct", "qwen2.5-coder-7b-instruct"))

    def test_server_off_is_reported(self):
        from edge_slm import local_health
        with patch.dict(os.environ, {"EDGE_LLM_URL": "http://127.0.0.1:9/v1"}):
            health = local_health()
        self.assertEqual(health["error_kind"], "unreachable")
        self.assertIn("Local Model API", health["error"])

    def test_old_ollama_settings_still_work(self):
        from edge_slm import local_base
        from llm_provider import selected_provider
        with patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "ollama", "EDGE_LLM_URL": "http://127.0.0.1:11434"}):
            self.assertEqual(selected_provider(), "lmstudio")
            self.assertEqual(local_base(), "http://127.0.0.1:1234")

    def test_complete_chat_success(self):
        from llm_provider import complete_chat, provider_status
        text = complete_chat("prompt")
        self.assertIn("3.8", text)
        path, sent = _Fake.received[-1]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(sent["model"], MODEL_ID)
        self.assertEqual(sent["messages"][-1]["content"], "prompt")
        self.assertTrue(provider_status()["verified"])

    def test_streaming_tokens(self):
        from llm_provider import stream_chat
        _Fake.reply = "Wet-well level 3.8 m."
        self.assertEqual("".join(stream_chat("prompt")), "Wet-well level 3.8 m.")

    def test_slow_model_times_out_at_configured_budget_without_retry(self):
        from llm_provider import ProviderError, complete_chat
        _Fake.delay = 3.0
        with patch.dict(os.environ, {"OPSLENS_LOCAL_TIMEOUT": "1"}):
            started = time.monotonic()
            with self.assertRaises(ProviderError) as ctx:
                complete_chat("prompt")
            elapsed = time.monotonic() - started
        self.assertEqual(ctx.exception.kind, "timeout")
        self.assertLess(elapsed, 2.5)
        self.assertEqual(len(_Fake.received), 1)

    def test_default_budget_is_long_enough_for_a_slow_cpu_answer(self):
        from llm_provider import complete_chat, llm_timeout
        _Fake.delay = 2.0
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPSLENS_LOCAL_TIMEOUT", None)
            self.assertEqual(llm_timeout(), 60.0)
            self.assertIn("3.8", complete_chat("prompt"))

    def test_warm_up_loads_model(self):
        from edge_slm import warm_up_local
        result = warm_up_local()
        self.assertTrue(result["loaded"])
        self.assertEqual(_Fake.received[-1][1]["max_tokens"], 1)


if __name__ == "__main__":
    unittest.main()
