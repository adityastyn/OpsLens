"""Watch Groq key rotation work against a local MOCK of Groq. No real keys,
no internet, no Groq quota used. OpsLens does not need to be running.

    cd edge_intelligence
    .venv\\Scripts\\python tools\\mock_groq_keys.py

Mock keys behave as named: "limited-..." always gets HTTP 429 (rate limit),
"bad-..." gets HTTP 401 (rejected), "ok-..." answers.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ANSWER = "\n".join([
    "## 1. What is happening", "- CONFIRMED: Test alarm ALM-1 is recorded.",
    "## 2. Evidence supporting the diagnosis", "- CONFIRMED: ALM-1 is active.",
    "## 3. Possible causes", "- HYPOTHESIS: unknown.",
    "## 4. Recommended checks from SOP", "- No directly matching approved SOP was found for this incident.",
    "## 5. Resolution and recovery check", "- UNKNOWN: no recovery criteria recorded.",
    "## 6. Additional advisory checks", "- Not in the SOP: compare the runtime with the field.",
    "## 7. Missing information", "- UNKNOWN: field confirmation.",
    "## 8. Confidence and limitations", "- The cause is a hypothesis.",
])
CALLS = []


class MockGroq(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        key = self.headers.get("Authorization", "").replace("Bearer ", "")
        if key.startswith("limited"):
            CALLS.append((key, "429 rate-limited"))
            self.send_response(429)
            self.send_header("retry-after", "60")
            self.end_headers()
            return
        if key.startswith("bad"):
            CALLS.append((key, "401 rejected"))
            self.send_response(401)
            self.end_headers()
            return
        CALLS.append((key, "200 answered"))
        self.send_response(200)
        self.end_headers()
        for word in ANSWER.split(" "):
            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": word + " "}}]}) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockGroq)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ.update({
        "OPSLENS_LLM_PROVIDER": "groq", "GROQ_API_KEY": "", "OPSLENS_DIAG_LOCAL_BACKUP": "false",
        "GROQ_API_ENDPOINT": f"http://127.0.0.1:{server.server_address[1]}/v1",
    })
    import ai_diagnosis
    from groq_keys import POOL

    facts = {"event_id": "mock", "incident": "Mock incident", "data_source": "SIMULATED",
             "primary_alarm": {"tag": "ALM-1", "description": "Test alarm", "first_at": "10:00:00", "input_active": True},
             "active_alarms": ["ALM-1"], "incident_tags": ["ALM-1"], "readings": [], "sequence": [], "sop": None, "missing": []}
    cases = [
        ("Key 1 rate-limited, key 2 answers", "limited-1,ok-2,ok-3"),
        ("Same keys again: key 1 is still cooling down, so it is skipped and key 3 takes its turn", "limited-1,ok-2,ok-3"),
        ("Key 1 rejected, key 2 answers", "bad-1,ok-2"),
        ("Every key rate-limited", "limited-1,limited-2"),
    ]
    for n, (title, keys) in enumerate(cases, 1):
        if keys != os.environ.get("GROQ_API_KEYS"):
            POOL.reset()
        os.environ["GROQ_API_KEYS"] = keys
        ai_diagnosis.CACHE.clear()
        CALLS.clear()
        done = [e for e in ai_diagnosis.stream_diagnosis_from_facts({**facts, "event_id": f"mock-{n}"}) if e["type"] == "done"][0]
        print(f"\n{n}. {title}   (GROQ_API_KEYS={keys})")
        for key, result in CALLS:
            print(f"   request with {key:<12} -> {result}")
        outcome = "AI answer from the mock" if done["engine"] == "groq" else f"fallback: {done['fallback_reason']}"
        print(f"   result: {outcome}")
        print("   keys:   " + ", ".join(f"{k['key']} {k['state']}" for k in POOL.status()["keys"]))
    server.shutdown()
    print("\nThis was a mock. Keys in one Groq account share one rate limit, so rotating them adds no quota.")


if __name__ == "__main__":
    main()
