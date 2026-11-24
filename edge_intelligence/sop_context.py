"""What the AI, the API and the HMI know about the SOP for one incident.

The SOP viewer and the diagnosis read the same document here, so anything the
operator can see in the viewer is available to the model. All text is copied from
the source with its page number; nothing is written or summarised. Coverage and
approval are worked out here, in Python, so the model cannot get them wrong.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("opslens.sop_context")

MAX_PASSAGES = 3
MAX_PASSAGE_CHARS = 420
MAX_STEP_CHARS = 280
MAX_STEPS = 14
MIN_ASPECT_TERMS = 1
_STOP = {"with", "from", "that", "this", "the", "and", "for", "low", "high", "rate", "total", "plant", "alarm", "level"}
DRAFT_LABEL = "DRAFT — NOT APPROVED"
APPROVED_LABEL = "APPROVED"
STATUS_TEXT = {
    "full": "This SOP covers the incident.",
    "partial": "This SOP partly covers the incident.",
    "none": "No directly matching approved SOP was found for this incident.",
}


def _terms(text: str) -> set:
    words = re.findall(r"[a-z][a-z\-]{2,}", str(text or "").lower().replace("-", " "))
    return {w.rstrip("s") for w in words if w not in _STOP}


def _load_document(sop_id: str) -> Optional[Dict[str, Any]]:
    from demo_sop_sections import load_demo_sop_document
    return load_demo_sop_document(sop_id)


def _pages_from(sop_match: Dict[str, Any], sop_id: str) -> tuple:
    """Source pages: the document the viewer shows, else what retrieval returned."""
    document = None
    try:
        document = _load_document(sop_id)
    except Exception as exc:
        logger.warning("SOP document %s could not be read: %s: %s", sop_id, type(exc).__name__, exc)
    if document and document.get("content_pages"):
        return document, [{"page": p.get("page"), "text": str(p.get("text") or "")} for p in document["content_pages"]]
    chunks = sop_match.get("retrieved_chunks") or sop_match.get("chunks") or []
    return None, [{"page": c.get("page"), "text": str(c.get("text") or "")} for c in chunks if c.get("text")]


def _paragraphs(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Split pages at numbered lines and section headings, keeping the page number."""
    out: List[Dict[str, Any]] = []
    for page in pages:
        current: List[str] = []
        for raw in page["text"].splitlines():
            line = raw.strip()
            if not line or re.match(r"^(SOP Manual\s+[–-].*|Page \d+ of \d+)$", line):
                continue
            if re.match(r"^\d{1,2}\.\s", line) and current:
                out.append({"page": page["page"], "text": " ".join(current)})
                current = []
            current.append(line)
        if current:
            out.append({"page": page["page"], "text": " ".join(current)})
    return out


def incident_terms(brief: Dict[str, Any]) -> set:
    terms = _terms(brief["problem"].get("alarm_description"))
    for row in brief.get("incident_readings") or []:
        if row.get("state_at_worst") != "within normal":
            terms |= _terms(row.get("label"))
    return terms


def relevant_passages(pages: List[Dict[str, Any]], terms: set, step_numbers_text: set) -> List[Dict[str, Any]]:
    """Best page text that is not already sent as a numbered step. One per page
    first, so an operator-facing citation spans the document, not one paragraph."""
    scored = []
    for order, paragraph in enumerate(_paragraphs(pages)):
        text = paragraph["text"]
        if re.sub(r"^\d{1,2}\.\s+", "", text)[:60] in step_numbers_text:
            continue
        hits = len(terms & _terms(text))
        if hits:
            scored.append((hits, -order, paragraph))
    scored.sort(key=lambda row: row[:2], reverse=True)
    chosen, seen_pages = [], set()
    for hits, _, paragraph in scored:
        if paragraph["page"] not in seen_pages:
            seen_pages.add(paragraph["page"])
            chosen.append((hits, paragraph))
    for hits, _, paragraph in scored:
        if len(chosen) >= MAX_PASSAGES:
            break
        if not any(paragraph is item for _, item in chosen):
            chosen.append((hits, paragraph))
    return [{"page": p["page"], "text": p["text"][:MAX_PASSAGE_CHARS], "matched_terms": hits} for hits, p in chosen[:MAX_PASSAGES]]


def _aspects(brief: Dict[str, Any], sop_text_terms: set, sop_id: str) -> tuple:
    """Which abnormal readings the SOP talks about, by its limits or its wording."""
    import alarm_config
    covered, missing = [], []
    for row in brief.get("incident_readings") or []:
        if row.get("state_at_worst") == "within normal":
            continue
        definition = alarm_config.definition_for_tag(row["tag"]) or {}
        limit_sops = {definition.get("sop_id")} | {l.get("sop_id") for l in definition.get("limits") or []}
        words = _terms(row.get("label"))
        if sop_id in limit_sops or (words and len(words & sop_text_terms) >= max(MIN_ASPECT_TERMS, len(words) - 1)):
            covered.append(row["label"])
        else:
            missing.append(row["label"])
    return covered, missing


