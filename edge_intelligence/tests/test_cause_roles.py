"""Every screen tells the same cause story, in all eight scenarios.

The leading hypothesis on Home, the Root-Cause Evidence panel, the knowledge
graph and the AI prompt all come from one ranked cause list. Readings that
changed after the leading cause are shown as likely effects, not as competing
causes. The expected words below describe what the record shows (a trip, an
SOP 'suspect' limit, a standby that never started); they are not the
simulator's scripted answer, which is never read by the cause builder.
"""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import ai_diagnosis
import incident_catalog as catalog
from alarm_lifecycle import get_lifecycle_manager
from cause_candidates import assign_roles, leading_summary
from database import Database

# scenario_id -> words the leading hypothesis must contain, and words no cause may contain.
EXPECTED = {
    # The rake trip latches after the differential alarm, so inflow over one-screen capacity leads.
    "screen_differential_pressure": (["Inlet flow vs one-screen capacity high", "BS-2 rake motor overload trip"], []),
    "wet_well_high_level": (["P-101 thermal overload trip", "clog on P-102"], []),
    "biological_foam": (["WAS (wasting) rate low", "which led to sludge age (SRT) high"], []),
    "ras_pump_failure": (["RAS-P1 thermal trip"], ["Clarifier solids loading"]),
    "biogas_holder_pressure": (["CHP engine trip", "which led to CHP gas consumption low"], ["Holder fill level", "Methane"]),
    "centrifuge_capture": (["polymer dose", "bowl speed"], ["Centrate ammonia", "Cake dryness"]),
    "aeration_blower_failure": (["Blower B-1", "which led to blower airflow low", "B-3 did not start"], []),
    "uv_disinfection_low": (["ballast fault"], ["UV dose"]),
}


class CauseRolesAcrossScenarios(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        get_lifecycle_manager().clear_all()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "none"})
        self.env.start()
        from api.routes import create_app
        from orchestrator import OpsLensOrchestrator
        self.db = Database(db_path=Path(self.tmp.name) / "c.db")
        self.orch = OpsLensOrchestrator(db=self.db)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(orchestrator=self.orch)), base_url="http://t", timeout=60)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.orch.stop()
        self.env.stop()
        get_lifecycle_manager().clear_all()
        self.tmp.cleanup()

    async def run_scenario(self, scenario_id):
        await self.client.post("/api/scenarios/run", json={"scenario_id": scenario_id, "speed_multiplier": 2000})
        for _ in range(800):
            status = (await self.client.get("/api/scenarios/status")).json()
            if status["status"] not in ("running", "recovering"):
                break
            await asyncio.sleep(0.02)
        await self.client.post("/api/scenarios/corrective-action", json={"scenario_id": scenario_id})
        return status

    async def test_every_scenario_has_one_leading_hypothesis_on_every_screen(self):
        self.assertEqual(set(EXPECTED), {s.scenario_id for s in catalog.SCENARIOS})
        for spec in catalog.SCENARIOS:
            with self.subTest(scenario=spec.scenario_id):
                status = await self.run_scenario(spec.scenario_id)
                event_id = status["event_id"]
                facts = ai_diagnosis.build_facts(self.orch.get_full_event(event_id), self.db)
                # Nothing scripted reaches the model: not the cause, not the story.
                prompt = ai_diagnosis.SYSTEM_PROMPT + ai_diagnosis.build_prompt(facts)
                for scripted in [*spec.root.values(), spec.summary, spec.who, spec.expected_outcome, spec.name, spec.scenario_id]:
                    self.assertNotIn(scripted, prompt)
                candidates = facts["cause_candidates"]
                lead = leading_summary(candidates)
                must, never = EXPECTED[spec.scenario_id]
                for words in must:
                    self.assertIn(words.lower(), lead.lower())
                self.assertEqual(candidates[0]["role"], "leading")
                self.assertTrue(all(c["role"] in ("leading", "contributing", "chain", "effect", "insufficient") for c in candidates))
                # Every candidate is assessed on its own evidence and says why it ranks where it does.
                self.assertEqual([c["evidence_points"] for c in candidates if c["role"] != "insufficient"],
                                 sorted([c["evidence_points"] for c in candidates if c["role"] != "insufficient"], reverse=True))
                self.assertTrue(all(c.get("ranking_note") for c in candidates))

                # Home: the insight carries the same sentence.
                insight = (await self.client.get(f"/api/events/{event_id}/insight")).json()
                self.assertEqual(insight["leading_hypothesis"], lead)
                self.assertEqual(insight["bluf"]["leading_hypothesis"], lead)

                # Knowledge graph: the same causes, and likely effects are not causes.
                graph = (await self.client.get("/api/knowledge-graph", params={"event_id": event_id})).json()
                causes = [n for n in graph["nodes"] if n["kind"] == "cause" and n["id"] in graph["incident_path"]]
                self.assertEqual([n["rank"] for n in causes], [c["rank"] for c in candidates if c["role"] != "effect"])
                self.assertTrue(causes[0]["label"].startswith("Leading hypothesis"))
                for words in never:
                    self.assertFalse(any(words in n["label"] for n in causes), (words, [n["label"] for n in causes]))
                for c in candidates:
                    if c["role"] == "effect":
                        self.assertIn("possible consequence of", graph["nodes"][[n["id"] for n in graph["nodes"]].index(f"tag:{c['tags'][0]}")]["details"])

                # AI prompt: the leading hypothesis and every role are given to the model.
                prompt = ai_diagnosis.build_prompt(facts)
                self.assertIn(f"Leading hypothesis: {lead}", prompt)
                self.assertIn("(LEADING)", prompt)
                for scripted in spec.root.values():
                    self.assertNotIn(scripted, prompt)

                # The fallback text names the same leading cause first.
                fallback = ai_diagnosis.deterministic_diagnosis(facts)
                self.assertIn("HYPOTHESIS: LEADING.", fallback)


