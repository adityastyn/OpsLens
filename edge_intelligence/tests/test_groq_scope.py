"""Groq is used for the AI diagnosis and Ask OpsLens only."""
import os
import unittest
from unittest.mock import patch

import operational_history


class GroqScope(unittest.TestCase):
    def test_shift_summary_never_calls_groq(self):
        called = []
        with patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "groq", "GROQ_API_KEY": "x"}):
            with patch("llm_provider.complete_chat", lambda *a, **k: called.append(1) or "text"):
                result = operational_history.generate_shift_narrative({"open_alarms": []}, use_llm=True)
        self.assertEqual(called, [])
        self.assertEqual(result["source"], "deterministic")

    def test_guidance_text_never_calls_groq(self):
        from guidance_generator import GuidanceGenerator
        called = []
        with patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "groq", "GROQ_API_KEY": "x", "GEMINI_API_KEY": "", "OPENAI_API_KEY": ""}):
            with patch("llm_provider.complete_chat", lambda *a, **k: called.append(1) or "text"):
                GuidanceGenerator()._call_cloud_llm("prompt")
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
