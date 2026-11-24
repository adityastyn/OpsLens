"""Possible causes built from the incident record only.

Candidates come from four kinds of evidence:
  1. trip    - an equipment fault or trip status that turned on, with the
               readings of the same equipment, or directly linked to it in
               the configured plant links, that back it up or stayed normal;
  1b. standby - a standby unit that never ran while a duty unit was tripped;
  2. sop     - readings past an SOP limit whose action names a cause, for
               example "300 m3/h low -> Suspect clog";
  3. early   - readings that left their normal range before the primary alarm.

Each candidate is then assessed on its own evidence (assess_candidate), on
the same factors for every candidate: recorded trip or fault, changed before
the alarm, size of the deviation, other readings that agree, an SOP limit
with an action, a configured path to the alarm, the same fix in earlier
incidents, minus related readings that stayed normal. Candidates are ordered
by those points, so the kind of evidence is one factor, not the whole
ranking: a trip after the alarm, or a lone reading its neighbours
contradict, does not lead by default. Points rank candidates against each
other; they are not probabilities.

Nothing here comes from the scenario's answer: it reads the same readings,
status changes, SOP limits and configured plant links the operator sees.

Roles, so every screen tells the same story:
  leading       - the candidate with the most evidence points;
  contributing  - a trip, SOP 'suspect' limit or standby failure, a reading
                  that changed no later than the leading cause, or a later
                  reading on another configured path to the alarm;
  chain         - "related condition": a later reading on the configured
                  path from the leading cause to the alarm;
  effect        - "possible consequence": a later reading off that path;
  insufficient  - "insufficient evidence": one abnormal reading whose related
                  readings stayed normal (it may be the instrument).
The roles are hypotheses too: the order of events does not prove cause, and
the links are the plant's configured relationships, not the scenario answer.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

MAX_CANDIDATES = 4
# Readings that changed within this many seconds of the leading cause count as
# changing together with it, not after it.
SAME_TIME_SECONDS = 1.0
ROLE_LABELS = {"leading": "Leading hypothesis", "contributing": "Contributing hypothesis", "chain": "Related condition",
               "effect": "Possible consequence", "insufficient": "Insufficient evidence"}


def _influence_maps():
    """Configured 'may influence' links: tag -> its upstream tags, and the reverse."""
    import incident_catalog as catalog
    up: Dict[str, set] = {}
    for tag, item in {**catalog.plant_signals(), **catalog.plant_statuses()}.items():
        up.setdefault(tag, set()).update(getattr(item, "upstream", ()) or ())
    down: Dict[str, set] = {}
    for tag, sources in up.items():
        for source in sources:
            down.setdefault(source, set()).add(tag)
    return up, down


def _reach(start: set, links: Dict[str, set]) -> set:
    seen, stack = set(), list(start)
    while stack:
        for nxt in links.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen
_FAULT = re.compile(r"(FAULT|TRIP|FAIL)", re.I)
_SUSPECT = re.compile(r"\bsuspect\s+([a-z][a-z \-]{2,40})", re.I)
_EQUIPMENT = re.compile(r"\b([A-Z]{1,4}-[A-Z]?\d{1,3}[A-Z]?|[A-Z]{2,4}-[A-Z]\d)\b")


def _time(ts: Optional[str]) -> Optional[datetime]:
    """Always timezone-aware (readings without an offset are UTC), so times
    from the story, the telemetry and the alarm can be compared."""
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _hms(ts: Optional[str]) -> Optional[str]:
    t = _time(ts)
    return t.strftime("%H:%M:%S UTC") if t else None


def _fmt(value: Any) -> Any:
    if isinstance(value, float):
        value = round(value, 2) if abs(value) < 100 else round(value)
        return int(value) if float(value).is_integer() else value
    return value


def equipment_of(text: Optional[str]) -> Optional[str]:
    match = _EQUIPMENT.search(text or "")
    return match.group(1) if match else None


def _evidence(row: Dict[str, Any]) -> str:
    text = f"{row['label']} reached {_fmt(row['worst_value'])} {row['unit']}".strip()
    if row.get("normal_text"):
        text += f" (normal {row['normal_text']})"
    if row.get("limit_text") or row.get("limit_reached"):
        text += f"; SOP limit {row.get('limit_text') or row.get('limit_reached')}"
        if row.get("limit_action"):
            text += f" -> {row['limit_action']}"
        if row.get("limit_page"):
            text += f" (p.{row['limit_page']})"
    return text


def _abnormal(row: Dict[str, Any]) -> bool:
    return row.get("state_at_worst") not in (None, "within normal")


def _status_turned_on(db, tag: str, since: Optional[datetime], until: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """When a status tag turned on for THIS incident, from the stored telemetry,
    for trips the event story did not keep: the latest turn-on at or before
    `until` (the primary alarm, or the latest at all without one), so an earlier
    trip of the same equipment in the look-back window is not mistaken for this
    one; if it only turned on after the alarm, the first turn-on after it."""
    if db is None:
        return None
    try:
        rows = db.get_readings(tag=tag, start_time=since.strftime("%Y-%m-%dT%H:%M:%S") if since else None, limit=5000)
    except Exception:
        return None
    rows = sorted((r for r in rows if _time(r.timestamp)), key=lambda r: _time(r.timestamp))
    previous = None
    turned_on = []
    for row in rows:
        t = _time(row.timestamp)
        if row.value is True and previous is not True and (since is None or t >= since):
            turned_on.append(row)
        previous = row.value
    if not turned_on:
        return None
    before = [row for row in turned_on if until is None or _time(row.timestamp) <= until]
    chosen = before[-1] if before else turned_on[0]
    return {"tag": tag, "timestamp": chosen.timestamp, "value_after": True}


def build_cause_candidates(full_event: Dict[str, Any], brief: Dict[str, Any], own_tags: set, db=None) -> List[Dict[str, Any]]:
    import alarm_config
    from operator_brief import _label

    problem = brief.get("problem") or {}
    definition = alarm_config.definition_for_tag(problem.get("alarm_tag") or "") or {}
    # The primary alarm's own measurement is the symptom, not a cause.
    symptom = set(alarm_config.measurement_tags(definition)) if definition else set()
    if not symptom:
        # Alarms without a configured measurement: the sensor the alarm is raised from.
        import incident_catalog as catalog
        source = (catalog.alarm_inputs().get(problem.get("alarm_tag") or "") or {}).get("source_tag")
        symptom = {source} if source else set()
    readings = [r for r in brief.get("incident_readings") or [] if r.get("tag") in own_tags and r.get("tag") not in symptom]
    steps = list((full_event.get("story") or {}).get("steps") or [])
    primary_at = _time(problem.get("first_triggered_at"))
    story_tags = {s.get("tag") for s in steps}
    window = None
    first = min((_time(s.get("timestamp")) for s in steps if _time(s.get("timestamp"))), default=None)
    if first is not None:
        from datetime import timedelta
        window = first - timedelta(minutes=10)
    for tag in sorted(own_tags):
        if _FAULT.search(tag) and tag not in story_tags:
            found = _status_turned_on(db, tag, window, primary_at)
            if found:
                steps.append(found)
    steps.sort(key=lambda s: _time(s.get("timestamp")) or datetime.max.replace(tzinfo=timezone.utc))
    first_change: Dict[str, datetime] = {}
    for step in steps:
        t = _time(step.get("timestamp"))
        if t and step.get("tag") and step["tag"] not in first_change:
            first_change[step["tag"]] = t

    def before_alarm(tag: str) -> Optional[bool]:
        t = first_change.get(tag)
        return None if t is None or primary_at is None else t < primary_at

    candidates: List[Dict[str, Any]] = []
    used: set = set()
    up_links, down_links = _influence_maps()

    def linked_to(tag: str, hops: int = 1) -> set:
        """Tags within `hops` configured links of `tag`, either direction."""
        seen, frontier = set(), {tag}
        for _ in range(hops):
            frontier = {n for t in frontier for n in up_links.get(t, set()) | down_links.get(t, set())} - seen - {tag}
            seen |= frontier
        return seen

    # 1. Trips and faults recorded as status changes.
    for step in steps:
        tag = step.get("tag") or ""
        if tag in used or tag not in own_tags or not _FAULT.search(tag) or step.get("value_after") is not True:
            continue
        used.add(tag)
        label = _label(tag)
        equipment = equipment_of(label)
        support = [f"{tag} {label} turned on at {_hms(step.get('timestamp'))}"]
        against: List[str] = []
        # Readings of the same equipment, or directly linked to the trip in the plant's configured links.
        near = linked_to(tag)
        related = [r for r in readings if r["tag"] not in used and
                   ((equipment and equipment_of(r.get("label")) == equipment) or r["tag"] in near)]
        for row in related:
            (support if _abnormal(row) else against).append(
                _evidence(row) if _abnormal(row) else f"{row['label']} stayed within normal ({_fmt(row['worst_value'])} {row['unit']})")
            used.add(row["tag"])
        candidates.append({
            "kind": "trip", "equipment": equipment, "tags": [tag],
            "evidence_tags": [r["tag"] for r in related],
            "statement": f"{label}. The trip is recorded; what caused the trip is not recorded.",
            "basis": "Equipment trip or fault status in the record",
            "first_seen": _hms(step.get("timestamp")), "first_seen_at": step.get("timestamp"), "before_alarm": before_alarm(tag),
            "short": label,
            "supporting": support, "contradicting": against,
        })

    # 1b. A standby unit that never ran while another unit was tripped.
    if any(c["kind"] == "trip" for c in candidates) and db is not None:
        for tag in sorted(own_tags):
            label = _label(tag)
            if "RUN" not in tag or "standby" not in label.lower():
                continue
            try:
                rows = [r for r in db.get_readings(tag=tag, start_time=window.strftime("%Y-%m-%dT%H:%M:%S") if window else None, limit=5000)
                        if _time(r.timestamp) and (window is None or _time(r.timestamp) >= window)]
            except Exception:
                continue
            if rows and not any(r.value is True for r in rows):
                used.add(tag)
                candidates.append({
                    "kind": "standby", "equipment": equipment_of(label), "tags": [tag], "evidence_tags": [],
                    "statement": f"{label.replace(' running', '')} did not start, so the lost capacity was not replaced.",
                    "basis": "Standby status stayed off while a duty unit was tripped",
                    "first_seen": None, "first_seen_at": None, "before_alarm": None,
                    "short": f"{label.replace(' running', '')} did not start",
                    "supporting": [f"{tag} {label} stayed off in all {len(rows)} recorded samples of this incident"],
                    "contradicting": [],
                })

    # 2. SOP limits whose action names a suspected cause, grouped by equipment.
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for row in readings:
        match = _SUSPECT.search(str(row.get("limit_action") or ""))
        if match and row.get("state_at_worst") == "LIMIT EXCEEDED":
            groups.setdefault((equipment_of(row.get("label")), match.group(1).strip().lower()), []).append(row)
    for (equipment, cause), rows in groups.items():
        others = [r for r in readings if equipment and equipment_of(r.get("label")) == equipment and r not in rows and r["tag"] not in used]
        support = [_evidence(r) for r in rows] + [_evidence(r) for r in others if _abnormal(r)]
        against = [f"{r['label']} stayed within normal ({_fmt(r['worst_value'])} {r['unit']})" for r in others if not _abnormal(r)]
        tags = [r["tag"] for r in rows] + [r["tag"] for r in others]
        used.update(tags)
        pages = sorted({r.get("limit_page") for r in rows if r.get("limit_page")})
        early = [before_alarm(t) for t in tags if before_alarm(t) is not None]
        candidates.append({
            "kind": "sop", "equipment": equipment, "tags": tags, "evidence_tags": list(tags),
            "statement": f"{cause.capitalize()}{' on ' + equipment if equipment else ''}: the SOP says to suspect {cause} at these readings.",
            "basis": f"SOP limit action 'Suspect {cause}'" + (f" (p.{', '.join(str(p) for p in pages)})" if pages else ""),
            "first_seen": _hms(min(first_change[t] for t in seen).isoformat()) if (seen := [t for t in tags if t in first_change]) else None,
            "first_seen_at": min(first_change[t] for t in seen).isoformat() if seen else None,
            "short": f"{cause.capitalize()}{' on ' + equipment if equipment else ''}",
            "before_alarm": any(early) if early else None,
            "supporting": support, "contradicting": against,
        })

    # 3. Readings that left normal first.
    early_rows = [r for r in readings if r["tag"] not in used and _abnormal(r) and r["tag"] in first_change]
    early_rows.sort(key=lambda r: first_change[r["tag"]])
    # Readings that changed in the same second get a fixed order, so the
    # leading cause does not depend on milliseconds: upstream first, then
    # readings past an SOP limit, then by name.
    clusters: List[List[Dict[str, Any]]] = []
    for row in early_rows:
        if clusters and (first_change[row["tag"]] - first_change[clusters[-1][0]["tag"]]).total_seconds() <= SAME_TIME_SECONDS:
            clusters[-1].append(row)
        else:
            clusters.append([row])
    early_rows = []
    for group in clusters:
        tags = {r["tag"] for r in group}
        group.sort(key=lambda r: (len(_reach({r["tag"]}, up_links) & tags), r.get("state_at_worst") != "LIMIT EXCEEDED", r["label"]))
        early_rows.extend(group)
    for row in early_rows:
        if len(candidates) >= MAX_CANDIDATES:
            break
        ahead = before_alarm(row["tag"])
        candidates.append({
            "kind": "early", "equipment": equipment_of(row.get("label")), "tags": [row["tag"]], "evidence_tags": [row["tag"]],
            "statement": f"{row['label']} outside normal, as a cause or a contributing condition.",
            "basis": "Changed before the primary alarm" if ahead else "Abnormal reading in this incident",
            "first_seen": _hms(first_change[row["tag"]].isoformat()), "first_seen_at": first_change[row["tag"]].isoformat(),
            "before_alarm": ahead, "short": f"{row['label']} {_direction(row)}".strip(),
            "supporting": [_evidence(row)],
            "contradicting": [] if ahead or ahead is None else ["Changed after the primary alarm, so it may be an effect"],
        })
        used.add(row["tag"])

    # 4. Nothing points to a cause, and the alarm's own reading moved alone while
    # the readings that should move with it stayed normal: say so, as a possible
    # instrument fault, rather than naming a cause the record does not support.
    if not candidates:
        all_rows = brief.get("incident_readings") or []
        for row in [r for r in all_rows if r.get("tag") in symptom and _abnormal(r)]:
            equipment = equipment_of(row.get("label"))
            near = linked_to(row["tag"])
            related = [r for r in all_rows if r is not row and r.get("tag") in own_tags and
                       ((equipment and equipment_of(r.get("label")) == equipment) or r.get("tag") in near)]
            normal = [r for r in related if not _abnormal(r)]
            if related and len(normal) == len(related):
                names = _join([r["label"] for r in normal])
                candidates.append({
                    "kind": "early", "equipment": equipment, "tags": [row["tag"]], "evidence_tags": [row["tag"], *[r["tag"] for r in normal]],
                    "statement": f"{row['label']} {_direction(row)} on its own: {names} stayed normal, so the reading itself may be wrong. "
                                 "Check the instrument before treating it as a process fault.",
                    "basis": "The alarm's own reading moved alone", "short": f"{row['label']} {_direction(row)} on its own",
                    "first_seen": _hms(first_change[row["tag"]].isoformat()) if row["tag"] in first_change else None,
                    "first_seen_at": first_change[row["tag"]].isoformat() if row["tag"] in first_change else None,
                    "before_alarm": None, "supporting": [_evidence(row)],
                    "contradicting": [f"{r['label']} stayed within normal ({_fmt(r['worst_value'])} {r['unit']})" for r in normal],
                })

    # Every candidate is assessed on the same evidence factors, independently,
    # and ordered by its points (see assess_candidate). The kind of evidence is
    # one factor among several, so a trip that came after the alarm, or a lone
    # reading its neighbours contradict, no longer leads by default.
    trip = next((c for c in candidates if c["kind"] == "trip"), None)
    for c in candidates:
        if c["kind"] == "standby" and trip is not None:
            c["before_alarm"] = trip.get("before_alarm")
            c["evidence_tags"] = list(trip.get("evidence_tags") or [])
    rows_by_tag = {r["tag"]: r for r in readings}
    toward_alarm = _reach(set(symptom), up_links)
    for c in candidates:
        assess_candidate(c, rows_by_tag, toward_alarm)
    kind_order = {"trip": 0, "standby": 1, "sop": 2, "early": 3}
    candidates.sort(key=lambda c: (-c["evidence_points"], kind_order.get(c["kind"], 9),
                                   _time(c.get("first_seen_at")) or datetime.max.replace(tzinfo=timezone.utc)))
    for rank, candidate in enumerate(candidates[:MAX_CANDIDATES], start=1):
        candidate["rank"] = rank
        candidate["status"] = "HYPOTHESIS"
    return explain_ranking(assign_roles(candidates[:MAX_CANDIDATES], symptom))


# Points per evidence factor, out of 100. Not a probability: the points rank
# candidates against each other and show what each one rests on.
FACTOR_POINTS = {
    "direct": 25,       # a trip or fault status turned on (standby that never started: 15)
    "timing": 20,       # changed before the primary alarm
    "magnitude": 15,    # how far its readings went: past an SOP limit, or how far outside normal
    "corroboration": 15,  # other readings of the same equipment that are also abnormal (5 each)
    "sop": 10,          # an SOP limit with an action ("Suspect clog") was exceeded
    "topology": 10,     # on the plant's configured path to the alarm
    "history": 5,       # same alarm resolved before with this equipment named in the fix
}
FACTOR_LABELS = {"direct": "Recorded trip or fault", "timing": "Changed before the alarm", "magnitude": "Size of the deviation",
                 "corroboration": "Other readings agree", "sop": "SOP limit with an action exceeded",
                 "topology": "On the configured path to the alarm", "history": "Same fix found in earlier incidents",
                 "contradiction": "Related readings stayed normal"}


def assess_candidate(c: Dict[str, Any], rows_by_tag: Dict[str, Dict[str, Any]], toward_alarm: set,
                     history_notes: Optional[List[str]] = None) -> Dict[str, Any]:
    """Points for one candidate from its own evidence, independent of the others."""
    rows = [rows_by_tag[t] for t in c.get("evidence_tags") or [] if t in rows_by_tag]
    abnormal = [r for r in rows if _abnormal(r)]
    normal = [r for r in rows if not _abnormal(r)]
    f: Dict[str, int] = {}
    f["direct"] = 25 if c["kind"] == "trip" else 15 if c["kind"] == "standby" else 0
    ahead = c.get("before_alarm")
    f["timing"] = 20 if ahead else 0 if ahead is False else 5
    ranks = [tuple(r.get("severity_rank") or (0, 0)) for r in abnormal]
    worst = max(ranks, default=(0, 0))
    f["magnitude"] = 15 if worst[0] >= 2 else min(10, round(4 + 4 * float(worst[1]))) if worst[0] == 1 else 0
    # The candidate's own first reading is its evidence; the rest corroborate it.
    extra = len(abnormal) if c["kind"] in ("trip", "standby") else max(0, len(abnormal) - 1)
    f["corroboration"] = min(3, extra) * 5
    f["sop"] = 10 if any(r.get("state_at_worst") == "LIMIT EXCEEDED" and r.get("limit_action") for r in abnormal) else 0
    linked = set(c.get("tags") or []) | {r["tag"] for r in abnormal}
    f["topology"] = 10 if linked & toward_alarm else 0
    words = [w for w in [c.get("equipment") or ""] if w]
    f["history"] = 5 if words and any(w.lower() in (n or "").lower() for n in history_notes or [] for w in words) else 0
    f["contradiction"] = -min(15, 5 * len(normal))
    c["evidence_factors"] = f
    c["evidence_points"] = max(0, sum(f.values()))
    c["evidence_counts"] = {"abnormal": len(abnormal), "normal": len(normal)}
    # Too little to stand on: no recorded trip, at most one abnormal reading,
    # and related readings that stayed normal (a lone flow reading with normal
    # pressure and current may be the meter, not a clog).
    c["insufficient"] = c["kind"] not in ("trip", "standby") and len(abnormal) <= 1 and len(normal) >= 1
    return c


def _factor_words(f: Dict[str, int]) -> List[str]:
    return [f"{_lower_first(FACTOR_LABELS[k])} (+{v})" for k, v in f.items() if v > 0]


def explain_ranking(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Why each candidate sits where it does, against the leading one."""
    lead = next((c for c in candidates if c.get("role") == "leading"), None)
    for c in candidates:
        f = c.get("evidence_factors") or {}
        if c is lead:
            c["ranking_note"] = (f"Ranks first with {c['evidence_points']} evidence points: "
                                 + (", ".join(_factor_words(f)) or "no strong factor")
                                 + (f"; {_lower_first(FACTOR_LABELS['contradiction'])} ({f['contradiction']})" if f.get("contradiction") else "") + ".")
            continue
        if lead is None:
            c["ranking_note"] = f"{c['evidence_points']} evidence points; not enough to lead."
            continue
        lf = lead.get("evidence_factors") or {}
        gaps = [f"{_lower_first(FACTOR_LABELS[k])} ({f.get(k, 0) - lf.get(k, 0):+d})" for k in FACTOR_POINTS if f.get(k, 0) != lf.get(k, 0)]
        if f.get("contradiction", 0) != lf.get("contradiction", 0):
            gaps.append(f"{_lower_first(FACTOR_LABELS['contradiction'])} ({f.get('contradiction', 0) - lf.get('contradiction', 0):+d})")
        if c.get("after_alarm"):
            c["ranking_note"] = (f"{c['evidence_points']} evidence points, but it first appeared after the primary alarm, so it cannot "
                                 f"have started it; {lead.get('short') or 'the leading hypothesis'} changed earlier.")
            continue
        name = lead.get('short') or 'the leading hypothesis'
        differ = (" Where they differ: " + ", ".join(gaps) + ".") if gaps else ""
        if c["evidence_points"] == lead["evidence_points"]:
            c["ranking_note"] = (f"Same evidence points as {name} ({c['evidence_points']}); a tie goes to a recorded trip or fault, "
                                 f"then a standby that did not start, an SOP limit action, an early change, then the earlier time.{differ}")
        else:
            c["ranking_note"] = f"{c['evidence_points']} evidence points against {lead['evidence_points']} for {name}.{differ}"
    return candidates


