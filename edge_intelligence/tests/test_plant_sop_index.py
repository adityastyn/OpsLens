"""SOP search order: Qdrant first, then BM25, checked against the alarm-tag link.

Qdrant and the embedding model are replaced by small stand-ins here, so these
tests check the order, the check and the indexing, not the real ranking quality.
"""
import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import incident_catalog
import plant_sop_index as index
from alarm_lifecycle import get_lifecycle_manager
from database import Database
from vector_store.base import VectorSearchResult


class FakeModel:
    def encode(self, texts, convert_to_numpy=True):
        return [[0.1] * 384 for _ in texts]


class FakeStore:
    def __init__(self, hits=None, connected=True):
        self.hits, self.connected, self.upserted = hits or [], connected, []

    def health(self):
        return {"connected": self.connected}

    def search(self, query_vector, top_k=5, **_):
        assert len(query_vector) == 384
        return self.hits[:top_k]

    def upsert(self, documents):
        self.upserted += documents
        return len(documents)


def hit(sop_id, score, page=None, source="OpsLens plant SOP section"):
    return VectorSearchResult(chunk_id=f"{sop_id}-{page}", sop_id=sop_id, text="...", similarity=score,
                              payload={"sop_id": sop_id, "page": page, "source": source})


class PlantIndex(unittest.TestCase):
    def test_plant_and_legacy_sops(self):
        ids = index.plant_sop_ids()
        for spec in incident_catalog.SCENARIOS:
            if spec.sop_id:
                self.assertIn(spec.sop_id, ids)
        self.assertIn("SOP-P02-VLV-01", index.legacy_sop_ids())
        self.assertIn("SOP-P02-SEAL-01", index.legacy_sop_ids())
        self.assertNotIn("SOP-AER-001", index.legacy_sop_ids())

    def test_sections_become_stable_points_with_the_scenario_tags(self):
        first, again = index.section_chunks("SOP-LFT-001"), index.section_chunks("SOP-LFT-001")
        self.assertTrue(first)
        self.assertEqual([c["chunk_id"] for c in first], [c["chunk_id"] for c in again])
        self.assertTrue(all(c["source"] == index.PLANT_SOURCE and c["approved"] is False for c in first))
        self.assertIn("ALM-WW-101-HH", first[0]["tags"])
        self.assertTrue(all(c["page"] in (12, 13, 14, 15) for c in first))

    def test_index_upserts_every_plant_sop(self):
        store = FakeStore()
        result = index.index_plant_sops(store=store, model=FakeModel())
        self.assertIsNone(result["error"])
        self.assertEqual(result["indexed"], len(store.upserted))
        self.assertTrue({s.sop_id for s in incident_catalog.SCENARIOS if s.sop_id} <= set(result["sops"]))
        self.assertTrue(all(len(d.embedding) == 384 for d in store.upserted))


class SearchOrder(unittest.TestCase):
    def search(self, store, linked, query="wet well high high level lift station pump clog"):
        with patch("sop_retriever.get_embed_model", return_value=FakeModel()), \
             patch("sop_retriever.get_qdrant_store", return_value=store):
            return index.find_sop(query, linked)

    def test_qdrant_first_and_old_prototype_sops_are_ignored(self):
        store = FakeStore([hit("SOP-P02-SEAL-01", 0.91, source="OpsLens SOP Knowledge Base"),
                           hit("SOP-LFT-001", 0.83, 13), hit("SOP-GEN-002", 0.61, 6)])
        found = self.search(store, "SOP-LFT-001")
        self.assertEqual(found["method"], "qdrant_vector_search")
        self.assertEqual(found["top_sop_id"], "SOP-LFT-001")
        self.assertTrue(found["agrees"])
        self.assertNotIn("SOP-P02-SEAL-01", [r["sop_id"] for r in found["ranking"]])

    def test_a_chunk_of_the_uploaded_manual_counts_for_its_section(self):
        found = self.search(FakeStore([hit("SOP-MAN-000", 0.8, 19)]), "SOP-BIO-002")
        self.assertEqual(found["top_sop_id"], "SOP-BIO-002")

    def test_a_different_first_hit_is_reported_and_the_linked_sop_stays(self):
        found = self.search(FakeStore([hit("SOP-GEN-002", 0.9, 6), hit("SOP-LFT-001", 0.7, 12)]), "SOP-LFT-001")
        self.assertFalse(found["agrees"])
        self.assertEqual(found["linked_rank"], 2)
        self.assertIn("The linked SOP, SOP-LFT-001, is shown", found["note"])

    def test_without_qdrant_bm25_is_used(self):
        found = self.search(FakeStore(connected=False), "SOP-LFT-001")
        self.assertEqual(found["method"], "bm25")
        self.assertTrue(found["agrees"])
        self.assertTrue(any("Qdrant" in e for e in found["errors"]))

    def test_no_linked_sop_shows_none(self):
        found = self.search(FakeStore(connected=False), None, "UV intensity low lamp bank ballast fault")
        self.assertFalse(found["agrees"])
        self.assertIn("no SOP is linked", found["note"])


class IngestLoadsThePlantSops(unittest.TestCase):
    def test_old_prototype_sops_only_on_request(self):
        fake = types.ModuleType("sentence_transformers")
        fake.SentenceTransformer = lambda *a, **k: FakeModel()
        with patch.dict(sys.modules, {"sentence_transformers": fake}):
            import ingest_sops
            store = FakeStore()
            ingest_sops.ingest(vector_store=store)
            self.assertFalse({d.sop_id for d in store.upserted} & index.legacy_sop_ids())
            store = FakeStore()
            ingest_sops.ingest(vector_store=store, include_legacy=True)
            self.assertIn("SOP-P02-VLV-01", {d.sop_id for d in store.upserted})


class AllScenarios(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        get_lifecycle_manager().clear_all()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"OPSLENS_LLM_PROVIDER": "none"})
        self.env.start()
        from api.routes import create_app
        from orchestrator import OpsLensOrchestrator
        self.orch = OpsLensOrchestrator(db=Database(db_path=Path(self.tmp.name) / "s.db"))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(orchestrator=self.orch)), base_url="http://t", timeout=60)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.orch.stop()
        self.env.stop()
        get_lifecycle_manager().clear_all()
        self.tmp.cleanup()

    async def test_every_scenario_is_found_by_search_and_agrees_with_its_link(self):
        for spec in incident_catalog.SCENARIOS:
            with self.subTest(scenario=spec.scenario_id):
                await self.client.post("/api/scenarios/run", json={"scenario_id": spec.scenario_id, "speed_multiplier": 2000})
                for _ in range(800):
                    status = (await self.client.get("/api/scenarios/status")).json()
                    if status["status"] not in ("running", "recovering"):
                        break
                    await asyncio.sleep(0.02)
                await self.client.post("/api/scenarios/corrective-action", json={"scenario_id": spec.scenario_id})
                event = self.orch.get_full_event(status["event_id"])
                found = event["sop_match"]["why_retrieved"]["found_by"]
                # No Qdrant in the test environment: BM25 is the search used.
                self.assertIn(found["method"], ("qdrant_vector_search", "bm25"))
                if spec.sop_id:
                    self.assertEqual(event["sop_match"]["sop_id"], spec.sop_id)
                    self.assertTrue(found["agrees"], found)
                else:
                    self.assertEqual(event["sop_match"]["sop_id"], "SOP-NONE")
                insight = (await self.client.get(f"/api/events/{status['event_id']}/insight")).json()
                self.assertEqual(insight["sop"]["found_by"]["method"], found["method"])


if __name__ == "__main__":
    unittest.main()
