"""Replaceable industrial relationship graph; NetworkX is optional at runtime.

build_plant_graph() is generated from the incident catalog and, when an incident
is given, from its stored record. Every link states its basis:
  configured  plant structure from the catalog;
  recorded    seen in this incident's data;
  hypothesis  a possible cause from the Evidence Engine, never a proven one.
"""
from typing import Any, Dict, List, Optional


class IndustrialKnowledgeGraph:
    """Relationships for the orchestrator and Ask OpsLens, generated from the
    same catalog as the knowledge graph page (it replaced an earlier hard-coded
    Pump-02 prototype that matched none of the plant's scenarios)."""

    def context(self, equipment: Optional[str], tags: List[str]) -> List[str]:
        return incident_links(equipment, tags)


def incident_links(equipment: Optional[str], tags, limit: int = 14) -> List[str]:
    """Configured relationships around an incident: which sensors sit on the
    equipment, which reading may influence which, which alarm comes from which
    sensor, and which SOP covers the alarm. Plain lines for the AI."""
    import incident_catalog as catalog
    tags = set(tags or [])
    signals = catalog.plant_signals()
    statuses = catalog.plant_statuses()
    alarms = catalog.alarm_inputs()
    lines: List[str] = []
    if equipment:
        on_equipment = [f"{t} {s.label}" for t, s in signals.items() if s.equipment_id == equipment]
        on_equipment += [f"{t} {s.label}" for t, s in statuses.items() if s.equipment_id == equipment and s.kind == "status"]
        if on_equipment:
            lines.append(f"{equipment} has sensors: " + "; ".join(on_equipment))
    for tag in sorted(tags):
        signal = signals.get(tag)
        for upstream in getattr(signal, "upstream", ()) or ():
            if upstream in signals:
                lines.append(f"{upstream} {signals[upstream].label} may influence {tag} {signal.label}")
    for tag in sorted(tags):
        alarm = alarms.get(tag)
        if alarm and alarm.get("source_tag"):
            lines.append(f"Alarm {tag} {alarm['label']} is raised from sensor {alarm['source_tag']}")
    for spec in catalog.SCENARIOS:
        if spec.primary_alarm in tags:
            lines.append(f"Alarm {spec.primary_alarm} is covered by {spec.sop_id} {spec.sop_title}" if spec.sop_id
                         else f"Alarm {spec.primary_alarm} is not covered by any SOP in the manual")
    return list(dict.fromkeys(lines))[:limit]


PLANT_NODE = "plant:wastewater"
AREAS = {
    "InletWorks-01": "Preliminary treatment", "LiftStation-01": "Preliminary treatment",
    "AerationBasin-01": "Biological treatment", "AerationBlower-01": "Biological treatment",
    "SecondaryClarifier-01": "Biological treatment", "RASSystem-01": "Biological treatment",
    "BiogasSystem-01": "Gas line", "DewateringCentrifuge-01": "Sludge line", "Effluent-01": "Effluent",
    "UVChannel-01": "Disinfection",  # after tertiary filtration, before discharge (SOP-MAN-000 §2, p.2)
}


