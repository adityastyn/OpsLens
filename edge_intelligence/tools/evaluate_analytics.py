"""Evaluate anomaly detection and forecasting on the seven simulated incidents.

    cd edge_intelligence
    python tools/evaluate_analytics.py

Generates each scenario into a throwaway database and scores the analytics
against the simulator's known timeline. These are results on SIMULATED data with
smooth ramps. They show the methods work as built, not how they will do on a
real plant. Prints a Markdown report; pass a path to also write it to a file.
"""
import sys
import tempfile
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import analytics  # noqa: E402
import incident_catalog as catalog  # noqa: E402
from database import Database  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from generator import SyntheticDataGenerator  # noqa: E402

HORIZON_S = 30.0


def main():
    lines = ["# Analytics evaluation (simulated data only)", "",
             "Each scenario: 60 samples at 2 s. Reproduce with `python tools/evaluate_analytics.py`; values vary slightly with simulator noise.",
             "These results come from smooth simulated ramps. They show the methods work as built, not how they would do on a real plant.", "",
             "**Anomaly detection.** Only the statistical detector is scored here, because the normal-band check is true by definition. "
             "False flags = samples flagged before the signal's ramp began. Lead = seconds between the first flag and the first limit crossing, on signals that have a limit.", "",
             f"**Forecast.** Horizon {HORIZON_S:.0f} s. Skill = 1 - MAE / (MAE of a no-change forecast). Above 0 beats no-change.", "",
             "| # | Scenario | Signals flagged | False flags (normal period) | Median lead before limit (s) | Forecast median skill | Beat no-change |",
             "|---|---|---|---|---|---|---|"]
    skills, leads, total_false, total_normal = [], [], 0, 0
    for spec in catalog.SCENARIOS:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(db_path=Path(tmp) / "e.db")
            gen = SyntheticDataGenerator(db=db, sample_interval=2.0, start_time=datetime(2026, 11, 4, tzinfo=timezone.utc))
            list(gen.run_scenario(spec.scenario_id, speed_multiplier=float("inf")))
            detected = checked = false_flags = normal_samples = 0
            scenario_leads, scenario_skills = [], []
            for signal in spec.signals:
                if signal.status == "n":
                    continue
                points = analytics.numeric_points(db.get_readings(tag=signal.tag, limit=1000))
                values = [p["v"] for p in points]
                band = signal.noise_band or signal.normal
                flags = [f["index"] for f in analytics.detect_anomalies(values, (None, None))]
                t0 = points[0]["_dt"]
                seconds = [(p["_dt"] - t0).total_seconds() for p in points]
                ramp_start = next((i for i, t in enumerate(seconds) if t >= signal.ramp[0]), 0)
                normal_samples += ramp_start
                false_flags += sum(1 for f in flags if f < ramp_start)
                checked += 1
                hits = [f for f in flags if f >= ramp_start]
                detected += 1 if hits else 0
                for limit in signal.limits:
                    crossed = next((i for i, v in enumerate(values) if (v >= limit.value if limit.direction == "high" else v <= limit.value)), None)
                    if crossed is not None and hits:
                        scenario_leads.append(seconds[crossed] - seconds[hits[0]])
                        break
                result = analytics.backtest(values, int(HORIZON_S / 2))
                if result and result["skill"] is not None:
                    scenario_skills.append(result["skill"])
            db.close()
        total_false += false_flags
        total_normal += normal_samples
        skills += scenario_skills
        leads += scenario_leads
        skill_text = f"{median(scenario_skills):.2f}" if scenario_skills else "n/a"
        lead_text = f"{median(scenario_leads):.0f}" if scenario_leads else "n/a"
        lines.append(f"| {spec.number} | {spec.name} | {detected}/{checked} | {false_flags}/{normal_samples} | {lead_text} | {skill_text} | "
                     f"{sum(1 for s in scenario_skills if s > 0)}/{len(scenario_skills)} |")
    lines += ["", f"Overall: {total_false} false flags in {total_normal} normal samples; median lead {median(leads):.0f} s before the limit; "
              f"forecast median skill {median(skills):.2f}, beating no-change on {sum(1 for s in skills if s > 0)} of {len(skills)} signals.", ""]
    report = "\n".join(lines)
    print(report)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
