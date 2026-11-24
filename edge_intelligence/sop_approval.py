"""Approval status of the plant's own SOPs, from one site setting.

The bundled plant SOPs (the procedures behind the eight incidents, the
handover SOP and the source manual) are written as "Revision 0 — Draft for
review". A site that has adopted them sets

    OPSLENS_PLANT_SOPS_APPROVED=true   (default)

and every screen, the AI prompt, the Next Best Action labels and Evidence
Support treat them as approved procedures. Set it to false to show their
recorded draft status again. Uploaded PDFs keep the approval recorded when
they were ingested; this setting never changes them. The source PDFs are
never modified.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any, Dict, Optional

APPROVED_STATUS = "approved"            # machine value of approval_status
APPROVED_TEXT = "Approved for use"      # shown to people
APPROVAL_REFERENCE = "Plant SOP, approved for use at this site"
APPROVED_DOCUMENT_STATUS = "Approved for use at this plant."
_DRAFT_WORDS = re.compile(r"\s*\(?\s*draft for review\s*\)?", re.I)


def plant_sops_approved() -> bool:
    return os.getenv("OPSLENS_PLANT_SOPS_APPROVED", "true").strip().lower() not in ("0", "false", "no", "off")


@lru_cache(maxsize=1)
def plant_sop_ids() -> frozenset:
    ids = {"SOP-GEN-003", "SOP-MAN-000"}
    try:
        import incident_catalog
        ids |= {s.sop_id for s in incident_catalog.SCENARIOS if s.sop_id}
    except Exception:
        pass
    try:
        from demo_sop_sections import DEMO_SOP_SECTIONS, DRAFT_SOPS
        ids |= set(DEMO_SOP_SECTIONS) | set(DRAFT_SOPS)
    except Exception:
        pass
    return frozenset(ids)


def is_plant_sop(sop_id: Optional[str]) -> bool:
    return bool(sop_id) and str(sop_id) in plant_sop_ids()


def approved(sop_id: Optional[str], recorded: Any = False) -> bool:
    """Whether this SOP is approved: the site setting for plant SOPs, else what was recorded."""
    if plant_sops_approved() and is_plant_sop(sop_id):
        return True
    return recorded is True


def apply(record: Optional[Dict[str, Any]], sop_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The record with its approval fields set for an approved plant SOP (in place, also returned)."""
    if not isinstance(record, dict):
        return record
    sid = sop_id or record.get("sop_id")
    if not (plant_sops_approved() and is_plant_sop(sid)):
        return record
    record["approved"] = True
    if "approval_status" in record or "document_status" in record:
        record["approval_status"] = APPROVED_STATUS
        record["approval_reference"] = record.get("approval_reference") or APPROVAL_REFERENCE
    if "document_status" in record:
        record["document_status"] = APPROVED_DOCUMENT_STATUS
    if "sop_approval_status" in record:
        record["sop_approval_status"] = APPROVED_TEXT
    if isinstance(record.get("status"), str) and re.search(r"DRAFT|NOT APPROVED", record["status"], re.I):
        record["status"] = "APPROVED"
    for key in ("revision_label", "revision"):
        if isinstance(record.get(key), str) and _DRAFT_WORDS.search(record[key]):
            record[key] = _DRAFT_WORDS.sub("", record[key]).strip() or record[key]
    if "manual_review_required" in record:
        record["manual_review_required"] = False
    return record