def build_plant_graph(full_event: Optional[Dict[str, Any]] = None, db=None) -> Dict[str, Any]:
    """Plant -> area -> equipment -> sensor -> alarm -> incident -> possible cause -> SOP."""
    import alarm_config
    import incident_catalog as catalog

    nodes: Dict[str, Dict[str, Any]] = {}
    edges: Dict[tuple, Dict[str, Any]] = {}

    def node(node_id: str, kind: str, label: str, **details: Any) -> str:
        nodes.setdefault(node_id, {"id": node_id, "kind": kind, "label": label, "details": details})
        return node_id

    def edge(source: str, target: str, relation: str, basis: str = "configured", **details: Any) -> None:
        edges[(source, target, relation)] = {"source": source, "target": target, "relation": relation,
                                             "basis": basis, "details": details}

    plant = node(PLANT_NODE, "plant", "Wastewater plant")
    for equipment, description in catalog.PLANT_EQUIPMENT.items():
        area = AREAS.get(equipment, "Other")
        area_id = node(f"area:{area}", "area", area)
        edge(plant, area_id, "has_area")
        edge(area_id, node(f"equipment:{equipment}", "equipment", equipment, description=description), "has_equipment")
    for tag, signal in catalog.plant_signals().items():
        sensor = node(f"tag:{tag}", "sensor", signal.label, tag=tag, unit=signal.unit, normal=signal.normal_text,
                      limits=[limit.label for limit in signal.limits])
        edge(f"equipment:{signal.equipment_id}", sensor, "has_sensor")
    for tag, status in catalog.plant_statuses().items():
        if status.kind == "status":
            edge(f"equipment:{status.equipment_id}", node(f"tag:{tag}", "sensor", status.label, tag=tag, unit="status"), "has_status")
    for tag, alarm in catalog.alarm_inputs().items():
        alarm_id = node(f"alarm:{tag}", "alarm", alarm["label"], tag=tag, threshold=alarm.get("threshold"), direction=alarm.get("direction"))
        source = f"tag:{alarm['source_tag']}" if alarm.get("source_tag") else f"equipment:{alarm['equipment_id']}"
        if source in nodes:
            edge(source, alarm_id, "raises")
        else:
            edge(f"equipment:{alarm['equipment_id']}", alarm_id, "raises")
    for spec in catalog.SCENARIOS:
        if not spec.sop_id:
            continue  # no SOP in the manual for this alarm
        sop = node(f"sop:{spec.sop_id}", "sop", spec.sop_id, title=spec.sop_title, approved=False, approval="DRAFT — NOT APPROVED")
        edge(f"alarm:{spec.primary_alarm}", sop, "procedure")
    for tag, signal in catalog.plant_signals().items():
        for upstream in signal.upstream:
            if f"tag:{upstream}" in nodes:
                edge(f"tag:{upstream}", f"tag:{tag}", "may_influence")

    path: List[str] = []
    breadcrumb: List[str] = []
    if full_event:
        path = _add_incident(full_event, node, edge, nodes, catalog, alarm_config, db)
        breadcrumb = [nodes[i]["label"] for i in path if nodes[i]["kind"] in ("plant", "area", "equipment")]
    return {
        "nodes": list(nodes.values()), "edges": list(edges.values()), "incident_path": path, "breadcrumb": breadcrumb,
        "generated_from": "incident_catalog.py and the incident's stored record" if full_event else "incident_catalog.py",
        "legend": {"configured": "Plant structure from the catalog", "recorded": "Seen in this incident's records",
                   "hypothesis": "Possible cause, not confirmed", "procedure": "From the SOP (advisory)"},
    }


def _short(statement: str) -> str:
    return statement.split(":")[0].split(". ")[0].split(", as a cause")[0].rstrip(".")


