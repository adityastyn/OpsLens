"""Limit status on the time-series page: a reading already past a limit is an
active breach (measured), never an early warning; a forecast crossing is a
prediction; a tag with no limit says so instead of 'within limits'."""
import unittest

from analytics import limit_state

HIGH = [{"label": "3.2 m high", "value": 3.2, "direction": "high"}, {"label": "3.6 m high-high", "value": 3.6, "direction": "high"}]


class LimitState(unittest.TestCase):
    def test_active_breach_names_the_furthest_limit(self):
        s = limit_state([3.0, 3.7], 0.0, HIGH, None)
        self.assertEqual(s["state"], "ACTIVE_BREACH")
        self.assertEqual(s["limit"], "3.6 m high-high")
        self.assertIn("not a prediction", s["note"])

    def test_breach_with_rising_trend_is_worsening(self):
        self.assertEqual(limit_state([3.5, 3.7], 0.05, HIGH, None)["state"], "ACTIVE_BREACH_WORSENING")
        self.assertEqual(limit_state([3.8, 3.7], -0.05, HIGH, None)["state"], "ACTIVE_BREACH")

    def test_breach_wins_over_a_forecast_crossing(self):
        crossing = {"limit": "3.6 m high-high", "value": 3.6, "in_seconds": 40}
        self.assertEqual(limit_state([3.3], 0.02, HIGH, crossing)["state"], "ACTIVE_BREACH_WORSENING")

    def test_predicted_crossing_when_within_limits(self):
        s = limit_state([2.9, 3.0], 0.02, HIGH, {"limit": "3.2 m high", "value": 3.2, "in_seconds": 30})
        self.assertEqual(s["state"], "PREDICTED_LIMIT_CROSSING")
        self.assertIn("not a measurement", s["note"])

    def test_within_limits(self):
        self.assertEqual(limit_state([2.0], 0.0, HIGH, None)["state"], "WITHIN_LIMITS")

    def test_no_limit_is_not_called_within_limits(self):
        s = limit_state([610.0], 0.0, [], None)
        self.assertEqual(s["state"], "NO_LIMIT")
        self.assertEqual(s["text"], "NO ALARM LIMIT CONFIGURED")


if __name__ == "__main__":
    unittest.main()