def _lower_first(text: str) -> str:
    """'SOP limit with…' stays 'SOP limit…'; 'Recorded trip' becomes 'recorded trip'."""
    return text if text[1:2].isupper() else text[:1].lower() + text[1:]


def _direction(row: Dict[str, Any]) -> str:
    worst, low, high = row.get("worst_value"), row.get("normal_low"), row.get("normal_high")
    try:
        if high is not None and float(worst) > float(high):
            return "high"
        if low is not None and float(worst) < float(low):
            return "low"
    except (TypeError, ValueError):
        pass
    return "outside normal"


def assign_roles(candidates: List[Dict[str, Any]], symptom: Optional[set] = None) -> List[Dict[str, Any]]:
    """Leading, contributing, chain or likely effect. See the module notes."""
    if not candidates:
        return candidates
    weak = [c for c in candidates if c.get("insufficient")]
    for c in weak:
        n_normal = (c.get("evidence_counts") or {}).get("normal", 0)
        c.update(role="insufficient",
                 role_note=(f"Abnormal, but {n_normal} related reading{'s' if n_normal != 1 else ''} stayed normal and nothing else backs it: "
                            "check the instrument before treating it as a cause." if n_normal
                            else "Too little evidence to treat it as a cause on its own."))
    strong = [c for c in candidates if not c.get("insufficient")]
    if not strong:
        return candidates
    # Something that first appeared after the primary alarm cannot have started
    # it: it may lead only when nothing was recorded earlier.
    early = [c for c in strong if c.get("before_alarm") is not False]
    late = [c for c in strong if c.get("before_alarm") is False]
    if early:
        for c in late:
            c["after_alarm"] = True
        strong = early + late
    # The best-supported candidate leads; weak ones stay listed below it.
    candidates[:] = strong + weak
    for rank, c in enumerate(candidates, start=1):
        c["rank"] = rank
    lead = candidates[0]
    lead_at = _time(lead.get("first_seen_at"))
    lead.update(role="leading", role_note="Leading hypothesis: the most evidence points in the record.")
    up_links, down_links = _influence_maps()
    toward_alarm = _reach(set(symptom or ()), up_links)       # everything that may influence the alarm
    from_lead = _reach(set(lead.get("tags") or ()), down_links)  # everything the leading cause may influence
    from_alarm = _reach(set(symptom or ()), down_links)           # everything the alarm's own measurement may influence
    into_lead = _reach(set(lead.get("tags") or ()), up_links)     # everything that may influence the leading cause
    for c in candidates[1:]:
        if c.get("role") == "insufficient":
            continue
        at = _time(c.get("first_seen_at"))
        tags = set(c.get("tags") or ())
        if c["kind"] in ("trip", "sop", "standby"):
            c.update(role="contributing",
                     role_note="Also backed by a recorded trip, an SOP 'suspect' limit or a standby status, so it may act together with the leading cause.")
        elif (tags & toward_alarm and tags & from_lead and lead_at is not None and at is not None
              and (at - lead_at).total_seconds() >= -SAME_TIME_SECONDS):
            seconds = max(0, round((at - lead_at).total_seconds()))
            c.update(role="chain",
                     role_note=(f"Changed {seconds} s after the leading cause" if seconds else "Changed with the leading cause")
                     + " and sits on the configured path from it to the alarm.")
            if c["kind"] == "early" and c.get("short"):
                c["statement"] = f"{c['short']}, a link between {lead.get('short') or 'the leading cause'} and the alarm."
        elif tags & into_lead:
            c.update(role="contributing",
                     role_note="Sits on the configured path into the leading cause, so it may have brought it about.")
        elif (tags & from_lead or tags & from_alarm) and not tags & toward_alarm:
            # Downstream of the leading cause and off the path to the alarm: a consequence.
            seconds = max(0, round((at - lead_at).total_seconds())) if at and lead_at else None
            where = "the alarm's own measurement" if tags & from_alarm and not tags & from_lead else "the leading cause"
            c.update(role="effect",
                     role_note=f"Sits downstream of {where} in the plant's configured links, so it is more likely an effect. Not proven.")
            if c["kind"] == "early" and c.get("short"):
                c["statement"] = f"{c['short']}, more likely an effect of {lead.get('short') or 'the leading cause'} than a cause."
            note = f"Downstream of {lead.get('short') or 'the leading cause'}" + (f", changed {seconds} s after it" if seconds else "")
            c.setdefault("contradicting", []).append(note + ", so it is more likely an effect")
        elif lead_at is None or at is None or (at - lead_at).total_seconds() <= SAME_TIME_SECONDS:
            c.update(role="contributing",
                     role_note="Changed no later than the leading cause, so it may have contributed.")
        elif tags & toward_alarm:
            c.update(role="contributing",
                     role_note="Changed later, but sits on another configured path to the alarm, so it may have contributed.")
        else:
            seconds = round((at - lead_at).total_seconds())
            c.update(role="effect",
                     role_note=f"Changed {seconds} s after the leading cause, so it is more likely an effect of it. Not proven.")
            if c["kind"] == "early" and c.get("short"):
                c["statement"] = f"{c['short']}, more likely an effect of {lead.get('short') or 'the leading cause'} than a cause."
            if not any("likely an effect" in x for x in c.get("contradicting") or []):
                c.setdefault("contradicting", []).append(f"Changed {seconds} s after {lead.get('short') or 'the leading cause'}, so it is more likely an effect")
    return candidates


