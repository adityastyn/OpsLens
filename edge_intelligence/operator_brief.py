"""
OpsLens - Deterministic Operator Brief.

Builds the eight-part incident brief the operator sees first:

  1. Problem / observed alarm
  2. Possible causes (hypotheses, never a confirmed root cause)
  3. Supporting evidence (observed vs. calculated, each labelled)
  4. Missing evidence and limitations
  5. Recommended safe inspection steps (verbatim SOP text only)
  6. Relevant SOP and approval status
  7. Verification criteria (verbatim SOP text only)
  8. Resolution status

Every number is either a recorded reading or calculated here in Python. No
LLM is involved, so the brief is always available - including when LM Studio,
Groq and Qdrant are all unavailable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import alarm_config
from tag_registry import TAG_REGISTRY

STALE_AFTER_SECONDS = 120.0
INCIDENT_LEAD_SECONDS = 60.0


def primary_alarm_first_on(db, tag: Optional[str], trigger_timestamp: Optional[str]) -> Optional[str]:
    """When the primary alarm came on for this incident: the start of the
    unbroken 'on' run of its alarm input that covers the incident's trigger
    time. The trigger timestamp alone can be later, when the incident's
    primary alarm is reassigned after more alarms arrive, and then a change
    after the alarm would wrongly count as before it."""
    trigger = _parse_ts(trigger_timestamp)
    if db is None or not tag or trigger is None:
        return trigger_timestamp
    try:
        from datetime import timedelta
        rows = db.get_readings(tag=tag, start_time=(trigger - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S"), limit=5000)
    except Exception:
        return trigger_timestamp
    rows = sorted((r for r in rows if _parse_ts(r.timestamp) and isinstance(r.value, bool)), key=lambda r: _parse_ts(r.timestamp))
    rows = [r for r in rows if _parse_ts(r.timestamp) <= trigger + timedelta(seconds=1)]
    if not rows or rows[-1].value is not True:
        return trigger_timestamp
    start = rows[-1]
    for row in reversed(rows):
        if row.value is not True:
            break
        start = row
    return start.timestamp if _parse_ts(start.timestamp) < trigger else trigger_timestamp


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _label(tag: str) -> str:
    entry = TAG_REGISTRY.get(tag)
    return entry.short_label if entry and entry.short_label else tag


def _source_label(data_source: Optional[str]) -> str:
    source = str(data_source or "").lower()
    if any(word in source for word in ("simulat", "synthetic")):
        return "SIMULATED"
    if any(word in source for word in ("plc", "opcua", "opc_ua", "live")):
        return "LIVE PLC"
    return "RECORDED (source not identified)"


def _equipment_readings(db, equipment_id: Optional[str], reference_time: Optional[datetime],
                        incident_tags: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Latest reading of every tag that belongs to this incident.

    For the six catalog incidents that is exactly the incident's own tag list
    (so a foam incident never shows the centrate incident's blower airflow);
    otherwise every registry tag on the incident's equipment."""
    rows = []
    if db is None or (not equipment_id and not incident_tags):
        return rows
    selected = list(incident_tags) if incident_tags else [
        tag for tag, entry in TAG_REGISTRY.items() if entry.equipment_id == equipment_id]
    for tag in selected:
        entry = TAG_REGISTRY.get(tag)
        if entry is None:
            continue
        reading = db.get_latest_reading(tag)
        if reading is None:
            rows.append({"tag": tag, "label": _label(tag), "class": alarm_config.classify_tag(tag, entry.data_type),
                         "value": None, "unit": entry.unit, "status": "MISSING"})
            continue
        ts = _parse_ts(reading.timestamp)
        age = (reference_time - ts).total_seconds() if (reference_time and ts) else None
        quality = str(reading.data_quality or "UNKNOWN").upper()
        status = "STALE" if quality == "STALE" or (age is not None and age > STALE_AFTER_SECONDS) else (
            "INVALID" if quality == "INVALID" else "OK")
        rows.append({
            "tag": tag, "label": _label(tag), "class": alarm_config.classify_tag(tag, entry.data_type),
            "value": reading.value, "unit": entry.unit, "timestamp": reading.timestamp,
            "data_quality": quality, "data_source": reading.data_source,
            "source_label": _source_label(reading.data_source),
            "age_seconds": round(age, 1) if age is not None else None, "status": status,
        })
    return rows


