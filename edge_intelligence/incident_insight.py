"""Bottom line, confidence rating and history for one incident, built from the
record without a model call, so they show at once while the AI is working.

- history:    earlier incidents of the same alarm (or equipment): when, how long
              to resolve, and what the operator wrote when resolving.
- confidence: High / Medium / Low with the reasons and flags such as
              "unprecedented" or "several possible causes". A rule-based rating
              of the evidence, NOT a probability.
- bluf:       Bottom Line Up Front: likely cause, the top two SOP steps for it,
              and the most relevant earlier incident.
- pre_alarm:  operator actions and other alarms on the same equipment in the
              30 minutes before this alarm (time-window correlation).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

PRE_ALARM_MINUTES = 30


def _time(ts: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _hm(ts: Optional[str]) -> str:
    t = _time(ts)
    return t.strftime("%Y-%m-%d %H:%M UTC") if t else "time not recorded"


def _equipment(full_event: Dict[str, Any], db) -> Optional[str]:
    if full_event.get("equipment_id"):
        return full_event["equipment_id"]
    row = db._get_connection().execute("SELECT equipment_id FROM correlated_events WHERE event_id = ?",
                                       (full_event.get("event_id"),)).fetchone()
    return row["equipment_id"] if row else (full_event.get("candidate") or {}).get("equipment_id")


def history_reference(full_event: Dict[str, Any], db, limit: int = 3) -> Dict[str, Any]:
    """Earlier incidents with the same primary alarm, then the same equipment."""
    event_id = full_event.get("event_id")
    tag = full_event.get("primary_tag")
    equipment = _equipment(full_event, db)
    trigger = _time(full_event.get("trigger_timestamp"))
    conn = db._get_connection()
    rows = conn.execute(
        """SELECT event_id, primary_tag, equipment_id, trigger_timestamp FROM correlated_events
           WHERE event_id != ? AND (primary_tag = ? OR equipment_id = ?)""",
        (event_id, tag, equipment),
    ).fetchall()
    seeded = set()
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='seeded_history'").fetchone():
        seeded = {r[0] for r in conn.execute("SELECT event_id FROM seeded_history")}
    earlier = []
    for row in rows:
        t = _time(row["trigger_timestamp"])
        if t is None or (trigger is not None and t >= trigger):
            continue
        actions = conn.execute(
            "SELECT action_type, operator, timestamp, notes FROM operator_actions WHERE event_id = ? ORDER BY timestamp",
            (row["event_id"],),
        ).fetchall()
        resolve = next((a for a in reversed(actions) if a["action_type"] == "RESOLVE"), None)
        inspections = [a["notes"] for a in actions if a["action_type"] in ("INSPECT", "ANNOTATE") and a["notes"]]
        minutes = None
        if resolve and _time(resolve["timestamp"]):
            minutes = round((_time(resolve["timestamp"]) - t).total_seconds() / 60, 1)
        earlier.append({
            "event_id": row["event_id"], "same_alarm": row["primary_tag"] == tag, "alarm": row["primary_tag"],
            "equipment": row["equipment_id"], "when": row["trigger_timestamp"], "when_text": _hm(row["trigger_timestamp"]),
            "resolved": resolve is not None, "minutes_to_resolve": minutes,
            "resolution_notes": resolve["notes"] if resolve else None, "inspection_notes": inspections[-2:],
            "seeded_history": row["event_id"] in seeded,
        })
    earlier.sort(key=lambda e: (not e["same_alarm"], -(_time(e["when"]).timestamp())))
    same = [e for e in earlier if e["same_alarm"]]
    day = [e for e in same if trigger is not None and trigger - _time(e["when"]) <= timedelta(hours=24)]
    month = [e for e in same if trigger is not None and trigger - _time(e["when"]) <= timedelta(days=30)]
    timed = [e["minutes_to_resolve"] for e in same if e["minutes_to_resolve"] is not None]
    return {
        "same_alarm_total": len(same), "same_alarm_last_24h": len(day), "same_alarm_last_30d": len(month),
        "same_alarm_avg_minutes_to_resolve": round(sum(timed) / len(timed), 1) if timed else None,
        "seeded_history_count": sum(1 for e in earlier if e["seeded_history"]),
        "same_equipment_total": len(earlier), "earlier": earlier[:limit],
        "note": "From this OpsLens database only. Simulated runs, and seeded simulated history, count as incidents.",
    }


def pre_alarm_activity(full_event: Dict[str, Any], db, minutes: int = PRE_ALARM_MINUTES) -> List[Dict[str, Any]]:
    """Operator actions and other alarms on the same equipment shortly before
    this alarm. Timing alone does not prove they caused it."""
    trigger = _time(full_event.get("trigger_timestamp"))
    equipment = _equipment(full_event, db)
    if trigger is None:
        return []
    start = (trigger - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    end = trigger.strftime("%Y-%m-%dT%H:%M:%S.999999")
    conn = db._get_connection()
    found: List[Dict[str, Any]] = []
    for a in conn.execute(
        """SELECT oa.action_type, oa.operator, oa.timestamp, oa.notes, ce.equipment_id
           FROM operator_actions oa LEFT JOIN correlated_events ce ON ce.event_id = oa.event_id
           WHERE oa.timestamp >= ? AND oa.timestamp <= ? AND oa.event_id != ?""",
        (start, end, full_event.get("event_id")),
    ).fetchall():
        if a["equipment_id"] == equipment or (equipment and equipment.lower() in str(a["notes"] or "").lower()):
            found.append({"kind": "operator_action", "timestamp": a["timestamp"], "equipment": a["equipment_id"],
                          "text": f"{a['action_type'].replace('_', ' ').lower()} by {a['operator']}"
                                  + (f": {a['notes']}" if a["notes"] else "")})
    for a in conn.execute(
        """SELECT tag, equipment_id, first_seen_timestamp, incident_id FROM individual_alarms
           WHERE equipment_id = ? AND first_seen_timestamp >= ? AND first_seen_timestamp <= ? AND incident_id != ?""",
        (equipment, start, end, full_event.get("event_id")),
    ).fetchall():
        found.append({"kind": "earlier_alarm", "timestamp": a["first_seen_timestamp"], "equipment": a["equipment_id"],
                      "text": f"alarm {a['tag']} on the same equipment (another incident)"})
    found.sort(key=lambda f: _time(f["timestamp"]) or trigger)
    for f in found:
        t = _time(f["timestamp"])
        f["minutes_before_alarm"] = round((trigger - t).total_seconds() / 60, 1) if t else None
    return found


def _history_check_text(history: Dict[str, Any]) -> str:
    """Says what the history point rests on, including simulated history."""
    total = history.get("same_alarm_total") or 0
    if not total:
        return "The same alarm has happened before (no earlier incident recorded)"
    seeded = history.get("seeded_history_count") or 0
    note = f", {seeded} of them simulated history" if seeded else ""
    return f"The same alarm has happened before ({total} earlier{note})"


def confidence(facts: Dict[str, Any], history: Dict[str, Any]) -> Dict[str, Any]:
    # Possible consequences are effects, not competing causes; a candidate with
    # insufficient evidence (contradicted by related readings) is not a cause either.
    candidates = [c for c in facts.get("cause_candidates") or [] if c.get("role") not in ("effect", "insufficient")]
    strong = [c for c in candidates if c["kind"] in ("trip", "sop", "standby")]
    sop = facts.get("sop") or {}
    data_issue = str(facts.get("data_source") or "").upper() in ("STALE", "INVALID", "NO DATA") or any(
        "STALE" in str(r.get("state_now")) or "INVALID" in str(r.get("state_now")) for r in facts.get("readings") or [])
    reasons: List[str] = []
    flags: List[Dict[str, str]] = []
    if strong:
        reasons.append(f"{len(strong)} possible cause(s) backed by a recorded trip, an SOP 'suspect' limit or a standby status")
    elif candidates:
        reasons.append("possible causes rest only on readings that changed early, with no recorded trip or SOP 'suspect' limit")
    else:
        reasons.append("the record does not point to a cause")
    if sop.get("sop_id"):
        reasons.append(f"{sop['sop_id']} coverage: {sop.get('coverage')}")
    else:
        reasons.append("no matching SOP was found")

    # Evidence score out of 100: each check below earns its points when the
    # record shows it. The level comes from the score, so they always agree.
    coverage = sop.get("coverage")
    checks = [
        {"check": "Cause backed by a recorded trip, an SOP 'suspect' limit or a standby status",
         "points": 30, "earned": 30 if strong else 10 if candidates else 0},
        {"check": "One leading cause stands out", "points": 10, "earned": 10 if strong or len(candidates) == 1 else 0},
        {"check": "A matching SOP was found", "points": 10, "earned": 10 if sop.get("sop_id") else 0},
        {"check": "The SOP covers this alarm in full", "points": 15,
         "earned": 15 if sop.get("sop_id") and coverage == "full" else 8 if sop.get("sop_id") and coverage == "partial" else 0},
        {"check": ("The SOP is approved" if not sop.get("sop_id") or sop.get("approved")
                   else f"The SOP is approved ({sop['sop_id']} is a draft, not approved)"),
         "points": 10, "earned": 10 if sop.get("sop_id") and sop.get("approved") else 0},
        {"check": "Readings are fresh and valid", "points": 15, "earned": 0 if data_issue else 15},
        {"check": _history_check_text(history), "points": 10, "earned": 10 if history.get("same_alarm_total") else 0},
    ]
    score = sum(c["earned"] for c in checks)
    level = "High" if score >= 75 else "Medium" if score >= 45 else "Low"
    if data_issue or not candidates:
        level = "Low"  # stale data, or nothing in the record points to a cause

    if not history.get("same_alarm_total"):
        flags.append({"flag": "unprecedented", "text": "No earlier incident of this alarm is recorded. There is no history to compare with."})
    if len(strong) >= 2:
        flags.append({"flag": "several_causes", "text": "Several causes are backed by the record and may act together; check each one."})
    if not strong and len(candidates) >= 2:
        flags.append({"flag": "ambiguous", "text": "No single cause stands out. Treat every possible cause as unconfirmed until it is checked in the field."})
    if sop.get("sop_id") and not sop.get("approved"):
        flags.append({"flag": "draft_sop", "text": f"{sop['sop_id']} is DRAFT — NOT APPROVED."})
    if data_issue:
        flags.append({"flag": "data_quality", "text": "Some data is stale or invalid; confirm values in the field."})
    if level == "Low":
        flags.append({"flag": "escalate", "text": "Low confidence: escalate to an authorized engineer before acting."})
    return {"level": level, "score": score, "checks": checks, "reasons": reasons, "flags": flags,
            "note": "An evidence score: points for each check the record meets. It is not a probability."}


_WORDS = re.compile(r"[a-z]{4,}")
_STOP = {"that", "this", "with", "from", "were", "have", "when", "before", "after", "check", "record", "recorded",
         "says", "suspect", "these", "readings", "outside", "normal", "cause", "contributing", "condition", "trip"}


def _step_score(step_text: str, candidate: Dict[str, Any]) -> int:
    text = step_text.lower()
    words = {w for w in _WORDS.findall(candidate["statement"].lower()) if w not in _STOP}
    if candidate["kind"] == "trip":
        words |= {"reset", "cool", "cause", "inspect", "overload"}
    if candidate["kind"] == "standby":
        words |= {"standby", "selector", "start"}
    score = sum(1 for w in words if w in text)
    if candidate.get("equipment") and candidate["equipment"].lower() in text:
        score += 2
    return score


def bluf(facts: Dict[str, Any], history: Dict[str, Any]) -> Dict[str, Any]:
    candidates = facts.get("cause_candidates") or []
    top = candidates[0] if candidates else None
    steps = (facts.get("sop") or {}).get("steps") or []
    chosen: List[Dict[str, Any]] = []
    if steps:
        if top:
            ranked = sorted(steps, key=lambda s: (-_step_score(s["text"], top), s["n"]))
            chosen = [s for s in ranked if _step_score(s["text"], top) > 0][:2]
        for s in steps:  # fill with the first steps, the immediate response
            if len(chosen) >= 2:
                break
            if s not in chosen:
                chosen.append(s)
        chosen.sort(key=lambda s: s["n"])
    earlier = history.get("earlier") or []
    ref = next((e for e in earlier if e["same_alarm"]), earlier[0] if earlier else None)
    if ref:
        text = f"{'Same alarm' if ref['same_alarm'] else 'Same equipment'} on {ref['when_text']}"
        text += (f", resolved in {ref['minutes_to_resolve']} min" if ref["minutes_to_resolve"] is not None
                 else (", resolved" if ref["resolved"] else ", not resolved"))
        if ref.get("resolution_notes"):
            text += f": \"{ref['resolution_notes']}\""
        if history.get("same_alarm_last_24h"):
            text += f". {history['same_alarm_last_24h']} time(s) in the last 24 h."
    else:
        text = "No earlier incident of this alarm or equipment is recorded."
    sop = facts.get("sop") or {}
    from cause_candidates import leading_summary
    return {
        "leading_hypothesis": leading_summary(candidates),
        "likely_cause": top["statement"] if top else "The record does not point to a likely cause.",
        "likely_cause_basis": top["basis"] if top else None,
        "top_steps": [{"step": s["n"], "page": s["page"], "text": s["text"]} for s in chosen],
        "steps_source": f"{sop['sop_id']} ({sop.get('status')})" if sop.get("sop_id") else None,
        "history": text,
    }


_KIND_SCORE = {"trip": 90, "sop": 82, "standby": 75, "early": 55, "action": 40}


def _clamp(value: float) -> int:
    return int(max(5, min(97, round(value))))


def scores(facts: Dict[str, Any], history: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Confidence scores (0-100) for the Home screen. Each is a rule-based
    rating of how much the record supports the item, with its basis written
    out. They are not probabilities and are not calibrated against outcomes."""
    candidates = facts.get("cause_candidates") or []
    top = candidates[0] if candidates else None
    sop = facts.get("sop") or {}
    readings = facts.get("readings") or []
    primary = facts.get("primary_alarm") or {}
    past_limit = [r for r in readings if r.get("state_at_worst") == "LIMIT EXCEEDED"]
    strong = [c for c in candidates if c["kind"] in ("trip", "sop", "standby")]
    data_ok = str(facts.get("data_source") or "").upper() not in ("STALE", "INVALID", "NO DATA")
    out: Dict[str, Dict[str, Any]] = {}

    # The incident itself: is the alarm real and backed by readings?
    value = 60 + (15 if primary.get("tag") else 0) + min(len(past_limit), 3) * 6 + (5 if data_ok else -25)
    out["incident"] = {"pct": _clamp(value), "basis": f"Alarm recorded; {len(past_limit)} reading(s) past an SOP limit; data {'valid' if data_ok else 'stale or invalid'}"}

    # The component to check: the top possible cause.
    if top:
        value = _KIND_SCORE.get(top["kind"], 50) + min(len(top.get("supporting") or []) - 1, 3) * 2 - len(top.get("contradicting") or []) * 8
        if len(strong) >= 2:
            value -= 12  # another cause is also backed by the record
        out["component"] = {"pct": _clamp(value), "basis": f"Top possible cause: {top['basis']}; {len(top.get('supporting') or [])} supporting record(s)"
                            + ("; another cause is also backed by the record" if len(strong) >= 2 else "")}
    else:
        out["component"] = {"pct": 20, "basis": "The record does not point to a component"}

    # The suggested SOP: how well it matches this incident.
    matched = [x["signal"] for x in sop.get("match_signals") or [] if x.get("matched")]
    if sop.get("sop_id"):
        # Coverage sets the base; each independent match signal adds a little.
        value = {"full": 70, "partial": 50}.get(sop.get("coverage"), 40) + len(matched) * 5
        out["sop"] = {"pct": _clamp(value), "basis": f"{sop['sop_id']} coverage {sop.get('coverage')}; matched on "
                      + (", ".join(matched).lower() or "passage text") + ". Relevance only; the SOP is " + str(sop.get("status"))}
    else:
        out["sop"] = {"pct": 5, "basis": "No matching SOP was found"}

    # Root-cause evidence: how strong the evidence behind the possible causes is.
    if candidates:
        value = 35 + len(strong) * 15 + min(sum(len(c.get("supporting") or []) for c in strong), 6) * 4 + (8 if any(c.get("before_alarm") for c in candidates) else 0)
        out["root_cause"] = {"pct": _clamp(value), "basis": f"{len(strong)} cause(s) backed by a trip, an SOP 'suspect' limit or a standby status; "
                             f"{sum(len(c.get('supporting') or []) for c in candidates)} supporting record(s)"}
    else:
        out["root_cause"] = {"pct": 15, "basis": "No possible cause in the record"}

    # Diagnosis: the explanation as a whole.
    value = (out["root_cause"]["pct"] + out["sop"]["pct"]) / 2 - (10 if not history.get("same_alarm_total") else 0) - (0 if data_ok else 20)
    out["diagnosis"] = {"pct": _clamp(value), "basis": "Average of root-cause evidence and SOP match"
                        + ("; no earlier incident to compare with" if not history.get("same_alarm_total") else "")}

    # Next best action: where the steps come from.
    resolved_before = [e for e in history.get("earlier") or [] if e.get("resolution_notes")]
    if sop.get("sop_id") and sop.get("steps"):
        source, value = "sop", out["sop"]["pct"] - 4
        basis = f"Steps from {sop['sop_id']} ({sop.get('status')})"
    elif resolved_before:
        source, value = "history", 55
        basis = f"No SOP; resolution notes from {len(resolved_before)} earlier incident(s)"
    else:
        source, value = "analysis", 35
        basis = "No SOP or earlier fix; reasoning from the root-cause analysis and event story"
    out["next_action"] = {"pct": _clamp(value), "basis": basis, "source": source}

    # How to verify resolution: are recovery criteria written down?
    checks = facts.get("verification") or []
    out["verify"] = {"pct": _clamp(88 if checks else 45), "basis": f"{len(checks)} recovery criteria from the SOP" if checks
                     else "No recovery criteria in the SOP; readings back in their normal ranges are used"}
    for item in out.values():
        item["note"] = "Rule-based evidence score, not a probability"
    return out


