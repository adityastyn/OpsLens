"""One month of SIMULATED past incidents, so Related Incidents, the alarm
history and the shift pages have a realistic past to show.

How it works, so nothing live is disturbed:
  1. In a separate process and a temporary database, each of the 8 catalog
     scenarios is run 2-3 times at past times over the last 30 days, through
     the normal OpsLens pipeline (alarms, event story, evidence, SOP match).
  2. Each past incident is acknowledged, fixed (simulated recovery) and
     resolved at past times, with a short note of what was found.
  3. Normal-range readings every 30 minutes are added for every plant signal,
     so trends over the month are not empty.
  4. Everything is copied into the real database, labelled "simulated
     history", and each incident is listed in the `seeded_history` table.

Seeded incidents are simulated, never live plant data. They are left out of
the Metrics page's time-to-resolution, because OpsLens did not handle them.
Remove them with `python seed_history.py --remove`.

Usage:
  python seed_history.py                  # into edge_intelligence/opslens.db
  python seed_history.py --db path\\to.db  # another database
  python seed_history.py --remove         # delete seeded history only
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

HISTORY_SOURCE = "simulated history"
DAYS = 30
RUNS_PER_SCENARIO = 3
OPERATORS = ("A-shift operator", "B-shift operator", "C-shift operator")
TABLES = ("candidate_events", "individual_alarms", "alarm_occurrences", "alarm_transitions", "correlated_events",
          "event_stories", "evidence", "sop_matches", "guidance", "operator_actions")


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _marker_table(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS seeded_history (
                        event_id TEXT PRIMARY KEY, scenario_id TEXT, seeded_at TEXT NOT NULL)""")


def seeded_count(db_path: Path) -> int:
    if not Path(db_path).exists():
        return 0
    with sqlite3.connect(str(db_path)) as conn:
        _marker_table(conn)
        return conn.execute("SELECT COUNT(*) FROM seeded_history").fetchone()[0]


def plan(now: datetime, rng: random.Random) -> List[Dict[str, Any]]:
    """Past start times: 2-3 runs per scenario, spread over the month, never in the last 12 hours."""
    import incident_catalog
    runs = []
    for spec in incident_catalog.SCENARIOS:
        count = RUNS_PER_SCENARIO if spec.sop_id else 2
        days = sorted(rng.sample(range(1, DAYS), count))
        for day in days:
            start = (now - timedelta(days=day)).replace(hour=rng.randrange(0, 24), minute=rng.randrange(0, 60), second=0, microsecond=0)
            start = min(start, now - timedelta(hours=12))
            runs.append({"scenario": spec, "start": start, "ack_min": rng.uniform(1.0, 4.5),
                         "fix_min": rng.uniform(8.0, 38.0), "operator": rng.choice(OPERATORS)})
    runs.sort(key=lambda r: r["start"])
    # Keep runs at least 2 hours apart so no two past incidents overlap.
    for earlier, later in zip(runs, runs[1:]):
        if later["start"] - earlier["start"] < timedelta(hours=2):
            later["start"] = earlier["start"] + timedelta(hours=2)
    return runs


# What the crew did, as an operator would write it in the resolution note.
# Simulated history; matches each scenario's scripted cause and recovery.
FIX_NOTES = {
    "screen_differential_pressure": "Isolated BS-2 under LOTO, cleared the rag mat from the rake, put it back in service. Channel below 1.2 m and differential below 15 cm.",
    "wet_well_high_level": "Called the tanker, lifted P-102 and cleared rags from the impeller, let P-101 cool and reset it after checking insulation. Wet well back below 2.4 m.",
    "biological_foam": "Tank 3 foam from high sludge age; raised WAS in steps over 3 days. Foam below 5 cm and sludge age back near 11 days.",
    "ras_pump_failure": "Ran RAS-P2 at maximum with a portable pump; RAS-P1 motor had overheated, cooled and repaired it. Sludge blanket back below 0.8 m.",
    "biogas_holder_pressure": "Confirmed the flare lit, set up the exclusion zone; technician replaced the faulty spark plug sensor and restarted the CHP. Holder back to 15–25 mbar.",
    "centrifuge_capture": "Bowl speed and polymer dose had been set wrongly after a repair; restored the approved values, held centrate and bled it back over 24 h. Inlet ammonia back below 42 mg/L.",
    "aeration_blower_failure": "B-1 tripped on overload and standby B-3 did not auto-start; started B-3 by hand under the site procedure, then checked and reset B-1. DO back to 1.5–3.0 mg/L.",
    "uv_disinfection_low": "Maintenance electrician replaced the lamp bank 2 ballast; bank 2 back on, UV intensity and dose back in range.",
}