class LeadingCheck(unittest.TestCase):
    CANDIDATES = assign_roles([
        {"rank": 1, "kind": "trip", "equipment": "P-101", "tags": ["P101-FAULT"], "short": "P-101 thermal overload trip",
         "statement": "P-101 thermal overload trip. The trip is recorded; what caused the trip is not recorded.",
         "first_seen_at": "2026-11-08T11:45:02+00:00", "supporting": ["a"], "contradicting": []},
        {"rank": 2, "kind": "sop", "equipment": "P-102", "tags": ["FIT-102-FLW"], "short": "Clog on P-102",
         "statement": "Clog on P-102: the SOP says to suspect clog at these readings.",
         "first_seen_at": "2026-11-08T11:44:36+00:00", "supporting": ["b"], "contradicting": []},
        {"rank": 3, "kind": "early", "equipment": None, "tags": ["FIT-101-IN"], "short": "Inflow to wet well high",
         "statement": "Inflow to wet well outside normal, as a cause or a contributing condition.",
         "first_seen_at": "2026-11-08T11:44:28+00:00", "supporting": ["c"], "contradicting": []},
        {"rank": 4, "kind": "early", "equipment": None, "tags": ["X"], "short": "Something low",
         "statement": "Something outside normal, as a cause or a contributing condition.",
         "first_seen_at": "2026-11-08T11:46:02+00:00", "supporting": ["d"], "contradicting": []},
    ])

    def result(self, causes, limits=()):
        return {"source_status": "ai_generated", "operator_card": {"causes": list(causes), "limits": list(limits)}}

    def test_roles_and_sentence(self):
        roles = [c["role"] for c in self.CANDIDATES]
        self.assertEqual(roles, ["leading", "contributing", "contributing", "effect"])
        self.assertIn("more likely an effect", self.CANDIDATES[3]["statement"])
        self.assertEqual(leading_summary(self.CANDIDATES),
                         "P-101 thermal overload trip, together with clog on P-102 and inflow to wet well high.")

    def test_ai_line_that_names_the_leading_cause_is_shown(self):
        check = ai_diagnosis.leading_check(self.result(["HYPOTHESIS: Most likely, P-101 thermal overload trip while P-102 is clogged."]),
                                           {"cause_candidates": self.CANDIDATES})
        self.assertTrue(check["agrees"])
        self.assertEqual(check["source"], "ai")
        self.assertTrue(check["show"].startswith("Most likely, P-101"))

    def test_ai_line_with_another_cause_first_shows_the_record_and_says_so(self):
        check = ai_diagnosis.leading_check(self.result(["HYPOTHESIS: Most likely cause is excess inflow to the wet well."]),
                                           {"cause_candidates": self.CANDIDATES})
        self.assertFalse(check["agrees"])
        self.assertEqual(check["source"], "record")
        self.assertEqual(check["show"], leading_summary(self.CANDIDATES))
        self.assertIn("different cause first", check["note"])

    def test_fallback_shows_the_record_without_a_note(self):
        check = ai_diagnosis.leading_check({"source_status": "deterministic_fallback", "operator_card": {}},
                                           {"cause_candidates": self.CANDIDATES})
        self.assertEqual(check["source"], "record")
        self.assertIsNone(check["note"])


class TripTimeBelongsToThisIncident(unittest.TestCase):
    def test_latest_turn_on_before_the_alarm_is_used(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from cause_candidates import _status_turned_on
        at = lambda m, s=0: f"2026-11-09T05:{m:02d}:{s:02d}"
        rows = [SimpleNamespace(timestamp=at(48, 51), value=True),   # an earlier run of the same equipment
                SimpleNamespace(timestamp=at(50, 0), value=False),   # reset by its fix
                SimpleNamespace(timestamp=at(51, 39), value=True)]   # this incident
        db = SimpleNamespace(get_readings=lambda **_: rows)
        since = datetime(2026, 11, 9, 5, 41, tzinfo=timezone.utc)
        alarm = datetime(2026, 11, 9, 5, 53, 17, tzinfo=timezone.utc)
        self.assertEqual(_status_turned_on(db, "CHP-601-TRIP", since, alarm)["timestamp"], at(51, 39))
        # Without an alarm time, the latest turn-on.
        self.assertEqual(_status_turned_on(db, "CHP-601-TRIP", since)["timestamp"], at(51, 39))
        # A trip that only turned on after the alarm is still found.
        early_alarm = datetime(2026, 11, 9, 5, 45, tzinfo=timezone.utc)
        self.assertEqual(_status_turned_on(db, "CHP-601-TRIP", since, early_alarm)["timestamp"], at(48, 51))


if __name__ == "__main__":
    unittest.main()
