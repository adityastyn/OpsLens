"""Incident diagnosis: Evidence Engine facts -> AI explanation (Groq, or a local model in LM Studio).

The Evidence Engine and operator brief are the factual source. This module
turns one incident's facts into a compact prompt, asks the local model
(Groq in the cloud, or a Qwen model in LM Studio on this computer) for an
eight-section diagnosis, streams it,
validates it against the facts, and stores the result.

Rules enforced here, not just requested in the prompt:
  * only this incident's tags go into the prompt (no cross-incident evidence);
  * readings are what happened DURING the incident as well as their value now,
    so a diagnosis read after recovery still describes the real event;
  * the output is checked line by line: a line that cites a foreign tag, an
    invented number or a non-existent SOP step is removed, and the removal is
    reported. The whole answer is rejected only when too little survives, or
    when it claims to have operated equipment or calls a draft SOP approved;
  * the source of every answer is labelled (see SOURCE_LABELS). A deterministic
    answer is never shown as AI output;
  * one generation runs per incident context. A browser that leaves or reloads
    re-attaches to the same job, so the model is never asked the same thing
    twice in parallel;
  * OpsLens is read-only: the prompt forbids control actions.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import requests

logger = logging.getLogger("opslens.ai_diagnosis")

SECTIONS = [
    "What is happening",
    "Evidence supporting the diagnosis",
    "Possible causes",
    "Recommended checks from SOP",
    "Resolution and recovery check",
    "Additional advisory checks",
    "Missing information",
    "Confidence and limitations",
]
# Changing the instructions must not serve answers written under the old ones.
PROMPT_VERSION = "8"  # 8: earlier fixes are context, not evidence
FALLBACK_LABEL = "DETERMINISTIC FALLBACK — not AI-generated"
NO_SOP_SENTENCE = "No directly matching approved SOP was found for this incident."
SOURCE_LABELS = {
    "ai_generated": "AI-generated",
    "deterministic_fallback": "Deterministic fallback",
    "sop_retrieval_only": "SOP retrieval only",
    "ai_unavailable": "AI unavailable",
}
FIX_HINTS = {
    "unreachable": "Start LM Studio's Local Model API (or run start_lmstudio.bat), then press Retry.",
    "model_missing": "Download the model in LM Studio, or set EDGE_LLM_MODEL to a model LM Studio lists, then press Retry.",
    "context": "Reload the model in LM Studio with Context Length 8192 or more, then press Retry.",
    "timeout": "The model took too long. Press Retry, raise OPSLENS_DIAG_TOTAL_TIMEOUT, or use a faster model.",
    "unavailable": "Check the model service, then press Retry.",
    "rate_limit": "The Groq limit was reached. Wait a minute, then press Retry.",
    "auth": "Check GROQ_API_KEY or GROQ_API_KEYS in edge_intelligence\\.env, then restart OpsLens.",
    "invalid_request": "Groq refused the request. Check OPSLENS_GROQ_MODEL, then press Retry.",
    "empty": "The model returned nothing. Press Retry.",
    "rejected": "The model's answer did not match the incident record. Press Retry, or use the evidence shown here.",
    "provider_off": "Set OPSLENS_LLM_PROVIDER=groq or lmstudio in edge_intelligence\\.env to enable AI.",
    "superseded": "A newer diagnosis is on its way.",
}
MAX_STORY_STEPS = 12
MAX_SOP_STEPS = 8
MAX_READINGS = 10
MAX_STEP_CHARS = 200
MAX_PASSAGE_CHARS = 320
# Sent to the model only. SOP steps are never shortened: a cut step can lose a safety condition.
PROMPT_PASSAGES = 2
PROMPT_PASSAGE_CHARS = 240
PROMPT_UPLOADED_PASSAGES = 1
MAX_REMOVED_SHARE = 0.75
# The answer is still shown when at least this many checked lines survive.
MIN_KEPT_LINES = 6
MAX_OPERATOR_CHECKS = 7

SYSTEM_PROMPT = (
    "You are an experienced wastewater process engineer helping a control-room operator diagnose one incident. "
    "Write plain, short sentences. Use ONLY the FACTS given. Never invent readings, tags, limits, SOP steps, "
    "equipment states or times. Copy every number exactly as it appears in FACTS, with its unit. Do not calculate "
    "differences, ratios or percentages, and do not write dates. "
    "Start each bullet with CONFIRMED (a recorded reading, alarm or status in FACTS), HYPOTHESIS (a possible cause) "
    "or UNKNOWN (not in FACTS). A cause is always HYPOTHESIS, never proven. The order of events does not prove cause. "
    "The ranking score is NOT a probability.\n"
    "Section rules:\n"
    "- Evidence supporting the diagnosis: one CONFIRMED bullet per abnormal reading or trip, with its worst value, its "
    "normal range and the SOP limit it passed. Most important first.\n"
    "- Possible causes: first the 'Leading hypothesis', called the most likely cause. Then each candidate in order: a "
    "HYPOTHESIS bullet with its role, supporting readings and anything against it. CONTRIBUTING causes may act together "
    "with the leading one; a RELATED CONDITION is a step from it to the alarm; a POSSIBLE CONSEQUENCE is an effect, not a cause; "
    "INSUFFICIENT EVIDENCE: related readings contradict it. Disagree with the leading hypothesis "
    "only if FACTS contradicts it, and say why. A recorded trip is a fact; what caused the trip is a hypothesis.\n"
    "- Recommended checks from SOP: when FACTS lists an SOP, it DOES apply. Choose the steps that matter most now, in "
    "order. Write each as 'SOP step N (p.P): ' plus a plain instruction of at most 15 words starting with a verb. "
    "Never write that no SOP or SOP step covers the event.\n"
    "- Resolution and recovery check: for each likely cause, the SOP steps that fix it, written like the checks, "
    "then the readings that show recovery, using the numbers from "
    "'Recovery criteria'. Restarting a pump or resetting a "
    "trip is only allowed under the SOP's conditions; say so.\n"
    "- Additional advisory checks: each starts with 'Not in the SOP:'.\n"
    "- Missing information: what must be confirmed in the field.\n"
    "- Confidence and limitations: the most likely cause and what would confirm it.\n"
    "Never call a draft SOP approved. If FACTS lists details the SOP does not address, "
    "mention them. "
    f"Only if FACTS says 'SOP: none', write '{NO_SOP_SENTENCE}' under 'Recommended checks from SOP', give cautious "
    "evidence-based checks under 'Additional advisory checks' labelled GENERAL ADVICE, and recommend escalation to an "
    "authorized engineer. "
    "OpsLens is advisory and read-only: never say you started, stopped, reset, acknowledged or cleared anything. "
    "Write exactly these eight markdown headings, in order, with at most five short bullets under each:\n"
    + "\n".join(f"## {i}. {name}" for i, name in enumerate(SECTIONS, 1))
)

_TAG_RE = re.compile(r"\b(?:[A-Z]{2,5}-){1,3}[A-Z0-9]{1,6}(?:-[A-Z0-9]{1,6})*\b")
_NUM_RE = re.compile(r"(?<![A-Za-z0-9-])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![A-Za-z0-9-])\d+(?:\.\d+)?")
_STEP_RE = re.compile(r"\bsteps?\s+(\d{1,2}(?:\s*(?:,|and|to|-|–)\s*\d{1,2})*)", re.I)
_CONTROL_CLAIMS = re.compile(
    r"\b(I|OpsLens|the system)\s+(have|has|will|did)?\s*(started|stopped|reset|acknowledged|cleared|opened|closed|switched)\b",
    re.I)
_APPROVED_CLAIM = re.compile(r"(?<!not )(?<!un)\bapproved (SOP|procedure)\b", re.I)
_HEADING_RE = re.compile(r"^\s*#{1,4}\s")
_DENIES_SOP = re.compile(r"\b(no|not any|without( an?)?)\s+(\w+\s+){0,2}(sop|procedure)(\s+step)?s?\b|"
                         r"\bsop\b[^.]{0,30}\b(does not|doesn't|do not)\s+(cover|apply|address this event)", re.I)


# --------------------------------------------------------------------------- facts

def _hms(ts: Optional[str]) -> str:
    if not ts:
        return "?"
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).strftime("%H:%M:%S UTC")
    except ValueError:
        return str(ts)


def _fmt(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    try:
        v = float(value)
    except (TypeError, ValueError):
        return value
    return int(v) if v.is_integer() else round(v, 3)


def _process_area(brief: Dict[str, Any]) -> Optional[str]:
    import incident_catalog
    return incident_catalog.PLANT_EQUIPMENT.get(brief["problem"].get("equipment"))


def build_facts(full_event: Dict[str, Any], db=None) -> Dict[str, Any]:
    """Compact, incident-scoped facts for the model and the fallback."""
    from operator_brief import build_operator_brief
    from tag_registry import TAG_REGISTRY

    brief = build_operator_brief(full_event, db)
    problem = brief["problem"]
    scenario = brief.get("scenario") or {}
    own = set(scenario.get("incident_tags") or [])
    if not own:
        cand = (full_event.get("candidate") or {})
        own = set(cand.get("clustered_tags") or []) | {full_event.get("primary_tag")}
    own.discard(None)

    def label(tag: str) -> str:
        entry = TAG_REGISTRY.get(tag)
        return getattr(entry, "description", None) or tag

    readings: List[Dict[str, Any]] = []
    seen = set()
    for row in brief.get("incident_readings") or []:
        if row["tag"] not in own:
            continue
        seen.add(row["tag"])
        limit = None
        if row.get("limit_text") or row.get("limit_action"):
            limit = f"{row.get('limit_text') or row.get('limit_reached')} -> {row.get('limit_action')} (SOP p.{row.get('limit_page')})"
        readings.append({
            "tag": row["tag"], "name": row["label"], "unit": row["unit"], "normal": row.get("normal_text"),
            "start": _fmt(row["first_value"]), "worst": _fmt(row["worst_value"]), "worst_at": _hms(row["worst_at"]),
            "now": _fmt(row["now_value"]), "state_at_worst": row["state_at_worst"], "state_now": row["state_now"],
            "limit": limit,
        })
    for chk in brief["supporting_evidence"]["limit_checks"]:
        if chk["tag"] not in own or chk["tag"] in seen:
            continue
        seen.add(chk["tag"])
        readings.append({"tag": chk["tag"], "name": chk.get("parameter") or label(chk["tag"]), "unit": chk.get("unit"),
                         "normal": chk.get("normal_text"), "start": None, "worst": _fmt(chk.get("value")), "worst_at": None,
                         "now": _fmt(chk.get("value")), "state_at_worst": (chk.get("normal_state") or "").replace("_", " "),
                         "state_now": (chk.get("normal_state") or "").replace("_", " "), "limit": None})
    if db is not None:
        for tag in sorted(own - seen):
            entry = TAG_REGISTRY.get(tag)
            if entry is None or entry.unit in ("bool", "status") or tag.startswith("ALM-"):
                continue
            row = db.get_latest_reading(tag)
            if row is not None and not isinstance(row.value, bool):
                readings.append({"tag": tag, "name": label(tag), "unit": entry.unit, "normal": None, "start": None,
                                 "worst": _fmt(row.value), "worst_at": None, "now": _fmt(row.value),
                                 "state_at_worst": "no SOP limit", "state_now": "no SOP limit", "limit": None})
    readings = readings[:MAX_READINGS]

    sequence = []
    for step in (full_event.get("story") or {}).get("steps") or []:
        if step.get("tag") not in own:
            continue
        sequence.append({"time": _hms(step.get("timestamp")), "tag": step["tag"],
                         "change": f"{_fmt(step.get('value_before'))} -> {_fmt(step.get('value_after'))} {step.get('unit') or ''}".strip()})
    if len(sequence) > MAX_STORY_STEPS:
        sequence = sequence[:4] + sequence[-(MAX_STORY_STEPS - 4):]

    changes = [{"tag": c["tag"], "change_pct": c["change_percent_vs_baseline_midpoint"]}
               for c in brief["supporting_evidence"]["calculated"] if c["tag"] in own][:8]

    evidence = full_event.get("evidence") or {}
    ranking = None
    if evidence.get("likely_contributor_tag") in own:
        ranking = {"tag": evidence["likely_contributor_tag"], "score": evidence.get("confidence_score"),
                   "note": "evidence-ranking score, NOT a probability of root cause"}

    from sop_context import build_sop_context
    context = build_sop_context(full_event, brief)
    sop_facts = None
    if context["sop_id"]:
        sop_facts = {
            "sop_id": context["sop_id"], "title": context["title"], "revision": context["revision"],
            "source_document": context["source_document"], "status": context["approval_label"],
            "approved": context["approved"], "coverage": context["status"], "pages": context["pages"],
            "match_signals": context.get("match_signals") or {},
            "steps": context["steps"], "passages": context["passages"], "covered": context["covered"],
            "not_covered": context["not_covered"], "notes": context["notes"],
        }

    from cause_candidates import build_cause_candidates
    try:
        candidates = build_cause_candidates(full_event, brief, own, db)
    except Exception as exc:
        logger.warning("Cause candidates unavailable: %s: %s", type(exc).__name__, exc)
        candidates = []

    import incident_insight
    try:
        history = incident_insight.history_reference(full_event, db)
        pre_alarm = incident_insight.pre_alarm_activity(full_event, db)
    except Exception as exc:
        logger.warning("History or pre-alarm activity unavailable: %s: %s", type(exc).__name__, exc)
        history, pre_alarm = {"same_alarm_total": 0, "same_alarm_last_24h": 0, "earlier": []}, []
    for item in pre_alarm:
        if item["kind"] == "operator_action" and len(candidates) < 5:
            candidates.append({
                "kind": "action", "rank": len(candidates) + 1, "status": "HYPOTHESIS", "equipment": item.get("equipment"),
                "tags": [], "statement": f"An operator action on this equipment shortly before the alarm: {item['text']}.",
                "basis": f"Recorded {item['minutes_before_alarm']} min before the alarm",
                "first_seen": item["timestamp"], "before_alarm": True, "supporting": [item["text"]],
                "contradicting": ["Timing alone does not prove the action caused the alarm"],
            })

    alarms = [a for a in problem.get("active_alarm_inputs") or [] if a in own]
    return {
        "event_id": full_event.get("event_id"),
        "scenario_id": scenario.get("scenario_id"),
        # Named by its alarm, never by the scenario: scenario names and ids state
        # the injected fault ("RAS pump fails"), which is the answer to reason towards.
        "incident": problem.get("alarm_description") or problem.get("alarm_tag"),
        "process_area": _process_area(brief),
        "equipment": problem.get("equipment"),
        "primary_alarm": {"tag": problem.get("alarm_tag"), "description": problem.get("alarm_description"),
                          "first_at": _hms(problem.get("first_triggered_at")),
                          "input_active": problem.get("alarm_input_active")},
        "data_source": problem.get("data_source"),
        "incident_status": full_event.get("status"),
        "incident_tags": sorted(own),
        "active_alarms": alarms,
        "readings": readings,
        "sequence": sequence,
        "changes_vs_normal_pct": changes,
        "ranking": ranking,
        "cause_candidates": candidates,
        "plant_links": __import__("knowledge_graph").incident_links(full_event.get("equipment_id") or (brief.get("problem") or {}).get("equipment"), own),
        "history": history,
        "pre_alarm": pre_alarm,
        "sop": sop_facts,
        "sop_context": context,
        "uploaded_passages": _uploaded_passages(full_event, sop_facts),
        "verification": [v for v in brief.get("verification_criteria") or []][:3],
        "missing": [m for m in brief.get("missing_evidence") or [] if "STALE" not in m][:4],
    }


def _uploaded_passages(full_event: Dict[str, Any], sop_facts: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Uploaded plant documents that match this incident, so an uploaded SOP
    really reaches the model. Keyword search; failure leaves the answer intact."""
    try:
        from sop_retriever import uploaded_document_passages
        story = full_event.get("story") or {}
        query = " ".join(str(x) for x in (
            full_event.get("primary_tag"), story.get("causal_label"),
            " ".join(str(s.get("description")) for s in (story.get("steps") or [])[:8])) if x)
        return uploaded_document_passages(query, exclude_sop=(sop_facts or {}).get("sop_id"), max_chars=MAX_PASSAGE_CHARS)
    except Exception as exc:
        logger.warning("Uploaded SOP passages unavailable: %s: %s", type(exc).__name__, exc)
        return []