async def _build(tmp_db: Path, runs: List[Dict[str, Any]], log) -> List[Dict[str, Any]]:
    os.environ["OPSLENS_LLM_PROVIDER"] = "none"        # never call an AI while seeding
    os.environ["OPSLENS_INDEX_PLANT_SOPS"] = "false"
    import incident_catalog
    from database import Database
    from generator import SyntheticDataGenerator
    from orchestrator import OpsLensOrchestrator

    db = Database(db_path=tmp_db)
    orch = OpsLensOrchestrator(db=db)
    done = []
    try:
        for index, run in enumerate(runs, start=1):
            spec, start = run["scenario"], run["start"]
            before = {c.event_id for c in db.get_candidate_events()}
            gen = SyntheticDataGenerator(db=db, sample_interval=2.0, start_time=start)
            gen.only_tags = set(incident_catalog.scenario_tags(spec))
            async for reading in gen.run_scenario_async(spec.scenario_id, speed_multiplier=1e9):
                await orch.process_reading(reading)
            if orch._in_flight_tasks:
                await asyncio.gather(*orch._in_flight_tasks.values(), return_exceptions=True)
            new = [c for c in db.get_candidate_events() if c.event_id not in before]
            for candidate in new:
                await orch.process_candidate_event(candidate)
            event = next((c for c in new if c.primary_tag == spec.primary_alarm), new[-1] if new else None)
            if event is None:
                log(f"  {index}/{len(runs)} {spec.name}: no incident, skipped")
                continue
            alarm_time = datetime.fromisoformat(str(event.trigger_timestamp).replace("Z", "+00:00"))
            operator = run["operator"]
            ack_at = alarm_time + timedelta(minutes=run["ack_min"])
            for alarm in db.get_individual_alarms(event.event_id):
                db.record_alarm_acknowledgement(event_id=event.event_id, alarm_id=alarm["alarm_id"], operator=operator,
                                                notes="Acknowledged at the panel.", timestamp=_iso(ack_at))
            fix_at = alarm_time + timedelta(minutes=run["fix_min"])
            found = "; ".join(str(v) for v in (spec.root or {}).values())[:300]
            orch.audit.log_action(event_id=event.event_id, operator=operator, action_type="INSPECT",
                                  notes=f"Field check: {found}" if found else "Field check completed.",
                                  timestamp=_iso(fix_at - timedelta(minutes=3)))
            gen.sim_clock = fix_at
            async for reading in gen.run_recovery_async(speed_multiplier=1e9):
                await orch.process_reading(reading)
            if orch._in_flight_tasks:
                await asyncio.gather(*orch._in_flight_tasks.values(), return_exceptions=True)
            resolve_at = gen.sim_clock + timedelta(minutes=2)
            fix = FIX_NOTES.get(spec.scenario_id) or spec.expected_outcome
            notes = (f"Followed {spec.sop_id}: {fix}" if spec.sop_id else f"No SOP; escalated to the shift supervisor. {fix}")
            action = orch.audit.log_action(event_id=event.event_id, operator=operator, action_type="RESOLVE",
                                           notes=notes, timestamp=_iso(resolve_at))
            for alarm in db.get_individual_alarms(event.event_id):
                db.record_alarm_transition(alarm["alarm_id"], event.event_id, "RESOLVED", operator, action.timestamp, notes)
            done.append({"event_id": event.event_id, "scenario_id": spec.scenario_id})
            minutes = (resolve_at - alarm_time).total_seconds() / 60
            log(f"  {index}/{len(runs)} {start:%d %b %H:%M} {spec.name}: resolved in {minutes:.0f} min")
    finally:
        await orch.stop()
        db.close()  # Windows cannot delete the temporary database while it is open
    return done


