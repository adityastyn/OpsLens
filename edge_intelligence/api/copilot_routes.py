"""
OpsLens - Additional read-only routes: PDF-derived alarm configuration,
alarm state summary, operator brief, data-source status and local-LLM health.

All routes are advisory. None of them writes to a PLC or changes equipment.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

import alarm_config

logger = logging.getLogger("opslens.api.copilot")


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def data_source_state(plc_status: Dict[str, Any], simulation_status: Optional[str], latest_source: Optional[str],
                      plc_enabled: bool) -> Dict[str, Any]:
    """Single source of truth for the HMI 'Data source' indicator.

    - SIMULATED: the simulator is the active source (running, or its readings
      are the latest recorded data and no PLC is enabled).
    - LIVE PLC: the OPC UA session is connected and fresh.
    - STALE: the PLC is connected but readings are older than the stale limit.
    - PLC DISCONNECTED: a PLC is enabled/configured but not connected.
    - NO DATA: nothing has been received yet.
    A simulation is never labelled 'PLC DISCONNECTED'.
    """
    latest = str(latest_source or "").lower()
    label = plc_status.get("source_label")
    if label == "LIVE PLC DATA":
        mode, detail = "LIVE PLC", "Read-only OPC UA session connected."
    elif label == "STALE DATA":
        mode, detail = "STALE", "PLC connected but readings are older than the stale limit."
    elif simulation_status in ("running", "recovering") or any(w in latest for w in ("simulat", "synthetic")):
        mode, detail = "SIMULATED", "Synthetic scenario data. Not live PLC telemetry."
    elif plc_enabled:
        mode, detail = "PLC DISCONNECTED", plc_status.get("last_error") or "PLC enabled but no session is connected."
    elif latest:
        mode, detail = "RECORDED", f"Latest readings came from '{latest_source}'."
    else:
        mode, detail = "NO DATA", "No telemetry received yet. Start a scenario or configure the PLC adapter."
    return {
        "mode": mode,
        "detail": detail,
        "simulation_status": simulation_status,
        "plc": {k: plc_status.get(k) for k in ("state", "configured", "connected", "last_read_at", "source_label", "last_error")},
        "plc_enabled": plc_enabled,
        "latest_reading_source": latest_source,
        "read_only": True,
    }


def alarm_state_rows(db, open_event_ids) -> list:
    """ISA-18.2-style state for each alarm input record.

    ACTIVE_UNACK -> ACTIVE_ACK (acknowledged, still active) ->
    RTN_UNACK (returned to normal, not acknowledged) -> NORMAL.
    Acknowledgement never changes the 'active' flag; only the source input does.
    """
    rows = []
    open_ids = set(open_event_ids or [])
    for alarm in db.get_all_individual_alarms():
        tag = alarm.get("tag")
        if not alarm_config.is_alarm_tag(tag):
            continue  # legacy rows created before status/measurement filtering
        latest = db.get_latest_reading(tag)
        input_active = None
        if latest is not None:
            input_active = latest.value is True or str(latest.value).lower() in ("true", "1", "1.0")
        lifecycle = str(alarm.get("lifecycle_state") or "").upper()
        acknowledged = bool(alarm.get("acknowledged_timestamp")) or lifecycle == "ACKNOWLEDGED"
        if lifecycle in ("CLEARED", "RETURNED_TO_NORMAL") and input_active is not True:
            input_active = False
        if input_active is None:
            state = "UNKNOWN"
        elif input_active and acknowledged:
            state = "ACTIVE_ACK"
        elif input_active:
            state = "ACTIVE_UNACK"
        elif acknowledged:
            state = "NORMAL"
        else:
            state = "RTN_UNACK"
        definition = alarm_config.definition_for_tag(tag)
        rows.append({
            "alarm_id": alarm.get("alarm_id"),
            "incident_id": alarm.get("incident_id"),
            "incident_open": alarm.get("incident_id") in open_ids,
            "tag": tag,
            "equipment_id": alarm.get("equipment_id"),
            "state": state,
            "input_active": input_active,
            "acknowledged": acknowledged,
            "first_seen": alarm.get("first_seen_timestamp"),
            "last_seen": alarm.get("last_seen_timestamp"),
            "repeat_count": alarm.get("repeat_count"),
            "source_priority": alarm.get("source_priority") or "NOT SUPPLIED",
            "data_source": getattr(latest, "data_source", None) if latest else None,
            "sop_reference": {
                "sop_id": definition.get("sop_id"), "page": definition.get("source_page"),
                "parameter": definition.get("parameter"),
            } if definition else None,
        })
    order = {"ACTIVE_UNACK": 0, "RTN_UNACK": 1, "ACTIVE_ACK": 2, "UNKNOWN": 3, "NORMAL": 4}
    rows.sort(key=lambda r: (order.get(r["state"], 9), r.get("last_seen") or ""), reverse=False)
    return rows


def register_copilot_routes(router: APIRouter, app, orch, simulation_state: Dict[str, Any]) -> None:

    @router.get("/alarms/reference")
    async def alarm_reference():
        """Alarm limits extracted from SOP-MAN-000 with page citations."""
        return {**alarm_config.summary(), "definitions": alarm_config.definitions()}

    @router.get("/alarms/conflicts")
    async def alarm_conflicts():
        """Differences between the SOP manual and the OpsLens tag registry (reported, not auto-fixed)."""
        conflicts = alarm_config.config_conflicts()
        return {"count": len(conflicts), "conflicts": conflicts,
                "policy": "Neither source is changed automatically. Confirm the site value, then update one source."}

    @router.get("/alarms/limit-check")
    async def alarm_limit_check(tag: str = Query(..., min_length=1), value: Optional[float] = None):
        """Compare a value (or the latest reading) with the manual's limits. Calculated in Python."""
        quality = None
        if value is None:
            latest = orch.db.get_latest_reading(tag)
            if latest is None:
                raise HTTPException(status_code=404, detail=f"No reading recorded for {tag}.")
            value, quality = latest.value, latest.data_quality
        ctx = alarm_config.limit_context(tag, value, data_quality=quality)
        if ctx is None:
            raise HTTPException(status_code=404, detail=f"SOP-MAN-000 has no limit mapped to {tag}.")
        return ctx

    @router.get("/alarms/active")
    async def active_alarm_states():
        """Alarm inputs with ISA-18.2-style states. Acknowledged alarms stay active until the source clears."""
        try:
            open_ids = orch.audit.get_open_events()
            rows = alarm_state_rows(orch.db, open_ids)
        except Exception as exc:
            logger.warning("Active alarm summary unavailable (%s)", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Alarm state summary temporarily unavailable.")
        counts: Dict[str, int] = {}
        for row in rows:
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        return {
            "alarms": rows,
            "counts": counts,
            "active_total": counts.get("ACTIVE_UNACK", 0) + counts.get("ACTIVE_ACK", 0),
            "unacknowledged_total": counts.get("ACTIVE_UNACK", 0) + counts.get("RTN_UNACK", 0),
            "note": "Only configured alarm inputs (ALM-*, *-FAULT, *-TRIP) are alarms. Status tags such as P101-RUN and analog measurements are evidence.",
        }

    @router.get("/events/{event_id}/brief")
    async def operator_brief(event_id: str):
        """Deterministic eight-part operator brief (no LLM)."""
        full_event = orch.get_full_event(event_id)
        if not full_event:
            raise HTTPException(status_code=404, detail="Incident not found.")
        from operator_brief import build_operator_brief
        try:
            # Runs on the event loop: it only reads SQLite, and the shared
            # connection must not be used from a second thread.
            return build_operator_brief(full_event, orch.db)
        except Exception as exc:
            logger.exception("Operator brief failed for %s", event_id)
            raise HTTPException(status_code=500, detail=f"Operator brief could not be built ({type(exc).__name__}).")

    def _diagnosis_facts(event_id: str):
        full_event = orch.get_full_event(event_id)
        if not full_event:
            raise HTTPException(status_code=404, detail="Incident not found.")
        from ai_diagnosis import build_facts
        # Facts are read from SQLite on the event loop; only the model call
        # runs in a worker thread.
        return build_facts(full_event, orch.db)

    @router.get("/events/{event_id}/diagnosis")
    async def incident_diagnosis(event_id: str, refresh: bool = False):
        """Seven-section diagnosis, labelled with its true source. refresh=true regenerates."""
        facts = _diagnosis_facts(event_id)
        from ai_diagnosis import stream_diagnosis_from_facts

        def run():
            final = {}
            for item in stream_diagnosis_from_facts(facts, db=orch.db, force=refresh):
                if item["type"] == "done":
                    final = item
            return final
        return await asyncio.to_thread(run)

    @router.get("/events/{event_id}/diagnosis/stream")
    async def incident_diagnosis_stream(event_id: str, refresh: bool = False):
        """Server-sent events: meta, status, token (unvalidated draft), done (validated result).

        The generation runs in its own job, so it continues if this connection
        drops; a later request for the same context re-attaches to it."""
        import json as _json
        from fastapi.responses import StreamingResponse
        facts = _diagnosis_facts(event_id)
        from ai_diagnosis import stream_diagnosis_from_facts

        def generate():
            try:
                for item in stream_diagnosis_from_facts(facts, db=orch.db, force=refresh, stream_keepalive=True):
                    if item["type"] == "keepalive":
                        yield ": keepalive\n\n"
                        continue
                    yield f"event: {item['type']}\ndata: {_json.dumps(item, default=str)}\n\n"
            except Exception as exc:  # never leave the client hanging
                logger.exception("Diagnosis stream failed for %s", event_id)
                yield f"event: error\ndata: {_json.dumps({'detail': f'{type(exc).__name__}: {exc}'})}\n\n"
        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.get("/events/{event_id}/trends")
    async def incident_trends(event_id: str, points: int = Query(48, ge=8, le=200)):
        """Recorded readings for this incident's own analog tags, with normal
        band and SOP limits from the incident map. Read-only."""
        full_event = orch.get_full_event(event_id)
        if not full_event:
            raise HTTPException(status_code=404, detail="Incident not found.")
        import incident_catalog
        from datetime import timedelta
        cand = full_event.get("candidate") or {}
        spec = incident_catalog.scenario_for_incident(full_event.get("primary_tag"), cand.get("clustered_tags"))
        trigger = full_event.get("trigger_timestamp")
        try:
            t0 = datetime.fromisoformat(str(trigger).replace("Z", "+00:00"))
        except ValueError:
            t0 = None
        start = (t0 - timedelta(seconds=240)).isoformat().replace("+00:00", "Z") if t0 else None
        signals = list(spec.signals) if spec else []
        if not spec:
            from tag_registry import TAG_REGISTRY
            for tag in cand.get("clustered_tags") or []:
                entry = TAG_REGISTRY.get(tag)
                if entry and entry.unit not in ("bool", "status") and not tag.startswith("ALM-"):
                    signals.append(type("S", (), {"tag": tag, "label": entry.description, "unit": entry.unit,
                                                  "equipment_id": entry.equipment_id, "normal": None,
                                                  "normal_text": None, "limits": ()})())
        out = []
        for sig in signals:
            rows = orch.db.get_readings(tag=sig.tag, start_time=start, limit=2000)
            series = [(r.timestamp, float(r.value)) for r in rows if isinstance(r.value, (int, float)) and not isinstance(r.value, bool)]
            if len(series) > points:
                step = len(series) / points
                series = [series[int(i * step)] for i in range(points - 1)] + [series[-1]]
            latest = series[-1][1] if series else None
            beyond = None
            for lim in sig.limits or ():
                if latest is not None and ((lim.direction == "high" and latest >= lim.value) or (lim.direction == "low" and latest <= lim.value)):
                    beyond = lim.label
            normal = list(sig.normal) if isinstance(getattr(sig, "normal", None), tuple) else None
            out.append({
                "tag": sig.tag, "label": sig.label, "unit": sig.unit, "equipment_id": sig.equipment_id,
                "normal": normal, "normal_text": getattr(sig, "normal_text", None),
                "limits": [{"value": l.value, "direction": l.direction, "label": l.label} for l in (sig.limits or ())],
                "points": [{"t": t, "v": round(v, 3)} for t, v in series],
                "latest": latest, "beyond_limit": beyond,
                "data_source": (rows[-1].data_source if rows else None),
            })
        return {"event_id": event_id, "scenario_id": spec.scenario_id if spec else None,
                "trigger_timestamp": trigger, "signals": out,
                "note": "Recorded values only. Limits from SOP-MAN-000 (draft, not approved)."}

    @router.get("/plant/overview")
    async def plant_overview():
        """Monitoring view: latest recorded value of every plant tag, grouped
        by equipment, with its normal band. Not an incident. Read-only."""
        import incident_catalog
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        stale_after = float(os.environ.get("OPSLENS_STALE_AFTER_SECONDS", "120"))
        groups: Dict[str, Any] = {eq: [] for eq in incident_catalog.PLANT_EQUIPMENT}
        for tag, sig in incident_catalog.plant_signals().items():
            row = orch.db.get_latest_reading(tag)
            age = None
            if row is not None:
                try:
                    age = (now - datetime.fromisoformat(str(row.timestamp).replace("Z", "+00:00"))).total_seconds()
                except ValueError:
                    age = None
            value = row.value if row is not None else None
            beyond = None
            for lim in sig.limits:
                if isinstance(value, (int, float)) and not isinstance(value, bool) and (
                        (lim.direction == "high" and value >= lim.value) or (lim.direction == "low" and value <= lim.value)):
                    beyond = lim.label
            groups.setdefault(sig.equipment_id, []).append({
                "tag": tag, "label": sig.label, "unit": sig.unit, "normal_text": sig.normal_text,
                "value": value, "timestamp": row.timestamp if row is not None else None,
                "data_source": row.data_source if row is not None else None,
                "stale": age is None or age > stale_after, "beyond_limit": beyond,
                "outside_normal": isinstance(value, (int, float)) and not isinstance(value, bool) and (
                    (sig.normal[0] is not None and value < sig.normal[0]) or (sig.normal[1] is not None and value > sig.normal[1])),
            })
        return {"generated_at": now.isoformat().replace("+00:00", "Z"), "stale_after_seconds": stale_after,
                "equipment": [{"equipment_id": eq, "signals": sigs} for eq, sigs in groups.items() if sigs],
                "note": "Monitoring view. Values are the latest recorded readings; SIMULATED when produced by the Scenario Lab."}

    # ------------------------------------------------------------- analytics
    def _tag_limits(tag: str):
        import incident_catalog
        signal = incident_catalog.plant_signals().get(tag)
        if signal is None:
            return None, None, []
        band = signal.noise_band or signal.normal
        normal = list(band) if band[0] is not None or band[1] is not None else None
        limits = [{"value": l.value, "direction": l.direction, "label": l.label} for l in signal.limits]
        return signal, normal, limits

    def _series_start(tag: str, minutes: Optional[float], start: Optional[str]) -> Optional[str]:
        """Windows are measured back from the tag's newest record, not the wall
        clock, so an idle simulation still shows its data instead of nothing."""
        if start or not minutes:
            return start
        import analytics
        from datetime import timedelta
        latest = orch.db.get_latest_reading(tag)
        moment = analytics.parse_time(latest.timestamp) if latest else None
        return analytics.iso(moment - timedelta(minutes=minutes)) if moment else None

    @router.get("/telemetry/tags")
    async def telemetry_tags():
        """Tags that can be charted, with how much data is really recorded."""
        import incident_catalog
        rows = {r[0]: r for r in orch.db._get_connection().execute(
            "SELECT tag, COUNT(*), MIN(timestamp), MAX(timestamp) FROM tag_readings GROUP BY tag").fetchall()}
        tags = []
        for tag, signal in incident_catalog.plant_signals().items():
            row = rows.get(tag)
            tags.append({"tag": tag, "label": signal.label, "unit": signal.unit, "equipment_id": signal.equipment_id,
                         "normal_text": signal.normal_text, "samples": row[1] if row else 0,
                         "first": row[2] if row else None, "last": row[3] if row else None})
        return {"tags": tags}

    @router.get("/telemetry/series")
    async def telemetry_series(tag: str = Query(..., min_length=1), minutes: Optional[float] = Query(30, ge=0.5, le=100000),
                               start: Optional[str] = None, end: Optional[str] = None,
                               max_points: int = Query(600, ge=50, le=3000)):
        """Recorded readings of one tag with its bands, anomaly markers and data gaps."""
        import analytics
        signal, normal, limits = _tag_limits(tag)
        # Newest first, then put back in time order: on a long range the cap
        # must drop the oldest readings, never the most recent ones.
        rows = orch.db.get_readings(tag=tag, start_time=_series_start(tag, minutes, start), end_time=end, limit=20000, order_desc=True)[::-1]
        points = analytics.numeric_points(rows)
        result = {"tag": tag, "label": signal.label if signal else tag, "unit": signal.unit if signal else None,
                  "equipment_id": signal.equipment_id if signal else None, "normal": normal,
                  "normal_text": signal.normal_text if signal else None, "limits": limits,
                  "available": bool(points), "message": None if points else f"No recorded readings for {tag} in this period."}
        if not points:
            return {**result, "points": [], "anomalies": [], "gaps": [], "data_sources": [], "summary": None}
        anomalies = analytics.detect_anomalies([p["v"] for p in points], tuple(normal) if normal else (None, None))
        flagged = {a["index"] for a in anomalies}
        for item in anomalies:
            points[item["index"]]["anomaly"] = item["reasons"]
        shown = analytics.downsample(points, max_points, keep=sorted(flagged)[:300])
        return {**result, "points": analytics.strip_internal(shown), "total_samples": len(points),
                "anomalies": [{"t": points[a["index"]]["t"], "v": points[a["index"]]["v"], "reasons": a["reasons"],
                               "robust_z": a["robust_z"]} for a in anomalies],
                "anomaly_method": f"Rolling median/MAD (window {analytics.ANOMALY_WINDOW}, z at least {analytics.ANOMALY_Z}), plus where a reading leaves its normal range",
                "gaps": analytics.find_gaps(points), "sample_interval_seconds": analytics.sample_interval(points),
                "data_sources": sorted({str(p["source"]) for p in points}), "summary": analytics.summarize(points)}

    def _incident_periods(full_event: Dict[str, Any]):
        """(start of incident, first clear reading of its primary alarm or None)."""
        import analytics
        primary = full_event.get("primary_tag")
        trigger = full_event.get("trigger_timestamp")
        steps = (full_event.get("story") or {}).get("steps") or []
        first = min([s.get("timestamp") for s in steps if s.get("timestamp")] + ([trigger] if trigger else []), default=trigger)
        cleared = None
        for row in orch.db.get_readings(tag=primary, start_time=trigger, limit=20000):
            if row.value is False or str(row.value).lower() in ("false", "0", "0.0"):
                cleared = row.timestamp
                break
        return first, cleared

    @router.get("/telemetry/compare")
    async def telemetry_compare(event_id: str = Query(..., min_length=1), tag: str = Query(..., min_length=1)):
        """Before-fix and after-fix statistics of one tag for one incident, from records."""
        import analytics
        full_event = orch.get_full_event(event_id)
        if not full_event:
            raise HTTPException(status_code=404, detail="Incident not found.")
        first, cleared = _incident_periods(full_event)
        during = analytics.numeric_points(orch.db.get_readings(tag=tag, start_time=first, end_time=cleared, limit=20000))
        after = analytics.numeric_points(orch.db.get_readings(tag=tag, start_time=cleared, limit=20000)) if cleared else []
        return {"event_id": event_id, "tag": tag, "incident_start": first, "alarm_cleared_at": cleared,
                "before_fix": analytics.summarize(during), "after_fix": analytics.summarize(after),
                "message": None if cleared else "The primary alarm has not cleared, so there is no after-fix period yet."}

    @router.get("/analytics/forecast")
    async def analytics_forecast(tag: str = Query(..., min_length=1), horizon_seconds: float = Query(30, ge=4, le=3600)):
        """Short-term prediction for one tag, with its backtest. Not a measurement or a diagnosis."""
        import analytics
        _, _, limits = _tag_limits(tag)
        rows = orch.db.get_readings(tag=tag, limit=400, order_desc=True)
        return {"tag": tag, **analytics.forecast_series(analytics.numeric_points(rows), horizon_seconds, limits)}

    @router.get("/analytics/warnings")
    async def analytics_warnings(horizon_seconds: float = Query(60, ge=10, le=3600)):
        """Readings predicted to reach a limit. Skips tags already past it and
        tags with no fresh data, so a held value cannot raise a warning."""
        import analytics
        import incident_catalog
        warnings, breaches = [], []
        for tag, signal in incident_catalog.plant_signals().items():
            if not signal.limits:
                continue
            _, _, limits = _tag_limits(tag)
            points = analytics.numeric_points(orch.db.get_readings(tag=tag, limit=100, order_desc=True))
            if len(points) < analytics.MIN_FORECAST_POINTS or max(p["v"] for p in points[-10:]) == min(p["v"] for p in points[-10:]):
                continue
            result = analytics.forecast_series(points, horizon_seconds, limits)
            state = (result.get("limit_state") or {}).get("state")
            if state in ("ACTIVE_BREACH", "ACTIVE_BREACH_WORSENING"):
                # Already past a limit: a measured breach, never an early warning.
                breaches.append({"tag": tag, "label": signal.label, "unit": signal.unit, "kind": "MEASURED",
                                 "last_measured": result["last_measured"], "limit_state": result["limit_state"]})
            elif result.get("available") and result.get("crossing"):
                warnings.append({"tag": tag, "label": signal.label, "unit": signal.unit, "kind": "PREDICTION",
                                 "last_measured": result["last_measured"], "crossing": result["crossing"],
                                 "limit_state": result["limit_state"], "backtest": result["backtest"]})
        return {"warnings": warnings, "active_breaches": breaches, "horizon_seconds": horizon_seconds,
                "method": "Statistical linear trend; not a machine-learning model",
                "note": "Early warnings are predictions from recorded trends for readings still within their limits. "
                        "Readings already past a limit are listed as active breaches, not warnings. Not alarms and not a diagnosis."}

    @router.get("/knowledge-graph")
    async def knowledge_graph(event_id: Optional[str] = None):
        """Plant knowledge graph generated from the catalog and an incident's records."""
        from knowledge_graph import build_plant_graph
        full_event = None
        if event_id:
            full_event = orch.get_full_event(event_id)
            if not full_event:
                raise HTTPException(status_code=404, detail="Incident not found.")
            full_event["individual_alarms"] = orch.db.get_individual_alarms(event_id)
        return await asyncio.to_thread(build_plant_graph, full_event, orch.db if full_event else None)

    @router.get("/events/{event_id}/insight")
    async def event_insight(event_id: str):
        """Bottom line (likely cause, top two SOP steps, history), a rule-based
        confidence rating with flags, and pre-alarm activity. No model call."""
        import incident_insight
        full = orch.get_full_event(event_id)
        if full is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        return await asyncio.to_thread(incident_insight.build_insight, full, orch.db)

    @router.get("/events/{event_id}/lifecycle")
    async def incident_lifecycle(event_id: str):
        """Alarm lifecycle state and the recovery criteria, evaluated from records."""
        full_event = orch.get_full_event(event_id)
        if not full_event:
            raise HTTPException(status_code=404, detail="Incident not found.")
        import incident_lifecycle as lifecycle
        return lifecycle.evaluate(full_event, orch.db)

    @router.get("/events/{event_id}/timeline")
    async def incident_timeline(event_id: str):
        """Complete recorded history of the incident: observations, alarm states, operator actions."""
        full_event = orch.get_full_event(event_id)
        if not full_event:
            raise HTTPException(status_code=404, detail="Incident not found.")
        from incident_views import build_timeline
        return build_timeline(full_event, orch.db, getattr(getattr(orch, "correlator", None), "topology", None))

    @router.get("/events/{event_id}/evidence-detail")
    async def incident_evidence(event_id: str):
        """Ranked evidence and hypotheses with what supports and contradicts each."""
        full_event = orch.get_full_event(event_id)
        if not full_event:
            raise HTTPException(status_code=404, detail="Incident not found.")
        from incident_views import build_evidence
        return build_evidence(full_event, orch.db)

    @router.get("/events/{event_id}/inspection")
    async def incident_inspection(event_id: str):
        """Engineering Console only: the readings, breaches, alarms, timeline and
        candidate ranking the analysis worked from, with the scenario's test
        reference kept separate. Not part of the operator HMI."""
        full_event = orch.get_full_event(event_id)
        if not full_event:
            raise HTTPException(status_code=404, detail="Incident not found.")
        from incident_views import build_inspection
        return await asyncio.to_thread(build_inspection, full_event, orch.db)

    @router.get("/history/seeded")
    async def seeded_history_status():
        """Seeded past-month history: how many simulated past incidents, and
        whether seeding is running. It is never live plant data."""
        import seed_history
        from pathlib import Path as _P
        import os as _os
        enabled = (_os.getenv("OPSLENS_SEED_HISTORY", "true").strip().lower() in ("1", "true", "yes"))
        return {"incidents": seed_history.seeded_count(_P(orch.db.db_path)), "run": seed_history.run_state(), "enabled_on_start": enabled,
                "label": seed_history.HISTORY_SOURCE, "days": seed_history.DAYS,
                "retention": "Kept until the database is reset or the seeded history is removed; OpsLens never deletes records on its own."}

    @router.post("/history/seed")
    async def seed_history_now():
        import seed_history
        from pathlib import Path as _P
        return seed_history.start_background(_P(orch.db.db_path))

    @router.delete("/history/seeded")
    async def remove_seeded_history():
        import seed_history
        from pathlib import Path as _P
        if seed_history.run_state().get("status") == "running":
            raise HTTPException(status_code=409, detail="Seeding is still running.")
        removed = await asyncio.to_thread(seed_history.remove, _P(orch.db.db_path))
        seed_history._RUN.update(status="idle", detail=f"Removed {removed} seeded past incidents.")
        return {"removed": removed}

    @router.get("/sop-search/status")
    async def sop_search_status():
        """Which SOP search will be tried first: Qdrant (if running, with the
        plant SOPs indexed), else BM25. The alarm-tag link checks either."""
        import plant_sop_index
        try:
            from sop_retriever import get_qdrant_store
            health = get_qdrant_store().health()
        except Exception as error:  # noqa: BLE001
            health = {"connected": False, "error": type(error).__name__}
        try:
            from sop_retriever import get_embed_model
            get_embed_model()
            embedding = {"available": True, "model": "all-MiniLM-L6-v2"}
        except Exception as error:  # noqa: BLE001
            embedding = {"available": False, "error": f"{type(error).__name__}: {error}"[:200]}
        first = "qdrant_vector_search" if health.get("connected") and embedding["available"] else "bm25"
        return {"search_order": ["qdrant_vector_search", "bm25", "alarm_tag_link"], "first_available": first,
                "qdrant": health, "embedding_model": embedding, "plant_sop_index": plant_sop_index.index_state(),
                "plant_sops": plant_sop_index.plant_sop_ids()}

    @router.post("/incidents/{event_id}/acknowledge")
    async def acknowledge_incident(event_id: str, operator: str = Query(..., min_length=1), notes: Optional[str] = None):
        """Acknowledge every unacknowledged alarm input of this incident.
        Records who and when. Active alarms stay active; nothing is sent to a PLC."""
        full_event = orch.get_full_event(event_id)
        if not full_event:
            raise HTTPException(status_code=404, detail="Incident not found.")
        import incident_lifecycle as lifecycle
        before = lifecycle.evaluate(full_event, orch.db)
        pending = [i for i in before["alarm_inputs"] if not i["acknowledged"]]
        if not pending:
            raise HTTPException(status_code=409, detail="There is no unacknowledged alarm on this incident.")
        for item in pending:
            try:
                orch.db.record_alarm_acknowledgement(event_id=event_id, alarm_id=item["alarm_id"], operator=operator,
                                                     notes=notes, timestamp=None)
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
        return {"event_id": event_id, "acknowledged": [i["tag"] for i in pending], "operator": operator,
                "message": lifecycle.MESSAGES["ACTIVE_ACKNOWLEDGED"] if before["active_count"] else "Alarm acknowledged. The alarm inputs are back to normal.",
                "writes_to_plc": False, "lifecycle": lifecycle.evaluate(orch.get_full_event(event_id), orch.db)}

    @router.get("/events/{event_id}/retrieval")
    async def incident_retrieval(event_id: str):
        """What SOP retrieval did for this incident: for the engineer's view."""
        full_event = orch.get_full_event(event_id)
        if not full_event:
            raise HTTPException(status_code=404, detail="Incident not found.")
        sop = full_event.get("sop_match") or {}
        from ai_diagnosis import _uploaded_passages
        from operator_brief import build_operator_brief
        from sop_context import build_sop_context
        context = build_sop_context(full_event, build_operator_brief(full_event, orch.db))
        chunks = [{k: c.get(k) for k in ("sop_id", "page", "section", "section_title", "approval_status", "approved")}
                  | {"text": str(c.get("text") or "")[:400]} for c in (sop.get("retrieved_chunks") or [])]
        return {"sop_id": sop.get("sop_id"), "title": sop.get("sop_title"), "retrieval_method": sop.get("retrieval_method"),
                "retrieval_status": sop.get("retrieval_status"), "retrieval_state": sop.get("retrieval_state"),
                "query_context": sop.get("query_context"), "why_retrieved": sop.get("why_retrieved"),
                "chunks": chunks, "sop_context": context,
                "uploaded_passages": _uploaded_passages(full_event, {"sop_id": sop.get("sop_id")})}

    @router.get("/datasource")
    async def datasource():
        plc = app.state.opcua_adapter.status()
        latest_source = None
        try:
            row = orch.db._get_connection().execute(
                "SELECT data_source FROM tag_readings ORDER BY id DESC LIMIT 1").fetchone()
            latest_source = row[0] if row else None
        except Exception:
            latest_source = None
        enabled = str(os.environ.get("PLC_OPCUA_ENABLED", "false")).lower() in ("1", "true", "yes")
        return data_source_state(plc, simulation_state.get("status"), latest_source, enabled)

    @router.get("/copilot/ai-status")
    async def ai_status():
        """AI status for the status pill: Groq, or the local model in LM Studio.
        The app works without either (deterministic answers)."""
        from edge_slm import first_token_timeout, local_health, local_llm_timeout
        from llm_provider import provider_status
        health = await asyncio.to_thread(local_health)
        provider = provider_status()
        selected = provider.get("provider") == "lmstudio"
        import ai_diagnosis
        groq = {"groq_selected": ai_diagnosis.groq_enabled(), "groq_model": ai_diagnosis.groq_model(),
                "groq_key_set": __import__("groq_keys").POOL.count() > 0,
                "groq_keys": __import__("groq_keys").POOL.status()}
        return {
            **health, **groq,
            "selected_provider": provider.get("provider"),
            "local_selected": selected,
            "timeout_seconds": local_llm_timeout(),
            "first_token_timeout_seconds": first_token_timeout(),
            "verified_in_this_process": provider.get("verified"),
            "last_diagnosis_call": __import__("ai_diagnosis").LAST_CALL,
            "fallback": "deterministic record-grounded answers and verbatim SOP steps",
            "status_label": (
                "AI: deterministic (no LLM selected)" if not selected else
                "AI: LM Studio ready" if health["reachable"] and health["model_installed"] else
                "AI: LM Studio model missing" if health["reachable"] else
                "AI: LM Studio unavailable — deterministic fallback"
            ),
        }

    @router.post("/copilot/ai-status/warmup")
    async def local_warmup():
        """Load the local model in LM Studio so the first question is not a cold start."""
        from edge_slm import warm_up_local
        return await asyncio.to_thread(warm_up_local)

    @router.get("/diagnostics")
    async def diagnostics():
        """One-call health summary for the troubleshooting guide."""
        from llm_provider import provider_status
        import importlib.util
        sentence = importlib.util.find_spec("sentence_transformers") is not None
        try:
            from sop_retriever import _QDRANT_STORE
            qdrant_connected = bool(_QDRANT_STORE and _QDRANT_STORE.health().get("connected"))
        except Exception:
            qdrant_connected = False
        counts = {}
        try:
            conn = orch.db._get_connection()
            for table in ("tag_readings", "candidate_events", "individual_alarms", "operator_actions", "guidance"):
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except Exception as exc:
            counts["error"] = type(exc).__name__
        return {
            "backend": "ONLINE",
            "database": str(getattr(orch.db, "db_path", "")),
            "row_counts": counts,
            "simulation": {k: v for k, v in simulation_state.items() if k != "generator"},
            "retrieval": {
                "sentence_transformers_installed": sentence,
                "qdrant_connected": qdrant_connected,
                "active_method": "qdrant_vector_search" if (sentence and qdrant_connected) else "bm25_keyword (fallback)",
                "curated_manual_sections": "bundled_pdf_section (SOP-MAN-000 page ranges)",
            },
            "llm": provider_status(),
            "alarm_reference": alarm_config.summary(),
            "plc": app.state.opcua_adapter.status(),
            "advisory_only": True,
            "writes_to_plc": False,
        }