def stable_facts(facts: Dict[str, Any]) -> Dict[str, Any]:
    """The part of the facts that defines the answer. Excludes readings' live
    'now' noise, so an unrelated simulation cannot invalidate a stored answer.
    Evidence, alarms, SOP, status or a real recovery do change the hash."""
    # History and pre-alarm activity grow as other incidents happen; they do not
    # change what this incident's answer is about.
    stable = {k: v for k, v in facts.items() if k not in ("readings", "missing", "sop_context", "history", "pre_alarm")}
    stable["prompt_version"] = PROMPT_VERSION
    stable["readings"] = [{"tag": r["tag"], "worst": r["worst"], "state_at_worst": r["state_at_worst"],
                           "state_now": r["state_now"]} for r in facts.get("readings") or []]
    return stable


def facts_hash(facts: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(stable_facts(facts), sort_keys=True, default=str).encode()).hexdigest()[:16]


def _reading_line(r: Dict[str, Any]) -> str:
    unit = r.get("unit") or ""
    worst = f"worst {r.get('worst')} {unit}" + (f" at {r['worst_at']}" if r.get("worst_at") else "")
    start = f"start {r['start']} {unit}, " if r.get("start") is not None else ""
    limit = f"; limit: {r['limit']}" if r.get("limit") else ""
    return (f"- {r['tag']} {r['name']}: normal {r.get('normal') or 'not stated'}; {start}{worst} "
            f"({r.get('state_at_worst')}); now {r.get('now')} {unit} ({r.get('state_now')}){limit}")


def build_prompt(facts: Dict[str, Any]) -> str:
    """User message: labelled plain lines. Far fewer tokens than JSON, which
    matters most for prompt evaluation on a CPU."""
    pa = facts["primary_alarm"]
    state = {True: "still active", False: "returned to normal"}.get(pa.get("input_active"), "state not observed")
    lines = [
        f"FACTS for one incident. Data source: {facts.get('data_source')}.",
        f"Incident {str(facts.get('event_id'))[:8]} | "
        f"{facts.get('incident')} | area {facts.get('process_area') or 'not recorded'} | equipment {facts.get('equipment')}",
        f"Primary alarm: {pa.get('tag')} {pa.get('description')}, first {pa.get('first_at')}, {state}.",
        "Other active alarm inputs: " + (", ".join(a for a in facts["active_alarms"] if a != pa.get("tag")) or "none"),
        "Tags in this incident: " + ", ".join(facts.get("incident_tags") or []),
        "Readings:",
        *[_reading_line(r) for r in facts["readings"]],
        "Recorded order of events (time order, not proof of cause):",
        *[f"- {s['time']} {s['tag']} {s['change']}" for s in facts["sequence"]],
    ]
    if facts.get("ranking"):
        rk = facts["ranking"]
        lines.append(f"Evidence Engine top signal: {rk['tag']}, ranking score {rk['score']} ({rk['note']}).")
    if facts.get("plant_links"):
        lines.append("Plant relationships (configured, from the knowledge graph):")
        lines += [f"- {link}" for link in facts["plant_links"]]
    history = facts.get("history") or {}
    if history.get("earlier"):
        lines.append(f"Earlier incidents (this database): same alarm {history.get('same_alarm_total', 0)} time(s), "
                     f"{history.get('same_alarm_last_24h', 0)} in the last 24 h. Context only: a past fix is not evidence "
                     "for this incident. Name causes from this incident's readings, trips and status changes; you may say "
                     "a cause matches an earlier fix only after the readings support it:")
        for e in history["earlier"]:
            done = (f"resolved in {e['minutes_to_resolve']} min" if e.get("minutes_to_resolve") is not None
                    else "resolved" if e.get("resolved") else "not resolved")
            notes = f"; resolution notes: {e['resolution_notes']}" if e.get("resolution_notes") else ""
            lines.append(f"- {'Same alarm' if e['same_alarm'] else 'Same equipment, ' + str(e['alarm'])} at {e['when_text']}, {done}{notes}")
    elif history:
        lines.append("Earlier incidents (this database): none for this alarm or equipment.")
    if facts.get("pre_alarm"):
        lines.append("Recorded in the 30 minutes before the alarm, same equipment:")
        lines += [f"- {p['minutes_before_alarm']} min before: {p['text']}" for p in facts["pre_alarm"]]
    candidates = facts.get("cause_candidates") or []
    if candidates:
        from cause_candidates import leading_summary
        lines.append(f"Leading hypothesis: {leading_summary(candidates)}")
        lines.append("Cause candidates, ordered by each one's own evidence (NOT a probability):")
        # Timing is already in the line; these are the other factors it was ranked on.
        short = {"direct": "trip", "magnitude": "past limit", "corroboration": "readings agree",
                 "sop": "SOP limit", "topology": "path to alarm", "history": "earlier fix"}
        for c in candidates:
            role = _ROLE_NAMES.get(c.get("role"), "")
            has = [short[k] for k, v in (c.get("evidence_factors") or {}).items() if v > 0 and k in short]
            has_text = f" Has: {', '.join(has)}." if has and c.get("role") in ("leading", "contributing") else ""
            lines.append(f"- Candidate {c['rank']}{' (' + role + ')' if role else ''}: {c['statement']} Basis: {c['basis']}. First seen {c.get('first_seen') or 'not recorded'}"
                         f"{', before the primary alarm' if c.get('before_alarm') else ''}.{has_text}")
            lines += [f"  supports: {x}" for x in c.get("supporting") or []]
            lines += [f"  contradicts: {x}" for x in c.get("contradicting") or []]
    sop = facts.get("sop")
    if sop:
        lines.append(f"SOP: {sop['sop_id']} {sop['title']} | revision {sop.get('revision')} | {sop['status']} | "
                     f"source {sop.get('source_document')} | pages {sop.get('pages')} | coverage {sop['coverage']}. This SOP applies to this incident.")
        lines += [f"- SOP step {s['n']} (p.{s['page']}): {s['text']}" for s in sop["steps"]]
        lines += [f"- SOP passage (p.{p['page']}): {p['text'][:PROMPT_PASSAGE_CHARS]}" for p in [p for p in sop.get("passages") or [] if (p.get("matched_terms") or 0) >= 2][:PROMPT_PASSAGES]]
        if sop.get("covered"):
            lines.append("Incident readings the SOP covers: " + ", ".join(sop["covered"]))
        details = [*(f"reading {name}" for name in sop.get("not_covered") or []), *(str(n)[:170] for n in sop.get("notes") or [])]
        if details:
            lines.append("Details the SOP does not address: " + " | ".join(details))
    else:
        lines.append(f"SOP: none. {NO_SOP_SENTENCE}")
    for p in (facts.get("uploaded_passages") or [])[:PROMPT_UPLOADED_PASSAGES]:
        lines.append(f"Uploaded document {p['sop_id']} p.{p['page']} ({p['status']}): {p['text']}")
    if facts.get("verification"):
        lines.append("Recovery criteria: " + " | ".join(str(v)[:140] for v in facts["verification"]))
    if facts.get("missing"):
        lines.append("Known gaps: " + " | ".join(str(m)[:140] for m in facts["missing"]))
    lines.append("Write the eight-section diagnosis now.")
    return "\n".join(lines)