def component_to_check(facts: Dict[str, Any], full_event: Dict[str, Any]) -> Dict[str, Any]:
    candidates = facts.get("cause_candidates") or []
    top = candidates[0] if candidates else None
    if top:
        tags = [t for t in top.get("tags") or [] if t]
        return {"name": top.get("equipment") or facts.get("equipment"), "id": tags[0] if tags else None,
                "reason": top["statement"]}
    return {"name": facts.get("equipment"), "id": (facts.get("primary_alarm") or {}).get("tag"), "reason": "Equipment of the primary alarm"}


def _found_by(full_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    found = ((full_event.get("sop_match") or {}).get("why_retrieved") or {}).get("found_by")
    if not found:
        return None
    return {key: found.get(key) for key in ("method", "method_text", "top_sop_id", "linked_sop_id", "agrees",
                                            "score", "linked_rank", "note")}


def build_insight(full_event: Dict[str, Any], db, facts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    import ai_diagnosis
    facts = facts or ai_diagnosis.build_facts(full_event, db)
    history = facts.get("history") or history_reference(full_event, db)
    # The Related Incidents panel lists more than the AI prompt needs.
    related = history_reference(full_event, db, limit=8)
    sop = facts.get("sop") or {}
    return {
        "event_id": full_event.get("event_id"),
        "scores": scores(facts, history),
        "component": component_to_check(facts, full_event),
        "sop": {"sop_id": sop.get("sop_id"), "title": sop.get("title"), "status": sop.get("status"), "approved": bool(sop.get("approved")),
                "coverage": sop.get("coverage"), "pages": sop.get("pages"),
                # Plain words for the operator instead of a percentage.
                "match_label": ({"full": "Full match", "partial": "Partial match"}.get(sop.get("coverage"), "Weak match")
                                if sop.get("sop_id") else "No SOP found"),
                "matched_on": [x["signal"] for x in sop.get("match_signals") or [] if x.get("matched")],
                # How the SOP was found: Qdrant, then BM25, checked against the alarm-tag link.
                "found_by": _found_by(full_event)},
        "verification": facts.get("verification") or [],
        "cause_candidates": facts.get("cause_candidates") or [],
        "leading_hypothesis": __import__("cause_candidates").leading_summary(facts.get("cause_candidates") or []),
        "bluf": bluf(facts, history),
        "confidence": confidence(facts, history),
        "history": related,
        "pre_alarm": facts.get("pre_alarm") if "pre_alarm" in facts else pre_alarm_activity(full_event, db),
        "advisory_only": True,
    }