def _band_distance(value: float, low: Optional[float], high: Optional[float]) -> float:
    """How far a value is outside its normal band, as a share of the band."""
    span = (high - low) if low is not None and high is not None and high > low else max(abs(low or high or 1.0), 1.0)
    if low is not None and value < low:
        return (low - value) / span
    if high is not None and value > high:
        return (value - high) / span
    return 0.0


def incident_readings(db, tags: Optional[List[str]], story: Dict[str, Any],
                      trigger_timestamp: Optional[str]) -> List[Dict[str, Any]]:
    """What each measurement did during the incident, not only its value now.

    Once an alarm has returned to normal the latest reading is healthy, so a
    diagnosis built on latest values would describe a plant with no problem.
    Reports the start value, the worst recorded value and when, and the latest
    value. All recorded; nothing is interpolated."""
    if db is None or not tags:
        return []
    steps = story.get("steps") or []
    times = [t for t in (_parse_ts(s.get("timestamp")) for s in steps) if t]
    trigger = _parse_ts(trigger_timestamp)
    start = min(times + ([trigger] if trigger else []), default=None)
    if start is None:
        return []
    # The first event in the story comes after the excursion began; include the lead-up.
    start_text = (start - timedelta(seconds=INCIDENT_LEAD_SECONDS)).isoformat().replace("+00:00", "Z")
    rows: List[Dict[str, Any]] = []
    for tag in tags:
        entry = TAG_REGISTRY.get(tag)
        if entry is None or alarm_config.classify_tag(tag, entry.data_type) != "measurement":
            continue
        series = [r for r in db.get_readings(tag=tag, start_time=start_text, limit=4000)
                  if isinstance(r.value, (int, float)) and not isinstance(r.value, bool)]
        if not series:
            continue
        normal = (alarm_config.definition_for_tag(tag) or {}).get("normal") or {}
        low, high = normal.get("low"), normal.get("high")
        if low is None and high is None:
            low, high = entry.baseline_min, entry.baseline_max
        worst = max(series, key=lambda r: _band_distance(float(r.value), low, high))
        if _band_distance(float(worst.value), low, high) == 0.0:
            worst = max(series, key=lambda r: abs(float(r.value) - float(series[0].value)))
        at_worst = alarm_config.limit_context(tag, worst.value) or {}
        now = alarm_config.limit_context(tag, series[-1].value) or {}
        hit = at_worst.get("highest_exceeded") or {}
        if entry.unit == "A" and float(worst.value) == 0:
            hit = {}  # 0 A means the motor is stopped, so a low-load limit does not apply
        distance = _band_distance(float(worst.value), low, high)
        rows.append({
            "tag": tag, "label": _label(tag), "unit": entry.unit,
            "normal_text": at_worst.get("normal_text") or (f"{low:g} to {high:g} {entry.unit}" if low is not None and high is not None else None),
            "normal_low": low, "normal_high": high,
            "first_value": series[0].value, "worst_value": worst.value, "worst_at": worst.timestamp,
            "now_value": series[-1].value, "now_at": series[-1].timestamp,
            "limit_reached": hit.get("name"), "limit_text": hit.get("source_text") or hit.get("text"),
            "limit_action": hit.get("action"), "limit_page": hit.get("source_page"),
            "state_at_worst": "LIMIT EXCEEDED" if hit else ("outside normal" if distance > 0 else "within normal"),
            "state_now": "LIMIT EXCEEDED" if now.get("highest_exceeded") else (
                "outside normal" if _band_distance(float(series[-1].value), low, high) > 0 else "within normal"),
            "severity_rank": (2 if hit else 1 if distance > 0 else 0, round(distance, 4)),
            "data_source": _source_label(series[-1].data_source),
        })
    rows.sort(key=lambda r: r["severity_rank"], reverse=True)
    return rows


def _format_value(value: Any) -> str:
    return f"{float(value):,.4g}" if abs(float(value)) < 1000 else f"{float(value):,.0f}"