# ----------------------------------------------------------------- validation

def _to_number(text: str) -> Optional[float]:
    try:
        return round(float(text.replace(",", "")), 3)
    except ValueError:
        return None


def _allowed_numbers(facts: Dict[str, Any]) -> set:
    text = json.dumps(facts, ensure_ascii=False, default=str) + "\n" + build_prompt(facts)
    return {n for n in (_to_number(m) for m in _NUM_RE.findall(text)) if n is not None}


_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
_CLOCK_RE = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?\b")
_APPROX_RE = re.compile(r"(about|approx\w*|roughly|around|nearly|almost|~|≈)\s*$", re.I)
_DURATION_RE = re.compile(r"^\s*(s|sec|secs|seconds?|min|mins|minutes?)\b", re.I)


def _timestamps(text: str) -> List[datetime]:
    out = []
    for raw in sorted(set(_ISO_RE.findall(text)))[:80]:
        try:
            out.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            continue
    return out


def _clock_times(facts: Dict[str, Any]) -> set:
    text = json.dumps(facts, ensure_ascii=False, default=str)
    times = {t.strftime("%H:%M") for t in _timestamps(text)}
    times |= {":".join(f"{int(x):02d}" for x in c.split(":")[:2]) for c in _CLOCK_RE.findall(text)}
    return times


def _durations(facts: Dict[str, Any]) -> Tuple[set, set]:
    """Seconds and minutes between any two recorded times of this incident."""
    text = json.dumps(facts, ensure_ascii=False, default=str)
    stamps = _timestamps(text)
    clock = sorted({c for c in _CLOCK_RE.findall(text) if c.count(":") == 2})
    secs = set()
    for i, x in enumerate(stamps):
        for y in stamps[i + 1:]:
            secs.add(abs((x - y).total_seconds()))
    marks = [int(h) * 3600 + int(m) * 60 + int(sec) for h, m, sec in (c.split(":") for c in clock)]
    for i, x in enumerate(marks):
        for y in marks[i + 1:]:
            secs.add(float(abs(x - y)))
    return secs, {v / 60 for v in secs}


def _reading_numbers(reading: Dict[str, Any]) -> set:
    text = json.dumps({k: v for k, v in reading.items() if not k.endswith("_at")}, ensure_ascii=False, default=str)
    return {abs(n) for n in (_to_number(m) for m in _NUM_RE.findall(_CLOCK_RE.sub(" ", text))) if n not in (None, 0)}


def _line_base(line: str, facts: Dict[str, Any], allowed: set) -> set:
    """Values a sentence may compute from: the readings it names and the record
    numbers it quotes. A derived number must come from these, so a made-up
    reading cannot pass as 'a difference of something'."""
    low = line.lower()
    base = set()
    for r in facts.get("readings") or []:
        name = str(r.get("name") or r.get("label") or "")
        names = [str(r.get("tag") or ""), name, " ".join(name.split()[:2]) if len(" ".join(name.split()[:2])) >= 8 else ""]
        if any(n and n.lower() in low for n in names) or (r.get("tag") and "-".join(str(r["tag"]).split("-")[:2]).lower() in low):
            base |= _reading_numbers(r)
    for m in _NUM_RE.findall(_CLOCK_RE.sub(" ", _TAG_RE.sub(" ", line))):
        n = _to_number(m)
        if n and _number_known(n, allowed):
            base.add(abs(n))
    return base


def _close(value: float, target: float, pct: float) -> bool:
    return abs(value - target) <= max(abs(target) * pct, 0.06)


def _derived(value: float, base: set, percent: bool, pct: float) -> bool:
    values = sorted(base)
    for i, x in enumerate(values):
        for y in values:
            if x == y:
                continue
            if percent:
                if _close(value, abs(x - y) / y * 100, pct) or _close(value, x / y * 100, pct):
                    return True
            elif _close(value, abs(x - y), pct) or _close(value, x / y, pct):
                return True
    return False


def _number_known(value: float, allowed: set, approx: bool = False) -> bool:
    if value <= 10 and float(value).is_integer():
        return True  # list numbering, section and step numbers
    if value in allowed:
        return True
    # The model may round 408.27 to 408; that is not an invented reading.
    margin = max(abs(value) * 0.006, 0.06)
    return any(abs(value - known) <= margin for known in allowed)


def _number_ok(value: float, line: str, start: int, end: int, facts: Dict[str, Any], allowed: set,
               base: set, durations: Tuple[set, set]) -> bool:
    if _number_known(value, allowed):
        return True
    approx = bool(_APPROX_RE.search(line[max(0, start - 16):start]))
    pct = 0.05 if approx else 0.02
    after = line[end:end + 12]
    if _DURATION_RE.match(after):
        pool = durations[1] if after.strip().lower().startswith("min") else durations[0]
        return any(_close(value, d, pct) or abs(value - d) <= 1 for d in pool)
    if approx and any(_close(value, b, pct) for b in base):
        return True  # "about 2,100 m³/h" for a recorded 2,150 m³/h
    return _derived(value, base, after.lstrip().startswith("%") or after.lstrip().lower().startswith("per"), pct)


def _time_known(clock: str, times: set) -> bool:
    hours, minutes = (int(x) for x in clock.split(":")[:2])
    for delta in (-1, 0, 1):
        m = (hours * 60 + minutes + delta) % 1440
        if f"{m // 60:02d}:{m % 60:02d}" in times:
            return True
    return False


def _steps_cited(line: str) -> set:
    cited = set()
    for match in _STEP_RE.finditer(line):
        numbers = [int(n) for n in re.findall(r"\d{1,2}", match.group(1))]
        if re.search(r"\b(to|-|–)\b|[-–]", match.group(1)) and len(numbers) == 2 and numbers[0] <= numbers[1]:
            numbers = list(range(numbers[0], numbers[1] + 1))
        cited.update(numbers)
    return cited


# gpt-oss and other large models write "P‑101" with a non-breaking hyphen
# (U+2011) and use narrow no-break spaces. Unnormalised, "P‑101" is not seen
# as a tag and "101" counts as an invented number, so good lines were removed.
_HYPHENS = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2212\ufe63\uff0d"), "-")
_SPACES = dict.fromkeys(map(ord, "\u00a0\u202f\u2007\u2009\u200a"), " ")
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)


def normalize_model_text(text: str) -> str:
    """Plain hyphens and spaces, no invisible characters. En and em dashes stay."""
    return (text or "").translate(_HYPHENS).translate(_SPACES).translate(_ZERO_WIDTH)


def _line_problems(line: str, facts: Dict[str, Any], allowed_refs: set, allowed_numbers: set,
                   step_numbers: set, partial_refs: set, clock_times: Optional[set] = None,
                   durations: Optional[Tuple[set, set]] = None) -> List[str]:
    problems: List[str] = []
    found = {t for t in _TAG_RE.findall(line) if any(ch.isdigit() for ch in t)}
    for tag in sorted(found):
        if tag in allowed_refs or tag in partial_refs:
            continue
        problems.append(f"SOP not mapped to this incident: {tag}" if tag.startswith("SOP-")
                        else f"tag not in this incident: {tag}")
    bare = _TAG_RE.sub(" ", line)
    times = clock_times if clock_times is not None else _clock_times(facts)
    bad_times = [c for c in _CLOCK_RE.findall(bare) if not _time_known(c, times)]
    if bad_times:
        problems.append("time not in the incident record: " + ", ".join(bad_times))
    bare = _CLOCK_RE.sub(" ", bare)
    unknown = []
    base = None
    for match in _NUM_RE.finditer(bare):
        n = _to_number(match.group(0))
        if n is None or _number_known(n, allowed_numbers):
            continue
        if base is None:
            base = _line_base(line, facts, allowed_numbers)
        if not _number_ok(abs(n), bare, match.start(), match.end(), facts, allowed_numbers, base, durations or _durations(facts)):
            unknown.append(match.group(0))
    if unknown:
        problems.append("number not in the incident record: " + ", ".join(unknown))
    bad = sorted(_steps_cited(line) - step_numbers)
    if bad:
        problems.append(f"SOP step that does not exist: {bad}")
    if facts.get("sop") and _DENIES_SOP.search(line) and not re.search(r"not in the sop", line, re.I):
        problems.append("says no SOP applies, but a matching SOP was retrieved")
    return problems


def line_checker(facts: Dict[str, Any]) -> Callable[[str], List[str]]:
    """The same per-line record check as validate(), for other model answers
    (the repair pass and Ask OpsLens)."""
    own = set(facts.get("incident_tags") or [])
    sop = facts.get("sop") or {}
    facts_text = json.dumps(facts, ensure_ascii=False, default=str)
    refs = own | {sop.get("sop_id"), "SOP-MAN-000"} | {f"SOP-GEN-00{i}" for i in range(1, 10)} | set(_TAG_RE.findall(facts_text))
    refs.discard(None)
    partial = {"-".join(t.split("-")[:n]) for t in refs for n in range(2, len(t.split("-")))}
    numbers = _allowed_numbers(facts)
    steps = {st["n"] for st in sop.get("steps") or []}
    times = _clock_times(facts)
    durations = _durations(facts)

    def check(line: str) -> List[str]:
        line = normalize_model_text(line)
        problems = _line_problems(line, facts, refs, numbers, steps, partial, times, durations)
        if _CONTROL_CLAIMS.search(line):
            problems.append("claims OpsLens operated equipment")
        return problems
    return check


