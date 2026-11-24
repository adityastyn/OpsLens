"""Time-series analytics on recorded telemetry. Pure Python, no extra packages.

Everything here reads real records from tag_readings. Nothing is invented for
display: missing periods are reported as gaps, and a forecast is always labelled
PREDICTION and never used as a diagnosis.

Anomaly detection
  Algorithm: rolling median and median absolute deviation (MAD) of the previous
  samples, flagged when the robust z-score exceeds ANOMALY_Z, plus a plain check
  against the configured normal band.
  Input: one tag's numeric readings. Output: flagged samples with their reason.

Forecasting
  Algorithm: local linear trend with a significance gate. A least-squares line
  through the last few samples is extended, with damping, only if its slope is
  clearly above the noise; otherwise the forecast holds the recent mean. Chosen
  over damped Holt smoothing by backtest on the seven scenarios at a 30 s horizon
  (see docs/ANALYTICS_EVALUATION.md): Holt chased noise and overshot plateaus.
  Input: one tag's recent numeric readings. Output: a value per future step with
  an approximate 95 % interval that widens with the horizon.
  Backtest: rolling-origin error compared with a naive "no change" forecast. The
  score is reported with every forecast, so a useless forecast shows as useless.

Predictive warning
  A forecast whose path crosses a configured limit inside the horizon.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

ANOMALY_WINDOW = 20
ANOMALY_Z = 6.0
MIN_ANOMALY_HISTORY = 8
MIN_FORECAST_POINTS = 15
FORECAST_WINDOW = 60
TREND_WINDOW = 6
TREND_MIN_T = 5.0
DAMPING = 0.8
Z_95 = 1.96


def parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def numeric_points(rows: Sequence[Any]) -> List[Dict[str, Any]]:
    points = []
    for row in rows:
        if isinstance(row.value, bool) or not isinstance(row.value, (int, float)):
            continue
        moment = parse_time(row.timestamp)
        if moment is not None and math.isfinite(float(row.value)):
            points.append({"t": row.timestamp, "v": float(row.value), "quality": row.data_quality,
                           "source": row.data_source, "_dt": moment})
    points.sort(key=lambda p: p["_dt"])
    return points


def sample_interval(points: Sequence[Dict[str, Any]]) -> Optional[float]:
    deltas = [(b["_dt"] - a["_dt"]).total_seconds() for a, b in zip(points, points[1:])]
    deltas = [d for d in deltas if d > 0]
    return median(deltas) if deltas else None


def latest_run(points: Sequence[Dict[str, Any]], window: int = FORECAST_WINDOW) -> List[Dict[str, Any]]:
    """The newest readings back to the first gap much longer than their usual spacing."""
    pts = list(points)
    if len(pts) < 3:
        return pts
    tail = pts[-window:]
    usual = sample_interval(tail) or 0
    if usual <= 0:
        return pts
    start = 0
    for i in range(len(pts) - 1, 0, -1):
        if (pts[i]["_dt"] - pts[i - 1]["_dt"]).total_seconds() > max(5 * usual, usual + 10):
            start = i
            break
    return pts[start:]


def find_gaps(points: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Periods with no recorded data: more than 3 sampling intervals, at least 10 s."""
    interval = sample_interval(points)
    if interval is None:
        return []
    threshold = max(3.0 * interval, 10.0)
    gaps = []
    for a, b in zip(points, points[1:]):
        seconds = (b["_dt"] - a["_dt"]).total_seconds()
        if seconds > threshold:
            gaps.append({"from": a["t"], "to": b["t"], "seconds": round(seconds, 1)})
    return gaps


def downsample(points: List[Dict[str, Any]], limit: int, keep: Sequence[int] = ()) -> List[Dict[str, Any]]:
    """Evenly thin the series for drawing, keeping first, last and flagged samples."""
    if len(points) <= limit:
        return points
    step = len(points) / limit
    wanted = {int(i * step) for i in range(limit)} | {len(points) - 1} | set(keep)
    return [points[i] for i in sorted(wanted)]


