"""The confidence level comes from an evidence score out of 100, so the two always agree."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from incident_insight import confidence  # noqa: E402

TRIP = {"kind": "trip", "statement": "P-101 thermal overload trip", "role": "leading"}
EARLY = {"kind": "early", "statement": "Inflow rose first", "role": "contributing"}


def facts(candidates, sop=None, data_source="SIM"):
    return {"cause_candidates": candidates, "sop": sop or {}, "data_source": data_source, "readings": []}


class ConfidenceScore(unittest.TestCase):
    def test_everything_met_scores_100_and_high(self):
        c = confidence(facts([TRIP], {"sop_id": "SOP-X", "coverage": "full", "approved": True}), {"same_alarm_total": 2})
        self.assertEqual(c["score"], 100)
        self.assertEqual(c["level"], "High")
        self.assertEqual(sum(x["points"] for x in c["checks"]), 100)

    def test_draft_sop_costs_its_points_only(self):
        c = confidence(facts([TRIP], {"sop_id": "SOP-X", "coverage": "full", "approved": False}), {"same_alarm_total": 2})
        self.assertEqual(c["score"], 90)
        self.assertEqual(c["level"], "High")
        self.assertIn("draft_sop", [f["flag"] for f in c["flags"]])

    def test_no_sop_and_weak_causes_is_medium_or_low(self):
        c = confidence(facts([EARLY, dict(EARLY, statement="Other")]), {"same_alarm_total": 0})
        self.assertLess(c["score"], 45)
        self.assertEqual(c["level"], "Low")

    def test_level_always_matches_score_band(self):
        for cands in ([], [EARLY], [TRIP]):
            for sop in ({}, {"sop_id": "S", "coverage": "partial"}, {"sop_id": "S", "coverage": "full", "approved": True}):
                for seen in (0, 3):
                    c = confidence(facts(cands, sop), {"same_alarm_total": seen})
                    band = "High" if c["score"] >= 75 else "Medium" if c["score"] >= 45 else "Low"
                    self.assertEqual(c["level"], "Low" if not cands else band, (cands, sop, seen, c["score"]))

    def test_stale_data_forces_low(self):
        c = confidence(facts([TRIP], {"sop_id": "S", "coverage": "full", "approved": True}, data_source="STALE"), {"same_alarm_total": 1})
        self.assertEqual(c["level"], "Low")
        self.assertIn("escalate", [f["flag"] for f in c["flags"]])

    def test_effects_are_not_counted_as_causes(self):
        c = confidence(facts([dict(TRIP, role="effect")]), {"same_alarm_total": 1})
        self.assertEqual(c["level"], "Low")

    def test_says_it_is_not_a_probability(self):
        self.assertIn("not a probability", confidence(facts([TRIP]), {})["note"])


if __name__ == "__main__":
    unittest.main()
