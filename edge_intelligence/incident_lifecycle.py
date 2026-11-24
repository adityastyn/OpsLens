"""Alarm lifecycle of one incident, worked out from records.

ACTIVE_UNACKNOWLEDGED -> ACTIVE_ACKNOWLEDGED -> CONDITION_RECOVERED -> RESOLVED

Acknowledging records that a person has seen the alarm; the process condition is
unchanged. An incident can be resolved only when every criterion below holds, and
the backend enforces this. OpsLens reads these states. It never clears an alarm,
on a simulator or on a PLC: only the source can do that.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import alarm_config

LABELS = {
    "ACTIVE_UNACKNOWLEDGED": "Active, not acknowledged",
    "ACTIVE_ACKNOWLEDGED": "Active, acknowledged",
    "CONDITION_RECOVERED": "Condition recovered",
    "RESOLVED": "Resolved",
}
MESSAGES = {
    "ACTIVE_UNACKNOWLEDGED": "The alarm condition exists and has not been acknowledged.",
    "ACTIVE_ACKNOWLEDGED": "Alarm acknowledged. The process condition remains active.",
    "CONDITION_RECOVERED": "The recovery criteria are met. Confirm in the field, then confirm resolved.",
    "RESOLVED": "An operator confirmed this incident as resolved.",
}
BAD_QUALITY = ("STALE", "INVALID", "BAD")
_FUTURE_CLEAR_SEEN: Dict[str, datetime] = {}


def hold_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("OPSLENS_RECOVERY_HOLD_SECONDS", "10")))
    except ValueError:
        return 10.0


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_true(value: Any) -> bool:
    return value is True or str(value).strip().lower() in ("true", "1", "1.0")


def _criterion(key: str, label: str, passed: bool, detail: str) -> Dict[str, Any]:
    return {"id": key, "label": label, "passed": bool(passed), "detail": detail}


def _clear_since(db, tag: str, first_seen: Optional[str]) -> Optional[str]:
    """When this alarm input last changed to clear, from its recorded samples."""
    since = None
    for row in db.get_readings(tag=tag, start_time=first_seen, limit=20000):
        if _is_true(row.value):
            since = None
        elif since is None:
            since = row.timestamp
    return since


def evaluate(full_event: Dict[str, Any], db, brief: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    from operator_brief import build_operator_brief
    now = now or datetime.now(timezone.utc)
    brief = brief or build_operator_brief(full_event, db)
    event_id = full_event.get("event_id")
    alarms = [a for a in db.get_individual_alarms(event_id) if alarm_config.is_alarm_tag(a.get("tag"))]
    inputs: List[Dict[str, Any]] = []
    for alarm in alarms:
        latest = db.get_latest_reading(alarm["tag"])
        inputs.append({"alarm_id": alarm.get("alarm_id"), "tag": alarm["tag"], "active": bool(latest and _is_true(latest.value)),
                       "observed": latest is not None, "acknowledged": bool(alarm.get("acknowledged_timestamp")) or
                       str(alarm.get("lifecycle_state") or "").upper() == "ACKNOWLEDGED",
                       "acknowledged_at": alarm.get("acknowledged_timestamp"), "first_seen": alarm.get("first_seen_timestamp"),
                       "quality": str(getattr(latest, "data_quality", "") or "").upper(),
                       "data_source": getattr(latest, "data_source", None)})
    active = [i for i in inputs if i["active"]]
    # An alarm that returned to normal unacknowledged still needs acknowledging.
    unacknowledged = [i for i in inputs if not i["acknowledged"]]

    criteria = [_criterion("alarm_inputs_clear", "All alarm inputs have returned to normal", bool(inputs) and not active and all(i["observed"] for i in inputs),
                           f"{len(active)} of {len(inputs)} still active" + (f": {', '.join(i['tag'] for i in active[:4])}" if active else "."))]
    exceeded = [r["label"] for r in brief.get("incident_readings") or [] if r.get("state_now") == "LIMIT EXCEEDED"]
    criteria.append(_criterion("readings_inside_limits", "Readings are back inside their alarm limits", not exceeded,
                               f"Still past a limit: {', '.join(exceeded[:4])}." if exceeded else "No reading is past a configured limit."))
    stopped = []
    for tag in (brief.get("problem") or {}).get("status_tags_are_not_alarms") or []:
        normal = alarm_config.status_normal_value(tag)
        latest = db.get_latest_reading(tag)
        if normal is not None and latest is not None and _is_true(latest.value) != normal:
            stopped.append(tag)
    criteria.append(_criterion("equipment_state_normal", "Equipment is back in its normal state", not stopped,
                               f"Not yet normal: {', '.join(stopped)}." if stopped else "Status inputs match their normal state."))
    clears = [t for t in (_parse(_clear_since(db, i["tag"], i["first_seen"])) for i in inputs) if t]
    latest_clear = max(clears) if clears and not active else None
    # Simulated samples can be dated ahead of the wall clock. A clear that is
    # dated in the future counts from when it was first seen here.
    event_key = str(event_id)
    if latest_clear is None or latest_clear <= now:
        _FUTURE_CLEAR_SEEN.pop(event_key, None)
    else:
        latest_clear = _FUTURE_CLEAR_SEEN.setdefault(event_key, now)
    held = (now - latest_clear).total_seconds() if latest_clear else None
    needed = hold_seconds()
    criteria.append(_criterion("held_clear", f"Stayed clear for {needed:g} s", held is not None and held >= needed,
                               f"Clear for {max(held, 0):.0f} s." if held is not None else "Not clear yet."))
    bad = [i["tag"] for i in inputs if i["quality"] in BAD_QUALITY]
    criteria.append(_criterion("data_quality", "Alarm data is valid", not bad, f"Bad quality: {', '.join(bad)}." if bad else "Latest alarm samples are valid."))

    criteria.append(_criterion("acknowledged", "Every alarm is acknowledged", not unacknowledged,
                               f"{len(unacknowledged)} not acknowledged." if unacknowledged else "All acknowledged."))

    can_resolve = all(c["passed"] for c in criteria)
    if full_event.get("status") == "RESOLVED":
        state, can_resolve = "RESOLVED", False
    elif can_resolve:
        state = "CONDITION_RECOVERED"
    elif unacknowledged or not any(i["acknowledged"] for i in inputs):
        state = "ACTIVE_UNACKNOWLEDGED"
    else:
        state = "ACTIVE_ACKNOWLEDGED"
    label, message = LABELS[state], MESSAGES[state]
    if state.startswith("ACTIVE") and inputs and not active:
        label = "Returned to normal, not acknowledged" if unacknowledged else "Recovering"
        message = ("The alarm inputs are back to normal. Acknowledge them to continue." if unacknowledged
                   else "The alarm inputs are back to normal. Waiting for the recovery criteria.")
    sources = {str(i["data_source"] or "").lower() for i in inputs}
    return {
        "event_id": event_id, "state": state, "label": label, "message": message,
        "can_acknowledge": bool(unacknowledged) and state != "RESOLVED", "can_resolve": can_resolve,
        "can_mark_unresolved": state == "RESOLVED",
        "criteria": criteria, "blocking": [c["label"] for c in criteria if not c["passed"]],
        "alarm_inputs": inputs, "active_count": len(active), "unacknowledged_count": len(unacknowledged),
        "simulated": any("simulat" in s for s in sources), "advisory_only": True,
    }