def validate(text: str, facts: Dict[str, Any]) -> Dict[str, Any]:
    """Check model output against the facts, line by line.

    Lines that fail are dropped from 'clean_text' and listed in 'removed_lines'.
    'valid' is False when the answer as a whole cannot be shown."""
    text = normalize_model_text(text)
    rejected: List[str] = []
    warnings: List[str] = []
    own = set(facts.get("incident_tags") or [])
    sop = facts.get("sop") or {}
    sop_id = sop.get("sop_id")
    facts_text = json.dumps(facts, ensure_ascii=False, default=str)
    allowed_refs = own | {sop_id, "SOP-MAN-000"} | {f"SOP-GEN-00{i}" for i in range(1, 10)} | set(_TAG_RE.findall(facts_text))
    allowed_refs.discard(None)
    # "FIT-102" for "FIT-102-FLW" is an abbreviation of a real tag, not a new one.
    partial_refs = {"-".join(tag.split("-")[:n]) for tag in allowed_refs for n in range(2, len(tag.split("-")))}
    allowed_numbers = _allowed_numbers(facts)
    step_numbers = {s["n"] for s in sop.get("steps") or []}
    clock_times = _clock_times(facts)
    durations = _durations(facts)

    kept: List[str] = []
    removed: List[Dict[str, Any]] = []
    content_lines = 0
    section = None
    for line in text.splitlines():
        if _HEADING_RE.match(line):
            section = next((name for name in SECTIONS if name.lower() in line.lower()), section)
        if not line.strip() or _HEADING_RE.match(line):
            kept.append(line)
            continue
        content_lines += 1
        problems = _line_problems(line, facts, allowed_refs, allowed_numbers, step_numbers, partial_refs, clock_times, durations)
        if problems:
            removed.append({"line": line.strip(), "problems": problems, "section": section})
        else:
            kept.append(line)
    clean = "\n".join(kept).strip()

    lower = clean.lower()
    missing_headings = [s for s in SECTIONS if s.lower() not in lower]
    if len(missing_headings) > 2:
        rejected.append(f"missing sections: {', '.join(missing_headings)}")
    elif missing_headings:
        warnings.append(f"missing sections: {', '.join(missing_headings)}")
    kept_lines = content_lines - len(removed)
    if content_lines and (len(removed) / content_lines > MAX_REMOVED_SHARE or kept_lines < min(MIN_KEPT_LINES, content_lines)):
        rejected.append(f"{len(removed)} of {content_lines} lines did not match the incident record")
    if _CONTROL_CLAIMS.search(text):
        rejected.append("claims to have operated equipment or changed alarm state")
    if _APPROVED_CLAIM.search(text.replace(NO_SOP_SENTENCE, "")) and not sop.get("approved"):
        rejected.append("describes the draft SOP as approved")
    if not sop_id and NO_SOP_SENTENCE.lower() not in lower:
        warnings.append("no-SOP sentence missing")
    if removed:
        warnings.append(f"{len(removed)} line(s) removed because they did not match the incident record")
    cited = set()
    for line in kept:
        cited |= _steps_cited(line)
    unverified = sorted({n for r in removed for p in r["problems"] if p.startswith("number")
                         for n in p.split(": ", 1)[1].split(", ")})
    return {"valid": not rejected, "rejected_reasons": rejected, "warnings": warnings,
            "cited_sop_steps": sorted(cited & step_numbers), "unverified_numbers": unverified,
            "removed_lines": removed, "clean_text": clean}


def add_sop_checks(result: Dict[str, Any], facts: Dict[str, Any]) -> None:
    """SOP fields come from the source, not from the model's wording."""
    from sop_context import checks_from_sop
    context = facts.get("sop_context") or {}
    cited = (result.get("validation") or {}).get("cited_sop_steps") or []
    result["sop_context"] = {k: v for k, v in context.items()}
    result["sop_checks"] = checks_from_sop(context, cited)
    result["plan"] = build_plan(result, facts)
    result["leading"] = leading_check(result, facts)


_MOST_LIKELY = re.compile(r"most likely|likeliest|leading hypothesis|probabl", re.I)
_ROLE_NAMES = {"leading": "LEADING", "contributing": "CONTRIBUTING", "chain": "RELATED CONDITION",
               "effect": "POSSIBLE CONSEQUENCE", "insufficient": "INSUFFICIENT EVIDENCE"}
_ROLE_PREFIX = re.compile(r"^(LEADING|CONTRIBUTING|CHAIN LINK|LIKELY EFFECT|RELATED CONDITION|POSSIBLE CONSEQUENCE|INSUFFICIENT EVIDENCE)\s*[.:]\s*", re.I)
# A longer 'most likely' line is still checked, but the shared sentence is shown.
MAX_LEAD_CHARS = 200


