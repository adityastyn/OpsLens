"""Shift handover written for the incoming operator (handover_view)."""
import unittest

import handover_view as hv


class HandoverView(unittest.TestCase):
    def test_acknowledgements_are_grouped_without_ids(self):
        actions = [{"action_type": "ACKNOWLEDGE", "operator": "Ravi", "timestamp": f"2026-11-11T10:1{i}:00Z",
                    "notes": f"Alarm ID: 27248b14-bff4-4a39-a737-29{i}. OpsLens operator acknowledgement; no PLC command issued."}
                   for i in range(6)]
        actions.append({"action_type": "INSPECT", "operator": "Ravi", "timestamp": "2026-11-11T10:30:00Z", "notes": "Walked the wet well."})
        done = [g["text"] for g in hv.group_actions(actions)]
        self.assertEqual(len(done), 2)
        self.assertRegex(done[0], r"^Ravi acknowledged 6 alarms at \d\d:\d\d–\d\d:\d\d \(in OpsLens; not sent to SCADA or the PLC\)$")
        self.assertNotIn("27248b14", " ".join(done))
        self.assertRegex(done[1], r"^Ravi inspected at \d\d:\d\d: Walked the wet well\.$")

    def test_now_says_when_everything_is_back_to_normal(self):
        lines = hv.now_lines([{"label": "Holder pressure", "unit": "mbar", "now_value": 22, "normal_text": "15 to 25 mbar", "state_now": "within normal"}])
        self.assertEqual(lines, ["All key readings are back within normal:", "Holder pressure 22 mbar (normal 15 to 25 mbar)"])

    def test_now_lists_abnormal_readings_with_their_normal_range(self):
        readings = [
            {"label": "Wet well level", "unit": "m", "now_value": 3.85, "normal_text": "1.0 to 2.4 m", "state_now": "LIMIT EXCEEDED"},
            {"label": "UV transmittance", "unit": "%", "now_value": 65, "normal_text": "55 to 75 %", "state_now": "within normal"},
            {"label": "P-101 motor current", "unit": "A", "now_value": 0, "normal_low": 28, "normal_text": "28 to 34 A", "state_now": "outside normal"},
        ]
        self.assertEqual(hv.now_lines(readings), ["Wet well level 3.85 m (normal 1.0 to 2.4 m)",
                                                  "P-101 motor current 0 A (normal 28 to 34 A), stopped"])

    def test_escalation_comes_from_sop_notify_limits_or_says_no_sop(self):
        brief = {"sop": {"sop_id": "SOP-LFT-001"}, "incident_readings": [
            {"label": "Wet well level", "limit_text": "3.85 m", "limit_action": "Notify the Plant Manager", "limit_page": 15},
            {"label": "P-102 flow", "limit_text": "300 m³/h low", "limit_action": "Suspect clog", "limit_page": 14}]}
        self.assertEqual(hv.escalation(brief), ["If wet well level reaches 3.85 m: Notify the Plant Manager (SOP-LFT-001 p.15)."])
        self.assertEqual(hv.escalation({"sop": {}, "incident_readings": []}),
                         ["No SOP covers this alarm: escalate to the shift supervisor."])
        self.assertEqual(hv.escalation({"sop": {"sop_id": "SOP-NONE"}, "incident_readings": []}),
                         ["No SOP covers this alarm: escalate to the shift supervisor."])
        long = {"sop": {"sop_id": "SOP-LFT-001"}, "incident_readings": [{"label": "Wet well level", "limit_page": 15,
                "limit_text": "If the level reaches 3.85 m or a spill is imminent, the event is P1. Notify the Plant Manager.",
                "limit_action": "Notify the Plant Manager"}]}
        self.assertEqual(hv.escalation(long), ["If the level reaches 3.85 m or a spill is imminent, the event is P1. "
                                               "Notify the Plant Manager (SOP-LFT-001 p.15)."])

    def test_recovery_checks_in_operator_words(self):
        self.assertEqual(hv.checks(["ALM-UV-901-INT-LO reads FALSE (returned to normal) at a timestamp later than the alarm, on the operator HMI.",
                                    "Verify motor current 28 to 34 A. (page 15)"]),
                         ["Confirm alarm ALM-UV-901-INT-LO has cleared on the HMI.", "Verify motor current 28 to 34 A. (page 15)"])

    def test_rows_are_ordered_numbered_and_repeats_flagged(self):
        rows = hv.number_rows([
            {"alarm_description": "UV intensity low", "severity": "High", "alarm_active": True},
            {"alarm_description": "Gas holder pressure high", "severity": "High", "alarm_active": False},
            {"alarm_description": "Wet well level high-high", "severity": "Critical", "alarm_active": True},
            {"alarm_description": "Wet well level high-high", "severity": "Critical", "alarm_active": True},
        ])
        self.assertEqual([r["alarm_description"] for r in rows],
                         ["Wet well level high-high", "Wet well level high-high", "UV intensity low", "Gas holder pressure high"])
        self.assertEqual([r["number"] for r in rows], [1, 2, 3, 4])
        self.assertIn("2 open incidents for this alarm", rows[0]["repeat_note"])
        self.assertIsNone(rows[2]["repeat_note"])

    def test_narrative_is_plant_status_then_review_first(self):
        rows = hv.number_rows([
            {"alarm_description": "Wet well level high-high", "equipment": "LiftStation-01", "severity": "Critical", "alarm_active": True,
             "status_line": hv.status_line(True, False)},
            {"alarm_description": "Gas holder pressure high", "equipment": "BiogasSystem-01", "severity": "High", "alarm_active": False,
             "status_line": hv.status_line(False, True)},
        ])
        text = hv.shift_narrative_text({"incident_handover": rows, "open_alarms": [{}] * 11, "unacknowledged_alarms": [{}] * 6,
                                        "events_in_shift": ["e1"], "actions_in_shift": [{}] * 4,
                                        "alarms_acknowledged_in_shift": [{}] * 4, "alarms_resolved_in_shift": []})
        lines = text.split("\n")
        self.assertEqual(lines[0], "Plant status: 2 open incidents (1 critical, 1 high). Alarms: 11 active, 6 not acknowledged.")
        self.assertEqual(lines[1], "This shift: 1 new incident, 4 alarm acknowledgements, 0 resolved, 4 operator actions.")
        self.assertEqual(lines[3], "1. Wet well level high-high · LiftStation-01 · Critical · Alarm still active, not acknowledged")
        self.assertIn("back to normal", lines[4])
        self.assertTrue(lines[-1].startswith("Not tracked by OpsLens"))


if __name__ == "__main__":
    unittest.main()
