"""A small stand-in for LM Studio's Local Model API, for tests.

Speaks the parts OpsLens uses: GET /v1/models, GET /api/v0/models and
POST /v1/chat/completions (streamed as server-sent events, or one JSON reply).
Subclass it and set the class attributes to shape the answers."""
import json
import time
from http.server import BaseHTTPRequestHandler

MODEL_ID = "qwen2.5-7b-instruct"


class FakeLMStudio(BaseHTTPRequestHandler):
    reply = "Wet-well level 3.8 m."
    delay = 0.0          # seconds before the first word
    status = 200         # HTTP status for chat requests
    error_body = ""      # body sent with a non-200 status
    models = [MODEL_ID]
    loaded = True
    context = 8192       # loaded context length reported by /api/v0/models
    received = []        # (path, payload) for every POST

    def log_message(self, *args):
        pass

    def _json(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        cls = type(self)
        if self.path == "/v1/models":
            self._json({"object": "list", "data": [{"id": m, "object": "model"} for m in cls.models]})
        elif self.path == "/api/v0/models":
            self._json({"object": "list", "data": [
                {"id": m, "type": "llm", "state": "loaded" if cls.loaded else "not-loaded",
                 **({"loaded_context_length": cls.context} if cls.loaded else {})} for m in cls.models]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        cls = type(self)
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        cls.received.append((self.path, payload))
        if self.path != "/v1/chat/completions":
            self._json({"error": "not found"}, 404)
            return
        if cls.status != 200:
            self._json({"error": cls.error_body or f"HTTP {cls.status}"}, cls.status)
            return
        time.sleep(cls.delay)
        try:
            if payload.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                words = cls.reply.split(" ")
                for i, word in enumerate(words):
                    token = word + (" " if i < len(words) - 1 else "")
                    chunk = {"choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                done = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                self.wfile.write(f"data: {json.dumps(done)}\n\ndata: [DONE]\n\n".encode())
            else:
                self._json({"choices": [{"index": 0, "message": {"role": "assistant", "content": cls.reply},
                                         "finish_reason": "stop"}]})
        except (BrokenPipeError, ConnectionResetError):
            pass
