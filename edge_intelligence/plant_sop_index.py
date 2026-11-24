"""The plant's own SOPs in Qdrant, and the search that finds an incident's SOP.

Search order for an incident's SOP:
  1. Qdrant vector search (all-MiniLM-L6-v2, offline) over the plant's SOP
     sections: each SOP-XXX-NNN section of the plant manual (SOP-MAN-000) and
     the SOP-AER-001 draft.
  2. BM25 keyword search over the same sections, when Qdrant or the embedding
     model is not available.
  3. Nothing found: the SOP linked to the alarm tag in incident_catalog.py.

The search result is then checked against that alarm-tag link. When they agree,
the SOP is shown as found by the search. When they differ, the linked SOP is
shown and the difference is reported, so a look-alike procedure (for example a
different pump's SOP) is never put in front of the operator.

Points from the early prototype's Pump-02 JSON SOPs (SOP-P02-...) may still be
in an existing Qdrant collection. They do not belong to this plant and are
ignored by the search; ingest_sops.py no longer loads them by default.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SOP_DIR = Path(__file__).resolve().parent / "sop_documents"
PLANT_SOURCE = "OpsLens plant SOP section"
METHOD_TEXT = {
    "qdrant_vector_search": "Qdrant vector search",
    "bm25": "BM25 keyword search",
    "alarm_tag_link": "Alarm tag link",
}


def plant_sop_ids() -> List[str]:
    """Every SOP section of the plant manual, plus the draft SOPs."""
    from demo_sop_sections import DEMO_SOP_SECTIONS, DRAFT_SOPS
    return list(DEMO_SOP_SECTIONS) + [s for s in DRAFT_SOPS if s not in DEMO_SOP_SECTIONS]


@lru_cache(maxsize=1)
def legacy_sop_ids() -> frozenset:
    """SOP ids of the early prototype's JSON SOPs (Pump-02 and similar)."""
    from demo_sop_sections import DRAFT_SOPS
    ids = set()
    for path in SOP_DIR.glob("*.json"):
        try:
            sop_id = json.loads(path.read_text(encoding="utf-8")).get("sop_id")
        except (OSError, ValueError):
            continue
        if sop_id and sop_id not in DRAFT_SOPS:
            ids.add(sop_id)
    return frozenset(ids)


def _scenario_tags(sop_id: str) -> List[str]:
    import incident_catalog
    tags: List[str] = []
    for spec in incident_catalog.SCENARIOS:
        if spec.sop_id == sop_id:
            tags += [spec.primary_alarm, *incident_catalog.scenario_tags(spec)]
    return sorted(set(t for t in tags if t))


def section_chunks(sop_id: str) -> List[Dict[str, Any]]:
    """The section's own text, page by page, in search-sized chunks."""
    from demo_sop_sections import load_demo_sop_document
    from pdf_sop_ingestion import _chunk_page
    document = load_demo_sop_document(sop_id)
    if not document:
        return []
    revision = str(document.get("revision") or "0")
    tags = _scenario_tags(sop_id)
    chunks = []
    for page in document.get("content_pages") or []:
        text = str(page.get("text") or "").strip()
        if not text:
            continue
        for number, piece in enumerate(_chunk_page(text), start=1):
            chunk_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"opslens-plant-sop:{sop_id}:{revision}:{page.get('page')}:{number}"))
            chunks.append({
                "chunk_id": chunk_id, "document_id": f"{sop_id}::plant::{revision}", "sop_id": sop_id,
                "document_name": document.get("title") or sop_id, "title": document.get("title") or sop_id,
                "text": piece, "instruction": piece, "page": page.get("page"), "section": "content", "chunk_number": number,
                "equipment": document.get("equipment") or "", "equipment_type": document.get("equipment_type") or "",
                "plant_area": document.get("plant_area") or "", "revision": revision, "tags": tags,
                "source": PLANT_SOURCE, "source_pdf": document.get("source_pdf"),
                "approval_status": document.get("approval_status") or "draft", "approved": False,
                "document_status": document.get("document_status") or "Draft for review — NOT APPROVED",
            })
            __import__("sop_approval").apply(chunks[-1], sop_id)
    return chunks


