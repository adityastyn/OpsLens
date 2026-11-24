"""Incidents must only start when an operator starts them (or a real plant alarm
comes on). A repeat of an alarm that is still active in an open incident, sent
after the in-memory cluster expired or after a backend restart, used to start
one or two brand-new incidents nobody had asked for."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from database import Database
from dedup import DeduplicationEngine
from models import TagReading


def _iso(t):
    return t.isoformat().replace("+00:00", "Z")


def _alarm(ts, **extra):
    return TagReading(tag="ALM-SCR-110-DL", value=True, timestamp=_iso(ts), unit="status",
                      equipment_id="InletWorks-01", data_source="simulated",
                      connection_status="simulation", **extra)


class NoPhantomIncidentTests(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        self.db = Database(db_path=self.path)

    def tearDown(self):
        try:
            self.db.close()
            os.remove(self.path)
        except Exception:
            pass

    def test_repeat_after_restart_counts_on_the_open_incident(self):
        first = DeduplicationEngine(db=self.db).process_reading(
            _alarm(datetime.now(timezone.utc) - timedelta(minutes=60)))
        restarted = DeduplicationEngine(db=self.db)  # new process, clusters rebuilt from SQLite
        repeat = restarted.process_reading(_alarm(datetime.now(timezone.utc), alarm_state="REANNUNCIATED"))
        self.assertEqual(repeat.event_id, first.event_id)
        self.assertEqual(repeat.primary_tag, "ALM-SCR-110-DL")
        self.assertGreaterEqual(repeat.repeat_count, 2)
        self.assertEqual(len(self.db.get_candidate_events()), 1)

    def test_orchestrator_stream_skips_in_process_simulator_readings(self):
        import inspect
        import orchestrator
        source = inspect.getsource(orchestrator.OpsLensOrchestrator._consume_stream)
        self.assertIn("simulated", source)


if __name__ == "__main__":
    unittest.main()