def what_happened(problem: Dict[str, Any], readings: List[Dict[str, Any]], state_note: Optional[str]) -> str:
    """One plain sentence for the operator, built from recorded values only."""
    name = problem.get("alarm_description") or problem.get("alarm_tag") or "An alarm"
    equipment = problem.get("equipment") or "the plant"
    sentence = f"{name} on {equipment}."
    top = [r for r in readings if r["state_at_worst"] == "LIMIT EXCEEDED"][:2] or [
        r for r in readings if r["state_at_worst"] == "outside normal"][:2]
    if top:
        parts = [f"{r['label']} reached {_format_value(r['worst_value'])} {r['unit']} (normal {r['normal_text']})" for r in top]
        sentence += " " + "; ".join(parts) + "."
    if state_note:
        sentence += f" {state_note}"
    return sentence


def build_operator_brief(full_event: Dict[str, Any], db=None) -> Dict[str, Any]:
    candidate = full_event.get("candidate") or {}
    story = full_event.get("story") or {}
    evidence = full_event.get("evidence") or {}
    sop = full_event.get("sop_match") or {}
    runtime = full_event.get("runtime_alarm_state") or {}
    if not runtime and db is not None:
        # The HTTP layer normally attaches the runtime state; derive it from
        # the latest recorded alarm-input reading when it is absent.
        tag = full_event.get("primary_tag") or (full_event.get("candidate") or {}).get("primary_tag")
        latest = db.get_latest_reading(tag) if tag and alarm_config.is_alarm_tag(tag) else None
        if latest is not None:
            runtime = {"tag": tag, "observed": True, "timestamp": latest.timestamp,
                       "active": latest.value is True or str(latest.value).lower() in ("true", "1", "1.0"),
                       "data_source": latest.data_source, "data_quality": latest.data_quality}
    actions = full_event.get("actions") or []
    alarms = full_event.get("individual_alarms") or []

    primary_tag = full_event.get("primary_tag") or candidate.get("primary_tag")
    equipment_id = candidate.get("equipment_id") or (TAG_REGISTRY[primary_tag].equipment_id if primary_tag in TAG_REGISTRY else None)
    now = datetime.now(timezone.utc)
    latest_story_ts = max((_parse_ts(s.get("timestamp")) for s in story.get("steps") or [] if _parse_ts(s.get("timestamp"))), default=None)
    import incident_catalog
    scenario = incident_catalog.scenario_for_incident(primary_tag, candidate.get("clustered_tags") or [])
    incident_tags = incident_catalog.scenario_tags(scenario) if scenario else None
    if scenario:
        equipment_id = scenario.equipment_id  # the unit that owns the incident, not the alarm's sensor
    readings = _equipment_readings(db, equipment_id, max(filter(None, [latest_story_ts, now])) if db else None,
                                   incident_tags)

    # --- 1. Problem -------------------------------------------------------
    alarm_inputs = [r for r in readings if r["class"] == "alarm"]
    active_alarm_inputs = [r for r in alarm_inputs if r.get("value") is True]
    definition = alarm_config.definition_for_tag(primary_tag or "")
    problem = {
        "alarm_tag": primary_tag,
        "alarm_description": _label(primary_tag) if primary_tag else None,
        "process_area": (definition or {}).get("process_area"),
        "equipment": equipment_id,
        "first_triggered_at": primary_alarm_first_on(db, primary_tag, full_event.get("trigger_timestamp")),
        "alarm_input_active": runtime.get("active") if runtime else None,
        "active_alarm_inputs": [r["tag"] for r in active_alarm_inputs],
        "data_source": _source_label(runtime.get("data_source") if runtime else None),
        "priority_from_source": candidate.get("source_priority") or candidate.get("alarm_severity") or "NOT SUPPLIED BY SOURCE",
        "status_tags_are_not_alarms": [r["tag"] for r in readings if r["class"] == "status"],
    }

    # --- 3. Evidence: observed and calculated ---------------------------
    observed = [{
        "kind": "OBSERVED",
        "tag": step.get("tag"),
        "label": _label(step.get("tag")),
        "timestamp": step.get("timestamp"),
        "description": step.get("description"),
        "value_before": step.get("value_before"),
        "value_after": step.get("value_after"),
        "unit": step.get("unit"),
        "data_source": _source_label(step.get("data_source")),
    } for step in story.get("steps") or []]

    calculated = [{
        "kind": "CALCULATED",
        "tag": d.get("tag"),
        "label": _label(d.get("tag")),
        "change_percent_vs_baseline_midpoint": d.get("delta_pct"),
        "direction": d.get("direction"),
        "calculated_by": "evidence_engine (Python)",
    } for d in evidence.get("supporting_deltas") or []]

    stopped_equipment = any(
        (r["class"] == "status" and r.get("value") is False and alarm_config.status_normal_value(r["tag"]) is True)
        or (r["class"] == "alarm" and r.get("value") is True and "FAULT" in r["tag"].upper())
        for r in readings
    )
    limit_checks = []
    for row in readings:
        if row["class"] != "measurement":
            continue
        ctx = alarm_config.limit_context(row["tag"], row.get("value"), data_quality=row.get("data_quality"))
        if ctx is not None and ctx.get("applied") and stopped_equipment and ctx.get("unit") == "A" and ctx.get("value") == 0:
            # 0 A on a stopped/tripped motor is expected, not a low-load condition.
            reason = "0 A with the pump stopped or tripped: the low-load limit does not apply (see P101-RUN / P101-FAULT)."
            ctx = {**ctx, "applied": False, "reason": reason, "summary": reason, "highest_exceeded": None,
                   "exceeded_limits": [], "next_limit": None}
        if ctx is not None:
            ctx["kind"] = "CALCULATED"
            ctx["label"] = row["label"]
            limit_checks.append(ctx)

    p1_reached = [c for c in limit_checks if c.get("p1_escalation_reached")]
    p1_conditional = [c for c in limit_checks if c.get("p1_conditional_reached") and not c.get("p1_escalation_reached")]
    next_limits = [c for c in limit_checks if c.get("applied") and c.get("next_limit")]

    # --- 2. Possible causes (hypotheses) --------------------------------
    hypotheses = []
    contributor = evidence.get("likely_contributor_tag")
    if contributor:
        hypotheses.append({
            "kind": "HYPOTHESIS",
            "statement": f"{_label(contributor)} ({contributor}) is the strongest recorded signal linked to this alarm.",
            "ranking_score": evidence.get("confidence_score"),
            "ranking_note": "Heuristic evidence-ranking score, NOT a probability that this is the root cause.",
            "how_to_confirm": "Compare the field instrument / local reading with the HMI value and check the related equipment state.",
        })
    status_changes = [r for r in readings if r["class"] == "status" and r.get("value") is not None
                      and alarm_config.status_normal_value(r["tag"]) is not None
                      and r["value"] != alarm_config.status_normal_value(r["tag"])]
    for row in status_changes:
        hypotheses.append({
            "kind": "HYPOTHESIS",
            "statement": f"{row['label']} ({row['tag']}) is {row['value']} (normally {alarm_config.status_normal_value(row['tag'])}); equipment unavailability may contribute.",
            "ranking_score": None,
            "ranking_note": "Equipment status change; recorded, not diagnosed.",
            "how_to_confirm": "Check the local panel / fault display for the equipment.",
        })
    fault_inputs = [r for r in active_alarm_inputs if r["tag"] != primary_tag]
    for row in fault_inputs:
        hypotheses.append({
            "kind": "HYPOTHESIS",
            "statement": f"{row['label']} ({row['tag']}) is active at the same time.",
            "ranking_score": None,
            "ranking_note": "Co-occurring alarm input; timing alone does not prove cause.",
            "how_to_confirm": "Record the equipment fault code; identify the cause before any reset.",
        })

    # --- 4. Missing evidence and limitations ----------------------------
    missing = []
    for row in readings:
        if row["status"] in ("MISSING", "STALE", "INVALID"):
            missing.append(f"{row['label']} ({row['tag']}): {row['status']}"
                           + (f", last reading {row['age_seconds']} s old" if row.get("age_seconds") else ""))
    process_area = (definition or {}).get("process_area")
    for item in alarm_config.definitions():
        mapping = item.get("opslens_mapping") or {}
        if item.get("process_area") == process_area and mapping.get("mapping") == "none":
            missing.append(f"{item['parameter']} is covered by {item['sop_id']} (p.{item['source_page']}) but is not instrumented in OpsLens.")
        elif item.get("process_area") == process_area and mapping.get("mapping") in ("unit_mismatch", "conflict"):
            missing.append(f"{item['parameter']}: manual limit not applied — {mapping.get('note')}")
    if not runtime.get("observed", True) if runtime else True:
        missing.append("The current state of the source alarm input has not been observed.")
    limitations = [
        "Evidence ranking is a heuristic; it does not confirm a root cause.",
        "Event order is the recorded chronological sequence, not causal proof.",
        "OpsLens is advisory and read-only; it cannot start, stop or reset equipment.",
    ]
    if problem["data_source"] == "SIMULATED":
        limitations.insert(0, "All readings for this incident are SIMULATED, not live PLC telemetry.")

    # --- 5/6. SOP steps and approval ------------------------------------
    sop_id = sop.get("sop_id")
    steps_block = {"procedure_title": None, "steps": [], "source": "none"}
    if sop_id and sop_id not in ("SOP-NONE", "NONE", "UNKNOWN-SOP") and sop.get("retrieval_status") == "match_found":
        try:
            from sop_steps import procedure_steps
            steps_block = procedure_steps(sop_id)
        except Exception:
            steps_block = {"procedure_title": None, "steps": [], "source": "unavailable"}
    import sop_approval
    approved = sop_approval.approved(sop_id, any(chunk.get("approved") is True for chunk in (sop.get("retrieved_chunks") or [])))
    sop_block = {
        "sop_id": sop_id,
        "title": sop.get("sop_title") or sop.get("title"),
        "retrieval_method": sop.get("retrieval_method"),
        "retrieval_status": sop.get("retrieval_status"),
        "source_document": ("SOP-MAN-000" if sop.get("retrieval_method") == "bundled_pdf_section" and sop_id != "SOP-AER-001" else sop_id),
        "source_pages": (sop.get("why_retrieved") or {}).get("source_pages"),
        "approved": approved,
        "approval_label": "APPROVED" if approved else "DRAFT — NOT APPROVED",
        "coverage": scenario.sop_coverage if scenario and scenario.sop_id == sop_id else None,
        "coverage_notes": list(scenario.sop_coverage_notes) if scenario and scenario.sop_id == sop_id else [],
    }
    if steps_block.get("steps"):
        safe_steps = [{"number": s["number"], "text": s["text"], "page": s["page"], "source": "verbatim"} for s in steps_block["steps"]]
    else:
        rules = (alarm_config.load_reference().get("alarm_response_rules") or {})
        safe_steps = [{"number": i + 1, "text": text, "page": None, "source": f"{rules.get('source_document')} (general alarm response)"}
                      for i, text in enumerate(rules.get("rules") or [])]

    # --- 7. Verification criteria ---------------------------------------
    verification = []
    for row in readings:
        item = alarm_config.definition_for_tag(row["tag"])
        if item and (item.get("opslens_mapping") or {}).get("tag") == row["tag"]:
            for text in item.get("verification_criteria") or []:
                if text not in verification:
                    verification.append(text)
    alarm_input_tag = primary_tag if alarm_config.is_alarm_tag(primary_tag or "") else None
    if alarm_input_tag:
        verification.append(f"{alarm_input_tag} reads FALSE (returned to normal) at a timestamp later than the alarm, on the operator HMI.")

    # --- 8. Resolution status -------------------------------------------
    acknowledged = any(str(a.get("lifecycle_state") or "").upper() == "ACKNOWLEDGED" for a in alarms) or any(
        str(a.get("action_type") or "").upper() in ("ACKNOWLEDGE", "ACKNOWLEDGED") for a in actions)
    input_active = runtime.get("active") if runtime else None
    if full_event.get("status") == "RESOLVED":
        state = "RESOLVED (operator confirmed)"
    elif input_active is False:
        state = "ALARM RETURNED TO NORMAL — awaiting operator confirmation"
    elif acknowledged:
        state = "ACTIVE / ACKNOWLEDGED — acknowledgement does not clear the alarm"
    else:
        state = "ACTIVE / UNACKNOWLEDGED"

    escalation_priority = "P1" if p1_reached else None
    ack = alarm_config.priority_for(escalation_priority or problem["priority_from_source"])
    deadline = None
    if ack and not acknowledged:
        first = _parse_ts(full_event.get("trigger_timestamp"))
        if first:
            due = first.timestamp() + float(ack["acknowledge_within_seconds"])
            deadline = {
                "priority": ack["priority"],
                "acknowledge_within": ack["acknowledge_within_text"],
                "operator_on_location_within": ack["operator_on_location_within_text"],
                "due_at": datetime.fromtimestamp(due, timezone.utc).isoformat().replace("+00:00", "Z"),
                "overdue": now.timestamp() > due,
                "source": "SOP-MAN-000 Section 6, page 4",
            }

    headline = _headline(problem, limit_checks, p1_reached, p1_conditional)
    tracked_tags = incident_tags or [r["tag"] for r in readings]
    during = incident_readings(db, tracked_tags, story, full_event.get("trigger_timestamp"))
    source_tag = (incident_catalog.alarm_inputs().get(primary_tag) or {}).get("source_tag")
    during.sort(key=lambda row: row["tag"] != source_tag)  # the alarm's own measurement first
    state_note = ("The alarm input has since returned to normal." if input_active is False
                  else "The alarm is still active." if input_active else None)
    return {
        "event_id": full_event.get("event_id"),
        # Only the incident's identity is exposed here. The simulator's scripted
        # root cause stays in the Scenario Lab; neither the brief nor the AI
        # is handed the answer it is supposed to reason towards.
        "scenario": ({"scenario_id": scenario.scenario_id, "number": scenario.number, "name": scenario.name,
                      "severity": scenario.severity, "priority_note": scenario.priority_note,
                      "incident_tags": incident_tags}
                     if scenario else None),
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "generated_by": "deterministic_backend",
        "llm_used": False,
        "headline": headline,
        "what_happened": what_happened(problem, during, state_note),
        "incident_readings": during,
        "problem": problem,
        "possible_causes": hypotheses,
        "supporting_evidence": {"observed": observed, "calculated": calculated, "limit_checks": limit_checks},
        "missing_evidence": missing,
        "limitations": limitations,
        "safe_inspection_steps": {"procedure_title": steps_block.get("procedure_title"), "steps": safe_steps,
                                  "note": "Copied verbatim from the cited SOP. Follow the plant-approved procedure; OpsLens does not operate equipment."},
        "sop": sop_block,
        "verification_criteria": verification,
        "resolution": {"state": state, "acknowledged": acknowledged, "alarm_input_active": input_active,
                       "incident_status": full_event.get("status"),
                       "note": "Acknowledgement is not clearing. The alarm clears only when its source input returns to normal."},
        "escalation": {
            "p1_reached": bool(p1_reached),
            "p1_details": [c["highest_exceeded"] for c in p1_reached if c.get("highest_exceeded")],
            "p1_conditional": [{"tag": c["tag"], "condition": c.get("p1_condition_text"),
                                "source_page": (c.get("highest_exceeded") or {}).get("source_page")} for c in p1_conditional],
            "next_limits": [{"tag": c["tag"], "parameter": c["parameter"], **c["next_limit"]} for c in next_limits],
            "acknowledgement_deadline": deadline,
        },
    }


def _headline(problem: Dict[str, Any], limit_checks: List[Dict[str, Any]], p1_reached: List[Dict[str, Any]],
              p1_conditional: Optional[List[Dict[str, Any]]] = None) -> str:
    parts = [f"{problem.get('alarm_description') or problem.get('alarm_tag')} on {problem.get('equipment')}"]
    focus = None
    for check in limit_checks:
        if check.get("applied") and check.get("highest_exceeded"):
            focus = check
            break
    if focus:
        parts.append(focus["summary"].rstrip("."))
    if p1_reached:
        parts.append("P1 escalation threshold reached per SOP — notify the Plant Manager")
    elif p1_conditional:
        parts.append("Conditional P1 per SOP — verify on site: " + str(p1_conditional[0].get("p1_condition_text") or ""))
    return ". ".join(parts) + "."