def detect_anomalies(values: Sequence[float], normal: Tuple[Optional[float], Optional[float]] = (None, None)) -> List[Dict[str, Any]]:
    """Flag samples that break from their recent history or leave the normal band.

    Only samples before the current one are used, so this also works live."""
    low, high = normal
    span = (high - low) if low is not None and high is not None and high > low else None
    floor = (span * 0.02) if span else 1e-6
    flags: List[Dict[str, Any]] = []
    outside_before = False
    for i, value in enumerate(values):
        reasons = []
        outside = (low is not None and value < low) or (high is not None and value > high)
        # Mark where a reading leaves the normal band, not every sample while it
        # stays out: a sustained breach is shown as the limit state instead.
        if outside and not outside_before:
            reasons.append("left the normal range")
        outside_before = outside
        history = values[max(0, i - ANOMALY_WINDOW):i]
        z = None
        if len(history) >= MIN_ANOMALY_HISTORY:
            center = median(history)
            mad = median(abs(x - center) for x in history)
            scale = max(1.4826 * mad, floor)
            z = abs(value - center) / scale
            if z >= ANOMALY_Z:
                reasons.append("sudden change from recent history")
        if reasons:
            flags.append({"index": i, "reasons": reasons, "robust_z": round(z, 2) if z is not None else None})
    return flags


def _trend(values: Sequence[float]) -> Tuple[float, float, float]:
    """Level, slope per sample (0 if not significant), residual std of the recent line."""
    window = list(values[-TREND_WINDOW:])
    n = len(window)
    x_mean, y_mean = (n - 1) / 2, sum(window) / n
    sxx = sum((i - x_mean) ** 2 for i in range(n))
    slope = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(window)) / sxx
    intercept = y_mean - slope * x_mean
    residuals = [y - (intercept + slope * i) for i, y in enumerate(window)]
    variance = sum(r * r for r in residuals) / max(n - 2, 1)
    standard_error = math.sqrt(variance / sxx)
    t_value = abs(slope) / standard_error if standard_error > 1e-12 else (math.inf if abs(slope) > 1e-12 else 0.0)
    if t_value < TREND_MIN_T:
        recent = values[-5:]
        return sum(recent) / len(recent), 0.0, _noise(values)
    return intercept + slope * (n - 1), slope, max(math.sqrt(variance), _noise(values) * 0.25)


def _noise(values: Sequence[float]) -> float:
    """Sample-to-sample scatter, robust to a ramp."""
    steps = [b - a for a, b in zip(values[-30:], values[-29:])]
    if not steps:
        return 0.0
    center = median(steps)
    return 1.4826 * median(abs(step - center) for step in steps) / math.sqrt(2)


def project(level: float, trend: float, steps: int) -> List[float]:
    path, factor, phi = [], 0.0, 1.0
    for _ in range(steps):
        phi *= DAMPING
        factor += phi
        path.append(level + factor * trend)
    return path


def backtest(values: Sequence[float], steps: int, stride: int = 3) -> Optional[Dict[str, Any]]:
    """Rolling-origin error at the given horizon, against "no change"."""
    errors, naive_errors = [], []
    start = max(MIN_FORECAST_POINTS, 10)
    for origin in range(start, len(values) - steps, stride):
        history = values[max(0, origin - FORECAST_WINDOW):origin]
        if max(history) == min(history) and values[origin + steps - 1] == history[-1]:
            continue
        level, trend, _ = _trend(history)
        forecast = project(level, trend, steps)[-1]
        actual = values[origin + steps - 1]
        errors.append(abs(actual - forecast))
        naive_errors.append(abs(actual - history[-1]))
    if len(errors) < 3:
        return None
    mae, naive = sum(errors) / len(errors), sum(naive_errors) / len(naive_errors)
    return {"origins": len(errors), "mae": round(mae, 4), "naive_mae": round(naive, 4),
            "skill": round(1 - mae / naive, 3) if naive > 0 else None}


