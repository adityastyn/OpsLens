"""Possible causes come from the record (trips, SOP 'suspect' limits, early
changes), and a strong one cannot vanish from the AI answer."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_diagnosis
from alarm_lifecycle import get_lifecycle_manager
from database import Database
from generator import SyntheticDataGenerator
from orchestrator import OpsLensOrchestrator


async def _event(scenario_id, db, orch):
    before = {c.event_id for c in db.get_candidate_events()}
    async for r in SyntheticDataGenerator(db=db, sample_interval=2.0).run_scenario_async(scenario_id, speed_multiplier=float("inf")):
        await orch.process_reading(r)
    while orch._in_flight_tasks:
        await asyncio.gather(*list(orch._in_flight_tasks.values()), return_exceptions=True)
    for c in db.get_candidate_events():
        if c.event_id not in before:
            await orch.process_candidate_event(c)
    while orch._in_flight_tasks:
        await asyncio.gather(*list(orch._in_flight_tasks.values()), return_exceptions=True)
    new = [c for c in db.get_candidate_events() if c.event_id not in before]
    return orch.get_full_event(new[0].event_id)


class Candidates(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        get_lifecycle_manager().clear_all()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "none"})
        self.env.start()
        self.db = Database(db_path=Path(self.tmp.name) / "c.db")
        self.orch = OpsLensOrchestrator(db=self.db)

    async def asyncTearDown(self):
        await self.orch.stop()
        self.env.stop()
        get_lifecycle_manager().clear_all()
        self.tmp.cleanup()

    async def candidates(self, scenario_id):
        event = await _event(scenario_id, self.db, self.orch)
        return ai_diagnosis.build_facts(event, self.db)

    async def test_lift_station_names_the_trip_and_the_clog(self):
        facts = await self.candidates("wet_well_high_level")
        found = facts["cause_candidates"]
        kinds = {c["kind"]: c for c in found}
        self.assertIn("P-101 thermal overload trip", kinds["trip"]["statement"])
        self.assertIn("clog", kinds["sop"]["statement"].lower())
        self.assertIn("P-102", kinds["sop"]["statement"])
        self.assertTrue(any("Suspect clog" in s for s in kinds["sop"]["supporting"]))
        self.assertTrue(all(c["status"] == "HYPOTHESIS" for c in found))
        # The alarm's own measurement is the symptom, not a cause.
        self.assertFalse(any("Wet well level outside normal" in c["statement"] for c in found))
        prompt = ai_diagnosis.build_prompt(facts)
        self.assertIn("Cause candidates, ordered by each one's own evidence (NOT a probability)", prompt)
        self.assertIn("NOT a probability", prompt)

    async def test_trips_found_from_telemetry_for_other_scenarios(self):
        for scenario_id, words in (("ras_pump_failure", "RAS-P1"), ("biogas_holder_pressure", "CHP engine trip"),
                                   ("aeration_blower_failure", "Blower B-1")):
            with self.subTest(scenario_id):
                facts = await self.candidates(scenario_id)
                self.assertTrue(any(c["kind"] == "trip" and words in c["statement"] for c in facts["cause_candidates"]),
                                [c["statement"] for c in facts["cause_candidates"]])

    async def test_standby_that_never_started_is_a_candidate(self):
        facts = await self.candidates("aeration_blower_failure")
        self.assertTrue(any(c["kind"] == "standby" and "B-3" in c["statement"] for c in facts["cause_candidates"]),
                        [c["statement"] for c in facts["cause_candidates"]])

    async def test_a_skipped_strong_cause_is_added_and_labelled(self):
        facts = await self.candidates("wet_well_high_level")
        text = "## 3. Possible causes\n- HYPOTHESIS: excess inflow.\n## 4. Recommended checks from SOP\n- x"
        out, added = ai_diagnosis.ensure_candidates(text, facts)
        self.assertEqual(len(added), 2)
        self.assertIn("Added by OpsLens from the record", out)
        self.assertLess(out.index("Added by OpsLens"), out.index("## 4."))
        written = "## 3. Possible causes\n- HYPOTHESIS: P-101 tripped on thermal overload.\n- HYPOTHESIS: P-102 clog.\n## 4. x"
        self.assertEqual(ai_diagnosis.ensure_candidates(written, facts)[1], [])

    async def test_fallback_lists_causes_and_recovery(self):
        facts = await self.candidates("wet_well_high_level")
        text = ai_diagnosis.deterministic_diagnosis(facts)
        card = ai_diagnosis.operator_card(text)
        self.assertTrue(any("P-101" in c for c in card["causes"]))
        self.assertTrue(any("2.4 m" in c for c in card["fix"]))


if __name__ == "__main__":
    unittest.main()
