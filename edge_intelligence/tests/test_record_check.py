"""The record check: worked-out values pass, made-up ones do not."""
import unittest
from unittest.mock import patch

import ai_diagnosis

FACTS = {
    "event_id": "e1", "incident_tags": ["LDT-110-SCR", "FIT-110-IN", "SCR-110-MOT-CUR"],
    "readings": [
        {"tag": "LDT-110-SCR", "name": "Differential level across BS-2", "unit": "cm", "normal": "5 to 15 cm",
         "worst": 52, "worst_at": "17:02:40 UTC", "now": 9, "limit": "30 cm (high)"},
        {"tag": "FIT-110-IN", "name": "Inlet flow vs one-screen capacity", "unit": "m³/h", "normal": "900 to 1,200 m³/h",
         "worst": 2150, "worst_at": "17:02:20 UTC", "now": 1480},
        {"tag": "SCR-110-MOT-CUR", "name": "Rake motor current", "unit": "A", "normal": "6 to 9 A",
         "worst": 15.2, "worst_at": "17:02:34 UTC", "now": 7.5},
    ],
    "sop": {"sop_id": "SOP-INL-001", "steps": [{"n": i, "text": "Check.", "page": 12} for i in range(1, 9)]},
}


class RecordCheck(unittest.TestCase):
    def setUp(self):
        # Only the facts matter here; the prompt text adds nothing to check against.
        self.prompt = patch.object(ai_diagnosis, "build_prompt", return_value="")
        self.prompt.start()
        self.addCleanup(self.prompt.stop)
        self.check = ai_diagnosis.line_checker(FACTS)

    def assertKept(self, line):
        self.assertEqual(self.check(line), [], line)

    def assertRemoved(self, line):
        self.assertNotEqual(self.check(line), [], line)

    def test_worked_out_values_pass(self):
        self.assertKept("Differential level across BS-2 reached 52 cm, about 37 cm above the normal maximum of 15 cm.")
        self.assertKept("Inlet flow rose to 2,150 m³/h, roughly 79% above the 1,200 m³/h normal maximum.")
        self.assertKept("Differential level is 3.5 times the normal maximum.")
        self.assertKept("Inlet flow was about 2,100 m³/h when the alarm came in.")
        self.assertKept("The level peaked at 17:02 and the flow 20 seconds earlier.")
        self.assertKept("Hypothesis: rags blocked the screen and overloaded the rake motor.")

    def test_made_up_values_fail(self):
        self.assertRemoved("Inlet flow reached 3,400 m³/h.")
        self.assertRemoved("Rake motor current reached 47 A.")
        self.assertRemoved("Differential level across BS-2 is about 75 cm.")
        self.assertRemoved("The rake motor tripped at 16:41.")
        self.assertRemoved("Check FIT-999-XX flow.")
        self.assertRemoved("Follow SOP-INL-001 step 12.")

    def test_answer_with_some_removed_lines_is_still_valid(self):
        headings = "\n".join(f"## {s}\n- Differential level across BS-2 reached 52 cm." for s in ai_diagnosis.SECTIONS)
        text = headings + "\n- Flow 812.3 m³/h.\n- Pressure 9.13 bar."
        result = ai_diagnosis.validate(text, FACTS)
        self.assertTrue(result["valid"], result["rejected_reasons"])
        self.assertEqual(len(result["removed_lines"]), 2)

    def test_mostly_made_up_answer_is_rejected(self):
        headings = "\n".join(f"## {s}\n- Flow {800 + i}.3 m³/h." for i, s in enumerate(ai_diagnosis.SECTIONS))
        result = ai_diagnosis.validate(headings, FACTS)
        self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main()