def index_plant_sops(store=None, model=None) -> Dict[str, Any]:
    """Embed and upsert every plant SOP section. Stable chunk ids make it safe to repeat."""
    from vector_store.base import VectorStoreDocument
    if store is None:
        from sop_retriever import get_qdrant_store
        store = get_qdrant_store()
    if model is None:
        from sop_retriever import get_embed_model
        model = get_embed_model()
    chunks = [chunk for sop_id in plant_sop_ids() for chunk in section_chunks(sop_id)]
    if not chunks:
        return {"indexed": 0, "sops": [], "error": "No plant SOP text could be read (is the plant manual uploaded?)."}
    vectors = model.encode([c["text"] for c in chunks], convert_to_numpy=True)
    documents = [VectorStoreDocument(chunk_id=c["chunk_id"], document_id=c["document_id"], sop_id=c["sop_id"],
                                     text=c["text"], embedding=list(map(float, v)), payload=c)
                 for c, v in zip(chunks, vectors)]
    count = store.upsert(documents)
    sops = sorted({c["sop_id"] for c in chunks})
    logger.info("[PlantSOPs] %s chunks from %s SOPs are in Qdrant.", count, len(sops))
    return {"indexed": count, "sops": sops, "error": None}


_INDEX_STATE: Dict[str, Any] = {"status": "not_started", "detail": None}


def index_on_start() -> None:
    """Add the plant SOPs to Qdrant in the background when the backend starts.
    Without Qdrant or the embedding model, nothing changes: BM25 is used."""
    def run():
        _INDEX_STATE.update(status="running", detail=None)
        try:
            from sop_retriever import get_qdrant_store
            store = get_qdrant_store()
            if not store.health().get("connected"):
                _INDEX_STATE.update(status="skipped", detail="Qdrant is not running; SOP search uses BM25.")
            else:
                result = index_plant_sops(store)
                _INDEX_STATE.update(status="done" if not result["error"] else "failed",
                                    detail=result["error"] or f"{result['indexed']} chunks from {len(result['sops'])} plant SOPs indexed; SOP search tries Qdrant first.")
        except Exception as error:  # noqa: BLE001 - startup must never fail on this
            hint = (" Install the embedding model: pip install -r requirements.txt (while online)."
                    if "sentence" in f"{error}{type(error).__name__}".lower() or "MiniLM" in str(error) else "")
            _INDEX_STATE.update(status="skipped", detail=f"Not indexed ({type(error).__name__}). SOP search uses BM25.{hint}")
        logger.info("[PlantSOPs] %s", _INDEX_STATE["detail"])
        print(f"[OpsLens] Plant SOPs in Qdrant: {_INDEX_STATE['detail']}", flush=True)
    threading.Thread(target=run, name="plant-sop-index", daemon=True).start()


def index_state() -> Dict[str, Any]:
    return dict(_INDEX_STATE)


# --- search -----------------------------------------------------------------

def _section_for_manual_page(page: Any) -> List[str]:
    from demo_sop_sections import DEMO_SOP_SECTIONS
    try:
        page = int(page)
    except (TypeError, ValueError):
        return []
    return [sop_id for sop_id, section in DEMO_SOP_SECTIONS.items() if page in section["pages"]]


@lru_cache(maxsize=1)
def _section_bm25():
    from rank_bm25 import BM25Okapi
    sops, corpus = [], []
    for sop_id in plant_sop_ids():
        text = " ".join(c["text"] for c in section_chunks(sop_id))
        if text:
            sops.append(sop_id)
            corpus.append(re.findall(r"[a-z0-9_\-]+", text.lower()))
    return (BM25Okapi(corpus) if corpus else None), sops