def _join(items: List[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def leading_summary(candidates: List[Dict[str, Any]]) -> Optional[str]:
    """One sentence every screen shows as the leading hypothesis."""
    if not candidates:
        return None
    lead = candidates[0]
    if lead.get("role") == "insufficient":
        return (f"Insufficient evidence for a cause: {lead.get('short') or lead['statement']}, "
                "but related readings stayed normal.")
    low = lambda t: t[0].lower() + t[1:] if t[:2] != t[:2].upper() else t
    chain = [c.get("short") or c["statement"] for c in candidates[1:] if c.get("role") == "chain"]
    together = [c.get("short") or c["statement"] for c in candidates[1:] if c.get("role") == "contributing"]
    text = lead.get("short") or lead["statement"]
    if chain:
        text += ", which led to " + _join([low(t) for t in chain])
    if together:
        text += (";" if chain else ",") + " together with " + _join([low(t) for t in together])
    return text + "."


def mentioned(candidate: Dict[str, Any], text: str) -> bool:
    """Whether a written causes section talks about this candidate."""
    lower = text.lower()
    if any(tag.lower() in lower for tag in candidate.get("tags") or []):
        return True
    words = [w for w in re.findall(r"[a-z]{4,}", candidate["statement"].lower().split(":")[0].split(".")[0])
             if w not in {"outside", "normal", "trip", "motor", "this", "with", "from"}]
    equipment = (candidate.get("equipment") or "").lower()
    if equipment and equipment in lower and any(w in lower for w in words):
        return True
    return bool(words) and all(w in lower for w in words[:2])
