"""Check each Groq key in edge_intelligence/.env with one tiny real request.

    cd edge_intelligence
    .venv\\Scripts\\python tools\\check_groq_keys.py

Reads GROQ_API_KEYS (or GROQ_API_KEY) and OPSLENS_GROQ_MODEL from .env. Each
check uses a few tokens. Keys are shown as "key 1", "key 2" and the last 4
characters only. Prints Groq's own remaining-limit headers, so you can see
whether keys share one limit (same account) or not.
"""
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_env():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def main():
    load_env()
    from groq_keys import configured_keys
    keys = configured_keys()
    if not keys:
        print("No keys found. Set GROQ_API_KEYS=key1,key2 in edge_intelligence\\.env")
        return 2
    model = os.environ.get("OPSLENS_GROQ_MODEL") or "openai/gpt-oss-120b"
    endpoint = os.environ.get("GROQ_API_ENDPOINT") or "https://api.groq.com/openai/v1/chat/completions"
    print(f"Checking {len(keys)} key(s) with model {model}\n")
    working = 0
    for n, key in enumerate(keys, 1):
        name = f"key {n} (...{key[-4:]})"
        try:
            resp = requests.post(endpoint, timeout=30, headers={"Authorization": f"Bearer {key}"}, json={
                "model": model, "max_completion_tokens": 40, "reasoning_effort": "low",
                "messages": [{"role": "user", "content": "Reply with the word OK."}]})
        except requests.RequestException as exc:
            print(f"{name}: could not reach Groq ({type(exc).__name__}). Check the internet connection.")
            continue
        h = resp.headers
        limits = (f"requests left today {h.get('x-ratelimit-remaining-requests', '?')}/{h.get('x-ratelimit-limit-requests', '?')}, "
                  f"tokens left this minute {h.get('x-ratelimit-remaining-tokens', '?')}/{h.get('x-ratelimit-limit-tokens', '?')}")
        if resp.status_code == 200:
            working += 1
            print(f"{name}: OK. {limits}")
        elif resp.status_code == 429:
            print(f"{name}: rate-limited now, retry after {h.get('retry-after', '?')} s. {limits}")
        elif resp.status_code in (401, 403):
            print(f"{name}: rejected. The key is wrong, revoked or has no access to {model}.")
        else:
            print(f"{name}: HTTP {resp.status_code}: {resp.text[:200]}")
    print(f"\n{working} of {len(keys)} key(s) working.")
    print("If two keys show the same 'left' numbers going down together, they share one account's limit.")
    return 0 if working else 1


if __name__ == "__main__":
    sys.exit(main())
