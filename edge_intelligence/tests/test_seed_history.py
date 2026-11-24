"""Past-month history: seeded through the real pipeline, labelled, resolved,
never replayed as live readings, and removable."""
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import Database  # noqa: E402
from operational_history import build_handover_summary  # noqa: E402
import seed_history  # noqa: E402
from stream import SQLitePollingBroker  # noqa: E402


def _insert_reading(path: Path, source: str, value: float = 1.0) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute(
            """INSERT INTO tag_readings (tag, value, value_raw, timestamp, unit, equipment_id,
                   received_timestamp, data_quality, data_source, connection_status)
               VALUES ('LT-101', ?, ?, ?, 'm', 'LiftStation-01', ?, 'VALID', ?, 'simulated')""",
            (value, str(value), now, now, source))


class StreamSkipsSeededHistory(unittest.TestCase):
    def test_live_stream_never_replays_seeded_readings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.db"
            Database(db_path=path)
            _insert_reading(path, seed_history.HISTORY_SOURCE, 9.9)
            _insert_reading(path, "Scenario Lab", 1.5)
            _insert_reading(path, seed_history.HISTORY_SOURCE, 9.8)
            rows = SQLitePollingBroker(path)._fetch_rows_after(0)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][2], 1.5)


class SeedHistory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "plant.db"
        Database(db_path=cls.path)
        cls.result = seed_history.seed(cls.path, log=lambda *_: None)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _q(self, sql, *args):
        with sqlite3.connect(self.path) as conn:
            return conn.execute(sql, args).fetchall()

    def test_incidents_are_seeded_over_the_past_month(self):
        self.assertEqual(self.result["status"], "seeded")
        self.assertGreaterEqual(self.result["incidents"], 20)
        self.assertEqual(seed_history.seeded_count(self.path), self.result["incidents"])
        self.assertEqual(seed_history.seed(self.path, log=lambda *_: None)["status"], "already_seeded")

    def test_seeded_incidents_are_resolved_not_open(self):
        # The same summary the shift panel and handover use.
        summary = build_handover_summary(Database(db_path=self.path), None, None)
        self.assertEqual(summary.get("unresolved_incidents") or [], [])
        self.assertEqual(summary.get("open_alarms") or [], [])

    def test_seeded_incidents_are_in_the_past_and_spread_out(self):
        now = datetime.now(timezone.utc)
        times = sorted(datetime.fromisoformat(r[0].replace("Z", "+00:00")) for r in self._q(
            "SELECT c.trigger_timestamp FROM correlated_events c JOIN seeded_history s USING (event_id)"))
        self.assertTrue(times)
        self.assertTrue(all(now - timedelta(days=31) <= t <= now - timedelta(hours=12) for t in times), times[:3])
        self.assertGreater(times[-1] - times[0], timedelta(days=14))

    def test_seeded_readings_are_labelled_and_valid(self):
        labels = {r[0] for r in self._q("SELECT DISTINCT data_source FROM tag_readings")}
        self.assertEqual(labels, {seed_history.HISTORY_SOURCE})
        # They load as TagReading (data_quality must be one the model accepts).
        readings = Database(db_path=self.path).get_readings(limit=500)
        self.assertEqual(len(readings), 500)

    def test_remove_takes_it_all_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "copy.db"
            with sqlite3.connect(self.path) as src, sqlite3.connect(copy) as dst:
                src.backup(dst)
            _insert_reading(copy, "Scenario Lab", 2.0)
            seed_history.remove(copy)
            self.assertEqual(seed_history.seeded_count(copy), 0)
            with sqlite3.connect(copy) as conn:
                left = conn.execute("SELECT data_source FROM tag_readings").fetchall()
            self.assertEqual(left, [("Scenario Lab",)])  # live readings stay


if __name__ == "__main__":
    unittest.main()