def _qdrant_ranking(query: str, top_k: int) -> List[Dict[str, Any]]:
    from sop_retriever import get_embed_model, get_qdrant_store
    vector = get_embed_model().encode([query], convert_to_numpy=True)[0]
    store = get_qdrant_store()
    if not store.health().get("connected"):
        raise RuntimeError("Qdrant is not running")
    best: Dict[str, float] = {}
    legacy = legacy_sop_ids()
    # An older collection can hold many prototype chunks (Pump-02 and similar)
    # that fill the first results; look deeper before giving up on Qdrant.
    for k in (top_k, 200):
        for hit in store.search(query_vector=[float(x) for x in vector], top_k=k):
            sop_id = hit.sop_id
            if sop_id in legacy and (hit.payload or {}).get("source") != PLANT_SOURCE:
                continue  # the early prototype's Pump-02 SOPs do not belong to this plant
            names = _section_for_manual_page(hit.payload.get("page")) if sop_id == "SOP-MAN-000" else [sop_id]
            for name in names:
                best[name] = max(best.get(name, 0.0), float(hit.similarity))
        if best:
            break
    if not best:
        raise RuntimeError("Qdrant has no plant SOP chunks yet (still indexing, or indexing failed: see the backend window)")
    return [{"sop_id": s, "score": round(v, 4)} for s, v in sorted(best.items(), key=lambda x: -x[1])]


def _bm25_ranking(query: str) -> List[Dict[str, Any]]:
    bm25, sops = _section_bm25()
    if bm25 is None:
        raise RuntimeError("No plant SOP text for keyword search")
    scores = bm25.get_scores(re.findall(r"[a-z0-9_\-]+", query.lower()))
    ranked = sorted(zip(sops, (float(s) for s in scores)), key=lambda x: -x[1])
    ranked = [{"sop_id": s, "score": round(v, 3)} for s, v in ranked if v > 0]
    if not ranked:
        raise RuntimeError("No keyword match")
    return ranked


def find_sop(query: str, linked_sop_id: Optional[str], top_k: int = 12) -> Dict[str, Any]:
    """Qdrant first, then BM25, checked against the SOP linked to the alarm tag."""
    errors = []
    ranking, method = [], "alarm_tag_link"
    for name, search in (("qdrant_vector_search", lambda: _qdrant_ranking(query, top_k)),
                         ("bm25", lambda: _bm25_ranking(query))):
        try:
            ranking, method = search(), name
            break
        except Exception as error:  # noqa: BLE001 - each step falls through to the next
            errors.append(f"{METHOD_TEXT[name]}: {error}")
    top = ranking[0]["sop_id"] if ranking else None
    agrees = bool(top and linked_sop_id and top == linked_sop_id)
    if not ranking:
        note = (f"Search found nothing; the SOP linked to the alarm ({linked_sop_id}) is shown." if linked_sop_id
                else "Search found nothing, and no SOP is linked to this alarm.")
    elif not linked_sop_id:
        note = f"{METHOD_TEXT[method]} ranked {top} closest, but no SOP is linked to this alarm, so none is shown."
    elif agrees:
        note = f"{METHOD_TEXT[method]} found {top}; it matches the SOP linked to the alarm."
    else:
        note = (f"{METHOD_TEXT[method]} ranked {top} first, which is not the SOP linked to this alarm. "
                f"The linked SOP, {linked_sop_id}, is shown.")
    return {
        "method": method if ranking else "alarm_tag_link", "method_text": METHOD_TEXT[method if ranking else "alarm_tag_link"],
        "top_sop_id": top, "linked_sop_id": linked_sop_id, "agrees": agrees,
        "score": ranking[0]["score"] if ranking else None,
        "linked_rank": next((i for i, r in enumerate(ranking, start=1) if r["sop_id"] == linked_sop_id), None),
        "ranking": ranking[:5], "note": note, "errors": errors,
    }
