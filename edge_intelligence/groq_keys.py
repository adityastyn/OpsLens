"""Several Groq API keys, used in turn when one is rate-limited or rejected.

Set GROQ_API_KEYS=key1,key2,key3 in edge_intelligence/.env (GROQ_API_KEY alone
still works). Keys are never logged or returned by the API; they are named
"key 1", "key 2" and so on, in the order given.

Groq applies rate limits per organization. Keys from the same account share
one limit, so switching between them does not add quota. Groq's Acceptable
Use Policy restricts registering several accounts to get more usage, so keys
from different accounts may put those accounts at risk. This is meant for
testing against a mock server, or for keeping a spare key if one is revoked.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Dict, List, Optional, Tuple

DEFAULT_COOLDOWN_SECONDS = 60.0


def configured_keys() -> List[str]:
    raw = os.environ.get("GROQ_API_KEYS") or os.environ.get("GROQ_API_KEY") or ""
    keys = [k.strip() for k in re.split(r"[,;\s]+", raw) if k.strip()]
    return list(dict.fromkeys(keys))


class KeyPool:
    def __init__(self):
        self._lock = threading.Lock()
        self._keys: Tuple[str, ...] = ()
        self._cooldown: Dict[int, float] = {}
        self._rejected: set = set()
        self._next = 0

    def _sync(self) -> None:
        keys = tuple(configured_keys())
        if keys != self._keys:  # .env or environment changed
            self._keys, self._cooldown, self._rejected, self._next = keys, {}, set(), 0

    def count(self) -> int:
        with self._lock:
            self._sync()
            return len(self._keys)

    def candidates(self) -> List[Tuple[int, str]]:
        """Usable keys, starting from the one after the last used, so the load
        is shared. Keys cooling down after a 429 and rejected keys are skipped."""
        with self._lock:
            self._sync()
            now = time.monotonic()
            order = [(self._next + i) % len(self._keys) for i in range(len(self._keys))] if self._keys else []
            usable = [(i, self._keys[i]) for i in order if i not in self._rejected and self._cooldown.get(i, 0) <= now]
            # Nothing usable: try every key again rather than refuse without asking.
            # With one key this is the normal case after a 429; Groq answers again
            # with 429 if the limit still applies.
            return usable or [(i, self._keys[i]) for i in order]

    def any_usable(self) -> bool:
        with self._lock:
            self._sync()
            now = time.monotonic()
            return any(i not in self._rejected and self._cooldown.get(i, 0) <= now for i in range(len(self._keys)))

    def used(self, index: int) -> None:
        with self._lock:
            if self._keys:
                self._next = (index + 1) % len(self._keys)

    def limited(self, index: int, retry_after: Optional[str]) -> None:
        try:
            seconds = float(retry_after) if retry_after else DEFAULT_COOLDOWN_SECONDS
        except ValueError:
            seconds = DEFAULT_COOLDOWN_SECONDS
        with self._lock:
            self._cooldown[index] = time.monotonic() + max(seconds, 0.0)

    def rejected(self, index: int) -> None:
        with self._lock:
            self._rejected.add(index)

    def shortest_wait(self) -> Optional[float]:
        """Seconds until the first rate-limited key can be used again."""
        with self._lock:
            now = time.monotonic()
            waits = [until - now for i, until in self._cooldown.items() if i not in self._rejected]
            return max(min(waits), 0.0) if waits else None

    def status(self) -> Dict[str, object]:
        with self._lock:
            self._sync()
            now = time.monotonic()
            rows = []
            for i in range(len(self._keys)):
                state = "rejected" if i in self._rejected else (
                    f"rate-limited, {self._cooldown[i] - now:.0f} s left" if self._cooldown.get(i, 0) > now else "ready")
                rows.append({"key": f"key {i + 1}", "state": state})
            return {"count": len(self._keys), "keys": rows}

    def reset(self) -> None:
        with self._lock:
            self._keys, self._cooldown, self._rejected, self._next = (), {}, set(), 0


POOL = KeyPool()
