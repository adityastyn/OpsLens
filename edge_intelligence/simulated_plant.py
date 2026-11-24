"""State of the simulated plant across Scenario Lab runs.

A real plant does not repair one fault because a different fault appears. Each
scenario that finishes stays "unfixed" here until its simulated corrective
action runs, so its alarms stay active and its readings stay as they were while
other scenarios run. Simulator state only; nothing here talks to a PLC.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

import alarm_config
import incident_catalog as catalog

logger = logging.getLogger("opslens.simulated_plant")


def _is_true(value: Any) -> bool:
    return value is True or str(value).strip().lower() in ("true", "1", "1.0")


class SimulatedPlant:
    def __init__(self) -> None:
        self._unfixed: Dict[str, Any] = {}
        self._events: Dict[str, Optional[str]] = {}
        self._loaded = False

    def clear(self) -> None:
        self._unfixed.clear()
        self._events.clear()
        self._loaded = False

    def register(self, scenario: Any, event_id: Optional[str]) -> None:
        self._unfixed[scenario.scenario_id] = scenario
        self._events[scenario.scenario_id] = event_id

    def mark_fixed(self, scenario_id: str) -> None:
        self._unfixed.pop(scenario_id, None)
        self._events.pop(scenario_id, None)

    def get(self, scenario_id: Optional[str]) -> Any:
        return self._unfixed.get(scenario_id or "")

    def event_for(self, scenario_id: str) -> Optional[str]:
        return self._events.get(scenario_id)

    def scenario_for_event(self, event_id: str) -> Optional[str]:
        return next((sid for sid, eid in self._events.items() if eid == event_id and event_id), None)

    def unfixed(self) -> List[Dict[str, Optional[str]]]:
        return [{"scenario_id": sid, "event_id": self._events.get(sid)} for sid in self._unfixed]

    def load_from_db(self, db) -> None:
        """After a backend restart, rebuild what is still broken from the record.
        Reads only; a scenario counts as unfixed if an alarm of its own is active."""
        if self._loaded:
            return
        self._loaded = True
        try:
            from scenarios.incident_scenarios import CatalogScenario
            candidates = {c.primary_tag: c for c in db.get_candidate_events()}
            for spec in catalog.SCENARIOS:
                if spec.scenario_id in self._unfixed:
                    continue
                candidate = candidates.get(spec.primary_alarm)
                latest = db.get_latest_reading(spec.primary_alarm)
                if candidate is None or latest is None or not _is_true(latest.value):
                    continue
                scenario = CatalogScenario(spec.scenario_id)
                for tag in catalog.scenario_tags(spec):
                    reading = db.get_latest_reading(tag)
                    if reading is not None:
                        scenario._last_values[tag] = reading.value
                self.register(scenario, candidate.event_id)
        except Exception as exc:
            logger.warning("Simulated plant state could not be rebuilt: %s: %s", type(exc).__name__, exc)

    def holds(self, db, except_scenario: Optional[str]) -> Tuple[Set[str], Set[str]]:
        """Tags to leave alone, and which of them are alarms still TRUE."""
        tags: Set[str] = set()
        for scenario_id in self._unfixed:
            if scenario_id != except_scenario:
                tags.update(catalog.scenario_tags(catalog.SCENARIO_BY_ID[scenario_id]))
        active = set()
        for tag in tags:
            if alarm_config.is_alarm_tag(tag):
                latest = db.get_latest_reading(tag)
                if latest is not None and _is_true(latest.value):
                    active.add(tag)
        return tags, active
