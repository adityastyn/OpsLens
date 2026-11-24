"""
OpsLens - Alarm Urgency Engine (Phase 2)

Deterministic, configurable urgency scoring without ML.
Formula: U = 0.35*D + 0.25*R + 0.20*E + 0.10*P + 0.10*Q
Bands: URGENT (80-100), ELEVATED (50-79), ROUTINE (0-49), UNKNOWN

Hard rules override the weighted score entirely:
  HARD_RULE_SOURCE_CRITICAL       - source priority = CRITICAL
  HARD_RULE_TRIP_LIMIT_BREACH     - confirmed trip threshold breach
  HARD_RULE_TIME_TO_LIMIT_CRITICAL- TTL <= 30 seconds
  HARD_RULE_COMBINED_CONDITIONS   - 3+ concurrent deviations > 30%
  HARD_RULE_DATA_UNCERTAINTY      - data quality = INVALID
"""

import math
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

from models import (
    TagReading, TagRegistryEntry, CandidateEvent, UrgencyAssessment,
    TimeToLimitEstimate, current_iso_utc,
)

logger = logging.getLogger("opslens.alarm_urgency")


# ---------------------------------------------------------------------------
# Telemetry validation
# ---------------------------------------------------------------------------

MAX_STALENESS_SECONDS = 90.0


def validate_telemetry(
    reading: TagReading,
    entry: TagRegistryEntry,
    reference_time: Optional[datetime] = None,
    max_staleness_seconds: float = MAX_STALENESS_SECONDS,
) -> Tuple[bool, str, Optional[str]]:
    """
    Validate a single TagReading against its registry definition.

    Returns (is_valid: bool, quality_status: str, issue_reason: Optional[str])
    quality_status is one of: VALID, DEGRADED, STALE, INVALID
    """
    if reference_time is None:
        reference_time = datetime.now(timezone.utc)

    # 1. Missing value
    if reading.value is None:
        return False, "INVALID", "reading value is None"

    # 2. For float tags, value must be numeric and finite
    if entry.data_type == "float":
        try:
            fval = float(reading.value)
        except (TypeError, ValueError):
            return False, "INVALID", f"non-numeric value for float tag: {reading.value!r}"
        if not math.isfinite(fval):
            return False, "INVALID", f"non-finite value for float tag: {fval}"

    # 3. Freshness check
    try:
        ts_str = reading.timestamp.replace("Z", "+00:00")
        ts_dt = datetime.fromisoformat(ts_str)
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        age_seconds = (reference_time - ts_dt).total_seconds()
        if age_seconds > max_staleness_seconds:
            return False, "STALE", f"reading is {age_seconds:.0f}s old (max {max_staleness_seconds:.0f}s)"
    except Exception as exc:
        return False, "DEGRADED", f"cannot parse timestamp: {exc}"

    # 4. Range check against registry baseline (degraded, not invalid)
    if entry.data_type == "float":
        fval = float(reading.value)
        range_span = entry.baseline_max - entry.baseline_min
        # Allow up to 5x the baseline range before marking as suspicious
        lo_guard = entry.baseline_min - 5.0 * range_span
        hi_guard = entry.baseline_max + 5.0 * range_span
        if fval < lo_guard or fval > hi_guard:
            return True, "DEGRADED", f"value {fval} is outside sanity range [{lo_guard:.1f}, {hi_guard:.1f}]"

    return True, "VALID", None


# ---------------------------------------------------------------------------
# Time-to-limit estimation
# ---------------------------------------------------------------------------

