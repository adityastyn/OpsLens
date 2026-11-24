"""Measure how fast OpsLens assembles incident context on YOUR database.

    cd edge_intelligence
    .venv\\Scripts\\python tools\\measure_latency.py            (uses OPSLENS_DB_PATH or opslens.db)
    .venv\\Scripts\\python tools\\measure_latency.py path\\to.db

For each incident it times, in milliseconds:
  context  - the 30-minute context window (alarms, time series, actions) for Ask OpsLens
  facts    - everything the AI diagnosis is given (readings, causes, SOP, history)
  insight  - the bottom line, confidence rating and history (shown before the AI)
Target: under 2000 ms each. No model is called and nothing is written.
"""
import os
import sqlite3
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OPSLENS_LLM_PROVIDER", "none")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("OPSLENS_DB_PATH") or str(ROOT / "opslens.db")
    if not Path(path).exists():
        print(f"Database not found: {path}")
        return 2
    from context_builder import ContextBuilder
    from database import Database
    from orchestrator import OpsLensOrchestrator
    import ai_diagnosis
    import incident_insight

    db = Database(db_path=path)
    orch = OpsLensOrchestrator(db=db)
    builder = ContextBuilder(db=db, event_loader=orch.get_full_event)
    rows = sqlite3.connect(path).execute("SELECT COUNT(*) FROM tag_readings").fetchone()[0]
    ids = [r[0] for r in sqlite3.connect(path).execute(
        "SELECT event_id FROM correlated_events ORDER BY trigger_timestamp DESC LIMIT 10")]
    print(f"Database: {path}\n{rows:,} stored readings, measuring {len(ids)} recent incident(s)\n")

    def timed(fn):
        started = time.perf_counter()
        fn()
        return round((time.perf_counter() - started) * 1000)

    results = {"context": [], "facts": [], "insight": []}
    for event_id in ids:
        event = orch.get_full_event(event_id)
        if not event:
            continue
        row = {
            "context": timed(lambda: builder.build(event_id, None, event_record=event)),
            "facts": timed(lambda: ai_diagnosis.build_facts(event, db)),
            "insight": timed(lambda: incident_insight.build_insight(event, db)),
        }
        for key, value in row.items():
            results[key].append(value)
        print(f"{str(event.get('primary_tag'))[:22]:<22} " + "  ".join(f"{k} {v:>5} ms" for k, v in row.items()))
    print()
    for key, values in results.items():
        if values:
            worst = max(values)
            print(f"{key:<8} median {statistics.median(values):>6.0f} ms   worst {worst:>6} ms   "
                  f"{'OK, under 2 s' if worst < 2000 else 'OVER the 2 s target'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