def forecast_series(points: Sequence[Dict[str, Any]], horizon_seconds: float, limits: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Forecast one tag. 'available' is False when the record cannot support one."""
    base = {"kind": "PREDICTION", "method": "Statistical linear trend (least squares on recent samples, significance gate); not a machine-learning model",
            "input": "Recorded readings of this tag", "note": "A prediction, not a measurement and not a diagnosis."}
    # Forecast from the latest unbroken run only: older readings at another spacing
    # (for example a past month logged every 30 min) must not set the time step.
    points = latest_run(points)
    interval = sample_interval(points)
    if len(points) < MIN_FORECAST_POINTS or not interval:
        return {**base, "available": False, "reason": f"Needs at least {MIN_FORECAST_POINTS} recent samples in one unbroken run; found {len(points)}."}
    recent = list(points[-FORECAST_WINDOW:])
    values = [p["v"] for p in recent]
    level, trend, residual = _trend(values)
    residual = max(residual, abs(trend) * 0.25)  # a perfect recent fit does not mean a certain future
    steps = max(1, int(round(horizon_seconds / interval)))
    path = project(level, trend, steps)
    last = recent[-1]["_dt"]
    out = []
    crossing = None
    for i, value in enumerate(path, start=1):
        half = Z_95 * residual * math.sqrt(i)
        moment = last + timedelta(seconds=interval * i)
        out.append({"t": iso(moment), "v": round(value, 3), "lo": round(value - half, 3), "hi": round(value + half, 3)})
        if crossing is None:
            for limit in limits:
                crossed = value >= limit["value"] if limit["direction"] == "high" else value <= limit["value"]
                already = last_past(values[-1], limit)
                if crossed and not already:
                    crossing = {"limit": limit["label"], "value": limit["value"], "direction": limit["direction"],
                                "at": iso(moment), "in_seconds": round(interval * i, 1)}
                    break
    state = limit_state(values, trend, limits, crossing)
    return {**base, "available": True, "limit_state": state, "fitted": {"trend_window": TREND_WINDOW, "min_t": TREND_MIN_T, "damping": DAMPING,
                                                   "slope_per_sample": round(trend, 5), "trend_significant": trend != 0.0},
            "horizon_seconds": round(interval * steps, 1), "sample_interval_seconds": interval,
            "samples_used": len(values), "last_measured": {"t": recent[-1]["t"], "v": round(values[-1], 3)},
            "residual_std": round(residual, 4), "points": out, "crossing": crossing,
            "backtest": backtest([p["v"] for p in points[-(FORECAST_WINDOW + 2 * steps):]], steps)}


STATE_TEXT = {
    "ACTIVE_BREACH": "ACTIVE BREACH",
    "ACTIVE_BREACH_WORSENING": "ACTIVE BREACH + WORSENING TREND",
    "PREDICTED_LIMIT_CROSSING": "PREDICTED LIMIT CROSSING",
    "WITHIN_LIMITS": "WITHIN LIMITS",
    "NO_LIMIT": "NO ALARM LIMIT CONFIGURED",
}


def limit_state(values: Sequence[float], trend: float, limits: Sequence[Dict[str, Any]], crossing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Where the latest reading stands against its limits.

    Already past a limit -> ACTIVE BREACH (a measurement, never an early
    warning), plus WORSENING TREND when the significant trend moves further
    past it. Within limits but the forecast crosses one -> PREDICTED LIMIT
    CROSSING. Otherwise WITHIN LIMITS."""
    last = values[-1] if values else None
    if last is None:
        return {"state": "WITHIN_LIMITS", "text": STATE_TEXT["WITHIN_LIMITS"]}
    breached = [l for l in limits if last_past(last, l)]
    if breached:
        # The furthest limit passed (a high-high before a high).
        worst = max(breached, key=lambda l: l["value"] if l["direction"] == "high" else -l["value"])
        worsening = trend != 0.0 and ((trend > 0) if worst["direction"] == "high" else (trend < 0))
        key = "ACTIVE_BREACH_WORSENING" if worsening else "ACTIVE_BREACH"
        return {"state": key, "text": STATE_TEXT[key], "limit": worst["label"], "limit_value": worst["value"],
                "direction": worst["direction"], "measured": round(last, 3),
                "note": "Measured now, not a prediction." + (" The recorded trend is moving further past the limit." if worsening else "")}
    if crossing:
        return {"state": "PREDICTED_LIMIT_CROSSING", "text": STATE_TEXT["PREDICTED_LIMIT_CROSSING"], "limit": crossing["limit"],
                "limit_value": crossing["value"], "in_seconds": crossing["in_seconds"], "measured": round(last, 3),
                "note": "A forecast from the recent trend, not a measurement."}
    if not limits:
        return {"state": "NO_LIMIT", "text": STATE_TEXT["NO_LIMIT"], "measured": round(last, 3),
                "note": "No SOP or alarm limit is configured for this tag, so only the normal range applies."}
    return {"state": "WITHIN_LIMITS", "text": STATE_TEXT["WITHIN_LIMITS"], "measured": round(last, 3)}


def last_past(value: float, limit: Dict[str, Any]) -> bool:
    return value >= limit["value"] if limit["direction"] == "high" else value <= limit["value"]


def strip_internal(points: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{k: v for k, v in p.items() if not k.startswith("_")} for p in points]


def summarize(points: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not points:
        return None
    values = [p["v"] for p in points]
    return {"samples": len(values), "min": round(min(values), 3), "max": round(max(values), 3),
            "mean": round(sum(values) / len(values), 3), "last": round(values[-1], 3),
            "from": points[0]["t"], "to": points[-1]["t"]}