def _background(tmp_db: Path, runs: List[Dict[str, Any]], now: datetime, rng: random.Random) -> int:
    """Normal-range readings every 30 minutes for every plant signal, outside the incident windows."""
    import incident_catalog
    busy: Dict[str, List[tuple]] = {}
    for run in runs:
        for tag in incident_catalog.scenario_tags(run["scenario"]):
            busy.setdefault(tag, []).append((run["start"] - timedelta(hours=1), run["start"] + timedelta(hours=2)))
    rows = []
    start = now - timedelta(days=DAYS)
    steps = int(DAYS * 24 * 2)
    for tag, sig in incident_catalog.plant_signals().items():
        low, high = sig.normal if isinstance(sig.normal, tuple) else (None, None)
        if low is None or high is None:
            band = sig.noise_band
            if not band:
                continue
            low, high = band
        mid, half = (low + high) / 2, (high - low) / 2
        value = mid
        for i in range(steps):
            t = start + timedelta(minutes=30 * i)
            if t > now - timedelta(hours=12) or any(a <= t <= b for a, b in busy.get(tag, ())):
                continue
            value = max(low, min(high, value + rng.uniform(-0.15, 0.15) * half + (mid - value) * 0.2))
            rows.append((tag, round(value, 3), str(round(value, 3)), _iso(t), sig.unit, sig.equipment_id,
                         _iso(t), "VALID", HISTORY_SOURCE, "simulated"))
    with sqlite3.connect(str(tmp_db)) as conn:
        conn.executemany("""INSERT INTO tag_readings (tag, value, value_raw, timestamp, unit, equipment_id,
                            received_timestamp, data_quality, data_source, connection_status)
                            VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
    return len(rows)


def _copy(tmp_db: Path, target: Path, done: List[Dict[str, Any]]) -> None:
    with sqlite3.connect(str(target), timeout=30) as conn:
        _marker_table(conn)
        conn.execute("ATTACH DATABASE ? AS seed", (str(tmp_db),))
        conn.execute("UPDATE seed.tag_readings SET data_source = ?", (HISTORY_SOURCE,))
        conn.execute("UPDATE seed.alarm_occurrences SET data_source = ?", (HISTORY_SOURCE,))
        for table in TABLES:
            main_cols = [r[1] for r in conn.execute(f"PRAGMA main.table_info({table})")]
            seed_cols = {r[1] for r in conn.execute(f"PRAGMA seed.table_info({table})")}
            cols = [c for c in main_cols if c in seed_cols and c != "id"]
            if cols:
                names = ", ".join(cols)
                conn.execute(f"INSERT OR IGNORE INTO main.{table} ({names}) SELECT {names} FROM seed.{table}")
        cols = [r[1] for r in conn.execute("PRAGMA main.table_info(tag_readings)") if r[1] != "id"]
        names = ", ".join(cols)
        conn.execute(f"INSERT INTO main.tag_readings ({names}) SELECT {names} FROM seed.tag_readings")
        seeded_at = _iso(datetime.now(timezone.utc))
        conn.executemany("INSERT OR IGNORE INTO seeded_history (event_id, scenario_id, seeded_at) VALUES (?,?,?)",
                         [(d["event_id"], d["scenario_id"], seeded_at) for d in done])
        conn.commit()
        conn.execute("DETACH DATABASE seed")


def remove(target: Path) -> int:
    """Delete seeded history only: its incidents, their records and the history readings."""
    with sqlite3.connect(str(target), timeout=30) as conn:
        _marker_table(conn)
        ids = [r[0] for r in conn.execute("SELECT event_id FROM seeded_history")]
        for table, column in (("candidate_events", "event_id"), ("correlated_events", "event_id"), ("event_stories", "event_id"),
                              ("evidence", "event_id"), ("sop_matches", "event_id"), ("guidance", "event_id"),
                              ("operator_actions", "event_id"), ("individual_alarms", "incident_id"),
                              ("alarm_occurrences", "incident_id"), ("alarm_transitions", "incident_id")):
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                conn.executemany(f"DELETE FROM {table} WHERE {column} = ?", [(i,) for i in ids])
        conn.execute("DELETE FROM tag_readings WHERE data_source = ?", (HISTORY_SOURCE,))
        conn.execute("DELETE FROM seeded_history")
        conn.commit()
    return len(ids)


def seed(target: Path, seed_value: int = 7, log=print) -> Dict[str, Any]:
    if seeded_count(target):
        return {"status": "already_seeded", "incidents": seeded_count(target)}
    rng = random.Random(seed_value)
    now = datetime.now(timezone.utc)
    runs = plan(now, rng)
    log(f"Seeding {len(runs)} past incidents over the last {DAYS} days (simulated history)...")
    # ignore_cleanup_errors: on Windows a file still held open would otherwise
    # make the run fail after the history was already copied.
    with tempfile.TemporaryDirectory(prefix="opslens-history-", ignore_cleanup_errors=True) as tmp:
        tmp_db = Path(tmp) / "history.db"
        done = asyncio.run(_build(tmp_db, runs, log))
        readings = _background(tmp_db, runs, now, rng)
        _copy(tmp_db, target, done)
    log(f"Done: {len(done)} past incidents and {readings} history readings added, labelled '{HISTORY_SOURCE}'.")
    return {"status": "seeded", "incidents": len(done), "history_readings": readings}


_RUN: Dict[str, Any] = {"status": "idle", "detail": None}


def start_background(db_path: Path) -> Dict[str, Any]:
    """Seed in a separate process (never inside the live backend), once."""
    import subprocess
    import threading
    if _RUN["status"] == "running":
        return dict(_RUN)
    if seeded_count(db_path):
        _RUN.update(status="done", detail=f"{seeded_count(db_path)} past incidents already seeded.")
        return dict(_RUN)

    def run():
        _RUN.update(status="running", detail="Adding one month of simulated past incidents...")
        print("[OpsLens] Adding one month of simulated past incidents (separate process, about 15-60 s)...", flush=True)
        try:
            done = subprocess.run([sys.executable, str(HERE / "seed_history.py"), "--db", str(db_path)],
                                  capture_output=True, text=True, timeout=900, cwd=str(HERE))
            # What counts is whether the history reached the database, not the exit code
            # (a temporary-file cleanup error on Windows can come after a good copy).
            if seeded_count(db_path):
                _RUN.update(status="done", detail=f"{seeded_count(db_path)} past incidents seeded (simulated history).")
            else:
                _RUN.update(status="failed", detail=(done.stderr or done.stdout or "seeding failed").strip()[-400:])
        except Exception as error:  # noqa: BLE001
            _RUN.update(status="failed", detail=f"{type(error).__name__}: {error}")
        print(f"[OpsLens] Past-month history: {_RUN['detail']}", flush=True)
        if _RUN["status"] == "failed":
            print("[OpsLens] Try again in Engineering Console (localhost:5174) > Scenario Lab > Past month of incidents > Add one month, or run:"
                  f" python seed_history.py --db \"{db_path}\"", flush=True)
    threading.Thread(target=run, name="seed-history", daemon=True).start()
    return dict(_RUN)


def run_state() -> Dict[str, Any]:
    return dict(_RUN)


def main() -> None:
    import logging
    logging.disable(logging.INFO)  # keep the output to one line per past incident
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.getenv("OPSLENS_DB_PATH") or str(HERE / "opslens.db"))
    parser.add_argument("--remove", action="store_true", help="delete seeded history only")
    args = parser.parse_args()
    target = Path(args.db)
    if args.remove:
        print(f"Removed {remove(target)} seeded past incidents and their history readings.")
        return
    from database import Database
    Database(db_path=target)  # make sure the tables exist
    print(seed(target))


if __name__ == "__main__":
    main()