def estimate_time_to_limit_with_reason(
    readings: List[TagReading],
    target_limit: float,
    direction: str = "auto",
    window_seconds: float = 60.0,
    reference_time: Optional[datetime] = None,
) -> Tuple[Optional[TimeToLimitEstimate], Optional[str]]:
    """
    Direction-aware linear rate estimator.

    direction: "rising" | "falling" | "auto"
    Returns (estimate: Optional[TimeToLimitEstimate], unavailable_reason: Optional[str]).
    """
    if reference_time is None:
        reference_time = datetime.now(timezone.utc)

    if len(readings) < 2:
        return None, f"Insufficient reading history (found {len(readings)}, required >= 2)"

    # Collect (time_seconds_from_now, value) within window
    points: List[Tuple[float, float]] = []
    for r in readings:
        if not isinstance(r.value, (int, float)):
            continue
        try:
            ts_str = r.timestamp.replace("Z", "+00:00")
            ts_dt = datetime.fromisoformat(ts_str)
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            age = (reference_time - ts_dt).total_seconds()
        except Exception:
            continue
        if 0 <= age <= window_seconds:
            points.append((age, float(r.value)))

    if len(points) < 2:
        return None, f"Insufficient telemetry points within the active {window_seconds:.0f}s window (found {len(points)}, required >= 2)"

    # Sort by age ascending (oldest last in time = largest age)
    points.sort(key=lambda p: p[0], reverse=True)  # oldest first

    # Determine direction from data if auto
    oldest_val = points[0][1]
    newest_val = points[-1][1]
    current_val = newest_val

    if direction == "auto":
        direction = "rising" if newest_val >= oldest_val else "falling"

    # Already breached?
    if direction == "rising" and current_val >= target_limit:
        return None, f"Target trip limit already reached or breached ({current_val:.2f} >= {target_limit:.2f})"
    if direction == "falling" and current_val <= target_limit:
        return None, f"Target trip limit already reached or breached ({current_val:.2f} <= {target_limit:.2f})"

    # Compute linear rate via simple linear regression
    n = len(points)
    # x = seconds before reference_time (age), y = value
    ages = [p[0] for p in points]
    vals = [p[1] for p in points]
    mean_x = sum(ages) / n
    mean_y = sum(vals) / n
    denom = sum((x - mean_x) ** 2 for x in ages)
    if abs(denom) < 1e-9:
        return None, "Telemetry samples have identical timestamps (zero time delta)"

    # slope in value/second (but x is age, so positive age means older)
    # rate of change in time = -slope_wrt_age
    slope_age = sum((ages[i] - mean_x) * (vals[i] - mean_y) for i in range(n)) / denom
    rate_per_second = -slope_age  # positive = value rising with time

    # Validate direction vs rate
    if direction == "rising" and rate_per_second <= 0:
        return None, f"Trend direction is not approaching high trip limit (rate: {rate_per_second:+.4f}/s)"
    if direction == "falling" and rate_per_second >= 0:
        return None, f"Trend direction is not approaching low trip limit (rate: {rate_per_second:+.4f}/s)"

    remaining = abs(target_limit - current_val)
    abs_rate = abs(rate_per_second)
    if abs_rate < 1e-9:
        return None, "Process rate of change is steady (rate near zero)"

    ttl_seconds = remaining / abs_rate

    # Confidence based on number of points and R² proxy
    if n >= 6:
        confidence = "HIGH"
    elif n >= 3:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    estimate = TimeToLimitEstimate(
        time_to_limit_seconds=round(ttl_seconds, 1),
        target_limit=target_limit,
        current_value=current_val,
        rate_per_second=round(rate_per_second, 6),
        direction=direction,
        confidence=confidence,
        assumptions=f"Linear trend over {len(points)} readings in last {window_seconds:.0f}s; advisory only",
        timestamp=current_iso_utc(),
    )
    return estimate, None


def estimate_time_to_limit(
    readings: List[TagReading],
    target_limit: float,
    direction: str = "auto",
    window_seconds: float = 60.0,
    reference_time: Optional[datetime] = None,
) -> Optional[TimeToLimitEstimate]:
    """
    Direction-aware linear rate estimator.

    direction: "rising" | "falling" | "auto"
    Returns None when rate is zero / wrong direction / already breached / stale data.
    """
    est, _ = estimate_time_to_limit_with_reason(
        readings, target_limit, direction, window_seconds, reference_time
    )
    return est

    return TimeToLimitEstimate(
        time_to_limit_seconds=round(ttl_seconds, 1),
        target_limit=target_limit,
        current_value=current_val,
        rate_per_second=round(rate_per_second, 6),
        direction=direction,
        confidence=confidence,
        assumptions=f"Linear trend over {len(points)} readings in last {window_seconds:.0f}s; advisory only",
        timestamp=current_iso_utc(),
    )


# ---------------------------------------------------------------------------
# Urgency evaluation
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "w_deviation": 0.35,
    "w_rate": 0.25,
    "w_evidence": 0.20,
    "w_persistence": 0.10,
    "w_quality": 0.10,
    "urgent_threshold": 80.0,
    "elevated_threshold": 50.0,
    "ttl_critical_seconds": 30.0,
    "combined_conditions_min": 3,
    "combined_deviation_pct": 30.0,
}


