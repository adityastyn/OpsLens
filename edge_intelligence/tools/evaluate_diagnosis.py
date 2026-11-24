"""Score the real AI diagnosis for all seven scenarios. Run on the laptop while
OpsLens is running (start_all.bat):

    cd edge_intelligence
    .venv\\Scripts\\python tools\\evaluate_diagnosis.py

Each scenario is run in Scenario Lab (SIMULATED data), then its diagnosis is
fetched from the backend with refresh=true. The scenario's known root cause is
used ONLY here, to score the answer. It is never sent to the model.

The cause score is a rough word match, not a judgement of quality. Read the
answers in the report too. It waits between scenarios so the Groq free limit
is not hit (OPSLENS_EVAL_PAUSE, default 40 s).

Writes reports/diagnosis_eval_<time>.md and .json.
"""
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

API = os.environ.get("OPSLENS_API") or "http://127.0.0.1:8001/api"
PAUSE = float(os.environ.get("OPSLENS_EVAL_PAUSE") or 40)
SESSION = requests.Session()
SESSION.trust_env = False
STOP = {"after", "above", "below", "while", "which", "their", "there", "motor", "tripped", "level", "with", "from"}


def get(path, **params):
    response = SESSION.get(API + path, params=params, timeout=300)
    response.raise_for_status()
    return response.json()


def post(path, body):
    response = SESSION.post(API + path, json=body, timeout=60)
    response.raise_for_status()
    return response.json()


def key_terms(root: str) -> list:
    """Equipment names and the main nouns of the known cause."""
    equipment = re.findall(r"\b[A-Z]{1,4}-[A-Z]?\d{1,3}[A-Z]?\b", root)
    words = [w for w in re.findall(r"[a-z]{5,}", root.lower()) if w not in STOP]
    return list(dict.fromkeys(equipment + words))


def section(text: str, name: str) -> str:
    match = re.search(rf"^#+[^\n]*{re.escape(name)}[^\n]*\n(.*?)(?=^#+\s|\Z)", text, re.S | re.M | re.I)
    return match.group(1) if match else ""


def run_one(scenario):
    post("/scenarios/run", {"scenario_id": scenario["scenario_id"], "speed_multiplier": 20})
    for _ in range(600):
        status = get("/scenarios/status")
        if status["status"] not in ("running", "recovering"):
            break
        time.sleep(0.5)
    started = time.time()
    done = get(f"/events/{status['event_id']}/diagnosis", refresh="true")
    seconds = round(time.time() - started, 1)
    text = done.get("text") or ""
    causes = section(text, "Possible causes")
    root = " ".join((scenario.get("root_cause") or {}).values())
    terms = key_terms(root)
    found = [t for t in terms if t.lower() in causes.lower()]
    validation = done.get("validation") or {}
    return {
        "scenario": scenario["name"], "event_id": status["event_id"], "source": done.get("source_status"),
        "engine": done.get("engine"), "seconds": seconds, "known_cause": root,
        "cause_terms_found": f"{len(found)}/{len(terms)}", "cause_terms_missing": [t for t in terms if t not in found],
        "removed_lines": len(validation.get("removed_lines") or []), "repaired_lines": validation.get("repaired_lines", 0),
        "causes_added_by_opslens": len(validation.get("added_causes") or []),
        "sop_steps_cited": validation.get("cited_sop_steps"), "fallback_reason": done.get("fallback_reason"),
        "removed": validation.get("removed_lines") or [], "text": text,
    }


def main():
    scenarios = get("/scenarios")
    scenarios = scenarios.get("scenarios", scenarios) if isinstance(scenarios, dict) else scenarios
    results = []
    for i, scenario in enumerate(scenarios):
        if i:
            print(f"Waiting {PAUSE:.0f} s for the rate limit...", flush=True)
            time.sleep(PAUSE)
        print(f"Running {scenario['name']}...", flush=True)
        try:
            row = run_one(scenario)
        except Exception as exc:
            row = {"scenario": scenario["name"], "error": f"{type(exc).__name__}: {exc}"}
        results.append(row)
        print("  " + json.dumps({k: v for k, v in row.items() if k not in ("text", "removed")}), flush=True)

    out = Path("reports")
    out.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    (out / f"diagnosis_eval_{stamp}.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# OpsLens diagnosis evaluation", "",
             "| Scenario | Source | Seconds | Known cause words in 'Possible causes' | Lines removed | Repaired | Causes added by OpsLens |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['scenario']} | ERROR {r['error']} | | | | | |")
            continue
        lines.append(f"| {r['scenario']} | {r['source']} ({r['engine']}) | {r['seconds']} | {r['cause_terms_found']} "
                     f"(missing: {', '.join(r['cause_terms_missing']) or 'none'}) | {r['removed_lines']} | {r['repaired_lines']} | {r['causes_added_by_opslens']} |")
    for r in results:
        if "text" in r:
            lines += ["", f"## {r['scenario']}", f"Known cause (used only for scoring): {r['known_cause']}", "", r["text"]]
            if r["removed"]:
                lines += ["", "Removed by the record check:"] + [f"- {x['line']} ({'; '.join(x['problems'])})" for x in r["removed"]]
    path = out / f"diagnosis_eval_{stamp}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport: {path.resolve()}")
    return 0 if all(r.get("source") == "ai_generated" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
