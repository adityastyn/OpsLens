"""Read-only views of one incident for the Operator Console, built from records."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import alarm_config
import incident_catalog
from tag_registry import TAG_REGISTRY


def _label(tag: Optional[str]) -> str:
    entry = TAG_REGISTRY.get(tag or "")
    return (entry.short_label if entry and entry.short_label else tag) or "Not recorded"


def _equipment(tag: Optional[str]) -> Optional[str]:
    entry = TAG_REGISTRY.get(tag or "")
    return entry.equipment_id if entry else None


def _severity(step: Dict[str, Any]) -> str:
    tag = step.get("tag")
    if alarm_config.is_alarm_tag(tag):
        return "Alarm" if step.get("value_after") in (True, None, "True") else "Cleared"
    value = step.get("value_after")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "Status"
    context = alarm_config.limit_context(tag, value) or {}
    if context.get("highest_exceeded"):
        return "Limit exceeded"
    return "Outside normal" if context.get("normal_state") not in (None, "within_normal") else "Normal"


def _relationship(tag: str, primary: Optional[str], topology) -> str:
    if tag == primary:
        return "Primary alarm of this incident"
    source = (incident_catalog.alarm_inputs().get(primary or "") or {}).get("source_tag")
    if tag == source:
        return "Measurement behind the primary alarm"
    distance = None
    if topology is not None:
        try:
            distance = topology.get_distance(primary, tag)
        except Exception:
            distance = None
    if distance:
        return f"Linked to the alarm in the plant model ({distance} step{'s' if distance > 1 else ''}). Link is configured, not proven"
    return "Recorded in this incident"


def build_timeline(full_event: Dict[str, Any], db, topology=None) -> Dict[str, Any]:
    """Every timestamped record of the incident. Nothing is added or inferred
    into the events themselves; relationships are labelled for what they are."""
    event_id = full_event.get("event_id")
    primary = full_event.get("primary_tag") or (full_event.get("candidate") or {}).get("primary_tag")
    rows: List[Dict[str, Any]] = []
    for step in (full_event.get("story") or {}).get("steps") or []:
        tag = step.get("tag")
        before, after = step.get("value_before"), step.get("value_after")
        rows.append({"timestamp": step.get("timestamp"), "kind": "observation", "basis": "Recorded observation",
                     "event": _label(tag), "tag": tag, "equipment": _equipment(tag), "severity": _severity(step),
                     "change": None if isinstance(after, bool) or after is None else f"{before} → {after} {step.get('unit') or ''}".strip(),
                     "relationship": _relationship(tag, primary, topology)})
    seen = set()
    for alarm in db.get_individual_alarms(event_id):
        for item in db.get_alarm_transitions(alarm["alarm_id"]):
            key = (alarm["tag"], item.get("state"), item.get("timestamp"))
            if key in seen:
                continue
            seen.add(key)
            rows.append({"timestamp": item.get("timestamp"), "kind": "alarm_state", "basis": "Recorded state change",
                         "event": f"{_label(alarm['tag'])}: {str(item.get('state')).replace('_', ' ').lower()}", "tag": alarm["tag"],
                         "equipment": alarm.get("equipment_id"), "severity": "State change", "change": None,
                         "operator": item.get("operator"), "relationship": _relationship(alarm["tag"], primary, topology)})
    for action in full_event.get("actions") or []:
        rows.append({"timestamp": action.get("timestamp"), "kind": "operator", "basis": "Recorded operator action",
                     "event": str(action.get("action_type")).replace("_", " ").title(), "tag": None, "equipment": None,
                     "severity": "Operator", "change": None, "operator": action.get("operator"), "notes": action.get("notes"),
                     "relationship": "Operator record for this incident"})
    rows.sort(key=lambda row: str(row.get("timestamp") or ""))
    return {"event_id": event_id, "events": rows, "observed_count": sum(1 for r in rows if r["kind"] == "observation"),
            "note": "Times are recorded. Order and plant-model links do not prove cause."}


def build_evidence(full_event: Dict[str, Any], db) -> Dict[str, Any]:
    """Ranked evidence and possible causes. Scores are never probabilities."""
    audit = (full_event.get("evidence_audit") or {}).get("scoring_breakdown") or {}
    scores = audit.get("tag_scores") or {}
    total = audit.get("total_score") or 0
    primary = full_event.get("primary_tag")
    steps = (full_event.get("story") or {}).get("steps") or []
    deltas = {d.get("tag"): d for d in (full_event.get("evidence") or {}).get("supporting_deltas") or []}
    items: List[Dict[str, Any]] = []
    for rank, (tag, row) in enumerate(sorted(scores.items(), key=lambda kv: kv[1].get("raw_score") or 0, reverse=True), start=1):
        step = next((s for s in reversed(steps) if s.get("tag") == tag), {})
        observed = row.get("observed_reading") or {}
        value = observed.get("value", step.get("value_after"))
        context = (alarm_config.limit_context(tag, value) or {}) if isinstance(value, (int, float)) and not isinstance(value, bool) else {}
        spread = observed.get("configured_range") or []
        expected = context.get("normal_text") or (f"{spread[0]} to {spread[1]} {observed.get('unit') or ''}".strip() if len(spread) == 2 else None)
        kind = "Alarm input" if alarm_config.is_alarm_tag(tag) else "Status" if isinstance(value, bool) else "Sensor"
        precedes, distance = row.get("precedes_primary"), row.get("topology_distance")
        order = "Changed before the alarm" if precedes else "Changed after the alarm"
        link = f"{distance} step(s) from the alarm in the plant model" if distance else "no configured link to the alarm"
        items.append({"rank": rank, "tag": tag, "item": _label(tag), "source": kind, "observed": value,
                      "unit": observed.get("unit") or step.get("unit"), "expected": expected,
                      "timestamp": observed.get("timestamp") or step.get("timestamp"), "relevance": f"{order}; {link}",
                      "ranking_score": row.get("raw_score"), "precedes_alarm": precedes, "topology_distance": distance,
                      "deviation_pct": row.get("deviation_pct"), "severity_weight": row.get("severity_weight"),
                      "explanation": (f"Change {row.get('deviation_pct')} % from normal midpoint, weight {row.get('severity_weight')}, {link}, "
                                      f"{'earlier' if precedes else 'later'} than the alarm.")})
    hypotheses = []
    for item in items[:3]:
        supports, contradicts = [], []
        (supports if item["precedes_alarm"] else contradicts).append(item["relevance"].split(";")[0])
        (supports if item["topology_distance"] else contradicts).append(item["relevance"].split("; ")[1].capitalize())
        reading = next((r for r in _readings(full_event, db) if r["tag"] == item["tag"]), None)
        # Only a measurement that actually went abnormal can stand in for a cause;
        # a status at its normal value (a pump still running) is not one.
        if not reading or reading["state_at_worst"] == "within normal":
            continue
        supports.append(f"Worst value {reading['worst_value']} {reading['unit']} was {reading['state_at_worst'].lower()}")
        hypotheses.append({"rank": item["rank"], "statement": f"Possible contributor: {item['item']}", "tag": item["tag"], "status": "HYPOTHESIS",
                           "role": "reading", "role_label": "Abnormal reading (no cause candidate)", "short": item["item"],
                           "ranking_score": item["ranking_score"], "why_ranked": item["explanation"],
                           "supporting": supports, "contradicting": contradicts})
    # Possible causes read from trips, SOP 'suspect' limits and early changes,
    # the same list the AI diagnosis is given.
    try:
        from cause_candidates import build_cause_candidates
        from operator_brief import build_operator_brief
        brief = build_operator_brief(full_event, db)
        own = set((brief.get("scenario") or {}).get("incident_tags") or []) or set(scores) | {primary}
        candidates = build_cause_candidates(full_event, brief, own, db)
    except Exception:
        candidates = []
    comparison = None
    if candidates:
        from cause_candidates import FACTOR_LABELS, FACTOR_POINTS, ROLE_LABELS
        hypotheses = [{"rank": c["rank"], "statement": c["statement"], "short": c.get("short"), "tag": (c.get("tags") or [None])[0],
                       "status": "HYPOTHESIS", "ranking_score": None, "kind": c["kind"],
                       "role": c.get("role"), "role_label": ROLE_LABELS.get(c.get("role"), "Possible cause"), "role_note": c.get("role_note"),
                       "evidence_points": c.get("evidence_points"), "ranking_note": c.get("ranking_note"),
                       "factors": [{"factor": FACTOR_LABELS[k], "key": k, "earned": (c.get("evidence_factors") or {}).get(k, 0),
                                    "points": FACTOR_POINTS.get(k)} for k in [*FACTOR_POINTS, "contradiction"]],
                       "why_ranked": f"{c['basis']}. First seen {c.get('first_seen') or 'not recorded'}"
                                     f"{', before the primary alarm' if c.get('before_alarm') else ''}.",
                       "supporting": c["supporting"], "contradicting": c["contradicting"]} for c in candidates]
        lead = next((h for h in hypotheses if h["role"] == "leading"), None)
        runner = next((h for h in hypotheses if h is not lead and h["role"] in ("contributing", "chain")), None)
        if lead:
            comparison = {"leading": lead["short"] or lead["statement"], "leading_points": lead["evidence_points"],
                          "runner_up": (runner or {}).get("short"), "runner_up_points": (runner or {}).get("evidence_points"),
                          "why": runner["ranking_note"] if runner else lead["ranking_note"]}
    return {"event_id": full_event.get("event_id"), "primary_tag": primary, "items": items, "hypotheses": hypotheses,
            "comparison": comparison,
            "total_score": total, "score_note": "Ranking scores order the evidence. They are not probabilities and do not add up to a chance of being the cause.",
            "candidate_note": ("Each possible cause is assessed on its own evidence: a recorded trip, timing against the alarm, size of the "
                               "deviation, readings that agree, an SOP limit with an action, the plant's configured links and earlier fixes, "
                               "minus related readings that stayed normal. Evidence points rank causes; they are not probabilities."),
            "formula": audit.get("formula")}


def _readings(full_event: Dict[str, Any], db) -> List[Dict[str, Any]]:
    from operator_brief import build_operator_brief
    cached = full_event.get("_brief_readings")
    if cached is None:
        cached = build_operator_brief(full_event, db).get("incident_readings") or []
        full_event["_brief_readings"] = cached
    return cached


def build_inspection(full_event: Dict[str, Any], db) -> Dict[str, Any]:
    """Diagnostic inspection for development and demonstrations (Engineering
    Console only): what the analysis read and how it ranked the causes.

    Everything under 'computed' is derived at request time from the stored
    telemetry and alarm records. The 'test_reference' block is the scenario's
    injected fault, shown only so a tester can compare; no part of the
    analysis reads it.
    """
    import ai_diagnosis
    import incident_catalog
    from datetime import datetime, timedelta, timezone

    facts = ai_diagnosis.build_facts(full_event, db)
    tags = list(facts.get("incident_tags") or [])
    trigger = full_event.get("trigger_timestamp")
    try:
        t0 = datetime.fromisoformat(str(trigger).replace("Z", "+00:00"))
        t0 = t0 if t0.tzinfo else t0.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        t0 = None
    since = (t0 - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S") if t0 else None

    def _hms(ts: Any) -> Optional[str]:
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            return t.strftime("%H:%M:%S")
        except (TypeError, ValueError):
            return None

    # Incoming readings and the first time each crossed an SOP/alarm limit.
    readings, breaches, alarms = [], [], []
    for tag in tags:
        try:
            series = [r for r in db.get_readings(tag=tag, start_time=since, limit=4000)]
        except Exception:
            series = []
        series.sort(key=lambda r: str(r.timestamp))
        if not series:
            continue
        values = [r for r in series if isinstance(r.value, (int, float)) and not isinstance(r.value, bool)]
        if alarm_config.is_alarm_tag(tag) or all(isinstance(r.value, bool) for r in series):
            on = next((r for r in series if r.value is True), None)
            entry = {"tag": tag, "label": _label(tag), "first_on": on.timestamp if on else None, "first_on_hms": _hms(on.timestamp) if on else None,
                     "now": series[-1].value, "samples": len(series)}
            (alarms if alarm_config.is_alarm_tag(tag) else readings).append({**entry, "kind": "alarm" if alarm_config.is_alarm_tag(tag) else "status"})
            continue
        if not values:
            continue
        first_cross = {}
        for r in values:
            ctx = alarm_config.limit_context(tag, r.value) or {}
            hit = ctx.get("highest_exceeded") or {}
            if values[0].unit == "A" and float(r.value) == 0:
                hit = {}  # 0 A: the motor is stopped, so a low-load limit does not apply (same rule as the brief)
            if hit and hit.get("name") not in first_cross:
                first_cross[hit.get("name")] = {"tag": tag, "label": _label(tag), "limit": hit.get("source_text") or hit.get("text") or hit.get("name"),
                                                "value": r.value, "unit": values[0].unit, "at": r.timestamp, "at_hms": _hms(r.timestamp),
                                                "action": hit.get("action")}
        breaches.extend(first_cross.values())
        fact = next((x for x in facts.get("readings") or [] if x.get("tag") == tag), {})
        readings.append({"tag": tag, "label": _label(tag), "kind": "measurement", "unit": values[0].unit, "samples": len(values),
                         "first": values[0].value, "first_at_hms": _hms(values[0].timestamp),
                         "worst": fact.get("worst"), "worst_at": fact.get("worst_at"), "now": values[-1].value,
                         "normal": fact.get("normal"), "state_at_worst": fact.get("state_at_worst"), "state_now": fact.get("state_now")})
    breaches.sort(key=lambda b: str(b["at"]))
    alarms.sort(key=lambda a: str(a.get("first_on") or "~"))

    evidence = build_evidence(full_event, db)
    spec = incident_catalog.scenario_for_incident(full_event.get("primary_tag"), (full_event.get("candidate") or {}).get("clustered_tags"))
    reference = None
    if spec is not None:
        reference = {
            "note": "Test reference only. Defines the injected conditions of the simulated scenario. The analysis above never reads it; "
                    "compare it with the computed ranking to validate OpsLens.",
            "scenario_id": spec.scenario_id, "scenario_name": spec.name,
            "injected_conditions": [f"{s.label}: {s.normal_text} -> {s.incident_text}" for s in spec.signals if s.status in ("c", "w")]
                                   + [f"{s.label}: {s.text}" for s in spec.statuses if s.incident != s.normal],
            "scripted_root": list(spec.root.values()),
            "expected_primary": (incident_catalog.REFERENCE_OUTCOME.get(spec.scenario_id) or {}).get("primary"),
            "expected_secondary": (incident_catalog.REFERENCE_OUTCOME.get(spec.scenario_id) or {}).get("secondary") or [],
        }
    lead = next((h for h in evidence.get("hypotheses") or [] if h.get("role") == "leading"), None)
    if reference is not None:
        reference["computed_matches_expected"] = reference_match((lead or {}).get("short") or (lead or {}).get("statement"),
                                                                 reference.get("expected_primary"))
    return {
        "event_id": full_event.get("event_id"),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "provenance": {
            "predefined": ["Scenario title and injected fault conditions (test reference below)", "Equipment and tag configuration",
                           "Normal ranges and SOP/alarm limits", "Configured plant links (which reading may influence which)",
                           "Alarm-to-SOP links"],
            "computed": ["Sensor readings (simulated telemetry stored in the database)", "Alarm events and their times",
                         "Limit breaches", "Incident grouping", "Event Story order", "Reading ranking scores",
                         "Candidate causes, their evidence points and roles", "Leading hypothesis", "AI diagnosis (checked against these facts)"],
        },
        "primary_alarm": facts.get("primary_alarm"), "equipment": facts.get("equipment"), "process_area": facts.get("process_area"),
        "incident_tags": tags, "readings": readings, "breaches": breaches, "alarms": alarms,
        "timeline": facts.get("sequence") or [],
        "evidence_items": evidence.get("items") or [], "candidates": evidence.get("hypotheses") or [],
        "comparison": evidence.get("comparison"), "candidate_note": evidence.get("candidate_note"),
        "computed_leading": (lead or {}).get("short") or (lead or {}).get("statement"),
        "test_reference": reference,
    }


_STOP = {"the", "and", "with", "on", "of", "a", "an", "to", "in", "at", "low", "high", "trip", "tripped", "fault", "motor"}


def reference_match(computed: Optional[str], expected: Optional[str]) -> Optional[bool]:
    """Whether the computed leading hypothesis names the reference's primary
    fault: the same equipment code (P-101, RAS-P1), or two shared content
    words. A plain comparison for test records, never fed back to the analysis."""
    if not computed or not expected:
        return None
    import re
    code = re.compile(r"\b([A-Z]{1,4}-[A-Z]?\d{1,3}[A-Z]?|[A-Z]{2,4}-[A-Z]\d)\b")
    a, b = set(code.findall(computed)), set(code.findall(expected))
    if a and b:
        return bool(a & b)
    words = lambda t: {w for w in re.findall(r"[a-z0-9]{3,}", t.lower()) if w not in _STOP}
    return len(words(computed) & words(expected)) >= 2