def _add_incident(full_event, node, edge, nodes, catalog, alarm_config, db=None) -> List[str]:
    event_id = full_event.get("event_id")
    candidate = full_event.get("candidate") or {}
    primary = full_event.get("primary_tag") or candidate.get("primary_tag")
    spec = catalog.scenario_for_incident(primary, candidate.get("clustered_tags"))
    incident = node(f"incident:{event_id}", "incident", f"Incident: {catalog.alarm_inputs().get(primary, {}).get('label', primary)}",
                    event_id=event_id, status=full_event.get("status"), trigger_timestamp=full_event.get("trigger_timestamp"))
    alarm_node = f"alarm:{primary}"
    if alarm_node not in nodes:
        node(alarm_node, "alarm", primary, tag=primary)
    edge(alarm_node, incident, "triggered", "recorded", timestamp=full_event.get("trigger_timestamp"))
    for alarm in full_event.get("individual_alarms") or []:
        other = f"alarm:{alarm.get('tag')}"
        if other in nodes and other != alarm_node:
            edge(other, incident, "part_of", "recorded", lifecycle_state=alarm.get("lifecycle_state"))
    for step in (full_event.get("story") or {}).get("steps") or []:
        sensor = f"tag:{step.get('tag')}"
        if sensor in nodes:
            edge(sensor, incident, "recorded_in", "recorded", timestamp=step.get("timestamp"),
                 change=f"{step.get('value_before')} -> {step.get('value_after')} {step.get('unit') or ''}".strip())

    facts = None
    if db is not None:
        try:
            import ai_diagnosis
            facts = ai_diagnosis.build_facts(full_event, db)
        except Exception:
            facts = None

    extra: List[str] = []
    causes: List[str] = []
    sop_id = (full_event.get("sop_match") or {}).get("sop_id") or ((facts or {}).get("sop") or {}).get("sop_id")
    sop = f"sop:{sop_id}" if f"sop:{sop_id}" in nodes else None
    if facts:
        # Sensor state on each node: past an SOP limit, outside normal, normal, or no data.
        seen = set()
        for r in facts.get("readings") or []:
            sensor = f"tag:{r['tag']}"
            if sensor in nodes:
                seen.add(r["tag"])
                state = {"LIMIT EXCEEDED": "limit", "outside normal": "abnormal"}.get(r.get("state_at_worst"), "normal")
                nodes[sensor]["state"] = state
                nodes[sensor]["details"].update({"worst": f"{r['worst']} {r.get('unit') or ''}".strip(),
                                                 "now": f"{r['now']} {r.get('unit') or ''}".strip(),
                                                 "state at worst": r.get("state_at_worst"), "state now": r.get("state_now")})
                if state != "normal":
                    extra.append(sensor)
        for tag in facts.get("incident_tags") or []:
            sensor = f"tag:{tag}"
            if sensor in nodes and tag not in seen and nodes[sensor]["details"].get("unit") != "status":
                nodes[sensor]["state"] = "no_data"
                nodes[sensor]["details"]["data"] = "No recorded readings for this incident"
                edge(sensor, incident, "expected_in", "recorded")
                extra.append(sensor)
        # The leading and contributing causes from the record (the same ones every
        # screen shows), with the SOP steps that address them. Likely effects are
        # readings, not causes: they stay on their sensor with a note.
        import incident_insight
        from cause_candidates import ROLE_LABELS
        steps = (facts.get("sop") or {}).get("steps") or []
        all_candidates = facts.get("cause_candidates") or []
        lead_short = (all_candidates[0].get("short") if all_candidates else None) or "the leading cause"
        for c in all_candidates:
            if c.get("role") == "effect":
                for tag in c.get("tags") or []:
                    if f"tag:{tag}" in nodes:
                        nodes[f"tag:{tag}"]["details"]["possible consequence of"] = lead_short
                        nodes[f"tag:{tag}"]["role"] = "effect"
            elif c.get("role") == "insufficient":
                for tag in c.get("tags") or []:
                    if f"tag:{tag}" in nodes:
                        nodes[f"tag:{tag}"]["details"]["insufficient evidence"] = c.get("role_note") or "Related readings stayed normal."
                        nodes[f"tag:{tag}"]["role"] = "insufficient"
        for c in [c for c in all_candidates if c.get("role") not in ("effect", "insufficient")]:
            role = ROLE_LABELS.get(c.get("role"), "Possible cause")
            cause = node(f"cause:{event_id}:{c['rank']}", "cause", f"{role}: {c.get('short') or _short(c['statement'])}",
                         statement=c["statement"], role=role, why=c.get("role_note"), evidence_type=c["kind"], basis=c["basis"],
                         supporting=c.get("supporting"), contradicting=c.get("contradicting"),
                         note="A hypothesis from the record. Not confirmed.")
            nodes[cause]["role"] = c.get("role")
            nodes[cause]["rank"] = c["rank"]
            nodes[cause]["support_count"] = len(c.get("supporting") or [])
            causes.append(cause)
            edge(incident, cause, "possible_cause", "hypothesis", rank=c["rank"])
            for tag in c.get("tags") or []:
                for source in (f"tag:{tag}", f"alarm:{tag}"):
                    if source in nodes:
                        edge(source, cause, "supports", "hypothesis")
                        if source not in extra:
                            extra.append(source)
                        break
            ranked = sorted((s for s in steps if incident_insight._step_score(s["text"], c) > 0),
                            key=lambda s: (-incident_insight._step_score(s["text"], c), s["n"]))[:1]  # the best match, to keep the graph readable
            for s in ranked:
                fix = node(f"step:{sop_id}:{s['n']}", "fix", f"SOP step {s['n']} (p.{s['page']})", text=s["text"],
                           sop=sop_id, approval=(facts.get("sop") or {}).get("status"))
                edge(cause, fix, "addressed_by", "procedure")
                if sop:
                    edge(fix, sop, "from_procedure", "procedure")
                extra.append(fix)
        checks = facts.get("verification") or []
        if checks:
            check = node(f"check:{event_id}", "check", "Recovery check", criteria=checks,
                         note="From the SOP. Confirm resolved stays locked until the recovery criteria pass.")
            edge(sop or incident, check, "recovered_when", "procedure")
            extra.append(check)
    else:
        evidence = full_event.get("evidence") or {}
        contributor = evidence.get("likely_contributor_tag")
        if contributor:
            label = catalog.alarm_inputs().get(contributor, {}).get("label") or catalog.plant_signals().get(
                contributor, type("S", (), {"label": contributor})).label
            cause = node(f"cause:{event_id}", "cause", f"Possible cause: {label}", tag=contributor,
                         ranking_score=evidence.get("confidence_score"),
                         note="Heuristic ranking score, not a probability. Not confirmed.")
            causes.append(cause)
            edge(incident, cause, "possible_cause", "hypothesis", ranking_score=evidence.get("confidence_score"))
            for source in (f"tag:{contributor}", f"alarm:{contributor}"):
                if source in nodes:
                    edge(source, cause, "supports", "hypothesis")
                    break
    if sop:
        for cause in causes or [incident]:
            edge(cause, sop, "see_procedure", "hypothesis" if causes else "recorded")

    equipment = spec.equipment_id if spec else None
    source = catalog.alarm_inputs().get(primary, {}).get("source_tag")
    area = AREAS.get(equipment or "")
    path = [PLANT_NODE, f"area:{area}" if area else None, f"equipment:{equipment}" if equipment else None,
            f"tag:{source}" if source and f"tag:{source}" in nodes else None, alarm_node, incident, *causes, sop, *extra]
    return list(dict.fromkeys(item for item in path if item and item in nodes))