def evaluate_urgency(
    candidate: CandidateEvent,
    readings_history: List[TagReading],
    correlated_deviations: Optional[List[Dict[str, Any]]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> UrgencyAssessment:
    """
    Evaluate provisional urgency for a CandidateEvent.

    Returns UrgencyAssessment with urgency_score, urgency_band, triggered_rules,
    urgency_reasons, data_quality, time_to_limit, and scoring_breakdown.
    """
    cfg = {**_DEFAULT_CONFIG, **(config or {})}
    reasons: List[str] = []
    triggered_rules: List[str] = []
    data_quality = "VALID"

    # --- Registry lookup ---
    try:
        from registry_loader import get_registry_loader
        loader = get_registry_loader()
        entry = loader.get_tag(candidate.primary_tag)
    except Exception:
        entry = None

    # --- Telemetry validation ---
    now = datetime.now(timezone.utc)
    primary_readings = [r for r in readings_history if r.tag == candidate.primary_tag]
    latest_reading = max(primary_readings, key=lambda r: r.timestamp, default=None) if primary_readings else None

    if latest_reading is None or entry is None:
        data_quality = "INVALID"
        triggered_rules.append("HARD_RULE_DATA_UNCERTAINTY")
        reasons.append("No valid reading or registry entry available for primary tag")
        return UrgencyAssessment(
            source_priority=candidate.source_priority,
            urgency_score=None,
            urgency_band="UNKNOWN",
            urgency_reasons=reasons,
            triggered_rules=triggered_rules,
            data_quality=data_quality,
            time_to_limit=None,
            scoring_breakdown={},
        )

    is_valid, quality, issue = validate_telemetry(latest_reading, entry, reference_time=now)
    data_quality = quality
    if not is_valid:
        if quality == "STALE":
            triggered_rules.append("HARD_RULE_DATA_UNCERTAINTY")
            reasons.append(f"Primary reading is stale: {issue}")
        elif quality == "INVALID":
            triggered_rules.append("HARD_RULE_DATA_UNCERTAINTY")
            reasons.append(f"Primary reading is invalid: {issue}")
    elif quality == "DEGRADED":
        reasons.append(f"Data quality degraded: {issue}")

    # --- D: Deviation component (0-100) ---
    dev_pct = abs(candidate.raw_deviation_pct)
    D = min(100.0, dev_pct)

    # --- R: Rate-of-change component (0-100) ---
    R = 0.0
    ttl_estimate: Optional[TimeToLimitEstimate] = None
    ttl_unavailable_reason: Optional[str] = None
    if entry and entry.trip_threshold is not None:
        ttl_estimate, ttl_unavailable_reason = estimate_time_to_limit_with_reason(
            primary_readings,
            target_limit=entry.trip_threshold,
            reference_time=now,
        )
        if ttl_estimate and ttl_estimate.time_to_limit_seconds is not None:
            ttl = ttl_estimate.time_to_limit_seconds
            # Map TTL to rate score: 0s → 100, 300s → 0
            R = max(0.0, min(100.0, (300.0 - ttl) / 300.0 * 100.0))
        elif ttl_unavailable_reason:
            reasons.append(f"TTL unavailable: {ttl_unavailable_reason}")
    else:
        ttl_unavailable_reason = "No trip threshold configured in registry for primary tag"
        reasons.append(f"TTL unavailable: {ttl_unavailable_reason}")

    # Fallback rate estimation when linear TTL is unavailable
    if ttl_estimate is None and entry and len(primary_readings) >= 2:
        sorted_r = sorted(primary_readings, key=lambda r: r.timestamp)
        try:
            v0 = float(sorted_r[0].value)
            v1 = float(sorted_r[-1].value)
            baseline_span = max(abs(entry.baseline_max - entry.baseline_min), 1e-6)
            abs_rate = abs(v1 - v0) / baseline_span
            R = min(100.0, abs_rate * 100.0)
        except Exception:
            R = 0.0

    # --- E: Evidence / correlated deviations component (0-100) ---
    corr_count = len(correlated_deviations or [])
    E = min(100.0, corr_count * 20.0)

    # --- P: Persistence component (0-100) ---
    repeat_count = getattr(candidate, "repeat_count", 1) or 1
    P = min(100.0, (repeat_count - 1) * 25.0)

    # --- Q: Data quality component (0 = invalid, 100 = valid) ---
    quality_scores = {"VALID": 100.0, "DEGRADED": 60.0, "STALE": 20.0, "INVALID": 0.0}
    Q = quality_scores.get(data_quality, 100.0)

    # --- Weighted formula ---
    wD = cfg["w_deviation"]
    wR = cfg["w_rate"]
    wE = cfg["w_evidence"]
    wP = cfg["w_persistence"]
    wQ = cfg["w_quality"]
    raw_score = wD * D + wR * R + wE * E + wP * P + wQ * Q
    urgency_score = round(min(100.0, max(0.0, raw_score)), 2)

    scoring_breakdown = {
        "D_deviation": round(D, 2),
        "R_rate": round(R, 2),
        "E_evidence": round(E, 2),
        "P_persistence": round(P, 2),
        "Q_quality": round(Q, 2),
        "weighted_score": urgency_score,
    }

    # --- Hard rules (override band and score) ---
    hard_rule_fired = False

    # Source Critical
    source_pri = (candidate.source_priority or "").upper()
    if source_pri == "CRITICAL":
        triggered_rules.append("HARD_RULE_SOURCE_CRITICAL")
        reasons.append("Source priority is CRITICAL — urgency protected at URGENT band")
        hard_rule_fired = True
        urgency_score = max(urgency_score, 90.0)

    # Trip threshold breach
    if candidate.primary_alarm and (entry and entry.trip_threshold is not None):
        try:
            fval = float(latest_reading.value) if latest_reading else None
            if fval is not None:
                if entry.trip_threshold > entry.baseline_max and fval >= entry.trip_threshold:
                    triggered_rules.append("HARD_RULE_TRIP_LIMIT_BREACH")
                    reasons.append(f"Trip threshold breached: {fval} >= {entry.trip_threshold}")
                    hard_rule_fired = True
                    urgency_score = max(urgency_score, 85.0)
                elif entry.trip_threshold < entry.baseline_min and fval <= entry.trip_threshold:
                    triggered_rules.append("HARD_RULE_TRIP_LIMIT_BREACH")
                    reasons.append(f"Low trip threshold breached: {fval} <= {entry.trip_threshold}")
                    hard_rule_fired = True
                    urgency_score = max(urgency_score, 85.0)
        except Exception:
            pass

    # Time-to-limit critical
    if ttl_estimate and ttl_estimate.time_to_limit_seconds is not None:
        if ttl_estimate.time_to_limit_seconds <= cfg["ttl_critical_seconds"]:
            triggered_rules.append("HARD_RULE_TIME_TO_LIMIT_CRITICAL")
            reasons.append(f"Time-to-limit is critical: {ttl_estimate.time_to_limit_seconds:.0f}s")
            hard_rule_fired = True
            urgency_score = max(urgency_score, 92.0)

    # Combined conditions
    high_dev_count = sum(
        1 for d in (correlated_deviations or [])
        if abs(d.get("deviation_pct", 0)) >= cfg["combined_deviation_pct"]
    )
    if high_dev_count >= cfg["combined_conditions_min"]:
        triggered_rules.append("HARD_RULE_COMBINED_CONDITIONS")
        reasons.append(f"{high_dev_count} concurrent deviations >={cfg['combined_deviation_pct']:.0f}% — combined condition active")
        hard_rule_fired = True
        urgency_score = max(urgency_score, 82.0)

    # Data uncertainty → cap at ELEVATED
    if "HARD_RULE_DATA_UNCERTAINTY" in triggered_rules and not hard_rule_fired:
        urgency_score = min(urgency_score, 65.0)

    # --- Band assignment ---
    urgent_thresh = cfg["urgent_threshold"]
    elevated_thresh = cfg["elevated_threshold"]

    if urgency_score >= urgent_thresh:
        band = "URGENT"
    elif urgency_score >= elevated_thresh:
        band = "ELEVATED"
    else:
        band = "ROUTINE"

    # Build human-readable reasons
    if not reasons:
        reasons.append(f"Deviation {dev_pct:.1f}%, correlated signals: {corr_count}, repeat count: {repeat_count}")

    return UrgencyAssessment(
        source_priority=candidate.source_priority,
        urgency_score=urgency_score,
        urgency_band=band,
        urgency_reasons=reasons,
        triggered_rules=triggered_rules,
        data_quality=data_quality,
        time_to_limit=ttl_estimate,
        ttl_unavailable_reason=ttl_unavailable_reason if ttl_estimate is None else None,
        scoring_breakdown=scoring_breakdown,
    )


# ---------------------------------------------------------------------------
# Incident ranking
# ---------------------------------------------------------------------------

def rank_incidents(incidents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Multi-tier deterministic ranking of active incidents.

    Tier 0: Incidents with an alarm input still active rank above incidents
            whose alarms have all returned to normal
    Tier 1: Hard rules fired (any HARD_RULE_* except DATA_UNCERTAINTY)
    Tier 2: Time-to-limit urgency (smaller TTL ranks first)
    Tier 3: Urgency band (URGENT > ELEVATED > ROUTINE > UNKNOWN)
    Tier 4: Urgency score (higher first)
    Tier 5: Trigger timestamp (older = more persistent, ranks higher)

    Attaches ranking_position and ranking_rationale to each incident dict.
    """
    _BAND_ORDER = {"URGENT": 0, "ELEVATED": 1, "ROUTINE": 2, "UNKNOWN": 3}
    _SAFETY_RULES = {
        "HARD_RULE_SOURCE_CRITICAL",
        "HARD_RULE_TRIP_LIMIT_BREACH",
        "HARD_RULE_TIME_TO_LIMIT_CRITICAL",
        "HARD_RULE_COMBINED_CONDITIONS",
    }

    def _has_active_alarm(incident: Dict[str, Any]) -> bool:
        # Without alarm records the state is unknown, so nothing is demoted.
        alarms = incident.get("individual_alarms") or []
        return not alarms or any(str(a.get("lifecycle_state") or "").upper() in ("ACTIVE", "ACKNOWLEDGED", "UNRESOLVED")
                                 for a in alarms)

    def _sort_key(incident: Dict[str, Any]):
        urgency = incident.get("urgency") or {}
        if hasattr(urgency, "model_dump"):
            urgency = urgency.model_dump()

        triggered = set(urgency.get("triggered_rules") or [])
        has_hard = int(bool(triggered & _SAFETY_RULES))
        band = urgency.get("urgency_band") or "UNKNOWN"
        band_rank = _BAND_ORDER.get(band, 3)
        score = urgency.get("urgency_score") or 0.0
        ttl_obj = urgency.get("time_to_limit") or {}
        if hasattr(ttl_obj, "model_dump"):
            ttl_obj = ttl_obj.model_dump()
        ttl_val = ttl_obj.get("time_to_limit_seconds") if ttl_obj else None
        ttl_key = ttl_val if ttl_val is not None else float("inf")

        ts = incident.get("trigger_timestamp") or ""

        return (
            0 if _has_active_alarm(incident) else 1,  # T0: live alarms first
            -has_hard,         # T1: hard rule fires → lower = better
            ttl_key,           # T2: smaller TTL → lower = better
            band_rank,         # T3: URGENT=0 → lower = better
            -score,            # T4: higher score → lower sort key
            ts,                # T5: older timestamp → sorts first (lexicographic ISO8601)
        )

    sorted_incidents = sorted(incidents, key=_sort_key)

    for pos, incident in enumerate(sorted_incidents, start=1):
        urgency = incident.get("urgency") or {}
        if hasattr(urgency, "model_dump"):
            urgency = urgency.model_dump()

        triggered = set(urgency.get("triggered_rules") or [])
        band = urgency.get("urgency_band") or "UNKNOWN"
        score = urgency.get("urgency_score")
        reasons = []

        if triggered & _SAFETY_RULES:
            reasons.append(f"Hard rules active: {', '.join(sorted(triggered & _SAFETY_RULES))}")
        reasons.append(f"Band: {band}")
        if score is not None:
            reasons.append(f"Score: {score:.1f}/100")
        ttl_obj = urgency.get("time_to_limit") or {}
        if hasattr(ttl_obj, "model_dump"):
            ttl_obj = ttl_obj.model_dump()
        if ttl_obj and ttl_obj.get("time_to_limit_seconds") is not None:
            reasons.append(f"TTL: {ttl_obj['time_to_limit_seconds']:.0f}s")

        if not _has_active_alarm(incident):
            reasons.insert(0, "All alarm inputs returned to normal")
        incident["alarm_inputs_active"] = _has_active_alarm(incident)
        incident["ranking_position"] = pos
        incident["ranking_rationale"] = "; ".join(reasons)

    # In plain words: which rule put each incident where it is, compared with
    # its neighbour (the first incident is compared with the second).
    for pos, incident in enumerate(sorted_incidents):
        if len(sorted_incidents) == 1:
            incident["ranking_reason"] = "Only open incident."
            continue
        other = sorted_incidents[pos + 1] if pos == 0 else sorted_incidents[pos - 1]
        incident["ranking_reason"] = _order_reason(incident, other, _sort_key(incident), _sort_key(other), above=(pos == 0))

    # The full order, explained from each incident's own values: the facts the
    # rules use, and why it sits above the next one (the first rule that differs).
    for pos, incident in enumerate(sorted_incidents):
        incident["ranking_facts"] = _ranking_facts(incident, _has_active_alarm(incident), _SAFETY_RULES)
        below = sorted_incidents[pos + 1] if pos + 1 < len(sorted_incidents) else None
        incident["ranking_vs_next"] = (_order_reason(incident, below, _sort_key(incident), _sort_key(below), above=True)
                                       if below is not None else None)

    return sorted_incidents


def _ranking_facts(incident: Dict[str, Any], live: bool, safety_rules: set) -> Dict[str, Any]:
    urgency = incident.get("urgency") or {}
    if hasattr(urgency, "model_dump"):
        urgency = urgency.model_dump()
    ttl = urgency.get("time_to_limit") or {}
    if hasattr(ttl, "model_dump"):
        ttl = ttl.model_dump()
    alarms = incident.get("individual_alarms") or []
    active = [a for a in alarms if str(a.get("lifecycle_state") or "").upper() in ("ACTIVE", "ACKNOWLEDGED", "UNRESOLVED")]
    rules = sorted(set(urgency.get("triggered_rules") or []) & safety_rules)
    return {
        "live": live,
        "active_alarms": len(active) if alarms else None,
        "safety_rules": [_RULE_WORDS.get(r, r) for r in rules],
        "time_to_limit_seconds": ttl.get("time_to_limit_seconds") if ttl else None,
        "target_limit": ttl.get("target_limit") if ttl else None,
        "current_value": ttl.get("current_value") if ttl else None,
        "band": urgency.get("urgency_band") or "UNKNOWN",
        "score": round(float(urgency.get("urgency_score")), 1) if urgency.get("urgency_score") is not None else None,
        "started": incident.get("trigger_timestamp"),
    }


_RULE_WORDS = {
    "HARD_RULE_SOURCE_CRITICAL": "the alarm source marks it critical",
    "HARD_RULE_TRIP_LIMIT_BREACH": "a trip limit is breached",
    "HARD_RULE_TIME_TO_LIMIT_CRITICAL": "a reading will reach its limit very soon",
    "HARD_RULE_COMBINED_CONDITIONS": "several readings are badly out of range together",
}


def _name(incident: Dict[str, Any]) -> str:
    return str(incident.get("display_equipment") or incident.get("primary_tag") or "the other incident")


def _order_reason(me: Dict[str, Any], other: Dict[str, Any], mine: tuple, theirs: tuple, above: bool) -> str:
    """One sentence: the first ranking rule on which the two incidents differ."""
    who = _name(other)
    lead = f"Ahead of {who}" if above else f"Behind {who}"
    urgency = me.get("urgency") or {}
    if hasattr(urgency, "model_dump"):
        urgency = urgency.model_dump()
    their_urgency = other.get("urgency") or {}
    if hasattr(their_urgency, "model_dump"):
        their_urgency = their_urgency.model_dump()
    tier = next((i for i, (a, b) in enumerate(zip(mine, theirs)) if a != b), None)
    winner, loser = (urgency, their_urgency) if above else (their_urgency, urgency)
    here, there = ("this incident", who) if above else (who, "this incident")  # here = the one ranked higher
    if tier == 0:
        return f"{lead}: {here} still has active alarms; {there}'s alarms have returned to normal."
    if tier == 1:
        rules = sorted(set(winner.get("triggered_rules") or []) & set(_RULE_WORDS))
        why = " and ".join(_RULE_WORDS[r] for r in rules) or "a safety rule fired"
        return f"{lead}: on {here} {why}; on {there} no safety rule fired. Safety rules come first."
    if tier == 2:
        t = winner.get("time_to_limit") or {}
        secs = t.get("time_to_limit_seconds") if isinstance(t, dict) else None
        return f"{lead}: {here} will reach its limit sooner" + (f" (in {secs:.0f} s)." if secs is not None else ".")
    if tier == 3:
        return f"{lead}: {here} is in a higher urgency band ({winner.get('urgency_band')} vs {loser.get('urgency_band')})."
    if tier == 4:
        return (f"{lead}: same safety rules and band; {here} has the higher urgency score "
                f"({(winner.get('urgency_score') or 0):.0f} vs {(loser.get('urgency_score') or 0):.0f}).")
    if tier == 5:
        return f"{lead}: everything else equal; {here} started first."
    return f"{lead}: ranked equal; shown in the order received."