def match_signals(full_event: Dict[str, Any], brief: Dict[str, Any], document: Optional[Dict[str, Any]],
                  sop_text: str, passages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Why this SOP was matched. Each signal is a recorded fact, not a score."""
    sop = full_event.get("sop_match") or {}
    scenario = brief.get("scenario") or {}
    problem = brief["problem"]
    signals = []

    def add(name: str, passed: bool, detail: str) -> None:
        signals.append({"signal": name, "matched": bool(passed), "detail": detail})

    add("Scenario and process area", bool(scenario.get("scenario_id")) and sop.get("retrieval_method") == "bundled_pdf_section",
        f"Scenario {scenario.get('scenario_id') or 'none'} maps to this SOP.")
    applicable = " ".join(str(x) for x in ((document or {}).get("applicable_equipment_ids") or [])) + " " + str((document or {}).get("equipment") or "")
    equipment_words = _terms(re.sub(r"([a-z])([A-Z])", r"\1 \2", re.sub(r"-\d+$", "", str(problem.get("equipment") or ""))))
    add("Equipment", bool(_terms(applicable) & equipment_words), f"Equipment {problem.get('equipment')}.")
    tag = problem.get("alarm_tag")
    add("Alarm ID", bool(tag and (tag in sop_text or tag in ((document or {}).get("tags") or []))), f"Alarm {tag}.")
    add("SOP metadata", bool(_terms(sop.get("sop_title") or sop.get("title")) & (incident_terms(brief) | equipment_words)),
        f"Title: {sop.get('sop_title') or sop.get('title')}.")
    add("Keyword match in source text", bool(passages), f"{len(passages)} relevant passage(s) found.")
    similarity = sop.get("semantic_similarity")
    add("Semantic similarity", similarity is not None, f"Similarity {similarity}." if similarity is not None else "Vector search not used for this match.")
    return signals


def build_sop_context(full_event: Dict[str, Any], brief: Dict[str, Any]) -> Dict[str, Any]:
    """The incident's SOP status, passages, steps and coverage."""
    sop_block = brief.get("sop") or {}
    sop_match = full_event.get("sop_match") or {}
    sop_id = sop_block.get("sop_id")
    found = bool(sop_id) and sop_block.get("retrieval_status") == "match_found" and sop_id not in ("SOP-NONE", "NONE", "UNKNOWN-SOP")
    if not found:
        return {"status": "none", "status_text": STATUS_TEXT["none"], "sop_id": None, "title": None, "approved": False,
                "approval_label": None, "steps": [], "passages": [], "covered": [], "not_covered": [], "notes": [],
                "match_signals": [], "pages": [], "revision": None, "source_document": None}
    document, pages = _pages_from(sop_match, sop_id)
    all_text = " ".join(p["text"] for p in pages)
    verbatim = [s for s in (brief.get("safe_inspection_steps") or {}).get("steps") or [] if s.get("source") == "verbatim"]
    steps = [{"n": s["number"], "text": s["text"][:MAX_STEP_CHARS], "page": s.get("page")} for s in verbatim[:MAX_STEPS]]
    passages = relevant_passages(pages, incident_terms(brief), {s["text"][:60] for s in steps})
    covered, missing = _aspects(brief, _terms(all_text), sop_id)
    notes = list(sop_block.get("coverage_notes") or [])
    catalog_coverage = sop_block.get("coverage")
    gaps = [re.sub(r"^gap:\s*", "", n, flags=re.I) for n in notes if n.lower().startswith("gap")]
    status = catalog_coverage if catalog_coverage in ("full", "partial") else ("full" if covered and not missing else "partial")
    import sop_approval
    approved = sop_approval.approved(sop_id, sop_block.get("approved") is True)
    return {
        "status": status, "status_text": STATUS_TEXT[status], "sop_id": sop_id, "title": sop_block.get("title"),
        "revision": (document or {}).get("revision_label") or (document or {}).get("revision") or sop_match.get("revision"),
        "approved": approved, "approval_label": APPROVED_LABEL if approved else DRAFT_LABEL,
        "source_document": sop_block.get("source_document"), "pages": [p["page"] for p in pages],
        "steps": steps, "passages": passages, "covered": covered, "not_covered": missing, "notes": gaps,
        "match_signals": match_signals(full_event, brief, document, all_text, passages),
    }


def checks_from_sop(context: Dict[str, Any], cited: List[int], limit: int = 7) -> List[Dict[str, Any]]:
    """Verbatim steps for the operator, in SOP order: the first two (the
    immediate response, such as acknowledging and calling the technician) and
    the steps the model cited. Without citations, the first ones."""
    steps = context.get("steps") or []
    wanted = set(cited) | {s["n"] for s in steps[:2]}
    chosen = [s for s in steps if s["n"] in wanted] if cited else steps
    return [{"step": s["n"], "page": s["page"], "text": s["text"], "source": "verbatim SOP text"} for s in chosen[:limit]]