def leading_check(result: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    """Every screen shows the record's leading hypothesis. The AI's own
    'most likely' line is shown only when it names the same leading cause;
    otherwise the record's sentence is shown and the difference is reported,
    so the panels never disagree without saying so."""
    from cause_candidates import leading_summary, mentioned
    candidates = facts.get("cause_candidates") or []
    record = leading_summary(candidates)
    card = result.get("operator_card") or {}
    # Lines OpsLens added to the causes are not the AI's words.
    own = [l for l in [*(card.get("causes") or []), *(card.get("limits") or [])] if "Added by OpsLens" not in l]
    causes = [l for l in card.get("causes") or [] if "Added by OpsLens" not in l]
    ai_line = next((l for l in own if _MOST_LIKELY.search(l)), causes[0] if causes else None)
    ai_line = _ROLE_PREFIX.sub("", _LABEL_RE.sub("", (ai_line or "").lstrip("-* ").strip())) or None
    if not candidates:
        return {"record": None, "ai_line": ai_line, "agrees": None, "show": ai_line, "source": "ai" if ai_line else None}
    ai = result.get("source_status") == "ai_generated"
    agrees = bool(ai and ai_line and mentioned(candidates[0], ai_line))
    return {"record": record, "ai_line": ai_line if ai else None, "agrees": agrees if ai else None,
            "show": ai_line if agrees and len(ai_line) <= MAX_LEAD_CHARS else record, "source": "ai" if agrees else "record",
            "note": None if agrees or not ai else
            "The AI named a different cause first; its view is in the full assessment."}


_STEP_REF_RE = re.compile(r"^\s*(?:\*\*)?SOP\s+step\s+(\d{1,2})(?:\s*\(p\.?\s*(\d+)\))?(?:\*\*)?\s*[:\-–]\s*", re.I)
_LABEL_RE = re.compile(r"^(CONFIRMED|HYPOTHESIS|UNKNOWN|GENERAL ADVICE|Not in the SOP)\s*:\s*", re.I)
_KINDS = (
    ("CALL", re.compile(r"^(call|notify|inform|escalate|contact|report|request|ask|dispatch|send)\b", re.I)),
    ("VERIFY", re.compile(r"^(verify|watch|monitor)\b|^(confirm|check|ensure|make sure)\b.*\b(returns?|stays?|remains?|recover\w*|is back|back (below|above|within)|falls? below|rises? above)\b", re.I)),
    ("CHECK", re.compile(r"^(check|confirm|inspect|look|calculate|measure|compare|note|record|review|identify|read|sample|test|walk)\b", re.I)),
    ("ADJUST", re.compile(r"^(increase|decrease|reduce|raise|lower|start|stop|switch|open|close|adjust|change|set|reset|restart|isolate|"
                          r"clear|remove|run|put|bring|divert|bypass|lock|operate|place|transfer|shut|turn)\b", re.I)),
)
_WARN_RE = re.compile(r"[^.]*\b(do not|don't|never|must not|only (if|after|when)|caution|warning|danger|lock(ed)? out)\b[^.]*\.?", re.I)
MAX_PLAN_STEPS = 6
MAX_EVIDENCE_STEPS = 3


def _kind(text: str, default: str = "CHECK") -> str:
    for name, pattern in _KINDS:
        if pattern.search(text.strip()):
            return name
    return default


def _sop_warnings(sop_text: str) -> List[str]:
    """Warnings copied word for word from the SOP step, never reworded."""
    return [m.group(0).strip() for m in _WARN_RE.finditer(sop_text or "")][:2]


_READING_RE = re.compile(r"^(?P<label>.+?) reached (?P<value>.+?)\s*\(normal (?P<normal>(?:[^()]|\([^()]*\))*)\)(?:; SOP limit (?P<limit>[^>]+?))?(?:\s*->.*)?$")


def _readings_of(c: Dict[str, Any]) -> List[Dict[str, str]]:
    """The candidate's abnormal readings, parsed from its own supporting evidence."""
    out = []
    for line in c.get("supporting") or []:
        m = _READING_RE.match(line.strip())
        if m:
            out.append({k: (v or "").strip() for k, v in m.groupdict().items()})
    return out


def _r(x: Dict[str, str]) -> str:
    return f"{x['label']} {x['value']} (normal {x['normal']}{'; limit ' + x['limit'].strip() if x.get('limit') else ''})"


def evidence_checks(facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Checks built from this incident's own evidence, before any SOP step:
    confirm what the record says about the leading and contributing causes,
    check an instrument that moved alone, and the readings to verify before
    recovery is confirmed. Checks only: OpsLens never operates equipment."""
    out: List[Dict[str, Any]] = []
    cands = facts.get("cause_candidates") or []
    sop = facts.get("sop") or {}
    recorded = set(facts.get("incident_tags") or []) | {r.get("tag") for r in facts.get("readings") or []}
    state = {r.get("tag"): r.get("state_at_worst") for r in facts.get("readings") or []}
    active = set(facts.get("active_alarms") or [])

    def refs(c: Dict[str, Any]) -> Dict[str, List[str]]:
        """The recorded tags a check rests on: the candidate's own tags (a trip or
        status input, the meter itself) and its abnormal supporting readings.
        Only tags that are in this incident's record count; none means the
        check's evidence is not identifiable and it gets no evidence label."""
        own = [t for t in c.get("tags") or [] if t in recorded]
        support = [t for t in c.get("evidence_tags") or []
                   if t in recorded and t not in own and state.get(t) not in (None, "within normal")]
        tags = own + support
        return {"evidence_tags": tags[:5], "alarm_ids": [t for t in tags if t in active]}
    for c in cands:
        if len(out) >= MAX_EVIDENCE_STEPS:
            break
        role, short = c.get("role"), c.get("short") or c.get("statement")
        rows = _readings_of(c)
        if role == "insufficient":
            normal = [x.split(" stayed")[0] for x in c.get("contradicting") or [] if "stayed within normal" in x]
            meter = rows[0]["label"] if rows else short.replace(" on its own", "").rsplit(" ", 1)[0]
            text = (f"Check the {meter} instrument before treating it as a fault"
                    + (f": {', '.join(normal)} stayed normal." if normal else "."))
        elif role not in ("leading", "contributing"):
            continue
        elif c.get("kind") in ("trip", "standby"):
            text = f"Confirm the {short[0].lower() + short[1:] if short[:2] != short[:2].upper() else short} status at the panel"
            text += f". Evidence: {_r(rows[0])}." if rows else "."
            if c.get("kind") == "trip" and sop.get("sop_id"):
                text += f" Read {sop['sop_id']}{' (draft, not approved)' if not sop.get('approved') else ''} before any reset."
        elif len(rows) > 1:
            text = f"Inspect these together for {short[0].lower() + short[1:] if short[:2] != short[:2].upper() else short}: " + "; ".join(_r(x) for x in rows[:3]) + "."
        elif rows:
            text = f"Check why {rows[0]['label']} is at {rows[0]['value']} (normal {rows[0]['normal']}"
            text += f"; limit {rows[0]['limit'].strip()})." if rows[0].get("limit") else ")."
        else:
            text = f"Check {short}."
        out.append({"text": text, "kind": "CHECK", "source": "evidence", "sop_step": None, "page": None, "sop_text": None,
                    "warnings": [], "candidate": short, "role": role, **refs(c)})
    abnormal = [r for r in facts.get("readings") or [] if r.get("state_at_worst") not in (None, "within normal") and r.get("normal")]
    if abnormal:
        names = "; ".join(f"{r['name']} ({r['normal']})" for r in abnormal[:4])
        out.append({"text": f"Verify these readings are back within normal before confirming recovery: {names}.", "kind": "VERIFY",
                    "source": "evidence", "sop_step": None, "page": None, "sop_text": None, "warnings": [], "final": True,
                    "evidence_tags": [r["tag"] for r in abnormal[:4] if r.get("tag")], "alarm_ids": []})
    return out


def build_plan(result: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    """Next Best Action: the AI's ordered plan in its own words, each step
    linked to the SOP step it comes from, with that step's exact text and
    warnings. Steps that are not from the SOP are labelled AI suggestions.
    Built only from lines that passed the record check."""
    sections = split_sections(result.get("text") or "")
    sop = facts.get("sop") or {}
    steps = {st["n"]: st for st in sop.get("steps") or []}
    ai = result.get("source_status") == "ai_generated"
    plan: List[Dict[str, Any]] = []
    used: set = set()

    def add(line: str, section: str) -> None:
        if len(plan) >= MAX_PLAN_STEPS:
            return
        text = _LABEL_RE.sub("", line.strip()).strip()
        match = _STEP_REF_RE.match(text)
        if match and steps.get(int(match.group(1))):
            n = int(match.group(1))
            if n in used:
                return
            used.add(n)
            words = text[match.end():].strip() or steps[n]["text"]
            step = steps[n]
            default = "VERIFY" if section == SECTIONS[4] and re.search(r"\b(recover|return|stays?|below|above)\b", words, re.I) else "CHECK"
            plan.append({"text": words, "kind": _kind(words, _kind(step["text"], default)), "source": "sop",
                         "sop_step": n, "page": step.get("page"), "sop_text": step["text"], "warnings": _sop_warnings(step["text"])})
        elif section == SECTIONS[5] and text and ai:
            plan.append({"text": text, "kind": _kind(text), "source": "ai", "sop_step": None, "page": None, "sop_text": None, "warnings": []})

    for line in sections.get(SECTIONS[3], []):
        add(line, SECTIONS[3])
    for line in sections.get(SECTIONS[4], []):
        add(line, SECTIONS[4])
    for line in sections.get(SECTIONS[5], [])[:2]:
        add(line, SECTIONS[5])
    if not any(item["source"] == "sop" for item in plan) and steps:
        # The model cited no step: the SOP's own first steps, word for word.
        for n in sorted(steps)[1:4]:
            st = steps[n]
            plan.insert(len([p for p in plan if p["source"] == "sop"]), {
                "text": st["text"], "kind": _kind(st["text"]), "source": "sop", "sop_step": n, "page": st.get("page"),
                "sop_text": st["text"], "warnings": _sop_warnings(st["text"]), "verbatim": True})
    # Evidence-backed checks first, the SOP and AI steps next (calls and
    # dispatches last), and the recovery check at the end.
    checks = evidence_checks(facts)
    first = [c for c in checks if not c.get("final")]
    final = [c for c in checks if c.get("final")]
    plan = first + [p for p in plan if p["kind"] != "CALL"] + [p for p in plan if p["kind"] == "CALL"] + final
    for i, item in enumerate(plan, 1):
        item["order"] = i
    if not sop.get("sop_id"):
        basis = "no_sop"
    else:
        basis = "ai_plan" if ai and any(not item.get("verbatim") and item["source"] == "sop" for item in plan) else "sop_steps"
    return {"basis": basis, "sop_id": sop.get("sop_id"), "sop_status": sop.get("status"),
            "sop_approved": bool(sop.get("approved")), "sop_document": sop.get("source_document"), "steps": plan,
            "ai_generated": ai}


def split_sections(text: str) -> Dict[str, List[str]]:
    """Section name -> bullet text, for the operator's short card."""
    result: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(">"):
            continue
        if _HEADING_RE.match(line):
            title = re.sub(r"^#+\s*(\d+[.)]\s*)?", "", line).replace("*", "").strip().rstrip(":")
            current = next((s for s in SECTIONS if s.lower() in title.lower()), title)
            result.setdefault(current, [])
        elif current:
            result[current].append(re.sub(r"^([-*•]|\d+[.)])\s*", "", line).replace("**", ""))
    return result


def operator_card(text: str) -> Dict[str, List[str]]:
    sections = split_sections(text)
    return {
        "what": sections.get(SECTIONS[0], [])[:3],
        "why": sections.get(SECTIONS[1], [])[:6],
        "causes": sections.get(SECTIONS[2], [])[:5],
        "checks": sections.get(SECTIONS[3], [])[:MAX_OPERATOR_CHECKS],
        "fix": sections.get(SECTIONS[4], [])[:5],
        "advisory": sections.get(SECTIONS[5], [])[:3],
        "missing": sections.get(SECTIONS[6], [])[:4],
        "limits": sections.get(SECTIONS[7], [])[:4],
    }


def _candidate_line(c: Dict[str, Any], note: str = "") -> str:
    support = "; ".join(c.get("supporting") or [])
    against = "; ".join(c.get("contradicting") or [])
    role = (_ROLE_NAMES.get(c.get("role")) + ". ") if c.get("role") in _ROLE_NAMES else ""
    return (f"- HYPOTHESIS: {role}{c['statement']} Supported by: {support or 'nothing recorded'}."
            + (f" Against: {against}." if against else "") + (f" ({note})" if note else ""))


def ensure_candidates(clean: str, facts: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Strong candidates (a recorded trip, or an SOP 'suspect' limit) must not
    vanish from the causes because the model skipped them or a line was removed.
    Added lines say they come from the record, not from the model."""
    from cause_candidates import mentioned
    lines = clean.splitlines()
    head = next((i for i, l in enumerate(lines) if _HEADING_RE.match(l) and SECTIONS[2].lower() in l.lower()), None)
    if head is None:
        return clean, []
    end = next((i for i in range(head + 1, len(lines)) if _HEADING_RE.match(lines[i])), len(lines))
    written = "\n".join(lines[head + 1:end])
    added = [c for c in facts.get("cause_candidates") or [] if c["kind"] in ("trip", "sop", "standby") and not mentioned(c, written)]
    if not added:
        return clean, []
    new = [_candidate_line(c, "Added by OpsLens from the record; the AI left it out") for c in added]
    lines[end:end] = new
    return "\n".join(lines), [c["statement"] for c in added]


# ------------------------------------------------------------------- fallback

def deterministic_diagnosis(facts: Dict[str, Any]) -> str:
    """Eight sections built only from recorded facts. Clearly not AI output."""
    pa = facts["primary_alarm"]
    sop = facts.get("sop")
    out = [f"> {FALLBACK_LABEL}", ""]
    out.append(f"## 1. {SECTIONS[0]}")
    state = "still active" if pa.get("input_active") else "returned to normal"
    out.append(f"- CONFIRMED: {pa['tag']} ({pa['description']}) on {facts.get('equipment')} first at {pa['first_at']}; "
               f"alarm input {state}. Data: {facts.get('data_source')}.")
    out.append(f"## 2. {SECTIONS[1]}")
    if not facts["readings"] and not facts["active_alarms"]:
        out.append("- UNKNOWN: no readings for this incident's tags are recorded.")
    for r in facts["readings"]:
        limit = f"; {r['limit']}" if r.get("limit") else ""
        out.append(f"- CONFIRMED: {r['name']} ({r['tag']}) reached {r['worst']} {r['unit']}, normal {r.get('normal') or 'not stated'}"
                   f" ({r['state_at_worst']}); now {r['now']} {r['unit']}{limit}.")
    if facts["active_alarms"]:
        out.append(f"- CONFIRMED: active alarm inputs: {', '.join(facts['active_alarms'])}.")
    out.append(f"## 3. {SECTIONS[2]}")
    candidates = facts.get("cause_candidates") or []
    for c in candidates:
        out.append(_candidate_line(c))
    if not candidates and facts.get("ranking"):
        rk = facts["ranking"]
        out.append(f"- HYPOTHESIS: {rk['tag']} is the strongest recorded signal (ranking score {rk['score']}; {rk['note']}).")
    if not candidates and not facts.get("ranking"):
        out.append("- UNKNOWN: the record does not point to a likely cause.")
    out.append(f"## 4. {SECTIONS[3]}")
    if sop:
        for s in sop["steps"][:5]:
            out.append(f"- SOP step {s['n']} (p.{s['page']}): {s['text']}")
    else:
        out.append(f"- {NO_SOP_SENTENCE}")
    out.append(f"## 5. {SECTIONS[4]}")
    for v in facts.get("verification") or []:
        out.append(f"- Recovery check from the SOP: {v}")
    if not facts.get("verification"):
        out.append("- UNKNOWN: the record gives no recovery criteria. Confirm readings are back in their normal ranges.")
    out.append(f"## 6. {SECTIONS[5]}")
    if sop:
        out.append("- Not in the SOP: no extra checks were generated, because no model interpreted this incident.")
    else:
        out.append("- GENERAL ADVICE: compare the field instruments with the HMI values and record any fault codes.")
        out.append("- GENERAL ADVICE: escalate to an authorized engineer before operating any equipment.")
    out.append(f"## 7. {SECTIONS[6]}")
    for m in facts["missing"] or ["UNKNOWN: field confirmation of the HMI values (local gauges, panel fault codes)."]:
        out.append(f"- {m}")
    if sop:
        for note in sop.get("notes") or []:
            out.append(f"- Not addressed by {sop['sop_id']}: {note}")
    out.append(f"## 8. {SECTIONS[7]}")
    out.append("- This text was assembled from recorded facts by fixed rules. No model interpreted them.")
    out.append("- Candidate order and the ranking score are not probabilities, and event order does not prove cause.")
    return "\n".join(out)


# --------------------------------------------------------------- LM Studio

def local_enabled() -> bool:
    from llm_provider import selected_provider
    return selected_provider() == "lmstudio"


def groq_enabled() -> bool:
    from llm_provider import selected_provider
    return selected_provider() == "groq"


def ai_enabled() -> bool:
    return local_enabled() or groq_enabled()


def groq_model() -> str:
    """Separate from EDGE_LLM_MODEL, which names the local LM Studio model."""
    return (os.environ.get("OPSLENS_GROQ_MODEL") or "openai/gpt-oss-120b").strip()


def groq_reasoning() -> str:
    """How long gpt-oss thinks before writing. Medium finds causes better than
    low; high is slower and uses more of the rate limit."""
    value = (os.environ.get("OPSLENS_GROQ_REASONING") or "medium").strip().lower()
    return value if value in ("low", "medium", "high") else "medium"


def _local_backup_allowed() -> bool:
    return (os.environ.get("OPSLENS_DIAG_LOCAL_BACKUP") or "true").strip().lower() not in ("false", "0", "no")


def _local_max_tokens() -> int:
    for name in ("OPSLENS_DIAG_MAX_TOKENS", "OPSLENS_DIAG_NUM_PREDICT"):
        try:
            return max(200, int(os.environ[name]))
        except (KeyError, ValueError):
            continue
    return 1500


def stream_local(prompt: str) -> Iterator[Tuple[str, Optional[Dict[str, Any]]]]:
    """Stream a diagnosis from LM Studio's Local Model API (OpenAI chat format).
    Yield (token, None) then ("", stats). Raises ProviderError on failure."""
    from edge_slm import first_token_timeout, local_base, local_connect_timeout, local_session, total_generation_timeout
    from llm_provider import ProviderError, llm_model
    payload = {"model": llm_model("lmstudio"), "stream": True, "temperature": 0.2, "top_p": 0.9,
               "max_tokens": _local_max_tokens(),
               "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]}
    deadline = time.monotonic() + total_generation_timeout()
    finish = None
    started = time.monotonic()
    first = None
    chunks = 0
    try:
        with local_session() as session, session.post(
                f"{local_base()}/v1/chat/completions", json=payload, stream=True,
                timeout=(local_connect_timeout(), first_token_timeout())) as resp:
            if resp.status_code >= 400:
                body = resp.text[:300]
                if "context" in body.lower() and ("length" in body.lower() or "overflow" in body.lower() or "exceed" in body.lower()):
                    raise ProviderError("context", f"The prompt is longer than the model's loaded context in LM Studio: {body}", resp.status_code)
                if resp.status_code == 404 or "not found" in body.lower() or "no models loaded" in body.lower():
                    raise ProviderError("model_missing", f"LM Studio has no model '{payload['model']}' ready (HTTP {resp.status_code}): {body}", resp.status_code)
                raise ProviderError("unavailable", f"LM Studio HTTP {resp.status_code}: {body}", resp.status_code)
            resp.encoding = "utf-8"  # event streams name no charset
            for line in resp.iter_lines(decode_unicode=True):
                if time.monotonic() > deadline:
                    raise ProviderError("timeout", f"Diagnosis exceeded the {total_generation_timeout():.0f} s limit")
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    body = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("LM Studio sent a malformed stream line; skipped")
                    continue
                if body.get("error"):
                    raise ProviderError("unavailable", f"LM Studio error: {body['error']}")
                choice = (body.get("choices") or [{}])[0]
                finish = choice.get("finish_reason") or finish
                token = (choice.get("delta") or {}).get("content") or ""
                if token:
                    first = first or time.monotonic()
                    chunks += 1
                    yield token, None
    except requests.exceptions.ConnectionError as exc:
        # requests reports a read timeout inside a stream as ConnectionError.
        if "timed out" in str(exc).lower():
            raise ProviderError("timeout", f"LM Studio sent no text within {first_token_timeout():.0f} s") from exc
        raise ProviderError("unreachable", f"LM Studio's Local Model API is not running at {local_base()}") from exc
    except requests.exceptions.Timeout as exc:
        raise ProviderError("timeout", f"LM Studio sent no text within {first_token_timeout():.0f} s") from exc
    writing = time.monotonic() - first if first else 0
    yield "", {"done_reason": finish, "eval_count": chunks,
               "eval_duration": writing * 1e9 if writing else None,
               "load_duration": ((first or time.monotonic()) - started) * 1e9}


def _groq_retry_wait() -> float:
    try:
        return max(0.0, float(os.environ.get("OPSLENS_GROQ_MAX_RETRY_WAIT_DIAG", "20")))
    except ValueError:
        return 20.0


def _groq_request(prompt: str, reasoning_options: bool, key: str, system: Optional[str] = None,
                  effort: Optional[str] = None) -> Iterator[Tuple[str, Optional[Dict[str, Any]]]]:
    from edge_slm import total_generation_timeout
    from llm_provider import ProviderError, _auth_failure
    endpoint = (os.environ.get("GROQ_API_ENDPOINT") or "https://api.groq.com/openai/v1/chat/completions").strip()
    payload: Dict[str, Any] = {
        "model": groq_model(), "stream": True, "temperature": 0.2,
        "max_completion_tokens": int(os.environ.get("OPSLENS_GROQ_MAX_TOKENS") or 4000),
        "messages": [{"role": "system", "content": system or SYSTEM_PROMPT}, {"role": "user", "content": prompt}]}
    if reasoning_options:
        payload.update({"reasoning_effort": effort or groq_reasoning(), "include_reasoning": False})
    deadline = time.monotonic() + total_generation_timeout()
    finish = None
    try:
        with requests.post(endpoint, json=payload, stream=True, timeout=(5, 45),
                           headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}) as resp:
            if resp.status_code == 429:
                error = ProviderError("rate_limit", "Groq limit reached (HTTP 429)", 429)
                error.retry_after = resp.headers.get("retry-after")
                raise error
            _auth_failure(resp.status_code, "Groq")
            resp.encoding = "utf-8"  # event streams name no charset, which would give bytes
            for line in resp.iter_lines(decode_unicode=True):
                if time.monotonic() > deadline:
                    raise ProviderError("timeout", f"Diagnosis exceeded the {total_generation_timeout():.0f} s limit")
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    choice = (json.loads(data).get("choices") or [{}])[0]
                except json.JSONDecodeError:
                    logger.warning("Groq sent a malformed stream line; skipped")
                    continue
                finish = choice.get("finish_reason") or finish
                token = (choice.get("delta") or {}).get("content") or ""
                if token:
                    yield token, None
    except requests.exceptions.Timeout as exc:
        raise ProviderError("timeout", "Groq did not answer in time") from exc
    except requests.exceptions.ConnectionError as exc:
        raise ProviderError("unreachable", "Groq could not be reached. Check the internet connection") from exc
    yield "", {"done_reason": finish}


def stream_groq(prompt: str, job: Optional["_Job"] = None, system: Optional[str] = None,
                effort: Optional[str] = None) -> Iterator[Tuple[str, Optional[Dict[str, Any]]]]:
    """Yield (token, None) then ("", stats).

    With several keys (GROQ_API_KEYS), a key that is rate-limited (429) or
    rejected (401/403) is set aside and the next one is tried. When every key
    is cooling down, one short wait is allowed. Nothing is retried once text
    has arrived. Keys are named by position only; their values are never logged."""
    from groq_keys import POOL
    from llm_provider import ProviderError
    waited = False
    last: Optional[Exception] = None
    while True:
        candidates = POOL.candidates() if (POOL.any_usable() or last is None) else []
        for index, key in candidates:
            reasoning_options = True
            while True:
                try:
                    for token, final in _groq_request(prompt, reasoning_options, key, system, effort):
                        if final is not None:
                            POOL.used(index)  # the next diagnosis starts with the next key
                        yield token, final
                    return
                except ProviderError as exc:
                    if exc.kind == "invalid_request" and reasoning_options:
                        reasoning_options = False  # a model that does not take these options
                        continue
                    last = exc
                    if exc.kind == "rate_limit":
                        POOL.limited(index, getattr(exc, "retry_after", None))
                        logger.warning("Groq key %d of %d rate-limited; trying the next key", index + 1, POOL.count())
                        break
                    if exc.kind == "auth":
                        POOL.rejected(index)
                        logger.warning("Groq key %d of %d was rejected; trying the next key", index + 1, POOL.count())
                        break
                    raise
        wait = POOL.shortest_wait()
        if waited or wait is None or not 0 <= wait <= _groq_retry_wait():
            if last is None:
                raise ProviderError("auth", "No usable Groq API key (all keys were rejected)")
            if POOL.count() > 1 and getattr(last, "kind", None) == "rate_limit":
                raise ProviderError("rate_limit", f"All {POOL.count()} Groq keys are rate-limited (HTTP 429)", 429)
            raise last
        waited = True
        if job is not None:
            job.publish({"type": "status", "stage": "queued"})
        time.sleep(wait)


def _repair_enabled() -> bool:
    return (os.environ.get("OPSLENS_DIAG_REPAIR") or "true").strip().lower() not in ("false", "0", "no")


def _groq_complete(system: str, user: str, max_tokens: int) -> str:
    """One short non-streamed answer, using the same key rotation."""
    from groq_keys import POOL
    from llm_provider import ProviderError, _auth_failure
    endpoint = (os.environ.get("GROQ_API_ENDPOINT") or "https://api.groq.com/openai/v1/chat/completions").strip()
    payload = {"model": groq_model(), "temperature": 0.1, "max_completion_tokens": max_tokens,
               "reasoning_effort": "low", "include_reasoning": False,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    for index, key in POOL.candidates():
        headers = {"Authorization": f"Bearer {key}"}
        resp = requests.post(endpoint, json=payload, timeout=(5, 40), headers=headers)
        if resp.status_code == 400:
            payload.pop("reasoning_effort", None)
            payload.pop("include_reasoning", None)
            resp = requests.post(endpoint, json=payload, timeout=(5, 40), headers=headers)
        if resp.status_code == 429:
            POOL.limited(index, resp.headers.get("retry-after"))
            continue
        if resp.status_code in (401, 403):
            POOL.rejected(index)
            continue
        if resp.status_code >= 400:
            _auth_failure(resp.status_code, "Groq")
            raise ProviderError("unavailable", f"Groq HTTP {resp.status_code}")
        POOL.used(index)
        return ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    raise ProviderError("rate_limit", "No Groq key is available for the repair call")


REPAIR_PROMPT = (
    "You correct lines of an incident diagnosis. Each line below was removed because it did not match the "
    "incident record. Rewrite each one so it uses only tags, numbers, times and SOP step numbers that appear "
    "exactly in FACTS, keeping its meaning and its CONFIRMED / HYPOTHESIS / UNKNOWN / 'Not in the SOP:' label. "
    "Do not calculate new numbers. If a line cannot be fixed, write DROP. "
    "Reply with exactly one line per input line, in the same order, each starting with its number like '3) '."
)


def repair_removed(check: Dict[str, Any], facts: Dict[str, Any]) -> Tuple[str, int]:
    """Ask the model once to fix the lines the record check removed. Every
    fixed line is checked again; only lines that now pass are put back, in
    their own section. Returns the new text and how many lines came back."""
    removed = check.get("removed_lines") or []
    if not removed:
        return check["clean_text"], 0
    numbered = "\n".join(f"{i}) [{r.get('section') or 'unknown section'}] {r['line']}  -- removed: {'; '.join(r['problems'])}"
                         for i, r in enumerate(removed, 1))
    reply = _groq_complete(REPAIR_PROMPT, build_prompt(facts) + "\n\nREMOVED LINES:\n" + numbered, 900)
    line_ok = line_checker(facts)
    fixed: Dict[str, List[str]] = {}
    for raw in reply.splitlines():
        match = re.match(r"^\s*(\d{1,2})\)\s*(.+)$", raw)
        if not match or not 1 <= int(match.group(1)) <= len(removed):
            continue
        line = re.sub(r"^\[[^\]]*\]\s*", "", match.group(2)).strip()
        if not line or line.upper().startswith("DROP"):
            continue
        line = line if line.startswith(("-", "*")) else "- " + line
        if line_ok(line):
            continue
        section = removed[int(match.group(1)) - 1].get("section")
        if section:
            fixed.setdefault(section, []).append(line)
    if not fixed:
        return check["clean_text"], 0
    lines = check["clean_text"].splitlines()
    for section, new in fixed.items():
        head = next((i for i, l in enumerate(lines) if _HEADING_RE.match(l) and section.lower() in l.lower()), None)
        if head is None:
            continue
        end = next((i for i in range(head + 1, len(lines)) if _HEADING_RE.match(lines[i])), len(lines))
        lines[end:end] = new
    return "\n".join(lines), sum(len(v) for v in fixed.values())


DIAGNOSIS_LATENCIES: List[Dict[str, Any]] = []  # last 200 AI answers, for /metrics/impact


LAST_CALL: Dict[str, Any] = {"attempted": False, "succeeded": None, "at": None, "error": None,
                             "time_to_first_token_ms": None, "total_ms": None, "model": None}


def _record_call(succeeded: bool, error: Optional[str] = None, latency: Optional[Dict[str, Any]] = None) -> None:
    LAST_CALL.update({"attempted": True, "succeeded": succeeded, "error": error, "model": _model_name(),
                      "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                      "time_to_first_token_ms": (latency or {}).get("time_to_first_token_ms"),
                      "total_ms": (latency or {}).get("total_ms")})


def _mark_verified() -> None:
    import llm_provider
    llm_provider._verified_provider = ("lmstudio", llm_provider.llm_model("lmstudio"))


# ---------------------------------------------------------------------- cache

class _Cache:
    def __init__(self, size: int = 64):
        self._d: "OrderedDict[str, Tuple[Dict[str, Any], Optional[float]]]" = OrderedDict()
        self._size = size
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            hit = self._d.get(key)
            if hit is None:
                return None
            value, expires = hit
            if expires is not None and time.monotonic() > expires:
                del self._d[key]
                return None
            self._d.move_to_end(key)
            return value

    def put(self, key: str, value: Dict[str, Any], ttl: Optional[float] = None) -> None:
        with self._lock:
            self._d[key] = (value, time.monotonic() + ttl if ttl else None)
            self._d.move_to_end(key)
            while len(self._d) > self._size:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


CACHE = _Cache()
REJECTED_TTL_SECONDS = 300.0


def _model_name() -> str:
    from llm_provider import llm_model
    return groq_model() if groq_enabled() else llm_model("lmstudio") if local_enabled() else "none"


def _cache_key(facts: Dict[str, Any]) -> str:
    return f"{facts['event_id']}:{facts_hash(facts)}:{_model_name()}"


# ----------------------------------------------------------------------- jobs

class _Job:
    """One generation. Any number of readers follow it; it outlives them."""

    def __init__(self, key: str):
        self.key = key
        self.events: List[Dict[str, Any]] = []
        self.done = False
        self._cond = threading.Condition()

    def publish(self, event: Dict[str, Any]) -> None:
        with self._cond:
            self.events.append(event)
            self._cond.notify_all()

    def finish(self) -> None:
        with self._cond:
            self.done = True
            self._cond.notify_all()

    def read(self, cursor: int, wait: float) -> Tuple[List[Dict[str, Any]], bool]:
        with self._cond:
            if cursor >= len(self.events) and not self.done:
                self._cond.wait(timeout=wait)
            return self.events[cursor:], self.done


_JOBS: Dict[str, _Job] = {}
_LATEST_JOB: Dict[str, str] = {}  # event_id -> key of its newest job
_JOBS_LOCK = threading.Lock()
# One generation at a time: a 3B model on a CPU has no spare capacity, and
# parallel requests only make every one of them time out.
_MODEL_SLOT = threading.Semaphore(1)
KEEPALIVE_SECONDS = 2.0


def _superseded(job: _Job, facts: Dict[str, Any]) -> bool:
    """A growing incident changes its facts. Only the newest context is worth
    model time; an older job in the queue would otherwise hold up the newer one."""
    return _LATEST_JOB.get(facts["event_id"], job.key) != job.key


def _failure_result(facts: Dict[str, Any], kind: str, reason: str, started: float,
                    rejected_text: Optional[str] = None, latency: Optional[Dict[str, Any]] = None,
                    validation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if kind in ("provider_off", "rejected"):
        status = "deterministic_fallback"
    else:
        status = "sop_retrieval_only" if facts.get("sop") else "ai_unavailable"
    text = deterministic_diagnosis(facts)
    result = {"engine": "deterministic_fallback", "source_status": status, "source_label": SOURCE_LABELS[status],
              "label": FALLBACK_LABEL, "fallback_reason": reason, "failure_kind": kind,
              "fix_hint": FIX_HINTS.get(kind), "text": text, "operator_card": operator_card(text),
              "validation": validation, "latency": latency or {"total_ms": round((perf_counter() - started) * 1000, 1)},
              "facts": facts, "rejected_ai_text": rejected_text}
    add_sop_checks(result, facts)
    return result


def _consume(job: _Job, facts: Dict[str, Any], stream, parts: List[str], timing: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Read one model stream into parts. Returns its stats, or None if superseded."""
    for token, final in stream:
        if final is not None:
            return final
        if timing.get("first") is None:
            timing["first"] = perf_counter()
            job.publish({"type": "status", "stage": "generating"})
        parts.append(token)
        job.publish({"type": "token", "text": token})
        if _superseded(job, facts):
            # Leaving the stream closes the request, which stops the model.
            return None
    return {}


def _missing_sections(text: str) -> List[str]:
    lower = normalize_model_text(text).lower()
    return [name for name in SECTIONS if name.lower() not in lower]


def _has_core_sections(text: str) -> bool:
    """Enough of a stopped answer to be useful: through the SOP checks."""
    return bool(re.search(r"^#+\s*5\.", text, re.M))  # section 4, the SOP checks, is complete


def _generate(job: _Job, facts: Dict[str, Any], store: Optional[Callable[[str, str, Dict[str, Any]], None]]) -> Dict[str, Any]:
    """Run the model for one job. Publishes progress and returns the result."""
    from edge_slm import local_health
    from llm_provider import ProviderError, llm_model
    started = perf_counter()
    if not ai_enabled():
        return _failure_result(facts, "provider_off", "No LLM provider selected (OPSLENS_LLM_PROVIDER is not 'groq' or 'lmstudio').", started)

    engine = "groq" if groq_enabled() else "lmstudio"
    local_warning = None
    job.publish({"type": "status", "stage": "checking_model"})
    prompt = build_prompt(facts)
    parts: List[str] = []
    stats: Dict[str, Any] = {}
    timing: Dict[str, Any] = {"first": None}
    partial = False
    superseded = "The incident changed; a newer diagnosis replaced this one."
    groq_note, groq_kind = None, None

    if engine == "groq":
        from groq_keys import POOL
        if not POOL.count():
            reason = "GROQ_API_KEY (or GROQ_API_KEYS) is not set."
            _record_call(False, error=reason)
            return _failure_result(facts, "auth", reason, started)
        try:
            with _MODEL_SLOT:
                if _superseded(job, facts):
                    return _failure_result(facts, "superseded", superseded, started)
                job.publish({"type": "status", "stage": "generating"})
                stats = _consume(job, facts, stream_groq(prompt, job), parts, timing)
            if stats is None:
                return _failure_result(facts, "superseded", superseded, started)
        except ProviderError as exc:
            if parts and _has_core_sections("".join(parts)):
                partial, stats = True, {"done_reason": "length"}
                logger.warning("ai_diagnosis event=%s kept partial Groq answer: %s", facts["event_id"], exc)
            elif exc.kind in ("unreachable", "timeout", "unavailable", "rate_limit", "auth") and _local_backup_allowed():
                # Order: Groq, then the local model in LM Studio, then the answer built
                # from the record. Used when Groq is offline, slow, out of quota or
                # its key is rejected.
                logger.warning("ai_diagnosis event=%s Groq failed, trying local model: %s", facts["event_id"], exc)
                groq_note, groq_kind, engine, parts, timing = str(exc), exc.kind, "lmstudio", [], {"first": None}
            else:
                reason = f"Groq unavailable: {exc}"
                _record_call(False, error=reason)
                logger.warning("ai_diagnosis event=%s failed kind=%s error=%s", facts["event_id"], exc.kind, exc)
                return _failure_result(facts, exc.kind if exc.kind in FIX_HINTS else "unavailable", reason, started)

    if engine == "groq" and parts and not partial and stats.get("done_reason") == "length" \
            and _missing_sections("".join(parts)) and groq_reasoning() != "low":
        # gpt-oss counts its thinking against the output limit; a long think can
        # leave the answer cut off after a few sections. Ask once more with less
        # thinking, and keep whichever answer is more complete.
        missing = _missing_sections("".join(parts))
        logger.warning("ai_diagnosis event=%s Groq answer hit the output limit (missing %d sections); retrying with low reasoning",
                       facts["event_id"], len(missing))
        job.publish({"type": "status", "stage": "retrying"})
        retry_parts: List[str] = []
        retry_timing: Dict[str, Any] = {"first": None}
        try:
            with _MODEL_SLOT:
                retry_stats = _consume(job, facts, stream_groq(prompt, job, effort="low"), retry_parts, retry_timing)
            if retry_stats is None:
                return _failure_result(facts, "superseded", superseded, started)
            if len(_missing_sections("".join(retry_parts))) < len(missing):
                parts, stats = retry_parts, retry_stats
        except ProviderError as exc:
            logger.warning("ai_diagnosis event=%s retry failed, keeping the first answer: %s", facts["event_id"], exc)

    if engine == "lmstudio":
        health = local_health()
        local_warning = health.get("warning")
        if health.get("error_kind"):
            reason = f"LM Studio unavailable: {health['error']}"
            kind = health["error_kind"]
            if groq_note:
                # Both failed: Groq's problem is the one to fix first.
                reason = f"Groq unavailable: {groq_note}. Backup: {reason}"
                kind = groq_kind if groq_kind in FIX_HINTS else kind
            _record_call(False, error=reason)
            logger.warning("ai_diagnosis event=%s preflight_failed kind=%s error=%s", facts["event_id"], health["error_kind"], health["error"])
            return _failure_result(facts, kind, reason, started)

        job.publish({"type": "status", "stage": "queued"})
        with _MODEL_SLOT:
            if _superseded(job, facts):
                return _failure_result(facts, "superseded", superseded, started)
            job.publish({"type": "status", "stage": "generating" if health.get("model_loaded") else "loading_model"})
            try:
                stats = _consume(job, facts, stream_local(prompt), parts, timing)
                if stats is None:
                    return _failure_result(facts, "superseded", superseded, started)
            except ProviderError as exc:
                if parts and _has_core_sections("".join(parts)):
                    partial, stats = True, {"done_reason": "length"}
                    logger.warning("ai_diagnosis event=%s kept partial answer: %s", facts["event_id"], exc)
                else:
                    reason = f"LM Studio unavailable: {exc}"
                    if parts:
                        reason += " (stream interrupted; partial text discarded)"
                    kind = exc.kind if exc.kind in FIX_HINTS else "unavailable"
                    if groq_note:
                        reason = f"Groq unavailable: {groq_note}. Backup: {reason}"
                        kind = groq_kind if groq_kind in FIX_HINTS else kind
                    _record_call(False, error=reason)
                    logger.warning("ai_diagnosis event=%s failed kind=%s error=%s", facts["event_id"], exc.kind, exc)
                    return _failure_result(facts, kind, reason, started)

    first_token_at = timing["first"]
    text = "".join(parts).strip()
    total_ms = round((perf_counter() - started) * 1000, 1)
    if not text:
        reason = "The model returned an empty response."
        _record_call(False, error=reason)
        logger.warning("ai_diagnosis event=%s empty response stats=%s", facts["event_id"], stats)
        return _failure_result(facts, "empty", reason, started)

    if engine == "lmstudio":
        _mark_verified()
    latency = {"time_to_first_token_ms": round((first_token_at - started) * 1000, 1) if first_token_at else None,
               "total_ms": total_ms,
               "load_ms": round((stats.get("load_duration") or 0) / 1e6, 1),
               "prompt_tokens": stats.get("prompt_eval_count"),
               "output_tokens": stats.get("eval_count"),
               "tokens_per_second": (round(stats["eval_count"] / (stats["eval_duration"] / 1e9), 1)
                                     if stats.get("eval_count") and stats.get("eval_duration") else None)}
    DIAGNOSIS_LATENCIES.append({"engine": engine, "ttft_ms": latency["time_to_first_token_ms"], "total_ms": total_ms})
    del DIAGNOSIS_LATENCIES[:-200]
    _record_call(True, latency=latency)
    check = validate(text, facts)
    if stats.get("done_reason") == "length":
        check["warnings"].append("the model reached its output limit; the answer may end early")
    logger.info("ai_diagnosis event=%s engine=%s valid=%s removed=%d ttft_ms=%s total_ms=%s tokens_per_s=%s",
                facts["event_id"], engine, check["valid"], len(check["removed_lines"]),
                latency["time_to_first_token_ms"], total_ms, latency["tokens_per_second"])
    for r in check["removed_lines"]:
        logger.info("ai_diagnosis event=%s removed: %r [%s]", facts["event_id"], r["line"][:200], "; ".join(r["problems"]))
    if not check["valid"]:
        reason = "Model output rejected by the validator: " + "; ".join(check["rejected_reasons"])
        # Say which model wrote it, and why Groq was not used, so the fix is clear.
        try:
            from llm_provider import llm_model
            who = f"Groq ({groq_model()})" if engine == "groq" else f"LM Studio ({llm_model('lmstudio')})"
        except Exception:  # naming the model must never hide the rejection itself
            who = "Groq" if engine == "groq" else "LM Studio"
        reason += f". Written by {who}."
        if groq_note:
            reason += f" Groq was tried first and failed: {groq_note}"
        if stats.get("done_reason") == "length":
            reason += " The answer hit the output limit and ended early."
        logger.warning("ai_diagnosis event=%s rejected: %s", facts["event_id"], reason)
        return _failure_result(facts, "rejected", reason, started, rejected_text=text, latency=latency, validation=check)

    clean = check["clean_text"]
    repaired = 0
    if engine == "groq" and check["removed_lines"] and _repair_enabled():
        try:
            clean, repaired = repair_removed(check, facts)
        except Exception as exc:
            logger.warning("ai_diagnosis event=%s repair skipped: %s: %s", facts["event_id"], type(exc).__name__, exc)
    if repaired:
        removed = check["removed_lines"]
        check = validate(clean, facts)
        check["removed_lines"] = removed
        check["repaired_lines"] = repaired
        check["warnings"].append(f"{repaired} of {len(removed)} removed line(s) were corrected by the model and checked again")
    clean, added = ensure_candidates(clean, facts)
    if added:
        check["added_causes"] = added
        check["warnings"].append(f"{len(added)} possible cause(s) from the record were added because the model left them out")
    check["clean_text"] = clean
    label = f"AI diagnosis — {groq_model()} via Groq (cloud)" if engine == "groq" else f"AI diagnosis — {llm_model('lmstudio')} via LM Studio (local)"
    if local_warning:
        check["warnings"].append(local_warning)
    if groq_note:
        check["warnings"].append(f"Groq was unavailable, so the local model answered: {groq_note}")
    result = {"engine": engine, "source_status": "ai_generated", "source_label": SOURCE_LABELS["ai_generated"],
              "label": label, "fallback_reason": None,
              "failure_kind": None, "fix_hint": None, "text": clean, "operator_card": operator_card(clean),
              "validation": check, "latency": latency, "facts": facts, "rejected_ai_text": None}
    add_sop_checks(result, facts)
    if partial:
        # Keep it briefly so views do not restart the model. Retry bypasses this.
        check["warnings"].append("the model stopped before the end; the later sections may be missing")
        CACHE.put(job.key, result, ttl=REJECTED_TTL_SECONDS)
        return result
    CACHE.put(job.key, result)
    if store is not None:
        try:
            store(facts["event_id"], job.key.split(":")[1], result)
        except Exception as exc:
            logger.warning("AI diagnosis could not be stored: %s: %s", type(exc).__name__, exc)
    return result


def _run_job(job: _Job, facts: Dict[str, Any], store) -> None:
    try:
        result = _generate(job, facts, store)
        if result.get("failure_kind") == "rejected":
            # Do not re-ask for the same text on every page view; Retry bypasses this.
            CACHE.put(job.key, result, ttl=REJECTED_TTL_SECONDS)
        job.publish({"type": "done", **result, "cached": False})
    except Exception as exc:
        logger.exception("Diagnosis job failed for %s", facts.get("event_id"))
        reason = f"Diagnosis failed: {type(exc).__name__}: {exc}"
        job.publish({"type": "done", **_failure_result(facts, "unavailable", reason, perf_counter()), "cached": False})
    finally:
        job.finish()
        with _JOBS_LOCK:
            if _JOBS.get(job.key) is job:
                del _JOBS[job.key]


def _get_job(key: str, facts: Dict[str, Any], store) -> Tuple[_Job, bool]:
    with _JOBS_LOCK:
        job = _JOBS.get(key)
        if job is not None:
            return job, True
        job = _Job(key)
        _JOBS[key] = job
        _LATEST_JOB[facts["event_id"]] = key
    threading.Thread(target=_run_job, args=(job, facts, store), daemon=True,
                     name=f"opslens-diagnosis-{str(facts.get('event_id'))[:8]}").start()
    return job, False


# ------------------------------------------------------------------ main API

def stream_diagnosis(full_event: Dict[str, Any], db=None) -> Iterator[Dict[str, Any]]:
    yield from stream_diagnosis_from_facts(build_facts(full_event, db), db=db)


def _stored_result(key: str, facts: Dict[str, Any], db) -> Optional[Dict[str, Any]]:
    cached = CACHE.get(key)
    if cached is not None or db is None or not ai_enabled():
        return cached
    try:
        stored = db.get_ai_diagnosis(facts["event_id"], key.split(":")[1], key.split(":", 2)[2])
    except Exception as exc:
        logger.warning("Stored AI diagnosis unavailable: %s: %s", type(exc).__name__, exc)
        return None
    if stored is not None:
        stored["facts"] = facts
        CACHE.put(key, stored)
    return stored


def stream_diagnosis_from_facts(facts: Dict[str, Any], db=None, force: bool = False,
                                stream_keepalive: bool = False) -> Iterator[Dict[str, Any]]:
    """Yield events: meta, status*, token*, done. 'done' carries the final result.

    Tokens are an unvalidated draft shown while the model writes; the 'done'
    event carries the validated result. source_status is 'ai_generated' only
    when the model really produced the text and it passed validation.
    """
    started = perf_counter()
    prompt = build_prompt(facts)
    key = _cache_key(facts)
    from llm_provider import llm_model
    meta = {"type": "meta", "event_id": facts["event_id"], "incident": facts["incident"],
            "data_source": facts["data_source"], "model": _model_name() if ai_enabled() else None,
            "prompt_chars": len(SYSTEM_PROMPT) + len(prompt), "facts_hash": key.split(":")[1],
            "context_version": key.split(":")[1]}
    cached = None if force else _stored_result(key, facts, db)
    if cached is not None:
        yield {**meta, "cached": True}
        yield {"type": "done", **cached, "cached": True,
               "latency": {**(cached.get("latency") or {}), "served_from_cache_ms": round((perf_counter() - started) * 1000, 1)}}
        return
    store = None
    if db is not None:
        model = _model_name()
        db_path = getattr(db, "db_path", None)

        def store(event_id: str, context_hash: str, result: Dict[str, Any]) -> None:
            # Runs on the job's thread; Database gives each thread its own connection.
            from database import Database
            target = db if db_path is None else Database(db_path=db_path)
            target.save_ai_diagnosis(event_id, context_hash, model, {k: v for k, v in result.items() if k != "facts"})
    job, attached = _get_job(key, facts, store)
    yield {**meta, "cached": False, "attached_to_running_job": attached}
    cursor = 0
    while True:
        events, finished = job.read(cursor, KEEPALIVE_SECONDS)
        cursor += len(events)
        for event in events:
            yield event
        if finished and not events:
            return
        if stream_keepalive and not events and not finished:
            yield {"type": "keepalive"}


def diagnose(full_event: Dict[str, Any], db=None) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for item in stream_diagnosis(full_event, db):
        if item["type"] == "done":
            result = item
    return result
