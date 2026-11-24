"""Time-series, anomaly, forecast and knowledge-graph behaviour on real records."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

import analytics
import incident_catalog as catalog
from alarm_lifecycle import get_lifecycle_manager
from database import Database


def points(values, start=None, step=2.0):
    start = start or datetime(2026, 11, 4, tzinfo=timezone.utc)
    rows = [SimpleNamespace(value=v, timestamp=(start + timedelta(seconds=i * step)).isoformat().replace("+00:00", "Z"),
                            data_quality="VALID", data_source="simulated") for i, v in enumerate(values)]
    return analytics.numeric_points(rows)


class AnalyticsUnitTests(unittest.TestCase):
    def test_a_step_is_flagged_and_noise_is_not(self):
        values = [10.0 + 0.1 * ((i * 7) % 3 - 1) for i in range(30)] + [15.0] * 3
        flagged = {f["index"] for f in analytics.detect_anomalies(values)}
        self.assertIn(30, flagged)
        self.assertFalse([i for i in flagged if i < 30])

    def test_band_check_flags_values_outside_normal(self):
        flags = analytics.detect_anomalies([5.0, 6.0, 9.0], (4.0, 8.0))
        self.assertEqual([f["index"] for f in flags], [2])
        self.assertEqual(flags[0]["reasons"], ["left the normal range"])

    def test_gaps_are_reported_not_filled(self):
        series = points(list(range(10))) + points([10, 11], start=datetime(2026, 11, 4, 0, 5, tzinfo=timezone.utc))
        gaps = analytics.find_gaps(series)
        self.assertEqual(len(gaps), 1)
        self.assertGreater(gaps[0]["seconds"], 200)

    def test_forecast_follows_a_clear_ramp_and_says_it_is_a_prediction(self):
        result = analytics.forecast_series(points([float(i) for i in range(40)]), 20, [{"value": 42.0, "direction": "high", "label": "42 high"}])
        self.assertTrue(result["available"])
        self.assertEqual(result["kind"], "PREDICTION")
        self.assertGreater(result["points"][-1]["v"], 39)
        self.assertEqual(result["crossing"]["limit"], "42 high")
        self.assertLess(result["points"][0]["hi"] - result["points"][0]["lo"], result["points"][-1]["hi"] - result["points"][-1]["lo"])
        self.assertIsNotNone(result["backtest"])

    def test_a_past_month_at_another_spacing_does_not_set_the_time_step(self):
        # 340 readings every 30 min (seeded history), then 60 live readings every 2 s.
        start = datetime(2026, 10, 10, tzinfo=timezone.utc)
        old = points([10.0] * 340, start=start, step=1800)
        live = points([10.0 - i * 0.1 for i in range(60)], start=start + timedelta(days=30), step=2)
        result = analytics.forecast_series(old + live, 30)
        self.assertTrue(result["available"])
        self.assertEqual(result["sample_interval_seconds"], 2)
        self.assertEqual(result["horizon_seconds"], 30)

    def test_noise_forecast_stays_flat(self):
        values = [50.0 + ((i * 13) % 7 - 3) * 0.2 for i in range(40)]
        result = analytics.forecast_series(points(values), 30)
        self.assertFalse(result["fitted"]["trend_significant"])
        self.assertLess(abs(result["points"][-1]["v"] - 50), 1.0)

    def test_too_little_data_is_stated(self):
        result = analytics.forecast_series(points([1.0, 2.0, 3.0]), 30)
        self.assertFalse(result["available"])
        self.assertIn("at least", result["reason"])

    def test_already_past_a_limit_is_not_a_prediction(self):
        result = analytics.forecast_series(points([float(i) for i in range(40)]), 20, [{"value": 5.0, "direction": "high", "label": "5"}])
        self.assertIsNone(result["crossing"])


class AnalyticsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        get_lifecycle_manager().clear_all()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "none"})
        self.env.start()
        from api.routes import create_app
        from orchestrator import OpsLensOrchestrator
        self.db = Database(db_path=Path(self.tmp.name) / "a.db")
        self.orch = OpsLensOrchestrator(db=self.db)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(orchestrator=self.orch)), base_url="http://t", timeout=60)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.orch.stop()
        self.env.stop()
        get_lifecycle_manager().clear_all()
        self.tmp.cleanup()

    async def run_lift(self):
        await self.client.post("/api/scenarios/run", json={"scenario_id": "wet_well_high_level", "speed_multiplier": 2000})
        for _ in range(600):
            status = (await self.client.get("/api/scenarios/status")).json()
            if status["status"] != "running":
                return status
            await asyncio.sleep(0.02)

    async def test_series_comes_from_records_with_bands_and_markers(self):
        empty = (await self.client.get("/api/telemetry/series", params={"tag": "LIT-101-WWL"})).json()
        self.assertFalse(empty["available"])
        self.assertIn("No recorded readings", empty["message"])
        await self.run_lift()
        series = (await self.client.get("/api/telemetry/series", params={"tag": "LIT-101-WWL"})).json()
        self.assertTrue(series["available"])
        self.assertEqual(series["total_samples"], 60)
        self.assertEqual(series["data_sources"], ["simulated"])
        self.assertEqual(series["normal"], [1.0, 2.4])
        self.assertTrue(any(l["value"] == 3.6 for l in series["limits"]))
        self.assertTrue(series["anomalies"])
        self.assertEqual(series["summary"]["max"], 3.85)
        recorded = [r.value for r in self.db.get_readings(tag="LIT-101-WWL", limit=1000)]
        self.assertTrue(all(p["v"] in recorded for p in series["points"]))
        tags = (await self.client.get("/api/telemetry/tags")).json()["tags"]
        self.assertEqual(next(t for t in tags if t["tag"] == "LIT-101-WWL")["samples"], 60)

    async def test_before_and_after_fix_comparison(self):
        status = await self.run_lift()
        params = {"event_id": status["event_id"], "tag": "LIT-101-WWL"}
        before = (await self.client.get("/api/telemetry/compare", params=params)).json()
        self.assertIsNone(before["after_fix"])
        self.assertIn("has not cleared", before["message"])
        await self.client.post("/api/scenarios/corrective-action", json={})
        after = (await self.client.get("/api/telemetry/compare", params=params)).json()
        self.assertEqual(after["before_fix"]["max"], 3.85)
        self.assertEqual(after["after_fix"]["last"], 1.6)

    async def test_forecast_and_warning_routes(self):
        await self.run_lift()
        forecast = (await self.client.get("/api/analytics/forecast", params={"tag": "LIT-101-WWL"})).json()
        self.assertEqual(forecast["kind"], "PREDICTION")
        self.assertTrue(forecast["available"])
        warnings = (await self.client.get("/api/analytics/warnings")).json()
        self.assertIn("Not alarms", warnings["note"])
        for item in warnings["warnings"]:
            self.assertEqual(item["kind"], "PREDICTION")

    async def test_graph_is_generated_and_keeps_causes_as_hypotheses(self):
        plain = (await self.client.get("/api/knowledge-graph")).json()
        kinds = {n["kind"] for n in plain["nodes"]}
        self.assertEqual(kinds, {"plant", "area", "equipment", "sensor", "alarm", "sop"})
        self.assertEqual(plain["incident_path"], [])
        status = await self.run_lift()
        graph = (await self.client.get("/api/knowledge-graph", params={"event_id": status["event_id"]})).json()
        ids = {n["id"] for n in graph["nodes"]}
        self.assertTrue({e["source"] for e in graph["edges"]} <= ids and {e["target"] for e in graph["edges"]} <= ids)
        by_id = {n["id"]: n for n in graph["nodes"]}
        path = [by_id[i]["kind"] for i in graph["incident_path"]]
        self.assertEqual(path[:7], ["plant", "area", "equipment", "sensor", "alarm", "incident", "cause"])
        # Every possible cause from the record, the SOP, the fix steps and the recovery check.
        self.assertGreaterEqual(path.count("cause"), 2)
        self.assertTrue({"sop", "fix", "check"} <= set(path))
        self.assertEqual({e["basis"] for e in graph["edges"] if e["relation"] == "possible_cause"}, {"hypothesis"})
        cause = next(n for n in graph["nodes"] if n["kind"] == "cause")
        self.assertTrue(cause["label"].startswith(("Leading hypothesis", "Contributing")), cause["label"])
        for scripted in catalog.SCENARIO_BY_ID["wet_well_high_level"].root.values():
            self.assertNotIn(scripted, str(graph))
        self.assertEqual((await self.client.get("/api/knowledge-graph", params={"event_id": "nope"})).status_code, 404)

    async def test_retrieval_detail_names_the_method_and_pages(self):
        status = await self.run_lift()
        detail = (await self.client.get(f"/api/events/{status['event_id']}/retrieval")).json()
        self.assertEqual(detail["sop_id"], "SOP-LFT-001")
        self.assertEqual(detail["retrieval_method"], "bundled_pdf_section")
        self.assertTrue(detail["chunks"][0]["page"])
        self.assertFalse(detail["chunks"][0]["approved"])


class UploadedDocumentTests(unittest.TestCase):
    def test_uploaded_passages_carry_citation_and_true_status(self):
        import sop_retriever
        sops = [
            {"sop_id": "SOP-X-1", "title": "Plant wet well note", "page": 3, "sha256": "abc", "approved": False, "steps": ["wet well pump clog response at the site"]},
            {"sop_id": "SOP-X-2", "title": "Records", "page": 5, "sha256": "jkl", "approved": False, "steps": ["records to retain for each alarm"]},
            {"sop_id": "SOP-MAN-000", "title": "Manual", "page": 1, "sha256": "def", "steps": ["wet well pump"]},
            {"sop_id": "SOP-LFT-001", "title": "Mapped", "page": 2, "sha256": "ghi", "steps": ["wet well pump"]},
            {"sop_id": "SOP-JSON", "title": "No file", "page": None, "steps": ["wet well"]},
        ]
        index = SimpleNamespace(get_scores=lambda tokens: [2.0, 9.0, 3.0, 4.0, 5.0])
        with patch.object(sop_retriever, "get_bm25_fallback", return_value=(index, sops)):
            passages = sop_retriever.uploaded_document_passages("wet well pump clog", exclude_sop="SOP-LFT-001")
        self.assertEqual([p["sop_id"] for p in passages], ["SOP-X-1"])
        self.assertEqual(passages[0]["page"], 3)
        self.assertEqual(passages[0]["status"], "DRAFT / NOT APPROVED")

    def test_uploaded_passage_reaches_the_prompt(self):
        import ai_diagnosis
        facts = {"event_id": "e", "scenario_id": None, "incident": "x", "process_area": None, "equipment": "E",
                 "primary_alarm": {"tag": "A", "description": "d", "first_at": "t", "input_active": True}, "data_source": "SIMULATED",
                 "incident_tags": [], "active_alarms": [], "readings": [], "sequence": [], "changes_vs_normal_pct": [],
                 "ranking": None, "sop": None, "verification": [], "missing": [],
                 "uploaded_passages": [{"sop_id": "SOP-X-1", "page": 3, "status": "DRAFT / NOT APPROVED", "text": "Isolate the wet well inlet gate."}]}
        self.assertIn("Isolate the wet well inlet gate.", ai_diagnosis.build_prompt(facts))
