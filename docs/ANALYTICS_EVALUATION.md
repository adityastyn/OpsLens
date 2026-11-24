# Analytics evaluation (simulated data only)

Each scenario: 60 samples at 2 s. Reproduce with `python tools/evaluate_analytics.py`; values vary slightly with simulator noise.
These results come from smooth simulated ramps. They show the methods work as built, not how they would do on a real plant.

**Anomaly detection.** Only the statistical detector is scored here, because the normal-band check is true by definition. False flags = samples flagged before the signal's ramp began. Lead = seconds between the first flag and the first limit crossing, on signals that have a limit.

**Forecast.** Horizon 30 s. Skill = 1 - MAE / (MAE of a no-change forecast). Above 0 beats no-change.

| # | Scenario | Signals flagged | False flags (normal period) | Median lead before limit (s) | Forecast median skill | Beat no-change |
|---|---|---|---|---|---|---|
| 1 | Rags jam the bar screen | 4/5 | 1/56 | 6 | -0.11 | 2/5 |
| 2 | The lift station pumps clog | 4/7 | 0/52 | 6 | 0.11 | 5/7 |
| 3 | Nocardia foam on the aeration tank | 2/7 | 0/111 | 9 | 0.18 | 6/7 |
| 4 | RAS pump fails, sludge blanket rises | 3/7 | 0/103 | 4 | 0.09 | 4/7 |
| 5 | CHP engine trips, gas holder fills | 4/6 | 0/67 | 0 | 0.16 | 3/4 |
| 6 | Centrate poisons the plant inlet | 3/8 | 0/135 | 4 | 0.07 | 4/6 |
| 7 | Low dissolved oxygen due to aeration blower failure | 3/5 | 0/92 | 6 | -0.01 | 2/4 |

Overall: 1 false flags in 616 normal samples; median lead 6 s before the limit; forecast median skill 0.10, beating no-change on 26 of 40 signals.
