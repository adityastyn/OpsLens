"""Alarm priorities (HIGH / MEDIUM / LOW), the LOW advisory alarms, and the
facts behind the incident order."""
import unittest

import incident_catalog as catalog
from alarm_urgency import rank_incidents
from tag_registry import TAG_REGISTRY


class AlarmPriorities(unittest.TestCase):
    def test_every_alarm_input_has_a_priority(self):
        for tag, alarm in catalog.alarm_inputs().items():
            self.assertIn(alarm["priority"], ("HIGH", "MEDIUM", "LOW"), tag)

    def test_primary_alarms_follow_incident_severity_and_trips_are_high(self):
        for spec in catalog.SCENARIOS:
            want = {"critical": "HIGH", "high": "HIGH", "medium": "MEDIUM"}[spec.severity.lower()]
            self.assertEqual(catalog.alarm_priority(spec.primary_alarm), want, spec.scenario_id)
            for st in spec.statuses:
                if st.kind == "fault":
                    self.assertEqual(catalog.alarm_priority(st.tag), "HIGH", st.tag)

    def test_every_scenario_has_a_low_advisory_alarm_its_incident_reaches(self):
        for spec in catalog.SCENARIOS:
            advisories = [(sig, lim) for sig in spec.signals for lim in sig.advisories]
            self.assertTrue(advisories, spec.scenario_id)
            for sig, lim in advisories:
                self.assertEqual(catalog.alarm_priority(lim.alarm_tag), "LOW")
                lo, hi = sig.normal
                crossed = sig.incident >= lim.value if lim.direction == "high" else sig.incident <= lim.value
                self.assertTrue(crossed, f"{lim.alarm_tag} is never reached by {sig.tag}")
                outside_normal = lim.value > (hi if hi is not None else lim.value - 1) if lim.direction == "high" else lim.value < (lo if lo is not None else lim.value + 1)
                self.assertTrue(outside_normal, f"{lim.alarm_tag} would fire inside the normal range")
                back = sig.after < lim.value if lim.direction == "high" else sig.after > lim.value
                self.assertTrue(back, f"{lim.alarm_tag} would stay on after the fix")

    def test_advisories_are_alarm_inputs_not_limits_and_never_primary(self):
        for spec in catalog.SCENARIOS:
            for sig in spec.signals:
                limit_values = {(l.value, l.direction) for l in catalog.plant_signals()[sig.tag].limits}
                for lim in sig.advisories:
                    self.assertNotIn((lim.value, lim.direction), limit_values)
                    self.assertLess(TAG_REGISTRY[lim.alarm_tag].severity_weight, TAG_REGISTRY[spec.primary_alarm].severity_weight)
                    self.assertIn(lim.alarm_tag, catalog.scenario_tags(spec))


class RankingFacts(unittest.TestCase):
    def incident(self, eid, score, band="URGENT", live=True, ts="2026-11-11T10:00:00Z", rules=()):
        state = "ACTIVE" if live else "CLEARED"
        return {"event_id": eid, "display_equipment": eid, "trigger_timestamp": ts,
                "individual_alarms": [{"tag": "A", "lifecycle_state": state}, {"tag": "B", "lifecycle_state": state}],
                "urgency": {"urgency_score": score, "urgency_band": band, "triggered_rules": list(rules)}}

    def test_each_incident_gets_its_facts_and_why_it_is_above_the_next(self):
        ranked = rank_incidents([
            self.incident("UVChannel-01", 70, band="ELEVATED"),
            self.incident("LiftStation-01", 90, rules=["HARD_RULE_TRIP_LIMIT_BREACH"]),
            self.incident("BiogasSystem-01", 80, live=False),
        ])
        self.assertEqual([i["event_id"] for i in ranked], ["LiftStation-01", "UVChannel-01", "BiogasSystem-01"])
        top = ranked[0]
        self.assertEqual(top["ranking_facts"]["active_alarms"], 2)
        self.assertEqual(top["ranking_facts"]["safety_rules"], ["a trip limit is breached"])
        self.assertIn("UVChannel-01", top["ranking_vs_next"])
        self.assertIn("a trip limit is breached", top["ranking_vs_next"])
        self.assertIn("returned to normal", ranked[1]["ranking_vs_next"])
        self.assertFalse(ranked[2]["ranking_facts"]["live"])
        self.assertIsNone(ranked[2]["ranking_vs_next"])


if __name__ == "__main__":
    unittest.main()
