"""Shift handover, written for the incoming operator.

Everything here is built from stored records: the incident's readings, its
alarm inputs, the backend's cause ranking, logged operator actions and the
SOP. Nothing is invented and nothing is sent to the PLC. The layout follows
normal control-room practice: plant status first, then one block per open
incident with what is wrong now, the likely cause (not confirmed), what was
done, what to do next and when to escalate. No database ids are shown; an
incident is named by its alarm and a short number.
"""
from __future__ import annotations

import re
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict, List, Optional

ESCALATE = re.compile(r"\b(notify|escalat|plant manager|supervisor|regulator)", re.I)
NO_SOP = {None, "", "SOP-NONE", "NONE", "UNKNOWN-SOP"}
_CLEARED = re.compile(r"^(ALM-[A-Z0-9-]+|[A-Z0-9-]+-(?:FAULT|TRIP))\s+reads FALSE.*$", re.I)
NOT_TRACKED = "permits and LOTO, maintenance work, laboratory results, chemical and polymer stock"
_CAUSE_CACHE: "OrderedDict[str, tuple]" = OrderedDict()
_CAUSE_TTL_SECONDS = 20.0


def short_id(event_id: Optional[str]) -> str:
    return str(event_id or "")[:8]


def _num(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    v = round(v, 2) if abs(v) < 100 else round(v)
    return str(int(v)) if float(v).is_integer() else str(v)


def _local_hm(ts: Optional[str]) -> Optional[str]:
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t.astimezone().strftime("%H:%M")


def now_lines(readings: List[Dict[str, Any]], limit: int = 4) -> List[str]:
    """What is wrong now: abnormal readings first, each with its normal range."""
    rows = [r for r in readings if r.get("now_value") is not None]
    abnormal = [r for r in rows if r.get("state_now") not in (None, "within normal")]
    out = [] if abnormal or not rows else ["All key readings are back within normal:"]
    for r in (abnormal or rows)[:limit]:
        unit = f" {r['unit']}" if r.get("unit") else ""
        text = f"{r['label']} {_num(r['now_value'])}{unit}"
        if r.get("normal_text"):
            text += f" (normal {r['normal_text']})"
        if r.get("now_value") == 0 and r.get("normal_low"):
            text += ", stopped"
        out.append(text)
    return out


def group_actions(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Logged operator actions, one line per operator and kind of action."""
    groups: "OrderedDict[tuple, Dict[str, Any]]" = OrderedDict()
    for a in actions or []:
        key = (str(a.get("action_type") or "ACTION").upper(), a.get("operator") or "operator not recorded")
        g = groups.setdefault(key, {"action_type": key[0], "operator": key[1], "count": 0, "first": None, "last": None, "notes": []})
        g["count"] += 1
        ts = a.get("timestamp")
        g["first"] = g["first"] or ts
        g["last"] = ts or g["last"]
        note = (a.get("notes") or "").strip()
        # Acknowledgement notes are generated boilerplate; keep only what an operator wrote.
        if note and g["action_type"] not in ("ACKNOWLEDGE",) and note not in g["notes"]:
            g["notes"].append(note)
    out = []
    for g in groups.values():
        when = _local_hm(g["first"])
        if g["last"] and _local_hm(g["last"]) != when:
            when = f"{when}–{_local_hm(g['last'])}"
        verb = {"ACKNOWLEDGE": "acknowledged", "INSPECT": "inspected", "ANNOTATE": "added a note",
                "RESOLVE": "resolved", "UNRESOLVED": "marked unresolved"}.get(g["action_type"], g["action_type"].lower())
        if g["action_type"] == "ACKNOWLEDGE":
            text = f"{g['operator']} acknowledged {g['count']} alarm{'s' if g['count'] != 1 else ''}"
            text += f" at {when}" if when else ""
            text += " (in OpsLens; not sent to SCADA or the PLC)"
        else:
            text = f"{g['operator']} {verb}" + (f" ×{g['count']}" if g["count"] > 1 else "") + (f" at {when}" if when else "")
            if g["notes"]:
                text += ": " + "; ".join(g["notes"][:2])
        out.append({**g, "text": text})
    return out


def escalation(brief: Dict[str, Any]) -> List[str]:
    """When to escalate, from SOP limits whose action is to notify someone."""
    sop = brief.get("sop") or {}
    if sop.get("sop_id") in NO_SOP:
        return ["No SOP covers this alarm: escalate to the shift supervisor."]
    out = []
    for r in brief.get("incident_readings") or []:
        action = str(r.get("limit_action") or "")
        if ESCALATE.search(action):
            page = f" ({sop['sop_id']} p.{r['limit_page']})" if r.get("limit_page") else f" ({sop['sop_id']})"
            limit = str(r.get("limit_text") or "").strip()
            if len(limit) > 40 or action.rstrip(".").lower() in limit.lower():
                out.append(f"{limit.rstrip('.')}{page}.")  # the SOP sentence already says what to do
            else:
                out.append(f"If {r['label'].lower()} reaches {limit}: {action.rstrip('.')}{page}.")
    return list(OrderedDict.fromkeys(out))[:2]


def likely_cause(full_event: Dict[str, Any], db) -> Dict[str, Any]:
    """The same leading and contributing causes the Root Cause page shows."""
    event_id = full_event.get("event_id") or ""
    hit = _CAUSE_CACHE.get(event_id)
    if hit and time.monotonic() - hit[0] < _CAUSE_TTL_SECONDS:
        return hit[1]
    result: Dict[str, Any] = {"text": None, "leading": None, "contributing": []}
    try:
        import ai_diagnosis
        from cause_candidates import leading_summary
        candidates = ai_diagnosis.build_facts(full_event, db).get("cause_candidates") or []
        lead = next((c for c in candidates if c.get("role") == "leading"), None)
        result = {"text": leading_summary(candidates),
                  "leading": (lead or {}).get("short"),
                  "contributing": [c.get("short") for c in candidates if c.get("role") == "contributing" and c.get("short")],
                  "insufficient": bool(lead is None and candidates and candidates[0].get("role") == "insufficient")}
    except Exception:  # noqa: BLE001 - the handover must still render without the ranking
        pass
    _CAUSE_CACHE[event_id] = (time.monotonic(), result)
    while len(_CAUSE_CACHE) > 64:
        _CAUSE_CACHE.popitem(last=False)
    return result


def checks(criteria: List[str], limit: int = 3) -> List[str]:
    """Recovery checks in operator words ('ALM-X reads FALSE ...' becomes 'Confirm alarm ALM-X has cleared')."""
    out = []
    for c in criteria or []:
        m = _CLEARED.match(str(c).strip())
        out.append(f"Confirm alarm {m.group(1)} has cleared on the HMI." if m else str(c))
    return list(OrderedDict.fromkeys(out))[:limit]


def status_line(alarm_active: bool, acknowledged: bool) -> str:
    if alarm_active:
        return "Alarm still active" + (", acknowledged" if acknowledged else ", not acknowledged")
    return "Alarm back to normal; confirm in the field, then record the resolution"


def shift_narrative_text(summary: Dict[str, Any]) -> str:
    """Plant status, what changed this shift, and what to review first, one point per line."""
    rows = summary.get("incident_handover") or []
    unresolved = summary.get("unresolved_incidents") or []
    alarms = summary.get("open_alarms") or []
    unacked = summary.get("unacknowledged_alarms") or []
    events = summary.get("events_in_shift")
    actions = summary.get("actions_in_shift")
    lines = []
    n = len(rows) or len(unresolved)
    by_sev: "OrderedDict[str, int]" = OrderedDict()
    for r in rows:
        sev = str(r.get("severity") or "unrated").lower()
        by_sev[sev] = by_sev.get(sev, 0) + 1
    sev_text = ", ".join(f"{v} {k}" for k, v in by_sev.items())
    lines.append(f"Plant status: {n} open incident{'s' if n != 1 else ''}" + (f" ({sev_text})" if sev_text else "")
                 + f". Alarms: {len(alarms)} active, {len(unacked)} not acknowledged.")
    if events is None:
        lines.append("This shift: shift timing is not configured, so shift counts are unavailable.")
    else:
        acked = summary.get("alarms_acknowledged_in_shift") or []
        resolved = summary.get("alarms_resolved_in_shift") or []
        plural = lambda n, word: f"{n} {word}{'s' if n != 1 else ''}"
        lines.append(f"This shift: {plural(len(events), 'new incident')}, {plural(len(acked), 'alarm acknowledgement')}, "
                     f"{len(resolved)} resolved, "
                     + (plural(len(actions), 'operator action') if actions is not None else "operator actions not recorded") + ".")
    if rows:
        lines.append("Review first:")
        for i, r in enumerate(rows[:6], start=1):
            lines.append(f"{i}. {r.get('alarm_description') or r.get('alarm')} · {r.get('equipment')}"
                         + (f" · {r['severity']}" if r.get("severity") else "") + f" · {r.get('status_line') or r.get('state')}")
    elif unresolved:
        lines.append("Review first: " + ", ".join(f"incident {short_id(u.get('incident_id'))}" for u in unresolved[:6]) + ".")
    repeats = [f"{name} ({count} open incidents)" for name, count in _repeats(rows).items()]
    if repeats:
        lines.append("Same alarm open more than once: " + "; ".join(repeats) + ".")
    lines.append(f"Not tracked by OpsLens: {NOT_TRACKED}. Check the shift log.")
    return "\n".join(lines)


def _repeats(rows: List[Dict[str, Any]]) -> "OrderedDict[str, int]":
    counts: "OrderedDict[str, int]" = OrderedDict()
    for r in rows:
        name = r.get("alarm_description") or r.get("alarm") or "alarm"
        counts[name] = counts.get(name, 0) + 1
    return OrderedDict((k, v) for k, v in counts.items() if v > 1)


def number_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Order by urgency (active critical first) and number them 1..n; flag repeats."""
    rank = {"critical": 0, "high": 1, "medium": 2, "med": 2, "low": 3}
    rows.sort(key=lambda r: (not r.get("alarm_active"), rank.get(str(r.get("severity") or "").lower(), 4),
                             r.get("alarm_description") or ""))
    repeats = _repeats(rows)
    for i, r in enumerate(rows, start=1):
        r["number"] = i
        name = r.get("alarm_description") or r.get("alarm") or "alarm"
        r["repeat_note"] = (f"{repeats[name]} open incidents for this alarm; close the older one once checked."
                            if name in repeats else None)
    return rows
